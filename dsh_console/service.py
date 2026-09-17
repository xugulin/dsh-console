"""与 systemd user 服务 `dsh-web` 交互。

设计前提（来自实际环境勘察）：
  * harness 以 systemd user 服务运行，单元名 ``dsh-web``；
  * 服务由 wrapper ``~/.local/bin/dsh-web`` 启动，带 ``--no-open``，
    因此启动后不会自动开浏览器，需要从 journal 里取带 token 的 URL；
  * **绝不能用 SIGKILL 停服务**：浏览器插件的 cookies/storage 只在干净退出时落盘，
    丢了 flush 会把用户从所有网站登出。本模块只用 ``systemctl stop/restart``。
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import portable

UNIT = "dsh-web"
SERVICE_LABEL = {"web": "DSH Web UI"}

#: 从 journal 中提取带 token 的地址（与服务 wrapper、dsh-open 保持一致）。
_URL_RE = re.compile(r"http://127\.0\.0\.1:\d+/\?token=[A-Za-z0-9_-]+")

#: 子进程统一超时，避免界面被卡死。
_TIMEOUT = 20


class ServiceError(RuntimeError):
    """systemctl 调用失败。"""


@dataclass(slots=True)
class ServiceStatus:
    """一次状态快照。

    "harness 在不在跑"和"systemd 单元在不在跑"是**两件事**：在终端里手敲
    ``dsh web`` 就能让服务活得好好的，而单元那边一直是 inactive。所以这里同时保留
    两套信息——单元状态（systemctl）与监听进程（ss + /proc），谁有就用谁。
    """

    load_state: str = "unknown"
    active_state: str = "unknown"
    sub_state: str = "unknown"
    unit_file_state: str = "unknown"
    main_pid: int = 0
    memory_bytes: int = 0
    n_restarts: int = 0
    active_enter: datetime | None = None
    inactive_enter: datetime | None = None
    exec_main_start: datetime | None = None
    port: int | None = None
    url: str | None = None
    linger: bool = False
    raw_error: str = ""
    # --- 监听套接字的真实主人（systemd 之外启动时只有这里有答案）
    listener_pid: int = 0
    listener_name: str = ""
    listener_rss: int = 0
    listener_uptime: int = 0

    @property
    def unit_running(self) -> bool:
        """systemd 认为这个单元在 active。"""
        return self.active_state == "active"

    @property
    def foreign_running(self) -> bool:
        """harness 在跑，但**不是**这个单元拉起来的。

        典型场景：有人在终端里敲了 ``dsh web``。这时 ``systemctl`` 说 inactive，
        可端口确实被占着、界面确实能打开——只看单元状态就会谎报"已停止"。
        """
        return not self.unit_running and self.listener_pid > 0

    @property
    def is_running(self) -> bool:
        """harness 到底在不在服务（**不只看 systemd**）。"""
        return self.unit_running or self.foreign_running

    @property
    def is_failed(self) -> bool:
        return self.active_state == "failed"

    @property
    def label(self) -> str:
        if self.foreign_running:
            return "运行中（非 systemd）"
        return {
            "active": "运行中",
            "inactive": "已停止",
            "failed": "启动失败",
            "activating": "启动中",
            "deactivating": "停止中",
        }.get(self.active_state, self.active_state)

    @property
    def effective_pid(self) -> int:
        """该显示哪个 PID：单元在跑就用 MainPID，否则用占着端口的那个进程。"""
        return self.main_pid or self.listener_pid

    @property
    def effective_memory_bytes(self) -> int:
        """内存占用。

        ``MemoryCurrent`` 来自单元的 cgroup，**单元没在管这个进程时是空的**
        （``[not set]``）——手工启动的 harness 就落在 ``session-N.scope`` 里，
        怎么问 systemd 都是 0。这时退回读那个进程自己的 ``/proc/<pid>/status``。
        """
        if self.memory_bytes > 0:
            return self.memory_bytes
        return self.listener_rss

    @property
    def uptime_seconds(self) -> int:
        """**本次**运行的时长；确实没在跑时是 0。

        坑：systemd 的 ``ActiveEnterTimestamp`` 在服务停止后并不清零——它的含义是
        "进入当前 ActiveState 的时刻"，对 inactive 单元来说就是**停止的时刻**。
        所以 ``now - active_enter`` 只能表示"现在还在跑多久"，停止后直接拿它当
        "运行时长"会得到"已停止 50 分钟"却显示成"运行时长 50分"的荒唐结果。
        停止后要显示上一次跑了多久，用 :attr:`last_run_text`。

        非 systemd 托管时单元里没有任何时间戳，改用监听进程的 /proc 启动时刻。
        """
        if self.unit_running:
            if self.active_enter is None:
                return 0
            return max(0, int((datetime.now() - self.active_enter).total_seconds()))
        return max(0, self.listener_uptime)

    @property
    def uptime_text(self) -> str:
        return format_duration(self.uptime_seconds)

    @property
    def last_run_seconds(self) -> int:
        """**单元**当前（运行中）或上一次运行持续了多久。停止后依然算得出来。

        判据用 :attr:`unit_running` 而不是 :attr:`is_running`：后者把"手工启动的
        harness"也算作在跑，可那种情况下 systemd 里的时间戳是上一次单元运行的，
        拿它算"这次跑了多久"会得出一个与事实无关的数字。
        """
        if self.active_enter is None:
            return 0
        end = datetime.now() if self.unit_running else (self.inactive_enter or self.active_enter)
        return max(0, int((end - self.active_enter).total_seconds()))

    @property
    def last_run_text(self) -> str:
        return format_duration(self.last_run_seconds)

    @property
    def stopped_for_seconds(self) -> int:
        """**单元**已经停了多久；单元正在跑（或从未停过）时是 0。"""
        if self.unit_running or self.inactive_enter is None:
            return 0
        return max(0, int((datetime.now() - self.inactive_enter).total_seconds()))

    @property
    def stopped_for_text(self) -> str:
        return format_duration(self.stopped_for_seconds)

    @property
    def memory_text(self) -> str:
        b = self.effective_memory_bytes
        if b <= 0:
            return "—"
        for unit, div in (("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)):
            if b >= div:
                return f"{b / div:.1f} {unit}"
        return f"{b} B"


def format_duration(seconds: int) -> str:
    """把秒数格式化成 ``3天4小时5分`` 这样的中文时长。"""
    if seconds <= 0:
        return "—"
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if d:
        parts.append(f"{d}天")
    if h or d:
        parts.append(f"{h}小时")
    if m or h or d:
        parts.append(f"{m}分")
    if not parts:
        parts.append(f"{s}秒")
    return "".join(parts[:3])


def _run(args: list[str], timeout: int = _TIMEOUT) -> subprocess.CompletedProcess[str]:
    """跑一条子进程命令，永不抛 FileNotFoundError。"""
    try:
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:  # 极端情况下 PATH 不含 systemctl
        raise ServiceError(f"找不到命令 {args[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ServiceError(f"命令超时：{' '.join(args)}") from exc


def _systemctl(*args: str, timeout: int = _TIMEOUT) -> subprocess.CompletedProcess[str]:
    return _run(["systemctl", "--user", *args], timeout=timeout)


def _journalctl(*args: str, timeout: int = _TIMEOUT) -> subprocess.CompletedProcess[str]:
    return _run(["journalctl", "--user", *args], timeout=timeout)


def _parse_systemd_time(value: str) -> datetime | None:
    """解析 ``Mon 2026-09-14 18:34:59 CST`` 这类时间戳。"""
    if not value or value in {"n/a", "0"}:
        return None
    cleaned = value.strip()
    # 去掉开头的星期缩写
    cleaned = re.sub(r"^[A-Za-z]{3}\s+", "", cleaned)
    # 去掉结尾的时区缩写（CST 等），datetime 不认识它
    cleaned = re.sub(r"\s+[A-Z]{2,5}$", "", cleaned)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
    return None


#: 状态快照缓存。侧边栏每 5 秒、仪表盘每 3 秒都要状态，而取一次状态要 fork
#: 4 个子进程（systemctl / ss / journalctl / loginctl）外加 1 次 HTTP。
#: 两边共享同一份快照可以砍掉一半开销。
_status_lock = threading.Lock()
_status_snapshot: "ServiceStatus | None" = None
_status_at: float = 0.0


def invalidate_status() -> None:
    """让下次取状态必须重新采集（启停重启之后调用）。"""
    global _status_snapshot, _status_at
    with _status_lock:
        _status_snapshot, _status_at = None, 0.0


def get_status_cached(max_age: float = 2.0, unit: str = UNIT) -> ServiceStatus:
    """带 TTL 的状态快照；``max_age`` 秒内的重复请求直接复用。"""
    global _status_snapshot, _status_at
    with _status_lock:
        snap, at = _status_snapshot, _status_at
    if snap is not None and (time.monotonic() - at) < max_age:
        return snap
    st = get_status(unit)
    with _status_lock:
        _status_snapshot, _status_at = st, time.monotonic()
    return st


def get_status(unit: str = UNIT) -> ServiceStatus:
    """读取一次完整状态。任何失败都体现在 ``raw_error`` 里，不抛异常。

    便携模式下没有 systemd，harness 是控制台自己拉起来的子进程——走
    :func:`_portable_status`。
    """
    if use_child_backend():
        return _portable_status()
    props = (
        "LoadState",
        "ActiveState",
        "SubState",
        "UnitFileState",
        "MainPID",
        "MemoryCurrent",
        "NRestarts",
        "ActiveEnterTimestamp",
        "InactiveEnterTimestamp",
        "ExecMainStartTimestamp",
    )
    st = ServiceStatus()
    try:
        proc = _systemctl("show", unit, *(f"-p{p}" for p in props))
        if proc.returncode != 0 and not proc.stdout.strip():
            st.raw_error = (proc.stderr or "systemctl show 失败").strip()
            return st
        values: dict[str, str] = {}
        for line in proc.stdout.splitlines():
            key, _, val = line.partition("=")
            values[key.strip()] = val.strip()

        st.load_state = values.get("LoadState", "unknown")
        st.active_state = values.get("ActiveState", "unknown")
        st.sub_state = values.get("SubState", "unknown")
        st.unit_file_state = values.get("UnitFileState", "unknown")
        st.main_pid = int(values.get("MainPID") or 0)
        mem = values.get("MemoryCurrent") or "0"
        # systemd 用 [not set] 或极大值表示未知
        st.memory_bytes = int(mem) if mem.isdigit() else 0
        st.n_restarts = int(values.get("NRestarts") or 0)
        st.active_enter = _parse_systemd_time(values.get("ActiveEnterTimestamp", ""))
        st.inactive_enter = _parse_systemd_time(values.get("InactiveEnterTimestamp", ""))
        st.exec_main_start = _parse_systemd_time(values.get("ExecMainStartTimestamp", ""))
    except ServiceError as exc:
        st.raw_error = str(exc)
        return st

    # 监听套接字：端口 + 真正占着它的进程。这是"harness 到底在不在跑"的**实测**依据，
    # 比 systemd 的单元状态更接近事实——手工在终端敲 `dsh web` 时单元是 inactive，
    # 但端口占着、界面能开，只看单元状态就会谎报"已停止"。
    st.port, st.listener_pid, st.listener_name = _find_listener()
    if st.listener_pid:
        st.listener_rss = _proc_rss_bytes(st.listener_pid)
        st.listener_uptime = _proc_uptime_seconds(st.listener_pid)
    # 地址只认单元这次运行签发的 token：手工启动的那个实例把地址打在自己终端里，
    # 这里既看不到也不该猜——猜出来的只可能是上一次单元运行留下的**失效** token。
    if st.unit_running:
        st.url = current_url(unit)
    # 开机自启
    st.linger = _linger_enabled()
    return st


#: harness 的默认端口。找不到任何线索时按它找监听套接字。
DEFAULT_PORT = 3080

#: ss 输出里带进程信息的那一段：users:(("node-MainThread",pid=17313,fd=25))
_SS_PEER_RE = re.compile(r'users:\(\("([^"]+)",pid=(\d+)')
_SS_ADDR_RE = re.compile(r"127\.0\.0\.1:(\d+)")


def _find_listener() -> tuple[int | None, int, str]:
    """找出监听 harness 端口的进程，返回 ``(端口, pid, 进程名)``。

    优先认端口 3080，其次认进程名像 node 的本地监听——harness 换端口启动时
    （``dsh-web --port 8080``）前者会落空，后者还能兜住。
    """
    try:
        proc = _run(["ss", "-ltnp"])
    except ServiceError:
        return None, 0, ""

    fallback: tuple[int, int, str] | None = None
    for line in proc.stdout.splitlines():
        if "LISTEN" not in line:
            continue
        addr = _SS_ADDR_RE.search(line)
        if addr is None:
            continue
        port = int(addr.group(1))
        peer = _SS_PEER_RE.search(line)
        pid = int(peer.group(2)) if peer else 0
        name = peer.group(1) if peer else ""
        if port == DEFAULT_PORT:
            return port, pid, _proc_comm(pid) or name
        # 端口对不上时留个候选：本机回环 + 进程名像 node
        if fallback is None and pid and ("node" in name.lower() or "dsh" in name.lower()):
            fallback = (port, pid, _proc_comm(pid) or name)
    if fallback is not None:
        return fallback
    return None, 0, ""


def _port_of_pid(pid: int) -> int | None:
    """某个进程监听的本机端口。

    ``_find_listener`` 是按"端口 3080 / 进程名像 node"挑的——那是给**系统单元**用的
    启发式。便携包里宿主机可能也跑着一个 harness（本机就是），按端口找会找到别人头上，
    所以这里必须按 pid 精确定位。
    """
    if pid <= 0:
        return None
    try:
        proc = _run(["ss", "-ltnp"])
    except ServiceError:
        return None
    for line in proc.stdout.splitlines():
        if "LISTEN" not in line:
            continue
        peer = _SS_PEER_RE.search(line)
        addr = _SS_ADDR_RE.search(line)
        if peer is None or addr is None:
            continue
        if int(peer.group(2)) == pid:
            return int(addr.group(1))
    return None


def _proc_comm(pid: int) -> str:
    """进程名。``ss`` 给的是线程名（``node-MainThread``），这里换成 argv[0] 更好认。"""
    if pid <= 0:
        return ""
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return ""
    for chunk in raw.split(b"\0"):
        if chunk:
            return Path(chunk.decode("utf-8", "replace")).name
    return ""


def _proc_rss_bytes(pid: int) -> int:
    """进程的常驻内存（VmRSS，单位 KB → 字节）。

    非 systemd 托管的 harness 落在 ``session-N.scope`` 里，systemd 的
    ``MemoryCurrent`` 对它一无所知（``[not set]``），只能直接问 /proc。
    """
    if pid <= 0:
        return 0
    try:
        for line in Path(f"/proc/{pid}/status").read_text(
            encoding="utf-8", errors="replace"
        ).splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return 0


def _proc_uptime_seconds(pid: int) -> int:
    """进程已经跑了多久，靠 /proc 算，不额外 fork ``ps``。

    ``/proc/<pid>/stat`` 第 22 个字段是进程启动时刻（单位 CLK_TCK），
    拿系统 uptime 一减就是运行时长。
    """
    if pid <= 0:
        return 0
    try:
        # comm 字段可能含空格和括号，从最后一个 ')' 之后开始切才是对的
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
        fields = stat[stat.rindex(")") + 2 :].split()
        starttime = int(fields[19])  # 整体第 22 个，减去前 3 个后是第 19 个
        with open("/proc/uptime", encoding="utf-8") as fh:
            up = float(fh.read().split()[0])
        return max(0, int(up - starttime / os.sysconf("SC_CLK_TCK")))
    except (OSError, ValueError, IndexError):
        return 0


def _linger_enabled(user: str | None = None) -> bool:
    import getpass

    user = user or getpass.getuser()
    try:
        proc = _run(["loginctl", "show-user", user, "-p", "Linger"])
    except ServiceError:
        return False
    return "Linger=yes" in proc.stdout


# --------------------------------------------------------------------- 便携模式
#
# 便携包里没有 systemd（也不该要求目标机器有），harness 就是控制台拉起来的**子进程**：
#   * pid 落在 <包根>/run/web.pid，日志落在 <包根>/run/web.log；
#   * 地址带 token，harness 启动时会打到 stdout，我们抓在日志里，再从中正则取；
#   * 停止用 SIGTERM（**不能** SIGKILL：浏览器插件的登录态只在干净退出时落盘）。
#
# 这套东西刻意只写在 service.py 里、不另开模块：状态结构、/proc 读数、URL 校验这些
# 都已经在下面了，挪出去反而要复制一遍。


def _run_dir() -> Path:
    """子进程后端的运行时目录。

    便携包里是 ``<包根>/run``（跟着包走）；**源码模式下也得有地方放 pid 和日志**——
    现在"内置 harness"这个来源在源码模式里同样可选（包做着玩、系统那份不想动），
    没地方落 pid 就会退化成"启动了但查不到状态"。
    """
    run = portable.run_dir()
    if run is not None:
        return run
    base = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache"))
    return base / "dsh-console" / "run"


def _portable_pid_file() -> Path | None:
    return _run_dir() / "web.pid"


def _portable_log_path() -> Path | None:
    return _run_dir() / "web.log"


def use_child_backend() -> bool:
    """web 服务该不该用"子进程"后端（而不是 systemd）。

    判断依据是**当前选中的 harness 来源**，不再只是"是不是便携模式"：
    内置那份永远用子进程拉起来（它不在 systemd 单元里）；系统那份在 Linux 上
    走 systemd 单元。
    """
    from . import harness as _harness

    return _harness.current_source() == _harness.SOURCE_BUNDLED


def pid_alive(pid: int) -> bool:
    """进程还活着吗。

    ⚠️ **Windows 上绝对不能用 ``os.kill(pid, 0)`` 探测**：POSIX 里信号 0 是"只检查
    不发信号"，但 Windows 的 ``os.kill`` 是 ``OpenProcess`` + ``TerminateProcess``，
    传 0 就是"以退出码 0 结束那个进程"——**查一次状态就把 harness 杀了**。
    Windows 上改用 ``tasklist`` 查。
    """
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                                 capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            return False
        return str(pid) in (out.stdout or "")
    try:
        os.kill(pid, 0)          # 只探测存在性，不真的发信号
    except ProcessLookupError:
        return False
    except PermissionError:
        return True              # 别人的进程，但确实活着
    except OSError:
        return False
    return True


def _portable_pid() -> int | None:
    """读 pid 文件并确认进程还活着；不活就顺手把陈旧文件删掉。"""
    path = _portable_pid_file()
    if path is None or not path.is_file():
        return None
    try:
        pid = int(path.read_text(encoding="utf-8").strip() or 0)
    except (OSError, ValueError):
        return None
    if pid <= 0:
        return None
    if not pid_alive(pid):
        try:
            path.unlink()
        except OSError:
            pass
        return None
    return pid


def _port_from_url(url: str | None) -> int | None:
    """从 ``http://127.0.0.1:3080/?token=…`` 里取端口。"""
    if not url:
        return None
    # _URL_RE 只匹配整串、没有捕获组，这里要单独取端口，所以另用一条
    match = re.search(r"127\.0\.0\.1:(\d+)", url or "")
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _portable_log_text(limit_bytes: int = 512 * 1024) -> str:
    path = _portable_log_path()
    if path is None or not path.is_file():
        return ""
    try:
        size = path.stat().st_size
        with open(path, "rb") as fh:
            if size > limit_bytes:
                fh.seek(size - limit_bytes)
            return fh.read().decode("utf-8", "replace")
    except OSError:
        return ""


