"""三个前端的启动逻辑：web 版 / TUI 版 / GUI 版。

控制台本身是「管家」，这三个才是真正的用户界面：

============  ===========================================================
web 版         ``dsh-web`` systemd 单元，浏览器里用（harness 自带）
TUI 版         ``dsh_console.tui``，终端里的对话界面（本项目自带，走 ACP）
GUI 版         ``dsh_console.gui``，把 **harness 自己的 web UI** 装进原生
               Qt 窗口——界面与功能与 web 版一致（同一个 UI，只是换了外壳）
============  ===========================================================

GUI 版为什么不是"用 Qt 控件复刻一遍"：web UI 是几十个 ``dsh-client-ui-*`` 插件组成的
React 应用，复刻只能做到"像"，做不到一致，还会随 harness 升级而落后。所以窗口里跑的
就是那个 UI 本身。想要不依赖 web 服务的原生聊天窗口，用 ``gui --native``
（:mod:`dsh_console.gui_native`，走 ACP）。

TUI 版和 ``--native`` 都通过 :mod:`dsh_console.acp_client` 连 ``dsh --profile acp``——
harness 明确为外部客户端准备的标准 ACP v1 接口（见该模块的说明）。

## 为什么起一个 TUI 要这么绕

TUI 得有终端才跑得起来，而"从程序里开一个终端跑指定命令"在 Linux 上没有统一做法：

* 大多数终端有 exec 开关（``xterm -e``、``konsole -e``、``gnome-terminal --``…），
  本模块内置了这张表；
* **本机只装了 cosmic-term，而它没有这个开关**（``--help`` 只有 ``--version`` /
  ``--working-directory``）。所以额外提供一条兜底：终端都会用 ``$SHELL`` 启动会话，
  那就把 ``$SHELL`` 指到 ``tools/tui-shell.sh``（实现在那里说明）。
  这条路径是在 Xvfb 上实测过的，不是猜的。

两条路都不通时**不装死**：返回失败原因和可以直接粘贴的命令，界面照原样告诉用户。
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import portable

ROOT = Path(__file__).resolve().parent.parent

#: GUI/TUI 子进程的日志（起不来时靠它排查）。
LOG_DIR = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")) / "dsh-console"

#: 覆盖终端选择，例如 ``DSH_CONSOLE_TERMINAL="xterm -e"``。
TERMINAL_ENV = "DSH_CONSOLE_TERMINAL"

#: 用户显式指定终端时，这些占位符会被替换成真正的命令。
_PLACEHOLDER = "{cmd}"

#: 有 exec 开关的终端：``前缀参数`` 之后直接跟 argv，不需要经过 shell。
#: 顺序即优先级——排在前面的是更"现代/更可能正确传参"的。
TERMINALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("xdg-terminal-exec", ()),                       # XDG 标准助手，直接吃 argv
    ("kgx", ("--",)),                                # GNOME Console
    ("gnome-terminal", ("--",)),
    ("konsole", ("-e",)),
    ("xfce4-terminal", ("-x",)),
    ("mate-terminal", ("-x",)),
    ("tilix", ("-e",)),
    ("alacritty", ("-e",)),
    ("kitty", ()),
    ("wezterm", ("start", "--")),
    ("foot", ()),
    ("qterminal", ("-e",)),
    ("lxterminal", ("-e",)),
    ("sakura", ("-x",)),
    ("terminator", ("-x",)),
    ("xterm", ("-e",)),
    ("st", ("-e",)),
    ("urxvt", ("-e",)),
)

#: 只认 $SHELL 的终端（走 tools/tui-shell.sh）。
SHELL_ONLY_TERMINALS: tuple[str, ...] = ("cosmic-term",)

#: 会话可用的工作目录默认值。
DEFAULT_WORKSPACE = ROOT

#: 拉起 GUI 后等多久再确认它还活着（Qt 建不出窗口时会立刻退出）。
GUI_PROBE_DELAY = 1.5


def as_ok_msg(result) -> tuple[bool, str]:
    """把 :class:`LaunchResult` **或** ``(ok, msg)`` 元组统一成 ``(ok, msg)``。

    为什么要有这个函数：界面里既有"直接 `run_async(launch_browser, done)`"（回调拿到
    `LaunchResult` 对象），也有"包一层 work() 返回元组"的写法。照抄旁边那处的
    `ok, msg = result` 就会在界面上炸
    ``TypeError: cannot unpack non-iterable LaunchResult object``——实测踩过，
    内置浏览器照样打开了，但控制台会弹一个报错框。
    **统一走这里，回调拿到哪种形状都不会再炸。**
    """
    if hasattr(result, "ok") and hasattr(result, "message"):
        return bool(result.ok), str(result.message)
    ok, msg = result
    return bool(ok), str(msg)



@dataclass
class LaunchResult:
    """一次启动尝试的结果。界面直接用 ``ok`` 和 ``message``。"""

    ok: bool
    message: str
    argv: list[str] = field(default_factory=list)
    pid: int = 0
    #: 失败时给用户的可粘贴命令
    manual_command: str = ""


# ------------------------------------------------------------------ 命令拼装
def python_executable() -> str:
    """用当前解释器——控制台是被 run.sh 用对的解释器拉起来的（见 main.py）。"""
    return sys.executable or "python3"


def tui_argv(cwd: str | os.PathLike[str] | None = None, dsh: str | None = None) -> list[str]:
    argv = [python_executable(), "-m", "dsh_console.tui"]
    if cwd:
        argv += ["--cwd", str(cwd)]
    if dsh:
        argv += ["--dsh", dsh]
    return argv


def gui_argv(cwd: str | os.PathLike[str] | None = None, dsh: str | None = None) -> list[str]:
    argv = [python_executable(), "-m", "dsh_console.gui"]
    if cwd:
        argv += ["--cwd", str(cwd)]
    if dsh:
        argv += ["--dsh", dsh]
    return argv


def dsh_executable() -> str | None:
    """``dsh`` 可执行文件的位置。

    **便携包优先**：包里的 dsh 既不在 PATH 上，也不是 npm 全局前缀，只找 PATH
    会得到"找不到 dsh，TUI/GUI 连不上 harness"（在 wine 里实测到的）。
    Windows 上包内给的是 ``dsh.cmd``。
    """
    return portable.dsh_bin() or shutil.which("dsh")


def _shell_env() -> dict[str, str]:
    """子进程环境。

    ``PYTHONPATH`` 指到项目根，这样 ``python -m dsh_console.tui`` 不依赖 cwd——
    终端可能把工作目录设成别处。
    """
    env = dict(os.environ)
    parts = [str(ROOT)]
    if env.get("PYTHONPATH"):
        parts.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(parts)
    env.setdefault("PYTHONUNBUFFERED", "1")
    return env


#: 只在没有显示器时才用的 Qt 平台。
#:
#: 控制台自己可能正被强制成这些平台跑（离屏自查、CI、无头诊断），但用户点
#: 「启动 GUI 版」要的是**一个真窗口**。把这个值原样交给子进程，Qt 就会安静地
#: 在离屏里渲染——进程活着、退出码 0、日志空白，用户只看到"已启动"却什么都没有，
#: 是最难查的那种失败。所以这几个值必须剥掉，让子进程按真实显示器自己选平台。
_HEADLESS_QPA = frozenset({"offscreen", "minimal", "minimalegl", "vnc", "linuxfb", "eglfs"})


def _child_env() -> dict[str, str]:
    """前端子进程的环境：在 ``_shell_env`` 基础上剥掉无头 Qt 平台。"""
    env = _shell_env()
    qpa = (env.get("QT_QPA_PLATFORM") or "").strip()
    if qpa and any(p.strip() in _HEADLESS_QPA for p in qpa.split(";")):
        env.pop("QT_QPA_PLATFORM", None)
    return env


def _log_file(name: str):
    """给子进程一个日志文件。父进程随即关掉自己那一份——子进程已经 dup 了 fd。"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    return open(LOG_DIR / f"{name}.log", "a", encoding="utf-8")


