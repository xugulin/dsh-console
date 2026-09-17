"""插件市场页：浏览、排名、评分、搜索、安装/卸载/更新。

数据来自官方 npm registry 的 ``dsh-plugin`` 关键字（实测 4900+ 个包）。
社区那几个"插件市场"本身也都是拉 npm 数据，所以直接用 npm 最稳。

排名/评分的口径刻意做得可解释：

* **排名**：默认按**周下载量**（真实使用量），可切换评分/最近更新/名称；
* **评分**：npm 的 ``quality`` / ``popularity`` / ``maintenance`` 三项（各 0..1）
  取均值换算成 5 星。**不**拿 ``searchScore`` 当评分——它量纲不明（实测 34~47），
  当星级显示会误导。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import catalog as catalog_mod
from .. import market, plugin_manager
from ..themes import Theme
from ..workers import run_async
from .components import Card, FixedTable, ScrollPage
from .dialogs import MarketPluginDialog

#: 每页拉取条数（滑到底一次加载这么多）
PAGE_SIZE = 80
#: 距底部还剩这么多行时就开始预取，让滚动不中断
PREFETCH_ROWS = 15

#: 「宿主半 / 前端半」筛选项 → (标签, 提示)
HALF_FILTERS: dict[str, tuple[str, str]] = {
    "all": ("全部", "不按形态过滤"),
    "client": ("有前端半", "带 Web GUI 界面（dsh.client 声明）"),
    "host": ("有宿主半", "带宿主装配行（dsh.bundle.patch）"),
    "client_only": ("仅前端半", "纯界面插件，不注入宿主逻辑"),
    "host_only": ("仅宿主半", "纯后台插件，没有界面"),
    "both": ("两者都有", "既有界面又注入宿主"),
}


class MarketPage(ScrollPage):
    """插件市场。

    技能市场（:class:`~dsh_console.ui.page_skills.SkillsPage`）继承本类，
    只覆写标题与搜索函数——筛选、排序、安装、体检那一整套都是复用的。
    """

    #: 子类可覆写
    PAGE_TITLE = "插件市场"
    PAGE_SUBTITLE = (
        "数据来自官方 npm registry 的 dsh-plugin 关键字。"
        "安装前请先看详情里的「安装前体检」——那会告诉你这个包在装的时候会不会执行代码。"
    )

    changed = Signal()
    # 安装/卸载后通知其他页面
    #: 工作线程 → 主线程的体检进度
    _audit_progress = Signal(int, int, str)

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme = theme
        self._items: list[market.MarketPlugin] = []
        self._sorted: list[market.MarketPlugin] = []
        self._visible: list[market.MarketPlugin] = []
        self._installed: dict[str, str] = {}
        self._busy = False
        self._auditing = False
        self._catalog_items: list = []
        self._catalog_meta: dict = {}
        self._cat_by_pkg: dict = {}
        # --- 分页状态（"滑到底继续加载"，表格高度不变）
        self._acc: list[market.MarketPlugin] = []   # 已从源累积到的全部条目
        self._view: list[market.MarketPlugin] = []  # 过滤后的视图
        self._shown = 0                             # 当前渲染多少条
        self._offset = 0                            # 已从源拉取到的原始偏移
        self._total = 0                             # 源报告的总数（判断到底）
        self._exhausted = False
        self._loading_more = False
        self._gen = 0           # 请求代号：切数据源时 +1，让在途结果作废
        self._build()
        self._audit_progress.connect(self._on_audit_progress)

    def activate(self) -> None:
        self.refresh()

    # ------------------------------------------------------------------ UI
    def _build(self) -> None:
        # 用 ScrollPage 的 body：装不下时**滚动**而不是把内容裁掉
        root = self.body
        root.setSpacing(14)

        self.btn_sources = QPushButton("市场源设置…")
        self.btn_sources.setToolTip("区域 / npm 镜像 / GitHub 加速 / 目录源，并可自动发现测速")
        self.btn_sources.clicked.connect(self._open_source_dialog)
        self.btn_refresh = QPushButton("刷新")

        card = Card("浏览")
        # 这两个按钮管的就是"浏览"这张卡（数据源 + 重新拉取），放在它的右上角，
        # 而不是飘在页面顶部——离它作用的区域近，少一次视线跳转。
        card.header.addWidget(self.btn_sources)
        card.header.addWidget(self.btn_refresh)

        bar = QHBoxLayout()
        bar.setSpacing(9)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索插件（留空=按排名列出全部）…")
        self.search_edit.returnPressed.connect(self.refresh)
        bar.addWidget(self.search_edit, 1)
        bar.addWidget(QLabel("排序"))
        self.sort_box = QComboBox()
        for key, (label, tip) in market.SORTS.items():
            self.sort_box.addItem(label, key)
            self.sort_box.setItemData(self.sort_box.count() - 1, tip, Qt.ToolTipRole)
        self.sort_box.setFixedWidth(130)
        self.sort_box.currentIndexChanged.connect(self._resort)
        bar.addWidget(self.sort_box)
        self.btn_search = QPushButton("搜索")
        self.btn_search.setObjectName("Primary")
        self.btn_search.clicked.connect(self.refresh)
        bar.addWidget(self.btn_search)

        bar.addWidget(QLabel("数据源"))
        self.source_box = QComboBox()
        self.source_box.addItem("npm 搜索（≤250 条）", "npm")
        self.source_box.addItem("官方目录（3632 个）", "catalog")
        self.source_box.setFixedWidth(180)
        self.source_box.setToolTip(
            "npm 搜索一次最多返回 250 条；官方目录收录 3632 个插件，"
            "带中文描述与分类，但需要先拉取一次（约 3MB，之后走缓存）。"
        )
        self.source_box.currentIndexChanged.connect(self._on_source_change)
        bar.addWidget(self.source_box)
        card.body.addLayout(bar)

        # ---- 第二行：筛选 + 体检
        bar2 = QHBoxLayout()
        bar2.setSpacing(9)
        self.kw_edit = QLineEdit()
        self.kw_edit.setPlaceholderText("关键字筛选（包名 / 说明 / 标签，本地匹配）…")
        # lambda 吞掉信号参数：textChanged 会把文本当第一个位置参数传进来，正好落进
        # _refill(reset=...) —— 于是清空关键字（'' 是假值）时不会重置分页窗口，
        # 表格就停在筛选后的那几行上，看起来"清不掉筛选"。currentIndexChanged 同理
        # （回到第 0 项传的是 0，也是假值）。
        self.kw_edit.textChanged.connect(lambda *_: self._refill())
        bar2.addWidget(self.kw_edit, 1)

        bar2.addWidget(QLabel("分类"))
        self.category_box = QComboBox()
        self.category_box.addItem("全部", "")
        self.category_box.setFixedWidth(130)
        self.category_box.currentIndexChanged.connect(lambda *_: self._refill())
        self.category_box.setVisible(False)
        bar2.addWidget(self.category_box)

        bar2.addWidget(QLabel("形态"))
        self.half_box = QComboBox()
        for key, (label, tip) in HALF_FILTERS.items():
            self.half_box.addItem(label, key)
            self.half_box.setItemData(self.half_box.count() - 1, tip, Qt.ToolTipRole)
        self.half_box.setFixedWidth(120)
        self.half_box.currentIndexChanged.connect(lambda *_: self._refill())
        bar2.addWidget(self.half_box)

        self.btn_audit = QPushButton("体检全部")
        self.btn_audit.setToolTip(
            "并发拉取每个包的注册表详情，才能判断「宿主半 / 前端半」。\n"
            "实测 16 并发约 0.3 秒/个（不体检时该筛选不可用）。"
        )
        self.btn_audit.clicked.connect(self._audit)
        bar2.addWidget(self.btn_audit)
        card.body.addLayout(bar2)

        self.audit_bar = QProgressBar()
        self.audit_bar.setTextVisible(False)
        self.audit_bar.setVisible(False)
        card.body.addWidget(self.audit_bar)

        self.table = FixedTable(
            ["排名", "插件", "说明", "版本", "周下载", "评分", "本机"],
            visible_rows=10,
        )
        self.table.setToolTip(
            "评分由控制台自算（0–5）：使用量按周下载取对数、活跃度看最近发布、\n"
            "生态看被依赖数、规范看是否声明许可证与仓库。\n"
            "不用 npm 的 score.detail——实测它对 250 个包恒为 1.0，没有区分度。"
        )
        self.table.set_column_stretch(2)
        card.body.addWidget(self.table)
        root.addWidget(card)

        buttons = QHBoxLayout()
        buttons.setSpacing(9)
        self.btn_install = QPushButton("安装选中")
        self.btn_install.setObjectName("Primary")
        self.btn_detail = QPushButton("详情 / 体检")
        self.btn_uninstall = QPushButton("卸载")
        self.btn_uninstall.setObjectName("Danger")
        self.btn_open = QPushButton("打开 npm 页面")
        self.btn_open.setObjectName("Ghost")
        for b in (self.btn_install, self.btn_detail, self.btn_uninstall, self.btn_open):
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
        # 滑到底自动加载更多（表格高度不变，只是内部滚动范围变长）
        self.table.verticalScrollBar().valueChanged.connect(self._on_scroll)
        self.table.doubleClicked.connect(self._detail)
        self.btn_install.clicked.connect(self._install)
        self.btn_detail.clicked.connect(self._detail)
        self.btn_uninstall.clicked.connect(self._uninstall)
        self.btn_open.clicked.connect(self._open_npm)
        self.btn_refresh.clicked.connect(self.refresh)

    # -------------------------------------------------------------- 数据
    # ---------------------------------------------------------- 分页骨架
    def _is_catalog(self) -> bool:
        return (self.source_box.currentData() or "npm") == "catalog"

    def _reset_paging(self) -> None:
        self._acc.clear()
        self._view.clear()
        self._sorted = []
        self._shown = 0
        self._offset = 0
        self._total = 0
        self._exhausted = False
        self._loading_more = False

    def _fetch_page(self, query: str, offset: int) -> market.SearchPage:
        """子类覆写以换数据源（技能页换成 search_skills_page）。"""
        return market.search_page(query, PAGE_SIZE, offset=offset)

    def _set_buttons_enabled(self, on: bool) -> None:
        for b in (self.btn_search, self.btn_refresh, self.btn_audit):
            b.setEnabled(on)

    def refresh(self, force: bool = False) -> None:
        """重置并拉第一页。

        ``force=True`` 给「切换数据源」用：即使有请求在途也要重新发起。同时用代号
        (:attr:`_gen`) 把在途的那份结果作废——否则它回来时会按**新**数据源的规则
        过滤，一条都留不下，表格就永久空着（只有再点一次刷新才能恢复）。
        """
        if self._busy and not force:
            return
        self._gen += 1
        gen = self._gen
        self._busy = True
        self._reset_paging()
        self._set_buttons_enabled(False)
        mode = self.source_box.currentData() or "npm"
        self._set_status(
            "正在拉取官方目录（约 3MB，之后走 6 小时缓存）…"
            if mode == "catalog"
            else "正在从 npm 拉取第一页（结果会缓存 15 分钟）…"
        )
        query = self.search_edit.text().strip()

        def job():
            if mode == "catalog":
                items, meta, err = catalog_mod.load()
                return self._catalog_as_plugins(items), err, len(items), 0, meta
            page = self._fetch_page(query, 0)
            return page.items, page.error, page.total, page.raw_count, None

        def done(result) -> None:
            if gen != self._gen:
                return  # 期间又切了数据源，这份结果属于上一轮，丢掉
            items, err, total, raw, meta = result
            if meta is not None:
                # 目录全部在内存里，一次拿完；模块内有缓存，这里只是 stat 一下 mtime
                self._catalog_items, self._catalog_meta, _ = catalog_mod.load()
                # 分类下拉在这里填，**不能**在切源时同步填：目录 3.2MB，首次
                # （或缓存过期后）要联网下载，慢网上几十秒，放 GUI 线程里就是整窗冻死。
                self._apply_categories(meta)
            self._acc.extend(items)
            self._total = total
            self._offset = raw
            # 目录模式一次性拿全；npm/技能模式到底的条件是"没有更多原始数据"
            self._exhausted = (
                meta is not None or raw <= 0 or self._offset >= self._total
            )
            self._installed = {p.name: p.version for p in plugin_manager.installed()}
            self._busy = False
            self._set_buttons_enabled(True)
            self._resort()
            if err:
                self._set_status(f"⚠️ {err}", error=True)
            self._update_audit_hint()

        def fail(msg: str) -> None:
            if gen != self._gen:
                return
            self._busy = False
            self._set_buttons_enabled(True)
            self._set_status(f"获取失败：{msg}", error=True)

        run_async(job, done, fail)

    def _resort(self) -> None:
        if not self._acc:
            self._view = []
            self._shown = 0
            self._fill()
            return
        self._sorted = market.sort_plugins(
            self._acc, self.sort_box.currentData() or "downloads"
        )
        self._refill(reset=True)

    def _refill(self, reset: bool = True) -> None:
        """按「关键字 + 形态」过滤，然后只渲染前 ``_shown`` 条。

        排名列取的是**在过滤后完整列表里的位次**——否则按关键字筛出 3 条就会
        显示成 1/2/3，看不出它们实际排在哪。
        """
        items = self._sorted
        is_catalog = self._is_catalog()
        if is_catalog:
            items = self._filter_catalog(items)
        rank_of = {id(p): i for i, p in enumerate(items, 1)}

        kw = self.kw_edit.text().strip().lower()
        if kw and not is_catalog:
            items = [
                p for p in items
                if kw in p.name.lower()
                or kw in (p.description or "").lower()
                or any(kw in k.lower() for k in p.keywords)
            ]

        mode = self.half_box.currentData() or "all"
        self._unaudited = 0
        if mode != "all":
            kept = []
            for p in items:
                man = market.cached_manifest(p.name)
                if man is None:
                    self._unaudited += 1
                    continue
                hb, hc = man.has_bundle, man.has_client
                if {
                    "client": hc,
                    "host": hb,
                    "client_only": hc and not hb,
                    "host_only": hb and not hc,
                    "both": hb and hc,
                }.get(mode, True):
                    kept.append(p)
            items = kept

        self._view = items
        self._rank_of = rank_of
        if reset:
            self._shown = min(PAGE_SIZE, len(items))
            self.table.verticalScrollBar().setValue(0)
        else:
            self._shown = min(self._shown, len(items))
        self._fill()

    def _fill(self) -> None:
        """只渲染前 ``_shown`` 条——表格高度固定，多出来的靠滚动看。"""
        is_catalog = self._is_catalog()
        rows = []
        for p in self._view[: self._shown]:
            have = self._installed.get(p.name)
            if have is None:
                state = "未安装"
            elif have == p.version:
                state = "已安装"
            else:
                state = f"可更新 {have}→{p.version}"
            desc = (p.description or "").replace("\n", " ")
            if len(desc) > 100:
                desc = desc[:100] + "…"
            metric = p.rating_text
            if is_catalog:
                cp = self._cat_by_pkg.get(p.name)
                metric = f"{cp.stars:,}" if cp and cp.stars else "—"
            rows.append([
                str(self._rank_of.get(id(p), 0)), p.name, desc, p.version,
                p.downloads_text, metric, state,
            ])
        self.table.fill(rows, align_right={4}, align_center={0, 3, 5, 6})
        self._sync_buttons()

        parts = [f"已加载 {self._shown} / {len(self._view)}"]
        if self._total:
            parts.append(f"源共 {self._total:,}")
        if self._exhausted and self._shown >= len(self._view):
            parts.append("已全部加载")
        else:
            parts.append("滑到底继续加载 ↓")
        kw = self.kw_edit.text().strip()
        if kw:
            parts.append(f"关键字「{kw}」")
        mode = self.half_box.currentData() or "all"
        if mode != "all":
            parts.append(f"形态「{HALF_FILTERS.get(mode, ('', ''))[0]}」")
            if self._unaudited:
                parts.append(f"（{self._unaudited} 个未体检已排除）")
        self._set_status("　".join(parts))

    # ---------------------------------------------------------- 滑到底加载
    def _on_scroll(self, value: int) -> None:
        bar = self.table.verticalScrollBar()
        if bar.maximum() <= 0:
            return
        # 距底部还有十几行时就开始预取，滚动不会顿住
        if value >= bar.maximum() - PREFETCH_ROWS * self.table.row_height:
            self._load_more()

    def _load_more(self) -> None:
        """先展示已累积但没显示的；不够了再向源要下一页。"""
        if self._shown < len(self._view):
            self._shown = min(self._shown + PAGE_SIZE, len(self._view))
            self._fill()
            return
        if self._exhausted or self._loading_more or self._busy or self._is_catalog():
            return

        self._loading_more = True
        query = self.search_edit.text().strip()
        offset = self._offset
        gen = self._gen

        def job():
            return self._fetch_page(query, offset)

        def done(page: market.SearchPage) -> None:
            if gen != self._gen:
                return  # 已经换了数据源，这一页属于上一轮
            self._loading_more = False
            self._offset += page.raw_count
            if page.items:
                self._acc.extend(page.items)
            # 到底的三种信号：本页没有原始数据 / 没有可用条目 / 已越过总数
            if page.raw_count <= 0 or not page.items or self._offset >= page.total:
                self._exhausted = True
            if page.error:
                self._set_status(f"⚠️ {page.error}", error=True)
            self._sorted = market.sort_plugins(
                self._acc, self.sort_box.currentData() or "downloads"
            )
            self._refill(reset=False)
            self._shown = min(self._shown + PAGE_SIZE, len(self._view))
            self._fill()

        def fail(msg: str) -> None:
            if gen != self._gen:
                return
            self._loading_more = False
            self._set_status(f"加载更多失败：{msg}", error=True)

        run_async(job, done, fail)

    def _sync_buttons(self) -> None:
        p = self._selected()
        has = p is not None and not self._busy
        self.btn_detail.setEnabled(has)
        self.btn_open.setEnabled(has)
        if p is not None:
            have = self._installed.get(p.name)
            self.btn_uninstall.setEnabled(has and have is not None)
            if have == p.version:
                self.btn_install.setText("已安装（重装）")
            elif have is not None:
                self.btn_install.setText("更新到此版本")
            else:
                self.btn_install.setText("安装选中")
            self.btn_install.setEnabled(has)
        else:
            self.btn_install.setEnabled(False)
            self.btn_uninstall.setEnabled(False)

    # ---------------------------------------------------------- 数据源切换
    def _on_source_change(self) -> None:
        mode = self.source_box.currentData() or "npm"
        is_catalog = mode == "catalog"
        self.category_box.setVisible(is_catalog)
        self.kw_edit.setPlaceholderText(
            "在目录里筛选（名称 / 作者 / 中英文描述，本地匹配）…"
            if is_catalog
            else "关键字筛选（包名 / 说明 / 标签，本地匹配）…"
        )
        # 目录模式没有 npm 的评分构成，把该列改成星标（目录的真实字段）
        self.table.set_headers(
            ["排名", "插件", "说明", "版本", "下载", "星标", "本机"]
            if is_catalog
            else ["排名", "插件", "说明", "版本", "周下载", "评分", "本机"]
        )
        if not is_catalog:
            self._apply_categories(None)
        # force=True：在途请求还没回来时切源，以前会被 refresh() 的 `if self._busy` 挡掉，
        # 结果是旧数据源的数据 + 新数据源的过滤规则 = 空表且无法自愈。
        self.refresh(force=True)

    def _apply_categories(self, meta: dict | None) -> None:
        """用目录元数据填「分类」下拉。

        **不要在切源时同步调 ``catalog_mod.load()``**：目录 3.2 MB，首次（或缓存
        过期后）要联网下载 + 解析，慢网上是几十秒（catalog.py 自己的说明里写 ~47 s），
        放在 GUI 线程里就是整个窗口冻住。``refresh()`` 的后台任务已经把它拉回来了，
        这里只负责填下拉，顺手把信号挡住免得触发一串 _refill。
        """
        cats = sorted(((meta or {}).get("categories") or {}).keys())
        self.category_box.blockSignals(True)
        try:
            self.category_box.clear()
            self.category_box.addItem("全部", "")
            for c in cats:
                self.category_box.addItem(c, c)
        finally:
            self.category_box.blockSignals(False)

    @staticmethod
    def _catalog_as_plugins(items: list) -> list[market.MarketPlugin]:
        """把目录条目适配成 MarketPlugin，这样表格/详情/安装全都能复用。"""
        out = []
        for c in items:
            out.append(
                market.MarketPlugin(
                    name=c.pkg,
                    version=c.version,
                    description=c.desc,
                    publisher=c.owner,
                    repository=c.repo_url,
                    homepage=c.page,
                    published=c.added,
                    weekly_downloads=c.downloads,
                    keywords=[c.category] if c.category else [],
                )
            )
        return out

    def _filter_catalog(self, plugins: list[market.MarketPlugin]) -> list[market.MarketPlugin]:
        """目录模式下的分类 + 关键字过滤。

        关键字要按**目录条目的原始字段**匹配（中文描述、作者、npm 包名），
        而不是适配后的 MarketPlugin——后者只留了英文/合并后的字段。
        """
        cat = self.category_box.currentData() or ""
        kw = self.kw_edit.text().strip().lower()
        self._cat_by_pkg = {c.pkg: c for c in self._catalog_items}
        out = []
        for p in plugins:
            cp = self._cat_by_pkg.get(p.name)
            if cp is None:
                continue
            if cat and cp.category != cat:
                continue
            if kw and not (
                kw in cp.name.lower()
                or kw in cp.owner.lower()
                or kw in cp.desc.lower()
                or kw in cp.pkg.lower()
            ):
                continue
            out.append(p)
        return out

    def _open_source_dialog(self) -> None:
        """子类覆写成各自的市场源设置。"""
        from .dialogs_sources import PluginSourceDialog

        dlg = PluginSourceDialog(self.theme, self)
        dlg.applied.connect(self.refresh)
        dlg.exec()

    def _do_search(self, query: str) -> tuple[list[market.MarketPlugin], str]:
        """子类覆写以换数据源（技能页就换了查询）。"""
        return market.search(query)

    def _selected(self) -> market.MarketPlugin | None:
        r = self.table.selected_row()
        if 0 <= r < min(self._shown, len(self._view)):
            return self._view[r]
        return None

    # -------------------------------------------------------------- 体检
    def _update_audit_hint(self) -> None:
        """显示体检覆盖率，让"形态"筛选的可用范围对用户可见。"""
        if not self._sorted:
            self.btn_audit.setEnabled(False)
            return
        # 只读内存索引（O(1)）——绝不能在这里碰 29 MB 的详情文档
        known = sum(1 for p in self._sorted if market.cached_manifest(p.name) is not None)
        self.btn_audit.setEnabled(known < len(self._sorted) and not self._auditing)
        self.btn_audit.setText(
            "体检全部" if known == 0 else f"补齐体检 ({known}/{len(self._sorted)})"
        )

    def _on_audit_progress(self, done: int, total: int, name: str) -> None:
        self.audit_bar.setRange(0, max(1, total))
        self.audit_bar.setValue(done)
        self._set_status(f"体检中 {done}/{total}：{name}")

    def _audit(self) -> None:
        """并发拉取所有已加载插件的注册表详情，之后「形态」筛选才有依据。"""
        if self._auditing or not self._sorted:
            return
        names = [p.name for p in self._sorted]
        self._auditing = True
        self.audit_bar.setVisible(True)
        self.audit_bar.setRange(0, len(names))
        self.audit_bar.setValue(0)
        self.btn_audit.setEnabled(False)
        self.btn_search.setEnabled(False)

        def job() -> int:
            # 已缓存的不再请求；audit_many 内部一次写回缓存，避免多线程争抢
            market.audit_many(
                names,
                workers=16,
                on_progress=lambda i, t, n: self._audit_progress.emit(i, t, n),
            )
            return len(names)

        def done(count: int) -> None:
            self._auditing = False
            self.audit_bar.setVisible(False)
            self.btn_search.setEnabled(True)
            self._update_audit_hint()
            self._refill()

        def fail(msg: str) -> None:
            self._auditing = False
            self.audit_bar.setVisible(False)
            self.btn_search.setEnabled(True)
            self._update_audit_hint()
            self._set_status(f"体检失败：{msg}", error=True)

        run_async(job, done, fail)

    def _set_status(self, text: str, error: bool = False, ok: bool = False) -> None:
        color = self.theme.danger if error else (self.theme.ok if ok else self.theme.text_dim)
        self.status.setStyleSheet(
            f"color: {color}; background: transparent; font-size: 12.5px;"
        )
        self.status.setText(text)

    # -------------------------------------------------------------- 动作
    def _detail(self) -> None:
        p = self._selected()
        if p is None:
            return
        dlg = MarketPluginDialog(p, self.theme, self._installed.get(p.name, ""), self)

        def job() -> market.MarketDetail:
            return market.detail(p.name)

        run_async(job, dlg.set_audit, lambda m: dlg.set_audit(market.MarketDetail(error=m)))
        dlg.exec()

    def _open_npm(self) -> None:
        p = self._selected()
        if p is None:
            return
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl

        url = p.npm_url or f"https://www.npmjs.com/package/{p.name}"
        QDesktopServices.openUrl(QUrl(url))

    def _install(self) -> None:
        p = self._selected()
        if p is None:
            return
        have = self._installed.get(p.name)
        spec = p.name if have is None else f"{p.name}@{p.version}"
        ans = QMessageBox.question(
            self,
            "确认安装",
            f"即将安装：{spec}\n\n"
            "建议先点「详情 / 体检」确认这个包不会在安装时执行代码。\n"
            "安装后需要重启 harness 才会生效。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ans != QMessageBox.Yes:
            return
        self._busy = True
        self._sync_buttons()
        self._set_status(f"正在安装 {spec}…")

        def job() -> str:
            return plugin_manager.install(spec)

        def done(msg: str) -> None:
            self._busy = False
            self._set_status(msg, ok=True)
            self.refresh()
            self.changed.emit()
            self._offer_restart()

        def fail(msg: str) -> None:
            self._busy = False
            self._set_status(f"安装失败：{msg}", error=True)
            self._sync_buttons()

        run_async(job, done, fail)

    def _uninstall(self) -> None:
        p = self._selected()
        if p is None or p.name not in self._installed:
            return
        ans = QMessageBox.question(
            self, "确认卸载", f"确定卸载 {p.name} 吗？需要重启 harness 才生效。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if ans != QMessageBox.Yes:
            return
        self._busy = True
        self._sync_buttons()
        self._set_status(f"正在卸载 {p.name}…")

        def job() -> str:
            return plugin_manager.uninstall(p.name)

        def done(msg: str) -> None:
            self._busy = False
            self._set_status(msg, ok=True)
            self.refresh()
            self.changed.emit()
            self._offer_restart()

        def fail(msg: str) -> None:
            self._busy = False
            self._set_status(f"卸载失败：{msg}", error=True)
            self._sync_buttons()

        run_async(job, done, fail)

    def _offer_restart(self) -> None:
        ans = QMessageBox.question(
            self, "需要重启",
            "插件装配列表已改变，需要重启 harness 才会生效。\n\n"
            "⚠️ 重启会断开正在进行的会话。是否现在重启？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
        )
        if ans != QMessageBox.Yes:
            return
        self._set_status("正在重启 harness…")

        def job() -> str:
            return plugin_manager.restart_service()

        run_async(job, lambda m: self._set_status(m, ok=True),
                  lambda m: self._set_status(f"重启失败：{m}", error=True))

    def apply_theme(self, theme: Theme) -> None:
        self.theme = theme
        self.status.setStyleSheet(
            f"color: {theme.text_dim}; background: transparent; font-size: 12.5px;"
        )