def portable_log_tail(lines: int = 12) -> str:
    """便携模式下的 harness 日志尾部（非便携模式返回空串）。

    给界面用：起不来时把那几行直接摆给用户看，比"请查看日志"有用得多。
    """
    if not use_child_backend():
        return ""
    return "\n".join(_portable_log_text().splitlines()[-lines:])


def _portable_status() -> ServiceStatus:
    st = ServiceStatus()
    st.load_state = "loaded"
    st.unit_file_state = "bundled"
    pid = _portable_pid()
    if pid is None:
        # 只看**自己**有没有在跑。**不要**退回"扫一遍谁占着端口"——宿主系统上很
        # 可能另有一个 harness（本机就是），那样会把它认成自己的，于是便携包里
        # 从没启动过的 harness 显示成"运行中"，用户点不动启动按钮（实测踩过）。
        st.active_state = "inactive"
        st.sub_state = "dead"
        return st
    st.active_state = "active"
    st.sub_state = "running"
    st.main_pid = pid
    st.memory_bytes = _proc_rss_bytes(pid)
    st.listener_uptime = _proc_uptime_seconds(pid)
    st.url = current_url()
    # 端口优先从**日志里的地址**取：那是 harness 自己报的，两个平台都准，
    # 而且不依赖 `ss`（Windows 上根本没有这个命令）。
    st.port = _port_from_url(st.url) or (_port_of_pid(pid) if os.name != "nt" else None)
    if st.port is None:
        # 我们的进程起来了却还没监听——多半就是端口被占了。把占用者找出来说清楚，
        # 否则用户只看到"启动了但打不开"。
        other, other_pid, _ = _find_listener()
        if other_pid and other_pid != pid:
            st.raw_error = (f"端口 {other} 被别的进程（PID {other_pid}）占着；"
                            f"换端口可以设环境变量 {PORTABLE_PORT_ENV}")
    st.listener_pid, st.listener_name = pid, "node"
    return st


