"""数据源配置：区域、npm 镜像、GitHub 加速、市场目录源。

为什么需要这一层
----------------
控制台原来只会硬编码访问 ``registry.npmjs.org``，这在两处吃亏：

* **结果太少**——npm 搜索只按关键字捞，而 DSH 生态有官方**插件目录**
  （``awesome-dsh-plugin.com/plugins.json``，由 ``dsh-plugin-catalog`` 包分发），
  收录量远大于一次搜索能返回的 250 条上限。
* **国内网络慢**——本机实测到 PyPI/npm 只有几十 KiB/s，GitHub 更是经常连不上。

区域与代理的口径**刻意与已安装的 dshmarket 插件保持一致**（同一套取值，
见它的 ``lib/regions.js``），这样控制台和市场插件看到的网络行为是同一个，
不会出现"控制台能装、插件装不动"这种互相矛盾的怪象：

===========  ==================================  ==========================
区域           npm registry                        GitHub 加速前缀
===========  ==================================  ==========================
global        registry.npmjs.org                  （不使用）
china         mirrors.cloud.tencent.com/npm       gh-proxy.com → ghfast.top
===========  ==================================  ==========================

另外还支持读/写 dshmarket 自己的 ``.dsh-market/state.json`` 和技能中枢的
``~/.dsh/dsh-skill-hub.json``，让控制台能管理这两处已有的市场源。

⚠️ 环境变量（``DSHM_NPM_MIRROR`` / ``DSHM_GITHUB_PROXY`` / ``DSHM_REGISTRY_URL``）
由运维方设置，**优先级高于本文件的配置**，且此时界面应显示为"由环境变量托管"、
不允许就地修改——否则用户改了却不生效，只会更困惑。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "dsh-console"
CONFIG_PATH = CONFIG_DIR / "sources.json"

DSH_HOME = Path(os.environ.get("DSH_HOME") or (Path.home() / ".dsh"))
PROFILE_DIR = DSH_HOME / "profiles" / "web"
MARKET_STATE = PROFILE_DIR / ".dsh-market" / "state.json"   # dshmarket 自己的状态
SKILL_HUB_STATE = DSH_HOME / "dsh-skill-hub.json"           # 技能中枢的 sidecar

#: 官方插件目录（dshmarket 用的同一个地址）。
CATALOG_OFFICIAL = "https://awesome-dsh-plugin.com/plugins.json"
#: 目录也发布成了 npm 包，国内可通过镜像拿到（与 dshmarket 的策略一致）。
CATALOG_NPM_PKG = "dsh-plugin-catalog"

#: 环境变量名（与 dshmarket 完全一致）。
ENV_NPM_MIRROR = "DSHM_NPM_MIRROR"
ENV_GITHUB_PROXY = "DSHM_GITHUB_PROXY"
ENV_REGISTRY_URL = "DSHM_REGISTRY_URL"

DEFAULT_REGISTRY = "https://registry.npmjs.org"
TENCENT_NPM = "https://mirrors.cloud.tencent.com/npm"

#: 常用 GitHub 加速前缀（可自由填写其它）。
KNOWN_GITHUB_PROXIES: tuple[tuple[str, str], ...] = (
    ("https://gh-proxy.com", "gh-proxy.com（dshmarket 国内默认）"),
    ("https://ghfast.top", "ghfast.top（备用）"),
    ("https://ghproxy.net", "ghproxy.net"),
    ("https://mirror.ghproxy.com", "mirror.ghproxy.com"),
)


@dataclass(slots=True)
class RegionPreset:
    key: str
    name: str
    description: str
    npm_registry: str
    github_proxy: str


REGIONS: tuple[RegionPreset, ...] = (
    RegionPreset(
        key="global",
        name="国际",
        description="直连 registry.npmjs.org，不使用 GitHub 加速",
        npm_registry=DEFAULT_REGISTRY,
        github_proxy="",
    ),
    RegionPreset(
        key="china",
        name="中国大陆",
        description="腾讯云 npm 镜像 + gh-proxy.com 加速（与 dshmarket 国内档一致）",
        npm_registry=TENCENT_NPM,
        github_proxy="https://gh-proxy.com",
    ),
)

REGION_BY_KEY = {r.key: r for r in REGIONS}

#: 可作为插件来源的目录（比一次 npm 搜索的 250 条上限收录更多）。
CATALOG_PRESETS: tuple[tuple[str, str], ...] = (
    (CATALOG_OFFICIAL, "官方插件目录（awesome-dsh-plugin.com）"),
    (f"npm:{CATALOG_NPM_PKG}", "官方目录的 npm 包（可走镜像）"),
)


def _normalize_prefix(value: str) -> str:
    """规范化代理/registry 前缀：去掉尾部斜杠，非法值返回空串。

    与 dshmarket 的 ``normalizeGithubProxy`` 同样只接受 https、且不接受
    带凭据或查询串的地址——这类值会被持久化并在界面上回显。
    """
    raw = (value or "").strip()
    if not raw or "\\" in raw:
        return ""
    try:
        from urllib.parse import urlparse

        p = urlparse(raw)
        if p.scheme != "https" or p.username or p.password or p.query or p.fragment:
            return ""
        return f"{p.scheme}://{p.netloc}{p.path.rstrip('/')}"
    except Exception:
        return ""


@dataclass(slots=True)
class SourcesConfig:
    """控制台自己维护的数据源配置。"""

    region: str = "global"
    #: 留空 = 跟随区域预设
    npm_registry: str = ""
    github_proxy: str = ""
    #: 插件目录源（每行一个 URL 或 ``npm:包名``）
    catalog_urls: list[str] = field(default_factory=lambda: [CATALOG_OFFICIAL])
    #: 额外搜索关键字（在 dsh-plugin 之外多扫几组）
    extra_keywords: list[str] = field(default_factory=list)
    #: 技能市场源（owner/repo），与技能中枢的 marketSources 同步
    skill_sources: list[str] = field(default_factory=list)

    # ---------------------------------------------------------------- 生效值
    @property
    def preset(self) -> RegionPreset:
        return REGION_BY_KEY.get(self.region, REGION_BY_KEY["global"])

    def effective_registry(self) -> tuple[str, str]:
        """返回 ``(registry, 来源说明)``。环境变量最优先。"""
        env = (os.environ.get(ENV_NPM_MIRROR) or "").strip()
        if env:
            return env.rstrip("/"), f"环境变量 {ENV_NPM_MIRROR}"
        if self.npm_registry:
            return self.npm_registry.rstrip("/"), "控制台自定义"
        return self.preset.npm_registry.rstrip("/"), f"区域预设「{self.preset.name}」"

    def effective_proxy(self) -> tuple[str, str]:
        env = (os.environ.get(ENV_GITHUB_PROXY) or "").strip()
        if env:
            return env.rstrip("/"), f"环境变量 {ENV_GITHUB_PROXY}"
        if self.github_proxy:
            return self.github_proxy.rstrip("/"), "控制台自定义"
        p = self.preset.github_proxy
        return (p.rstrip("/") if p else ""), (
            f"区域预设「{self.preset.name}」" if p else "未启用（直连）"
        )

    def effective_catalog(self) -> tuple[list[str], str]:
        env = (os.environ.get(ENV_REGISTRY_URL) or "").strip()
        if env:
            # 与 dshmarket 一致：命名目录**替换**列表，而不是追加
            return [env], f"环境变量 {ENV_REGISTRY_URL}（替换列表）"
        return list(self.catalog_urls), "控制台配置"

    def proxied(self, url: str) -> str:
        """按需给 GitHub 系地址套上加速前缀。

        代理接收的是**完整目标 URL 作为路径**（``{proxy}/{url}``），
        与 dshmarket 的 ``throughProxy()`` 同一约定。
        """
        proxy, _ = self.effective_proxy()
        if not proxy:
            return url
        if not url.startswith("https://"):
            return url
        host = url.split("/", 3)[2] if url.count("/") >= 2 else ""
        if not any(host == h or host.endswith("." + h) for h in (
            "github.com", "raw.githubusercontent.com", "codeload.github.com",
            "objects.githubusercontent.com", "api.github.com",
        )):
            return url
        return f"{proxy}/{url}"

    # ---------------------------------------------------------------- 持久化
    def to_json(self) -> dict:
        return {
            "region": self.region,
            "npm_registry": self.npm_registry,
            "github_proxy": self.github_proxy,
            "catalog_urls": self.catalog_urls,
            "extra_keywords": self.extra_keywords,
            "skill_sources": self.skill_sources,
        }

    @classmethod
    def from_json(cls, raw: dict) -> "SourcesConfig":
        cfg = cls()
        cfg.region = str(raw.get("region") or "global")
        if cfg.region not in REGION_BY_KEY:
            cfg.region = "global"
        cfg.npm_registry = _normalize_prefix(str(raw.get("npm_registry") or ""))
        cfg.github_proxy = _normalize_prefix(str(raw.get("github_proxy") or ""))
        urls = raw.get("catalog_urls")
        cfg.catalog_urls = [str(u) for u in urls] if isinstance(urls, list) and urls else [CATALOG_OFFICIAL]
        kws = raw.get("extra_keywords")
        cfg.extra_keywords = [str(k) for k in kws] if isinstance(kws, list) else []
        srcs = raw.get("skill_sources")
        cfg.skill_sources = [str(s) for s in srcs] if isinstance(srcs, list) else []
        return cfg


def load() -> SourcesConfig:
    try:
        return SourcesConfig.from_json(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except Exception:
        return SourcesConfig()


def save(cfg: SourcesConfig) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(cfg.to_json(), ensure_ascii=False, indent=2), encoding="utf-8"
    )


def env_managed() -> dict[str, str]:
    """哪些项被环境变量托管（界面应禁用编辑并说明原因）。"""
    out: dict[str, str] = {}
    for name, label in (
        (ENV_NPM_MIRROR, "npm 镜像"),
        (ENV_GITHUB_PROXY, "GitHub 加速"),
        (ENV_REGISTRY_URL, "市场目录"),
    ):
        val = (os.environ.get(name) or "").strip()
        if val:
            out[label] = val
    return out


# --------------------------------------------------------------------------- #
# 技能中枢的市场源（读写 ~/.dsh/dsh-skill-hub.json）
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class SkillSource:
    repo: str
    ref: str = ""
    stars: int = 0

    @property
    def label(self) -> str:
        return f"{self.repo}@{self.ref}" if self.ref else self.repo


def read_skill_hub_state() -> dict:
    """读技能中枢的 sidecar。文件很大（统计快照），只取需要的字段。"""
    try:
        return json.loads(SKILL_HUB_STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def read_skill_sources() -> list[SkillSource]:
    """技能中枢里已添加的市场源（含星标缓存）。"""
    state = read_skill_hub_state()
    stats = (state.get("marketStats") or {}).get("stats") or {}
    out: list[SkillSource] = []
    for item in state.get("marketSources") or []:
        repo = str(item.get("repo") or "")
        if not repo:
            continue
        out.append(
            SkillSource(
                repo=repo,
                ref=str(item.get("ref") or ""),
                stars=int((stats.get(repo) or {}).get("stars") or 0),
            )
        )
    return out


def write_skill_sources(sources: list[SkillSource]) -> None:
    """把市场源写回技能中枢的 sidecar。

    ⚠️ 该文件由 dsh-skill-hub 拥有，字段很多（统计快照、回收站、分组顺序…）。
    这里**只改 ``marketSources`` 一个键**，其余原样保留；并且先备份。
    """
    state = read_skill_hub_state()
    if not state:
        raise RuntimeError("读不到 ~/.dsh/dsh-skill-hub.json（技能中枢还没初始化？）")
    try:
        SKILL_HUB_STATE.with_suffix(".json.dsh-console.bak").write_text(
            json.dumps(state, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:
        pass
    state["marketSources"] = [
        {"repo": s.repo, **({"ref": s.ref} if s.ref else {})} for s in sources
    ]
    SKILL_HUB_STATE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# --------------------------------------------------------------------------- #
# dshmarket 的区域状态（读写 .dsh-market/state.json）
# --------------------------------------------------------------------------- #
def read_market_state() -> dict:
    try:
        return json.loads(MARKET_STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def market_region() -> dict:
    """dshmarket 当前生效的区域与代理（只读展示用）。"""
    st = read_market_state()
    return {
        "region": st.get("region") or "global",
        "githubProxy": st.get("githubProxy") or "",
        "hasState": bool(st),
    }
