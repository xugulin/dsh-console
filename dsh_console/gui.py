#!/usr/bin/env python3
"""DSH 控制台 · GUI 版 —— 把 harness 自己的 web UI 装进原生窗口。

## 为什么是"装进去"，而不是"照着做一遍"

harness 的 web UI 不是一张页面，而是一个由几十个 ``dsh-client-ui-*`` 插件组成的
React 应用：会话与轨迹、子代理、计划、目标、作业、命令、工作区文件、设置、主题…
用 Qt 控件复刻，最好也就是"像"，做不到**一致**，而且 harness 每升一次级复刻版就落后一截。

所以这里的做法是：**窗口里跑的就是那个 UI 本身**。
界面与功能与 web 版的一致性是**构造上**保证的——因为它就是 web 版。

GUI 版 = ``QtWebEngineView`` + 一层原生外壳。外壳负责原生该负责的事：

* **起服务**：web 单元没在跑就先拉起来，等带 token 的地址就绪再加载，
  用户不用先开浏览器再去复制地址；
* **持久 profile**：cookie 落在 ``~/.cache/dsh-console/webengine``，
  重开窗口不必重新认证（token 每次服务重启都会换，只靠 URL 里的 token 是不够的）；
* **地址失效自愈**：加载到 401/连接失败时自动重新取一次地址；
* **原生菜单**：重新加载 / 重新取地址 / 在系统浏览器打开 / 复制访问地址 /
  开发者工具 / 缩放 / 全屏；
* **关窗只是关窗**：绝不停掉 harness——那是控制台「停止」按钮的职责。

> 想不用 web 服务的原生聊天窗口，用 ``--native``（见 :mod:`dsh_console.gui_native`）。
> 那条路走 ACP，不依赖浏览器引擎，但它**不是**"与 web 版一致"的那一个。

启动::

    python -m dsh_console.gui                 # 默认：web 同款界面
    python -m dsh_console.gui --url <地址>     # 直接加载指定地址
    python -m dsh_console.gui --no-start      # 绝不启动服务，只连已有的
    python -m dsh_console.gui --native        # 原生 ACP 客户端（另一条路）
"""

from __future__ import annotations

import argparse
import html
import os
import sys
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QStatusBar,
    QWidget,
)

from . import service, themes
from .screenfit import apply_screen_fit

#: webengine 的持久化目录（cookie / 缓存 / 本地存储）。
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")) / "dsh-console"

#: 窗口几何等信息存在这里（QSettings 的 INI 文件）。
SETTINGS_INI = CACHE_DIR / "gui.ini"

#: 取地址时最多等多久（服务冷启动要几秒）。
WAIT_ATTEMPTS = 40
WAIT_DELAY = 1.0


def mask_url(url: str) -> str:
    """只显示 scheme://host:port，**丢掉 token**。

    token 等同于登录凭据，界面上不该出现；剪贴板/状态栏都走这个函数。
    """
    if not url:
        return ""
    try:
        u = QUrl(url)
        host = u.host() or "127.0.0.1"
        port = f":{u.port()}" if u.port() > 0 else ""
        return f"{u.scheme()}://{host}{port}"
    except Exception:  # noqa: BLE001 - 解析失败就退化成空串，别让界面炸
        return ""


def resolve_url(allow_start: bool = True) -> tuple[str | None, str]:
    """拿到一个**可用**的带 token 地址，返回 ``(url, 说明)``。

    顺序是有讲究的：先用 journal 里现有的地址（最常见、零成本），
    验一下 token 还有效就直接用；失效或没有，再考虑启动服务并等新地址。
    """
    url = service.current_url()
    if url and service.url_is_alive(url):
        return url, "使用现有地址"

    st = service.get_status()
    if st.foreign_running:
        # 手工启动的实例：地址只打在它自己的终端里，这里既看不到也不该猜
        return None, (
            f"harness 正在运行（PID {st.listener_pid}），但不是 dsh-web.service 拉起来的，"
            f"带 token 的地址只打印在它自己的终端里，这里取不到。"
            f"要让它归 systemd 管，先在那个终端里结束进程，再点「启动 web 版」。"
        )

    if not allow_start:
        return None, "web 服务没有运行，而且指定了 --no-start。"

    if not st.unit_running:
        try:
            service.start()
        except Exception as exc:  # noqa: BLE001 - 起不来要把原因显示给用户
            return None, f"启动 dsh-web 失败：{exc}"

    url = service.wait_for_url(attempts=WAIT_ATTEMPTS, delay=WAIT_DELAY)
    if not url:
        # 别只说"等不到地址"——最常见的真实原因是端口被占（宿主机上已经跑着一个
        # harness，便携包抢不到 3080）。把日志里的那行 EADDRINUSE 捞出来给用户看，
        # 否则他只会看到"启动了但打不开"。
        tail = service.portable_log_tail(8) if service.portable.enabled() else ""
        if "EADDRINUSE" in tail:
            hint = ("端口被占用：宿主机上已经有一个 harness 在跑（同一个端口）。"
                    "先停掉它，或给便携包换一个端口（环境变量 "
                    "DSH_CONSOLE_WEB_PORT=3081）。")
            return None, hint
        detail = f"\n日志尾部：\n{tail}" if tail else "（看 journalctl --user -u dsh-web）"
        return None, f"服务起来了，但等不到可用的访问地址。{detail}"
    return url, "已启动服务并取得地址"


