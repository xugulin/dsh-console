"""插件市场数据源：npm registry。

DSH 插件生态的事实标准就是 **npm 上的 ``dsh-plugin`` 关键字**（实测 4900+ 个包），
社区那几个"插件市场"本身也都是从 npm 拉数据的。所以这里直接用官方 npm registry，
不依赖任何第三方目录站——它们随时可能下线，npm 不会。

用到的两个官方接口：

* ``GET /-/v1/search?text=keywords:dsh-plugin&size=N``
  一次请求就同时拿到**排名依据**（下载量、score）、**评分依据**
  （``score.detail.popularity/quality/maintenance``，各 0..1）和包元信息。
* ``GET /<name>``
  完整 registry 文档：版本历史、依赖、安装期脚本、README——用于详情页与安全体检。
  这是判断"**宿主半 / 前端半**"的唯一来源：search 接口**不返回** ``dsh`` 清单字段，
  而 npm 上也没有 ``dsh-client``/``dsh-host`` 这类关键字约定（实测 250 个包里 0 个使用）。

因为本机网络很慢（实测约 42 KiB/s），搜索结果**带磁盘缓存**，默认 15 分钟。
单包详情文档约 3.2 秒/个，所以批量体检走**并发**（实测 16 并发降到 0.30 秒/个）。
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

SEARCH_URL = "https://registry.npmjs.org/-/v1/search"
DOC_URL = "https://registry.npmjs.org/{name}"

#: DSH 插件的约定关键字。
PLUGIN_KEYWORD = "dsh-plugin"

CACHE_DIR = Path.home() / ".cache" / "dsh-console"
SEARCH_CACHE = CACHE_DIR / "market-search.json"
DETAIL_CACHE = CACHE_DIR / "market-detail.json"

#: 搜索结果缓存时长（秒）。npm 的下载量/评分是滚动统计，不需要频繁刷新。
SEARCH_TTL = 15 * 60
DETAIL_TTL = 6 * 60 * 60

USER_AGENT = "dsh-console/1.0 (+local)"


# --------------------------------------------------------------------------- #
# 数据模型
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class MarketPlugin:
    """市场里的一个插件。"""

    name: str
    version: str = ""
    description: str = ""
    publisher: str = ""
    maintainers: list[str] = field(default_factory=list)
    license: str = ""
    published: str = ""          # 最近发布时间 ISO
    weekly_downloads: int = 0
    monthly_downloads: int = 0
    dependents: int = 0
    rank_score: float = 0.0      # npm searchScore（综合排序用）
    popularity: float = 0.0      # 0..1
    quality: float = 0.0         # 0..1
    maintenance: float = 0.0     # 0..1
    keywords: list[str] = field(default_factory=list)
    npm_url: str = ""
    homepage: str = ""
    repository: str = ""
    insecure: bool = False

    @property
    def rating_parts(self) -> dict[str, float]:
        """评分构成——全部由**可观测信号**算出，满分 5 分。

        为什么不直接用 npm 的 ``score.detail``：实测本机拉到 250 个包，
        三项全部恒为 ``(1.0, 1.0, 1.0)``，换算成星级就是"人人 5.0 星"，
        这种评分毫无区分度，还不如不给。所以这里改用透明的自算分：

        ==========  ======  ================================================
        分项         满分    依据
        ==========  ======  ================================================
        使用量       2.2     周下载量，``log10`` 刻度（1k≈1.3，100k=2.2）
        活跃度       1.4     最近发布时间（30 天内满分，越久越低）
        生态         0.7     被依赖数
        规范         0.7     是否声明许可证、源码仓库
        ==========  ======  ================================================
        """
        import math

        usage = 0.0
        if self.weekly_downloads > 0:
            usage = min(2.2, math.log10(self.weekly_downloads) / 5.0 * 2.2)

        d = self.age_days
        if d < 0:
            activity = 0.0
        elif d <= 30:
            activity = 1.4
        elif d <= 90:
            activity = 1.15
        elif d <= 180:
            activity = 0.85
        elif d <= 365:
            activity = 0.5
        else:
            activity = 0.2

        eco = min(0.7, math.log10(self.dependents + 1) / 3.0 * 0.7)
        spec = (0.35 if self.license else 0.0) + (0.35 if self.repository else 0.0)
        return {"使用量": usage, "活跃度": activity, "生态": eco, "规范": spec}

    @property
    def rating(self) -> float:
        """0..5 星（控制台自算，见 :attr:`rating_parts`）。"""
        return round(sum(self.rating_parts.values()), 2)

    @property
    def rating_text(self) -> str:
        return f"{self.rating:.1f}"

    @property
    def downloads_text(self) -> str:
        w = self.weekly_downloads
        if w >= 1_000_000:
            return f"{w / 1_000_000:.1f}M"
        if w >= 1_000:
            return f"{w / 1_000:.1f}k"
        return str(w)

    @property
    def age_days(self) -> int:
        return _days_since(self.published)

    @property
    def freshness(self) -> str:
        d = self.age_days
        if d < 0:
            return "—"
        if d == 0:
            return "今天"
        if d < 30:
            return f"{d} 天前"
        if d < 365:
            return f"{d // 30} 个月前"
        return f"{d // 365} 年前"


@dataclass(slots=True)
class SearchPage:
    """一页搜索结果。

    ``total`` 很关键：npm 上带 dsh-plugin 关键字的包**实测有 4927 个**，
    早先只取 250 是因为我把 ``size`` 写成了 250——那不是 npm 的上限
    （``size`` 最大 250，但可以用 ``from`` 翻页）。
    """

    items: list["MarketPlugin"] = field(default_factory=list)
    total: int = 0
    #: 本页**原始**返回条数。技能页会先过滤再返回，翻页偏移必须按原始条数推进，
    #: 否则会反复请求同一段区间。
    raw_count: int = 0
    error: str = ""


@dataclass(slots=True)
class MarketDetail:
    """包详情（详情页与安全体检用）。"""

    name: str = ""
    latest: str = ""
    description: str = ""
    readme: str = ""
    license: str = ""
    homepage: str = ""
    repository: str = ""
    maintainers: list[str] = field(default_factory=list)
    dependencies: dict[str, str] = field(default_factory=dict)
    install_scripts: dict[str, str] = field(default_factory=dict)
    version_count: int = 0
    created: str = ""
    modified: str = ""
    unpacked_mb: float = 0.0
    has_client: bool = False
    has_bundle: bool = False
    error: str = ""

    @property
    def risks(self) -> list[str]:
        """安装前的风险提示（真正会执行代码的只有安装期脚本）。"""
        out: list[str] = []
        if self.install_scripts:
            names = "、".join(sorted(self.install_scripts))
            out.append(f"包含安装期脚本（{names}）——安装时会执行代码，需谨慎")
        if self.version_count <= 1:
            out.append("只有一个发布版本，成熟度未知")
        if 0 <= _days_since(self.created) < 30:
            out.append("发布不足 30 天")
        if not self.repository:
            out.append("未声明源码仓库，无法审计")
        return out

    @property
    def good_signs(self) -> list[str]:
        out: list[str] = []
        if not self.install_scripts:
            out.append("无安装期脚本，装包时不会执行任意代码")
        if self.repository:
            out.append(f"已声明源码仓库：{self.repository}")
        if self.version_count > 10:
            out.append(f"版本迭代 {self.version_count} 次，维护活跃")
        if self.license:
            out.append(f"许可证：{self.license}")
        return out


def _days_since(iso: str) -> int:
    if not iso:
        return -1
    import datetime

    try:
        text = iso.replace("Z", "+00:00")
        dt = datetime.datetime.fromisoformat(text)
        now = datetime.datetime.now(datetime.timezone.utc)
        return max(0, (now - dt).days)
    except Exception:
        return -1


# --------------------------------------------------------------------------- #
# 带磁盘缓存的 HTTP
# --------------------------------------------------------------------------- #
def _load_cache(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(path: Path, data: dict) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _get_json(url: str, timeout: float = 30.0) -> dict:
    req = urllib.request.Request(  # noqa: S310 - 固定的 https 官方地址
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


#: 详情缓存是多线程共享的，读写都要加锁（并发体检会同时命中它）。
_CACHE_LOCK = threading.Lock()

#: 详情文档很大（实测体检 250 个包 → **28.9 MB**），整份 JSON 解析一次要 **0.27 秒**。
#: 列表渲染绝不能碰它——早期版本让列表每行都调一次 ``cached_detail()``，
#: 250 行就是 **69.6 秒**的 GUI 线程阻塞（这正是"界面卡死几分钟"的根因）。
#: 所以另存一份只含清单字段的**紧凑索引**，列表只读它。
INDEX_CACHE = CACHE_DIR / "market-index.json"

_INDEX_MEM: dict[str, dict] | None = None
_INDEX_MTIME: float = -1.0


@dataclass(slots=True)
class Manifest:
    """列表渲染需要的全部信息——刻意不含 README / 版本历史等大字段。"""

    name: str = ""
    latest: str = ""
    has_bundle: bool = False
    has_client: bool = False
    install_scripts: tuple[str, ...] = ()
    version_count: int = 0
    unpacked_mb: float = 0.0

    @property
    def form(self) -> str:
        if self.has_bundle and self.has_client:
            return "两者都有"
        if self.has_bundle:
            return "仅宿主半"
        if self.has_client:
            return "仅前端半"
        return "都没有"


def _manifest_of(d: MarketDetail) -> Manifest:
    return Manifest(
        name=d.name,
        latest=d.latest,
        has_bundle=d.has_bundle,
        has_client=d.has_client,
        install_scripts=tuple(sorted(d.install_scripts)),
        version_count=d.version_count,
        unpacked_mb=d.unpacked_mb,
    )


def _index_view() -> dict[str, dict]:
    """内存里的索引视图，按磁盘文件 mtime 失效。

    只读一次小文件并常驻内存，使 :func:`cached_manifest` 变成 O(1) 字典查找
    （实测 278 ms → 微秒级）。
    """
    global _INDEX_MEM, _INDEX_MTIME
    try:
        mtime = INDEX_CACHE.stat().st_mtime
    except OSError:
        mtime = 0.0
    with _CACHE_LOCK:
        if _INDEX_MEM is None or mtime != _INDEX_MTIME:
            _INDEX_MEM = _load_cache(INDEX_CACHE)
            _INDEX_MTIME = mtime
        return _INDEX_MEM


def _index_put(manifests: list[Manifest]) -> None:
    """把清单写回索引（内存 + 磁盘一起更新）。"""
    global _INDEX_MEM, _INDEX_MTIME
    if not manifests:
        return
    with _CACHE_LOCK:
        idx = dict(_INDEX_MEM if _INDEX_MEM is not None else _load_cache(INDEX_CACHE))
        for m in manifests:
            idx[m.name] = {
                "latest": m.latest,
                "hb": m.has_bundle,
                "hc": m.has_client,
                "sc": list(m.install_scripts),
                "vc": m.version_count,
                "mb": m.unpacked_mb,
            }
        _save_cache(INDEX_CACHE, idx)
        _INDEX_MEM = idx
        try:
            _INDEX_MTIME = INDEX_CACHE.stat().st_mtime
        except OSError:
            _INDEX_MTIME = 0.0


def ensure_index() -> int:
    """确保紧凑索引存在；索引缺失但详情缓存还在时，从中重建一次。

    这是给旧版本做的迁移：老缓存是 28.9 MB 的整份文档，重建索引要 ~0.3 秒
    （**必须在后台线程调用**），换来之后所有列表操作都是 O(1)。
    """
    global _INDEX_MEM, _INDEX_MTIME
    if INDEX_CACHE.exists():
        return len(_index_view())
    with _CACHE_LOCK:
        docs = _load_cache(DETAIL_CACHE)
    if not docs:
        return 0
    manifests = [
        _manifest_of(_parse_detail(n, e.get("data", {}))) for n, e in docs.items()
    ]
    _index_put(manifests)
    return len(manifests)


def cached_manifest(name: str) -> Manifest | None:
    """**只读内存索引**：O(1)，不发网络请求、不读大文件。

    列表与筛选用它；没体检过的返回 ``None``，界面显示"未体检"。
    """
    entry = _index_view().get(name)
    if not entry:
        return None
    return Manifest(
        name=name,
        latest=str(entry.get("latest", "")),
        has_bundle=bool(entry.get("hb")),
        has_client=bool(entry.get("hc")),
        install_scripts=tuple(entry.get("sc") or ()),
        version_count=int(entry.get("vc") or 0),
        unpacked_mb=float(entry.get("mb") or 0),
    )


def cached_detail(name: str) -> MarketDetail | None:
    """只读**大**缓存（详情对话框用）。

    列表渲染**不要**调它——那会重读 28.9 MB 的 JSON；列表请用 :func:`cached_manifest`。
    """
    with _CACHE_LOCK:
        entry = _load_cache(DETAIL_CACHE).get(name)
    if not entry:
        return None
    return _parse_detail(name, entry.get("data", {}))


def audit_many(
    names: list[str],
    workers: int = 16,
    on_progress: "Callable[[int, int, str], None] | None" = None,
    should_cancel: "Callable[[], bool] | None" = None,
) -> dict[str, Manifest]:
    """并发拉取多个包的详情，**只保留清单字段**（宿主半/前端半、安装期脚本、版本数）。

    实测：单线程 3.19 秒/个，**16 并发 0.30–0.9 秒/个**（urllib 在 I/O 时释放 GIL，
    线程对这个负载有效）。

    ⚠️ **为什么不缓存原始文档**：早期版本把每个包的完整注册表文档攒进一个 JSON
    缓存文件，250 个包就是 **28.9 MB**。问题不在于占磁盘，而在于**写回时要
    ``json.dumps`` 这 29 MB，会长时间独占 GIL** —— 实测造成 **11 秒的 GUI 冻结**。
    而且每次列表渲染还会重读整个文件（278 ms × 250 行 = 69.6 秒）。

    现在改为：解析工作在各 worker 线程里就地完成，只把几十 KB 的**紧凑清单**
    一次性写回；原始文档直接丢弃。要 README 时由 :func:`detail` 单独按需拉取。
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    progress = on_progress or (lambda *_: None)
    known = _index_view()
    todo = [n for n in names if n not in known]

    manifests: list[Manifest] = []
    total = len(todo)
    if total:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            futures = {
                ex.submit(_get_json, DOC_URL.format(name=urllib.parse.quote(n, safe="@"))): n
                for n in todo
            }
            for done, fut in enumerate(as_completed(futures), 1):
                if should_cancel is not None and should_cancel():
                    for f in futures:
                        f.cancel()
                    break
                name = futures[fut]
                try:
                    data = fut.result()
                except Exception:
                    data = None
                if isinstance(data, dict) and data:
                    # 就地解析成小对象，原始文档随即被回收，不进任何大缓存
                    manifests.append(_manifest_of(_parse_detail(name, data)))
                progress(done, total, name)
        _index_put(manifests)

    # 已在索引里的也一并返回，调用方拿到的是"全部可知清单"
    out: dict[str, Manifest] = {}
    for n in names:
        m = cached_manifest(n)
        if m is not None:
            out[n] = m
    return out


