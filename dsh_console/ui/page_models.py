"""模型与价格页：官方峰谷时段、当前档位、模型价目表。"""

from __future__ import annotations

import dataclasses

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
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

from .. import billing, pricing
from ..themes import Theme
from ..workers import run_async
from .components import Card, Pill, ScrollPage


class ModelsPage(ScrollPage):
    """模型与价格。"""

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme = theme
        self._currency = "CNY"
        self._live_models: list[str] = []
        self._build()
        self._tick()
        # 纯本地计算（峰谷倒计时），不碰网络

        # 倒计时需要每秒走字，但**只在页面可见时**跑
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)

    def activate(self) -> None:
        if not self._timer.isActive():
            self._timer.start()
        self.load_live_models()
        # 官方模型列表走网络，改为首次显示时才拉

    def deactivate(self) -> None:
        self._timer.stop()

    # ------------------------------------------------------------------ UI
    def _build(self) -> None:
        root = self.body
        root.setSpacing(16)


        # ---- 当前时段
        self.phase_card = Card("当前时段")
        head = QHBoxLayout()
        self.phase_pill = Pill("—")
        head.addWidget(self.phase_pill)
        self.phase_desc = QLabel("")
        self.phase_desc.setStyleSheet("background: transparent;")
        head.addWidget(self.phase_desc)
        head.addStretch(1)
        self.phase_card.body.addLayout(head)

        grid = QGridLayout()
        grid.setHorizontalSpacing(38)
        self.countdown_label = QLabel("—")
        self.countdown_label.setObjectName("MetricValue")
        self.next_label = QLabel("—")
        self.next_label.setObjectName("MetricValue")
        for col, (cap, widget) in enumerate(
            (("距离下一次切换", self.countdown_label), ("下一次进入", self.next_label))
        ):
            box = QVBoxLayout()
            box.setSpacing(3)
            box.addWidget(widget)
            cap_label = QLabel(cap)
            cap_label.setObjectName("MetricLabel")
            box.addWidget(cap_label)
            grid.addLayout(box, 0, col)
        self.phase_card.body.addLayout(grid)

        self.windows_label = QLabel("")
        self.windows_label.setObjectName("CardHint")
        self.phase_card.body.addWidget(self.windows_label)
        root.addWidget(self.phase_card)

        # ---- 价目表
        price_card = Card("模型价目表", "单位：每 100 万 tokens。当前档位价格随峰谷实时切换。")
        controls = QHBoxLayout()
        self.currency_box = QComboBox()
        self.currency_box.addItem("人民币 ¥", "CNY")
        self.currency_box.addItem("美元 $", "USD")
        self.currency_box.setFixedWidth(130)
        self.currency_box.currentIndexChanged.connect(self._on_currency)
        controls.addWidget(QLabel("计价币种"))
        controls.addWidget(self.currency_box)
        controls.addStretch(1)
        self.live_label = QLabel("")
        self.live_label.setObjectName("CardHint")
        controls.addWidget(self.live_label)
        price_card.body.addLayout(controls)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["模型", "缓存命中", "缓存未命中", "输出", "档位", "说明"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        for c in (1, 2, 3):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(5, QHeaderView.Stretch)
        # 行高固定，便于按行数算出刚好贴合内容的高度（避免大片空白）
        self.table.verticalHeader().setDefaultSectionSize(34)
        price_card.body.addWidget(self.table)
        root.addWidget(price_card)

        note = QLabel(
            "计费公式：input×缓存未命中 + output×输出 + (缓存读+缓存写)×缓存命中，"
            "按每次调用发生时刻的档位计费；跨峰谷不漂移。"
        )
        note.setObjectName("CardHint")
        note.setWordWrap(True)
        root.addWidget(note)
        root.addStretch(1)

    # -------------------------------------------------------------- 逻辑
    def _tick(self) -> None:
        phase = pricing.peak_phase_at()
        if phase is None:
            self.phase_pill.set_state("未知", self.theme.text_faint, self.theme.text)
            return
        color = self.theme.peak if phase.in_peak else self.theme.offpeak
        self.phase_pill.set_state(phase.label, color, self.theme.text)
        multiplier = "全价" if phase.in_peak else "半价"
        self.phase_desc.setText(
            f"当前按<b style='color:{color}'>{phase.label}</b>（{multiplier}）计费，"
            f"下一次切换：{'进入峰时段' if phase.next_into_peak else '进入谷时段'}"
        )
        self.countdown_label.setText(phase.countdown_text)
        self.next_label.setText(f"{phase.next_at.astimezone():%m-%d %H:%M}")
        self.windows_label.setText(
            f"峰时段（本地时间）：{pricing.local_windows_text()}　·　"
            f"UTC 周六/周日全天谷期　·　谷时价 = 峰时价的一半"
        )
        self._tick_table()

    def _tick_table(self) -> None:
        phase = pricing.peak_phase_at()
        in_peak = bool(phase and phase.in_peak)
        rows = list(pricing.MODELS)
        # 官方 API 返回的可能是别名（实测是 deepseek-flash / deepseek-v4-pro），
        # 别名会解析到与内置表**同一个**条目。因此去重必须比较「解析后的条目」，
        # 而不是原始模型 id —— 否则会出现一行 DeepSeek V4 Flash 重复两次。
        shown = {pricing._normalize(m.key) for m in rows}
        for mid in self._live_models:
            entry = pricing.price_entry_for(mid)
            if pricing._normalize(entry.key) in shown:
                continue
            if entry is pricing.DEFAULT_PRICE:
                # 官方有、内置价目表没有的模型：单列一行，套兜底档但保留真实模型名
                entry = dataclasses.replace(
                    pricing.DEFAULT_PRICE, key=mid, label=mid, note="官方模型，按 Flash 档计价"
                )
            shown.add(pricing._normalize(entry.key))
            rows.append(entry)

        self.table.setRowCount(len(rows))
        cur = self._currency
        for r, entry in enumerate(rows):
            tier = entry.peak if in_peak else entry.off_peak
            self._set(r, 0, entry.label)
            self._set(r, 1, self._money(tier.cache_hit, cur))
            self._set(r, 2, self._money(tier.cache_miss, cur))
            self._set(r, 3, self._money(tier.output, cur))
            self._set(r, 4, "峰时价" if in_peak else "谷时价")
            self._set(r, 5, entry.note or "—")
        # 表格高度贴合行数：表头 + 每行 34px
        header_h = self.table.horizontalHeader().sizeHint().height()
        self.table.setFixedHeight(header_h + 34 * len(rows) + 10)

    def _set(self, r: int, c: int, text: str) -> None:
        item = QTableWidgetItem(text)
        if c in (1, 2, 3):
            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        elif c == 4:
            item.setTextAlignment(Qt.AlignCenter)
        self.table.setItem(r, c, item)

    def _money(self, usd: float, cur: str) -> str:
        if cur == "CNY":
            return f"¥{usd * pricing.USD_TO_CNY:,.4f}"
        return f"${usd:,.4f}"

    def _on_currency(self) -> None:
        self._currency = self.currency_box.currentData()
        self._tick_table()

    def load_live_models(self) -> None:
        """后台拉一次官方模型列表（有 key 才有意义）。"""

        def done(models: list[str]) -> None:
            self._live_models = models
            self.live_label.setText(
                f"已从官方 API 同步 {len(models)} 个模型" if models else "未同步到官方模型列表（离线或未配置 key）"
            )
            self._tick_table()

        run_async(billing.fetch_models, done, lambda m: self.live_label.setText(m))

    def apply_theme(self, theme: Theme) -> None:
        self.theme = theme
        self.phase_desc.setStyleSheet("background: transparent;")
        self._tick()