def _shell_terminal(
    exe: str, argv: list[str], cwd: str | os.PathLike[str] | None
) -> tuple[list[str], dict[str, str], str]:
    """走 $SHELL 那条兜底路径（终端没有 exec 开关时）。"""
    wrapper = ROOT / "tools" / "tui-shell.sh"
    env = _child_env()
    env["SHELL"] = str(wrapper)
    env["DSH_CONSOLE_TUI_CMD"] = shlex.join(argv)
    term_argv = [exe]
    if cwd:
        term_argv += ["-w", str(cwd)]
    return term_argv, env, f"{Path(exe).name}（经 tui-shell.sh 转交）"


def terminal_argv(
    argv: list[str],
    terminal: str | None = None,
    cwd: str | os.PathLike[str] | None = None,
) -> tuple[list[str], dict[str, str], str] | None:
    """给 TUI 找一个能承载它的终端。

    返回 ``(argv, 额外环境变量, 说明)``；找不到返回 None。
    """
    override = (terminal or os.environ.get(TERMINAL_ENV) or "").strip()
    if override:
        # 显式指定优先，且不做兜底：用户说用哪个就用哪个，免得"以为在用 A 其实在用 B"
        words = shlex.split(override)
        exe = shutil.which(words[0])
        if exe is None:
            return None
        if _PLACEHOLDER in words:
            cmd = shlex.join(argv)
            return [exe] + [w.replace(_PLACEHOLDER, cmd) for w in words[1:]], _child_env(), override
        if words[0] in SHELL_ONLY_TERMINALS and len(words) == 1:
            return _shell_terminal(exe, argv, cwd)
        return [exe, *words[1:], *argv], _child_env(), override

    for name, prefix in TERMINALS:
        exe = shutil.which(name)
        if exe is not None:
            return [exe, *prefix, *argv], _child_env(), name

    for name in SHELL_ONLY_TERMINALS:
        exe = shutil.which(name)
        if exe is not None and (ROOT / "tools" / "tui-shell.sh").is_file():
            return _shell_terminal(exe, argv, cwd)
    return None


