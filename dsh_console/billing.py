"""账单数据：账户余额（官方 API）与会话成本聚合（本地会话文件）。

两个数据源：

1. **账户余额** —— ``GET https://api.deepseek.com/user/balance``，需要 API key。
   key 从 ``~/.dsh/.credentials.yaml`` 的 ``refs.DEEPSEEK_API_KEY`` 读取
   （实测该字段存的是 ``sk-`` 开头的原始 key）。**本模块任何日志/界面都只展示掩码**。

2. **会话成本** —— 直接读 ``~/.dsh/sessions/*/*/session.v3.jsonl.zstd``。
   Python 3.14 起标准库自带 ``compression.zstd``，因此**无需第三方依赖**。
   从 ``assistant/message`` 事件的 ``data.usage`` 取 token，按事件发生时刻
   （``event.time``）选峰/谷档位逐次计费——与 tidewatch 插件口径一致。

usage 块的实际结构（已核对）::

    {"inputTokens": 1203, "outputTokens": 300, "totalTokens": 9439,
     "cacheReadTokens": 7936, "reasoningTokens": 78}
"""

from __future__ import annotations

import json
import re
import os
import threading
from concurrent.futures import ThreadPoolExecutor
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import pricing

DSH_HOME = Path.home() / ".dsh"
CREDENTIALS = DSH_HOME / ".credentials.yaml"
SESSIONS_DIR = DSH_HOME / "sessions"
BALANCE_URL = "https://api.deepseek.com/user/balance"
MODELS_URL = "https://api.deepseek.com/models"