#: 便携包里 web 用哪个端口。默认 3080（和 DSH 一致）；被占了就设这个环境变量换一个。
PORTABLE_PORT_ENV = "DSH_CONSOLE_WEB_PORT"


def _portable_start(*, port: int | None = None) -> None:
    """把 ``node <harness>/lib/bin.js web --no-open`` 拉起来。"""
    if port is None:
        raw = (os.environ.get(PORTABLE_PORT_ENV) or "").strip()
        if raw.isdigit():
            port = int(raw)
    if _portable_pid() is not None:
        return                              # 已经在跑
    run = portable.run_dir()
    entry = portable.harness_entry()
    node = portable.node_bin()
    missing = [name for name, got in (("run 目录", run), ("harness", entry), ("node", node))
               if got is None]
    if missing:
        raise ServiceError(f"便携包不完整，缺少：{'、'.join(missing)}")
    assert run is not None and entry is not None and node is not None
    run.mkdir(parents=True, exist_ok=True)
    log_path = _portable_log_path()
    assert log_path is not None

    env = dict(os.environ)
    env.setdefault("PYTHONUNBUFFERED", "1")
    argv = [str(node), str(entry), "web", "--no-open"]
    if port:
        argv += ["--port", str(port)]
    # 日志用追加：重启后旧地址还在文件里，但 _URL_RE 取的是最后一条，不受影响
    with open(log_path, "ab") as log:
        log.write(f"\n=== 启动 {datetime.now():%Y-%m-%d %H:%M:%S} ===\n".encode())
        log.flush()
        # POSIX 用 setsid 脱离进程组；Windows 没有 setsid，改用新建进程组——
        # 这样后面能靠 CTRL_BREAK_EVENT 让它优雅退出（Windows 没有 SIGTERM）。
        extra: dict = {}
        if os.name == "nt":
            extra["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            extra["start_new_session"] = True
        proc = subprocess.Popen(
            argv, cwd=str(portable.root()), env=env,
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            **extra,
        )
    try:
        _portable_pid_file().write_text(str(proc.pid), encoding="utf-8")  # type: ignore[union-attr]
    except OSError:
        pass
    invalidate_status()


def _portable_stop(timeout: float = 15.0) -> None:
    """SIGTERM 并等它自己退。

    **不能**用 SIGKILL：浏览器插件的 cookies/storage 只在干净退出时落盘，
    硬杀会把所有网站登录态清掉（这条在 README 的"安全与注意事项"里也写着）。
    """
    pid = _portable_pid()
    if pid is None:
        return
    if os.name == "nt":
        # Windows 没有 SIGTERM：给整个进程组发 CTRL_BREAK，Node 会当成 SIGBREAK
        # 走正常的关闭流程（浏览器插件才有机会把 storage 落盘）。
        try:
            os.kill(pid, signal.CTRL_BREAK_EVENT)
        except (OSError, ValueError):
            pass
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            break
        time.sleep(0.2)
    else:
        # 等不到就只好硬来。Windows 上这一步尤其可能触发，说明它没响应 CTRL_BREAK。
        if pid_alive(pid):
            try:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                                   capture_output=True, timeout=20)
                else:
                    os.kill(pid, signal.SIGKILL)
            except (OSError, subprocess.SubprocessError):
                pass
    path = _portable_pid_file()
    if path is not None:
        try:
            path.unlink()
        except OSError:
            pass
    invalidate_status()



