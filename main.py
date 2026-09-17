#!/usr/bin/env python3
"""DSH 控制台入口。

    ./run.sh              启动控制台（解释器交给 run.sh 挑，见下）
    ./run.sh --tui        启动终端版前端（TUI）
    ./run.sh --gui        启动 PySide6 原生客户端（GUI 版）
    python main.py        直接启动；解释器不对会自动换成对的那个
    python main.py --theme daylight

控制台必须跑在 **Python 3.14** 上：会话文件是 zstd 压缩的，解压走标准库
``compression.zstd``（3.14 才进标准库），没有第三方替代品。PySide6 则装在 venv 里，
因为系统 Python 有 PEP 668 的 ``EXTERNALLY-MANAGED`` 保护，装不进去。

所以推荐用项目自带的 venv：在项目根建一个 ``.venv``（``python3.14 -m venv .venv``）
再装 ``requirements.txt``。想用别的环境就设 ``DSH_CONSOLE_VENV`` 指过去——
源码里**不写死任何绝对路径**：别人 clone 下来照着 README 做就能跑起来。
``_ensure_interpreter()`` 会校验当前解释器，不合格就 ``exec`` 换成合格的——
这样三个入口（``run.sh`` / ``main.py`` / 桌面启动器）不会因为解释器不同而行为不一致。
"""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT))

from dsh_console import subproc  # noqa: E402

#: 运行时下限。改这里要同步改 requirements.txt、tools/find-python.sh 和 README。
MIN_PYTHON = (3, 14)
MIN_PYTHON_TEXT = "3.14"

#: 可选的"标准运行时"目录，由 ``DSH_CONSOLE_VENV`` 指定。
#:
#: ⚠️ **这里绝不能写死某个绝对路径。** 早先写死过开发机上的 ``/home/<用户名>/…``，
#: 后果有两个：源码里泄露用户名，而且别人拿到代码后，错误提示会教他去修一条
#: 根本不存在、也不该存在的路径。默认应该是"用项目自带的 .venv"，那才是通用做法。
DEFAULT_VENV = (
    Path(os.environ["DSH_CONSOLE_VENV"]).expanduser()
    if os.environ.get("DSH_CONSOLE_VENV")
    else None
)

#: 重新执行过的标记，防止 exec 自己套自己。
_REEXEC_FLAG = "DSH_CONSOLE_REEXEC"


def _python_candidates() -> list[str]:
    """按优先级列出候选解释器（与 tools/find-python.sh 的顺序一致）。"""
    out: list[str] = []
    override = os.environ.get("DSH_CONSOLE_PYTHON")
    if override:
        # 显式指定就只认它：用户说用哪个就用哪个，悄悄换掉最难排查。
        return [override]
    # 显式指定的标准运行时排在最前；没设就跳过这一档。
    if DEFAULT_VENV is not None:
        out.append(str(DEFAULT_VENV / "bin" / "python"))
    # 项目自带的 .venv：通用做法，谁 clone 下来建一个就能跑。
    out.append(str(_ROOT / ".venv" / "bin" / "python"))
    for name in ("python3.14", "python3"):
        found = shutil.which(name)
        if found:
            out.append(found)
    return out