# --------------------------------------------------------------------------- #
# 搜索 / 排名
# --------------------------------------------------------------------------- #
#: npm 搜索单页上限（超过会被服务端截断）。
SEARCH_MAX_SIZE = 250


def search_page(
    query: str = "",
    size: int = 60,
    *,
    offset: int = 0,
    use_cache: bool = True,
    keyword: str = PLUGIN_KEYWORD,
    keyword_content: str = "",
) -> SearchPage:
    """搜索一页（支持 ``from`` 翻页），返回 ``SearchPage``（含 total）。

    翻页是"滑到底继续加载"的基础：``from`` 与缓存键都带上偏移量，
    因此每一页各自缓存、重复滚动不会重复请求。
    """
    text = f"keywords:{keyword}"
    if keyword_content:
        text += f" {keyword_content}"
    if query.strip():
        text = f"{query.strip()} {text}"

    size = max(1, min(int(size), SEARCH_MAX_SIZE))
    offset = max(0, int(offset))
    key = f"{text}|size={size}|from={offset}"

    cache = _load_cache(SEARCH_CACHE)
    entry = cache.get(key)
    if use_cache and entry and (time.time() - entry.get("at", 0)) < SEARCH_TTL:
        data = entry.get("data", {})
        parsed = _parse_search(data)
        return SearchPage(parsed, int(data.get("total") or 0), raw_count=len(parsed))

    url = f"{SEARCH_URL}?text={urllib.parse.quote(text)}&size={size}&from={offset}"
    try:
        data = _get_json(url)
    except urllib.error.HTTPError as exc:
        return SearchPage([], 0, f"npm 搜索失败：HTTP {exc.code}", raw_count=0)
    except Exception as exc:
        if entry:  # 网络不通时退回缓存（哪怕过期），总比空列表强
            data = entry.get("data", {})
            parsed = _parse_search(data)
            return SearchPage(
                parsed, int(data.get("total") or 0),
                f"网络不可用，显示缓存结果（{type(exc).__name__}）",
                raw_count=len(parsed),
            )
        return SearchPage([], 0, f"{type(exc).__name__}: {exc}", raw_count=0)

    cache[key] = {"at": time.time(), "data": data}
    _save_cache(SEARCH_CACHE, cache)
    parsed = _parse_search(data)
    return SearchPage(parsed, int(data.get("total") or 0), raw_count=len(parsed))


