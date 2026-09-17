"""已安装插件的完整生命周期管理：安装 / 卸载 / 更新 / 启用 / 停用。

三种操作的作用层不同，这也决定了**是否需要重启**：

===========================  ==========================================  ==========
操作                          改哪里                                       生效方式
===========================  ==========================================  ==========
安装 / 卸载 / 更新             ``package.json`` 的 deps 与 ``dsh.profile.bundles``   **需重启**
启用 / 停用                    ``cordis.patch.yml``（patch 层）              **热重载**
===========================  ==========================================  ==========

启用/停用的热重载来自 profile 里的 ``"patchReload": "live"``：宿主会 watch
``cordis.patch.yml`` 并实时重新组合插件树，所以停用插件不用重启。

⚠️ **为什么这里要这么小心**：那个 patch 文件一旦写坏，正在运行的 harness 会在
热重载时出错。因此 :func:`set_enabled` 一律走「**备份 → 写入 → 用
``dsh --profile web --dump-config`` 验证 → 失败自动回滚**」这条路径，
绝不留半个坏文件。

控制台自己写的停用项被包在一对哨兵注释之间，与用户手写的条目完全隔离，
每次重写都整段重新生成，因此是幂等的。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import subproc

PROFILE = Path.home() / ".dsh" / "profiles" / "web"
PACKAGE_JSON = PROFILE / "package.json"
PATCH_FILE = PROFILE / "cordis.patch.yml"
NODE_MODULES = PROFILE / "node_modules"

#: 托管段哨兵。控制台只碰这两行之间的内容，其余原样保留。
BEGIN = "# >>> dsh-console managed: disabled plugins (auto-generated, do not edit by hand)"
END = "# <<< dsh-console managed"

#: 需要重启才能生效的操作（供界面提示）
RESTART_REQUIRED = {"install", "uninstall", "update"}

_TIMEOUT = 300  # pnpm 走网络，给足时间


class PluginError(RuntimeError):
    """插件操作失败。"""


@dataclass(slots=True)
class InstalledPlugin:
    """profile 里已安装的一个插件。"""

    name: str
    version: str = ""
    spec: str = ""
    description: str = ""
    license: str = ""
    in_bundles: bool = False
    row_ids: list[str] = field(default_factory=list)
    disabled: bool = False
    homepage: str = ""
    repository: str = ""
    dependencies: dict[str, str] = field(default_factory=dict)
    has_install_scripts: bool = False
    unpacked_mb: float = 0.0

    @property
    def source(self) -> str:
        s = self.spec
        if s.startswith("github:"):
            return "GitHub"
        if s.startswith("link:") or s.startswith("file:"):
            return "本地目录"
        if s.startswith("workspace:"):
            return "工作区"
        return "npm"

    @property
    def status_text(self) -> str:
        if not self.in_bundles:
            return "未装配"
        return "已停用" if self.disabled else "已启用"

    @property
    def is_effective(self) -> bool:
        """是否真的在运行（在 bundles 里且未被停用）。"""
        return self.in_bundles and not self.disabled


# --------------------------------------------------------------------------- #
# 读取
# --------------------------------------------------------------------------- #
def _plugin_dir(name: str) -> Path:
    return NODE_MODULES / name


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def row_ids_for(name: str) -> list[str]:
    """从插件自带的 bundle patch 里取出它插入的装配行 id。

    例如 ``dsh-browser-panel`` → ``["browser-panel"]``、``dshmarket`` → ``["dsh-market"]``。
    这个 id 才是 ``disabled: true`` 要针对的目标（**不是**包名）。
    """
    patch = _plugin_dir(name) / "cordis.patch.yml"
    try:
        text = patch.read_text(encoding="utf-8")
    except OSError:
        return []
    ids: list[str] = []
    for line in text.splitlines():
        m = re.match(r"\s*-?\s*id:\s*['\"]?([A-Za-z0-9._@/-]+)['\"]?\s*$", line)
        if m and m.group(1) not in ids:
            ids.append(m.group(1))
    return ids


def _read_patch() -> str:
    try:
        return PATCH_FILE.read_text(encoding="utf-8")
    except OSError:
        return "[]\n"


def managed_disabled_ids() -> set[str]:
    """托管段里当前被停用的行 id。"""
    text = _read_patch()
    m = re.search(re.escape(BEGIN) + r"(.*?)" + re.escape(END), text, re.S)
    if not m:
        return set()
    return set(re.findall(r"^\s*-?\s*id:\s*['\"]?([A-Za-z0-9._@/-]+)['\"]?\s*$", m.group(1), re.M))


def installed() -> list[InstalledPlugin]:
    """列出已安装插件（全部信息来自本地文件，不发网络请求）。"""
    data = _read_json(PACKAGE_JSON)
    bundles = list(data.get("dsh", {}).get("profile", {}).get("bundles", []))
    bundle_set = set(bundles)
    disabled_ids = managed_disabled_ids()

    out: list[InstalledPlugin] = []
    for name, spec in (data.get("dependencies") or {}).items():
        pkg = _read_json(_plugin_dir(name) / "package.json")
        ids = row_ids_for(name)
        scripts = (pkg.get("scripts") or {})
        dist = pkg.get("dist") or {}
        out.append(
            InstalledPlugin(
                name=name,
                version=str(pkg.get("version", "")),
                spec=str(spec),
                description=str(pkg.get("description", "") or ""),
                license=str(pkg.get("license", "") or ""),
                in_bundles=name in bundle_set,
                row_ids=ids,
                disabled=any(i in disabled_ids for i in ids),
                homepage=str(pkg.get("homepage", "") or ""),
                repository=str((pkg.get("repository") or {}).get("url", "") or ""),
                dependencies=dict(pkg.get("dependencies") or {}),
                has_install_scripts=any(
                    k in scripts for k in ("preinstall", "install", "postinstall")
                ),
                unpacked_mb=round(int(dist.get("unpackedSize") or 0) / 1048576, 2),
            )
        )
    out.sort(key=lambda p: (not p.is_effective, p.name))
    return out


# --------------------------------------------------------------------------- #
# 写入 patch 层（停用 / 启用）
# --------------------------------------------------------------------------- #
def _render_managed(ids: set[str]) -> str:
    lines = [BEGIN]
    for i in sorted(ids):
        lines.append(f"- id: {i}")
        lines.append("  disabled: true")
    lines.append(END)
    return "\n".join(lines)


def _strip_managed(text: str) -> str:
    return re.sub(re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n?", "", text, flags=re.S)


def _patch_with_disabled(ids: set[str]) -> str:
    body = _strip_managed(_read_patch()).rstrip("\n")
    # 原文是空数组占位 `[]` 时换成真正的列表，否则直接追加
    if body.strip() == "[]":
        body = ""
    # 没有任何停用项时**不留空托管段**，否则启用/停用来回几次会攒下一堆空注释块
    if not ids:
        return (body + "\n") if body else "[]\n"
    rendered = _render_managed(ids)
    return (body + "\n\n" + rendered + "\n") if body else (rendered + "\n")


def validate_profile(timeout: int = 180) -> tuple[bool, str]:
    """用 ``--dump-config`` 验证 profile 还能正常组合。

    这是写入 patch 后的**安全网**：组合失败说明 YAML 写坏了。
    """
    try:
        proc = subproc.run(
            ["dsh", "--profile", "web", "--dump-config"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return False, "找不到 dsh 命令"
    except subprocess.TimeoutExpired:
        return False, "验证超时"
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout or "dump-config 失败").strip()[:400]
    return True, ""


def set_enabled(plugin: InstalledPlugin, enabled: bool) -> str:
    """启用/停用插件（写 patch 层，热重载生效，**无需重启**）。

    :returns: 给用户看的结果说明
    :raises PluginError: 写入后验证失败（此时已自动回滚）
    """
    if not plugin.in_bundles:
        raise PluginError(
            f"{plugin.name} 不在 dsh.profile.bundles 里，本来就不会被加载；"
            "如需启用请先重新安装。"
        )
    if not plugin.row_ids:
        raise PluginError(
            f"无法确定 {plugin.name} 的装配行 id（它没有提供 cordis.patch.yml），"
            "因此不能安全地停用。"
        )

    ids = managed_disabled_ids()
    before = ids.copy()
    if enabled:
        ids -= set(plugin.row_ids)
    else:
        ids |= set(plugin.row_ids)

    if ids == before:
        return f"{plugin.name} 已经是{'已启用' if enabled else '已停用'}状态"

    backup = PATCH_FILE.with_suffix(".yml.dsh-console.bak")
    original = _read_patch()
    try:
        shutil.copy2(PATCH_FILE, backup)
    except OSError:
        pass

    PATCH_FILE.write_text(_patch_with_disabled(ids), encoding="utf-8")

    ok, err = validate_profile()
    if not ok:
        # 回滚，绝不留下坏配置
        PATCH_FILE.write_text(original, encoding="utf-8")
        raise PluginError(f"写入后配置校验失败，已自动回滚：{err}")

    verb = "已启用" if enabled else "已停用"
    return f"{plugin.name} {verb}（patch 层热重载，无需重启）"


# --------------------------------------------------------------------------- #
# pnpm 操作（安装 / 卸载 / 更新）
# --------------------------------------------------------------------------- #
def _dsh_plugin(*args: str) -> str:
    cmd = ["dsh", "plugin", "--profile", "web", *args]
    try:
        proc = subproc.run(
            cmd, capture_output=True, text=True, timeout=_TIMEOUT, check=False
        )
    except FileNotFoundError as exc:
        raise PluginError("找不到 dsh 命令") from exc
    except subprocess.TimeoutExpired as exc:
        raise PluginError(f"操作超时（>{_TIMEOUT}s）：{' '.join(args)}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise PluginError(detail[-600:] or f"{' '.join(args)} 失败")
    return proc.stdout


def install(spec: str) -> str:
    """安装插件。``spec`` 可以是包名、``pkg@version`` 或 ``github:user/repo``。"""
    spec = spec.strip()
    if not spec:
        raise PluginError("请填写要安装的包名")
    _dsh_plugin("add", spec)
    return f"{spec} 安装完成，**需要重启** harness 才会加载"


def uninstall(name: str) -> str:
    _dsh_plugin("remove", name)
    return f"{name} 已卸载，**需要重启** harness 才会生效（其数据目录不会被删除）"


def update(name: str | None = None) -> str:
    _dsh_plugin("update", *([name] if name else []))
    return f"{name or '全部插件'} 更新完成，**需要重启** harness 才会生效"


def update_to_latest(
    names: list[str],
    on_progress: Callable[[int, int, str], None] | None = None,
) -> tuple[list[str], list[tuple[str, str]]]:
    """把选中的插件逐个升到最新版。

    用 ``add <pkg>@latest`` 而不是 ``update <pkg>``：后者只在 package.json 的 semver
    范围内升级——装的是 ``^1.0.6`` 时就永远到不了 2.x。``add @latest`` 是明确的
    "升到最新"，并会把 package.json 里的范围重写成新版本。

    pnpm 需要独占 node_modules，所以**必须串行**，不能像体检那样并发。

    :returns: ``(成功列表, [(包名, 错误), ...])``
    """
    done: list[str] = []
    failed: list[tuple[str, str]] = []
    total = len(names)
    for i, name in enumerate(names, 1):
        if on_progress is not None:
            on_progress(i, total, name)
        try:
            _dsh_plugin("add", f"{name}@latest")
        except PluginError as exc:
            failed.append((name, str(exc)))
        else:
            done.append(name)
    return done, failed


def restart_service() -> str:
    from . import service

    service.restart()
    url = service.wait_for_url(attempts=30, delay=1.0)
    return "harness 已重启" + ("，地址已就绪" if url else "，但暂未拿到访问地址")
