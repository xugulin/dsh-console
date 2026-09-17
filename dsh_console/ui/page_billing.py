"""账单页：账户余额 + 本地会话成本聚合。"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
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
from .components import FixedTable, Card, Metric, Pill, ScrollPage


class BillingPage(ScrollPage):
    """余额与会话花费。"""

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme = theme
        self._sessions: list[billing.SessionCost] | None = None
        self._loaded_at = 0.0
        self._build()

    # ------------------------------------------------------------------ UI
    def _build(self) -> None:
        root = self.body
        root.setSpacing(16)


        # ---- 余额
        self.balance_card = Card("账户余额")
        # 刷新按钮放在账户余额卡片的**右上角**：这一页真正"会变"的就是余额，
        # 刷新键待在页面顶部离它太远，眼睛要来回跑。
        self.btn_refresh = QPushButton("刷新")
        self.btn_refresh.setObjectName("Primary")
        self.balance_card.header.addWidget(self.btn_refresh)
        brow = QHBoxLayout()
        self.balance_pill = Pill("未查询")
        brow.addWidget(self.balance_pill)
        brow.addStretch(1)
        self.key_label = QLabel("")
        self.key_label.setObjectName("CardHint")
        brow.addWidget(self.key_label)
        self.balance_card.body.addLayout(brow)

        bgrid = QGridLayout()
        bgrid.setHorizontalSpacing(38)
        self.m_total = Metric("总余额", accent=True)
        self.m_topped = Metric("充值余额")
        self.m_granted = Metric("赠送余额")
        for i, m in enumerate((self.m_total, self.m_topped, self.m_granted)):
            bgrid.addWidget(m, 0, i)
        self.balance_card.body.addLayout(bgrid)
        self.balance_error = QLabel("")
        self.balance_error.setStyleSheet(
            f"color: {self.theme.warn}; background: transparent; font-size: 12px;"
        )
        self.balance_error.setWordWrap(True)
        self.balance_card.body.addWidget(self.balance_error)
        root.addWidget(self.balance_card)

        # ---- 今日消费（总 / 峰 / 谷）
        #
        # 口径和控制台页「今日消费」那张卡一致（同一个 `today_usage_cached`，
        # 60 秒 TTL，不额外扫盘）。放在余额下面：这一页问的依次是
        # "还剩多少 → 今天花了多少 → 一共花了多少 → 花在哪了"。
        today_card = Card("今日消费", "按事件发生时刻的峰谷档位计费；只统计本机会话记录")
        trow = QHBoxLayout()
        trow.setSpacing(34)
        self.t_total = Metric("今日总消费", accent=True)
        self.t_peak = Metric("峰时段消费")
        self.t_off = Metric("谷时段消费")
        for m in (self.t_total, self.t_peak, self.t_off):
            trow.addWidget(m)
        trow.addStretch(1)
        today_card.body.addLayout(trow)

        trow2 = QHBoxLayout()
        trow2.setSpacing(34)
        self.t_calls = Metric("模型调用")
        self.t_tokens = Metric("总 tokens")
        self.t_hit = Metric("缓存命中率")
        for m in (self.t_calls, self.t_tokens, self.t_hit):
            trow2.addWidget(m)
        trow2.addStretch(1)
        today_card.body.addLayout(trow2)
        root.addWidget(today_card)

        # ---- 汇总
        self.summary_card = Card("会话花费汇总", "仅在本地统计，不影响服务运行")
        sgrid = QGridLayout()
        sgrid.setHorizontalSpacing(38)
        self.m_sessions = Metric("会话数")
        self.m_calls = Metric("模型调用次数")
        self.m_tokens = Metric("总 tokens")
        self.m_cost = Metric("累计花费", accent=True)
        for i, m in enumerate((self.m_sessions, self.m_calls, self.m_tokens, self.m_cost)):
            sgrid.addWidget(m, 0, i)
        self.summary_card.body.addLayout(sgrid)
        root.addWidget(self.summary_card)

        # ---- 明细表
        detail = Card("会话明细", "按花费从高到低排序")
        self.table = FixedTable(
            ["工作区", "会话", "调用", "tokens", "模型", "花费（¥）"],
            visible_rows=9,
        )
        # **固定高度 + 表内滚动**：原来是裸的 QTableWidget，行数一多就无限拉长，
        # 把页面顶得没边（用户截图里那一列拉不到底的表）。FixedTable 会按
        # visible_rows 钉死高度，超出的行在表格内部滚。
        detail.body.addWidget(self.table)
        root.addWidget(detail)

        self.btn_refresh.clicked.connect(lambda: self.refresh(invalidate=True))

    def activate(self) -> None:
        """切到本页时拉数据——但**数据还热就不重复拉**。

        每次切页都重填 116 行表格是实打实的开销（实测切页 ~0.7 秒里它占大头）。
        60 秒内（和 billing 的缓存 TTL 对齐）直接用现成的。
        """
        if self._sessions is not None and (time.monotonic() - self._loaded_at) < 60:
            return
        self.refresh()

    # -------------------------------------------------------------- 数据
    def _on_today(self, today) -> None:
        if today is None or today.error:
            for m in (self.t_total, self.t_peak, self.t_off, self.t_calls, self.t_tokens, self.t_hit):
                m.set_value("—")
            return
        cny = pricing.cny
        self.t_total.set_value(f"¥{cny(today.cost_usd):,.2f}")
        self.t_peak.set_value(f"¥{cny(today.peak_cost_usd):,.2f}")
        self.t_off.set_value(f"¥{cny(today.offpeak_cost_usd):,.2f}")
        self.t_calls.set_value(f"{today.calls:,}")
        self.t_tokens.set_value(f"{today.tokens:,}")
        hit = (today.cache_read / today.tokens * 100) if today.tokens else 0.0
        self.t_hit.set_value(f"{hit:.1f}%")

    def refresh(self, *, invalidate: bool = False) -> None:
        """拉数据。

        ``invalidate`` 只有**点刷新按钮**时才为真——切页（``activate``）走缓存。
        这两条必须分开：全量扫一遍要 6.6 秒（116 个会话），如果切页也作废缓存，
        每次点侧边栏都要等十几秒，比不做缓存还糟。
        """
        if invalidate:
            billing.invalidate_sessions()
            billing.invalidate_today()
        self.btn_refresh.setEnabled(False)
        self.btn_refresh.setText("刷新中…")
        self.balance_error.setText("")
        self.key_label.setText(f"API key：{billing.mask_key(billing.read_api_key())}")

        run_async(billing.fetch_balance, self._on_balance, self._on_balance_error)
        run_async(self._scan, self._on_sessions, self._on_scan_error)
        run_async(billing.today_usage_cached, self._on_today, None)

    @staticmethod
    def _scan() -> list[billing.SessionCost]:
        # 不用再单独跑一遍 session_models()：模型已在同一遍解析中得出。
        # **走缓存**：全量扫描要解压并解析每个会话文件，是这一页最大的开销。
        return billing.scan_all_sessions_cached()

    def _on_balance(self, bal: billing.Balance) -> None:
        self.btn_refresh.setEnabled(True)
        self.btn_refresh.setText("刷新")
        if not bal.ok:
            self.balance_pill.set_state("查询失败", self.theme.danger, self.theme.text)
            self.balance_error.setText(bal.error)
            return
        primary = bal.primary
        if primary is None:
            self.balance_pill.set_state("无余额信息", self.theme.warn, self.theme.text)
            return
        ok = bal.available and primary.total > 0
        self.balance_pill.set_state(
            "可用" if ok else "余额不足",
            self.theme.ok if ok else self.theme.danger,
            self.theme.text,
        )
        sym = primary.symbol
        self.m_total.set_value(f"{sym}{primary.total:,.2f}")
        self.m_topped.set_value(f"{sym}{primary.topped_up:,.2f}")
        self.m_granted.set_value(f"{sym}{primary.granted:,.2f}")

    def _on_balance_error(self, msg: str) -> None:
        self.btn_refresh.setEnabled(True)
        self.btn_refresh.setText("刷新")
        self.balance_pill.set_state("查询失败", self.theme.danger, self.theme.text)
        self.balance_error.setText(msg)

    def _on_sessions(self, sessions: list[billing.SessionCost]) -> None:
        self._sessions = sessions
        self._loaded_at = time.monotonic()
        total_cost = sum(s.cost_usd for s in sessions)
        self.m_sessions.set_value(str(len(sessions)))
        self.m_calls.set_value(f"{sum(s.calls for s in sessions):,}")
        self.m_tokens.set_value(f"{sum(s.total_tokens for s in sessions):,}")
        self.m_cost.set_value(pricing.fmt_cny(pricing.cny(total_cost)))

        # FixedTable 用自定义模型：**一次性 fill**，不要逐格 setItem。
        # 逐格写 116×6=696 个 QTableWidgetItem 才是切页卡顿的大头。
        rows = [
            [
                s.display_name,
                s.session_id[:12],
                str(s.calls),
                f"{s.total_tokens:,}",
                "、".join(sorted(s.by_model)) or "—",
                pricing.fmt_cny(pricing.cny(s.cost_usd)),
            ]
            for s in sessions
        ]
        self.table.fill(rows, align_right={2, 3, 5})

    def _on_scan_error(self, msg: str) -> None:
        self.m_cost.set_value("扫描失败")
        self.balance_error.setText(f"会话扫描失败：{msg}")

    def apply_theme(self, theme: Theme) -> None:
        self.theme = theme
        self.balance_error.setStyleSheet(
            f"color: {theme.warn}; background: transparent; font-size: 12px;"
        )