def search(
    query: str = "",
    size: int = SEARCH_MAX_SIZE,
    *,
    use_cache: bool = True,
    keyword: str = PLUGIN_KEYWORD,
    keyword_content: str = "",
) -> tuple[list[MarketPlugin], str]:
    """兼容旧签名：只取第一页，返回 ``(列表, 错误)``。"""
    page = search_page(
        query, size, offset=0, use_cache=use_cache,
        keyword=keyword, keyword_content=keyword_content,
    )
    return page.items, page.error


def _parse_search(data: dict) -> list[MarketPlugin]:
    out: list[MarketPlugin] = []
    for obj in data.get("objects", []):
        pkg = obj.get("package") or {}
        score = obj.get("score") or {}
        detail = score.get("detail") or {}
        dl = obj.get("downloads") or {}
        links = pkg.get("links") or {}
        out.append(
            MarketPlugin(
                name=str(pkg.get("name", "")),
                version=str(pkg.get("version", "")),
                description=str(pkg.get("description", "") or ""),
                publisher=str(pkg.get("publisher", {}).get("username", "") or ""),
                maintainers=[m.get("username", "") for m in (pkg.get("maintainers") or [])],
                license=str(pkg.get("license", "") or ""),
                published=str(pkg.get("date", "") or ""),
                weekly_downloads=int(dl.get("weekly") or 0),
                monthly_downloads=int(dl.get("monthly") or 0),
                dependents=int(obj.get("dependents") or 0),
                rank_score=float(score.get("final") or 0.0),
                popularity=float(detail.get("popularity") or 0.0),
                quality=float(detail.get("quality") or 0.0),
                maintenance=float(detail.get("maintenance") or 0.0),
                keywords=[str(k) for k in (pkg.get("keywords") or [])],
                npm_url=str(links.get("npm", "") or ""),
                homepage=str(links.get("homepage", "") or ""),
                repository=str(links.get("repository", "") or ""),
                insecure=bool((obj.get("flags") or {}).get("insecure")),
            )
        )
    return out