def error_page(title: str, detail: str) -> str:
    """取不到地址时给用户看的一页，而不是一个空白窗口。"""
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>{html.escape(title)}</title><style>
 html,body{{height:100%;margin:0}}
 body{{display:flex;align-items:center;justify-content:center;
  font-family:system-ui,-apple-system,"Noto Sans CJK SC",sans-serif;
  background:#101418;color:#e6edf3}}
 .box{{max-width:640px;padding:32px 36px;border:1px solid #2a323c;border-radius:14px;
  background:#161b22}}
 h1{{margin:0 0 10px;font-size:19px}}
 p{{margin:0;line-height:1.7;color:#9aa7b4;font-size:14px;white-space:pre-wrap}}
 code{{background:#0d1117;padding:2px 6px;border-radius:5px;color:#8ab4f8}}
</style></head><body><div class="box">
<h1>{html.escape(title)}</h1><p>{html.escape(detail)}</p>
</div></body></html>"""


# --------------------------------------------------------------------- 外壳
# QtWebEngine 是 PySide6-Addons 的一部分，理论上可能没装。导入失败时不在这里抛，
# 而是记下原因、让 main() 给出一句人话（并告诉用户可以用 --native 绕开）。
try:
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
    from PySide6.QtWebEngineWidgets import QWebEngineView

    WEBENGINE_IMPORT_ERROR: str | None = None
except Exception as exc:  # noqa: BLE001
    QWebEnginePage = QWebEngineProfile = QWebEngineView = None  # type: ignore[assignment,misc]
    WEBENGINE_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"

#: 进程级共享的 webengine profile。**必须活得比所有 page 久**，所以挂到
#: QApplication 上并留一个模块级强引用（否则退出时会报 profile 先于 page 释放）。
_PROFILE: object | None = None


def _make_profile():
    """取（或建）那个进程级 profile。"""
    global _PROFILE
    if _PROFILE is not None:
        return _PROFILE
    app = QApplication.instance()
    profile = QWebEngineProfile("dsh-console", app)
    profile.setPersistentStoragePath(str(CACHE_DIR / "webengine"))
    profile.setCachePath(str(CACHE_DIR / "webengine-cache"))
    try:
        profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
        )
    except AttributeError:
        # 枚举路径在不同小版本里挪过位置，取不到就用默认策略（同样会持久化）
        pass
    _PROFILE = profile
    return profile


class WebShell(QMainWindow):
    """把 harness web UI 装进原生窗口的外壳。"""

    def __init__(
        self,
        url: str | None = None,
        note: str = "",
        *,
        allow_start: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("DSH · Web 界面")
        # 按屏幕自适应（小屏上别让窗口跑到屏幕外，和控制台主窗口同一套逻辑）
        apply_screen_fit(self, (1280, 860), (900, 600))
        self._url = url or ""
        self._allow_start = allow_start
        self._note = note
        self.session = QSettings(str(SETTINGS_INI), QSettings.IniFormat)

        # 具名 profile => 持久化：cookie 落盘，重开窗口不用重新认证。
        # 挂在 QApplication 上而不是窗口上：Qt 要求 page 先于 profile 析构，
        # 两者同挂一个父对象时顺序不确定，退出时会打印
        # "Release of profile requested but WebEnginePage still not deleted"。
        self.profile = _make_profile()

        self.view = QWebEngineView(self)
        self.page = QWebEnginePage(self.profile, self.view)
        self.view.setPage(self.page)
        self.setCentralWidget(self.view)

        self._build_menu()
        self._build_status()

        self.view.loadStarted.connect(self._on_load_started)
        self.view.loadProgress.connect(self._on_progress)
        self.view.loadFinished.connect(self._on_load_finished)
        self.page.authenticationRequired.connect(self._on_auth_required)

        self._restore_geometry()
        if self._url:
            self._load(self._url)
        else:
            self._show_unavailable()

    # ------------------------------------------------------------ 界面搭建
    def _build_menu(self) -> None:
        bar = self.menuBar()

        m_file = bar.addMenu("文件(&F)")
        self._add(m_file, "新会话（回到首页）", "Ctrl+N", self._home)
        self._add(m_file, "重新加载", "Ctrl+R", self.view.reload)
        self._add(m_file, "重新获取访问地址", "Ctrl+Shift+R", self._reload_url)
        m_file.addSeparator()
        self._add(m_file, "复制访问地址", "Ctrl+Shift+C", self._copy_url)
        self._add(m_file, "在系统浏览器中打开", "Ctrl+Shift+O", self._open_external)
        m_file.addSeparator()
        self._add(m_file, "退出", "Ctrl+Q", self.close)

        m_view = bar.addMenu("视图(&V)")
        self._add(m_view, "放大", "Ctrl+=", lambda: self._zoom(+0.1))
        self._add(m_view, "缩小", "Ctrl+-", lambda: self._zoom(-0.1))
        self._add(m_view, "重置缩放", "Ctrl+0", self._zoom_reset)
        m_view.addSeparator()
        self._add(m_view, "全屏", "F11", self._toggle_fullscreen)
        self._add(m_view, "开发者工具", "F12", self._devtools)

        m_help = bar.addMenu("帮助(&H)")
        self._add(m_help, "关于", None, self._about)

    def _add(self, menu, text: str, shortcut: str | None, slot) -> None:
        act = QAction(text, self)
        if shortcut:
            act.setShortcut(QKeySequence(shortcut))
        act.triggered.connect(slot)
        menu.addAction(act)

    def _build_status(self) -> None:
        bar = QStatusBar(self)
        self.setStatusBar(bar)
        self.lbl_state = QLabel("准备中…")
        self.lbl_host = QLabel("")
        self.lbl_host.setStyleSheet("color: palette(mid);")
        self.bar = QProgressBar()
        self.bar.setMaximumWidth(160)
        self.bar.setTextVisible(False)
        self.bar.setVisible(False)
        bar.addWidget(self.lbl_state, 1)
        bar.addPermanentWidget(self.lbl_host)
        bar.addPermanentWidget(self.bar)

    # ------------------------------------------------------------ 加载控制
    def _load(self, url: str) -> None:
        self._url = url
        self.lbl_host.setText(mask_url(url))
        self.view.load(QUrl(url))

    def _show_unavailable(self) -> None:
        self.view.setHtml(error_page("取不到 harness 的访问地址", self._note))
        self.lbl_state.setText("不可用")
        self.lbl_host.setText("")

    def _home(self) -> None:
        if self._url:
            self.view.load(QUrl(self._url))

    def _reload_url(self) -> None:
        """重新走一遍取地址流程（服务重启后 token 会变）。"""
        self.lbl_state.setText("正在重新获取访问地址…")
        QApplication.processEvents()
        url, note = resolve_url(self._allow_start)
        self._note = note
        if url:
            self._load(url)
        else:
            self._show_unavailable()

    def _copy_url(self) -> None:
        if not self._url:
            self.lbl_state.setText("当前没有可用地址")
            return
        QApplication.clipboard().setText(self._url)   # 剪贴板里给完整地址（含 token），方便粘贴
        self.lbl_state.setText("访问地址已复制到剪贴板（含 token，注意别外传）")

    def _open_external(self) -> None:
        if not self._url:
            self.lbl_state.setText("当前没有可用地址")
            return
        import webbrowser

        webbrowser.open(self._url)

    def _zoom(self, delta: float) -> None:
        self.view.setZoomFactor(max(0.4, min(3.0, self.view.zoomFactor() + delta)))

    def _zoom_reset(self) -> None:
        self.view.setZoomFactor(1.0)

    def _toggle_fullscreen(self) -> None:
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def _devtools(self) -> None:
        # 复用同一个 devtools 页面，否则每按一次都会新开一个窗口
        if getattr(self, "_devtools_view", None) is None:
            self._devtools_view = QWebEngineView()
            self._devtools_view.setWindowTitle("开发者工具 — DSH")
            self._devtools_view.resize(1000, 640)
            self.page.setDevToolsPage(self._devtools_view.page())
        self._devtools_view.show()
        self._devtools_view.raise_()

    def _about(self) -> None:
        from . import __version__

        QMessageBox.information(
            self,
            "关于",
            f"<b>DSH 控制台 · GUI 版</b> v{__version__}<br><br>"
            "这个窗口里跑的就是 harness 自带的 <b>web UI</b>——界面与功能与浏览器里"
            "打开的是同一个东西，只是套了一层原生外壳（菜单 / 状态栏 / 缩放 / 全屏）。<br><br>"
            "所以它不需要「复刻」：harness 升级 web 界面，这里自动跟着变。<br><br>"
            "想用不依赖 web 服务的原生聊天窗口，请用 <code>--native</code>。",
        )

    # ------------------------------------------------------------ 事件
    def _on_load_started(self) -> None:
        self.lbl_state.setText("正在加载…")
        self.bar.setVisible(True)
        self.bar.setValue(0)

    def _on_progress(self, pct: int) -> None:
        self.bar.setValue(pct)

    def _on_load_finished(self, ok: bool) -> None:
        self.bar.setVisible(False)
        if ok:
            self.lbl_state.setText("就绪")
            return
        # 失败最常见的原因是 token 过期（服务重启过）或服务没在跑——重新取一次地址
        self.lbl_state.setText("加载失败，正在重新获取访问地址…")
        QTimer.singleShot(0, self._reload_url)

    def _on_auth_required(self, url: QUrl, auth) -> None:
        # 401 会走到这里（token 失效）。不要弹输入框——直接去取新地址。
        auth.cancel()
        self.lbl_state.setText("认证失败（token 可能已过期），正在重新获取…")
        QTimer.singleShot(0, self._reload_url)

    # ------------------------------------------------------------ 收尾
    def _restore_geometry(self) -> None:
        geo = self.session.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self.session.setValue("geometry", self.saveGeometry())
        # 只关窗口，绝不停服务：harness 是共享的后台，停它是控制台「停止」按钮的事
        super().closeEvent(event)


# --------------------------------------------------------------------- 入口
def _parse(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="dsh_console.gui",
        description="DSH 控制台 · GUI 版：把 harness 的 web UI 装进原生 Qt 窗口（界面与功能同 web 版）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "例：\n"
            "  python -m dsh_console.gui                 默认，加载 web UI\n"
            "  python -m dsh_console.gui --no-start      只连已在跑的服务，不自动启动\n"
            "  python -m dsh_console.gui --native        改用原生 ACP 客户端（不依赖 web 服务）\n"
        ),
    )
    p.add_argument("--url", metavar="URL", help="直接加载这个地址（跳过自动取地址）")
    p.add_argument("--no-start", action="store_true", help="web 服务没在跑时不要去启动它")
    p.add_argument("--native", action="store_true",
                   help="改走原生 ACP 客户端（dsh_console.gui_native），不使用 web UI")
    p.add_argument("--theme", metavar="KEY", help="原生外壳的配色（见 run.sh --list-themes）")
    # 下面几个只有 --native 用得上；保留是为了从控制台启动时命令行形状一致
    p.add_argument("--cwd", metavar="DIR", help="（仅 --native）会话工作目录")
    p.add_argument("--dsh", metavar="PATH", help="（仅 --native）dsh 可执行文件路径")
    p.add_argument("--profile", metavar="NAME", default="acp", help="（仅 --native）harness profile")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse(sys.argv[1:] if argv is None else argv)

    if args.native:
        from .gui_native import main as native_main

        forwarded: list[str] = []
        if args.cwd:
            forwarded += ["--cwd", args.cwd]
        if args.dsh:
            forwarded += ["--dsh", args.dsh]
        if args.profile:
            forwarded += ["--profile", args.profile]
        if args.theme:
            forwarded += ["--theme", args.theme]
        return native_main(forwarded)

    if WEBENGINE_IMPORT_ERROR is not None:
        sys.stderr.write(
            "GUI 版需要 QtWebEngine（PySide6-Addons）：\n"
            f"  {WEBENGINE_IMPORT_ERROR}\n"
            "装上："
            f"{sys.executable} -m pip install PySide6-Addons\n"
            "或者改用不依赖它的原生客户端：python -m dsh_console.gui --native\n"
        )
        return 2

    from PySide6.QtWidgets import QApplication as _QApp

    app = _QApp(sys.argv[:1])
    app.setApplicationName("DSH Web 界面")
    app.setApplicationDisplayName("DSH · Web 界面")
    app.setDesktopFileName("dsh-console")
    if args.theme:
        app.setStyleSheet(themes.build_qss(themes.get_theme(args.theme)))

    url, note = (args.url, "由 --url 指定") if args.url else resolve_url(not args.no_start)
    win = WebShell(url, note, allow_start=not args.no_start)
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
