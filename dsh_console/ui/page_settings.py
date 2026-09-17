"""设置页：主题选择、插件列表、关于信息。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, service, themes
from ..themes import Theme
from ..workers import run_async
from .components import Card, ScrollPage


class ThemeSwatch(QFrame):
    """一个可点击的主题色卡。"""

    picked = Signal(str)

    def __init__(self, theme: Theme, active: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme_obj = theme
        self.setObjectName("ThemeSwatch")
        self.setFixedSize(126, 74)
        self.setCursor(Qt.PointingHandCursor)
        self._render(active)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(2)
        name = QLabel(theme.name)
        name.setStyleSheet(
            f"color: {theme.text}; background: transparent; font-weight: 600; font-size: 12.5px;"
        )
        tag = QLabel(theme.tag)
        tag.setStyleSheet(f"color: {theme.text_faint}; background: transparent; font-size: 11px;")
        lay.addWidget(name)
        lay.addWidget(tag)
        lay.addStretch(1)

        dots = QHBoxLayout()
        dots.setSpacing(4)
        for color in (theme.accent, theme.ok, theme.warn, theme.peak):
            d = QLabel()
            d.setFixedSize(12, 12)
            d.setStyleSheet(f"background: {color}; border-radius: 6px;")
            dots.addWidget(d)
        dots.addStretch(1)
        lay.addLayout(dots)

    def _render(self, active: bool) -> None:
        t = self.theme_obj
        border = t.accent if active else t.border
        width = 2 if active else 1
        # 选择器必须写成 #ThemeSwatch（子控件样式表会向下级联）：QLabel 继承自
        # QFrame，写成裸的 `QFrame { border: … }` 会给卡片里的标题、标签、色点
        # 每个 QLabel 都套上一圈同色边框——实测就是标题和「暗色」外面各多一个方框。
        self.setStyleSheet(
            f"#ThemeSwatch {{ background: {t.surface}; border: {width}px solid {border};"
            f" border-radius: 10px; }}"
        )

    def set_active(self, active: bool) -> None:
        self._render(active)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self.picked.emit(self.theme_obj.key)
        super().mousePressEvent(event)


class SettingsPage(ScrollPage):
    """设置与关于。"""

    theme_requested = Signal(str)

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme = theme
        self._swatches: list[ThemeSwatch] = []
        self._build()

    def activate(self) -> None:
        self.load_plugins()

    def _build(self) -> None:
        # 用 ScrollPage 的 body：它已经带了边距，并且**装不下时会滚动**。
        #
        # 原来这一页是普通 QWidget + QVBoxLayout：窗口一矮，布局就把主题网格的两行
        # 压到一起——色卡直接重叠、看不清（用户实测截图）。控件尺寸位置都不该跟着
        # 窗口变，所以正确的做法是"装不下就滚"，而不是把它们压小。
        root = self.body
        root.setSpacing(16)


        # ---- 主题
        theme_card = Card("外观主题", "选择后立即生效。深色主题共 4 套，浅色 3 套。")
        grid = QGridLayout()
        grid.setSpacing(10)
        for i, t in enumerate(themes.THEMES):
            sw = ThemeSwatch(t, active=(t.key == self.theme.key))
            sw.picked.connect(self.theme_requested.emit)
            self._swatches.append(sw)
            grid.addWidget(sw, i // 4, i % 4)
        theme_card.body.addLayout(grid)
        root.addWidget(theme_card)

        # ---- 插件（列表已移到独立的「插件」页，这里只留摘要）
        self.plugin_card = Card("已安装插件", "完整管理（安装 / 卸载 / 更新 / 启用停用 / 体检）见左侧「插件」页")
        self.plugin_summary = QLabel("读取中…")
        self.plugin_summary.setWordWrap(True)
        self.plugin_summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.plugin_summary.setStyleSheet(
            f"color: {self.theme.text_dim}; background: transparent; font-size: 12.5px;"
        )
        self.plugin_card.body.addWidget(self.plugin_summary)
        root.addWidget(self.plugin_card)

        # ---- 关于
        about = Card("关于")
        info = QLabel(
            f"<b>DSH 控制台</b> v{__version__}<br>"
            f"Python + PySide6 桌面控制台，用于管理 DSH Harness。<br><br>"
            f"服务单元：<code>dsh-web.service</code>（systemd user）<br>"
            f"配置目录：<code>~/.dsh</code><br>"
            f"启动 wrapper：<code>~/.local/bin/dsh-web</code><br>"
            f"打开脚本：<code>~/.local/bin/dsh-open</code>"
        )
        info.setWordWrap(True)
        info.setTextFormat(Qt.RichText)
        info.setStyleSheet(f"color: {self.theme.text_dim}; background: transparent;")
        about.body.addWidget(info)
        root.addWidget(about)
        root.addStretch(1)

    def load_plugins(self) -> None:
        from .. import plugin_manager

        run_async(plugin_manager.installed, self._on_plugins, None)

    def _on_plugins(self, plugins: list) -> None:
        if not plugins:
            self.plugin_summary.setText("（没有已安装的插件）")
            return
        active = sum(1 for p in plugins if p.is_effective)
        disabled = sum(1 for p in plugins if p.in_bundles and p.disabled)
        parts = [f"共 {len(plugins)} 个：{active} 个生效"]
        if disabled:
            parts.append(f"{disabled} 个已停用")
        names = "、".join(p.name for p in plugins)
        self.plugin_summary.setText("　·　".join(parts) + f"\n{names}")

    def set_active_theme(self, key: str) -> None:
        for sw in self._swatches:
            sw.set_active(sw.theme_obj.key == key)

    def apply_theme(self, theme: Theme) -> None:
        self.theme = theme
        self.set_active_theme(theme.key)