SORTS: dict[str, tuple[str, str]] = {
    "downloads": ("下载量", "周下载量（默认排名依据）"),
    "rating": ("评分", "npm 受欢迎度/质量/维护度三项均值"),
    "updated": ("最近更新", "最近发布时间的倒序"),
    "name": ("名称", "按包名字母序"),
}


#: 认定一个包属于"技能类"的关键字。npm 上没有统一的 DSH 技能关键字
#: （实测 `keywords:dsh-skill` 只有 9 个、`keywords:skill-hub` 只有 8 个），
#: 所以用「DSH 插件集合 + skill 文本查询」拿到候选，再用这组关键字判定。
SKILL_KEYWORDS = frozenset({
    "skill", "skills", "agent-skills", "agent-skill", "dsh-skill", "dsh-skills",
    "skill-hub", "skill-manager", "skill-extension", "skill-catalog", "skillhub",
    "skill-crystallization", "skill.md", "browserskill",
})


def looks_like_skill(p: MarketPlugin) -> bool:
    """判定是否技能类。

    只认「技能关键字」或「包名含 skill」，**不认说明文字**——说明里出现 skill
    的多半只是"支持技能"之类的一笔带过，实测会把搜索插件、MCP 连接器都卷进来
    （179 个 vs 125 个，后者精确得多）。
    """
    kws = {k.lower() for k in p.keywords}
    return bool(kws & SKILL_KEYWORDS) or "skill" in p.name.lower()


