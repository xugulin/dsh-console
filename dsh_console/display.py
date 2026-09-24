"""显示器服务：确保它在跑。

「显示器」面板要有画面，前提是 `dsh-display-viewer` 服务在跑。Linux 上它是 systemd
用户单元（见插件仓库的 install-service.sh），Windows 上没有 systemd 对应机制 ——
用户很容易**装好插件却从不启动服务**，于是面板永远停在「显示器还没有打开」
（真实反馈过）。所以控制台启动时顺手把它拉起来。

按优先级三条路：

1. 已经有实例在跑（探测 8099..8110，端口可能因占用而后移）→ 什么都不做；
2. Linux 且装了 systemd 用户单元 → ``systemctl --user start``（最干净，开机自启也归它管）；
3. 其余情况 → 用当前解释器 detached 启动 ``dsh-display-viewer.py``。

**失败不影响控制台**：这只是"顺手帮忙"，不是控制台的依赖，出错只记一条日志。
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path

from . import subproc

#: 与服务端、插件端保持一致的端口范围（服务端口被占时会自动往后找）。
VIEWER_PORTS = tuple(range(8099, 8111))

SERVICE = "dsh-display-viewer.service"


def running_port(timeout: float = 0.25) -> int | None:
    """返回正在跑的显示器服务端口；没有则 None。"""
    for port in VIEWER_PORTS:
        try:
            with socket.socket() as sock:
                sock.settimeout(timeout)
                if sock.connect_ex(("127.0.0.1", port)) == 0:
                    return port
        except OSError:
            continue
    return None


def viewer_script() -> Path | None:
    """定位 ``dsh-display-viewer.py``：源码布局与便携包布局都认。"""
    root = Path(__file__).resolve().parent.parent
    for cand in (root / "tools" / "dsh-display-viewer.py",              # 源码仓库
                 root / "app" / "tools" / "dsh-display-viewer.py"):      # 便携包
        if cand.is_file():
            return cand
    return None


def cache_dir() -> Path:
    """显示器服务的状态目录（token、端口、各会话的 Xvfb 信息）。"""
    import os

    base = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache"))
    return base / "dsh-display"


def reset_stale() -> str:
    """清掉显示器服务上次留下的陈旧状态，返回做了什么（可能为空串）。

    要清的原因是**硬杀**：服务被 KILL 掉时来不及回收自己拉起来的 Xvfb，端口和
    lock 文件就留在 ``/tmp/.X11-unix``、``/tmp/.X<n>-lock`` 里；下次启动会因为
    "显示号已被占用"而失败，表现成"显示器面板一直是黑的"。这里只删**没有活进程
    在听**的端口记录，正在跑的服务不受影响。
    """
    removed: list[str] = []
    port_file = cache_dir() / "port"
    if port_file.is_file():
        try:
            recorded = int(port_file.read_text(encoding="utf-8").strip() or 0)
        except (OSError, ValueError):
            recorded = 0
        # 记录里的端口没人听 → 那是上一次运行的残迹，删掉让服务重新挑
        if recorded and running_port() != recorded:
            try:
                port_file.unlink()
                removed.append(f"清掉陈旧端口记录 {recorded}")
            except OSError:
                pass
    locks = 0
    for lock in Path("/tmp").glob(".X*-lock"):
        try:
            pid = int(lock.read_text(encoding="utf-8", errors="replace").strip() or 0)
        except (OSError, ValueError):
            pid = 0
        # lock 文件里第一行是持有它的 X 服务 pid；进程没了就是死锁文件
        if pid and not Path(f"/proc/{pid}").exists():
            try:
                lock.unlink()
                locks += 1
            except OSError:
                pass
    if locks:
        removed.append(f"清掉 {locks} 个陈旧的 X lock 文件")
    return "；".join(removed)


def _start_via_systemd() -> bool:
    """优先走 systemd 用户单元（Linux）。单元不存在时返回 False。"""
    if sys.platform == "win32":
        return False
    try:
        probe = subproc.run(["systemctl", "--user", "list-unit-files", SERVICE],
                            capture_output=True, timeout=5)
        if probe.returncode != 0 or SERVICE.encode() not in (probe.stdout or b""):
            return False
        subproc.run(["systemctl", "--user", "start", SERVICE],
                    capture_output=True, timeout=10)
        return True
    except Exception:                                    # noqa: BLE001
        return False


def _start_detached() -> bool:
    """兜底：用当前解释器 detached 启动服务（Windows 无 systemd 走这条）。"""
    script = viewer_script()
    if script is None:
        return False
    try:
        kwargs = {"stdout": subproc.subprocess.DEVNULL, "stderr": subproc.subprocess.DEVNULL,
                  "stdin": subproc.subprocess.DEVNULL}
        if sys.platform == "win32":
            # Windows：pythonw 没有控制台；没有 pythonw 就用 python + CREATE_NO_WINDOW
            pyw = Path(sys.executable).with_name("pythonw.exe")
            exe = str(pyw) if pyw.is_file() else sys.executable
            subproc.popen([exe, str(script)], **kwargs)
        else:
            kwargs["start_new_session"] = True           # 脱离控制台，不被父进程带走
            subproc.popen([sys.executable, str(script)], **kwargs)
        return True
    except Exception:                                    # noqa: BLE001
        return False


def ensure() -> str:
    """确保显示器服务在跑。返回一句可直接写日志的结果说明（**不抛异常**）。"""
    try:
        port = running_port()
        if port is not None:
            return f"显示器服务已在运行（端口 {port}）"
        if _start_via_systemd():
            for _ in range(20):                          # 等它起来（最多约 4 秒）
                import time

                time.sleep(0.2)
                port = running_port()
                if port is not None:
                    return f"已通过 systemd 启动显示器服务（端口 {port}）"
            return "systemd 启动命令已发出，但服务还没监听"
        if _start_detached():
            import time

            for _ in range(20):
                time.sleep(0.2)
                port = running_port()
                if port is not None:
                    return f"已拉起显示器服务（端口 {port}）"
            return "已尝试拉起显示器服务，但还没有监听端口"
        return "没找到显示器服务脚本（面板需要它才有画面）"
    except Exception as exc:                             # noqa: BLE001
        return f"准备显示器服务时出错（已忽略）：{type(exc).__name__}: {exc}"