def current_url(unit: str = UNIT) -> str | None:
    """从 journal 里取当前这次运行签发的带 token 地址。

    token 每次启动都会重新签发，因此只认**最后一条**；调用方拿到后应先验证再用。
    便携模式没有 journal，地址在控制台自己抓的日志里（见 :func:`_portable_log_text`）。
    """
    if use_child_backend():
        matches = _URL_RE.findall(_portable_log_text())
        return matches[-1] if matches else None
    try:
        proc = _journalctl("-u", unit, "-n", "500", "--no-pager")
    except ServiceError:
        return None
    matches = _URL_RE.findall(proc.stdout)
    return matches[-1] if matches else None


def _no_redirect_opener():
    """一个**不跟随重定向**的 opener。

    这一点很关键：``GET /?token=…`` 会返回 ``303 See Other`` 并下发会话 cookie。
    urllib 默认会跟随这个重定向，但**不会**把 Set-Cookie 带回去，于是第二次请求
    到 ``/`` 就成了未认证请求，返回 401 —— 于是「有效 token」会被误判为失效。
    （``curl`` 不带 ``-L`` 时看到的就是 303，所以命令行验证一直是对的。）

    因此这里禁掉重定向跟随，直接按 3xx 判定 token 有效。
    """
    import urllib.request

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
            return None  # 返回 None → 交由 HTTPDefaultErrorHandler 抛 HTTPError(3xx)

    return urllib.request.build_opener(NoRedirect)