def search_skills_page(
    query: str = "", size: int = 60, *, offset: int = 0, use_cache: bool = True
) -> SearchPage:
    """技能市场的分页版本。

    注意：技能是**先按 skill 关键词取候选、再本地过滤**，所以一页原始结果
    过滤后剩下的可能不足，界面需要按"已过滤条数"决定是否继续翻页。
    ``total`` 给的是**未过滤**的总数，作为翻页上限的参考。
    """
    page = search_page(
        query, size, offset=offset, use_cache=use_cache, keyword_content="skill"
    )
    return SearchPage(
        items=[p for p in page.items if looks_like_skill(p)],
        total=page.total,
        raw_count=page.raw_count,
        error=page.error,
    )


def search_skills(
    query: str = "", size: int = SEARCH_MAX_SIZE, *, use_cache: bool = True
) -> tuple[list[MarketPlugin], str]:
    """搜 DSH 技能类插件。

    基础查询用 ``keywords:dsh-plugin skill``——实测在 DSH 插件集合内按 skill 排序后，
    本页 250 条里有 **179 条**是技能类（对比 ``keywords:dsh-skill`` 全站只有 9 条），
    是覆盖最好的取法。随后再用 :func:`looks_like_skill` 过滤掉误入的包。
    """
    page = search_skills_page(query, size, use_cache=use_cache)
    return page.items, page.error


