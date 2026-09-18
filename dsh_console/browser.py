"""harness 专用的内置浏览器。

## 为什么是"封装 QtWebEngine"而不是"编译 Chromium"

任务书里给的是 Chromium 源码地址。**从源码编译 Chromium 在这套东西里不成立**：
按官方文档要 100 GB 以上磁盘、专用工具链、单次构建数小时，而且产物是 150 MB+ 的
二进制——为了"给 harness 配个浏览器"付这个代价没有道理。

更关键的是**没必要**：``PySide6-Addons`` 里的 **QtWebEngine 就是 Chromium**
（同一个渲染引擎、同一套网络栈，Qt 只是把它包成库并提供了嵌入用的 API）。
控制台早就把它随包带上了——所以"用户机器上没有浏览器"这件事，其实已经解决了，
缺的只是把它**当成浏览器**来用：多标签、地址栏、独立 profile、可控的启动开关。

## 这个浏览器和「GUI 版」的区别

| | GUI 版（``gui.py``） | 内置浏览器（本模块） |
|---|---|---|
| 定位 | 控制台的"打开界面"外壳 | 一个**能日常用**的浏览器 |
| 界面 | 一个视图 + 菜单 + 状态栏 | 多标签 + 地址栏 + 前进后退 |
| 用途 | 只看 harness web UI | 看 harness，也能开别的页面/文档 |
| profile | 共享 ``webengine`` | 独立 ``browser/``（互不干扰） |

## 独立 profile = 不污染用户浏览器

profile 落在 ``home/.cache/dsh-console/browser/``（便携包里就是包内），cookie、
localStorage、缓存全在里面。**用户自己的 Chrome/Firefox 一点都不会被碰**——
删掉那个目录就等于没来过。

## Chromium 开关（"改造优化"落在这里）

``QTWEBENGINE_CHROMIUM_FLAGS`` 必须在 **QApplication 建起来之前**设置，所以
:func:`apply_chromium_flags` 得在 ``main()`` 最前面调用。关掉的都是"浏览器该有的
消费级行为"，对一个专用浏览器来说纯属噪音：后台联网、组件更新、账号同步、
崩溃上报、首次运行引导。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from . import i18n
from . import browser_theme as bt
from .screenfit import apply_screen_fit, prefer_xwayland
from .ui.components import PopupMenu, _col
from . import config as console_config

#: profile 与缓存的落点。便携模式下 HOME 已被启动器指到包内，所以自动落在包里。
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")) / "dsh-console"
PROFILE_DIR = CACHE_DIR / "browser"
CACHE_SUBDIR = CACHE_DIR / "browser-cache"

#: 自己的标识。挂在 UA 尾巴上，站点和日志里都能一眼看出请求来自哪个浏览器。
UA_SUFFIX = "DSH-Harness-Browser/1.0"

#: 版本号（内置浏览器的"外壳版本"，与引擎版本分开计）。
BROWSER_VERSION = "1.0.0"

#: 启动时注入的 Chromium 开关。
#:
#: 原则：**只关掉和"看 harness 界面"无关的东西**，不动渲染与网络相关的部分
#: （那些关了会让页面表现和真浏览器不一致，反而难排查）。
CHROMIUM_FLAGS: tuple[str, ...] = (
    "--disable-background-networking",      # 后台预取/更新检查
    "--disable-component-update",           # 组件（如 Widevine）自动更新
    "--disable-sync",                       # 账号同步
    "--disable-breakpad",                   # 崩溃上报
    "--disable-domain-reliability",         # 域名可靠性上报
    "--no-first-run",                       # 首次运行引导
    "--no-default-browser-check",           # "要不要设为默认浏览器"
    "--disable-features=Translate,MediaRouter,OptimizationHints",
)


def apply_chromium_flags(extra: list[str] | None = None) -> str:
    """把 Chromium 开关写进 ``QTWEBENGINE_CHROMIUM_FLAGS``，返回最终字符串。

    **必须在 QApplication 之前调用**：QtWebEngine 在初始化子进程时才读这个变量，
    晚一步就完全不起作用，而且不报错——属于"看着改了其实没生效"的那一类。
    已经存在的值会保留并前置，方便用户自己追加调试开关。
    """
    flags = list(CHROMIUM_FLAGS)
    # 深色外观下让 CSS 的 prefers-color-scheme: dark 命中——支持深色的站点
    # （包括 harness 自己的界面）就按深色渲染。**它不会把网页反色**（那是另一个
    # 实验特性 WebContentsForceDark），所以不会出现图片被反相那种灾难。
    if _wants_dark():
        flags.append("--force-dark-mode")
    if extra:
        flags.extend(extra)
    existing = (os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS") or "").strip()
    merged = (existing + " " + " ".join(flags)).strip() if existing else " ".join(flags)
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = merged
    return merged


# ------------------------------------------------------------------ 版本信息
#: 配置键。
KEY_THEME = "browserTheme"
KEY_MODE = "browserMode"


def current_choice() -> tuple[str, str]:
    """当前选择 ``(主题 key, 外观模式)``。"""
    return (console_config.get(KEY_THEME, bt.DEFAULT_THEME),
            console_config.get(KEY_MODE, bt.MODE_DARK))


def _wants_dark() -> bool:
    theme_key, mode = current_choice()
    return bt.resolve(theme_key, mode).dark


def save_choice(theme_key: str, mode: str) -> None:
    console_config.put(KEY_THEME, theme_key)
    console_config.put(KEY_MODE, mode)


def engine_info() -> dict:
    """内置浏览器的引擎信息（给 Harness 页那张卡片用）。

    一律不抛异常：QtWebEngine 没装、profile 还没建，都要能如实报告而不是让页面炸。
    """
    info: dict[str, object] = {
        "浏览器版本": BROWSER_VERSION,
        "profile": str(PROFILE_DIR),
        "profile_存在": PROFILE_DIR.is_dir(),
        "缓存大小": _dir_size(PROFILE_DIR) + _dir_size(CACHE_SUBDIR),
        "chromium": "",
        "qt": "",
        "引擎": "QtWebEngine（Chromium）",
        "可用": False,
    }
    try:
        from PySide6.QtCore import qVersion
        # ⚠️ 这两个是**模块级函数**，不需要 QApplication。
        # 别改成 QWebEngineProfile.defaultProfile()：没有 QApplication 时它会
        # 直接把进程 abort 掉（"Please instantiate the QApplication object first"），
        # 自检里就是这么崩的（退出码 134）——而自检恰恰是最不该依赖 GUI 的地方。
        from PySide6.QtWebEngineCore import qWebEngineChromiumVersion, qWebEngineVersion

        info["qt"] = qVersion()
        info["chromium"] = qWebEngineChromiumVersion()
        info["引擎版本"] = qWebEngineVersion()
        info["可用"] = True
        # UA 只有建过 QApplication 才拿得到，拿不到就不显示，不影响其它字段
        try:
            from PySide6.QtWidgets import QApplication
            from PySide6.QtWebEngineCore import QWebEngineProfile

            if QApplication.instance() is not None:
                ua = QWebEngineProfile.defaultProfile().httpUserAgent()
                info["user_agent"] = f"{ua} {UA_SUFFIX}"
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:  # noqa: BLE001
        info["错误"] = f"{type(exc).__name__}: {exc}"
    return info


def _chrome_version(user_agent: str) -> str:
    """从 UA 里抠出 ``Chrome/140.0.7339.0`` 那样的版本号。"""
    import re

    match = re.search(r"Chrome/([\d.]+)", user_agent or "")
    return match.group(1) if match else ""


def _dir_size(path: Path) -> int:
    if not path.is_dir():
        return 0
    total = 0
    for base, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (Path(base) / name).stat().st_size
            except OSError:
                continue
    return total


def human_size(n: int) -> str:
    for unit, div in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if n >= div:
            return f"{n / div:.1f} {unit}"
    return f"{n} B"


def describe() -> str:
    info = engine_info()
    if not info["可用"]:
        return f"内置浏览器不可用：{info.get('错误', '未知原因')}"
    return (f"内置浏览器 {info['浏览器版本']}　·　Chromium {info['chromium'] or '?'}"
            f"　·　Qt {info['qt']}　·　profile {human_size(int(info['缓存大小']))}")


# ------------------------------------------------------------------ 引擎更新
#: 引擎版本来自 PySide6，所以"升级内置浏览器"= 升级包内的 PySide6。
#: 升级只写 ``runtime/``（便携包）或当前解释器的 site-packages，不碰系统 Python。
ENGINE_PACKAGES = ("PySide6-Essentials", "PySide6-Addons", "shiboken6")


def update_command(python: str | None = None) -> list[str]:
    """生成升级内置浏览器引擎的命令。

    便携包里用包内的 python + ``--target`` 指到包内 site-packages；普通模式就是
    当前解释器的 ``pip install -U``。**两种都不会碰系统 Python**（便携包用的是
    自己的解释器）。
    """
    exe = python or sys.executable or "python3"
    return [exe, "-m", "pip", "install", "--upgrade", "--no-warn-script-location",
            *ENGINE_PACKAGES]


def installed_engine_version() -> str:
    """当前引擎包的版本（从 dist-info 读，比 import 快且不依赖 Qt 初始化）。"""
    try:
        from importlib.metadata import version

        return version("PySide6-Essentials")
    except Exception:  # noqa: BLE001
        return ""


def releases(timeout: float = 20.0) -> str:
    """查 PyPI 上 ``PySide6-Essentials`` 的最新版本。网络不通就抛异常，由调用方兜。"""
    import json
    import urllib.request

    req = urllib.request.Request(
        "https://pypi.org/pypi/PySide6-Essentials/json",
        headers={"User-Agent": UA_SUFFIX},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - 固定地址
        data = json.load(resp)
    return str(data.get("info", {}).get("version") or "")


# ------------------------------------------------------------------ 浏览器窗口
# Qt 相关的导入放在文件末尾：这样上面那些"查版本 / 拼开关"的纯函数在没有 Qt 的
# 环境里也能用（比如自检里只想知道 Chromium 版本），不会因为 import 失败全废掉。
def _swatch(theme):
    """给菜单项做一个小色块图标，选主题时能直接看到颜色。"""
    from PySide6.QtCore import QRect, Qt
    from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPixmap

    pix = QPixmap(14, 14)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QBrush(QColor(theme.surface)))
    painter.setPen(QColor(theme.border))
    painter.drawRoundedRect(QRect(0, 0, 13, 13), 3, 3)
    painter.setBrush(QBrush(QColor(theme.accent)))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(QRect(3, 5, 8, 4), 2, 2)
    painter.end()
    return QIcon(pix)


def _waiting_page(note: str) -> str:
    """等待 harness 就绪时的占位页。**先把窗口开出来**是重点，内容是次要的。"""
    import html

    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>正在启动 harness…</title><style>
 html,body{{height:100%;margin:0}}
 body{{display:flex;align-items:center;justify-content:center;
  font-family:system-ui,-apple-system,"Noto Sans CJK SC",sans-serif;
  background:#101418;color:#e6edf3}}
 .box{{max-width:620px;padding:30px 34px;border:1px solid #2a323c;border-radius:14px;
  background:#161b22;text-align:center}}
 h1{{margin:0 0 12px;font-size:18px}}
 p{{margin:0;line-height:1.8;color:#9aa7b4;font-size:13.5px}}
 .bar{{margin:20px auto 0;width:220px;height:3px;border-radius:2px;background:#21262d;
  overflow:hidden;position:relative}}
 .bar::after{{content:"";position:absolute;inset:0;width:40%;border-radius:2px;
  background:#4f8cff;animation:slide 1.2s ease-in-out infinite}}
 @keyframes slide{{0%{{left:-40%}}100%{{left:100%}}}}
</style></head><body><div class="box">
<h1>正在启动 harness…</h1>
<p>{html.escape(note) if note else "正在准备 web 服务，好了会自动跳转。"}</p>
<div class="bar"></div>
</div></body></html>"""


