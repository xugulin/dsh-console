"""DeepSeek 模型与峰谷价格。

这是对 ``dsh-tidewatch`` 插件 ``lib/pricing.js`` 的忠实移植（MIT），保证控制台显示的
价格与你在 DSH 界面里看到的那张卡片、以及会话计费**完全一致**。

官方口径（2026-08-17 起生效的峰谷分时定价）::

    峰时段（UTC 小时，半开区间）：01:00–04:00、06:00–10:00
    即北京时间 09:00–12:00、14:00–18:00；其余为空闲（谷）时段。
    谷时价 = 峰时价的一半。
    官方 2026-08-23 起：UTC 周六/周日全天按谷期计价，无峰谷切换。

计费公式（美元 / 1M tokens）::

    cost = ( input × cacheMiss
           + output × output
           + (cacheRead + cacheWrite) × cacheHit
           + reasoning × reasoningPrice ) / 1e6
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

#: 峰谷时代分界：此前的计费按当时的基础价执行（历史正确性）。
LEGACY_BASE_BOUNDARY = datetime(2026, 8, 16, 16, 0, tzinfo=timezone.utc)

#: 峰时段窗口（UTC 小时，半开区间 [start, end)）。
PEAK_WINDOWS: tuple[tuple[int, int], ...] = ((1, 4), (6, 10))

#: 美元 → 人民币换算率（与 tidewatch 默认一致）。
USD_TO_CNY = 6.82


@dataclass(frozen=True, slots=True)
class Price:
    """一档价格（美元 / 1M tokens）。"""

    cache_hit: float
    cache_miss: float
    output: float
    reasoning: float | None = None


@dataclass(frozen=True, slots=True)
class ModelPrice:
    """一个模型的完整价格记录。"""

    key: str
    label: str
    tier: Price  # 基础档（= 空闲档）
    off_peak: Price
    peak: Price
    legacy_base: Price | None = None
    note: str = ""


def _p(cache_hit: float, cache_miss: float, output: float, reasoning: float | None = None) -> Price:
    return Price(cache_hit=cache_hit, cache_miss=cache_miss, output=output, reasoning=reasoning)


#: 内置价格表（美元 / 1M tokens，与官方定价页数字一致）。
MODELS: tuple[ModelPrice, ...] = (
    ModelPrice(
        key="deepseek-v4-flash",
        label="DeepSeek V4 Flash",
        tier=_p(0.007, 0.22, 0.66),
        off_peak=_p(0.007, 0.22, 0.66),
        peak=_p(0.014, 0.44, 1.32),
        legacy_base=_p(0.0028, 0.14, 0.28),
        note="默认模型，速度优先",
    ),
    ModelPrice(
        key="deepseek-v4-pro",
        label="DeepSeek V4 Pro",
        tier=_p(0.022, 0.66, 1.98),
        off_peak=_p(0.022, 0.66, 1.98),
        peak=_p(0.044, 1.32, 3.96),
        legacy_base=_p(0.003625, 0.435, 0.87),
        note="复杂推理，能力优先",
    ),
    ModelPrice(
        key="deepseek-v4-flash-vision-exp",
        label="DeepSeek V4 Flash Vision (实验)",
        tier=_p(0.007, 0.22, 0.66),
        off_peak=_p(0.007, 0.22, 0.66),
        peak=_p(0.014, 0.44, 1.32),
        legacy_base=_p(0.0028, 0.14, 0.28),
        note="多模态实验版，按 Flash 价计费",
    ),
)

#: 未命中价格表时的兜底档位（= Flash 档）。
DEFAULT_PRICE: ModelPrice = ModelPrice(
    key="default",
    label="未知模型（按 Flash 档兜底）",
    tier=_p(0.007, 0.22, 0.66),
    off_peak=_p(0.007, 0.22, 0.66),
    peak=_p(0.014, 0.44, 1.32),
)


def _normalize(name: str) -> str:
    """忽略大小写/空格/横杠/点号与括号附注。"""
    out = []
    for ch in str(name or "").lower():
        if ch.isalnum():
            out.append(ch)
    return "".join(out)


def price_entry_for(model: str) -> ModelPrice:
    """按模型名查价格；未命中返回兜底档。

    支持 ``deepseek-flash`` 这类别名：先精确匹配，再退化为包含匹配
    （``provider/model`` 形式也能命中）。
    """
    key = _normalize(model)
    if not key:
        return DEFAULT_PRICE
    for entry in MODELS:
        if _normalize(entry.key) == key:
            return entry
    for entry in MODELS:
        if _normalize(entry.key) in key:
            return entry
    # 常见别名：flash / pro 直接对应
    if "pro" in key:
        return MODELS[1]
    if "flash" in key or "vision" in key:
        return MODELS[0]
    return DEFAULT_PRICE


def is_peak_hour(at: datetime, windows: tuple[tuple[int, int], ...] = PEAK_WINDOWS) -> bool:
    """某一时刻是否处于峰时段（UTC 周六/周日全天谷期）。"""
    if not windows:
        return False
    u = at.astimezone(timezone.utc)
    if u.weekday() >= 5:  # Python: 周一=0 … 周六=5、周日=6
        return False
    return any(start <= u.hour < end for start, end in windows)


def tier_for(entry: ModelPrice, at: datetime) -> Price:
    """为一次用量挑选价格档位。"""
    if at < LEGACY_BASE_BOUNDARY and entry.legacy_base is not None:
        return entry.legacy_base
    return entry.peak if is_peak_hour(at) else entry.off_peak


def cost_of(tokens: dict[str, float], entry: ModelPrice, at: datetime) -> float:
    """一次调用的美元成本。

    ``tokens`` 的键：``input``/``output``/``cacheRead``/``cacheWrite``/``reasoning``。
    """
    tier = tier_for(entry, at)
    inp = max(0.0, float(tokens.get("input") or 0))
    out = max(0.0, float(tokens.get("output") or 0))
    c_read = max(0.0, float(tokens.get("cacheRead") or 0))
    c_write = max(0.0, float(tokens.get("cacheWrite") or 0))
    reason = max(0.0, float(tokens.get("reasoning") or 0))
    reason_price = tier.reasoning if tier.reasoning is not None else 0.0
    return (
        inp * tier.cache_miss
        + out * tier.output
        + (c_read + c_write) * tier.cache_hit
        + reason * reason_price
    ) / 1e6


@dataclass(frozen=True, slots=True)
class Phase:
    """当前峰谷相位与相邻切换点。"""

    in_peak: bool
    prev_at: datetime
    next_at: datetime
    next_into_peak: bool

    @property
    def label(self) -> str:
        return "峰时段" if self.in_peak else "谷时段"

    @property
    def seconds_to_next(self) -> int:
        return max(0, int((self.next_at - datetime.now(timezone.utc)).total_seconds()))

    @property
    def countdown_text(self) -> str:
        s = self.seconds_to_next
        h, rem = divmod(s, 3600)
        m, sec = divmod(rem, 60)
        if h:
            return f"{h} 小时 {m} 分"
        if m:
            return f"{m} 分 {sec} 秒"
        return f"{sec} 秒"


def peak_phase_at(at: datetime | None = None) -> Phase | None:
    """当前相位与下一次切换点（供倒计时展示）。

    周末没有切换点，因此切换点收集范围要往前多铺几天，让周六/周日的「上一次切换」
    落在周五、「下一次」落在下周一。

    往前**必须是 3 天而不是 2 天**：周一 00:00–01:00 UTC（本地周一 08:00–09:00）时，
    上一个切换点是**上周五**最后一个窗口的结束；而周一看 -2 天只到周六，-3 天才够到
    周五。用 -2 时这一段算不出 ``prev``，函数返回 None，「模型与价格」页就显示「未知」
    并且不再刷新倒计时和表格——每周一早上固定失灵一小时。
    """
    at = at or datetime.now(timezone.utc)
    at = at.astimezone(timezone.utc)
    if not PEAK_WINDOWS:
        return None

    def hour_at(day_offset: int, hour: int) -> datetime:
        base = (at + timedelta(days=day_offset)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return base + timedelta(hours=hour)

    points: list[tuple[datetime, bool]] = []
    for day in range(-3, 4):
        d = hour_at(day, 0)
        if d.weekday() >= 5:  # 周末无峰谷切换
            continue
        for start, end in PEAK_WINDOWS:
            points.append((hour_at(day, start), True))
            points.append((hour_at(day + 1 if end <= start else day, end), False))
    if not points:
        return None

    prev = max((p for p in points if p[0] <= at), key=lambda p: p[0], default=None)
    nxt = min((p for p in points if p[0] > at), key=lambda p: p[0], default=None)
    if prev is None or nxt is None:
        return None
    return Phase(
        in_peak=is_peak_hour(at),
        prev_at=prev[0],
        next_at=nxt[0],
        next_into_peak=nxt[1],
    )


def cny(usd: float, rate: float = USD_TO_CNY) -> float:
    return usd * rate


def fmt_usd(v: float) -> str:
    return f"${v:,.4f}" if abs(v) < 1 else f"${v:,.2f}"


def fmt_cny(v: float) -> str:
    return f"¥{v:,.2f}"


def local_windows_text() -> str:
    """把 UTC 峰时段窗口换算成本地时间文案。"""
    tz = datetime.now().astimezone().tzinfo
    parts = []
    for start, end in PEAK_WINDOWS:
        ref = datetime(2026, 1, 5, tzinfo=timezone.utc)  # 一个周一
        s = (ref + timedelta(hours=start)).astimezone(tz)
        e = (ref + timedelta(hours=end)).astimezone(tz)
        parts.append(f"{s:%H:%M}–{e:%H:%M}")
    return "、".join(parts)
