"""官方插件目录（awesome-dsh-plugin）——控制台的第二个市场源。

为什么需要它
------------
npm 搜索一次最多返回 250 条（``size`` 上限），而 DSH 生态有官方**插件目录**：

* 地址 ``https://awesome-dsh-plugin.com/plugins.json``
* 实测：**3632 个插件 / 23 个分类**，3.2 MB（gzip 后约 890 KB，本机下载约 47 秒）
* 字段比 npm 搜索丰富：**中英文描述**、分类、星标、下载量、现成的安装命令

这正是"市场源太少"的症结——之前只用了 npm 搜索那一条路。

目录同时发布成 npm 包 ``dsh-plugin-catalog``，所以配了国内镜像时可以从镜像取
（与 dshmarket 的策略一致：国内档先试 npm 镜像，再回退官方站点）。

因为文件大、下载慢，**结果落盘缓存**（默认 6 小时），且解析出的条目也在内存里
按 mtime 失效——列表滚动/筛选绝不能反复解析 3 MB JSON。
"""

from __future__ import annotations

import io
import json
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from . import sources

CATALOG_CACHE = sources.CONFIG_DIR.parent / "dsh-console" / "plugin-catalog.json"
CATALOG_TTL = 6 * 60 * 60
USER_AGENT = "dsh-console/1.0 (+local)"

_MEM: dict | None = None
_MEM_MTIME: float = -1.0


@dataclass(slots=True)
class CatalogPlugin:
    """目录里的一个插件。"""

    name: str
    owner: str = ""
    repo_url: str = ""
    page: str = ""
    category: str = ""
    description_en: str = ""
    description_zh: str = ""
    npm: str = ""
    version: str = ""
    stars: int = 0
    downloads: int = 0
    install: str = ""
    added: str = ""

    @property
    def pkg(self) -> str:
        """真正的安装目标：优先 npm 包名，否则用 name。"""
        return self.npm or self.name

    @property
    def desc(self) -> str:
        """描述优先中文（目录本身就提供双语）。"""
        return self.description_zh or self.description_en or ""

    @property
    def short_desc(self) -> str:
        d = self.desc.replace("\n", " ")
        return d[:120] + "…" if len(d) > 120 else d

    @property
    def downloads_text(self) -> str:
        n = self.downloads
        if n >= 1_000_000:
            return f"{n / 1_000_000:.1f}M"
        if n >= 1_000:
            return f"{n / 1_000:.1f}k"
        return str(n)


