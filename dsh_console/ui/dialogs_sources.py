"""市场源设置对话框。

原先有一个独立的「数据源」页，但它离使用现场太远——改完还要切回市场页看效果。
现在拆成两个对话框，各自挂在对应的市场页上：

* :class:`PluginSourceDialog` —— 插件市场：区域 / npm 镜像 / GitHub 加速 / 目录源 / 自动发现测速
* :class:`SkillSourceDialog`  —— 技能市场：GitHub 仓库源（与技能中枢共享同一份列表）
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..screenfit import apply_screen_fit
from .. import catalog, probe, sources
from ..themes import Theme
from ..workers import run_async
from .components import Card, Pill

BUILTIN_SKILL_REPOS = (
    "anthropics/skills",
    "obra/superpowers",
    "mattpocock/skills",
    "nexu-io/open-design",
)


def _hint(text: str, theme: Theme, color: str | None = None) -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
    lab.setStyleSheet(
        f"color: {color or theme.text_faint}; background: transparent; font-size: 12px;"
    )
    return lab


def _label(text: str, theme: Theme) -> QLabel:
    lab = QLabel(text)
    lab.setStyleSheet(f"color: {theme.text_faint}; background: transparent;")
    return lab


class PluginSourceDialog(QDialog):
    """插件市场的数据源设置。"""

    applied = Signal()

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("插件市场 · 数据源设置")
        apply_screen_fit(self, (860, 640), (620, 480))
        self.theme = theme
        self.cfg = sources.load()
        self._probe_results: list = []
        self._build()
        self._reload()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(10)

        root.addWidget(_hint(
            "区域口径与已安装的 dshmarket 插件一致；环境变量（DSHM_NPM_MIRROR / "
            "DSHM_GITHUB_PROXY / DSHM_REGISTRY_URL）优先级更高。",
            self.theme,
        ))

        self.env_label = _hint("", self.theme, self.theme.warn)
        self.env_label.setVisible(False)
        root.addWidget(self.env_label)

        # ---- 区域与网络
        net = Card("区域与网络线路")
        form = QFormLayout()
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(8)
        self.region_box = QComboBox()
        for r in sources.REGIONS:
            self.region_box.addItem(f"{r.name} — {r.description}", r.key)
        self.region_box.currentIndexChanged.connect(self._on_region)
        form.addRow(_label("区域", self.theme), self.region_box)

        self.registry_edit = QLineEdit()
        self.registry_edit.setPlaceholderText("留空 = 跟随区域预设")
        form.addRow(_label("npm registry", self.theme), self.registry_edit)

        self.proxy_box = QComboBox()
        self.proxy_box.addItem("跟随区域预设", "")
        for url, label in sources.KNOWN_GITHUB_PROXIES:
            self.proxy_box.addItem(label, url)
        self.proxy_box.addItem("（不使用加速）", "__none__")
        self.proxy_box.currentIndexChanged.connect(self._on_proxy_preset)
        form.addRow(_label("GitHub 加速", self.theme), self.proxy_box)

        self.proxy_edit = QLineEdit()
        self.proxy_edit.setPlaceholderText("自定义加速前缀，如 https://gh-proxy.com")
        form.addRow(_label("自定义前缀", self.theme), self.proxy_edit)
        net.body.addLayout(form)

        self.effective_label = _hint("", self.theme)
        net.body.addWidget(self.effective_label)
        root.addWidget(net)

        # ---- 自动发现
        disc = Card(
            "自动发现与测速",
            "并发探测全部候选源，按「可达性 30 + 延迟 25 + 资源量 30 + 官方 15」加权评分排序。",
        )
        drow = QHBoxLayout()
        self.btn_probe = QPushButton("自动发现并测速")
        self.btn_probe.setObjectName("Primary")
        self.btn_best = QPushButton("采用各类最优")
        self.btn_best.setEnabled(False)
        drow.addWidget(self.btn_probe)
        drow.addWidget(self.btn_best)
        drow.addStretch(1)
        self.probe_pill = Pill("未探测")
        self.probe_pill.set_state("未探测", self.theme.text_faint, self.theme.text)
        drow.addWidget(self.probe_pill)
        disc.body.addLayout(drow)

        self.probe_table = QTableWidget(0, 6)
        self.probe_table.setHorizontalHeaderLabels(
            ["类型", "源", "延迟/速度", "资源量", "评分", "说明"]
        )
        self.probe_table.verticalHeader().setVisible(False)
        self.probe_table.setAlternatingRowColors(True)
        self.probe_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.probe_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.probe_table.setShowGrid(False)
        ph = self.probe_table.horizontalHeader()
        ph.setSectionResizeMode(1, QHeaderView.Stretch)
        for c in (0, 2, 3, 4):
            ph.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        ph.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.probe_table.setFixedHeight(230)
        disc.body.addWidget(self.probe_table)
        root.addWidget(disc)

        # ---- 目录源
        cat = Card("插件目录源", "官方目录收录 3632 个（远大于一次 npm 搜索）；可填 https 地址或 npm:包名。")
        self.catalog_view = QPlainTextEdit()
        self.catalog_view.setFixedHeight(60)
        cat.body.addWidget(self.catalog_view)
        crow = QHBoxLayout()
        self.btn_cat_fetch = QPushButton("拉取 / 刷新目录")
        self.cat_pill = Pill("未拉取")
        self.cat_pill.set_state("未拉取", self.theme.text_faint, self.theme.text)
        crow.addWidget(self.btn_cat_fetch)
        crow.addWidget(self.cat_pill)
        crow.addStretch(1)
        self.cat_info = _hint("", self.theme)
        crow.addWidget(self.cat_info)
        cat.body.addLayout(crow)
        root.addWidget(cat)

        self.status = _hint("", self.theme)
        root.addWidget(self.status)

        box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Close)
        box.button(QDialogButtonBox.Save).setText("保存并应用")
        box.button(QDialogButtonBox.Close).setText("关闭")
        box.accepted.connect(self._save)
        box.rejected.connect(self.reject)
        root.addWidget(box)

        self.btn_probe.clicked.connect(self._probe)
        self.btn_best.clicked.connect(self._apply_best)
        self.btn_cat_fetch.clicked.connect(self._fetch_catalog)

    # -------------------------------------------------------------- 载入
    def _reload(self) -> None:
        self.cfg = sources.load()
        self.region_box.setCurrentIndex(max(0, self.region_box.findData(self.cfg.region)))
        self.registry_edit.setText(self.cfg.npm_registry)
        self.proxy_edit.setText(self.cfg.github_proxy)
        idx = self.proxy_box.findData(self.cfg.github_proxy)
        self.proxy_box.setCurrentIndex(idx if idx >= 0 else 0)
        self.catalog_view.setPlainText("\n".join(self.cfg.catalog_urls))
        self._refresh_effective()
        managed = sources.env_managed()
        if managed:
            self.env_label.setVisible(True)
            self.env_label.setText(
                "⚠️ 以下项由环境变量托管（改这里不会生效）："
                + "　".join(f"{k} = {v}" for k, v in managed.items())
            )

    def _refresh_effective(self) -> None:
        reg, reg_src = self.cfg.effective_registry()
        proxy, proxy_src = self.cfg.effective_proxy()
        cat, cat_src = self.cfg.effective_catalog()
        self.effective_label.setText(
            f"当前生效：npm → <b>{reg}</b>（{reg_src}）　·　"
            f"GitHub 加速 → <b>{proxy or '不使用'}</b>（{proxy_src}）　·　"
            f"目录源 {len(cat)} 个（{cat_src}）"
        )

    def _on_region(self) -> None:
        preset = sources.REGION_BY_KEY.get(self.region_box.currentData())
        if preset is None:
            return
        self.registry_edit.setText("")
        self.proxy_edit.setText(preset.github_proxy)
        idx = self.proxy_box.findData(preset.github_proxy)
        self.proxy_box.setCurrentIndex(idx if idx >= 0 else 0)

    def _on_proxy_preset(self) -> None:
        val = self.proxy_box.currentData()
        if val == "__none__":
            self.proxy_edit.setText("")
        elif val:
            self.proxy_edit.setText(val)

    # -------------------------------------------------------------- 动作
    def _save(self) -> None:
        cfg = sources.SourcesConfig(
            region=self.region_box.currentData() or "global",
            npm_registry=self.registry_edit.text().strip(),
            github_proxy=self.proxy_edit.text().strip(),
            catalog_urls=[
                l.strip() for l in self.catalog_view.toPlainText().splitlines() if l.strip()
            ] or [sources.CATALOG_OFFICIAL],
            skill_sources=[s.repo for s in sources.read_skill_sources()],
        )
        dropped = []
        if cfg.npm_registry and not sources._normalize_prefix(cfg.npm_registry):
            dropped.append("npm registry")
            cfg.npm_registry = ""
        if cfg.github_proxy and not sources._normalize_prefix(cfg.github_proxy):
            dropped.append("GitHub 加速前缀")
            cfg.github_proxy = ""
        try:
            sources.save(cfg)
        except Exception as exc:
            self.status.setText(f"保存失败：{exc}")
            return
        self.cfg = cfg
        self._refresh_effective()
        self.applied.emit()
        note = "已保存，市场下次拉取即生效"
        if dropped:
            note += f"（{'、'.join(dropped)} 非法 https 前缀，已忽略）"
        self.status.setText(note)

    def _probe(self) -> None:
        self.btn_probe.setEnabled(False)
        self.probe_pill.set_state("探测中…", self.theme.warn, self.theme.text)
        self.status.setText("正在并发探测全部候选源…")

        def done(results: list) -> None:
            self._probe_results = results
            self.btn_probe.setEnabled(True)
            self.btn_best.setEnabled(bool(probe.best(results)))
            reachable = sum(1 for r in results if r.ok)
            self.probe_pill.set_state(
                f"{reachable}/{len(results)} 可达",
                self.theme.ok if reachable else self.theme.danger,
                self.theme.text,
            )
            kinds = {"npm": "npm", "proxy": "GitHub 加速", "catalog": "目录源"}
            self.probe_table.setRowCount(len(results))
            for ri, r in enumerate(results):
                cells = [
                    kinds.get(r.kind, r.kind), r.value or "（直连）", r.speed_text,
                    r.volume_text, f"{r.score:.0f}", r.label if r.ok else (r.note or "不可达"),
                ]
                for ci, text in enumerate(cells):
                    item = QTableWidgetItem(text)
                    if ci in (2, 3, 4):
                        item.setTextAlignment(Qt.AlignCenter)
                    self.probe_table.setItem(ri, ci, item)
            self.status.setText(f"探测完成：{reachable}/{len(results)} 可达")

        def fail(msg: str) -> None:
            self.btn_probe.setEnabled(True)
            self.probe_pill.set_state("失败", self.theme.danger, self.theme.text)
            self.status.setText(f"探测失败：{msg}")

        run_async(probe.discover_all, done, fail)

    def _apply_best(self) -> None:
        best = probe.best(self._probe_results)
        if not best:
            self.status.setText("还没有可用的探测结果")
            return
        picked = []
        if "npm" in best:
            self.registry_edit.setText(best["npm"].value)
            self.proxy_box.setCurrentIndex(0)
            picked.append(f"npm → {best['npm'].label}")
        if "proxy" in best:
            self.proxy_edit.setText(best["proxy"].value)
            picked.append(f"GitHub 加速 → {best['proxy'].label or '直连'}")
        if "catalog" in best:
            self.catalog_view.setPlainText(best["catalog"].value)
            picked.append(f"目录源 → {best['catalog'].label}")
        self._save()
        self.status.setText("已采用：" + "　".join(picked))

    def _fetch_catalog(self) -> None:
        self.btn_cat_fetch.setEnabled(False)
        self.cat_pill.set_state("拉取中…", self.theme.warn, self.theme.text)
        self._save()
        cfg = sources.load()

        def job():
            return catalog.load(cfg=cfg, force=True)

        def done(result) -> None:
            items, meta, err = result
            self.btn_cat_fetch.setEnabled(True)
            if not items:
                self.cat_pill.set_state("失败", self.theme.danger, self.theme.text)
                self.cat_info.setText(err)
                return
            self.cat_pill.set_state("已缓存", self.theme.ok, self.theme.text)
            self.cat_info.setText(
                f"{len(items)} 个 / {len(meta.get('categories') or {})} 分类"
                f"　更新于 {meta.get('updated') or '—'}　缓存 {catalog.cached_age_text()}"
            )

        run_async(job, done, lambda m: (self.btn_cat_fetch.setEnabled(True),
                                        self.status.setText(f"目录拉取失败：{m}")))


class SkillSourceDialog(QDialog):
    """技能市场的仓库源设置。"""

    applied = Signal()

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("技能市场 · 市场源设置")
        apply_screen_fit(self, (680, 520), (540, 420))
        self.theme = theme
        self._sources = sources.read_skill_sources()
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(10)

        root.addWidget(_hint(
            "与技能中枢共享同一份列表（读写 ~/.dsh/dsh-skill-hub.json 的 marketSources），"
            "所以在控制台加/删源，DSH 的「设置 → 技能 → 市场」里也会同步变化。",
            self.theme,
        ))

        card = Card("GitHub 仓库源")
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["仓库", "固定版本 / 分支", "星标"])
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.setFixedHeight(180)
        card.body.addWidget(self.table)

        row = QHBoxLayout()
        self.new_edit = QLineEdit()
        self.new_edit.setPlaceholderText("owner/repo 或 owner/repo@v1.2.3")
        btn_add = QPushButton("添加")
        btn_del = QPushButton("删除选中")
        btn_del.setObjectName("Danger")
        row.addWidget(self.new_edit, 1)
        row.addWidget(btn_add)
        row.addWidget(btn_del)
        card.body.addLayout(row)

        known = QHBoxLayout()
        known.addWidget(_label("内置精选：", self.theme))
        for repo in BUILTIN_SKILL_REPOS:
            b = QPushButton(f"+ {repo}")
            b.setObjectName("Ghost")
            b.clicked.connect(lambda _, r=repo: self._add(r))
            known.addWidget(b)
        known.addStretch(1)
        card.body.addLayout(known)
        root.addWidget(card)

        self.status = _hint("", self.theme)
        root.addWidget(self.status)
        root.addStretch(1)

        box = QDialogButtonBox(QDialogButtonBox.Close)
        box.button(QDialogButtonBox.Close).setText("关闭")
        box.rejected.connect(self.reject)
        root.addWidget(box)

        btn_add.clicked.connect(lambda: self._add(self.new_edit.text()))
        btn_del.clicked.connect(self._del)
        self._reload()

    def _reload(self) -> None:
        self._sources = sources.read_skill_sources()
        self.table.setRowCount(len(self._sources))
        for r, s in enumerate(self._sources):
            cells = [s.repo, s.ref or "（默认分支）", f"{s.stars:,}" if s.stars else "—"]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c == 2:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(r, c, item)

    def _add(self, spec: str) -> None:
        spec = (spec or "").strip()
        if not spec:
            self.status.setText("请填写 owner/repo")
            return
        repo, _, ref = spec.partition("@")
        repo = repo.strip()
        if repo.count("/") != 1:
            self.status.setText("格式应为 owner/repo 或 owner/repo@ref")
            return
        if any(s.repo == repo for s in self._sources):
            self.status.setText(f"{repo} 已在列表里")
            return
        self._sources.append(sources.SkillSource(repo=repo, ref=ref.strip()))
        self._persist(f"已添加 {repo}")

    def _del(self) -> None:
        r = self.table.currentRow()
        if not (0 <= r < len(self._sources)):
            self.status.setText("先选中一行")
            return
        s = self._sources[r]
        ans = QMessageBox.question(
            self, "确认删除",
            f"从技能市场源里移除 {s.repo}？\n\n已安装的技能文件不会被删除，"
            "只是不再从该源检查更新。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if ans != QMessageBox.Yes:
            return
        self._sources.pop(r)
        self._persist(f"已移除 {s.repo}")

    def _persist(self, note: str) -> None:
        try:
            sources.write_skill_sources(self._sources)
        except Exception as exc:
            self.status.setText(f"写入技能中枢状态失败：{exc}")
            return
        cfg = sources.load()
        cfg.skill_sources = [s.repo for s in self._sources]
        try:
            sources.save(cfg)
        except Exception:
            pass
        self._reload()
        self.applied.emit()
        self.status.setText(f"{note}（已写入 {sources.SKILL_HUB_STATE.name}，技能中枢下次扫描生效）")