# --------------------------------------------------------------------------- #
# 凭据
# --------------------------------------------------------------------------- #
def read_api_key(path: Path = CREDENTIALS) -> str | None:
    """从 credentials.yaml 读取 DeepSeek API key。

    刻意不引入 PyYAML：该文件结构固定，只需 ``refs:`` 段下的一行。
    读不到返回 ``None``（界面应提示用户）。
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    in_refs = False
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        stripped = raw.strip()
        if indent == 0:
            in_refs = stripped.startswith("refs:")
            continue
        if in_refs and stripped.startswith("DEEPSEEK_API_KEY:"):
            value = stripped.split(":", 1)[1].strip().strip("'\"")
            return value or None
    return None


def mask_key(key: str | None) -> str:
    """掩码显示，绝不泄露完整 key。"""
    if not key:
        return "未配置"
    if len(key) <= 12:
        return key[:2] + "…"
    return f"{key[:6]}…{key[-4:]}"


# --------------------------------------------------------------------------- #
# 账户余额
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class BalanceInfo:
    currency: str = "CNY"
    total: float = 0.0
    granted: float = 0.0
    topped_up: float = 0.0

    @property
    def symbol(self) -> str:
        return "¥" if self.currency.upper() == "CNY" else "$"


@dataclass(slots=True)
class Balance:
    ok: bool = False
    available: bool = False
    infos: list[BalanceInfo] = field(default_factory=list)
    error: str = ""

    @property
    def primary(self) -> BalanceInfo | None:
        for info in self.infos:
            if info.currency.upper() == "CNY":
                return info
        return self.infos[0] if self.infos else None


def fetch_balance(api_key: str | None = None, timeout: float = 20.0) -> Balance:
    """查询账户余额。网络失败时返回 ``ok=False`` 而不是抛异常。"""
    import urllib.error
    import urllib.request

    key = api_key or read_api_key()
    if not key:
        return Balance(ok=False, error="未找到 API key（~/.dsh/.credentials.yaml）")

    req = urllib.request.Request(  # noqa: S310 - 固定的 https 官方地址
        BALANCE_URL,
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8")[:200]
        except Exception:
            pass
        return Balance(ok=False, error=f"HTTP {exc.code} {detail}".strip())
    except Exception as exc:
        return Balance(ok=False, error=f"{type(exc).__name__}: {exc}")

    out = Balance(ok=True, available=bool(payload.get("is_available")))
    for item in payload.get("balance_infos") or []:
        try:
            out.infos.append(
                BalanceInfo(
                    currency=str(item.get("currency", "CNY")),
                    total=float(item.get("total_balance", 0) or 0),
                    granted=float(item.get("granted_balance", 0) or 0),
                    topped_up=float(item.get("topped_up_balance", 0) or 0),
                )
            )
        except (TypeError, ValueError):
            continue
    return out


def fetch_models(api_key: str | None = None, timeout: float = 20.0) -> list[str]:
    """查询官方可用模型 id 列表；失败返回空列表。"""
    import urllib.error
    import urllib.request

    key = api_key or read_api_key()
    if not key:
        return []
    req = urllib.request.Request(  # noqa: S310
        MODELS_URL,
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, Exception):
        return []
    return [str(m.get("id")) for m in (payload.get("data") or []) if m.get("id")]


# --------------------------------------------------------------------------- #
# 会话成本聚合
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class ModelUsage:
    """某个模型在一个会话里的用量。"""

    model: str = ""
    input: int = 0
    output: int = 0
    cache_read: int = 0
    reasoning: int = 0
    calls: int = 0
    cost_usd: float = 0.0

    def merge(self, other: "ModelUsage") -> None:
        self.input += other.input
        self.output += other.output
        self.cache_read += other.cache_read
        self.reasoning += other.reasoning
        self.calls += other.calls
        self.cost_usd += other.cost_usd

    @property
    def total_tokens(self) -> int:
        return self.input + self.output + self.cache_read


@dataclass(slots=True)
class SessionCost:
    """一个会话的成本汇总。"""

    session_id: str
    workspace: str
    path: Path
    title: str = ""
    events: int = 0
    started: datetime | None = None
    ended: datetime | None = None
    by_model: dict[str, ModelUsage] = field(default_factory=dict)
    error: str = ""

    @property
    def cost_usd(self) -> float:
        return sum(u.cost_usd for u in self.by_model.values())

    @property
    def calls(self) -> int:
        return sum(u.calls for u in self.by_model.values())

    @property
    def total_tokens(self) -> int:
        return sum(u.total_tokens for u in self.by_model.values())

    @property
    def display_name(self) -> str:
        # 目录名把路径编码成了 --home-alice-code-~7F51~...-- 形式，这里还原成可读路径
        return decode_workspace(self.workspace)


#: 目录名里的非 ASCII 码位转义。注意形式是 ``~XXXX``——前导波浪号同时充当
#: 下一个转义的分隔符（``~767E~5EA6`` = 百度），**没有**结尾波浪号。
#: 早期写成 ``~([0-9A-Fa-f]{4})~`` 会吃掉下一个转义的前导 ``~``，
#: 导致 "百度" 被解成 "百5EA6"。
_ESC_RE = re.compile(r"~([0-9A-Fa-f]{4})")


def _resolve_existing(body: str) -> str | None:
    """借助文件系统把 ``home-alice-Downloads-QuarkPan-master`` 还原成真实路径。

    目录名用 ``-`` 编码 ``/``，而真实路径本身也可能含 ``-``（如 ``QuarkPan-master``），
    这是该编码的固有歧义。这里从根开始做**最长优先**匹配，能在目录仍存在时消歧。
    """
    parts = body.split("-")
    if not parts or parts[0] != "home":
        return None

    def walk(index: int, current: Path) -> Path | None:
        if index >= len(parts):
            return current
        for end in range(len(parts), index, -1):
            candidate = current / "-".join(parts[index:end])
            if candidate.exists():
                found = walk(end, candidate)
                if found is not None:
                    return found
        return None

    found = walk(0, Path("/"))
    return str(found) if found is not None else None


def decode_workspace(name: str) -> str:
    """把会话目录名还原成工作区路径。

    例如::

        --home-alice-code-~7F51~7BA1~7406_V9--  →  /home/alice/code/网盘管理_V9

    先用文件系统消歧；目录已不存在时退化为「``-`` 全部当作 ``/``」，
    此时原路径中带 ``-`` 的目录会显示不准——可接受的降级。
    """
    if not name.startswith("--"):
        return name
    body = name.strip("-")
    body = _ESC_RE.sub(lambda m: chr(int(m.group(1), 16)), body)
    resolved = _resolve_existing(body)
    if resolved is not None:
        return resolved
    if body.startswith("home"):
        return "/" + body.replace("-", "/")
    return body


#: ``_iter_usage`` 的解析结果缓存，键同样是 (路径, 大小, mtime)。
#: 「今日消费」不走 scan_session，而是直接调 _iter_usage 逐事件聚合——没有这层缓存，
#: 它每次都要把同一批文件重新解压解析一遍（实测 10 秒级）。
_USAGE_CACHE: dict[tuple[str, int, int], tuple] = {}


def _iter_usage(
    session_file: Path,
) -> tuple[list[tuple[datetime, dict, str]], int, str, datetime | None]:
    """**单遍**解析会话文件，产出 ``(时刻, usage, 当时的模型)``。

    这里刻意做成一遍：早期版本让 ``session_models()`` 和 ``scan_all_sessions()``
    各自解压+解析一次全部会话文件（实测 1.18s + 1.60s），纯属重复劳动，而且
    两次都是 CPU 密集的 ``json.loads``，会长时间占着 GIL 把 GUI 拖卡。
    现在一遍过：模型从 ``model/selection`` 事件里跟踪，并按事件**发生时刻**归属——
    会话中途换过模型也能正确分桶。
    """
    # 命中缓存就直接返回：文件没变（大小 + mtime 都没变）时结果必然一样
    try:
        st = session_file.stat()
        ckey = (str(session_file), st.st_size, st.st_mtime_ns)
    except OSError:
        ckey = None
    if ckey is not None:
        hit = _USAGE_CACHE.get(ckey)
        if hit is not None:
            return hit

    from compression import zstd

    raw = zstd.decompress(session_file.read_bytes()).decode("utf-8", "replace")
    out: list[tuple[datetime, dict, str]] = []
    events = 0
    title = ""
    started: datetime | None = None
    model = ""
    for line_no, line in enumerate(raw.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        # 周期性让出 GIL：一个大会话有几千条事件，连续 json.loads 会把解释器锁
        # 霸住几百毫秒，界面就是那段时间卡住的。time.sleep(0) 主动让出一次调度，
        # 实测把"今日消费"首次加载的停顿从 294 ms 压到几十毫秒，
        # 而整体耗时几乎不变（多出的开销远小于一次线程切换的收益）。
        if line_no % 400 == 0:
            time.sleep(0)
        events += 1
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        etype = ev.get("type", "")
        t_ms = ev.get("time")
        when = (
            datetime.fromtimestamp(t_ms / 1000, tz=timezone.utc)
            if isinstance(t_ms, (int, float))
            else None
        )
        if started is None and when is not None:
            started = when
        if etype == "session/title":
            data = ev.get("data") or {}
            title = str(data.get("title") or data.get("text") or title)
        elif etype == "model/selection":
            model = str((ev.get("data") or {}).get("model") or model)
        elif etype in ("assistant/message", "compaction/summary"):
            usage = (ev.get("data") or {}).get("usage")
            if isinstance(usage, dict) and when is not None:
                out.append((when, usage, model))
    if ckey is not None:
        _USAGE_CACHE[ckey] = (out, events, title, started)
    return out, events, title, started


#: 会话文件 → 解析结果。键带上 (大小, mtime)，**文件没变就直接复用**。
#: 会话文件是追加写的，内容一变这两个值必变，所以这个缓存是安全的。
#: 为什么需要它：一次全量扫描要解压+解析 116 个文件、约 6.6 秒；而"今日消费"和
#: "会话汇总"是两个不同的聚合，各自都要过一遍同样的文件——没有这层缓存就是**两倍**
#: 的解压解析，两个线程并行还会互相抢 CPU（实测首次加载 24 秒）。
_FILE_CACHE: dict[tuple[str, int, int, str], "SessionCost"] = {}

#: **落盘**的会话汇总缓存。内存缓存在进程重启后就没了，而首次解析 116 个文件要 10 秒——
#: 用户每次打开控制台都要付这个成本。会话文件是追加写的，(大小, mtime) 一变内容必变，
#: 所以拿这两个值当键是安全的；命中就直接跳过解压 + 逐行解析。
_DISK_CACHE_VERSION = 3
_disk_cache_loaded = False


def _disk_cache_path() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache"))
    return base / "dsh-console" / "session-costs.json"


def _load_disk_cache() -> None:
    """把落盘的会话汇总读进内存。只在首次扫描时做一次。"""
    global _disk_cache_loaded
    if _disk_cache_loaded:
        return
    _disk_cache_loaded = True
    try:
        raw = json.loads(_disk_cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if raw.get("version") != _DISK_CACHE_VERSION:
        return
    for key, item in (raw.get("entries") or {}).items():
        try:
            path_s, size_s, mtime_s, hint = key.split("\x00")
            sc = SessionCost(
                session_id=item["session_id"], workspace=item["workspace"],
                path=Path(path_s), title=item.get("title", ""),
                events=int(item.get("events", 0)), error=item.get("error", ""),
            )
            for name, mu in (item.get("by_model") or {}).items():
                sc.by_model[name] = ModelUsage(
                    model=name, input=int(mu.get("input", 0)), output=int(mu.get("output", 0)),
                    cache_read=int(mu.get("cache_read", 0)), reasoning=int(mu.get("reasoning", 0)),
                    calls=int(mu.get("calls", 0)), cost_usd=float(mu.get("cost_usd", 0.0)),
                )
            _FILE_CACHE[(path_s, int(size_s), int(mtime_s), hint)] = sc
        except (KeyError, ValueError, TypeError):
            continue


def _save_disk_cache() -> None:
    """把内存里的会话汇总写回磁盘。失败就算了（缓存而已，不该影响主流程）。"""
    entries: dict[str, dict] = {}
    for (path_s, size, mtime, hint), sc in list(_FILE_CACHE.items())[:5000]:
        entries[f"{path_s}\x00{size}\x00{mtime}\x00{hint}"] = {
            "session_id": sc.session_id, "workspace": sc.workspace,
            "title": sc.title, "events": sc.events, "error": sc.error,
            "by_model": {
                n: {"input": u.input, "output": u.output, "cache_read": u.cache_read,
                    "reasoning": u.reasoning, "calls": u.calls, "cost_usd": u.cost_usd}
                for n, u in sc.by_model.items()
            },
        }
    try:
        path = _disk_cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": _DISK_CACHE_VERSION, "entries": entries}),
                       encoding="utf-8")
        tmp.replace(path)          # 原子替换，避免写一半被读到
    except OSError:
        pass


def scan_session(path: Path, workspace: str, model_hint: str = "") -> SessionCost:
    """统计单个会话的成本。

    带 (大小, mtime) 记忆化：文件没变就直接复用上次结果。
    """
    try:
        st = path.stat()
        key = (str(path), st.st_size, st.st_mtime_ns, model_hint)
    except OSError:
        key = None
    if key is not None:
        hit = _FILE_CACHE.get(key)
        if hit is not None:
            return hit

    sc = SessionCost(session_id=path.parent.name, workspace=workspace, path=path)
    try:
        usages, events, title, started = _iter_usage(path)
    except Exception as exc:  # zstd 损坏 / 权限问题
        sc.error = f"{type(exc).__name__}: {exc}"
        return sc
    sc.events = events
    sc.title = title
    sc.started = started
    for when, usage, model_at in usages:
        model = model_at or model_hint or "deepseek-v4-flash"
        m = ModelUsage(
            model=model,
            input=int(usage.get("inputTokens") or 0),
            output=int(usage.get("outputTokens") or 0),
            cache_read=int(usage.get("cacheReadTokens") or 0),
            reasoning=int(usage.get("reasoningTokens") or 0),
            calls=1,
        )
        # cacheWrite 未在该 usage 结构中单独给出，按 0 处理（与插件一致）
        m.cost_usd = pricing.cost_of(
            {
                "input": m.input,
                "output": m.output,
                "cacheRead": m.cache_read,
                "cacheWrite": 0,
                "reasoning": m.reasoning,
            },
            pricing.price_entry_for(model),
            when,
        )
        bucket = sc.by_model.setdefault(model, ModelUsage(model=model))
        bucket.merge(m)
        sc.ended = when
    if key is not None:
        _FILE_CACHE[key] = sc
    return sc


def scan_all_sessions(models_by_session: dict[str, str] | None = None) -> list[SessionCost]:
    """扫描全部会话，返回按成本降序排列的列表。

    ``models_by_session`` 只是历史兼容参数——模型现在由 :func:`_iter_usage`
    在**同一次解析**中得出，不再需要单独扫一遍（那会让解析量翻倍）。
    """
    models_by_session = models_by_session or {}
    results: list[SessionCost] = []
    if not SESSIONS_DIR.exists():
        return results
    _load_disk_cache()
    jobs: list[tuple[Path, str, str]] = []
    for workspace_dir in sorted(SESSIONS_DIR.iterdir()):
        if not workspace_dir.is_dir():
            continue
        for session_dir in sorted(workspace_dir.iterdir()):
            f = session_dir / "session.v3.jsonl.zstd"
            if not f.exists():
                continue
            jobs.append((f, workspace_dir.name, models_by_session.get(session_dir.name, "")))

    # **并行解析**。每个会话文件都要 zstd 解压 + 逐行 json.loads，116 个文件串行要 9 秒
    # （机器有负载时更久）。解压走的是 C 扩展，会释放 GIL，所以多线程是真有效果的。
    # 线程数取 CPU 核数，最多 8——再多只是互相抢内存带宽。
    workers = min(8, max(2, (os.cpu_count() or 4)))
    if len(jobs) > 4:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(lambda a: scan_session(a[0], a[1], model_hint=a[2]), jobs))
    else:
        results = [scan_session(f, ws, model_hint=hint) for f, ws, hint in jobs]
    results.sort(key=lambda s: s.cost_usd, reverse=True)
    _save_disk_cache()
    return results


_sessions_lock = threading.Lock()
_scan_lock = threading.Lock()      # 保证同一时刻只有一次全量扫描在跑
_sessions_snapshot: "list[SessionCost] | None" = None
_sessions_at: float = 0.0
SESSIONS_TTL = 60.0


def scan_all_sessions_cached(max_age: float = SESSIONS_TTL) -> list[SessionCost]:
    """带 TTL 的全量会话扫描。

    **账单页加载慢的根因就是它没缓存**：`scan_all_sessions()` 会把每个会话的
    `session.v3.jsonl.zstd` 解压 + 逐事件解析一遍，会话一多就是几秒起步，
    而每次切到账单页都重跑一遍。仪表盘早就有 `today_usage_cached`（60 秒 TTL），
    这里补上同款。点「刷新」按钮会显式 invalidate，所以不会看到过期数据。
    """
    global _sessions_snapshot, _sessions_at
    with _sessions_lock:
        snap, at = _sessions_snapshot, _sessions_at
        if snap is not None and (time.monotonic() - at) < max_age:
            return snap
    # ⚠️ **必须串行**：启动预热和"用户点了账单页"会同时调到这里。不串行的话
    # 两个线程各扫一遍 116 个文件，实测首次要 30 秒（比不预热还慢）。
    # 双重检查：等锁的线程醒来先再看一次缓存，多半已经有结果了。
    with _scan_lock:
        with _sessions_lock:
            snap, at = _sessions_snapshot, _sessions_at
            if snap is not None and (time.monotonic() - at) < max_age:
                return snap
        fresh = scan_all_sessions()
        with _sessions_lock:
            _sessions_snapshot, _sessions_at = fresh, time.monotonic()
        return fresh


def invalidate_sessions() -> None:
    global _sessions_snapshot, _sessions_at
    with _sessions_lock:
        _sessions_snapshot, _sessions_at = None, 0.0


def session_models() -> dict[str, str]:
    """从会话事件里取每个会话实际用过的模型（session_id → model）。"""
    from compression import zstd

    out: dict[str, str] = {}
    if not SESSIONS_DIR.exists():
        return out
    for f in SESSIONS_DIR.glob("*/*/session.v3.jsonl.zstd"):
        try:
            raw = zstd.decompress(f.read_bytes()).decode("utf-8", "replace")
        except Exception:
            continue
        model = ""
        for line in raw.splitlines():
            if "model/selection" not in line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("type") == "model/selection":
                model = str((ev.get("data") or {}).get("model") or model)
        if model:
            out[f.parent.name] = model
    return out


# --------------------------------------------------------------------------- #
# 今日消费
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class HourBucket:
    """一小时的用量（0–23）。"""

    hour: int
    cost_usd: float = 0.0
    calls: int = 0
    tokens: int = 0
    peak: bool = False       # 这一小时是否处于峰时段

    @property
    def cost_cny(self) -> float:
        return pricing.cny(self.cost_usd)


@dataclass(slots=True)
class TodayUsage:
    """今日消费明细。"""

    day: str = ""
    cost_usd: float = 0.0
    calls: int = 0
    input: int = 0
    output: int = 0
    cache_read: int = 0
    reasoning: int = 0
    peak_cost_usd: float = 0.0
    offpeak_cost_usd: float = 0.0
    by_model: dict[str, ModelUsage] = field(default_factory=dict)
    by_session: dict[str, float] = field(default_factory=dict)
    hours: list[HourBucket] = field(default_factory=list)
    scanned: int = 0          # 扫了几个会话文件
    error: str = ""

    @property
    def cost_cny(self) -> float:
        return pricing.cny(self.cost_usd)

    @property
    def tokens(self) -> int:
        return self.input + self.output + self.cache_read

    @property
    def peak_cny(self) -> float:
        return pricing.cny(self.peak_cost_usd)

    @property
    def offpeak_cny(self) -> float:
        return pricing.cny(self.offpeak_cost_usd)

    @property
    def busiest_hour(self) -> HourBucket | None:
        used = [h for h in self.hours if h.calls]
        return max(used, key=lambda h: h.cost_usd) if used else None

    @property
    def cache_hit_rate(self) -> float:
        """缓存命中率：cacheRead / (input + cacheRead)。"""
        denom = self.input + self.cache_read
        return (self.cache_read / denom * 100) if denom else 0.0


def today_usage(now: datetime | None = None) -> TodayUsage:
    """汇总**今天**（本地日期）的模型调用花费。

    性能考虑：只扫 ``mtime`` 落在今天及以后的会话文件——会话文件是追加写的，
    今天产生过事件的文件其 mtime 必然在今天，因此这个过滤是安全且大幅省时的
    （全量扫描 1.7 秒，今日过滤后通常只剩少数几个文件）。
    """
    local_now = (now or datetime.now()).astimezone()
    start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_ts = start.timestamp()
    day = start.strftime("%Y-%m-%d")

    out = TodayUsage(day=day, hours=[HourBucket(hour=h) for h in range(24)])
    if not SESSIONS_DIR.exists():
        return out

    for f in SESSIONS_DIR.glob("*/*/session.v3.jsonl.zstd"):
        try:
            if f.stat().st_mtime < start_ts:
                continue
        except OSError:
            continue
        out.scanned += 1
        try:
            usages, _events, _title, _started = _iter_usage(f)
        except Exception as exc:
            out.error = f"{type(exc).__name__}: {exc}"
            continue
        sid = f.parent.name[:12]
        for when, usage, model_at in usages:
            when_local = when.astimezone()
            if when_local < start:
                continue
            model = model_at or "deepseek-v4-flash"
            inp = int(usage.get("inputTokens") or 0)
            outp = int(usage.get("outputTokens") or 0)
            cread = int(usage.get("cacheReadTokens") or 0)
            reason = int(usage.get("reasoningTokens") or 0)
            cost = pricing.cost_of(
                {"input": inp, "output": outp, "cacheRead": cread,
                 "cacheWrite": 0, "reasoning": reason},
                pricing.price_entry_for(model),
                when,
            )
            is_peak = pricing.is_peak_hour(when)

            out.cost_usd += cost
            out.calls += 1
            out.input += inp
            out.output += outp
            out.cache_read += cread
            out.reasoning += reason
            if is_peak:
                out.peak_cost_usd += cost
            else:
                out.offpeak_cost_usd += cost
            out.by_session[sid] = out.by_session.get(sid, 0.0) + cost

            bucket = out.by_model.setdefault(model, ModelUsage(model=model))
            bucket.merge(ModelUsage(model=model, input=inp, output=outp,
                                    cache_read=cread, reasoning=reason,
                                    calls=1, cost_usd=cost))

            hb = out.hours[when_local.hour]
            hb.cost_usd += cost
            hb.calls += 1
            hb.tokens += inp + outp + cread
            hb.peak = is_peak
    return out


#: 今日消费的缓存快照。仪表盘每 3 秒刷一次状态，如果每次都重扫会话文件，
#: 就等于每 3 秒烧掉 ~115ms 的 CPU 去解析几千条 JSON —— 纯浪费，而且会周期性
#: 抢 GIL 让界面发涩。60 秒的 TTL 足够"今日花费"这种量级的实时性。
_today_lock = threading.Lock()
_today_snapshot: "TodayUsage | None" = None
_today_at: float = 0.0
TODAY_TTL = 60.0


def today_usage_cached(max_age: float = TODAY_TTL) -> TodayUsage:
    """带 TTL 的今日消费快照（仪表盘轮询用这个）。"""
    global _today_snapshot, _today_at
    with _today_lock:
        snap, at = _today_snapshot, _today_at
        if snap is not None and (time.monotonic() - at) < max_age:
            return snap
    with _scan_lock:              # 和全量扫描共用一把锁：两者都会解压同一批文件
        with _today_lock:
            snap, at = _today_snapshot, _today_at
            if snap is not None and (time.monotonic() - at) < max_age:
                return snap
        fresh = today_usage()
        with _today_lock:
            _today_snapshot, _today_at = fresh, time.monotonic()
        return fresh


def invalidate_today() -> None:
    global _today_snapshot, _today_at
    with _today_lock:
        _today_snapshot, _today_at = None, 0.0