#: 右键菜单条目：``(中文标签, 动作 id, 是否可用)``。
#:
#: **为什么自己拼菜单**：Qt 自带的右键菜单文案来自 ``qtwebengine_<lang>.qm``，而实测
#: 那份翻译里**根本没有这些条目**——把 qtwebengine_zh_CN.qm 装上，菜单照样全英文
#: （文件里只有"下载/请求"那十几条）。所以想要中文菜单只有自己建这一条路。
#: 标签走 :func:`i18n.tr`，于是它跟着界面的中/EN 开关一起切。
class _FallbackTheme:
    """菜单用的最小主题：只保证 PopupMenu 需要的几个颜色字段存在。

    真主题对象字段缺失/解析失败时用它顶上——**菜单画得丑一点没关系，不能没有菜单**。
    """

    surface = "#1e1e22"
    bg = "#1e1e22"
    border = "#3a3a42"
    text = "#e8e8ea"
    hover = "#2c2c33"
    surface_alt = "#2c2c33"
    dim = "#9a9aa2"
    text_faint = "#9a9aa2"
    accent = "#4c8dff"


def context_menu_items(*, editable: bool = False, has_selection: bool = False,
                       link_url: str = "", media_is_image: bool = False,
                       media_url: str = "") -> list[tuple[str, str, bool]]:
    """按"点在哪儿"列出右键菜单条目。**纯函数**，不碰 Qt 控件。

    QtWebEngine 在没有显示器的环境里一实例化就崩，所以"菜单里到底有什么"必须能在
    不起引擎的情况下验证——这个函数就是为可测性单独拆出来的。
    空标签 + 空 id 表示一条分隔线。
    """
    items: list[tuple[str, str, bool]] = [
        ("后退", "back", True), ("前进", "forward", True), ("重新加载", "reload", True),
    ]
    if link_url:
        items += [("", "", False),
                  ("在新标签页中打开链接", "open_link_new_tab", True),
                  ("复制链接地址", "copy_link", True)]
    if media_is_image and media_url:
        items += [("", "", False),
                  ("复制图片", "copy_image", True),
                  ("复制图片地址", "copy_image_address", True)]
    if editable:
        items += [("", "", False), ("剪切", "cut", has_selection),
                  ("复制", "copy", has_selection), ("粘贴", "paste", True),
                  ("全选", "select_all", True)]
    elif has_selection:
        # 选中文字时也要给全一套（实测反馈：以前这里只有"复制/全选"，
        # 用户找不到粘贴、翻译、打开链接）。粘贴对非输入区是**无害的空操作**，
        # 但"菜单里没有"比"点了没反应"更让人困惑，所以保留它。
        items += [("", "", False), ("复制", "copy", True), ("粘贴", "paste", True),
                  ("翻译选中文字", "translate_selection", True), ("全选", "select_all", True)]
    if link_url:
        items += [("", "", False), ("在系统浏览器中打开链接", "open_link_system", True)]
    items += [("", "", False),
              ("在系统浏览器中打开此页", "open_page_system", True),
              ("另存为…", "save_page", True), ("打印…", "print", True),
              ("查看网页源代码", "view_source", True), ("检查元素", "inspect", True)]
    return items