_ALIVE_CODES = (200, 301, 302, 303, 307, 308)


def url_is_alive(url: str, timeout: float = 5.0) -> bool:
    """问服务器这个 token 还有效吗。

    有效 → 303（重定向到 `/` 并下发 cookie）；失效 → 401。
    """
    import urllib.error

    if not url:
        return False
    opener = _no_redirect_opener()
    try:
        with opener.open(url, timeout=timeout) as resp:  # noqa: S310 - 本地回环
            return resp.status in _ALIVE_CODES
    except urllib.error.HTTPError as exc:
        # 禁掉重定向后 3xx 会以 HTTPError 形式抛出，这里同样视为有效
        return exc.code in _ALIVE_CODES
    except Exception:
        return False


def start(unit: str = UNIT) -> None:
    if use_child_backend():
        _portable_start()
        return
    _act("start", unit)


def stop(unit: str = UNIT) -> None:
    """优雅停止。**不要**改成 kill -9：会导致浏览器登录态丢失。"""
    if use_child_backend():
        _portable_stop()
        return
    _act("stop", unit)


def restart(unit: str = UNIT) -> None:
    if use_child_backend():
        _portable_stop()
        _portable_start()
        return
    _act("restart", unit)


def reset_failed(unit: str = UNIT) -> None:
    if use_child_backend():
        return
    _systemctl("reset-failed", unit)


