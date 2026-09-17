"""插件与技能合并页。

两块内容用的是同一套交互（一张固定高度表格 + 一排操作按钮），合到一页后用顶部的
分段按钮切换，避免侧边栏被两个近义页面占满。

内部复用 :class:`PluginsPage` 与 :class:`InstalledSkillsPage`（以 ``embedded=True``
构造，隐藏它们各自的标题块），切换时只激活当前那一半，另一半的定时器/请求不空转。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..themes import Theme
from .page_plugins import PluginsPage
from .page_skills_installed import InstalledSkillsPage


class PluginsSkillsPage(QWidget):
    """插件 / 技能 二合一。"""

    changed = Signal()

    SEGMENTS = (("插件", "已安装的插件：安装、卸载、更新、启用停用"), ("技能", "磁盘上已安装的技能：启用停用、查看正文"))

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme = theme
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(26, 18, 26, 22)
        root.setSpacing(10)

        # ---- 分段切换
        seg = QHBoxLayout()
        seg.setSpacing(0)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        for i, (label, tip) in enumerate(self.SEGMENTS):
            btn = QPushButton(label)
            btn.setObjectName("Segment")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setToolTip(tip)
            btn.setMinimumWidth(96)
            self.group.addButton(btn, i)
            seg.addWidget(btn)
        # **不再单独占一行**：这一行只有两个按钮，白占一行高度。
        # 改成由 _place_segments() 插进当前子页的按钮行最左边——
        # 于是「插件 / 技能」和「检查更新 / 安装插件」并排一行，切换器在左、操作在右。
        self._seg_buttons = [self.group.button(i) for i in range(len(self.SEGMENTS))]

        # ---- 两个子页
        self.stack = QStackedWidget()
        self.page_plugins = PluginsPage(self.theme, embedded=True)
        self.page_skills = InstalledSkillsPage(self.theme, embedded=True)
        self.stack.addWidget(self.page_plugins)
        self.stack.addWidget(self.page_skills)
        root.addWidget(self.stack, 1)

        self.group.idClicked.connect(self._switch)
        self.group.button(0).setChecked(True)
        self._place_segments(0)
        self.page_plugins.changed.connect(self.changed.emit)
        self.page_skills.changed.connect(self.changed.emit)

    def _place_segments(self, index: int) -> None:
        """把「插件 / 技能」两个按钮放到当前显示那一页的按钮行里（最左边）。

        Qt 的控件只能有一个父布局，所以切换时必须**搬过去**，不能两页各放一份。
        插入位置固定为 0：那两个子页的 head 都是 `[stretch, 操作按钮…]`，
        插到 0 号位正好是"切换器在左、操作在右"。
        """
        page = self.stack.widget(index) if self.stack.count() else None
        target = getattr(page, "head", None)
        if target is None:
            return
        for i, btn in enumerate(self._seg_buttons):
            target.insertWidget(i, btn)

    def _switch(self, index: int) -> None:
        prev = self.stack.currentIndex()
        if prev == index:
            return
        old = self.stack.widget(prev)
        stop = getattr(old, "deactivate", None)
        if callable(stop):
            stop()
        self.stack.setCurrentIndex(index)
        self._place_segments(index)
        self._activate_current()

    def _activate_current(self) -> None:
        page = self.stack.currentWidget()
        fn = getattr(page, "activate", None)
        if callable(fn):
            fn()
        else:
            for name in ("refresh", "reload"):
                f = getattr(page, name, None)
                if callable(f):
                    f()
                    return

    # 供 MainWindow / 其它页面调用
    def activate(self) -> None:
        self._activate_current()

    def refresh(self) -> None:
        """兼容旧约定（有些调用方只会找 refresh）。"""
        self._activate_current()

    def deactivate(self) -> None:
        page = self.stack.currentWidget()
        stop = getattr(page, "deactivate", None)
        if callable(stop):
            stop()

    def apply_theme(self, theme: Theme) -> None:
        self.theme = theme
        for page in (self.page_plugins, self.page_skills):
            fn = getattr(page, "apply_theme", None)
            if callable(fn):
                fn(theme)
