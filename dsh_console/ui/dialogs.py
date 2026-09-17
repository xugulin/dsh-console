"""插件详情对话框（已安装插件 / 市场插件共用一套视觉）。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ..screenfit import apply_screen_fit
from .. import market, plugin_manager
from ..themes import Theme
from .components import FixedTable


def _section(title: str, theme: Theme) -> QLabel:
    lab = QLabel(title)
    lab.setStyleSheet(
        f"color: {theme.text}; background: transparent; font-weight: 700;"
        f" font-size: 13.5px; padding-top: 6px;"
    )
    return lab


def _body(text: str, theme: Theme, color: str | None = None) -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
    lab.setStyleSheet(
        f"color: {color or theme.text_dim}; background: transparent; font-size: 12.5px;"
    )
    return lab


class InstalledPluginDialog(QDialog):
    """已安装插件的详情。"""

    def __init__(self, plugin: plugin_manager.InstalledPlugin, theme: Theme,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"插件详情 — {plugin.name}")
        apply_screen_fit(self, (620, 520), (520, 420))
        self.theme = theme

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(10)

        head = QLabel(plugin.name)
        head.setStyleSheet(
            f"color: {theme.text}; background: transparent; font-size: 19px; font-weight: 700;"
        )
        root.addWidget(head)
        root.addWidget(_body(plugin.description or "（无描述）", theme))

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(7)
        rows = [
            ("版本", plugin.version or "—"),
            ("来源", f"{plugin.source}　({plugin.spec})"),
            ("状态", plugin.status_text),
            ("装配行 id", "、".join(plugin.row_ids) or "—（无法停用）"),
            ("许可证", plugin.license or "—"),
            ("体积", f"{plugin.unpacked_mb} MB" if plugin.unpacked_mb else "—"),
            ("安装期脚本", "有 ⚠️ 装包时会执行代码" if plugin.has_install_scripts else "无 ✓"),
            ("安装位置", str(plugin_manager.NODE_MODULES / plugin.name)),
        ]
        for k, v in rows:
            kp = QLabel(k)
            kp.setStyleSheet(f"color: {theme.text_faint}; background: transparent;")
            vp = _body(str(v), theme, theme.text)
            form.addRow(kp, vp)
        root.addLayout(form)

        if plugin.dependencies:
            root.addWidget(_section("依赖", theme))
            root.addWidget(_body("、".join(sorted(plugin.dependencies)), theme))

        if plugin.repository:
            root.addWidget(_section("源码仓库", theme))
            root.addWidget(_body(plugin.repository, theme, theme.accent))

        hint = _body(
            "启用/停用写的是 patch 层（cordis.patch.yml），宿主会热重载，**无需重启**；\n"
            "卸载与更新会改动 package.json 的插件装配列表，**必须重启** harness 才生效。",
            theme, theme.text_faint,
        )
        root.addStretch(1)
        root.addWidget(hint)

        box = QDialogButtonBox(QDialogButtonBox.Close)
        box.rejected.connect(self.reject)
        box.accepted.connect(self.accept)
        root.addWidget(box)


class MarketPluginDialog(QDialog):
    """市场插件的详情：说明、评分、安全体检、README。"""

    def __init__(self, plugin: market.MarketPlugin, theme: Theme,
                 installed_version: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"市场详情 — {plugin.name}")
        apply_screen_fit(self, (760, 640), (560, 460))
        self.theme = theme
        self.plugin = plugin

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(9)

        head = QLabel(plugin.name)
        head.setStyleSheet(
            f"color: {theme.text}; background: transparent; font-size: 19px; font-weight: 700;"
        )
        root.addWidget(head)
        root.addWidget(_body(plugin.description or "（无描述）", theme))

        # 关键指标
        stats = QHBoxLayout()
        stats.setSpacing(26)
        for label, value in (
            ("最新版本", plugin.version or "—"),
            ("周下载", f"{plugin.weekly_downloads:,}"),
            ("评分", f"{plugin.rating_text} / 5"),
            ("最近发布", plugin.freshness),
        ):
            box = QVBoxLayout()
            box.setSpacing(2)
            v = QLabel(str(value))
            v.setStyleSheet(
                f"color: {theme.accent}; background: transparent;"
                f" font-size: 16px; font-weight: 700;"
            )
            k = QLabel(label)
            k.setStyleSheet(f"color: {theme.text_faint}; background: transparent; font-size: 11.5px;")
            box.addWidget(v)
            box.addWidget(k)
            stats.addLayout(box)
        stats.addStretch(1)
        root.addLayout(stats)

        # 评分细分
        root.addWidget(_section("评分构成（控制台自算，全部依据可观测信号）", theme))
        caps = {"使用量": 2.2, "活跃度": 1.4, "生态": 0.7, "规范": 0.7}
        parts = plugin.rating_parts
        detail = "　·　".join(
            f"{k} {v:.2f}/{caps[k]}" for k, v in parts.items()
        )
        root.addWidget(_body(f"合计 {plugin.rating:.2f} / 5　　{detail}", theme))
        root.addWidget(
            _body(
                "使用量按周下载量取对数刻度；活跃度看最近发布时间；生态看被依赖数；"
                "规范看是否声明许可证与源码仓库。",
                theme, theme.text_faint,
            )
        )

        if installed_version:
            same = installed_version == plugin.version
            root.addWidget(
                _body(
                    f"本机状态：已安装 v{installed_version}"
                    + ("　已是最新 ✓" if same else f"　可更新到 v{plugin.version} ⬆"),
                    theme,
                    theme.ok if same else theme.warn,
                )
            )
        else:
            root.addWidget(_body("本机状态：未安装", theme, theme.text_faint))

        # 体检结果异步补进来
        self.audit_box = QVBoxLayout()
        self.audit_box.setSpacing(4)
        root.addWidget(_section("安装前体检", theme))
        self.audit_label = _body("正在读取包元信息…", theme, theme.text_faint)
        root.addWidget(self.audit_label)
        root.addLayout(self.audit_box)

        # README
        root.addWidget(_section("说明文档（README）", theme))
        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(True)
        self.browser.setStyleSheet(
            f"QTextBrowser {{ background: {theme.surface}; color: {theme.text};"
            f" border: 1px solid {theme.border}; border-radius: 10px; padding: 10px; }}"
        )
        root.addWidget(self.browser, 1)

        box = QDialogButtonBox(QDialogButtonBox.Close)
        box.rejected.connect(self.reject)
        root.addWidget(box)

    def set_audit(self, det: market.MarketDetail) -> None:
        """把后台取回的详情填进体检区。"""
        theme = self.theme
        if det.error:
            self.audit_label.setText(f"体检失败：{det.error}")
            return
        lines = [f"依赖 {len(det.dependencies)} 个　·　版本迭代 {det.version_count} 次　·　包体 {det.unpacked_mb} MB"]
        lines.append(
            f"类型：{'含宿主半' if det.has_bundle else '无宿主半'}"
            f"　{'含前端半' if det.has_client else '无前端半'}"
        )
        self.audit_label.setText("\n".join(lines))

        for risk in det.risks:
            lab = _body(f"⚠️  {risk}", theme, theme.warn)
            self.audit_box.addWidget(lab)
        for good in det.good_signs:
            lab = _body(f"✓  {good}", theme, theme.ok)
            self.audit_box.addWidget(lab)

        if det.readme:
            body = market.sanitize_readme(det.readme)
            if len(body) > 60_000:
                body = body[:60_000] + "\n\n…（README 过长已截断）"
            self.browser.setMarkdown(body)
        else:
            self.browser.setPlainText("（npm 上没有提供 README）")


class UpdateCheckDialog(QDialog):
    """已安装插件的**批量更新检查与升级**。

    打开即并发查询每个插件的 npm 最新版（并发而非逐个——实测 12 个包从 38 秒降到几秒），
    有更新的行默认勾选，可"更新选中"一次性升级。

    升级用 ``add <pkg>@latest``：``update`` 只在 semver 范围内升，锁在 ``^1.0.6``
    就永远上不了 2.x。
    """

    restart_requested = Signal()
    #: 工作线程 → 主线程的进度回传（Qt 信号跨线程是队列投递，安全）
    _progress = Signal(int, int, str)

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("检查插件更新")
        apply_screen_fit(self, (700, 480), (520, 380))
        self.theme = theme
        self._rows: list[tuple[str, str, str]] = []  # (name, current, latest)

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(10)

        self.head = QLabel("正在并发查询最新版本…")
        self.head.setStyleSheet(
            f"color: {theme.text}; background: transparent; font-size: 14px; font-weight: 600;"
        )
        root.addWidget(self.head)
        root.addWidget(_body(
            "只比较来源为 npm 的插件；GitHub 源与本地目录源的插件没有可比的版本号。",
            theme, theme.text_faint,
        ))

        self.bar = QProgressBar()
        self.bar.setRange(0, 0)  # 不确定态
        self.bar.setTextVisible(False)
        root.addWidget(self.bar)

        self.table = FixedTable(
            ["更新", "插件", "当前版本", "最新版本", "状态"], visible_rows=8
        )
        self.table.set_column_stretch(1)
        root.addWidget(self.table)

        self.status = _body("", theme, theme.text_dim)
        root.addWidget(self.status)

        buttons = QHBoxLayout()
        self.btn_all = QPushButton("全选可更新")
        self.btn_none = QPushButton("全不选")
        self.btn_apply = QPushButton("更新选中")
        self.btn_apply.setObjectName("Primary")
        self.btn_close = QPushButton("关闭")
        for b in (self.btn_all, self.btn_none, self.btn_apply):
            buttons.addWidget(b)
        buttons.addStretch(1)
        buttons.addWidget(self.btn_close)
        root.addLayout(buttons)

        self.btn_all.clicked.connect(lambda: self._set_all(True))
        self.btn_none.clicked.connect(lambda: self._set_all(False))
        self.btn_apply.clicked.connect(self._apply)
        self.btn_close.clicked.connect(self.reject)
        self._progress.connect(self._on_progress)
        for b in (self.btn_all, self.btn_none, self.btn_apply):
            b.setEnabled(False)

        self._start_check()

    # ------------------------------------------------------------ 检查
    def _start_check(self) -> None:
        from ..workers import run_async

        def job() -> dict[str, str]:
            plugins = [p for p in plugin_manager.installed() if p.source == "npm"]
            return market.latest_versions([p.name for p in plugins])

        def done(latest: dict[str, str]) -> None:
            self.bar.setRange(0, 1)
            self.bar.setValue(1)
            plugins = [p for p in plugin_manager.installed() if p.source == "npm"]
            rows = []
            self._rows = []
            updatable = 0
            for p in plugins:
                new = latest.get(p.name, "")
                if not new:
                    state = "查询失败"
                elif new == p.version:
                    state = "已是最新"
                else:
                    state = "可更新"
                    updatable += 1
                self._rows.append((p.name, p.version, new or "—"))
                rows.append(["", p.name, p.version or "—", new or "—", state])
            # 复选框交给模型：可更新的默认勾上。
            # 顺序不能反——enable_checks 靠读第 4 列的文本判断该勾哪些，而文本要先 fill
            # 进模型；先 enable_checks 的话 cell_text() 全是空串，一行都不会被预勾上，
            # 提示语写着「可更新的默认勾上」却一个都没勾。
            self.table.fill(rows, align_center={2, 3, 4})
            self.table.enable_checks(
                0, [self.table.cell_text(r, 4) == "可更新" for r in range(len(rows))]
            )
            self.head.setText(
                f"{len(plugins)} 个 npm 插件，{updatable} 个可更新" if updatable
                else f"{len(plugins)} 个 npm 插件，全部已是最新"
            )
            self.status.setText("勾选要升级的插件，然后点「更新选中」。")
            for b in (self.btn_all, self.btn_none, self.btn_apply):
                b.setEnabled(bool(updatable))

        def fail(msg: str) -> None:
            self.bar.setRange(0, 1)
            self.bar.setValue(0)
            self.head.setText("检查失败")
            self.status.setText(msg)
            self.status.setStyleSheet(
                f"color: {self.theme.danger}; background: transparent; font-size: 12.5px;"
            )

        run_async(job, done, fail)

    # ------------------------------------------------------------ 勾选
    def _set_all(self, checked: bool) -> None:
        for r in range(len(self._rows)):
            if self.table.cell_text(r, 4) == "可更新":
                self.table.set_check(r, checked)

    def _selected(self) -> list[str]:
        return [
            name
            for r, (name, _, _) in enumerate(self._rows)
            if self.table.cell_text(r, 4) == "可更新" and self.table.check(r)
        ]

    # ------------------------------------------------------------ 更新
    def _on_progress(self, i: int, total: int, name: str) -> None:
        self.bar.setRange(0, max(1, total))
        self.bar.setValue(i)
        self.status.setText(f"正在更新第 {i}/{total} 个：{name}")

    def _apply(self) -> None:
        names = self._selected()
        if not names:
            self.status.setText("没有勾选任何可更新的插件。")
            return
        preview = "\n".join(f"· {n}" for n in names[:12])
        if len(names) > 12:
            preview += f"\n… 以及另外 {len(names) - 12} 个"
        ans = QMessageBox.question(
            self,
            "确认更新",
            f"将把以下 {len(names)} 个插件升级到最新版：\n\n{preview}\n\n"
            "逐个串行执行（pnpm 需要独占 node_modules），可能需要一会儿。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if ans != QMessageBox.Yes:
            return

        from ..workers import run_async

        self.bar.setRange(0, len(names))
        self.bar.setValue(0)
        for b in (self.btn_all, self.btn_none, self.btn_apply):
            b.setEnabled(False)
        self.head.setText(f"正在更新 {len(names)} 个插件…")

        def job() -> tuple[list[str], list[tuple[str, str]]]:
            return plugin_manager.update_to_latest(
                names, on_progress=lambda i, t, n: self._progress.emit(i, t, n)
            )

        def done(result: tuple[list[str], list[tuple[str, str]]]) -> None:
            ok, failed = result
            self.bar.setValue(self.bar.maximum())
            if failed:
                self.head.setText(f"完成：{len(ok)} 个成功，{len(failed)} 个失败")
                self.status.setText(
                    "失败：" + "；".join(f"{n}（{e[:70]}）" for n, e in failed)
                )
                self.status.setStyleSheet(
                    f"color: {self.theme.warn}; background: transparent; font-size: 12.5px;"
                )
            else:
                self.head.setText(f"全部 {len(ok)} 个插件已更新完成")
                self.status.setText("需要重启 harness 才会生效。")
            self.btn_close.setText("关闭")
            self.restart_requested.emit()

        def fail(msg: str) -> None:
            self.head.setText("更新失败")
            self.status.setText(msg)

        run_async(job, done, fail)


class InstallDialog(QDialog):
    """按包名安装。"""

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("安装插件")
        self.setMinimumWidth(520)
        from PySide6.QtWidgets import QLineEdit

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(10)
        root.addWidget(_body(
            "支持三种写法：\n"
            "　· npm 包名：dsh-cost-meter\n"
            "　· 指定版本：dsh-cost-meter@1.7.23\n"
            "　· GitHub 源：github:user/repo",
            theme,
        ))
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("包名 / 包名@版本 / github:user/repo")
        root.addWidget(self.edit)
        self.note = _body("安装后会改动插件装配列表，需要重启 harness 才生效。", theme, theme.text_faint)
        root.addWidget(self.note)

        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.button(QDialogButtonBox.Ok).setText("安装")
        box.button(QDialogButtonBox.Cancel).setText("取消")
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        root.addWidget(box)

    @property
    def spec(self) -> str:
        return self.edit.text().strip()
