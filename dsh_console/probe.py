"""源探测：自动发现候选数据源，并按**网速 / 资源数量 / 评分**排序。

为什么需要它
------------
数据源不是"配置一次就永远正确"的东西：公共 GitHub 代理会失效、npm 镜像的
响应速度随时间和线路变化、官方目录站点也可能临时不可达。与其让用户手工
一个个试，不如**并发探测**一遍，用实测数据排序，再一键采用最优。

三个维度（对应你说的"网速/资源数量/评分"）：

* **网速** —— 小请求测延迟（TTFB），有响应体时再算吞吐。多个候选各测一次。
* **资源数量** —— 这个源能提供多少东西：npm 搜索的 ``total``、目录的 ``count``
  、镜像能否取到包。
* **评分** —— 0–100 的可信度综合分：可达性 + 延迟 + 资源量 + 是否官方源。
  刻意做成可解释的加权分（各项权重写在 :data:`WEIGHTS` 里），而不是黑箱排名。

探测全部在后台线程池里并发跑，且**单项超时很短**——一个挂掉的代理不该让
整个探测卡住。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from . import sources

USER_AGENT = "dsh-console/1.0 (+local)"

#: 评分的权重（合计 100），做成常量便于解释与调整。
WEIGHTS = {
    "reachable": 30.0,   # 能不能连上（最重要：连不上其它都无意义）
    "latency": 25.0,     # 延迟越低越好
    "volume": 30.0,      # 能提供多少资源
    "official": 15.0,    # 官方/权威来源加分
}

#: 候选 npm registry（国内可用的公共镜像都列上，探测后择优）。
NPM_CANDIDATES: tuple[tuple[str, str], ...] = (
    ("https://registry.npmjs.org", "npm 官方"),
    ("https://mirrors.cloud.tencent.com/npm", "腾讯云镜像"),
    ("https://registry.npmmirror.com", "npmmirror（阿里）"),
    ("https://mirrors.huaweicloud.com/repository/npm", "华为云镜像"),
)

#: 候选 GitHub 加速前缀（空串代表"直连/不使用"）。
PROXY_CANDIDATES: tuple[tuple[str, str], ...] = (
    ("", "直连（不使用加速）"),
    ("https://gh-proxy.com", "gh-proxy.com"),
    ("https://ghfast.top", "ghfast.top"),
    ("https://ghproxy.net", "ghproxy.net"),
    ("https://mirror.ghproxy.com", "mirror.ghproxy.com"),
)

#: 候选目录源。
CATALOG_CANDIDATES: tuple[tuple[str, str], ...] = (
    (sources.CATALOG_OFFICIAL, "官方目录（站点）"),
    (f"npm:{sources.CATALOG_NPM_PKG}", "官方目录（npm 包）"),
)

#: 用于探测 GitHub 加速的一个小文件（raw 内容，体积小、稳定）。
PROBE_RAW = "https://raw.githubusercontent.com/anthropics/skills/main/README.md"
#: 探测 npm 用的包（体积小、必然存在）。
PROBE_PKG = "dsh-plugin-catalog"


@dataclass(slots=True)
class ProbeResult:
    """一个候选源的探测结果。"""

    kind: str                 # npm | proxy | catalog
    value: str                # registry 前缀 / 代理前缀 / 目录地址
    label: str = ""
    ok: bool = False
    latency_ms: float = 0.0   # TTFB
    total_ms: float = 0.0
    bytes_read: int = 0
    volume: int = 0           # 资源数量（包数/条目数）
    official: bool = False
    note: str = ""
    score: float = 0.0

    @property
    def speed_text(self) -> str:
        if not self.ok:
            return "—"
        if self.bytes_read and self.total_ms > self.latency_ms:
            kbps = self.bytes_read / 1024 / max(0.001, (self.total_ms - self.latency_ms) / 1000)
            return f"{self.latency_ms:.0f} ms · {kbps:.0f} KB/s"
        return f"{self.latency_ms:.0f} ms"

    @property
    def volume_text(self) -> str:
        return f"{self.volume:,}" if self.volume else "—"


def _score(r: ProbeResult) -> float:
    """可解释的加权评分（0–100）。"""
    if not r.ok:
        return 0.0
    total = WEIGHTS["reachable"]
    # 延迟：0–300ms 线性给分（越低越好，超过 300ms 得 0）
    if r.latency_ms > 0:
        total += WEIGHTS["latency"] * max(0.0, 1.0 - min(r.latency_ms, 300.0) / 300.0)
    # 资源量：以 4000 条为满分参考（官方目录实测 3632）
    if r.volume > 0:
        total += WEIGHTS["volume"] * min(1.0, r.volume / 4000.0)
    else:
        total += WEIGHTS["volume"] * 0.5   # 不提供数量的源给一半，不算失败
    if r.official:
        total += WEIGHTS["official"]
    return round(total, 1)


def _http_probe(url: str, timeout: float, read_bytes: int = 0) -> tuple[bool, float, float, int, str]:
    """发一个 GET，返回 ``(成功, TTFB ms, 总耗时 ms, 读到字节, 说明)``。"""
    req = urllib.request.Request(  # noqa: S310
        url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"}
    )
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            ttfb = (time.perf_counter() - t0) * 1000
            if read_bytes:
                data = resp.read(read_bytes)
                n = len(data)
            else:
                resp.read(64)
                n = 64
            total = (time.perf_counter() - t0) * 1000
            return True, ttfb, total, n, ""
    except urllib.error.HTTPError as exc:
        # 4xx/5xx 也算"能连上"——对 registry 来说 404 说明服务是活的
        ttfb = (time.perf_counter() - t0) * 1000
        return exc.code < 500, ttfb, ttfb, 0, f"HTTP {exc.code}"
    except Exception as exc:
        return False, 0.0, (time.perf_counter() - t0) * 1000, 0, type(exc).__name__


def probe_npm(registry: str, label: str, timeout: float = 8.0) -> ProbeResult:
    """探一个 npm registry：能否取到包元数据 + 搜索能返回多少条。"""
    r = ProbeResult(kind="npm", value=registry, label=label,
                    official=("npmjs.org" in registry))
    ok, ttfb, total, n, note = _http_probe(
        f"{registry.rstrip('/')}/{PROBE_PKG}", timeout, read_bytes=200_000
    )
    r.ok, r.latency_ms, r.total_ms, r.bytes_read, r.note = ok, ttfb, total, n, note
    if ok:
        # 资源数量：再打一次轻量的搜索接口，读 total（这才是"这个源有多少东西"）
        try:
            r.volume = int(json.loads(_fetch(registry, timeout)).get("total") or 0)
        except Exception:
            r.volume = 0
        if r.volume:
            r.note = f"搜索接口可用"
        else:
            # 这一点很关键：镜像往往能装包却**不提供搜索 API**。
            # 探测结果必须说清楚，否则用户切过去会发现「市场列表拉不出来」。
            r.note = "搜索接口不可用（仅可用于安装包，市场列表拉不出来）"
    r.score = _score(r)
    return r


def _fetch(registry: str, timeout: float) -> bytes:
    req = urllib.request.Request(  # noqa: S310
        f"{registry.rstrip('/')}/-/v1/search?"
        + urllib.parse.urlencode({"text": "keywords:dsh-plugin", "size": 1}),
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return resp.read()


def probe_proxy(proxy: str, label: str, timeout: float = 8.0) -> ProbeResult:
    """探一个 GitHub 加速前缀：取一小段 raw 内容。"""
    r = ProbeResult(kind="proxy", value=proxy, label=label,
                    official=(proxy == ""), volume=0)
    if proxy == "":
        ok, ttfb, total, n, note = _http_probe(PROBE_RAW, timeout, read_bytes=100_000)
    else:
        ok, ttfb, total, n, note = _http_probe(f"{proxy}/{PROBE_RAW}", timeout, read_bytes=100_000)
    r.ok, r.latency_ms, r.total_ms, r.bytes_read, r.note = ok, ttfb, total, n, note
    r.score = _score(r)
    return r


def probe_catalog(target: str, label: str, timeout: float = 25.0) -> ProbeResult:
    """探一个目录源：拿到就顺便读出条目数（这就是它的"资源数量"）。"""
    r = ProbeResult(kind="catalog", value=target, label=label,
                    official=(target == sources.CATALOG_OFFICIAL))
    if target.startswith("npm:"):
        registry, _ = sources.load().effective_registry()
        base = registry.rstrip("/")
        try:
            t0 = time.perf_counter()
            req = urllib.request.Request(  # noqa: S310
                f"{base}/{urllib.parse.quote(target[4:], safe='@')}",
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                raw = resp.read()
            r.latency_ms = (time.perf_counter() - t0) * 1000
            r.total_ms = r.latency_ms
            r.bytes_read = len(raw)
            packument = json.loads(raw.decode("utf-8"))
            latest = (packument.get("dist-tags") or {}).get("latest", "")
            r.ok = bool(latest)
            r.note = f"npm 包 @{latest}" if r.ok else "取不到 dist-tags"
            # 包体大小可视为它的"资源量"（同一份目录，这里用包内文件量近似）
            r.volume = int(
                ((packument.get("versions") or {}).get(latest, {}).get("dist") or {}).get(
                    "unpackedSize"
                )
                or 0
            ) // 1024
            if r.volume:
                r.note += f" · {r.volume:,} KB"
        except Exception as exc:
            r.note = type(exc).__name__
    else:
        url = sources.load().proxied(target)
        ok, ttfb, total, n, note = _http_probe(url, timeout, read_bytes=400_000)
        r.ok, r.latency_ms, r.total_ms, r.bytes_read, r.note = ok, ttfb, total, n, note
        if ok:
            try:
                data, _err = __import__(
                    "dsh_console.catalog", fromlist=["x"]
                ).fetch_catalog(target, timeout=timeout)
                r.volume = int(data.get("count") or 0)
            except Exception:
                pass
    r.score = _score(r)
    return r


def discover_all(timeout: float = 10.0, workers: int = 12) -> list[ProbeResult]:
    """并发探测全部候选源，返回按评分降序排列的结果。

    全部在后台线程池里跑（urllib 在 I/O 时释放 GIL），单项都是短超时，
    因此不会拖住 GUI。
    """
    jobs: list[tuple] = []
    for url, label in NPM_CANDIDATES:
        jobs.append((probe_npm, url, label, timeout))
    for proxy, label in PROXY_CANDIDATES:
        jobs.append((probe_proxy, proxy, label, timeout))
    for target, label in CATALOG_CANDIDATES:
        jobs.append((probe_catalog, target, label, max(timeout, 25.0)))

    out: list[ProbeResult] = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(fn, *args) for fn, *args in jobs]
        for fut in as_completed(futures):
            try:
                out.append(fut.result())
            except Exception as exc:
                out.append(ProbeResult(kind="?", value="", ok=False, note=str(exc)))
    out.sort(key=lambda r: (r.kind, -r.score))
    return out


def best(results: list[ProbeResult]) -> dict[str, ProbeResult]:
    """每类里评分最高的那个（仅取可达的）。"""
    out: dict[str, ProbeResult] = {}
    for r in results:
        if not r.ok:
            continue
        cur = out.get(r.kind)
        if cur is None or r.score > cur.score:
            out[r.kind] = r
    return out