def _interpreter_problem(exe: str, need_gui: bool) -> str | None:
    """解释器不可用时返回原因，可用时返回 None。

    在子进程里探测：失败的解释器会直接死在 import 上，本进程内试不出来。
    """
    if not exe or not (os.path.isfile(exe) and os.access(exe, os.X_OK)):
        return "不存在或不可执行"
    code = (
        "import sys\n"
        f"if sys.version_info < {MIN_PYTHON!r}:\n"
        "    raise SystemExit(2)\n"
    )
    if need_gui:
        code += "import PySide6\n"
    try:
        proc = subproc.run(
            [exe, "-c", code], capture_output=True, timeout=60, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"无法执行（{type(exc).__name__}: {exc}）"
    if proc.returncode == 0:
        return None
    if proc.returncode == 2:
        return f"Python 版本低于 {MIN_PYTHON_TEXT}，没有 compression.zstd"
    if need_gui:
        return "没装 PySide6"
    return "探测失败"


def _ensure_interpreter(need_gui: bool) -> None:
    """校验当前解释器；不合格就换成合格的（进程内 exec，不额外留一个进程）。"""
    problem = _interpreter_problem(sys.executable, need_gui)
    if problem is None:
        return

    if not os.environ.get(_REEXEC_FLAG):
        for exe in _python_candidates():
            # 只比路径字面量，**不能** resolve 符号链接：venv 的 bin/python 本身就是
            # 指向系统解释器的软链，"同一个二进制"不等于"同一个环境"——venv 的身份
            # 来自旁边的 pyvenv.cfg，按 realpath 比会把正确的 venv 误判成"就是我自己"
            # 而跳过，直接掉进下面的报错分支。真正防 exec 套娃的是 _REEXEC_FLAG。
            if os.path.abspath(exe) == os.path.abspath(sys.executable):
                continue
            if _interpreter_problem(exe, need_gui) is not None:
                continue
            if os.environ.get("DSH_CONSOLE_PYTHON"):
                print(f"改用 DSH_CONSOLE_PYTHON 指定的解释器：{exe}", file=sys.stderr)
            else:
                print(
                    f"{sys.executable} 不能跑这个控制台，改用 {exe}",
                    file=sys.stderr,
                )
            os.environ[_REEXEC_FLAG] = "1"
            os.execv(exe, [exe, str(_ROOT / "main.py"), *sys.argv[1:]])

    want = "Python >= " + MIN_PYTHON_TEXT + (" 且已装 PySide6" if need_gui else "")
    # 补依赖的建议要落在**实际会用到的那一个**解释器上：设了 DSH_CONSOLE_VENV 就用它，
    # 否则给项目自带的 .venv（连同"还没建就先建一个"的命令）。
    venv = DEFAULT_VENV or (_ROOT / ".venv")
    hint = f"  {venv / 'bin' / 'python'} -m pip install -r {_ROOT / 'requirements.txt'}"
    if DEFAULT_VENV is None:
        hint += (
            f"\n  （还没有 venv 就先建一个：python3.14 -m venv {_ROOT / '.venv'}）"
        )
    print(
        f"解释器不可用：{sys.executable}\n"
        f"  原因：{problem}\n"
        f"  控制台需要：{want}\n\n"
        f"用 run.sh 启动（它会自动挑解释器）：\n"
        f"  {_ROOT / 'run.sh'}\n"
        f"或补齐依赖：\n{hint}",
        file=sys.stderr,
    )
    raise SystemExit(1)


def _frontend_flag(raw: list[str]) -> tuple[str, list[str]] | None:
    """如果命令行里带了 ``--tui`` / ``--gui``，返回 ``(前端名, 去掉该 flag 的其余参数)``。

    分流必须在 argparse **之前**做：argparse 一看到 ``--tui --help`` 就会拿 ``--help``
    打印**控制台自己**的帮助然后退出，用户永远看不到前端的帮助。前端各有自己的一套
    参数（``--cwd`` / ``--dsh`` / ``--theme`` …），本来就该整包转交。
    """
    for flag, name in (("--tui", "tui"), ("--gui", "gui")):
        if flag in raw:
            return name, [a for a in raw if a != flag]
    return None


def main(argv: list[str] | None = None) -> int:
    # 先定解释器再解析参数：参数解析本身也需要一个能跑的解释器。
    raw = sys.argv[1:] if argv is None else argv
    # --tui 只用 curses，不需要 PySide6；其余入口都要建 Qt 窗口。
    need_gui = not any(a in ("--list-themes", "--self-test", "--tui") for a in raw)
    _ensure_interpreter(need_gui)

    picked = _frontend_flag(raw)
    if picked is not None:
        name, rest = picked
        if name == "tui":
            from dsh_console.tui import main as tui_main

            return tui_main(rest)
        from dsh_console.gui import main as gui_main

        return gui_main(rest)

    parser = argparse.ArgumentParser(description="DSH Harness 桌面控制台")
    parser.add_argument("--theme", help="启动时使用的主题 key（见 --list-themes）")
    parser.add_argument("--list-themes", action="store_true", help="列出所有主题后退出")
    parser.add_argument("--self-test", action="store_true", help="无界面自检后退出")
    parser.add_argument("--tui", action="store_true", help="启动终端版前端（TUI，其余参数转交它）")
    parser.add_argument("--web", action="store_true",
                        help="确保 harness 的 web 服务在跑，并用浏览器打开它")
    parser.add_argument("--gui", action="store_true", help="启动 PySide6 原生客户端（GUI，其余参数转交它）")
    args = parser.parse_args(argv)

    from dsh_console import themes

    if args.list_themes:
        for t in themes.THEMES:
            print(f"{t.key:10} {t.name:8} {t.tag}")
        return 0

    if args.self_test:
        return _self_test()

    _install_console_error_hook()      # 界面起来之前装好，槽里的异常才抓得到

    if args.web:
        return _open_web()

    # 后台线程里有 CPU 密集的活（解压会话文件、json.loads 大文档）。CPython 默认
    # 每 5 ms 才考虑切换 GIL，8 个后台线程就意味 GUI 线程最坏要等 40 ms 才拿到一次
    # 执行权——表现出来就是拖动/点击发涩。调到 1 ms 把最坏等待压到个位数毫秒，
    # 代价是后台吞吐略降（实测扫描 1.76s → 约 2s），换来界面明显更跟手。
    sys.setswitchinterval(0.001)

    from PySide6.QtWidgets import QApplication

    from dsh_console import i18n
    from dsh_console.ui.main_window import MainWindow

    # ⚠️ 顺序要紧：语言环境必须在 QApplication **之前**定下来（Chromium 的 --lang
    # 一旦引擎初始化就读不到了），翻译器则在 QApplication **之后**装。
    i18n.prepare_environment()
    app = QApplication(sys.argv[:1])
    _translators = i18n.install_translators(app)      # 必须保持引用，否则翻译失效
    app.setApplicationName("DSH 控制台")
    app.setApplicationDisplayName("DSH 控制台")
    app.setDesktopFileName("dsh-console")

    win = MainWindow()
    if args.theme:
        win.apply_theme(args.theme)
    win.show()
    code = app.exec()
    # 等还在跑的后台任务收尾，避免线程在解释器析构时被硬杀
    from dsh_console.workers import drain

    drain(3000)
    return code


def _open_web() -> int:
    """保证 web 服务在跑，再把带 token 的地址交给浏览器。

    便携包里这就是"启动 DSH 网页界面"那条路：不用先开控制台点按钮。
    """
    from dsh_console import service

    try:
        st = service.get_status()
        if not st.is_running:
            print("正在启动 harness 的 web 服务…")
            service.start()
        url = service.wait_for_url(attempts=40, delay=1.0)
    except Exception as exc:  # noqa: BLE001 - 起不来要说人话，不是抛栈
        print(f"启动失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        if service.portable.enabled():
            print(f"（便携模式）日志在 {service.portable.run_dir()}/web.log", file=sys.stderr)
        return 1
    if not url:
        print("服务起来了，但等不到可用的访问地址。", file=sys.stderr)
        return 1
    print(f"地址已就绪：{url.split('?')[0]}")
    service.open_in_browser(url)
    return 0


def _check_frontend_argv() -> list[str]:
    """把每个前端生成的 argv 喂给它自己的解析器，返回对不上的说明。

    只比"参数"部分：argv 形如 ``[python, "-m", "dsh_console.xxx", ...]``，
    后面前端自己的参数才是要验的。
    """
    import importlib

    from dsh_console import frontends

    checks = [
        ("TUI", "dsh_console.tui", frontends.tui_argv),
        ("GUI", "dsh_console.gui", frontends.gui_argv),
        ("内置浏览器", "dsh_console.browser", frontends.browser_argv),
    ]
    bad: list[str] = []
    for label, module_name, builder in checks:
        try:
            argv = builder(str(frontends.DEFAULT_WORKSPACE), frontends.dsh_executable())
            module = importlib.import_module(module_name)
            parser = getattr(module, "_parse", None)
            if parser is None:
                continue
            tail = argv[3:]          # 去掉解释器、-m、模块名
            try:
                parser(tail)
            except SystemExit:
                bad.append(f"{label} 不接受 {' '.join(tail)}")
        except Exception as exc:  # noqa: BLE001 - 自检不该因为导入失败而中断
            bad.append(f"{label} 检查失败：{type(exc).__name__}: {exc}")
    return bad


def _check_main_block_last() -> list[str]:
    """检查每个前端入口的 ``__main__`` 块都在文件末尾。

    ``python -m dsh_console.xxx`` 时 ``__name__ == "__main__"``，模块执行到
    ``if __name__ == "__main__": raise SystemExit(main())`` 就退出了——
    **后面再有任何定义都不会执行**。把新代码追加到它后面，表现是
    "import 测试正常、-m 跑就 NameError"（实测踩过：主题对话框就这么变成了死按钮）。
    """
    import re

    root = Path(__file__).resolve().parent / "dsh_console"
    bad: list[str] = []
    for name in ("browser.py", "gui.py", "tui.py"):
        path = root / name
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        idx = next((i for i, l in enumerate(lines) if l.startswith("if __name__")), None)
        if idx is None:
            continue
        after = [l for l in lines[idx:] if re.match(r"^(def |class )", l)]
        if after:
            bad.append(f"{name}：__main__ 之后还有 {len(after)} 个定义（{after[0][:40]}…）")
    return bad


def _install_console_error_hook() -> None:
    """让 Qt 槽里的异常**可见**。

    PySide6 对槽函数里抛出的异常只调 ``PyErr_Print()``（打到 stderr）就继续跑，
    界面上完全看不出来——表现就是"按了没反应"。这条在内置浏览器那边已经吃过一次亏
    （一个 NameError 让主题按钮变成死按钮），控制台这边同理：**没有钩子就没有线索**。
    现在出异常会弹框，并指引去看 stderr / 日志。
    """
    import traceback

    def hook(exc_type, exc, tb) -> None:
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        print(text, file=sys.stderr, flush=True)
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox

            if QApplication.instance() is None:
                return
            QMessageBox.critical(
                None, "控制台出错",
                text[-1800:] + "\n\n（这条异常本来会被 Qt 静默吞掉，是钩子把它显示出来的）",
            )
        except Exception:  # noqa: BLE001
            pass

    sys.excepthook = hook


def _tui_selftest() -> None:
    """TUI 的版面与滚动逻辑自检（不建终端也能跑）。

    这里检查的都是"改了容易悄悄坏掉、坏掉又不报错"的东西：右侧说明面板有没有溢出、
    两个区的滚动方向有没有搞反（它们的约定是相反的）、对话区折行用的是终端宽度还是
    左侧列宽。最后一条真出过问题——右对齐的用户消息整个飘到面板上去了。
    """
    from dsh_console import tui as T

    app = T.TuiApp(dsh="dsh", profile="acp", cwd="/tmp")
    fails: list[str] = []

    # 1) 几何：宽终端给足面板，窄终端藏起来
    for cols, want in ((120, (78, 42)), (90, (48, 42)), (70, (70, 0)), (200, (158, 42))):
        got = app._layout(cols)
        if got != want:
            fails.append(f"版面 {cols} 列 -> {got}，期望 {want}")

    # 2) 说明面板：每行都不许超出面板宽度，且内容不为空
    for w in (42, 38, 30, 24):
        _, lines = app._help_lines_for(w)
        if not lines:
            fails.append(f"面板宽 {w} 没有内容")
        for text, _ in lines:
            if T.display_width(text) > max(8, w - 3):
                fails.append(f"面板宽 {w} 有行超宽：{text!r}")
                break

    # 3) 滚动方向：两个区约定相反，这里锁住它
    app.view_h, app.main_w, app.help_w = 10, 78, 42
    app._lines = [("x", 0)] * 50
    app.focus = T.FOCUS_MAIN
    app._scroll(5)
    if app.scroll != 5:
        fails.append(f"对话区 _scroll(+5) -> {app.scroll}，期望 5")
    app._scroll(999)
    if app.scroll != 40:
        fails.append(f"对话区滚动没钳住：{app.scroll}，期望 40")
    app.focus = T.FOCUS_HELP
    app._panel_rows = lambda: 12              # 内容 34 行 > 12 行，必然可滚
    app.help_scroll = 0
    app._scroll(-1)                           # ↓ / PgDn
    if app.help_scroll <= 0:
        fails.append("面板：↓ 方向没有让 help_scroll 增加（方向反了）")
    app._scroll(1)                            # ↑ / PgUp
    if app.help_scroll != 0:
        fails.append(f"面板：↑ 方向没回到顶：{app.help_scroll}")

    # 4) 用户消息右对齐、且不越出对话区列宽
    item = T.Item(kind="user", text="只回答两个字：收到")
    rows = app._user_lines(item, 78)
    if not rows or not rows[0][0].startswith(" "):
        fails.append("用户消息没有右对齐")
    for text, _ in rows:
        if T.display_width(text) > 78:
            fails.append(f"用户消息行超出对话区宽度：{T.display_width(text)} > 78")
            break

    # 5) 焦点 / 面板开关
    app.focus = T.FOCUS_MAIN
    app._toggle_focus()
    if app.focus != T.FOCUS_HELP:
        fails.append("Tab 没切换焦点")
    app.help_visible = True
    app._toggle_help()
    if app.help_visible or app.focus != T.FOCUS_MAIN:
        fails.append("隐藏面板时没把焦点收回对话区")

    # 6) 会话/模型/工作区：用假 client 走一遍真实代码路径
    #
    # 这一段是被一个真 bug 逼出来的：`_pick_session` 里用了个没定义的 `_short_cwd`，
    # 它跑在后台线程里，界面只显示一句"读取会话列表失败：NameError"——自检不碰这些
    # 代码路径就永远发现不了。这里不连 harness，只喂假数据，几百毫秒就够。
    import time as _time

    class _FakeClient:
        def list_sessions(self, cwd=None):
            return [
                {"sessionId": "aaaaaaaa-1111", "cwd": "/tmp"},
                {"sessionId": "bbbbbbbb-2222", "cwd": "/home/user/project"},
            ]

    app.client = _FakeClient()          # type: ignore[assignment]
    app.session_established = True
    app.options = [
        {"id": "model", "name": "Model", "type": "select",
         "currentValue": '["p","m2"]',
         "options": [{"group": "g", "options": [
             {"value": '["p","m1"]', "name": "M1"},
             {"value": '["p","m2"]', "name": "M2"}]}]},
        {"id": "reasoning_effort", "name": "Reasoning effort", "type": "select",
         "currentValue": "high",
         "options": [{"value": "high", "name": "High"}, {"value": "low", "name": "Low"}]},
    ]
    for action, want in (("_pick_session", "切换会话"), ("_pick_workspace", "切换工作区")):
        app.picker = None
        app.events = __import__("queue").Queue()
        getattr(app, action)()
        got = None
        for _ in range(100):                     # 等后台线程把选择器放进来
            _time.sleep(0.01)
            if not app.events.empty():
                kind, payload = app.events.get_nowait()
                got = payload if kind == "open_picker" else None
                if got is not None:
                    break
        if got is None:
            fails.append(f"{action} 没有产出选择器（后台线程出错？）")
        elif want not in got.title:
            fails.append(f"{action} 的选择器标题不对：{got.title!r}")

    app.picker = None
    app._pick_model()
    if app.picker is None:
        fails.append("_pick_model 没有打开选择器")
    else:
        rows = app.picker.rows()
        if len(rows) != 4:
            fails.append(f"_pick_model 应当列出 4 项，实际 {len(rows)}")
        picked = app.picker.current()
        if picked is None or "M2" not in picked.label:
            fails.append(f"_pick_model 没有预选当前项：{picked.label if picked else None}")
    # 工作区选择器要能接受手敲的路径
    ws = T.Picker("切换工作区", [T.PickItem("/tmp", "/tmp")], allow_custom=True)
    ws.query = "/tmp/不存在但允许输入"
    if ws.rows()[0].value != "/tmp/不存在但允许输入":
        fails.append("工作区选择器没有把输入当成候选")

    if fails:
        print(f"  ⚠️ {len(fails)} 项异常：")
        for f in fails:
            print(f"     - {f}")
    else:
        print("  ✓ 版面几何 / 说明面板 / 滚动方向 / 用户消息对齐 全部正确")
        print(f"     说明面板宽 42 列时内容 {len(app._help_lines_for(42)[1])} 行")


def _self_test() -> int:
    """不开窗口，验证数据层可用。"""
    import json

    from dsh_console import billing, market, plugin_manager, pricing, service, themes

    problems = 0        # 本段自己累计；不能借用函数后面才定义的同名变量

    print("== 界面层导入检查 ==")
    # ⚠️ 这一项是补上的，代价很实在：`dsh_console/ui/page_harness.py` 里一行
    # `from . import subproc`（应为 `from ..`）写错了导入层级，而**自检当时全绿**——
    # 因为自检压根不导入界面层。结果是：`--self-test`、`--list-themes` 都正常，
    # 用户一双击图形界面就 ImportError 退出（Windows 上还没有控制台，什么都看不到）。
    # 所以这里把界面层逐个导入一遍，让"能不能起来"这件事在自检里就有答案。
    import importlib
    import pkgutil

    import dsh_console.ui as _ui

    ui_bad: list[str] = []
    for mod in pkgutil.iter_modules(_ui.__path__):
        name = f"dsh_console.ui.{mod.name}"
        try:
            importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 - 自检要把任何异常都当问题报出来
            ui_bad.append(f"{name}: {type(exc).__name__}: {exc}")
    if ui_bad:
        for line in ui_bad:
            print(f"  ✗ {line}")
        print(f"  ✗ 界面层有 {len(ui_bad)} 个模块导入失败——图形界面起不来")
        problems += 1
    else:
        count = len(list(pkgutil.iter_modules(_ui.__path__)))
        print(f"  ✓ 界面层 {count} 个模块全部导入正常")

    print("== 名字静态检查 ==")
    # 这个坑踩过三次（`_ThemeDialog`、`apply_screen_fit` ×2）：名字写在函数体里、
    # 真正点按钮时才炸，而 compileall 和 import 都是绿的。所以放进自检。
    # 这不是 lint，只查"用了但哪一层都没绑定、也不在内建里"的名字。
    sys.path.insert(0, str(Path(__file__).resolve().parent / "tools"))
    try:
        import namecheck  # type: ignore[import-not-found]

        root = Path(__file__).resolve().parent / "dsh_console"
        bad = [(f, n, ln) for f in sorted(root.rglob("*.py")) for n, ln in namecheck.check(f)]
        if bad:
            for f, n, ln in bad[:10]:
                print(f"  ✗ {f.name}:{ln} 未定义的名字 {n!r}")
            print(f"  ✗ 共 {len(bad)} 处——这些一旦被调用就会 NameError")
            problems += 1
        else:
            print(f"  ✓ 扫描 {len(list(root.rglob('*.py')))} 个文件，没有未定义的名字")
    except Exception as exc:  # noqa: BLE001 - 检查器本身出问题不该让自检挂掉
        print(f"  ⚠️ 检查器没跑起来：{type(exc).__name__}: {exc}")

    print("\n== 价格 ==")
    phase = pricing.peak_phase_at()
    print(f"当前：{phase.label if phase else '未知'}  倒计时 {phase.countdown_text if phase else '—'}")
    print(f"峰时段（本地）：{pricing.local_windows_text()}")

    print("\n== 服务 ==")
    st = service.get_status()
    print(json.dumps(
        {
            # 单元状态（systemctl）与 harness 实测状态要分开看：手工在终端敲 dsh web
            # 时前者是 inactive、后者才是事实。
            "unit_active": st.active_state,
            "unit_sub": st.sub_state,
            "harness_running": st.is_running,
            # 便携模式没有 systemd，harness 是控制台拉起的子进程——这里如实报 False，
            # 否则 Windows/便携包上会显示"由 systemd 管理"，纯属胡说
            "managed_by_systemd": st.unit_running and not service.portable.enabled(),
            "label": st.label,
            "pid": st.effective_pid,
            "listener": st.listener_name or None,
            "uptime": st.uptime_text,
            "last_run": st.last_run_text,
            "stopped_for": st.stopped_for_text,
            "memory": st.memory_text,
            "memory_source": "systemd-cgroup" if st.memory_bytes else (
                "proc-vmrs" if st.listener_rss else None
            ),
            "restarts": st.n_restarts,
            "port": st.port,
            "url_ok": bool(st.url) and service.url_is_alive(st.url),
            "unit_file": st.unit_file_state,
            "linger": st.linger,
            "error": st.raw_error,
        },
        ensure_ascii=False,
        indent=2,
    ))

    print("\n== 已安装插件 ==")
    for p in plugin_manager.installed():
        print(
            f"  {p.name:34} v{p.version:10} {p.source:8} {p.status_text:6}"
            f" rows={p.row_ids} scripts={p.has_install_scripts}"
        )
    print(f"  patch 层托管停用项：{plugin_manager.managed_disabled_ids() or '(空)'}")

    print("\n== 技能市场（npm dsh-plugin + skill） ==")
    try:
        sk, err = market.search_skills("")
        if err:
            print(f"  ⚠️ {err}")
        ranked_sk = market.sort_plugins(sk, "downloads")
        print(f"  技能类插件 {len(sk)} 个")
        for i, p in enumerate(ranked_sk[:5], 1):
            print(f"  {i}. {p.name:32} 周下载 {p.weekly_downloads:>8,}  评分 {p.rating:.2f}")
    except Exception as exc:
        print(f"  ⚠️ 技能市场自检跳过：{type(exc).__name__}: {exc}")

    print("\n== 插件市场（npm dsh-plugin） ==")
    try:
        items, err = market.search("")
        if err:
            print(f"  ⚠️ {err}")
        ranked = market.sort_plugins(items, "downloads")
        print(f"  拉到 {len(items)} 个插件")
        for i, p in enumerate(ranked[:5], 1):
            print(
                f"  {i}. {p.name:32} 周下载 {p.weekly_downloads:>8,}"
                f"  评分 {p.rating:.2f}  {p.freshness}"
            )
        ratings = sorted({round(p.rating, 1) for p in items})
        print(f"  评分区间 {ratings[0]} ~ {ratings[-1]}（有区分度才是有效评分）")
    except Exception as exc:
        print(f"  ⚠️ 市场自检跳过：{type(exc).__name__}: {exc}")

    print("\n== harness 版本 ==")
    from dsh_console import harness

    hinfo = harness.info()
    if hinfo.error and not hinfo.installed:
        print(f"  ⚠️ {hinfo.error}")
    else:
        print(f"  已安装：{hinfo.installed}　（{hinfo.install_path}）")
        bits = []
        if hinfo.node_version:
            bits.append(f"Node {hinfo.node_version}")
        if hinfo.npm_version:
            bits.append(f"npm {hinfo.npm_version}")
        if hinfo.registry:
            bits.append(f"registry {hinfo.registry}")
        print("  " + "　·　".join(bits))
        print(f"  升级权限：{hinfo.permission_note}")
        rel = harness.releases()
        if rel.error:
            print(f"  ⚠️ 取不到 npm 版本信息：{rel.error}")
        else:
            for channel in harness.CHANNELS:
                version = rel.tags.get(channel, "")
                mark = " ↑ 可升级" if harness.is_newer(version, hinfo.installed) else ""
                print(f"  {channel:7}{version or '—'}{mark}")
            print(f"  升级命令：{' '.join(harness.update_command('latest'))}")

    print("\n== TUI 版面 ==")
    _tui_selftest()

    print("\n== 三种前端 ==")
    from dsh_console import frontends

    dsh_bin = frontends.dsh_executable()
    print(f"  dsh 可执行文件：{dsh_bin or '⚠️ 找不到，TUI/GUI 连不上 harness'}")
    tui_cmd = frontends.tui_argv(frontends.DEFAULT_WORKSPACE, dsh_bin)
    gui_cmd = frontends.gui_argv(frontends.DEFAULT_WORKSPACE, dsh_bin)
    # Windows 上没有终端模拟器表（那全是 Linux 终端），是 cmd start 新开窗口，
    # 这里要跟着走同一套判断，否则自检会报"没找到可用的终端模拟器"把人吓一跳。
    picked = None if os.name == "nt" else frontends.terminal_argv(
        tui_cmd, cwd=frontends.DEFAULT_WORKSPACE)
    if picked:
        print(f"  TUI 版：终端 = {picked[2]}")
        print(f"          实际命令 = {' '.join(picked[0])}")
    elif os.name == "nt":
        # Windows 上没有终端模拟器表（那张表全是 Linux 终端），是 cmd start 新开窗口
        print("  TUI 版：用 cmd start 新开一个控制台窗口运行")
    else:
        print("  TUI 版：⚠️ 没找到可用的终端模拟器，请在自己的终端里运行：")
    print(f"          {shlex.join(tui_cmd)}")
    print(f"  GUI 版：{shlex.join(gui_cmd)}")
    try:
        import PySide6.QtWebEngineWidgets  # noqa: F401

        print("          界面 = harness 自己的 web UI（QtWebEngine 装进原生窗口，与 web 版一致）")
    except Exception as exc:  # noqa: BLE001
        print(f"          ⚠️ QtWebEngine 不可用（{type(exc).__name__}），GUI 版会提示缺依赖")
    print(f"          --native 退路 = {shlex.join([*gui_cmd, '--native'])}")
    print(f"          日志 = {frontends.log_path('gui')}")
    if service.portable.enabled():
        print("  web 版：便携包内直接拉起 harness 子进程（无 systemd，见上面的服务状态）")
    else:
        print("  web 版：systemd 单元 dsh-web（见上面的服务状态）")
    from dsh_console import browser as browser_mod

    print(f"  内置浏览器：{browser_mod.describe()}")

    # 每个前端"生成的 argv"必须能被它**自己的**参数解析器接受。
    # 这条是被一个真实 bug 逼出来的：browser_argv 抄了 gui_argv 的写法多传了两个参数，
    # 而 browser 的解析器不认——进程起来就退出，用户只看到"点了没反应"，
    # 日志里才有 unrecognized arguments。静态看代码完全看不出来。
    for note in _check_main_block_last():
        print(f"  ⚠️ {note}")

    problems = _check_frontend_argv()
    if problems:
        print("  ⚠️ 启动参数对不上：" + "；".join(problems))
    else:
        print("  ✓ 三个前端的启动参数都能被各自的解析器接受")

    print("\n== 会话花费 ==")
    sessions = billing.scan_all_sessions()
    total = sum(s.cost_usd for s in sessions)
    for s in sessions:
        print(
            f"  {s.session_id[:12]}  调用 {s.calls:>4}  tokens {s.total_tokens:>10,}"
            f"  {pricing.fmt_cny(pricing.cny(s.cost_usd))}"
        )
    print(f"  合计：{pricing.fmt_cny(pricing.cny(total))} / {pricing.fmt_usd(total)}")

    print("\n== 余额 ==")
    bal = billing.fetch_balance()
    print(f"  key={billing.mask_key(billing.read_api_key())} ok={bal.ok} err={bal.error}")
    if bal.ok and bal.primary:
        p = bal.primary
        print(f"  总余额 {p.symbol}{p.total:,.2f}（充值 {p.topped_up:,.2f} / 赠送 {p.granted:,.2f}）")

    print("\n== 主题 ==")
    for t in themes.THEMES:
        qss = themes.build_qss(t)
        assert len(qss) > 500
    print(f"  {len(themes.THEMES)} 套主题全部渲染 OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
