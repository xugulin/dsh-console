"""harness 自身的版本信息与升级。

这里的「harness」指的是那个 npm 全局包 ``@deepseek-ai/dsh``——``dsh`` 命令就是它的
``bin``，本机的 web 服务、TUI、GUI 全都跑在它上面。本模块只做两件事：**读版本**、
**升级**。

## 为什么要区分"已安装"和"各通道版本"

本机实测的情况很有代表性：``latest`` 是 ``0.1.5-rc.1``，正好等于已安装版本，
看起来"没有更新"；但 ``next`` 已经到了 ``0.1.5-rc.2``、``alpha`` 到了 ``0.1.6-alpha.1``。
只看 ``latest`` 会得出"不用更新"的结论，而用户可能正想上 ``next``。
所以这里把 ``dist-tags`` 整个取回来，让用户自己选通道。

## 权限

本机 ``/usr/lib/node_modules`` 是 root 的，普通用户写不进去，得走 ``sudo``。
所以 :func:`update` 会先看目录可不可写，不可写再加 ``sudo -n``（``-n`` 很关键：
**绝不能让控制台卡在密码提示上**，拿不到免密就直接报错让用户自己去终端敲）。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import config as console_config
from . import portable

#: harness 的 npm 包名。``dsh`` 命令就是它的 bin。
PKG = "@deepseek-ai/dsh"

#: config 里记当前来源的键。
KEY_SOURCE = "harnessSource"

#: 可选的升级通道（npm dist-tag）。latest = 稳定，next = 预览，alpha = 内测。
CHANNELS = ("latest", "next", "alpha")

#: ``npm view`` 走网络，给足时间但也别无限等。
VIEW_TIMEOUT = 60

#: 真正安装要下几百 MB，超时给到 15 分钟。
INSTALL_TIMEOUT = 900

#: dist-tags 的缓存时长（秒）。控制台每 3 秒刷一次页面，不缓存会把 npm 打爆。
TAGS_TTL = 600


class HarnessError(RuntimeError):
    """升级相关的失败。"""


# --------------------------------------------------------------------- 版本
@dataclass(slots=True)
class HarnessInfo:
    """harness 的本地信息（全部来自本机文件，不发网络请求）。"""

    installed: str = ""
    install_path: str = ""
    bin_path: str = ""
    node_version: str = ""
    npm_version: str = ""
    registry: str = ""
    writable: bool = False
    sudo_ok: bool = False
    error: str = ""
    source: str = ""

    @property
    def can_update(self) -> bool:
        """能不能在控制台里直接升级（要么目录可写，要么有免密 sudo）。"""
        return self.writable or self.sudo_ok

    @property
    def permission_note(self) -> str:
        if self.source == SOURCE_BUNDLED:
            return "内置 harness：装进包内的 harness/，不需要 sudo"
        if self.source == SOURCE_SYSTEM and portable.enabled():
            return "系统 harness：需要写系统目录（免密 sudo 可用时才能升）"
        if self.writable:
            return "全局目录可写，直接安装"
        if self.sudo_ok:
            return "需要 sudo（免密可用）"
        return "全局目录不可写且免密 sudo 不可用——请在终端里手动升级"


@dataclass(slots=True)
class Releases:
    """npm 上的可用版本（dist-tags）。"""

    tags: dict[str, str] = field(default_factory=dict)
    error: str = ""
    at: float = 0.0

    @property
    def latest(self) -> str:
        return self.tags.get("latest", "")

    def text(self) -> str:
        if self.error:
            return f"⚠️ {self.error}"
        if not self.tags:
            return "（没有取到版本信息）"
        return " · ".join(f"{k} {v}" for k, v in self.tags.items())


# --------------------------------------------------------------------- 工具
def _run(argv: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, check=False
        )
    except FileNotFoundError as exc:
        raise HarnessError(f"找不到命令 {argv[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise HarnessError(f"命令超时（>{timeout}s）：{' '.join(argv)}") from exc


# --------------------------------------------------------------------- 来源
#
# 一台机器上可能**同时存在两份 harness**：随便携包携带的那份，和系统里用
# npm -g 装的那份。它们各有各的用途（内置那份跟着包走、能随身带走；系统那份
# 和别的工具共用），所以控制台要能分别看到、分别启动、分别升级——
# 而不是像以前那样"便携包里就只能用内置的，系统那份连看都看不到"。

#: 随包携带的 harness。
SOURCE_BUNDLED = "bundled"
#: 系统里 npm -g 装的那份。
SOURCE_SYSTEM = "system"

SOURCES: tuple[tuple[str, str], ...] = (
    (SOURCE_BUNDLED, "内置 harness"),
    (SOURCE_SYSTEM, "系统 harness"),
)

#: 系统全局装的常见位置。便携模式下 PATH 被启动器改过（内置的排在最前），
#: 光靠 ``which("dsh")`` 只会找到内置那份，所以还得挨个看这几个标准位置。
_SYSTEM_ROOTS = (
    Path("/usr/lib/node_modules"),
    Path("/usr/local/lib/node_modules"),
    Path("/usr/lib64/node_modules"),
)


def source_label(source: str) -> str:
    return dict(SOURCES).get(source, source)


def bundled_package_root() -> Path | None:
    """包里那份 harness 的目录（没有就 None）。"""
    bundled = portable.harness_package()
    if bundled is not None and (bundled / "package.json").is_file():
        return bundled
    return None


def _root_from_bin(dsh: str) -> Path | None:
    """从 ``dsh`` 可执行文件反推包目录（``…/@deepseek-ai/dsh/lib/bin.js``）。"""
    try:
        resolved = Path(dsh).resolve()
    except OSError:
        return None
    for parent in resolved.parents:
        if parent.name == "dsh" and parent.parent.name == "@deepseek-ai":
            return parent
    return None


def system_package_root() -> Path | None:
    """**系统装的那份** harness 的目录。

    便携模式下不能只用 ``which("dsh")``：启动器把包内的 bin 前置到了 PATH，
    问出来的是内置那份。所以这里刻意：
    1. 先看 PATH 上的 dsh，但**排除掉包内的**；
    2. 再看几个标准全局位置；
    3. 最后才问 npm（子进程，几百毫秒，放最后）。
    """
    bundled_root = portable.root()
    for name in ("dsh.cmd", "dsh"):
        found = shutil.which(name)
        if not found:
            continue
        path = Path(found)
        if bundled_root is not None:
            try:
                path.resolve().relative_to(bundled_root)
                continue                     # 这是包里那份，跳过
            except (OSError, ValueError):
                pass
        root = _root_from_bin(found)
        if root is not None:
            return root
    for base in _SYSTEM_ROOTS:
        cand = base / PKG
        if (cand / "package.json").is_file():
            return cand
    npm = shutil.which("npm")
    if npm:
        try:
            proc = _run([npm, "root", "-g"], timeout=30)
            if proc.returncode == 0 and proc.stdout.strip():
                cand = Path(proc.stdout.strip()) / PKG
                if (cand / "package.json").is_file():
                    return cand
        except HarnessError:
            pass
    return None


def source_root(source: str) -> Path | None:
    """某个来源的包目录；没装返回 ``None``。"""
    if source == SOURCE_BUNDLED:
        return bundled_package_root()
    if source == SOURCE_SYSTEM:
        return system_package_root()
    return None


def current_source() -> str:
    """当前选中的来源（存在 config 里）。

    默认值：有内置的就用内置的（便携包场景下这才是"自带的那份"），否则系统那份。
    """
    saved = console_config.get(KEY_SOURCE)
    if saved in (SOURCE_BUNDLED, SOURCE_SYSTEM) and source_root(saved) is not None:
        return saved
    if bundled_package_root() is not None:
        return SOURCE_BUNDLED
    return SOURCE_SYSTEM


def set_source(source: str) -> None:
    console_config.put(KEY_SOURCE, source)
    invalidate()


def available_sources() -> list[tuple[str, str, bool, str]]:
    """``[(key, 标签, 是否可用, 说明)]``，给界面画选择器用。"""
    out: list[tuple[str, str, bool, str]] = []
    for key, label in SOURCES:
        root = source_root(key)
        if root is None:
            why = ("包里没有内置 harness" if key == SOURCE_BUNDLED
                   else "系统里没有安装 harness（npm -g）")
            out.append((key, label, False, why))
            continue
        version = ""
        try:
            version = json.loads((root / "package.json").read_text(encoding="utf-8")).get(
                "version", "")
        except (OSError, json.JSONDecodeError):
            pass
        out.append((key, label, True, f"{version or '?'}　{root}"))
    return out


def can_install(source: str) -> tuple[bool, str]:
    """**这个来源到底能不能装**，以及装不了的原因。

    为什么单独一个函数：界面上"未安装"和"能安装"是两件事。
    原来按钮只看"是不是正在忙"，于是没有便携包的环境里「安装内置 harness」
    也是可按的——而它底层会退回系统 npm，**点内置的安装、装到系统里去**
    （用户实测踩到：系统 harness 被从 0.1.5-rc.1 升成了 0.1.6-alpha.1）。
    """
    if source == SOURCE_BUNDLED:
        prefix = portable.harness_prefix()
        node = portable.node_bin()
        npm_cli = portable.npm_cli()
        if prefix is None:
            return False, "当前不是以便携包方式运行，没有可以安装的内置位置"
        missing = [n for n, v in (("node", node), ("npm", npm_cli)) if v is None]
        if missing:
            return False, f"包里缺少 {'、'.join(missing)}，装不了（重新解压一份完整的包）"
        return True, str(prefix)
    npm = shutil.which("npm")
    if npm is None:
        return False, "系统里找不到 npm，装不了"
    return True, npm


def package_root(source: str | None = None) -> Path | None:
    """``@deepseek-ai/dsh`` 的安装目录。

    ``source=None`` 时用当前选中的来源。指定来源时按来源找（内置的只在包里找，
    系统的只在系统里找）——这样"内置 harness 有没有装"这个问题才有确定答案。

    先走 **符号链接/文件系统**（几毫秒），实在找不到再问 npm（子进程，几百毫秒）。
    顺序反过来的话，侧边栏那种"启动时就要显示版本号"的地方会为了一个字符串白起一个进程。
    """
    return source_root(source or current_source())


def installed_version(source: str | None = None) -> str:
    """只读 package.json 拿版本号。

    **不启任何子进程**，所以侧边栏、页面构造函数里都能直接调。
    """
    root = package_root(source)
    if root is None:
        return ""
    try:
        return str(json.loads((root / "package.json").read_text(encoding="utf-8"))
                   .get("version", ""))
    except (OSError, json.JSONDecodeError):
        return ""


def package_mtime(source: str | None = None) -> float:
    """安装目录里 package.json 的修改时间。

    用来判断"**磁盘上的代码比正在跑的进程新**"——升级换的是文件，正在跑的还是旧代码，
    这个比较就是"需不需要重启"的依据（见页面里的用法）。
    """
    root = package_root(source)
    if root is None:
        return 0.0
    try:
        return (root / "package.json").stat().st_mtime
    except OSError:
        return 0.0


def info(source: str | None = None) -> HarnessInfo:
    """读本机的 harness 信息。不发网络请求，可以随便调。"""
    st = HarnessInfo()
    source = source or current_source()
    st.source = source
    root = package_root(source)
    if root is None:
        st.error = ("包里没有内置 harness" if source == SOURCE_BUNDLED
                    else "找不到系统安装的 @deepseek-ai/dsh")
        return st
    st.install_path = str(root)
    pkg_json = root / "package.json"
    try:
        st.installed = json.loads(pkg_json.read_text(encoding="utf-8")).get("version", "")
    except (OSError, json.JSONDecodeError) as exc:
        st.error = f"读不到 {pkg_json}：{exc}"

    dsh = shutil.which("dsh")
    st.bin_path = str(Path(dsh).resolve()) if dsh else ""

    node = shutil.which("node")
    if node:
        try:
            st.node_version = _run([node, "--version"], timeout=20).stdout.strip()
        except HarnessError:
            pass
    npm = shutil.which("npm")
    if npm:
        try:
            st.npm_version = _run([npm, "--version"], timeout=30).stdout.strip()
        except HarnessError:
            pass
        try:
            proc = _run([npm, "config", "get", "registry"], timeout=30)
            st.registry = proc.stdout.strip()
        except HarnessError:
            pass

    if source == SOURCE_BUNDLED:
        # 内置的那份就在包里，写它不需要 sudo（也不该要）
        st.writable = _can_write(root.parent)
        st.sudo_ok = False
        return st
    try:
        st.writable = root.parent.is_dir() and _can_write(root.parent)
    except OSError:
        st.writable = False
    st.sudo_ok = _sudo_available()
    return st


def _can_write(path: Path) -> bool:
    import os

    return os.access(path, os.W_OK)


def _sudo_available() -> bool:
    """有没有**免密** sudo。``-n`` 让它直接失败而不是等密码。"""
    sudo = shutil.which("sudo")
    if not sudo:
        return False
    try:
        return _run([sudo, "-n", "true"], timeout=15).returncode == 0
    except HarnessError:
        return False


# --------------------------------------------------------------------- 版本比较
_SEMVER_RE = re.compile(
    r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.\-]+))?(?:\+[0-9A-Za-z.\-]+)?$"
)


def _parse(version: str) -> tuple[int, int, int, list[str]] | None:
    m = _SEMVER_RE.match(version.strip())
    if not m:
        return None
    major, minor, patch = (int(m.group(i)) for i in (1, 2, 3))
    pre = m.group(4)
    return major, minor, patch, (pre.split(".") if pre else [])


def _cmp_pre(a: list[str], b: list[str]) -> int:
    """比较预发布标识：数字比字母小；数字按数值比；前缀短的小。"""
    if not a and not b:
        return 0
    if not a:
        return 1        # 有预发布的**小于**同版本的正式版
    if not b:
        return -1
    for x, y in zip(a, b):
        if x == y:
            continue
        xn, yn = x.isdigit(), y.isdigit()
        if xn and yn:
            return -1 if int(x) < int(y) else 1
        if xn != yn:
            return -1 if xn else 1      # 数字标识 < 字母标识
        return -1 if x < y else 1
    return (-1 if len(a) < len(b) else (1 if len(a) > len(b) else 0))


def compare(a: str, b: str) -> int:
    """语义化版本比较，返回 -1 / 0 / 1。解析不了就退回字符串比较。"""
    pa, pb = _parse(a), _parse(b)
    if pa is None or pb is None:
        return (a > b) - (a < b)
    if pa[:3] != pb[:3]:
        return -1 if pa[:3] < pb[:3] else 1
    return _cmp_pre(pa[3], pb[3])


def is_newer(candidate: str, than: str) -> bool:
    """``candidate`` 是否比 ``than`` 新。任一为空都算"否"。"""
    if not candidate or not than:
        return False
    return compare(candidate, than) > 0


# --------------------------------------------------------------------- 查询
_tags_lock = threading.Lock()
_tags_cache: Releases | None = None


def releases(force: bool = False, registry: str = "") -> Releases:
    """取 npm 上的 dist-tags（带 TTL 缓存）。

    控制台每 3 秒刷一次页面，不加缓存会把 npm 打爆；``force=True`` 供
    「检查更新」按钮显式绕过缓存。
    """
    global _tags_cache
    with _tags_lock:
        cached = _tags_cache
    if not force and cached is not None and (time.time() - cached.at) < TAGS_TTL:
        return cached

    npm = shutil.which("npm")
    if not npm:
        return Releases(error="找不到 npm", at=time.time())
    cmd = [npm, "view", PKG, "dist-tags", "--json"]
    if registry:
        cmd.append(f"--registry={registry}")
    try:
        proc = _run(cmd, timeout=VIEW_TIMEOUT)
    except HarnessError as exc:
        return Releases(error=str(exc), at=time.time())
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "npm view 失败").strip().splitlines()
        return Releases(error=detail[-1][:200] if detail else "npm view 失败", at=time.time())
    try:
        raw = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        return Releases(error=f"解析 dist-tags 失败：{exc}", at=time.time())
    # ``npm view <pkg> <field> --json`` 返回的是**数组**（每个匹配到的包一项），
    # 不是对象——直接 .items() 会 AttributeError。兼容两种形状。
    if isinstance(raw, list):
        raw = raw[0] if raw else {}
    if not isinstance(raw, dict):
        return Releases(error=f"dist-tags 形状意外：{type(raw).__name__}", at=time.time())
    out = Releases(tags={str(k): str(v) for k, v in raw.items()}, at=time.time())
    with _tags_lock:
        _tags_cache = out
    return out


def invalidate() -> None:
    """让下次 :func:`releases` 必须重新查（升级之后再问一次）。"""
    global _tags_cache
    with _tags_lock:
        _tags_cache = None


# --------------------------------------------------------------------- 升级
def update_command(target: str = "latest", *, source: str | None = None, sudo: bool | None = None,
                   registry: str = "") -> list[str]:
    """拼出升级命令。抽出来是为了能单独测、也能在界面上给用户看。

    **刻意不传 ``--allow-scripts``**：npm 12 默认拦下安装脚本，并会提示
    ``Run `npm install -g --allow-scripts=…` to allow these scripts once``。
    查过了，这里不需要放开——``koffi`` 靠 ``@koromix/koffi-linux-x64`` 这类平台
    二进制包、``node-pty`` 靠自带的 ``prebuilds/``，都不需要编译；唯一正经做事的
    第一方脚本 ``dsh-subprocess-local/scripts/ensure-spawn-helper.mjs`` 只是给
    node-pty 的 ``spawn-helper`` 补执行位，而那个文件在当前平台的预编译产物里
    **压根不存在**（实测 ``prebuilds/linux-x64/`` 只有 ``pty.node``），脚本本身空转。
    也就是说本机现装这份 harness 从来没跑过安装脚本，照样工作正常。
    既然没有收益，就不该让一堆第三方 postinstall 以 **root** 身份跑一遍——
    那正是 npm 12 默认拦下它们要防的事。
    """
    source = source or current_source()
    if source == SOURCE_BUNDLED:
        # 内置那份：用**包里的** node 跑包里的 npm，--prefix 指向包内 harness/。
        # 全程只写这个文件夹，不碰系统 npm 的全局前缀，也不需要 sudo。
        node, npm_cli, prefix = portable.node_bin(), portable.npm_cli(), portable.harness_prefix()
        if node and npm_cli and prefix:
            # --prefer-offline：**npm 自带"缓存里有就别重下"**。第一次装要下几百 MB，
            # 之后再装同一个版本基本是秒过——这正是"检查是否已经下载过、别重复下载"
            # 该用的办法（自己去看 _cacache 的结构既脆又没必要）。
            cmd = [str(node), str(npm_cli), "install", "-g", f"{PKG}@{target}",
                   "--prefix", str(prefix), "--no-audit", "--no-fund", "--prefer-offline"]
            if registry:
                cmd.append(f"--registry={registry}")
            return cmd
        # **绝不退回系统 npm。** 原来这里写的是"包不完整就退回系统 npm，好歹给条路"，
        # 但那意味着：用户点「安装内置 harness」，东西却装进了**系统**——
        # 一个请求装到另一个地方，还改的是全局环境。宁可明确报错。
        raise HarnessError(
            "便携包不完整，装不了内置 harness："
            f"node={node}、npm={npm_cli}、prefix={prefix}。"
            "请重新解压一份完整的包，或用「系统 harness」那一份。"
        )

    npm = shutil.which("npm") or "npm"
    cmd: list[str] = []
    if sudo is None:
        # 系统那份通常装在 /usr 下，要 sudo；内置那份不需要
        root = package_root(source)
        sudo = not _can_write(root.parent) if root is not None else True
    if sudo:
        # -n：拿不到免密就立刻失败，绝不让控制台挂在一个看不见的密码提示上
        cmd += [shutil.which("sudo") or "sudo", "-n"]
    cmd += [npm, "install", "-g", f"{PKG}@{target}", "--no-audit", "--no-fund",
            "--prefer-offline"]
    if registry:
        cmd.append(f"--registry={registry}")
    return cmd


#: npm 12 拦下安装脚本时的提示开头。它在 stderr 里，但**不是失败**——理由见
#: :func:`update_command`。这里把它翻成一句人话附在结果后面，免得用户看到一屏
#: "warning" 以为装坏了。
_SCRIPT_WARN = "npm warn install-scripts"


def _same_version(installed: str, target: str) -> bool:
    """``installed`` 是不是就是 ``target`` 指向的那个版本。

    ``target`` 可能是通道名（latest），也可能是具体版本号；也允许 ``^1.2.3`` 这种范围的开头。
    拿不准就返回 False——**宁可多下一次，也别把该升的版本跳过去**。
    """
    target = (target or "").strip().lstrip("^~>=<v ")
    if not target or not installed:
        return False
    if installed == target:
        return True
    try:
        if _parse(target) is None:
            return False
    except Exception:  # noqa: BLE001
        return False
    return False


def _run_stream(cmd: list[str], *, timeout: int, on_line) -> subprocess.CompletedProcess[str]:
    """跑命令并**逐行回调**，给界面做进度用。

    npm 的输出不是规规矩矩的百分比，所以界面上做的是
    "不确定进度条 + 实时日志尾巴"——这比编一个假百分比诚实，也比干等强。
    stderr 合并进 stdout：npm 的进度和警告都在 stderr 上。
    """
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
    except OSError as exc:
        raise HarnessError(f"起不来：{exc}") from exc
    collected: list[str] = []
    assert proc.stdout is not None
    deadline = time.monotonic() + timeout
    try:
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                collected.append(line)
                try:
                    on_line(line)
                except Exception:  # noqa: BLE001 - 回调出错不能影响安装
                    pass
            if time.monotonic() > deadline:
                proc.kill()
                raise HarnessError(f"超过 {timeout} 秒还没装完，已中止")
        code = proc.wait(timeout=60)
    except Exception:
        proc.kill()
        raise
    return subprocess.CompletedProcess(cmd, code, "\n".join(collected), "")


def _script_warning(stderr: str) -> str:
    if _SCRIPT_WARN not in stderr:
        return ""
    names: list[str] = []
    for line in stderr.splitlines():
        if line.startswith(_SCRIPT_WARN) and "postinstall" in line:
            names.append(line[len(_SCRIPT_WARN):].strip().split(" ")[0])
    detail = "、".join(names[:6]) if names else "若干包"
    return (
        f"\n（npm 照例拦下了 {detail} 的安装脚本。本机现装版本同样没跑过这些脚本、"
        f"工作正常，所以默认不放开；万一升级后原生模块出问题，再按 npm 的提示加 "
        f"--allow-scripts 重装一次即可。）"
    )


def update(target: str = "latest", *, source: str | None = None, timeout: int = INSTALL_TIMEOUT,
           registry: str = "", on_progress=None) -> str:
    """升级 harness，返回给用户看的结果说明。

    **不会**重启服务：升级只换磁盘上的代码，正在跑的进程还是旧的。
    要不要重启由用户在控制台上决定（那会中断正在进行的会话）。
    """
    # ⚠️ **必须传 source**。不传的话 info() 取的是"当前选中的来源"，
    # 于是"从 X 升级到 Y"里的 X 是别人的版本，**权限检查也会查错对象**——
    # 升级内置那份却去检查系统目录可不可写。实测：内置明明没装，
    # 却报"已从 0.1.6-alpha.1 升级到 0.1.5-rc.1"。
    #
    # ⚠️ 也**不能**因为"还没装"就报错：未安装恰恰是最需要走这条路的情况
    # （`info()` 对没装的来源会带 error，那是"没装"不是"装不了"）。
    # 实测踩过：把 error 当致命，结果整个"安装"功能被我自己堵死。
    before = info(source)
    installed = before.installed

    # 权限只对**系统那份**有意义：内置那份装在包里，不需要也不该要 sudo。
    # 判断依据是"包在不在"，而不是 info() 的 writable——包还没装时后者一定是 False。
    if not (source == SOURCE_BUNDLED and portable.harness_prefix() is not None):
        if not before.can_update:
            raise HarnessError(
                f"没有升级权限：{before.permission_note}\n"
                f"请在终端里执行：sudo npm install -g {PKG}@{target}"
            )

    # **已经装的就是要装的版本 → 直接跳过**，一个字节都不下。
    # 这是"不要重复下载"最有效的一刀：多数"再点一次"都是这种情况。
    if installed and _same_version(installed, target):
        return (f"{source_label(source)}已经是要装的版本 {installed}，"
                f"没有重复下载。（要强制重装就换个通道，或先卸载再装。）")

    cmd = update_command(target, source=source, registry=registry)
    lines: list[str] = []

    def on_line(text: str) -> None:
        lines.append(text)
        if on_progress is not None:
            on_progress(text)

    try:
        proc = _run_stream(cmd, timeout=timeout, on_line=on_line)
    except HarnessError as exc:
        raise HarnessError(str(exc)) from exc
    if proc.returncode != 0:
        tail = [ln for ln in lines if "npm warn" not in ln]
        raise HarnessError("\n".join(tail[-6:]) or f"npm 退出码 {proc.returncode}")

    warn = _script_warning("\n".join(lines))
    invalidate()
    after = info(source)
    if not installed:
        return (
            f"{source_label(source)}安装完成：{after.installed}"
            f"（{PKG}@{target}）。需要重启 harness 才会生效。" + warn
        )
    if after.installed == installed:
        return (
            f"已重新安装 {after.installed}（{PKG}@{target} 就是这个版本，没有变化）。"
            "想让新代码生效需要重启 harness。" + warn
        )
    return (
        f"已从 {installed or '?'} 升级到 {after.installed}"
        f"（{PKG}@{target}）。需要重启 harness 才会生效——"
        "重启会中断正在进行的会话，请在方便的时候点「重启」。" + warn
    )


def summary_text(inf: HarnessInfo, rel: Releases) -> str:
    """一行摘要，给界面/自检复用。"""
    if inf.error:
        return inf.error
    newest = rel.latest or "?"
    state = "已是最新" if not is_newer(newest, inf.installed) else f"可升级到 {newest}"
    return f"{inf.installed}（latest {newest}，{state}）"
