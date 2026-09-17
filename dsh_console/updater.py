"""控制台自身的更新：从发布地址取一个新包，只换代码，不动数据和运行时。

## 为什么只换 ``app/``

一个包里有四类东西，更新时该不该动完全不一样：

============  ==========================================================
``app/``      控制台自己的代码 —— **要换**
``tools/``    自检/构建脚本 —— **要换**
``harness/``  可以单独升级（Harness 页那条路），更新控制台时**不该碰**
``runtime/``  几十上百 MB 的 node/python/PySide6，跟代码版本无关 —— **不该碰**
``home/``     **用户的**：会话、配置、插件 —— 绝对不能碰
``run/``      进程与日志 —— 不该碰
============  ==========================================================

所以不是"解压覆盖"，而是**挑目录换**。换之前把旧的 ``app/`` 备份成 ``app.bak-<时间>``，
出问题还能手动退回去——这类"更新自己"的操作没有回滚就是耍流氓。

## 发布地址

配置在 ``config.json`` 的 ``consoleUpdateUrl``：可以是

* ``https://…/DSH及控制台.zip`` —— 新包的完整压缩包（会下载后读里面的清单）
* ``https://…/version.json`` —— 只放版本信息的 JSON（检查更新时更省）

检查更新时两者都支持：认后缀。
"""

from __future__ import annotations

import json
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from . import portable

#: 更新时允许被替换的顶层条目（其余一律不碰）。
REPLACEABLE = ("app", "tools")
#: 更新时一并刷新的顶层文件（启动器与说明——它们通常也随版本调整）。
REFRESH_FILES = (
    "启动DSH控制台.sh", "启动DSH终端界面.sh", "启动DSH网页界面.sh", "安装桌面快捷方式.sh",
    "启动DSH控制台.bat", "启动DSH终端界面.bat", "启动DSH网页界面.bat",
    "创建桌面快捷方式.bat", "创建桌面快捷方式.ps1", "DSH控制台.desktop",
    "使用说明.md", portable.MANIFEST,
)
TIMEOUT = 60


class UpdateError(RuntimeError):
    """检查或应用更新时的失败。"""


def current_version() -> str:
    return str(portable.manifest().get("版本") or "")


def _quote_url(url: str) -> str:
    """把 URL 里的非 ASCII 字符百分号编码。

    ``DSH及控制台.zip`` 这种文件名在 URL 里是合法的（浏览器会自己编码），但
    ``urllib`` 不会——中文会一路传到 http.client，最后在写请求行时炸在
    ``'ascii' codec can't encode characters``（实测踩过）。所以自己先编码，
    已经是 ``%xx`` 的部分不动（``safe`` 里带上 ``%``）。
    """
    parts = urllib.parse.urlsplit(url)
    path = urllib.parse.quote(parts.path, safe="/%")
    query = urllib.parse.quote(parts.query, safe="=&%")
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, query, parts.fragment))


def _fetch(url: str, dest: Path, timeout: int = TIMEOUT) -> Path:
    url = _quote_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": "dsh-console-updater"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp, open(dest, "wb") as out:  # noqa: S310
            shutil.copyfileobj(resp, out)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise UpdateError(f"下载失败：{type(exc).__name__}: {exc}") from exc
    return dest


def _manifest_from_zip(path: Path) -> dict:
    """从 zip 里读清单。包内路径是 ``DSH及控制台/版本信息.json``，但也兼容根目录直接放。"""
    try:
        with zipfile.ZipFile(path) as zf:
            names = [n for n in zf.namelist() if n.endswith(portable.MANIFEST)]
            if not names:
                raise UpdateError(f"压缩包里找不到 {portable.MANIFEST}，不像是本控制台的包")
            # 取层级最浅的那个，避免撞上子目录里的同名文件
            name = min(names, key=lambda n: n.count("/"))
            return json.loads(zf.read(name).decode("utf-8"))
    except zipfile.BadZipFile as exc:
        raise UpdateError(f"不是有效的 zip：{exc}") from exc