def _get(url: str, timeout: float, accept: str = "application/json") -> bytes:
    req = urllib.request.Request(  # noqa: S310
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": accept,
            "Accept-Encoding": "gzip, identity",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        raw = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            import gzip

            raw = gzip.decompress(raw)
        return raw


def _fetch_url(url: str, timeout: float) -> dict:
    return json.loads(_get(url, timeout).decode("utf-8"))


def _fetch_npm_package(pkg: str, registry: str, timeout: float) -> dict:
    """从 npm（可走镜像）取目录包，解出其中的 plugins.json。"""
    base = registry.rstrip("/")
    packument = json.loads(
        _get(f"{base}/{urllib.parse.quote(pkg, safe='@')}", timeout).decode("utf-8")
    )
    latest = (packument.get("dist-tags") or {}).get("latest", "")
    tarball = ((packument.get("versions") or {}).get(latest, {}).get("dist") or {}).get("tarball")
    if not tarball:
        raise RuntimeError("包的元数据里没有 tarball 地址")
    blob = _get(tarball, timeout, accept="application/octet-stream")
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
        member = next(
            (m for m in tf.getmembers() if m.name.endswith("plugins.json") and m.isfile()),
            None,
        )
        if member is None:
            raise RuntimeError("包里没有 plugins.json")
        return json.loads(tf.extractfile(member).read().decode("utf-8"))  # type: ignore[union-attr]


def _load_cache() -> dict:
    try:
        return json.loads(CATALOG_CACHE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(url: str, data: dict) -> None:
    try:
        CATALOG_CACHE.parent.mkdir(parents=True, exist_ok=True)
        CATALOG_CACHE.write_text(
            json.dumps({"at": time.time(), "url": url, "data": data}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass


def fetch_catalog(
    url_or_pkg: str | None = None,
    *,
    cfg: sources.SourcesConfig | None = None,
    timeout: float = 180.0,
    use_cache: bool = True,
    force: bool = False,
) -> tuple[dict, str]:
    """取目录原始 JSON，返回 ``(数据, 错误)``。

    支持两种写法：``https://…/plugins.json`` 或 ``npm:包名``。
    """
    cfg = cfg or sources.load()
    target = url_or_pkg or (cfg.effective_catalog()[0][0] if cfg.effective_catalog()[0] else sources.CATALOG_OFFICIAL)
    registry, _ = cfg.effective_registry()

    cache = _load_cache()
    if (
        not force
        and use_cache
        and cache.get("url") == target
        and (time.time() - cache.get("at", 0)) < CATALOG_TTL
        and cache.get("data")
    ):
        return cache["data"], ""

    try:
        if target.startswith("npm:"):
            data = _fetch_npm_package(target[4:], registry, timeout)
        else:
            data = _fetch_url(cfg.proxied(target), timeout)
    except urllib.error.HTTPError as exc:
        if cache.get("data"):
            return cache["data"], f"HTTP {exc.code}，已回退缓存"
        return {}, f"HTTP {exc.code}"
    except Exception as exc:
        if cache.get("data"):
            return cache["data"], f"{type(exc).__name__}，已回退缓存"
        return {}, f"{type(exc).__name__}: {exc}"

    _save_cache(target, data)
    return data, ""


def parse(data: dict) -> tuple[list[CatalogPlugin], dict]:
    """把原始 JSON 解析成条目列表与元信息。"""
    meta = {
        "name": data.get("name", ""),
        "url": data.get("url", ""),
        "source": data.get("source", ""),
        "updated": data.get("updated", ""),
        "count": int(data.get("count") or 0),
        "categories": dict(data.get("categories") or {}),
    }
    out: list[CatalogPlugin] = []
    for p in data.get("plugins") or []:
        if not isinstance(p, dict):
            continue
        d = p.get("description") or {}
        if isinstance(d, str):
            d = {"en": d}
        out.append(
            CatalogPlugin(
                name=str(p.get("name") or ""),
                owner=str(p.get("owner") or ""),
                repo_url=str(p.get("url") or ""),
                page=str(p.get("page") or ""),
                category=str(p.get("category") or ""),
                description_en=str(d.get("en") or ""),
                description_zh=str(d.get("zh") or ""),
                npm=str(p.get("npm") or ""),
                version=str(p.get("version") or ""),
                stars=int(p.get("stars") or 0),
                downloads=int(p.get("downloads") or 0),
                install=str(p.get("install") or ""),
                added=str(p.get("added") or ""),
            )
        )
    return out, meta


def load(
    *, cfg: sources.SourcesConfig | None = None, force: bool = False
) -> tuple[list[CatalogPlugin], dict, str]:
    """取目录条目（带内存缓存，按磁盘文件 mtime 失效）。"""
    global _MEM, _MEM_MTIME
    try:
        mtime = CATALOG_CACHE.stat().st_mtime
    except OSError:
        mtime = 0.0

    if not force and _MEM is not None and mtime == _MEM_MTIME:
        return _MEM["items"], _MEM["meta"], ""

    data, err = fetch_catalog(cfg=cfg, force=force)
    if not data:
        return [], {}, err
    items, meta = parse(data)
    _MEM = {"items": items, "meta": meta}
    try:
        _MEM_MTIME = CATALOG_CACHE.stat().st_mtime
    except OSError:
        _MEM_MTIME = 0.0
    return items, meta, err


def cached_age_text() -> str:
    """缓存的新鲜度文案。"""
    cache = _load_cache()
    at = cache.get("at")
    if not at:
        return "未缓存"
    days = (time.time() - at) / 86400
    if days < 1 / 24:
        return f"{int((time.time() - at) / 60)} 分钟前"
    if days < 1:
        return f"{int(days * 24)} 小时前"
    return f"{int(days)} 天前"


#: 排序方式（目录模式）
CATALOG_SORTS: dict[str, str] = {
    "downloads": "下载量",
    "stars": "星标",
    "added": "最新收录",
    "name": "名称",
}


def sort_items(items: list[CatalogPlugin], key: str) -> list[CatalogPlugin]:
    if key == "stars":
        return sorted(items, key=lambda p: (p.stars, p.downloads), reverse=True)
    if key == "added":
        return sorted(items, key=lambda p: p.added, reverse=True)
    if key == "name":
        return sorted(items, key=lambda p: p.name.lower())
    return sorted(items, key=lambda p: (p.downloads, p.stars), reverse=True)


def filter_items(
    items: list[CatalogPlugin],
    query: str = "",
    category: str = "",
) -> list[CatalogPlugin]:
    out = items
    if category and category != "全部":
        out = [p for p in out if p.category == category]
    q = query.strip().lower()
    if q:
        out = [
            p
            for p in out
            if q in p.name.lower()
            or q in p.owner.lower()
            or q in p.desc.lower()
            or q in p.pkg.lower()
        ]
    return out
