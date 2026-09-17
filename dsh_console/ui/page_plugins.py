"""插件页：已安装插件的完整管理。

与设置页里那张只读小表不同，这里提供**完整操作**：
安装、卸载、更新、启用/停用、详情、更新检测，以及重启 harness。

表格用 :class:`FixedTable`——行数固定，插件再多也是内部滚动，页面高度不跳。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import market, plugin_manager
from ..themes import Theme
from ..workers import run_async
from .components import Card, FixedTable, ScrollPage
from .dialogs import InstallDialog, InstalledPluginDialog, UpdateCheckDialog


class PluginsPage(ScrollPage):
    """已安装插件的管理页。"""

    changed = Signal()
    # 插件集合发生变化（供设置页等刷新）

    def __init__(self, theme: Theme, parent: QWidget | None = None,
                 *, embedded: bool = False) -> None:
        super().__init__(parent)
        self._embedded = embedded
        self.theme = theme
        self._plugins: list[plugin_manager.InstalledPlugin] = []
        self._latest: dict[str, str] = {}
        self._busy = False
        self._build()

    def activate(self) -> None:
        self.refresh()

    # ------------------------------------------------------------------ UI
    def _build(self) -> None:
        # 用 ScrollPage 的 body：装不下时**滚动**而不是把内容裁掉
        root = self.body
        root.setSpacing(14)

        head = QHBoxLayout()
        head.addStretch(1)
        self.head = head          # 容器要把「插件 / 技能」分段按钮插进来

        self.btn_check = QPushButton("检查更新")
        self.btn_install = QPushButton("安装插件…")
        self.btn_install.setObjectName("Primary")
        head.addWidget(self.btn_check)
        head.addWidget(self.btn_install)
        root.addLayout(head)

        card = Card("已安装插件", "选中一行后可用下方按钮操作")
        self.table = FixedTable(
            ["插件", "版本", "最新", "来源", "状态", "说明"], visible_rows=8
        )
        self.table.set_column_stretch(5)
        card.body.addWidget(self.table)
        root.addWidget(card)

        buttons = QHBoxLayout()
        buttons.setSpacing(9)
        self.btn_detail = QPushButton("详情")
        self.btn_toggle = QPushButton("启用 / 停用")
        self.btn_update = QPushButton("更新")
        self.btn_uninstall = QPushButton("卸载")
        self.btn_uninstall.setObjectName("Danger")
        self.btn_restart = QPushButton("重启 harness")
        self.btn_restart.setObjectName("Primary")
        for b in (self.btn_detail, self.btn_toggle, self.btn_update,
                  self.btn_uninstall, self.btn_restart):
            buttons.addWidget(b)
        buttons.addStretch(1)
        root.addLayout(buttons)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet(
            f"color: {self.theme.text_dim}; background: transparent; font-size: 12.5px;"
        )
        root.addWidget(self.status)
        root.addStretch(1)

        self.table.itemSelectionChanged.connect(self._sync_buttons)
        self.btn_install.clicked.connect(self._install)
        self.btn_detail.clicked.connect(self._detail)
        self.btn_toggle.clicked.connect(self._toggle)
        self.btn_update.clicked.connect(self._update)
        self.btn_uninstall.clicked.connect(self._uninstall)
        self.btn_restart.clicked.connect(self._restart)
        self.btn_check.clicked.connect(self._check_updates)
        self.table.doubleClicked.connect(self._detail)

    # -------------------------------------------------------------- 数据
    def refresh(self) -> None:
        run_async(plugin_manager.installed, self._on_plugins, self._on_error)

    def _on_plugins(self, plugins: list[plugin_manager.InstalledPlugin]) -> None:
        self._plugins = plugins
        rows = []
        for p in plugins:
            latest = self._latest.get(p.name, "")
            if not latest:
                up = "—"
            elif latest == p.version:
                up = "已最新"
            else:
                up = f"{latest} ⬆"
            rows.append([
                p.name,
                p.version or "—",
                up,
                p.source,
                p.status_text,
                (p.description or "")[:78],
            ])
        self.table.fill(rows, align_center={1, 2, 3, 4})
        self._sync_buttons()

    def _on_error(self, msg: str) -> None:
        self._set_status(f"读取插件失败：{msg}", error=True)

    def _selected(self) -> plugin_manager.InstalledPlugin | None:
        r = self.table.selected_row()
        if 0 <= r < len(self._plugins):
            return self._plugins[r]
        return None

    def _sync_buttons(self) -> None:
        p = self._selected()
        has = p is not None and not self._busy
        for b in (self.btn_detail, self.btn_toggle, self.btn_update, self.btn_uninstall):
            b.setEnabled(has)
        if p is not None:
            self.btn_toggle.setText("停用" if p.is_effective else "启用")
            self.btn_toggle.setEnabled(has and p.in_bundles and bool(p.row_ids))
            self.btn_uninstall.setEnabled(has and p.source != "本地目录")
        self.btn_install.setEnabled(not self._busy)
        self.btn_check.setEnabled(not self._busy)
        self.btn_restart.setEnabled(not self._busy)

    def _set_status(self, text: str, error: bool = False, ok: bool = False) -> None:
        color = self.theme.danger if error else (self.theme.ok if ok else self.theme.text_dim)
        self.status.setStyleSheet(
            f"color: {color}; background: transparent; font-size: 12.5px;"
        )
        self.status.setText(text)

    def _set_busy(self, busy: bool, what: str = "") -> None:
        self._busy = busy
        if busy:
            self._set_status(f"{what}…")
        self._sync_buttons()

    # -------------------------------------------------------------- 动作
    def _detail(self) -> None:
        p = self._selected()
        if p is None:
            return
        InstalledPluginDialog(p, self.theme, self).exec()

    def _toggle(self) -> None:
        p = self._selected()
        if p is None:
            return
        enable = not p.is_effective
        verb = "启用" if enable else "停用"
        self._set_busy(True, f"{verb} {p.name}")

        def job() -> str:
            return plugin_manager.set_enabled(p, enable)

        def done(msg: str) -> None:
            self._set_busy(False)
            self._set_status(msg, ok=True)
            self.refresh()

        def fail(msg: str) -> None:
            self._set_busy(False)
            self._set_status(f"{verb}失败：{msg}", error=True)

        run_async(job, done, fail)

    def _install(self) -> None:
        dlg = InstallDialog(self.theme, self)
        if dlg.exec() != QDialog.Accepted or not dlg.spec:
            return
        spec = dlg.spec
        self._set_busy(True, f"安装 {spec}")

        def job() -> str:
            return plugin_manager.install(spec)

        def done(msg: str) -> None:
            self._set_busy(False)
            self._set_status(msg, ok=True)
            self.refresh()
            self.changed.emit()
            self._offer_restart()

        def fail(msg: str) -> None:
            self._set_busy(False)
            self._set_status(f"安装失败：{msg}", error=True)

        run_async(job, done, fail)

    def _uninstall(self) -> None:
        p = self._selected()
        if p is None:
            return
        ans = QMessageBox.question(
            self,
            "确认卸载",
            f"确定卸载 {p.name} 吗？\n\n"
            "· 只从 profile 移除，插件自己的数据目录不会被删除\n"
            "· 卸载后需要重启 harness 才生效",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ans != QMessageBox.Yes:
            return
        self._set_busy(True, f"卸载 {p.name}")

        def job() -> str:
            return plugin_manager.uninstall(p.name)

        def done(msg: str) -> None:
            self._set_busy(False)
            self._set_status(msg, ok=True)
            self.refresh()
            self.changed.emit()
            self._offer_restart()

        def fail(msg: str) -> None:
            self._set_busy(False)
            self._set_status(f"卸载失败：{msg}", error=True)

        run_async(job, done, fail)

    def _update(self) -> None:
        p = self._selected()
        target = p.name if p else None
        label = target or "全部插件"
        self._set_busy(True, f"更新 {label}")

        def job() -> str:
            return plugin_manager.update(target)

        def done(msg: str) -> None:
            self._set_busy(False)
            self._set_status(msg, ok=True)
            self._latest.clear()
            self.refresh()
            self.changed.emit()
            self._offer_restart()

        def fail(msg: str) -> None:
            self._set_busy(False)
            self._set_status(f"更新失败：{msg}", error=True)

        run_async(job, done, fail)

    def _check_updates(self) -> None:
        """打开**批量更新检查**对话框：并发查最新版 → 勾选 → 批量升级。"""
        dlg = UpdateCheckDialog(self.theme, self)
        dlg.restart_requested.connect(self._after_batch_update)
        dlg.exec()
        self._latest.clear()
        self.refresh()

    def _after_batch_update(self) -> None:
        """批量升级后重新读本地版本，并问是否重启。"""
        self.refresh()
        self.changed.emit()
        self._offer_restart()

    def _offer_restart(self) -> None:
        ans = QMessageBox.question(
            self,
            "需要重启",
            "插件装配列表已改变，需要重启 harness 才会生效。\n\n"
            "⚠️ 重启会断开正在进行的会话。是否现在重启？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if ans == QMessageBox.Yes:
            self._restart()

    def _restart(self) -> None:
        self._set_busy(True, "重启 harness（会断开当前会话）")

        def job() -> str:
            return plugin_manager.restart_service()

        def done(msg: str) -> None:
            self._set_busy(False)
            self._set_status(msg, ok=True)

        def fail(msg: str) -> None:
            self._set_busy(False)
            self._set_status(f"重启失败：{msg}", error=True)

        run_async(job, done, fail)

    def apply_theme(self, theme: Theme) -> None:
        self.theme = theme
        self.status.setStyleSheet(
            f"color: {theme.text_dim}; background: transparent; font-size: 12.5px;"
        )