def check(url: str) -> tuple[str, str]:
    """问发布地址要版本。返回 ``(版本号, 说明)``。

    地址指向 ``.json`` 就直接读；指向 ``.zip`` 就下载后读包内清单（慢，但只需要一个地址）。
    """
    url = (url or "").strip()
    if not url:
        raise UpdateError("还没配置发布地址（config.json 的 consoleUpdateUrl）")
    run = portable.run_dir()
    if run is None:
        raise UpdateError("只有便携模式才支持控制台自更新")
    run.mkdir(parents=True, exist_ok=True)

    if url.lower().endswith(".json"):
        tmp = _fetch(url, run / "update-version.json", timeout=20)
        try:
            data = json.loads(tmp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise UpdateError(f"版本文件读不出来：{exc}") from exc
        version = str(data.get("版本") or data.get("version") or "")
        if not version:
            raise UpdateError("版本文件里没有「版本」字段")
        return version, f"发布地址上的版本：{version}"

    tmp = run / "update-check.zip"
    _fetch(url, tmp, timeout=120)
    data = _manifest_from_zip(tmp)
    try:
        tmp.unlink()
    except OSError:
        pass
    version = str(data.get("版本") or "")
    if not version:
        raise UpdateError("包清单里没有「版本」字段")
    return version, f"发布地址上的版本：{version}（构建于 {data.get('构建时间', '?')}）"


def apply(url: str, *, on_progress=None) -> str:
    """下载并应用更新。返回给用户看的结果说明。

    ``on_progress(text)`` 会在关键步骤被调用（界面拿它更新状态栏）。
    """
    def step(text: str) -> None:
        if callable(on_progress):
            on_progress(text)

    root = portable.root()
    run = portable.run_dir()
    if root is None or run is None:
        raise UpdateError("只有便携模式才支持控制台自更新")

    url = (url or "").strip()
    if not url.lower().endswith(".zip"):
        raise UpdateError("应用更新需要一个 .zip 地址；.json 只能用来检查版本")

    before = current_version()
    step("正在下载新包…")
    zip_path = _fetch(url, run / "update-new.zip", timeout=1800)
    step(f"下载完成（{zip_path.stat().st_size / 1048576:.1f} MB），正在校验…")

    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        manifest_names = [n for n in names if n.endswith(portable.MANIFEST)]
        if not manifest_names:
            raise UpdateError("压缩包里没有版本信息，拒绝更新")
        manifest_name = min(manifest_names, key=lambda n: n.count("/"))
        prefix = manifest_name[: -len(portable.MANIFEST)]      # 形如 "DSH及控制台/"
        new_version = str(json.loads(zf.read(manifest_name).decode("utf-8")).get("版本") or "")
        if not any(n == f"{prefix}app/main.py" for n in names):
            raise UpdateError("压缩包里没有 app/main.py，不像是本控制台的包")

        step("正在替换代码…")
        stamp = time.strftime("%Y%m%d-%H%M%S")
        for name in REPLACEABLE:
            member = f"{prefix}{name}/"
            if not any(n.startswith(member) for n in names):
                continue                       # 新包里没有这一项就保持原样
            target = root / name
            if target.exists():
                backup = root / f"{name}.bak-{stamp}"
                if backup.exists():
                    shutil.rmtree(backup, ignore_errors=True)
                shutil.move(str(target), str(backup))     # 先备份再写，失败可回退
            _extract_dir(zf, member, target)
            step(f"已更新 {name}/")

        refreshed = []
        for name in REFRESH_FILES:
            member = f"{prefix}{name}"
            if member in names:
                (root / name).write_bytes(zf.read(member))
                refreshed.append(name)
        step(f"已刷新 {len(refreshed)} 个启动器/说明文件")

    try:
        zip_path.unlink()
    except OSError:
        pass

    if new_version and new_version != before:
        return (f"控制台已从 {before or '?'} 更新到 {new_version}。"
                f"旧的 app/ 备份在 app.bak-{stamp}/，确认没问题后可以删掉；"
                f"重启控制台即可用上新版本。")
    return (f"控制台已重新应用 {new_version or before}（版本没变）。"
            f"备份在 app.bak-{stamp}/。重启控制台生效。")


def _extract_dir(zf: zipfile.ZipFile, prefix: str, target: Path) -> None:
    """把 zip 里 ``prefix`` 下的内容解到 ``target``，保留可执行位。"""
    target.mkdir(parents=True, exist_ok=True)
    for name in zf.namelist():
        if not name.startswith(prefix) or name.endswith("/"):
            continue
        rel = name[len(prefix):]
        if not rel or ".." in rel:
            continue
        out = target / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(zf.read(name))
        mode = zf.getinfo(name).external_attr >> 16
        if mode & 0o111:                       # zip 里的可执行位在 external_attr 高 16 位
            out.chmod(out.stat().st_mode | 0o755)