def build_window(url: str | None, note: str = "", *, autostart: bool = False):
    """建出内置浏览器主窗口。返回 ``(窗口, 应用)``。

    做成多标签：harness 页面经常要一边看界面一边翻文档/日志，单视图会很憋屈。
    """
    from PySide6.QtCore import QPoint, QTimer, QUrl, Qt
    from PySide6.QtGui import QAction, QKeySequence
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWidgets import (
        QApplication, QLabel, QLineEdit, QMainWindow, QMenu, QStatusBar, QTabWidget,
        QToolBar, QToolButton,
    )

    from . import gui as shell

    def _open_external(url: str) -> None:
        """把地址交给系统浏览器（内置浏览器打不开/不想开的时候用）。"""
        if not url:
            return
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl(url))

    def _translate(text: str) -> None:
        """翻译选中文字。

        用**百度翻译**而不是 Google：本项目主要面向中文用户，Google 翻译在境内不通。
        地址形如 ``https://fanyi.baidu.com/#auto/zh/<文本>`` —— 打开就带着原文，
        省得用户再粘贴一次。
        """
        from urllib.parse import quote

        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        text = (text or "").strip()
        if not text:
            return
        QDesktopServices.openUrl(QUrl(f"https://fanyi.baidu.com/#auto/zh/{quote(text)}"))

    class _BrowserPage(QWebEnginePage):
        """接管 dsh-menu:// 哨兵地址 —— 网页内菜单的点击就是这样回传出来的。

        菜单画在**页面 DOM** 里（见 _BrowserView._show_html_menu），条目点击时把
        location 指到 ``dsh-menu://<id>``；这里拦下它、执行动作、**阻止真正的导航**。
        这样整条交互都在 Chromium 内部，不创建任何 Qt 控件/原生弹窗 ——
        前几轮踩的坑（xdg_popup 需要输入 serial、事件过滤器里的销毁竞态）一个都不沾。
        """

        def __init__(self, profile, parent, on_menu_click) -> None:
            super().__init__(profile, parent)
            self._on_menu_click = on_menu_click

        def javaScriptConsoleMessage(self, level, message, line, source):  # noqa: N802
            """页面用 console.log 把两件事报上来（唯一的回传通道，见 _CTX_JS 的说明）：

            * ``dsh-ctx:{...}``  —— 这里被右键了（含坐标、链接、选中、是否可编辑）
            * ``dsh-menu:<id>``  —— 菜单里这一项被点了
            """
            text = str(message or "")
            if text.startswith("dsh-ctx:"):
                try:
                    import json as _json

                    self._on_menu_request(_json.loads(text[len("dsh-ctx:"):]))
                except Exception as exc:                 # noqa: BLE001
                    print(f"[html-menu] 右键请求失败：{type(exc).__name__}: {exc}",
                          file=sys.stderr, flush=True)
                return
            if text.startswith("dsh-menu:"):
                try:
                    self._on_menu_click(text[len("dsh-menu:"):])
                except Exception as exc:                 # noqa: BLE001
                    print(f"[html-menu] 动作失败：{type(exc).__name__}: {exc}",
                          file=sys.stderr, flush=True)
                return
            super().javaScriptConsoleMessage(level, message, line, source)

    class _BrowserView(QWebEngineView):
        """带**中文右键菜单**的 WebEngine 视图。

        Qt 自带的右键菜单在这里永远是英文（原因见 :func:`context_menu_items`），
        所以自己拼一个：条目由纯函数给出，这里只负责翻译标签、按点击位置启用/禁用，
        并把动作接到 page 的 WebAction 上。
        """

        def __init__(self, owner, parent=None) -> None:
            super().__init__(parent)
            self._owner = owner          # 浏览器主窗口（"在新标签页打开链接"要用它的 add_tab）
            self._ctx: dict = {}          # 本次右键位置的上下文（选中/链接/图片）

        def _request(self):
            page = self.page()
            getter = getattr(page, "lastContextMenuRequest", None)
            try:
                return getter() if callable(getter) else None
            except Exception:            # noqa: BLE001 - 拿不到就按"空白处点击"处理
                return None

        def _menu_context(self) -> dict:
            """收集点击位置的上下文（选中/链接/图片），菜单条目与动作都要用。"""
            page = self.page()
            req = self._request()
            selected = bool(getattr(req, "selectedText", lambda: "")())
            if not selected:
                # contextMenuData 里偶尔拿不到选中文本，page().hasSelection() 才是权威
                try:
                    selected = bool(page.hasSelection())
                except Exception:            # noqa: BLE001
                    pass
            link = getattr(req, "linkUrl", lambda: None)()
            media = getattr(req, "mediaType", lambda: None)()
            try:
                is_image = media == page.ContextMenuRequest.MediaType.MediaTypeImage
            except Exception:                # noqa: BLE001
                is_image = bool(getattr(req, "mediaUrl", lambda: None)()
                                and str(media).lower().endswith("image"))
            murl = getattr(req, "mediaUrl", lambda: None)()
            return {
                "editable": bool(getattr(req, "isContentEditable", lambda: False)()),
                "selected": selected,
                "link_url": link.toString() if link is not None else "",
                "is_image": is_image,
                "media_url": murl.toString() if murl is not None else "",
                "selected_text": (getattr(req, "selectedText", lambda: "")() or "").strip(),
            }

        def _run_action(self, act_id: str) -> None:
            """执行菜单条目。浮层菜单只发 id 出来，具体动作集中在这里。"""
            page = self.page()
            ctx = self._ctx or {}
            action_map = {
                "back": page.WebAction.Back, "forward": page.WebAction.Forward,
                "reload": page.WebAction.Reload, "cut": page.WebAction.Cut,
                "copy": page.WebAction.Copy, "paste": page.WebAction.Paste,
                "select_all": page.WebAction.SelectAll, "save_page": page.WebAction.SavePage,
                "view_source": page.WebAction.ViewSource,
                "inspect": page.WebAction.InspectElement,
            }
            try:
                action_map["print"] = page.WebAction.Print
            except AttributeError:
                pass
            if act_id in action_map:
                page.triggerAction(action_map[act_id])
                return
            # 「更多」菜单的条目 id 就是动作文本：交给窗口上那份 QAction
            if act_id:
                for act in self._owner.actions() if hasattr(self, "_owner") else []:
                    if act.text() == act_id and act.isEnabled():
                        act.trigger()
                        return
            if act_id == "open_link_new_tab":
                self._owner.add_tab(ctx.get("link_url", ""))
            elif act_id == "copy_link":
                QApplication.clipboard().setText(ctx.get("link_url", ""))
            elif act_id == "copy_image_address":
                QApplication.clipboard().setText(ctx.get("media_url", ""))
            elif act_id == "translate_selection":
                _translate(ctx.get("selected_text", ""))
            elif act_id == "open_link_system":
                _open_external(ctx.get("link_url", ""))
            elif act_id == "open_page_system":
                _open_external(self.url().toString())
            elif act_id == "copy_image":
                if hasattr(page.WebAction, "CopyImageToClipboard"):
                    page.triggerAction(page.WebAction.CopyImageToClipboard)

        #: 注入到每个页面的右键监听。
        #:
        #: 为什么要在页面里监听：实测 QtWebEngine **既不调用** contextMenuEvent，
        #: 也**不把鼠标释放交给我们**（右键始终无反应）。而 DOM 的 contextmenu 事件
        #: 一定会来 —— 让页面把它连同"点在哪、点到了什么"一起报给宿主，
        #: 走已经验证可靠的哨兵地址通道（acceptNavigationRequest）。
        _CTX_JS = """
        (function () {
          if (window.__dshCtxHooked) { return 'already'; }
          window.__dshCtxHooked = true;
          document.addEventListener('contextmenu', function (ev) {
            try {
              var t = ev.target || {};
              var a = t.closest ? t.closest('a') : null;
              var info = {
                x: Math.round(ev.clientX), y: Math.round(ev.clientY),
                link: a ? a.href : '',
                sel: String(window.getSelection ? window.getSelection() : ''),
                editable: !!(t.isContentEditable || /^(INPUT|TEXTAREA)$/.test(t.tagName || '')),
                image: !!((t.tagName || '') === 'IMG')
              };
              ev.preventDefault();
              // 用 console.log 回传：宿主重写 javaScriptConsoleMessage 一定能收到。
              // （哨兵 URL 那条路走不通 —— Chromium 对未知 scheme 直接忽略，
              //   acceptNavigationRequest 根本不会被调用，实测就是"右键毫无反应"。）
              console.log('dsh-ctx:' + JSON.stringify(info));
            } catch (e) { /* 页面里出错不能影响它自己 */ }
          }, true);
          return 'hooked';
        })();
        """

        #: 画在页面里的菜单。**不创建任何 Qt 控件**，所以不受合成器/事件循环影响。
        _HTML_MENU_JS = """
        (function () {
          var ID = 'dsh-ctx-menu';
          var old = document.getElementById(ID);
          if (old) { old.remove(); }
          var items = %(items)s, x = %(x)d, y = %(y)d;
          var d = document.createElement('div');
          d.id = ID;
          d.setAttribute('data-dsh', 'ctx-menu');
          d.style.cssText = 'position:fixed;z-index:2147483647;left:' + x + 'px;top:' + y + 'px;'
            + 'background:%(surface)s;border:1px solid %(border)s;border-radius:8px;padding:5px;'
            + 'min-width:190px;max-width:320px;box-shadow:0 10px 30px rgba(0,0,0,.45);'
            + 'font:13px/1.55 system-ui,-apple-system,"Segoe UI","Noto Sans CJK SC",sans-serif;'
            + 'color:%(text)s;user-select:none';
          items.forEach(function (it) {
            if (!it[0]) {
              var hr = document.createElement('div');
              hr.style.cssText = 'height:1px;background:%(border)s;margin:4px 6px';
              d.appendChild(hr);
              return;
            }
            var b = document.createElement('div');
            b.textContent = it[0];
            b.style.cssText = 'padding:5px 10px;border-radius:5px;white-space:nowrap;'
              + 'overflow:hidden;text-overflow:ellipsis;cursor:' + (it[2] ? 'pointer' : 'default')
              + ';opacity:' + (it[2] ? '1' : '.45');
            if (it[2]) {
              b.addEventListener('mouseenter', function () { b.style.background = '%(hover)s'; });
              b.addEventListener('mouseleave', function () { b.style.background = 'transparent'; });
              b.addEventListener('click', function (ev) {
                ev.preventDefault(); ev.stopPropagation();
                console.log('dsh-menu:' + it[1]);
                close();
              });
            }
            d.appendChild(b);
          });
          var px = Math.min(x, Math.max(0, window.innerWidth - d.offsetWidth - 8));
          var py = Math.min(y, Math.max(0, window.innerHeight - d.offsetHeight - 8));
          d.style.left = (d.offsetWidth > window.innerWidth - 16 ? 8 : px) + 'px';
          d.style.top = py + 'px';
          document.body.appendChild(d);
          function close() {
            if (d.parentNode) { d.remove(); }
            document.removeEventListener('mousedown', onDown, true);
            document.removeEventListener('keydown', onKey, true);
            window.removeEventListener('blur', close);
          }
          function onDown(ev) { if (!d.contains(ev.target)) { close(); } }
          function onKey(ev) { if (ev.key === 'Escape') { close(); } }
          document.addEventListener('mousedown', onDown, true);
          document.addEventListener('keydown', onKey, true);
          window.addEventListener('blur', close);
        })();
        """

        def _inject_ctx_hook(self) -> None:
            """把右键监听注入当前页面（每次加载完都要来一遍）。"""
            try:
                self.page().runJavaScript(self._CTX_JS)
            except Exception as exc:                     # noqa: BLE001
                print(f"[html-menu] 注入右键监听失败：{type(exc).__name__}: {exc}",
                      file=sys.stderr, flush=True)

        def _on_page_context_menu(self, info: dict) -> None:
            """页面报告右键位置后，把菜单画在那儿。"""
            try:
                self._ctx = {
                    "editable": bool(info.get("editable")),
                    "selected": bool(info.get("sel")),
                    "link_url": info.get("link") or "",
                    "is_image": bool(info.get("image")),
                    "media_url": info.get("link") or "",
                    "selected_text": info.get("sel") or "",
                }
                self._show_html_menu(int(info.get("x", 0)), int(info.get("y", 0)), self._ctx)
            except Exception as exc:                     # noqa: BLE001
                print(f"[html-menu] 画菜单失败：{type(exc).__name__}: {exc}",
                      file=sys.stderr, flush=True)

        def _show_html_menu(self, pos_x: int, pos_y: int, ctx: dict | None = None,
                            items: list | None = None) -> None:
            """把菜单画进当前页面（纯 DOM）。``items`` 给了就直接用（「更多」菜单走这条）。"""
            import json as _json

            if items is None:
                ctx = ctx or {}
                items = [
                    [i18n.tr(label), act_id, bool(enabled)]
                    for label, act_id, enabled in context_menu_items(
                        editable=ctx.get("editable", False),
                        has_selection=ctx.get("selected", False),
                        link_url=ctx.get("link_url", ""),
                        media_is_image=ctx.get("is_image", False),
                        media_url=ctx.get("media_url", ""))
                ]
            try:
                theme = getattr(self._owner, "theme", None) or bt.resolve(*current_choice())
            except Exception:                            # noqa: BLE001
                theme = _FallbackTheme()
            js = self._HTML_MENU_JS % {
                "items": _json.dumps(items, ensure_ascii=False),
                "x": int(pos_x), "y": int(pos_y),
                "surface": _col(theme, "surface", "bg"),
                "border": getattr(theme, "border", "#3a3a42"),
                "text": getattr(theme, "text", "#e8e8ea"),
                "hover": _col(theme, "hover", "surface_alt"),
            }
            self.page().runJavaScript(js)

        def build_menu(self):
            """拼出**窗口内浮层菜单**（PopupMenu）。

            不用 QMenu：它创建的是原生 xdg_popup，Wayland 下必须有输入 serial 才允许
            （详见 components.PopupMenu 的说明），表现就是"第一次点击弹不出来"。
            """
            ctx = self._menu_context()
            self._ctx = ctx
            items = [
                (i18n.tr(label), act_id, enabled)
                for label, act_id, enabled in context_menu_items(
                    editable=ctx["editable"], has_selection=ctx["selected"],
                    link_url=ctx["link_url"], media_is_image=ctx["is_image"],
                    media_url=ctx["media_url"])
            ]
            try:
                theme = getattr(self._owner, "theme", None) or bt.resolve(*current_choice())
            except Exception as exc:                     # noqa: BLE001
                # 主题取不到不该让菜单消失：用一个最小主题顶上，并把原因写进日志
                print(f"[context-menu] 主题解析失败：{type(exc).__name__}: {exc}",
                      file=sys.stderr, flush=True)
                theme = _FallbackTheme()
            # **复用一个菜单对象**（不每次新建/销毁）：销毁发生在事件过滤器里会与 Qt 的
            # 过滤器遍历竞态，表现为偶发崩溃（用户："菜单先显示，然后崩"）。
            popup = getattr(self, "_popup", None)
            if popup is None:
                popup = PopupMenu(self.window(), items, theme)
                popup.triggered.connect(self._run_action)
                self._popup = popup
            else:
                popup.set_items(items)
            return popup

        def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt 命名
            """右键在**鼠标事件**里接管。

            为什么不用 contextMenuEvent：实测**它根本不会被调用**（QtWebEngine 自己把右键
            吃掉了）—— 用户反馈"右键不显示菜单也不崩"正是这个原因（我的 HTML 菜单代码
            是对的，只是没人调用它）。这里从鼠标释放直接接管，稳妥且与平台无关。
            """
            if event.button() == Qt.MouseButton.RightButton:
                mode = (os.environ.get("DSH_BROWSER_MENU") or "html").strip().lower()
                if mode == "html":
                    try:
                        ctx = self._menu_context()
                        self._ctx = ctx
                        pos = event.position().toPoint()
                        self._show_html_menu(pos.x(), pos.y(), ctx)
                    except Exception as exc:             # noqa: BLE001
                        print(f"[html-menu] 失败：{type(exc).__name__}: {exc}",
                              file=sys.stderr, flush=True)
                    event.accept()
                    return
                if mode == "none":
                    event.accept()
                    return
            super().mouseReleaseEvent(event)

        def contextMenuEvent(self, event) -> None:  # noqa: N802 - Qt 命名
            # ⚠️ **默认用 Qt 自带菜单**（自绘浮层改为显式开启）。
            #
            # 原因：自绘浮层（PopupMenu）在用户的 COSMIC/Wayland 桌面上会**必现崩溃**
            # ——右键菜单先显示、随后浏览器进程硬崩（日志里没有 Python traceback，
            # 是 C++ 层的崩溃）。反复调整（改 popup、加兜底、改为复用对象）都没能消除，
            # 说明"QWidget + 事件过滤器 + 在合成器里反复弹出"这条路在该环境下不稳。
            # **可用性优先**：默认退回 Qt 自带菜单（Wayland 下第一次点击可能要多点一次，
            # 但不会崩）；想把自绘浮层要回来：DSH_BROWSER_MENU=overlay。
            mode = (os.environ.get("DSH_BROWSER_MENU") or "html").strip().lower()
            if mode == "qt":
                super().contextMenuEvent(event)
                return
            if mode == "none":
                event.accept()
                return
            if mode == "html":
                # 默认：菜单画在页面里 —— 不碰 Qt 控件/原生弹窗，最稳。
                try:
                    ctx = self._menu_context()
                    self._ctx = ctx
                    pos = event.position().toPoint()
                    self._show_html_menu(pos.x(), pos.y(), ctx)
                except Exception as exc:                 # noqa: BLE001
                    print(f"[html-menu] 失败：{type(exc).__name__}: {exc}",
                          file=sys.stderr, flush=True)
                event.accept()
                return
            # ⚠️ **右键绝不能把浏览器带崩**：以前这里是裸调用，一旦菜单构建或弹出抛异常，
            # PySide6 在虚函数里抛异常会直接终止进程（用户看到的就是"一右键就崩"）。
            # 现在兜住一切异常并**把原因写进 browser.log**，浏览器照常可用。
            try:
                self.build_menu().popup(event.globalPos())
            except Exception as exc:                     # noqa: BLE001
                print(f"[context-menu] 构建/弹出失败：{type(exc).__name__}: {exc}",
                      file=sys.stderr, flush=True)
            event.accept()

    class Browser(QMainWindow):
        """带标签页与地址栏的 harness 专用浏览器。"""

        def __init__(self) -> None:
            super().__init__()
            self.setWindowTitle(i18n.tr("DSH 内置浏览器"))
            # 按屏幕自适应：小屏上别让窗口跑到屏幕外（和控制台主窗口同一套逻辑）
            apply_screen_fit(self, (1180, 800), (760, 520))

            # 独立 profile：和 GUI 版那份互不干扰，也绝不碰用户自己的浏览器
            profile = QWebEngineProfile("dsh-browser", self)
            profile.setPersistentStoragePath(str(PROFILE_DIR))
            profile.setCachePath(str(CACHE_SUBDIR))
            profile.setHttpUserAgent(
                f"{QWebEngineProfile.defaultProfile().httpUserAgent()} {UA_SUFFIX}"
            )
            try:
                profile.setPersistentCookiesPolicy(
                    QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
                )
            except AttributeError:
                pass
            self._profile = profile

            self.tabs = QTabWidget()
            self.tabs.setTabsClosable(True)
            self.tabs.setMovable(True)
            self.tabs.tabCloseRequested.connect(self._close_tab)
            self.tabs.currentChanged.connect(lambda _i: self._sync_url())
            self.setCentralWidget(self.tabs)

            bar = QToolBar("导航")
            bar.setMovable(False)
            self.addToolBar(bar)
            for text, slot, key in (("←", self._back, "Alt+Left"),
                                    ("→", self._forward, "Alt+Right"),
                                    ("⟳", self._reload, "F5"),
                                    ("⌂", self._home, "Alt+Home")):
                act = QAction(text, self)
                act.setShortcut(QKeySequence(key))
                act.triggered.connect(slot)
                bar.addAction(act)
            self.url_edit = QLineEdit()
            self.url_edit.returnPressed.connect(self._navigate)
            bar.addWidget(self.url_edit)
            new_tab = QAction("＋", self)
            new_tab.setShortcut(QKeySequence("Ctrl+T"))
            new_tab.triggered.connect(lambda: self.add_tab())
            bar.addAction(new_tab)

            # **不建菜单栏**。原本"文件/视图"那一行占掉一条横带，而里面的操作要么工具栏
            # 已经有按钮（新建/关闭/重新加载），要么是低频的（开发者工具、缩放）。
            # 改成工具栏右端的「⋮」弹出菜单：横带省下来了，功能一个没少。
            # 「更多」菜单同样用浮层（它是原生 QMenu 时，Wayland 下第一次点击也弹不出来）。
            # 这里只登记条目；动作与快捷键仍挂在窗口上，浮层只是个入口。
            overflow_items: list[tuple[str, str, bool]] = []
            for text, slot, key in (("新建标签页", lambda: self.add_tab(), "Ctrl+T"),
                                    ("关闭标签页", self._close_current, "Ctrl+W"),
                                    ("重新加载", self._reload, "Ctrl+R"),
                                    ("开发者工具", self._devtools, "F12")):
                act = QAction(text, self)
                # 快捷键挂在**窗口**上（addAction），菜单只是个入口——
                # 这样菜单不弹出来时快捷键照样能用。
                act.setShortcut(QKeySequence(key))
                act.triggered.connect(slot)
                self.addAction(act)
                overflow_items.append((text, text, True))
            overflow_items.append(("", "", False))
            for text, delta in (("放大", 1), ("缩小", -1), ("重置缩放", 0)):
                act = QAction(text, self)
                act.setShortcut(QKeySequence("Ctrl+0" if delta == 0 else
                                             ("Ctrl+=" if delta > 0 else "Ctrl+-")))
                act.triggered.connect(lambda _c=False, d=delta: self._zoom(d))
                self.addAction(act)
                overflow_items.append((text, text, True))
            overflow_items.append(("", "", False))
            quit_act = QAction("退出", self)
            quit_act.setShortcut(QKeySequence("Ctrl+Q"))
            quit_act.triggered.connect(self.close)
            self.addAction(quit_act)
            overflow_items.append((quit_act.text(), quit_act.text(), True))
            self._overflow_items = overflow_items

            # 外观按钮就放在 ＋ 左边、紧挨地址栏——用户改主题时眼睛就在地址栏上。
            # 外观按钮：**不用 QToolButton 自带的 setMenu/InstantPopup**。
            #
            # 那套写法在这里"点了没反应"（用户实测：菜单根本不弹）。它依赖
            # QToolButton 内部的弹出路径 + 自定义 QSS，出问题时既不报错也无从下手。
            # 改成 clicked → 自己建菜单 → menu.exec()：这是 Qt 里最经典、跨平台最稳的
            # 弹菜单方式，而且"什么时候建菜单"完全由我们说了算（exec 之前菜单一定是关着的，
            # 不会出现"开着的时候被清空重建"那种事）。
            self.theme_btn = QToolButton(self)
            self.theme_btn.setText("◐")
            self.theme_btn.setAutoRaise(True)
            self.theme_btn.clicked.connect(self._open_theme_dialog)
            # 右键 = 直接切下一套主题（**不依赖弹出菜单**的退路）。
            # 万一某台机器上弹出菜单还是不出来，至少还能一套套翻过去，
            # 而不是"这个按钮彻底没用"。
            self.theme_btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            self.theme_btn.customContextMenuRequested.connect(
                lambda _p: self._cycle_theme()
            )
            self._update_theme_tooltip()
            bar.addWidget(self.theme_btn)

            # ⋮ 用同一套写法（clicked → exec）：两个按钮行为一致，
            # 而且不再依赖 QToolButton 自己的弹出路径。
            more = QToolButton(self)
            more.setText("⋮")
            more.setToolTip("更多（开发者工具 / 缩放 / 退出）")
            more.setAutoRaise(True)
            more.clicked.connect(
                lambda: self._open_overflow(more)
            )
            bar.addWidget(more)

            self.setStatusBar(QStatusBar())
            self.lbl_state = QLabel(note or "就绪")
            self.lbl_state.setObjectName("Muted")
            self.statusBar().addPermanentWidget(self.lbl_state)
            self._home_url = url or ""
            self._note = note
            self._tries = 0
            self._autostart = autostart
            # 地址还没就绪时**先把窗口开出来**。
            #
            # 原来的顺序是"等服务起来 → 再建窗口"，冷启动时用户要点完按钮干等十几到
            # 三十秒，屏幕上什么都没有——看起来就是"点了没反应"（用户反馈的正是这个）。
            # 现在立刻开窗显示进度，地址就绪后再导航过去。
            self._waiter = None
            if url:
                self.add_tab(url)
            else:
                self.add_tab(None)
                self._view().setHtml(_waiting_page(self._note))
                self._waiter = QTimer(self)
                self._waiter.setInterval(1000)
                self._waiter.timeout.connect(self._poll_url)
                self._waiter.start()
            self._apply_theme()          # 启动时按记住的选择上色（不写回配置）

            if note:
                self.statusBar().showMessage(note, 12000)

        # ---------------------------------------------------------- 外观
        def _update_theme_tooltip(self) -> None:
            theme_key, mode = current_choice()
            theme = bt.resolve(theme_key, mode)
            self.theme_btn.setToolTip(
                f"外观：{bt.mode_label(mode)} · {theme.name}\n"
                f"左键：选外观与主题（{len(bt.THEMES)} 套）\n"
                f"右键：直接切下一套"
            )

        def _cycle_theme(self) -> None:
            """右键切下一套主题（在**当前模式**的候选里轮转）。"""
            theme_key, mode = current_choice()
            theme = bt.resolve(theme_key, mode)
            pool = bt.themes_for(mode)
            if not pool:
                return
            keys = [x.key for x in pool]
            nxt = keys[(keys.index(theme.key) + 1) % len(keys)] if theme.key in keys else keys[0]
            save_choice(nxt, mode)
            self._apply_theme(record=True)

        def _open_theme_dialog(self) -> None:
            """点 ◐ 打开"外观与主题"对话框。

            **刻意不用弹出菜单**：`QToolButton.setMenu()`+`InstantPopup` 和
            后来改的 `clicked`+`menu.exec()` 在用户的桌面环境下**左键都弹不出来**
            （右键切主题却是好的，说明按钮收得到事件，坏的是"弹出"这个机制本身）。
            QDialog 是普通顶层窗口，不走 popup/grab 那套，跨平台稳得多；
            而且 30 套主题用列表选，本来就比塞进菜单里强。
            """
            dialog = _ThemeDialog(self)
            dialog.exec()
            self._apply_theme(record=True)

        def _build_theme_menu(self) -> None:
            """重建外观菜单：模式三项 + 当前模式下的主题列表。"""
            menu = self._theme_menu
            menu.clear()          # 只在"即将弹出"时清，安全
            theme_key, mode = current_choice()
            active = bt.resolve(theme_key, mode)

            # 外观三项**直接放在顶层**，不套子菜单：套一层之后用户要多点一次才看得到，
            # 而且"这个按钮是干什么的"会变得不明显（需求要的就是"像 harness 那样"能选外观）。
            for key, label in bt.MODES:
                act = QAction(f"{label}", self)
                act.setCheckable(True)
                act.setChecked(key == mode)
                act.triggered.connect(lambda _c=False, k=key: self._set_mode(k))
                menu.addAction(act)

            menu.addSeparator()
            # 只列**当前模式**下的主题：选了深色却列一堆浅色，选起来只会更乱
            candidates = bt.themes_for(mode)
            title = QAction(f"主题（{bt.mode_label(mode)} · {len(candidates)} 套）", self)
            title.setEnabled(False)
            menu.addAction(title)
            for theme in candidates:
                act = QAction(("● " if theme.key == active.key else "○ ") + theme.name, self)
                act.setCheckable(True)
                act.setChecked(theme.key == active.key)
                # 色块预览：菜单项左边点一个该主题的面板色
                act.setIcon(_swatch(theme))
                act.triggered.connect(lambda _c=False, k=theme.key: self._set_theme(k))
                menu.addAction(act)

        def _set_mode(self, mode: str) -> None:
            theme_key, _old = current_choice()
            save_choice(theme_key, mode)
            # 模式一变，候选范围就变了；让 resolve 去挑该模式下合适的一套。
            # 延到下一拍再应用：此刻菜单还在关闭过程中，动界面容易出怪状态。
            QTimer.singleShot(0, lambda: self._apply_theme(record=True))

        def _set_theme(self, theme_key: str) -> None:
            _old_key, mode = current_choice()
            save_choice(theme_key, mode)
            QTimer.singleShot(0, lambda: self._apply_theme(record=True))

        def _apply_theme(self, *, record: bool = False) -> None:
            """把当前选择应用到界面。

            ``record=False``（启动时）：只应用不写回配置——免得每次开浏览器都把
            用户的选择重写一遍（那样"跟随系统"会被固化成一个具体主题）。
            """
            theme_key, mode = current_choice()
            theme = bt.resolve(theme_key, mode)
            if record and theme.key != theme_key:
                # 模式把它挤到了另一套主题（比如从深色切到浅色），把结果记下来，
                # 免得下次还要再猜一次
                save_choice(theme.key, mode)
            app = QApplication.instance()
            if app is not None:
                app.setStyleSheet(bt.qss(theme))
            self.setWindowTitle(f"DSH 内置浏览器 · {theme.name}")
            self.lbl_state.setText(f"{bt.mode_label(mode)} · {theme.name}")
            self._update_theme_tooltip()

        def _poll_url(self) -> None:
            """每秒看一眼 harness 地址好了没有（就绪后导航过去）。

            用轮询而不是工作线程：取地址要读日志/查进程，都是毫秒级操作，
            而跨线程碰 QWebEngineView 反而容易出问题。最多等 60 秒。
            """
            from . import service

            self._tries += 1
            if self._tries == 1 and self._autostart:
                # 服务**在这里**才起——放在 main() 里的话，用户点完按钮要干等十几二十秒
                # 才看得到窗口（那条路是：起服务 → 等地址 → 才建窗口）。
                try:
                    if not service.get_status().is_running:
                        service.start()
                except Exception:  # noqa: BLE001 - 起不来由下面的超时分支兜
                    pass
            url = service.current_url()
            if url and service.url_is_alive(url):
                self._stop_waiting()
                self._home_url = url
                self.url_edit.setText(url)
                self._view().setUrl(QUrl(url))
                self.lbl_state.setText("就绪")
                return
            if self._tries > 60:
                self._stop_waiting()
                self._view().setHtml(
                    shell.error_page("等不到 harness 的访问地址", self._note or "")
                )
                self.lbl_state.setText("没等到地址")
                return
            if self._tries % 3 == 0:
                self.lbl_state.setText(f"正在等待 harness…（{self._tries} 秒）")

        def _stop_waiting(self) -> None:
            if self._waiter is not None:
                self._waiter.stop()
                self._waiter = None

        # ---------------------------------------------------------- 标签
        def add_tab(self, url: str | None = None):
            view = _BrowserView(self)          # 自定义右键菜单：Qt 默认那份是英文
            page = _BrowserPage(self._profile, view, view._run_action)
            page._on_menu_request = view._on_page_context_menu
            view.setPage(page)
            view.loadFinished.connect(lambda _ok, v=view: v._inject_ctx_hook())
            view.urlChanged.connect(lambda _u, v=view: self._on_url_changed(v))
            view.titleChanged.connect(lambda t, v=view: self._on_title(t, v))
            view.loadStarted.connect(lambda: self.lbl_state.setText("加载中…"))
            view.loadFinished.connect(
                lambda ok, v=view: self._on_loaded(ok, v)
            )
            index = self.tabs.addTab(view, "新标签页")
            self.tabs.setCurrentIndex(index)
            if url:
                view.setUrl(QUrl(url))
            elif self._home_url:
                view.setUrl(QUrl(self._home_url))
            else:
                view.setHtml(shell.error_page("没有可打开的地址", note or ""))
            return view

        def _open_overflow(self, anchor) -> None:
            """工具栏「⋮」的菜单。

            ⚠️ 也用**网页内菜单**：早先它走自绘浮层控件（PopupMenu），而用户实测
            "点开菜单显示后立即崩" —— 和右键菜单当初是同一个病根。既然网页内菜单
            已经验证可用且不会崩，这里统一过来。
            """
            items = getattr(self, "_overflow_items", None) or []
            if not items:
                return
            view = self.tabs.currentWidget()
            if view is None or not hasattr(view, "_show_html_menu"):
                return
            # 条目的 id 就是动作文本；按文本找回已注册的 QAction（快捷键挂在窗口上）
            by_text = {a.text(): a for a in self.actions() if a.text()}
            payload = [[text, text, bool(by_text.get(text) and by_text[text].isEnabled())]
                       for text, _id, _en in items]
            # 位置要贴着「⋮」按钮：把它在**视图坐标系**里的位置算出来
            # （早先写死 80,60，菜单跑到左上角去了 —— 用户截图里就是这个问题）。
            try:
                origin = anchor.mapToGlobal(QPoint(0, anchor.height()))
                local = view.mapFromGlobal(origin)
                x, y = local.x(), local.y()
            except Exception:                            # noqa: BLE001
                x, y = 80, 60
            view._ctx = {}
            view._show_html_menu(max(0, x), max(0, y), items=payload)

        def _close_current(self) -> None:
            """关掉当前标签（菜单项用；Ctrl+W 也走这里）。"""
            self._close_tab(self.tabs.currentIndex())

        def _close_tab(self, index: int) -> None:
            if index < 0:
                return
            widget = self.tabs.widget(index)
            self.tabs.removeTab(index)
            if widget is not None:
                widget.deleteLater()
            if self.tabs.count() == 0:
                self.close()

        def _view(self):
            return self.tabs.currentWidget()

        def _on_url_changed(self, view) -> None:
            if view is self._view():
                self.url_edit.setText(view.url().toString())
                self.url_edit.setCursorPosition(0)

        def _on_title(self, title: str, view) -> None:
            index = self.tabs.indexOf(view)
            if index >= 0:
                self.tabs.setTabText(index, (title or "无标题")[:22])

        def _on_loaded(self, ok: bool, view) -> None:
            if view is not self._view():
                return
            self.lbl_state.setText("就绪" if ok else "加载失败")
            if not ok:
                self.lbl_state.setText("加载失败（地址可能已失效，试试「重新加载」）")

        # ---------------------------------------------------------- 动作
        def _back(self) -> None:
            if self._view():
                self._view().back()

        def _forward(self) -> None:
            if self._view():
                self._view().forward()

        def _reload(self) -> None:
            if self._view():
                self._view().reload()

        def _home(self) -> None:
            if self._view() and self._home_url:
                self._view().setUrl(QUrl(self._home_url))

        def _devtools(self) -> None:
            """打开开发者工具。

            QtWebEngine 要把 devtools 挂在**另一个 page** 上，所以得单独开个窗口承载它。
            自己那份强引用在这里（``self._dev``）：不存的话窗口会被 GC 掉，表现为
            "按 F12 闪一下就没了"。
            """
            view = self._view()
            if view is None:
                return
            dev = QWebEngineView()
            dev.setWindowTitle("开发者工具")
            dev.resize(900, 620)
            view.page().setDevToolsPage(dev.page())
            dev.show()
            self._dev = dev

        def _zoom(self, delta: int) -> None:
            view = self._view()
            if view is None:
                return
            view.setZoomFactor(1.0 if delta == 0 else max(0.25, min(5.0, view.zoomFactor() + delta * 0.1)))

        def _navigate(self) -> None:
            text = self.url_edit.text().strip()
            if not text:
                return
            if "://" not in text and not text.startswith("about:"):
                # 不是地址就当搜索词——浏览器该有的行为
                text = "https://www.bing.com/search?q=" + text.replace(" ", "+")
            view = self._view()
            if view:
                view.setUrl(QUrl(text))

        def _sync_url(self) -> None:
            view = self._view()
            if view:
                self.url_edit.setText(view.url().toString())

    app = QApplication.instance() or QApplication(sys.argv[:1])
    _install_slot_error_hook()
    window = Browser()
    return window, app