def sort_plugins(items: list[MarketPlugin], key: str) -> list[MarketPlugin]:
    if key == "rating":
        return sorted(items, key=lambda p: (p.rating, p.weekly_downloads), reverse=True)
    if key == "updated":
        return sorted(items, key=lambda p: p.published, reverse=True)
    if key == "name":
        return sorted(items, key=lambda p: p.name.lower())
    # downloads（默认）
    return sorted(items, key=lambda p: p.weekly_downloads, reverse=True)


# --------------------------------------------------------------------------- #
# 详情
# --------------------------------------------------------------------------- #
#: 单个包的**精简**详情缓存目录。一个包一个文件（约几 KB～几十 KB），
#: 写入代价极小——不像原来那个 29 MB 的整份 JSON，写一次要 ``json.dumps`` 几秒。
PKG_CACHE_DIR = CACHE_DIR / "pkg"


def _pkg_cache_path(name: str) -> Path:
    import hashlib

    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:16]
    return PKG_CACHE_DIR / f"{digest}.json"


def _detail_to_json(d: MarketDetail) -> dict:
    return {
        "name": d.name,
        "latest": d.latest,
        "description": d.description,
        "readme": d.readme,
        "license": d.license,
        "homepage": d.homepage,
        "repository": d.repository,
        "maintainers": d.maintainers,
        "dependencies": d.dependencies,
        "install_scripts": d.install_scripts,
        "version_count": d.version_count,
        "created": d.created,
        "modified": d.modified,
        "unpacked_mb": d.unpacked_mb,
        "has_client": d.has_client,
        "has_bundle": d.has_bundle,
    }


def _detail_from_json(name: str, raw: dict) -> MarketDetail:
    return MarketDetail(
        name=name,
        latest=str(raw.get("latest", "")),
        description=str(raw.get("description", "")),
        readme=str(raw.get("readme", "")),
        license=str(raw.get("license", "")),
        homepage=str(raw.get("homepage", "")),
        repository=str(raw.get("repository", "")),
        maintainers=list(raw.get("maintainers") or []),
        dependencies=dict(raw.get("dependencies") or {}),
        install_scripts=dict(raw.get("install_scripts") or {}),
        version_count=int(raw.get("version_count") or 0),
        created=str(raw.get("created", "")),
        modified=str(raw.get("modified", "")),
        unpacked_mb=float(raw.get("unpacked_mb") or 0),
        has_client=bool(raw.get("has_client")),
        has_bundle=bool(raw.get("has_bundle")),
    )


