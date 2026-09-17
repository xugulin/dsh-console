"""主窗口：侧边栏导航 + 页面堆栈 + 主题管理。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QScrollArea,
    QApplication,
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..screenfit import apply_screen_fit
from .. import config, harness, service, themes
from ..workers import run_async
from .components import apply_screen_fit
from .page_billing import BillingPage
from .page_dashboard import DashboardPage
from .page_donate import DonatePage
from .page_harness import HarnessPage
from .page_logs import LogsPage
from .page_market import MarketPage
from .page_models import ModelsPage
from .page_plugins import PluginsPage
from .page_settings import SettingsPage
from .page_plugins_skills import PluginsSkillsPage
from .page_sysinfo import SysInfoPage
from .page_skills_installed import InstalledSkillsPage
from .page_skills import SkillsPage

# 配置读写统一走 dsh_console.config（更新功能也要用，不能两处各写一份）
_load_config = config.load
_save_config = config.save


def make_icon(theme: themes.Theme, size: int = 64) -> QIcon:
    """用主题强调色画一个应用图标，避免依赖外部资源文件。

    所有几何量都按 ``size`` 等比缩放——早期版本把字号写死成 30px，
    渲染到 256px 时文字会撑出圆角方块。
    """
    from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath

    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    margin = size * 0.0625          # 4/64
    radius = size * 0.22
    path.addRoundedRect(margin, margin, size - 2 * margin, size - 2 * margin, radius, radius)
    p.fillPath(path, QColor(theme.accent))

    f = QFont()
    f.setPixelSize(max(8, int(size * 0.38)))
    f.setBold(True)
    # 略微收紧字距，让 DSH 三个字母在方块里更从容
    f.setLetterSpacing(QFont.PercentageSpacing, 96)
    p.setFont(f)
    p.setPen(QColor(theme.accent_text))
    p.drawText(pm.rect(), Qt.AlignCenter, "DSH")
    p.end()
    return QIcon(pm)


class MainWindow(QMainWindow):
    """控制台主窗口。"""

    NAV = (
        ("dashboard", "控制台"),
        ("models", "模型与价格"),
        ("billing", "账单"),
        ("plugins", "插件与技能"),
        ("market", "插件市场"),
        ("skills", "技能市场"),
        ("sysinfo", "本机信息"),
        ("logs", "日志"),
        ("settings", "设置"),
        ("donate", "捐赠支持"),
    )

    def __init__(self) -> None:
        super().__init__()
        cfg = _load_config()
        self.theme = themes.get_theme(cfg.get("theme", themes.DEFAULT_THEME))
        self._last_status: service.ServiceStatus | None = None
        self.setWindowTitle("DSH 控制台")
        # 按屏幕算尺寸（小屏上自适应，别让右下角跑到屏幕外）
        apply_screen_fit(self, (1180, 800), (940, 640))
        self._build()
        self.apply_theme(self.theme.key, persist=False)

        # 顶部状态铃：即使不在控制台页也能看到服务状态
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(5000)
        self._status_timer.timeout.connect(self._poll_status)
        self._status_timer.start()
        self._poll_status()

        # **启动后台预热**：趁用户还在看首页，把会话成本扫一遍并落盘。
        # 首次解析 116 个文件要 10-19 秒，而这一刻用户通常还在首页；等他点到账单页，
        # 缓存已经建好，页面是**瞬间**出来的。扫描在后台线程，不挡界面。
        self._warm_cache()

    def _sync_nav_heights(self) -> None:
        """按**当前样式**把导航按钮的自然高度钉住。

        为什么不能写死数字：`#NavButton` 的 QSS 给了 `padding: 10px 14px` +
        `margin: 3px 10px` + `font-size: 13.5px`，自然高度是 39px。我一开始写死 36，
        结果**比需要的少 3px，文字被垂直裁掉**，而且固定高度和 margin 打架、相邻项的
        色块互相错位——用户看到的"割裂丑陋、文字显示不全"就是这个。
        必须在上完主题之后调：`sizeHint()` 要算上 QSS 才准。
        """
        for btn in self.nav_group.buttons():
            h = btn.sizeHint().height()
            if h > 0:
                btn.setMinimumHeight(h)
                btn.setMaximumHeight(h)

    # ------------------------------------------------------------------ UI
    def _build(self) -> None:
        central = QWidget()
        row = QHBoxLayout(central)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        # ---- 侧边栏
        side = QWidget()
        side.setObjectName("Sidebar")
        # 纯 QWidget 不设这个属性就不画 QSS 背景和描边 —— 侧边栏的
        # `background` / `border-right` 一直没生效过，所以它和内容区糊成一片。
        side.setAttribute(Qt.WA_StyledBackground, True)
        side.setFixedWidth(212)
        sv = QVBoxLayout(side)
        # 右边留 1px：侧边栏的 border-right 画在**自己的矩形边缘**上，
        # 而子控件会占满整个宽度把它盖住。留出这 1px，细线才看得见。
        sv.setContentsMargins(0, 0, 1, 0)
        sv.setSpacing(0)

        brand = QLabel("DSH 控制台")
        brand.setObjectName("Brand")
        sub = QLabel("Harness Console")
        sub.setObjectName("BrandSub")
        sv.addWidget(brand)
        sv.addWidget(sub)

        # ---- 导航：放进滚动区，**按钮尺寸不变**
        #
        # 原来是直接塞进侧边栏的 QVBoxLayout。窗口一矮，Qt 就把这些按钮**压扁**
        # （实测字都挤成一条），而它们本来就不该跟着窗口缩放。
        # 这里给每个按钮钉死高度，再套一层滚动区：放得下时和以前一模一样，
        # 放不下时出现滚动条——**不改尺寸位置，只多一条滚动**。
        nav_scroll = QScrollArea()
        nav_scroll.setObjectName("SidebarScroll")
        nav_scroll.setWidgetResizable(True)          # 让内层宽度跟着侧边栏
        nav_scroll.setFrameShape(QFrame.NoFrame)
        nav_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        nav_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        nav_host = QWidget()
        nav_host.setObjectName("SidebarNav")
        # 同 ScrollPage：纯 QWidget 不设这个属性就不画样式表背景
        nav_host.setAttribute(Qt.WA_StyledBackground, True)
        nav_scroll.setAttribute(Qt.WA_StyledBackground, True)
        nav_lay = QVBoxLayout(nav_host)
        nav_lay.setContentsMargins(0, 0, 0, 0)
        nav_lay.setSpacing(0)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        for key, label in self.NAV:
            btn = QPushButton(label)
            btn.setObjectName("NavButton")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            # 高度**不在建的时候定**：此刻样式表还没上，sizeHint 算不出 QSS 的
            # padding/font-size。交给 _sync_nav_heights() 在上完主题之后钉。
            self.nav_group.addButton(btn)
            nav_lay.addWidget(btn)
        nav_lay.addStretch(1)
        nav_scroll.setWidget(nav_host)
        sv.addWidget(nav_scroll, 1)                  # 剩余空间全给滚动区

        # ---- 版本按钮：平时就显示当前 harness 版本，点它进 Harness 页
        # 它**放进 nav_group**（而不是单独一个控件）：这样互斥选中、_goto 的按下标
        # 分发都能直接复用，不用另写一套选中状态管理。它是第 11 个按钮 => 下标 10，
        # 正好对上 pages 里的 HarnessPage。
        self.btn_harness = QPushButton(self._version_button_text())
        self.btn_harness.setObjectName("VersionButton")
        self.btn_harness.setCheckable(True)
        self.btn_harness.setCursor(Qt.PointingHandCursor)
        self.btn_harness.setToolTip("harness 版本、安装位置、运行状态与升级")
        self.nav_group.addButton(self.btn_harness)
        sv.addWidget(self.btn_harness)

        self.side_status = QLabel("状态：读取中…")
        self.side_status.setObjectName("SidebarFooter")
        self.side_status.setWordWrap(True)
        sv.addWidget(self.side_status)
        row.addWidget(side)

        # ---- 右侧：顶部功能区 + 页面，中间一条 1px 细横线
        #
        # 照 harness 的做法：**顶部是一条通栏功能区，和下面内容紧贴**，靠一条细线分界，
        # 而不是每个页面各自画一块带圆角的浮动卡片。
        right = QWidget()
        right.setObjectName("RightPane")
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(0)

        topbar = QWidget()
        topbar.setObjectName("TopBar")
        # 纯 QWidget 不设这个属性，QSS 的 background / border-bottom 一概不画
        topbar.setAttribute(Qt.WA_StyledBackground, True)
        topbar.setFixedHeight(44)
        tb = QHBoxLayout(topbar)
        tb.setContentsMargins(18, 0, 18, 0)
        tb.setSpacing(10)
        self.top_title = QLabel("控制台")
        self.top_title.setObjectName("TopBarTitle")
        tb.addWidget(self.top_title)
        tb.addStretch(1)
        self.top_status = QLabel("")
        self.top_status.setObjectName("TopBarStatus")
        tb.addWidget(self.top_status)
        rv.addWidget(topbar)

        self.stack = QStackedWidget()
        self.page_dashboard = DashboardPage(self.theme)
        self.page_models = ModelsPage(self.theme)
        self.page_billing = BillingPage(self.theme)
        self.page_plugins = PluginsSkillsPage(self.theme)
        self.page_market = MarketPage(self.theme)
        self.page_skills = SkillsPage(self.theme)
        self.page_sysinfo = SysInfoPage(self.theme)
        self.page_logs = LogsPage(self.theme)
        self.page_settings = SettingsPage(self.theme)
        self.page_donate = DonatePage(self.theme)
        self.page_harness = HarnessPage(self.theme)
        self.pages = [
            self.page_dashboard,
            self.page_models,
            self.page_billing,
            self.page_plugins,
            self.page_market,
            self.page_skills,
            self.page_sysinfo,
            self.page_logs,
            self.page_settings,
            self.page_donate,
            # 第 11 个：不对应 NAV 里的项，入口是侧边栏那个版本按钮（见 _build）
            self.page_harness,
        ]
        for p in self.pages:
            self.stack.addWidget(p)
        rv.addWidget(self.stack, 1)
        row.addWidget(right, 1)

        self.setCentralWidget(central)
        self._mark_first_cards()
        self._set_active_updates(0)      # 一开始只有控制台页需要更新

        # 插件集合变化时互相刷新（市场装了东西 → 插件页要更新）
        # 市场里装了东西 → 插件与技能页、设置页都要刷新
        self.page_plugins.changed.connect(self.page_settings.load_plugins)
        self.page_market.changed.connect(self.page_plugins.activate)
        self.page_market.changed.connect(self.page_settings.load_plugins)
        self.page_skills.changed.connect(self.page_plugins.activate)
        self.page_skills.changed.connect(self.page_settings.load_plugins)
        # 升级之后侧边栏的版本号要跟着变
        self.page_harness.version_changed.connect(self._set_version_text)

        for i, btn in enumerate(self.nav_group.buttons()):
            btn.clicked.connect(lambda _, idx=i: self._goto(idx))
        self.nav_group.buttons()[0].setChecked(True)

        self.page_settings.theme_requested.connect(self.apply_theme)
        # 只激活首屏；其余页面在第一次切过去时才加载（启动时零网络/零子进程）
        self._activate(0)

    # -------------------------------------------------------------- 行为
    def _version_button_text(self) -> str:
        """版本按钮的文字：平时就带着当前版本号。

        ``installed_version()`` 只读一个 package.json（实测 0.4 ms、不起子进程），
        所以构造函数里直接调没问题——不像 ``harness.info()`` 要跑 node/npm。
        """
        version = harness.installed_version()
        return f"Harness {version}" if version else "Harness"

    def _set_version_text(self, version: str) -> None:
        self.btn_harness.setText(f"Harness {version}" if version else "Harness")

    def _goto(self, index: int) -> None:
        if hasattr(self, "top_title"):
            self.top_title.setText(self.NAV[index][1] if index < len(self.NAV) else "Harness")
        """切页：先让旧页 deactivate（停轮询），再让新页 activate（首次才拉数据）。"""
        prev = self.stack.currentIndex()
        if prev == index:
            return
        if 0 <= prev < len(self.pages):
            stop = getattr(self.pages[prev], "deactivate", None)
            if callable(stop):
                stop()
        self.stack.setCurrentIndex(index)
        self._sync_nav(index)
        self._activate(index)
        self._set_active_updates(index)

    def _warm_cache(self) -> None:
        """后台预热会话成本缓存。

        只在**还没有缓存**时才跑：`scan_all_sessions_cached` 命中就直接返回，
        所以这一步在正常情况下几乎是零成本的。
        """
        from .. import billing

        run_async(billing.scan_all_sessions_cached, None, None)

    def _mark_first_cards(self) -> None:
        """给每个页面的**第一张卡片**打上 ``firstCard`` 标记。

        它头顶就是顶部功能区的下边线，如果再画自己的上边线，两条线之间夹着一段
        页面留白——看起来就是"一长一短两根横线"（用户截图里每页都有）。
        标记交给 QSS 处理（``#Card[firstCard="true"] { border-top: none; }``），
        这样布局和配色仍然归样式表管。
        """
        from .components import Card

        for page in self.pages:
            body = getattr(page, "body", None)
            if body is None:
                continue
            for i in range(body.count()):
                w = body.itemAt(i).widget()
                if w is None:
                    continue          # 顶部的按钮行是 layout，跳过继续找
                if isinstance(w, Card):
                    w.setProperty("firstCard", True)
                    # 动态属性变了要重新 polish，否则样式表不会重新匹配
                    w.style().unpolish(w)
                    w.style().polish(w)
                break                 # 只看第一个**控件**

    def _set_active_updates(self, index: int) -> None:
        """只让**当前显示的那一页**参与重绘/布局。

        11 个页面全都活在 QStackedWidget 里，Qt 在窗口缩放时会把它们**逐个布局一遍**
        （哪怕 10 个根本看不见）。实测：关掉隐藏页的更新后，缩放耗时从 19.8 ms/次
        降到 10.4 ms/次——正好一倍。滚动本来就是流畅的（6.5 ms），所以这一项就是
        "界面卡"的主因。
        """
        for i, page in enumerate(self.pages):
            page.setUpdatesEnabled(i == index)

    def _sync_nav(self, index: int) -> None:
        """让侧边栏的选中状态跟上当前页。

        版本按钮也在 nav_group 里，所以这条对"第 10 页"同样成立；程序化切页
        （自检、截图工具、将来别处的跳转）时按钮不会自己变，得在这里同步。
        """
        buttons = self.nav_group.buttons()
        if 0 <= index < len(buttons):
            buttons[index].setChecked(True)

    def _activate(self, index: int) -> None:
        page = self.pages[index]
        start = getattr(page, "activate", None)
        if callable(start):
            start()
            return
        # 没有 activate 的页面退回旧行为
        for name in ("refresh", "reload"):
            fn = getattr(page, name, None)
            if callable(fn):
                fn()
                return

    def _poll_status(self) -> None:
        from ..workers import run_async

        def done(st: service.ServiceStatus) -> None:
            self._last_status = st
            self._render_status(st)

        run_async(service.get_status_cached, done, None)

    def _render_status(self, st: service.ServiceStatus) -> None:
        """按当前主题重新着色状态文字。

        切主题时**只重绘、不重新查询**——否则每换一次主题就要 fork 一个
        systemctl，7 次切换会白白攒下 7 个后台任务。
        """
        if st.unit_running:
            color = self.theme.ok
        elif st.foreign_running:
            # 在跑，但不归 systemd 管：用 warn 色，别让侧边栏看起来一切正常
            color = self.theme.warn
        else:
            color = self.theme.danger if st.is_failed else self.theme.text_faint
        self.side_status.setText(
            f"<span style='color:{color}'>●</span> {st.label}"
            + (f"<br>{st.uptime_text}" if st.is_running else "")
        )
        self.side_status.setTextFormat(Qt.RichText)

    def apply_theme(self, key: str, persist: bool = True) -> None:
        self.theme = themes.get_theme(key)
        app = QApplication.instance()
        if app is not None:
            # 字体在 QApplication 上设一次（不要在 QSS 里用 * 通配——见 themes.build_font）
            app.setFont(themes.build_font())
            app.setStyleSheet(themes.build_qss(self.theme))
        self.setWindowIcon(make_icon(self.theme))
        for p in self.pages:
            fn = getattr(p, "apply_theme", None)
            if callable(fn):
                fn(self.theme)
        self.page_settings.set_active_theme(self.theme.key)
        # 样式刚上完，此刻 sizeHint 才算得准 —— 在这里钉导航按钮的高度
        self._sync_nav_heights()
        if persist:
            cfg = _load_config()
            cfg["theme"] = self.theme.key
            _save_config(cfg)
        # 只重绘状态，不重新查询（避免每次换主题都 fork 一个 systemctl）
        if self._last_status is not None:
            self._render_status(self._last_status)
        else:
            self._poll_status()