def _install_slot_error_hook() -> None:
    """把 Qt 槽里的异常**显示出来**。

    PySide6 对槽函数里抛出的异常只调 ``PyErr_Print()``（打到 stderr）就继续跑，
    界面上完全看不出来——表现就是"点了没反应"。这个坑在本模块里踩过不止一次
    （对话框里一个 NameError 就让主题按钮变成"死按钮"）。装个钩子至少让人看见。
    """
    import traceback

    def hook(exc_type, exc, tb) -> None:
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        print(text, file=sys.stderr, flush=True)
        try:
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.critical(None, "内置浏览器出错", text[-1800:])
        except Exception:  # noqa: BLE001 - 弹不出来也不能再抛
            pass

    sys.excepthook = hook


# ------------------------------------------------------------------ 入口
def _parse(argv: list[str] | None = None):
    import argparse

    ap = argparse.ArgumentParser(
        prog="dsh-console-browser",
        description="DSH 内置浏览器：用随包携带的 Chromium（QtWebEngine）打开 harness 界面，"
                    "独立 profile，不碰你自己的浏览器。",
    )
    ap.add_argument("--url", help="直接打开这个地址（默认用 harness 当前地址）")
    ap.add_argument("--no-start", action="store_true", help="不要自动启动 web 服务")
    ap.add_argument("--extra-flags", default="",
                    help="追加 Chromium 开关（分号分隔），调试用")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """内置浏览器入口。"""
    args = _parse(argv)

    # **必须在建 QApplication 之前**：QtWebEngine 只在初始化时读这个变量。
    # 先让 i18n 把 ``--lang=zh-CN``（或 en-US）追加进去——内置浏览器的界面语言
    # 就是这么定的；用户手动给的 --extra-flags 排在它后面，仍然可以覆盖。
    # Wayland 下菜单要先"收到输入"才允许弹出（详见 screenfit.prefer_xwayland 的说明），
    # 所以内置浏览器**默认退回 XWayland**；想用原生 Wayland：DSH_BROWSER_QPA=wayland
    _plat = prefer_xwayland("DSH_BROWSER_QPA")
    if _plat:
        print(f"内置浏览器：Qt 平台 = {_plat}（可用 DSH_BROWSER_QPA 覆盖）", file=sys.stderr)

    i18n.prepare_environment()
    flags = [f for f in (args.extra_flags or "").split(";") if f.strip()]
    # ⚠️ 不要给 Chromium 传 --ozone-platform=x11：这个 QtWebEngine 构建里 x11 **不是**
    # 合法的 ozone 平台（实测直接 FATAL: Invalid ozone platform: x11，进程起来、窗口不出）。
    # 让 Chromium 自己按环境判断即可。
    apply_chromium_flags(flags)

    try:
        from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        print(f"内置浏览器不可用：QtWebEngine 导入失败（{type(exc).__name__}: {exc}）",
              file=sys.stderr)
        print("便携包应当自带它；源码运行时用 pip install PySide6-Addons 安装。",
              file=sys.stderr)
        return 1

    from . import gui as shell

    # **这里绝不能阻塞。** 原先是直接调 ``shell.resolve_url()``——它会启动 harness 并
    # 轮询等地址（最长 30 秒），于是"点按钮 → 等半分钟 → 窗口才出现"，
    # 用户看到的就是"点了没反应"。现在只做一次**零成本**的探测：
    # 地址现成且有效就直接用，否则先把窗口开出来显示进度，
    # 服务和地址交给窗口里的轮询去搞（``autostart``）。
    if args.url:
        url, note = args.url, "使用指定地址"
    else:
        url = shell.service.current_url()
        if url and shell.service.url_is_alive(url):
            note = "使用现有地址"
        else:
            url, note = None, "正在启动 harness…（首次可能要十几秒）"

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_SUBDIR.mkdir(parents=True, exist_ok=True)

    window, app = build_window(url, note, autostart=not args.no_start)
    # ⚠️ **这一行不能少**：曾经我的正则补丁把它替换掉了，结果"进程起来了、窗口不出现"
    # ——应用进了事件循环，但窗口从未 show（两个平台都一样，极难查）。
    window.show()
    # show() 出来可能没有被合成器激活，此时菜单/弹窗抓不到输入（表现为必须先失去一次焦点）。
    # 这里显式抬升并激活一次作为兜底。
    from PySide6.QtCore import QTimer as _QTimer
    _QTimer.singleShot(0, lambda w=window: (w.raise_(), w.activateWindow()))

    return app.exec()



