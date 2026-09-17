"""技能页：管理**已安装**的技能。

与「技能市场」（装新技能）分工不同，本页面对付的是已经躺在磁盘上的技能：
查看、启用/停用、读正文、看调用统计。

技能散落在四个根目录里（见 :mod:`dsh_console.skills`），本页把它们汇总到一张表；
只有用户级根（``~/.dsh/skills``、``~/.agents/skills``）可写，项目/内置根只读展示。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .. import skills
from ..themes import Theme
from ..workers import run_async
from .components import Card, FixedTable, Metric, ScrollPage


class SkillBodyDialog(QDialog):
    """查看技能正文。"""

    def __init__(self, skill: skills.Skill, body: str, theme: Theme,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"技能正文 — {skill.name}")
        self.setMinimumSize(720, 600)
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(10)

        head = QLabel(skill.name)
        head.setStyleSheet(
            f"color: {theme.text}; background: transparent; font-size: 19px; font-weight: 700;"
        )
        root.addWidget(head)

        meta = QLabel(
            f"{skill.description or '（无描述）'}\n"
            f"来源：{skill.root_label}　·　形态：{skill.kind}　·　"
            f"状态：{'已启用' if skill.enabled else '已停用'}　·　大小：{skill.size_text}\n"
            f"路径：{skill.path}"
            + (f"\n标签：{'、'.join(skill.sets)}" if skill.sets else "")
        )
        meta.setWordWrap(True)
        meta.setTextInteractionFlags(Qt.TextSelectableByMouse)
        meta.setStyleSheet(
            f"color: {theme.text_dim}; background: transparent; font-size: 12.5px;"
        )
        root.addWidget(meta)

        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setStyleSheet(
            f"QTextBrowser {{ background: {theme.surface}; color: {theme.text};"
            f" border: 1px solid {theme.border}; border-radius: 10px; padding: 10px; }}"
        )
        browser.setMarkdown(body or "（正文为空）")
        root.addWidget(browser, 1)

        box = QDialogButtonBox(QDialogButtonBox.Close)
        box.rejected.connect(self.reject)
        root.addWidget(box)


class InstalledSkillsPage(ScrollPage):
    """已安装技能管理。"""

    changed = Signal()
    # 技能状态变化（通知其它页面）

    def __init__(self, theme: Theme, parent: QWidget | None = None,
                 *, embedded: bool = False) -> None:
        super().__init__(parent)
        self._embedded = embedded
        self.theme = theme
        self._skills: list[skills.Skill] = []
        self._busy = False
        self._build()

    # ------------------------------------------------------------------ UI
    def _build(self) -> None:
        # 用 ScrollPage 的 body：装不下时**滚动**而不是把内容裁掉
        root = self.body
        root.setSpacing(14)

        head = QHBoxLayout()
        head.addStretch(1)
        self.head = head          # 容器要把「插件 / 技能」分段按钮插进来

        self.btn_market_hint = QPushButton("去技能市场装新的")
        self.btn_market_hint.setObjectName("Ghost")
        self.btn_refresh = QPushButton("刷新")
        self.btn_refresh.setObjectName("Primary")
        head.addWidget(self.btn_market_hint)
        head.addWidget(self.btn_refresh)
        root.addLayout(head)

        metrics = Card("概览")
        row = QHBoxLayout()
        row.setSpacing(38)
        self.m_total = Metric("技能总数")
        self.m_enabled = Metric("已启用", accent=True)
        self.m_disabled = Metric("已停用")
        self.m_writable = Metric("可管理")
        self.m_calls = Metric("累计调用")
        for m in (self.m_total, self.m_enabled, self.m_disabled, self.m_writable, self.m_calls):
            row.addWidget(m)
        row.addStretch(1)
        metrics.body.addLayout(row)
        self.roots_label = QLabel("")
        self.roots_label.setObjectName("CardHint")
        self.roots_label.setWordWrap(True)
        metrics.body.addWidget(self.roots_label)
        root.addWidget(metrics)

        card = Card("技能列表", "选中一行后可用下方按钮操作；项目/内置来源的技能为只读")
        self.table = FixedTable(
            ["技能", "来源", "状态", "调用", "最近使用", "大小", "描述"], visible_rows=9
        )
        self.table.set_column_stretch(6)
        self.table.setToolTip(
            "「停用」把 SKILL.md 重命名为 SKILL.md.disabled，并把变更同步进技能中枢的\n"
            "sidecar（~/.dsh/dsh-skill-hub.json 的 disabled 数组），两边状态一致。"
        )
        card.body.addWidget(self.table)
        root.addWidget(card)

        buttons = QHBoxLayout()
        buttons.setSpacing(9)
        self.btn_body = QPushButton("查看正文")
        self.btn_toggle = QPushButton("启用 / 停用")
        self.btn_open = QPushButton("打开所在目录")
        self.btn_copy = QPushButton("复制路径")
        for b in (self.btn_body, self.btn_toggle, self.btn_open, self.btn_copy):
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
        self.table.doubleClicked.connect(self._show_body)
        self.btn_refresh.clicked.connect(self.refresh)
        self.btn_body.clicked.connect(self._show_body)
        self.btn_toggle.clicked.connect(self._toggle)
        self.btn_open.clicked.connect(self._open_dir)
        self.btn_copy.clicked.connect(self._copy_path)

    def activate(self) -> None:
        self.refresh()

    # -------------------------------------------------------------- 数据
    def refresh(self) -> None:
        if self._busy:
            return
        run_async(skills.scan, self._on_skills, self._on_error)

    def _on_skills(self, items: list[skills.Skill]) -> None:
        self._skills = items
        rows = []
        for s in items:
            rows.append([
                s.name,
                s.root_label,
                "已启用" if s.enabled else "已停用",
                str(s.calls) if s.calls else "—",
                s.last_used_text,
                s.size_text,
                (s.description or "").replace("\n", " ")[:90],
            ])
        self.table.fill(rows, align_center={2, 3, 4, 5})

        self.m_total.set_value(str(len(items)))
        self.m_enabled.set_value(str(sum(1 for s in items if s.enabled)))
        self.m_disabled.set_value(str(sum(1 for s in items if not s.enabled)))
        self.m_writable.set_value(str(sum(1 for s in items if s.writable)))
        self.m_calls.set_value(str(sum(s.calls for s in items)))
        roots = sorted({s.root_label for s in items})
        self.roots_label.setText(
            "发现的来源根：" + ("、".join(roots) if roots else "（无 — 还没有安装任何技能）")
        )
        if not items:
            self._set_status(
                "还没有任何技能。可以去「技能市场」安装，或在 DSH 的 设置 → 技能 里从市场导入。"
            )
        self._sync_buttons()

    def _on_error(self, msg: str) -> None:
        self._set_status(f"扫描技能失败：{msg}", error=True)

    def _selected(self) -> skills.Skill | None:
        r = self.table.selected_row()
        if 0 <= r < len(self._skills):
            return self._skills[r]
        return None

    def _sync_buttons(self) -> None:
        s = self._selected()
        has = s is not None and not self._busy
        for b in (self.btn_body, self.btn_open, self.btn_copy):
            b.setEnabled(has)
        self.btn_toggle.setEnabled(bool(has and s is not None and s.writable))
        if s is not None:
            self.btn_toggle.setText("停用" if s.enabled else "启用")
            if not s.writable:
                self.btn_toggle.setToolTip("只有用户级技能（~/.dsh/skills、~/.agents/skills）可启用/停用")
            else:
                self.btn_toggle.setToolTip("")

    def _set_status(self, text: str, error: bool = False, ok: bool = False) -> None:
        color = self.theme.danger if error else (self.theme.ok if ok else self.theme.text_dim)
        self.status.setStyleSheet(
            f"color: {color}; background: transparent; font-size: 12.5px;"
        )
        self.status.setText(text)

    # -------------------------------------------------------------- 动作
    def _show_body(self) -> None:
        s = self._selected()
        if s is None:
            return
        SkillBodyDialog(s, skills.read_body(s), self.theme, self).exec()

    def _toggle(self) -> None:
        s = self._selected()
        if s is None or not s.writable:
            return
        enable = not s.enabled
        self._busy = True
        self._sync_buttons()
        self._set_status(f"{'启用' if enable else '停用'} {s.name}…")

        def job() -> str:
            return skills.set_enabled(s, enable)

        def done(msg: str) -> None:
            self._busy = False
            self._set_status(msg, ok=True)
            self.refresh()
            self.changed.emit()

        def fail(msg: str) -> None:
            self._busy = False
            self._set_status(f"操作失败：{msg}", error=True)
            self._sync_buttons()

        run_async(job, done, fail)

    def _open_dir(self) -> None:
        s = self._selected()
        if s is None:
            return
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl.fromLocalFile(str(s.path.parent)))

    def _copy_path(self) -> None:
        s = self._selected()
        if s is None:
            return
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(str(s.path))
        self._set_status("路径已复制到剪贴板", ok=True)

    def apply_theme(self, theme: Theme) -> None:
        self.theme = theme
        self.status.setStyleSheet(
            f"color: {theme.text_dim}; background: transparent; font-size: 12.5px;"
        )
