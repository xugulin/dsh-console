"""日志页：journalctl 查看器。"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import service
from ..themes import Theme
from .components import ScrollPage
from ..workers import run_async


class LogsPage(ScrollPage):
    """带过滤与自动跟随的日志视图。"""

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme = theme
        self._build()

    def activate(self) -> None:
        self.reload()
        if self.follow.isChecked():
            self._timer.start()

    def deactivate(self) -> None:
        """切走时停掉轮询。

        ``MainWindow._goto`` 会对上一个页面调 ``deactivate()``——这个类以前没有它，
        于是勾着「自动刷新」切到别的页面后，journalctl 仍然每 2 秒 fork 一次，
        README 里"切走即停"的懒加载约定在这页是失效的。
        """
        self._timer.stop()

    def _build(self) -> None:
        # 用 ScrollPage 的 body：装不下时**滚动**而不是把内容裁掉
        root = self.body
        root.setSpacing(14)


        bar = QHBoxLayout()
        bar.setSpacing(10)
        bar.addWidget(QLabel("条数"))
        self.count_box = QComboBox()
        for n in (100, 300, 1000, 3000):
            self.count_box.addItem(str(n), n)
        self.count_box.setCurrentIndex(1)
        self.count_box.setFixedWidth(100)
        bar.addWidget(self.count_box)

        bar.addWidget(QLabel("级别"))
        self.level_box = QComboBox()
        self.level_box.addItem("全部", None)
        self.level_box.addItem("警告以上", "warning")
        self.level_box.addItem("仅错误", "err")
        self.level_box.setFixedWidth(120)
        bar.addWidget(self.level_box)

        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("按关键字过滤（本地匹配）…")
        self.filter_edit.returnPressed.connect(self.reload)
        bar.addWidget(self.filter_edit, 1)

        self.follow = QCheckBox("自动刷新")
        bar.addWidget(self.follow)
        self.btn = QPushButton("刷新")
        self.btn.setObjectName("Primary")
        self.btn.clicked.connect(self.reload)
        bar.addWidget(self.btn)
        root.addLayout(bar)

        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setLineWrapMode(QPlainTextEdit.NoWrap)
        root.addWidget(self.view, 1)

        # 定时器只建一次。以前建在 activate() 里，每进一次这一页就多一个 QTimer：
        # 旧的那个 self._timer 已经指向新对象，_on_follow 再也停不掉它，它也不会被销毁，
        # 于是在别的页面上一直空转。顺带修掉一个崩点——toggled 在 _build 里就接上了，
        # 而 _timer 直到 activate() 才存在，先勾选框就会 AttributeError。
        self._timer = QTimer(self)
        self._timer.setInterval(2000)
        self._timer.timeout.connect(self._maybe_reload)

        self.follow.toggled.connect(self._on_follow)
        self.count_box.currentIndexChanged.connect(self.reload)
        self.level_box.currentIndexChanged.connect(self.reload)

    def _on_follow(self, on: bool) -> None:
        if on:
            self._timer.start()
        else:
            self._timer.stop()

    def _maybe_reload(self) -> None:
        if self.follow.isChecked():
            self.reload()

    def reload(self) -> None:
        lines = self.count_box.currentData() or 300
        level = self.level_box.currentData()
        run_async(service.logs, self._on_text, self._on_error, service.UNIT, lines, level)

    def _on_text(self, text: str) -> None:
        needle = self.filter_edit.text().strip()
        if needle:
            kept = [ln for ln in text.splitlines() if needle in ln]
            text = "\n".join(kept)
            if not kept:
                text = f"（没有匹配 “{needle}” 的行）"
        if self.view.hasFocus() and self.view.textCursor().hasSelection():
            return  # 用户正在选中复制，别覆盖
        self.view.setPlainText(text.rstrip() or "（无日志）")
        sb = self.view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_error(self, msg: str) -> None:
        self.view.setPlainText(f"[读取日志失败] {msg}")

    def apply_theme(self, theme: Theme) -> None:
        self.theme = theme