def detail(name: str, *, use_cache: bool = True) -> MarketDetail:
    """取一个包的详情（含 README）。**按需、单包**，并写进小文件缓存。"""
    path = _pkg_cache_path(name)
    if use_cache:
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
            if (time.time() - entry.get("at", 0)) < DETAIL_TTL:
                return _detail_from_json(name, entry.get("d", {}))
        except Exception:
            pass

    try:
        data = _get_json(DOC_URL.format(name=urllib.parse.quote(name, safe="@")))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return MarketDetail(name=name, error="npm 上不存在这个包")
        return MarketDetail(name=name, error=f"HTTP {exc.code}")
    except Exception as exc:
        return MarketDetail(name=name, error=f"{type(exc).__name__}: {exc}")

    parsed = _parse_detail(name, data)
    try:
        PKG_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"at": time.time(), "d": _detail_to_json(parsed)}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass
    _index_put([_manifest_of(parsed)])
    return parsed


def _parse_detail(name: str, data: dict) -> MarketDetail:
    if not data:
        return MarketDetail(name=name, error="无数据")
    latest = (data.get("dist-tags") or {}).get("latest", "")
    ver = (data.get("versions") or {}).get(latest, {}) or {}
    dsh = ver.get("dsh") or {}
    readme = str(data.get("readme") or ver.get("readme") or "")
    times = data.get("time") or {}
    return MarketDetail(
        name=name,
        latest=latest,
        description=str(data.get("description") or ver.get("description") or ""),
        readme=readme,
        license=str(data.get("license") or ver.get("license") or ""),
        homepage=str(data.get("homepage") or ""),
        repository=str((data.get("repository") or {}).get("url", "") or ""),
        maintainers=[m.get("name", "") for m in (data.get("maintainers") or [])],
        dependencies=dict(ver.get("dependencies") or {}),
        # 只有 preinstall / install / postinstall 会在**装包时**执行。
        # prepare / prepack 是发布前跑的，registry 安装不会触发。
        install_scripts={
            k: v for k, v in (ver.get("scripts") or {}).items()
            if k in ("preinstall", "install", "postinstall")
        },
        version_count=len(data.get("versions") or {}),
        created=str(times.get("created", "")),
        modified=str(times.get("modified", "")),
        unpacked_mb=round(int((ver.get("dist") or {}).get("unpackedSize") or 0) / 1048576, 2),
        has_client=bool(dsh.get("client")),
        has_bundle=bool(dsh.get("bundle")),
    )


_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_HTML_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")


def sanitize_readme(text: str) -> str:
    """把 npm README 清洗成 Qt 的 Markdown 解析器能正确处理的形式。

    **实测踩坑**：dshmarket 的 README 开头是一段裸 HTML
    （``<p align="center"><img src="assets/logo.svg"></p>``）。Qt 的
    ``setMarkdown`` 遇到它就**大量丢内容**——6745 字的 README 只渲染出 **542 字、
    0 个超链接**（标题、链接文字全没了）。剥掉 HTML 标签后恢复正常：
    **5167 字、22 个链接**。

    顺带去掉图片语法：QTextBrowser 加载不了远程图片，留着只会变成一排破图占位。
    """
    if not text:
        return ""
    out = _HTML_COMMENT_RE.sub("", text)
    out = _HTML_TAG_RE.sub("", out)
    out = _MD_IMAGE_RE.sub("", out)
    out = re.sub(r"\n{3,}", "\n\n", out)  # 压掉因此产生的连续空行
    return out.strip()


def latest_versions(names: list[str], workers: int = 16) -> dict[str, str]:
    """批量取最新版本（用于判断"可更新"）。

    并发拉取——原先逐个查，实测 12 个包要 38 秒；同批并发只需几秒。
    """
    if not names:
        return {}
    details = audit_many(names, workers=workers)
    return {n: d.latest for n, d in details.items() if d.latest}