def _act(verb: str, unit: str) -> None:
    # 启停本身可能要几秒，给足超时
    proc = _systemctl(verb, unit, timeout=60)
    invalidate_status()
    if proc.returncode != 0:
        raise ServiceError((proc.stderr or proc.stdout or f"{verb} 失败").strip())


def is_active(unit: str = UNIT) -> bool:
    if use_child_backend():
        return _portable_pid() is not None
    proc = _systemctl("is-active", "--quiet", unit)
    return proc.returncode == 0


def logs(unit: str = UNIT, lines: int = 300, priority: str | None = None) -> str:
    """取最近的日志文本。"""
    if use_child_backend():
        return "\n".join(_portable_log_text().splitlines()[-lines:])
    args = ["-u", unit, "-n", str(lines), "--no-pager", "-o", "short-iso"]
    if priority:
        args += ["-p", priority]
    try:
        return _journalctl(*args).stdout
    except ServiceError as exc:
        return f"[无法读取日志] {exc}"


def wait_for_url(unit: str = UNIT, attempts: int = 30, delay: float = 1.0) -> str | None:
    """冷启动后轮询等待可用的带 token 地址（与 dsh-open 同样的策略）。"""
    import time

    for _ in range(attempts):
        url = current_url(unit)
        if url and url_is_alive(url):
            return url
        time.sleep(delay)
    return None