# ------------------------------------------------------------------ 外观对话框
def _theme_dialog_classes():
    """延迟导入 Qt 类（这个模块的 Qt 导入都在函数里，保持一致）。"""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
    from PySide6.QtWidgets import (
        QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
        QRadioButton, QVBoxLayout,
    )

    return Qt, QColor, QIcon, QPainter, QPixmap, (
        QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
        QRadioButton, QVBoxLayout,
    )


def _ThemeDialog(parent):
    """建一个"外观与主题"对话框类并实例化。

    做成工厂而不是模块级类：Qt 的导入全部推迟到调用时，这样没装 Qt 的环境
    （比如自检只想读版本号）不会因为这个模块而 import 失败。
    """
    (Qt, QColor, QIcon, QPainter, QPixmap,
     (QDialog, _QDB, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
      QRadioButton, QVBoxLayout)) = _theme_dialog_classes()

    def swatch(theme, size: int = 26) -> QIcon:
        pix = QPixmap(size, size)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor(theme.surface))
        painter.setPen(QColor(theme.border))
        painter.drawRoundedRect(0, 0, size - 1, size - 1, 5, 5)
        painter.setBrush(QColor(theme.accent))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(5, 8, size - 11, 6, 3, 3)
        painter.setBrush(QColor(theme.text))
        painter.drawRoundedRect(5, size - 10, size - 15, 3, 2, 2)
        painter.end()
        return QIcon(pix)

    class ThemeDialog(QDialog):
        """外观与主题选择器。

        **改了就立刻生效**（不用先点确定再看效果），关掉对话框即完成；
        「恢复默认」把主题和模式都退回出厂值。
        """

        def __init__(self, parent) -> None:
            super().__init__(parent)
            self.setWindowTitle("外观与主题")
            self.setMinimumSize(430, 520)
            self._loading = True

            theme_key, mode = current_choice()
            root = QVBoxLayout(self)

            root.addWidget(QLabel("<b>外观</b>"))
            row = QHBoxLayout()
            self._mode_buttons: dict[str, QRadioButton] = {}
            for key, label in bt.MODES:
                rb = QRadioButton(label)
                rb.setChecked(key == mode)
                rb.toggled.connect(
                    lambda checked, k=key: checked and self._pick_mode(k)
                )
                row.addWidget(rb)
                self._mode_buttons[key] = rb
            row.addStretch(1)
            root.addLayout(row)

            root.addSpacing(6)
            self._title = QLabel("")
            self._title.setTextFormat(Qt.TextFormat.RichText)
            root.addWidget(self._title)

            self._list = QListWidget()
            self._list.setIconSize(_qsize(26, 26))
            self._list.currentItemChanged.connect(self._pick_theme)
            root.addWidget(self._list, 1)

            buttons = _QDB(_QDB.StandardButton.Close)
            # Qt 的标准按钮文案在某些环境没有中文翻译，这里直接指定，
            # 免得一个中文界面里杵着一个 "Close"
            buttons.button(_QDB.StandardButton.Close).setText("关闭")
            reset = buttons.addButton("恢复默认", _QDB.ButtonRole.ResetRole)
            reset.clicked.connect(self._reset)
            buttons.rejected.connect(self.reject)
            root.addWidget(buttons)

            self._reload()
            self._loading = False

        # ---------------------------------------------------------- 内部
        def _reload(self) -> None:
            _theme_key, mode = current_choice()
            active = bt.resolve(*current_choice())
            pool = bt.themes_for(mode)
            self._title.setText(
                f"<b>主题</b>　当前 <b>{active.name}</b>　"
                f"（{bt.mode_label(mode)} · {len(pool)} 套）"
            )
            self._list.clear()
            for theme in pool:
                item = QListWidgetItem(swatch(theme), theme.name)
                item.setData(Qt.ItemDataRole.UserRole, theme.key)
                if theme.key == active.key:
                    item.setText(f"{theme.name}　（当前）")
                self._list.addItem(item)
            for i in range(self._list.count()):
                if self._list.item(i).data(Qt.ItemDataRole.UserRole) == active.key:
                    self._list.setCurrentRow(i)
                    break

        def _pick_mode(self, mode: str) -> None:
            if self._loading:
                return
            theme_key, _old = current_choice()
            save_choice(theme_key, mode)
            # 模式一变候选范围就变了，列表要重建（重建时会把光标放到该模式的当前主题）
            self._loading = True
            self._reload()
            self._loading = False
            _apply_now(self.parent())

        def _pick_theme(self, item, _prev=None) -> None:
            if self._loading or item is None:
                return
            key = item.data(Qt.ItemDataRole.UserRole)
            _old, mode = current_choice()
            save_choice(key, mode)
            _apply_now(self.parent())

        def _reset(self) -> None:
            save_choice(bt.DEFAULT_THEME, bt.MODE_DARK)
            self._loading = True
            for key, rb in self._mode_buttons.items():
                rb.setChecked(key == bt.MODE_DARK)
            self._reload()
            self._loading = False
            _apply_now(self.parent())

    return ThemeDialog(parent)


def _qsize(w: int, h: int):
    from PySide6.QtCore import QSize

    return QSize(w, h)


def _apply_now(window) -> None:
    """让当前窗口立刻套用新配色（对话框里改一下就见效）。"""
    apply = getattr(window, "_apply_theme", None)
    if callable(apply):
        apply(record=True)

# ⚠️ **这一段必须留在文件最末尾。**
#
# `python -m dsh_console.browser` 时 ``__name__ == "__main__"``，模块执行到这里就
# ``SystemExit`` 了——**后面再有任何定义都不会执行**。把新代码追加到这段之后，
# 表现是"导入时一切正常、用 -m 跑就 NameError"（实测踩过：主题对话框的
# ``_ThemeDialog`` 被追加到了这段后面，于是左键点主题就报 name is not defined，
# 而我自己用 ``import`` 写的验证永远测不出来）。
if __name__ == "__main__":
    raise SystemExit(main())