def launch_tui(
    cwd: str | os.PathLike[str] | None = None,
    terminal: str | None = None,
) -> LaunchResult:
    """在终端里打开 TUI 版。"""
    cwd = str(cwd or DEFAULT_WORKSPACE)
    argv = tui_argv(cwd, dsh_executable())
    manual = shlex.join(argv)
    if os.name == "nt":
        # Windows 上没有 TERMINALS 那张表（全是 Linux 终端模拟器），
        # 用 cmd 的 start 新开一个控制台窗口跑 TUI。
        # `start` 的第一个参数会被当成窗口标题，所以必须占位，否则含空格的路径会被吃掉。
        try:
            proc = subprocess.Popen(
                ["cmd", "/c", "start", "DSH 终端界面", *argv],
                env=_child_env(), cwd=cwd,
            )
        except OSError as exc:
            return LaunchResult(False, f"启动终端失败：{exc}", manual_command=manual)
        return LaunchResult(True, "已新开一个控制台窗口运行 TUI 版",
                            argv=["cmd", "/c", "start", *argv], pid=proc.pid,
                            manual_command=manual)
    picked = terminal_argv(argv, terminal=terminal, cwd=cwd)
    if picked is None:
        hint = (
            f"没找到可用的终端模拟器。\n"
            f"可以在自己的终端里直接运行：\n  {manual}\n"
            f"或者指定终端：{TERMINAL_ENV}='xterm -e' ./run.sh"
        )
        return LaunchResult(False, hint, manual_command=manual)
    term_argv, env, how = picked
    try:
        proc = subprocess.Popen(
            term_argv, env=env, cwd=cwd, start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        return LaunchResult(
            False, f"启动终端失败（{how}）：{exc}", manual_command=manual
        )
    return LaunchResult(
        True, f"已用 {how} 打开 TUI 版", argv=term_argv, pid=proc.pid, manual_command=manual
    )


# ------------------------------------------------------------------ 内置浏览器
def browser_argv(cwd: str | os.PathLike[str] | None = None,
                 dsh: str | None = None) -> list[str]:
    """内置浏览器的启动 argv。

    **不传 ``--cwd`` / ``--dsh``**：浏览器只是打开一个 URL，既不切工作目录也不拉起
    harness。这两个参数是给 TUI/GUI 用的（它们要通过 ACP 连 harness），这里照抄过去
    会让浏览器进程"参数不识别 → 立刻退出"，用户看到的就是"点了没反应"——
    实测踩过，日志里一堆 ``unrecognized arguments``。
    """
    del cwd, dsh          # 签名保留是为了和 tui_argv/gui_argv 一致，调用处不用分情况
    return [python_executable(), "-m", "dsh_console.browser"]


def launch_browser(
    cwd: str | os.PathLike[str] | None = None,
    dsh: str | None = None,
) -> LaunchResult:
    """用**随包携带的 Chromium**（QtWebEngine）打开 harness 界面。

    和 :func:`launch_gui` 的区别是定位：GUI 版是"控制台自己的一个窗口"，
    这个是一个**能日常用的浏览器**（多标签、地址栏、独立 profile）。
    两个都用包里的 QtWebEngine，所以用户机器上没装浏览器也能用。

    先确认 QtWebEngine 在不在：不在就当场给一句人话，而不是让子进程起来又崩，
    用户只看到一个黑框闪一下。
    """
    try:
        from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return LaunchResult(
            False,
            f"内置浏览器不可用：QtWebEngine 导入失败（{type(exc).__name__}: {exc}）。"
            f"便携包应当自带它；源码模式可 pip install PySide6-Addons。",
        )

    cwd = str(cwd or DEFAULT_WORKSPACE)
    argv = browser_argv(cwd, dsh or dsh_executable())
    log = None
    try:
        try:
            log = _log_file("browser")
            out = log
        except OSError:
            out = subprocess.DEVNULL
        proc = subprocess.Popen(
            argv, env=_child_env(), cwd=cwd, start_new_session=True,
            stdout=out, stderr=subprocess.STDOUT,
        )
    except OSError as exc:
        return LaunchResult(False, f"打开内置浏览器失败：{exc}",
                            manual_command=shlex.join(argv))
    finally:
        if log is not None:
            log.close()

    # 和 GUI 版一样先探一下活：参数不认、平台插件加载失败这类问题会让进程**立刻**退出，
    # 但 Popen 本身是成功的——不探就会报"已打开"，用户对着空气等（实测踩过：
    # browser_argv 多传了两个参数，日志里一屏 unrecognized arguments，
    # 而控制台显示"已用内置浏览器打开"）。
    time.sleep(GUI_PROBE_DELAY)
    code = proc.poll()
    if code is not None:
        tail = tail_log("browser", 12).strip()
        detail = f"\n日志尾部（{log_path('browser')}）：\n{tail}" if tail else ""
        return LaunchResult(
            False, f"内置浏览器启动后立刻退出（退出码 {code}）。{detail}",
            argv=argv, manual_command=shlex.join(argv),
        )
    return LaunchResult(True, f"已用内置浏览器打开 harness 界面（PID {proc.pid}）",
                        argv=argv, pid=proc.pid, manual_command=shlex.join(argv))


# ------------------------------------------------------------------ GUI
def launch_gui(
    cwd: str | os.PathLike[str] | None = None,
    dsh: str | None = None,
) -> LaunchResult:
    """把 GUI 版作为独立进程拉起来。

    用 ``start_new_session=True`` 脱离控制台的进程组：控制台关掉不该把正在用的
    GUI 一起带走。stdout/stderr 落到日志文件，起不来时有据可查。
    """
    cwd = str(cwd or DEFAULT_WORKSPACE)
    argv = gui_argv(cwd, dsh or dsh_executable())
    log = None
    try:
        try:
            log = _log_file("gui")
            out = log
        except OSError:
            out = subprocess.DEVNULL
        proc = subprocess.Popen(
            argv, env=_child_env(), cwd=cwd, start_new_session=True,
            stdout=out, stderr=subprocess.STDOUT,
        )
    except OSError as exc:
        return LaunchResult(False, f"启动 GUI 版失败：{exc}", manual_command=shlex.join(argv))
    finally:
        if log is not None:
            log.close()          # 子进程已经持有自己的 fd，父进程这份可以关

    # 起来之后再看一眼：Qt 建不出窗口时会**立刻**退出（没有显示器、平台插件加载失败
    # 之类），可它照样会留下一个"已启动（PID …）"的假象。宁可多等这一下，也别让
    # 用户对着一个不存在的窗口发呆。
    time.sleep(GUI_PROBE_DELAY)
    code = proc.poll()
    if code is not None:
        tail = tail_log("gui", 12).strip()
        detail = f"\n日志尾部（{log_path('gui')}）：\n{tail}" if tail else ""
        return LaunchResult(
            False, f"GUI 版启动后立刻退出（退出码 {code}）。{detail}",
            argv=argv, manual_command=shlex.join(argv),
        )
    return LaunchResult(
        True, f"已启动 GUI 版（PID {proc.pid}）", argv=argv, pid=proc.pid,
        manual_command=shlex.join(argv),
    )


def log_path(name: str) -> Path:
    return LOG_DIR / f"{name}.log"


def tail_log(name: str, lines: int = 30) -> str:
    """读子进程日志的尾巴，出错时拿给用户看。"""
    try:
        text = log_path(name).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])