def open_in_browser(url: str | None = None) -> None:
    """交给系统的默认浏览器。"""
    import webbrowser

    if url:
        webbrowser.open(url)
        return
    # 没给地址就走 dsh-open（它会自己取地址并在失败时弹通知）
    script = Path.home() / ".local" / "bin" / "dsh-open"
    if script.exists():
        _run([str(script)], timeout=60)
    else:
        resolved = wait_for_url()
        if resolved:
            webbrowser.open(resolved)
        else:
            raise ServiceError("拿不到可用的访问地址")


@dataclass(slots=True)
class PluginInfo:
    """profile 里装的一个插件。"""

    name: str
    version: str = ""
    spec: str = ""
    in_bundles: bool = False


def list_plugins(profile_dir: Path | None = None) -> list[PluginInfo]:
    """列出 web profile 已安装的插件（读 package.json，不跑 pnpm）。"""
    import json

    profile_dir = profile_dir or (Path.home() / ".dsh" / "profiles" / "web")
    pkg = profile_dir / "package.json"
    if not pkg.exists():
        return []
    try:
        data = json.loads(pkg.read_text(encoding="utf-8"))
    except Exception:
        return []
    bundles = set(data.get("dsh", {}).get("profile", {}).get("bundles", []))
    out: list[PluginInfo] = []
    for name, spec in (data.get("dependencies") or {}).items():
        version = ""
        mod_pkg = profile_dir / "node_modules" / name / "package.json"
        if mod_pkg.exists():
            try:
                version = json.loads(mod_pkg.read_text(encoding="utf-8")).get("version", "")
            except Exception:
                version = ""
        out.append(
            PluginInfo(name=name, version=version, spec=str(spec), in_bundles=name in bundles)
        )
    return out
