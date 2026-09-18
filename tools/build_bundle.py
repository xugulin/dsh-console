#!/usr/bin/env python3
"""把 DSH + 控制台打成一个可移植包（解压即用、不碰系统）。

## 先看这条（踩过的坑）

**不要把输出目录放在 ``/tmp``。** 本机 ``/tmp`` 是 tmpfs（7.7 GB），构建一次
（1.5 GB）+ 压缩（575 MB）+ 解压验证（1.5 GB）就把它撑满了；而 tmpfs 满了之后
**连 shell 都起不来**——执行工具要往临时目录写脚本，于是"清理现场"这件事反而做不了，
只能干等外部介入。所以默认输出在项目下的 ``dist/``（真实磁盘），并且构建前先检查
空间，不够就直接拒绝、说清还差多少，而不是写到一半炸掉。

## 产出形态

::

    DSH-Console/
    ├── Start-DSH-Console.sh / .bat / .desktop / .vbs     ← 快捷启动方式
    ├── Start-DSH-Terminal.sh / .bat
    ├── Start-DSH-Web-UI.sh / .bat
    ├── icons/  icon.png / icon.ico / icon-*.png
    ├── app/                     控制台源码（main.py + dsh_console/）
    ├── harness/                 npm 的 global prefix 形状 —— 升级只用 npm --prefix
    │   ├── bin/dsh
    │   └── lib/node_modules/@deepseek-ai/dsh/…
    ├── runtime/<平台>/          自带 node + python（含 PySide6），目标机不需要预装
    │   ├── node/{bin/node, lib/node_modules/npm}
    │   └── python/{bin/python3.14, lib/python3.14/{标准库, site-packages}}
    ├── home/                    重定向后的 HOME：.dsh / .config / .cache 都落这里
    ├── run/                     运行时：pid、web 日志
    └── README-Portable.md / version.json

## 为什么能"不污染系统"

启动器把 ``HOME``（Windows 上还有 ``USERPROFILE``）指到包内的 ``home/``，并把
``DSH_HOME`` 指到 ``home/.dsh``。于是控制台里所有 ``Path.home()`` 的写法——十几个模块、
写死的 ``~/.dsh`` / ``~/.config/dsh-console`` / ``~/.cache/dsh-console``——全都自动落进包里。
一处设置全局生效，比逐个改源码可靠。

## 裁剪

PySide6 装完 648 MB，其中 QtWebEngine 的 Chromium 一个 .so 就 194 MB。GUI 版要用它，
不能删；能删的是翻译、QML、设计器工具这些控制台用不到的（见 :data:`QT_PRUNE`）。
标准库那边 ``site-packages``（系统包）、``idlelib``、``config-*`` 也一律不拷。
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import sysconfig
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROJECT_NAME = "DSH-Console"

#: 系统里那些"源"。
#:
#: 这些路径都长在**构建机**上，换台机器布局就不一样，所以一律留了环境变量出口；
#: 默认值只按最常见的发行版布局写。**不要在这里写死带用户名的绝对路径**——
#: 早先 ``SYS_SITE`` 写死过一个 ``/home/<用户名>/…/site-packages``：既泄露用户名，
#: 别人拿去构建也必然找不到那个目录。
def _src(env: str, default: str) -> Path:
    return Path(os.environ.get(env) or default)


SYS_PYTHON = _src("DSH_BUILD_PYTHON", "/usr/bin/python3.14")
SYS_LIBPYTHON = _src("DSH_BUILD_LIBPYTHON", "/usr/lib/libpython3.14.so.1.0")
SYS_STDLIB = _src("DSH_BUILD_STDLIB", "/usr/lib/python3.14")
SYS_NODE = _src("DSH_BUILD_NODE", "/usr/bin/node")
SYS_NPM = _src("DSH_BUILD_NPM", "/usr/lib/node_modules/npm")
SYS_DSH_PKG = _src("DSH_BUILD_DSH", "/usr/lib/node_modules/@deepseek-ai/dsh")
#: PySide6 从**当前解释器**的 site-packages 里取：用哪个解释器构建，包里就装哪个
#: 环境的 PySide6。原来写死成某个 venv 的绝对路径，换个人构建就崩。
SYS_SITE = _src("DSH_BUILD_SITE", sysconfig.get_paths()["purelib"])
DSH_PROFILE = Path.home() / ".dsh" / "profiles" / "web"

#: 标准库里不拷的目录（用不到，或者本来就属于系统安装）。
STDLIB_SKIP = {
    "site-packages", "idlelib", "config-3.14-x86_64-linux-gnu", "test", "tests",
    "tkinter", "turtledemo", "lib2to3", "ensurepip", "__pycache__", "pydoc_data",
}

#: Qt 里不拷的目录。控制台用 QtCore/QtGui/QtWidgets + QtWebEngineWidgets，
#: 翻译/QML/设计器/3D 这些都用不到——实测能省掉 100 MB 量级。
#: translations/ 里**必须留下**的文件：Qt 自己的界面文案（标准对话框、按钮）与
#: **内置浏览器的右键菜单**都在 qm 里。此前把整个 translations/ 删掉，代价就是
#: 内置浏览器右键菜单永远是英文（实测反馈）。只留这两种 × 两种语言，约 160 KB。
#: 只留**内置的那两种语言**的 Qt 翻译：qtbase/qtwebengine × zh_CN/en。
#: 全语言的 qtbase_*.qm 加起来 7 MB 出头，而我们只需要两套。
QT_TRANSLATIONS_KEEP = ("qtbase_zh_CN", "qtbase_en", "qtwebengine_zh_CN", "qtwebengine_en")
#: Chromium 自己的语言包目录（``translations/qtwebengine_locales/``）。它决定
#: ``navigator.language`` 与网页里 Chromium 画的那些界面文字。整目录 44 MB（所有语言），
#: 我们只需要中文和英文这两份（各约 0.5 MB）。
QT_LOCALES_KEEP = ("zh-CN.pak", "en-US.pak", "en-GB.pak")

#: 打包时**整个跳过**的"Office 预览链"：
#:
#:   ``dsh-web-app`` → ``dsh-office-to-pdf`` → ``libreoffice-kit`` → ``libreoffice-kit-win32-x64``
#:
#: 最后一环是**一整份 LibreOffice Windows 运行时（约 325 MB）**，它只服务一件事：
#: harness Web 界面里预览 docx / xlsx / pptx（转成 PDF 再显示）。
#:
#: ⚠️ **只删引擎，不要删 `dsh-office-to-pdf`**：那个插件被写进了 dsh 自己的默认 profile
#: （loader entry ``office-to-pdf``），包不在就 `failed to import loader entry
#: office-to-pdf ... Cannot find package` → **整个插件树加载失败、harness 起不来**（实测）。
#: 引擎则是**惰性解析**的：缺了只是"预览时报错"，启动完全不受影响（实测 B ✓）。
#:
#: 为什么默认删掉（用户明确要求 + 实测支持）：
#:   1. 删掉后 harness **照常启动**——wine 实测给出 ``dsh web: http://127.0.0.1:8964/``；
#:   2. 本机这份正在使用的系统 harness 压根**没有** ``dsh-office-to-pdf`` 与
#:      ``libreoffice-kit``（245 个包里就没有），一切功能正常——说明这条链是
#:      **惰性加载**的，缺了只是"没有 Office 预览"，不会崩；
#:   3. Windows 上没有 WASM 退路（kit 源码里写死 ``platform !== "linux"`` 就抛错），
#:      也就是说这 325 MB 是"要么留、要么彻底不要"，没有中间档。
#: 想要 Office 预览：``DSH_BUNDLE_KEEP_OFFICE_PREVIEW=1`` 构建即可。
SKIP_HEAVY_PACKAGES = ("libreoffice-kit",)


def align_dsh_deps(dst: Path, plat: str) -> None:
    """把 harness 里 ``@deepseek-ai/dsh-*`` 的版本**对齐到 dsh 自己的版本**。

    为什么必须做：``dsh`` 声明的是 ``^0.1.6-alpha.1`` 这种**预发布范围**，npm 可能解析到
    更新的 alpha，而 **alpha 之间是会删导出的**——实测 Windows 侧两次都装到
    ``dsh-app-boot@0.1.6-alpha.2``，它删掉了 ``watchUserPatches``，于是 ``dsh`` 一启动就
    `SyntaxError: does not provide an export named ...`，harness 永远起不来。

    这些包都是**纯 JS**（同版本跨平台一致），所以做法很直接：从**另一个平台的树**里
    把同版本的包复制过来。Linux 侧先构建，正好可以当"正版来源"。
    """
    import shutil as _shutil

    other = "linux" if plat == "win" else "win"
    mine = dst / "harness" / plat / "lib" / "node_modules" / "@deepseek-ai" / "dsh"
    theirs = dst / "harness" / other / "lib" / "node_modules" / "@deepseek-ai" / "dsh"
    if not mine.is_dir() or not theirs.is_dir():
        return
    try:
        version = json.loads((mine / "package.json").read_text(encoding="utf-8"))["version"]
    except Exception:                          # noqa: BLE001
        return
    src_root = theirs / "node_modules" / "@deepseek-ai"
    dst_root = mine / "node_modules" / "@deepseek-ai"
    if not src_root.is_dir() or not dst_root.is_dir():
        return
    fixed = []
    for pkg in sorted(src_root.glob("dsh-*")):
        if not pkg.is_dir() or pkg.name == "dsh":
            continue
        target = dst_root / pkg.name
        try:
            have = json.loads((target / "package.json").read_text(encoding="utf-8"))["version"]
        except Exception:                      # noqa: BLE001
            continue
        want = json.loads((pkg / "package.json").read_text(encoding="utf-8")).get("version")
        # 只对齐"dsh 自己的版本"，其余依赖各平台可能确实不同，别乱动
        if have == want or want != version:
            continue
        if any(pkg.rglob("*.node")):           # 原生模块不能跨平台复制
            log(f"⚠️ {pkg.name} 版本不一致（{have} ≠ {want}）但有原生模块，跳过")
            continue
        _shutil.rmtree(target)
        _shutil.copytree(pkg, target)
        fixed.append(f"{pkg.name}: {have} → {want}")
    if fixed:
        log("对齐 harness 依赖版本：" + "；".join(fixed))


def verify_harness_starts(dst: Path, plat: str, *, timeout: int = 150) -> None:
    """**出包前的冒烟测试**：真的把 harness 拉起来，确认它能给出监听地址。

    为什么必须有这一步（血的教训）：v1.1.2/v1.1.3 发出的 Windows 包，harness
    **根本起不来**——``dsh`` 声明 ``@deepseek-ai/dsh-app-boot: ^0.1.6-alpha.1``，
    npm 解析到了 alpha.2，而 alpha.2 删掉了 ``watchUserPatches`` 这个导出，
    于是启动即 ``SyntaxError``，用户点「启动内置 harness」永远停在"启动中…"。

    这类"装得上、跑不起来"的问题：npm 成功、文件齐全、体积正常，
    **只有真的跑一次才看得见**。失败就把日志尾巴打出来并**中止出包**。
    """
    import subprocess

    run = dst / "run"
    run.mkdir(parents=True, exist_ok=True)
    log_path = run / "smoke-web.log"
    if log_path.exists():
        log_path.unlink()

    if plat == "win":
        node = dst / "runtime" / "win" / "node" / "node.exe"
        entry = (dst / "harness" / "win" / "lib" / "node_modules" / "@deepseek-ai"
                 / "dsh" / "lib" / "bin.js")
        if shutil.which("wine") is None:
            log("⚠️ 冒烟测试跳过：本机没有 wine，验不了 Windows 侧 harness")
            return
        argv = ["wine", str(node), str(entry), "web", "--no-open", "--port", "8977"]
        env_extra = {"WINEDEBUG": "-all", "WINEDLLOVERRIDES": "mscoree,mshtml="}
    else:
        node = dst / "runtime" / "linux" / "node" / "bin" / "node"
        entry = (dst / "harness" / "linux" / "lib" / "node_modules" / "@deepseek-ai"
                 / "dsh" / "lib" / "bin.js")
        argv = [str(node), str(entry), "web", "--no-open", "--port", "8977"]
        env_extra = {}
    if not node.is_file() or not entry.is_file():
        log("⚠️ 冒烟测试跳过：node/harness 不在预期位置")
        return

    env = dict(os.environ)
    env.update(env_extra)
    env["HOME"] = str(dst / "home")
    env.setdefault("USERPROFILE", str(dst / "home"))
    env["DSH_HOME"] = str(dst / "home" / ".dsh")
    env["XDG_CONFIG_HOME"] = str(dst / "home" / ".config")
    log(f"冒烟测试：启动 harness（{platform}，端口 8977）…")
    with open(log_path, "wb") as out:
        proc = subprocess.Popen(argv, stdout=out, stderr=subprocess.STDOUT, env=env,
                                cwd=str(dst))
    ok = False
    try:
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(2)
            text = log_path.read_text(encoding="utf-8", errors="replace")
            if "http://127.0.0.1" in text:
                ok = True
                break
            if any(p in text for p in ("SyntaxError", "ERR_MODULE_NOT_FOUND", "EADDRINUSE")):
                break
            if proc.poll() is not None:
                break
    finally:
        for closer in (proc.terminate, proc.kill):
            try:
                closer()
                proc.wait(timeout=10)
                break
            except Exception:                  # noqa: BLE001
                continue
    text = log_path.read_text(encoding="utf-8", errors="replace")
    if not ok and "EADDRINUSE" in text:
        log("⚠️ 冒烟测试跳过：端口 8977 被占用（本机已有 harness 在跑）")
        return
    if ok:
        log("✓ 冒烟测试通过：harness 能启动并给出监听地址")
        return
    log("✗ 冒烟测试失败：harness 起不来。日志尾部：")
    for line in text.strip().splitlines()[-8:]:
        log(f"    {line}")
    raise RuntimeError("harness 冒烟测试失败——这样的包发出去，用户只会看到「启动中…」")


def prune_heavy_packages(root: Path) -> int:
    """删掉 :data:`SKIP_HEAVY_PACKAGES` 里那些"整包跳过"的依赖，返回释放的字节数。

    ⚠️ **默认不删**（改成 opt-in 了，教训在下面）：
    v1.1.2 为了把 Windows 包从 470 MB 压到 357 MB，默认删掉了
    ``@deepseek-ai/libreoffice-kit-win32-x64``（325 MB 的 LibreOffice 运行时）。
    结果 Windows 上 **harness 起不来**（实测反馈："DSH 控制台无法启动 Harness"）——
    它是**运行时真的会被 require 到**的依赖，不是"只有预览时才用"的可选件。
    现在改成：默认**保留**，要瘦身得显式设 ``DSH_BUNDLE_DROP_LIBREOFFICE=1``，
    并且自己确认 harness 还能起来。

    教训：包体积是"体验问题"，功能是"能不能用"——不确定依赖是否可选时，先保功能。
    """
    import os

    if os.environ.get("DSH_BUNDLE_KEEP_OFFICE_PREVIEW"):
        log("保留 Office 预览链（DSH_BUNDLE_KEEP_OFFICE_PREVIEW=1，约 +325 MB）")
        return 0
    freed = 0
    for pattern in SKIP_HEAVY_PACKAGES:
        for path in sorted(root.rglob(f"@deepseek-ai/{pattern}*")):
            if not path.is_dir():
                continue
            size = du(path)
            shutil.rmtree(path, ignore_errors=True)
            freed += size
            log(f"跳过重包 {path.name}：-{human(size)}")
    return freed

QT_PRUNE = {
    # ⚠️ "translations" **不能**整目录删——见上面 QT_TRANSLATIONS_KEEP。
    "qml", "metatypes", "bin",
    # ⚠️ libexec **不能整目录删**：QtWebEngineProcess 就在里面，删了 GUI 版一开窗就
    # 崩（报 "could not find QtWebEngineProcess"）。只删 qml 那几个工具。
}
#: libexec 里可以删的（QML 工具链，控制台用不到；QtWebEngineProcess 必须留）
QT_LIBEXEC_PRUNE = {"qmlcachegen", "qmlimportscanner", "qmltyperegistrar", "rcc", "uic"}
QT_PLUGIN_KEEP = {
    "platforms", "imageformats", "iconengines", "styles", "platformthemes",
    "platforminputcontexts", "tls", "networkinformation", "generic",
}

#: 打包时跳过的杂项。
COPY_SKIP = {
    "__pycache__", ".git", ".venv", "shots", "node_modules/.cache",
}

MANIFEST_NAME = "version.json"


# --------------------------------------------------------------------- 工具
def log(msg: str) -> None:
    print(f"  {msg}", flush=True)


def du(path: Path) -> int:
    total = 0
    for base, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (Path(base) / name).lstat().st_size
            except OSError:
                pass
    return total


def human(n: int) -> str:
    for unit, div in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if n >= div:
            return f"{n / div:.1f} {unit}"
    return f"{n} B"


def dereference_symlinks(root: Path) -> int:
    """把包内所有软链换成**真文件**。

    这一步看着浪费（软链明明更省地方），但它是"解压即用"能不能成立的关键：

    * zip 格式**能**表达软链（``external_attr`` 高 16 位写 ``S_IFLNK``，内容是目标路径），
      ``unzip`` / ``7z`` / ``bsdtar`` 都会正确还原——实测过；
    * 但 **Python 的 ``zipfile`` 既不还原软链也不还原权限**（``_extract_member`` 里
      既没有 chmod 也没有 symlink 处理，CPython 至今如此）。用它解压，软链会变成
      **内容是目标路径的 20 字节文本文件**，于是 ``libpython3.14.so.1`` 不再是链接、
      Qt 的 ``libavcodec.so`` 变成一行文本——包直接跑不起来，而且报错完全指不到这里。

    赌"用户会用 unzip"是不负责任的：不少图形解压工具、`python -m zipfile`、
    各种语言自带的解压库都走同一条路。代价约 45 MB，换的是"任何工具解压都能用"。
    """
    replaced = 0
    gained = 0
    for path in sorted(root.rglob("*")):
        if not path.is_symlink():
            continue
        try:
            target = path.resolve(strict=True)
        except OSError:
            path.unlink(missing_ok=True)     # 断链：留着只会碍事
            continue
        if target.is_dir():
            path.unlink()
            shutil.copytree(target, path, symlinks=True)
            gained += du(path)
        else:
            data = target.read_bytes()
            mode = target.stat().st_mode
            path.unlink()
            path.write_bytes(data)
            path.chmod(mode | 0o644)
            gained += len(data)
        replaced += 1
    if replaced:
        log(f"软链落地：{replaced} 个 → 真文件（+{human(gained)}，换任何解压工具都能用）")
    return replaced


def copy_tree(src: Path, dst: Path, *, skip: set[str] | None = None,
              keep_only: set[str] | None = None, label: str = "") -> int:
    """拷贝目录树，**保留软链**（后面由 :func:`dereference_symlinks` 统一落地）。

    先原样保留、最后一并展开，而不是逐处手动 copy：软链可能来自源包（dsh 的
    ``node_modules/.bin``）、也可能来自我们自己去重（见下），一处漏了就是个
    "某台机器上莫名其妙跑不起来"的 bug。
    """
    skip = skip or set()
    if dst.exists():
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)

    def ignore(_dir: str, names: list[str]) -> set[str]:
        out = set()
        for name in names:
            if name in skip:
                out.add(name)
            elif keep_only is not None and name not in keep_only:
                out.add(name)
        return out

    shutil.copytree(src, dst, symlinks=True, ignore=ignore if (skip or keep_only) else None)
    size = du(dst)
    log(f"{label or src.name}: {human(size)}")
    return size


# --------------------------------------------------------------------- Linux
def build_linux_python(dst: Path) -> None:
    """自带一份可搬家的 CPython。

    ``/usr/bin/python3.14`` 动态链接 ``libpython3.14.so.1.0``，而且**没有 rpath**，
    所以在没有系统 Python 的机器上必须靠启动器设 ``LD_LIBRARY_PATH`` 指到包内的 lib
    （本机没有 patchelf，改不了 rpath；实测加载器会优先搜 LD_LIBRARY_PATH）。
    """
    py = dst / "runtime" / "linux" / "python"
    (py / "bin").mkdir(parents=True, exist_ok=True)
    (py / "lib").mkdir(parents=True, exist_ok=True)
    shutil.copy2(SYS_PYTHON, py / "bin" / "python3.14")
    (py / "bin" / "python3.14").chmod(0o755)
    shutil.copy2(SYS_LIBPYTHON, py / "lib" / SYS_LIBPYTHON.name)
    # libpython3.14.so.1 也放一份**真文件**（不建软链，理由见 dereference_symlinks）
    link = py / "lib" / "libpython3.14.so.1"
    if not link.exists():
        shutil.copy2(SYS_LIBPYTHON, link)

    copy_tree(SYS_STDLIB, py / "lib" / "python3.14", skip=STDLIB_SKIP, label="Python 标准库")

    # PySide6 + shiboken6 进 site-packages。dist-info 一起带上，pip 的元数据要它。
    site = py / "lib" / "python3.14" / "site-packages"
    site.mkdir(parents=True, exist_ok=True)
    for name in ("PySide6", "shiboken6"):
        src = SYS_SITE / name
        if src.is_dir():
            copy_tree(src, site / name, label=f"site-packages/{name}")
    for info in SYS_SITE.glob("*.dist-info"):
        if info.name.lower().startswith(("pyside6", "shiboken6")):
            copy_tree(info, site / info.name, label=f"  {info.name}")

    # 不去重成软链：最后会统一把软链落地成真文件，那样"去重"等于白做，
    # 还得来回拷 37 MB。留着这段注释是为了别有人再想加回去（见 dereference_symlinks）。

    # Qt 裁剪
    #
    # ⚠️ 两平台的 wheel 布局不一样：Linux 的 Qt 内容在 ``PySide6/Qt/`` 下，
    # 而 Windows wheel 直接把 Qt 内容放在 ``PySide6/`` 里（**没有 Qt/ 这一层**）。
    # 只认 Linux 那套路径的话，Windows 侧整块裁剪会被跳过——实测漏掉过
    # 60 MB 的 translations（304 个 .qm + 53 个 pak 原样进包）。
    qt = site / "PySide6" / "Qt"
    if not qt.is_dir() and (site / "PySide6").is_dir():
        qt = site / "PySide6"
        log("Qt 布局：Windows wheel（Qt 内容直接在 PySide6/ 下）")
    if qt.is_dir():
        for name in sorted(QT_PRUNE):
            target = qt / name
            if target.is_dir():
                shutil.rmtree(target)
                log(f"裁剪 Qt/{name}")
        # translations/：只留需要的几个 .qm（整目录 59 MB，留下的是 ~160 KB）
        # ⚠️ 两平台布局不同，而且 Windows wheel 上**两个位置同时存在**：
        #   Linux：PySide6/Qt/translations
        #   Windows：PySide6/Qt/translations（少量）+ PySide6/translations（60 MB 全量）
        # 早先只裁其中一处（还想当然地写成 if/else），结果 Windows 侧那 60 MB 原样进包。
        # 现在两处都裁。
        for tr_dir in (qt / "translations", qt.parent / "translations"):
            if not tr_dir.is_dir():
                continue
            removed = 0
            for f in list(tr_dir.iterdir()):
                if f.is_file() and not f.name.startswith(QT_TRANSLATIONS_KEEP):
                    f.unlink()
                    removed += 1
            kept = sorted(f.name for f in tr_dir.iterdir() if f.is_file())
            log(f"裁剪 {tr_dir.name}（{tr_dir.parent.name}）：删 {removed} 个，留 "
                f"{len(kept)} 个 ({', '.join(kept) if len(kept) <= 6 else '…'})")
            if not kept:
                log("  ⚠️ 一个翻译文件都没留——Qt 标准对话框会是英文")
            # Chromium 的语言包：只留中英两份（整目录 44 MB）
            locales = tr_dir / "qtwebengine_locales"
            if locales.is_dir():
                loc_removed = 0
                for f in list(locales.iterdir()):
                    if f.is_file() and f.name not in QT_LOCALES_KEEP:
                        f.unlink()
                        loc_removed += 1
                kept_loc = sorted(f.name for f in locales.iterdir() if f.is_file())
                log(f"裁剪 qtwebengine_locales：删 {loc_removed} 个，留 {kept_loc}")
        plugins = qt / "plugins"
        if plugins.is_dir():
            for child in list(plugins.iterdir()):
                if child.is_dir() and child.name not in QT_PLUGIN_KEEP:
                    shutil.rmtree(child)
                    log(f"裁剪 Qt/plugins/{child.name}")
        libexec = qt / "libexec"
        if libexec.is_dir():
            for name in sorted(QT_LIBEXEC_PRUNE):
                target = libexec / name
                if target.exists():
                    target.unlink()
                    log(f"裁剪 Qt/libexec/{name}")
        for huge in ("libQt6Designer.so.6", "libQt6Quick3D.so.6", "libQt6Charts.so.6",
                     "libQt6Pdf.so.6", "libQt6DataVisualization.so.6"):
            target = qt / "lib" / huge
            if target.exists():
                target.unlink()
                log(f"裁剪 Qt/lib/{huge}")


def _hoisted_npm_deps(npm_dir: Path) -> list[str]:
    """npm 的 dependencies 里，哪些不在它自己的 ``node_modules`` 下。

    这些就是被包管理器 hoist 到同级的，得单独拷。
    """
    try:
        pkg = json.loads((npm_dir / "package.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    deps = list((pkg.get("dependencies") or {}).keys())
    return [d for d in deps if not (npm_dir / "node_modules" / d).exists()]


def dedupe_identical(root: Path, *, label: str = "") -> int:
    """把**内容相同**的文件换成相对软链，省掉重复占的空间。

    PySide6 里 ``libavcodec.so`` / ``.so.61`` / ``.so.61.19.101`` 是三份一模一样的
    15 MB 实体文件（正常安装里前两个本该是软链）。实测这种重复有 37 MB，
    而 zip 是按文件各自压缩的，重复内容不会互相抵消——省下来的是实打实的。
    """
    import hashlib

    groups: dict[tuple[str, int], list[Path]] = {}
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            key = (hashlib.md5(path.read_bytes()).hexdigest(), path.stat().st_size)
        except OSError:
            continue
        groups.setdefault(key, []).append(path)

    saved = 0
    for (_, size), files in groups.items():
        if len(files) < 2 or size < 4096:
            continue
        # 名字最长的那个当正本（.so.61.19.101 比 .so 更"真实"），其余链过去
        files.sort(key=lambda f: len(f.name), reverse=True)
        canon = files[0]
        for other in files[1:]:
            try:
                other.unlink()
                other.symlink_to(os.path.relpath(canon, other.parent))
                saved += size
            except OSError:
                pass
    if saved:
        log(f"去重{label}：省下 {human(saved)}")
    return saved


def build_linux_node(dst: Path) -> None:
    node = dst / "runtime" / "linux" / "node"
    (node / "bin").mkdir(parents=True, exist_ok=True)
    shutil.copy2(SYS_NODE, node / "bin" / "node")
    (node / "bin" / "node").chmod(0o755)
    log(f"node: {human((node / 'bin' / 'node').stat().st_size)}")
    npm_root = node / "lib" / "node_modules"
    copy_tree(SYS_NPM, npm_root / "npm", label="npm")

    # ⚠️ **npm 的依赖不一定都在它自己目录下。**
    #
    # Arch 的 npm 包把 `semver` / `node-gyp` / `nopt` 这些 **hoist 到了
    # `/usr/lib/node_modules/`**（npm 目录的**同级**）。只拷 `npm/` 的话，
    # 包里那份 npm 一跑就 `Cannot find module 'semver/functions/satisfies'`——
    # 于是"升级内置 harness"永远失败、版本号纹丝不动（用户报的正是这个）。
    #
    # 精确算法：读 npm 的 package.json，凡是 dependencies 里**在 npm/node_modules
    # 下找不到**的，就是被 hoist 出去的，从同级目录补过来。比"把同级全拷一遍"精确，
    # 也不会把 pnpm 那种不相干的东西带进来。
    hoisted = _hoisted_npm_deps(SYS_NPM)
    for name in hoisted:
        src = SYS_NPM.parent / name
        if src.is_dir():
            copy_tree(src, npm_root / name, label=f"  npm 的 hoist 依赖 {name}")
        else:
            log(f"⚠️ npm 需要 {name}，但在 {src} 找不到")
    if hoisted:
        log(f"npm 补齐 hoist 依赖：{'、'.join(hoisted)}")


# --------------------------------------------------------------------- Windows
def build_windows_runtime(dst: Path, node_version: str, py_version: str) -> bool:
    """下载 Windows 侧的 node / Python / PySide6 轮子并铺好。

    ⚠️ 这一段**只能下载和摆放，没法在本机执行验证**（跑不了 exe）。产物齐备、逻辑自洽，
    但"在 Windows 上真的能起来"必须由用户在 Windows 上验收。
    """
    import urllib.request

    win = dst / "runtime" / "win"
    win.mkdir(parents=True, exist_ok=True)
    cache = ROOT / ".build-cache"
    cache.mkdir(exist_ok=True)

    def fetch(urls, name: str) -> Path:
        """按顺序试多个源，成功即止。

        python.org 在本机实测只有 25 KB/s（15 MB 的 embed 包要下十分钟），
        所以除了官方源再挂一个国内镜像——这是**构建期**的下载，不影响产物的可移植性。
        """
        if isinstance(urls, str):
            urls = [urls]
        out = cache / name
        if out.is_file() and out.stat().st_size > 0:
            log(f"用缓存 {name}")
            return out
        last: Exception | None = None
        for url in urls:
            try:
                log(f"下载 {url}")
                # 先写 .part 再改名：中途被杀（或断网）会留下半截文件，而"存在且非空"
                # 的缓存判定会把它当成好的——下一次构建就会解出一个损坏的压缩包，
                # 报错还很难懂。改名是原子的，所以只有完整文件才会出现在缓存里。
                part = out.with_suffix(out.suffix + ".part")
                urllib.request.urlretrieve(url, part)  # noqa: S310 - 固定地址
                part.replace(out)
                log(f"  -> {human(out.stat().st_size)}")
                return out
            except Exception as exc:  # noqa: BLE001 - 换下一个源
                last = exc
                log(f"  失败（{type(exc).__name__}），换下一个源")
        raise RuntimeError(f"{name} 所有源都下不动：{last}")

    # --- node
    node_zip = fetch([
        f"https://nodejs.org/dist/v{node_version}/node-v{node_version}-win-x64.zip",
        f"https://mirrors.huaweicloud.com/nodejs/v{node_version}/node-v{node_version}-win-x64.zip",
        f"https://npmmirror.com/mirrors/node/v{node_version}/node-v{node_version}-win-x64.zip",
    ], f"node-v{node_version}-win-x64.zip")
    node_dir = win / "node"
    if node_dir.exists():
        shutil.rmtree(node_dir)
    node_dir.mkdir(parents=True)
    with zipfile.ZipFile(node_zip) as zf:
        zf.extractall(win / "_tmp_node")
    inner = next((win / "_tmp_node").iterdir())
    for item in inner.iterdir():                       # 拍平到 runtime/win/node
        shutil.move(str(item), str(node_dir / item.name))
    shutil.rmtree(win / "_tmp_node", ignore_errors=True)
    log(f"Windows node 就位：{human(du(node_dir))}")

    # --- python（embeddable）
    # 镜像排在官方前面：本机实测 python.org 只有 25 KB/s（12 MB 要下八分钟），
    # 华为云同样这个文件 15 MB/s。官方留作回退。
    py_zip = fetch([
        f"https://mirrors.huaweicloud.com/python/{py_version}/python-{py_version}-embed-amd64.zip",
        f"https://www.python.org/ftp/python/{py_version}/python-{py_version}-embed-amd64.zip",
    ], f"python-{py_version}-embed-amd64.zip")
    py_dir = win / "python"
    if py_dir.exists():
        shutil.rmtree(py_dir)
    py_dir.mkdir(parents=True)
    with zipfile.ZipFile(py_zip) as zf:
        zf.extractall(py_dir)
    # embeddable 默认把搜索路径写死在 ._pth 里，必须显式加上 site-packages。
    #
    # ⚠️ 这里的 zip 名**不能靠版本号拼**：标准库压缩包叫 ``python314.zip``（ABI 标签是
    # ``major+minor``），而 ``"3.14.7".replace(".", "")`` 会算出 ``3147``——写进去之后
    # Python 找不到标准库，启动时打一大段 "Python path configuration" 就退了。
    # 所以从**真实的 ._pth 文件名**推（``python314._pth`` → ``314``），永远不会算错。
    for pth in py_dir.glob("python*._pth"):
        tag = pth.stem.replace("python", "")
        stdlib_zip = f"python{tag}.zip"
        if not (py_dir / stdlib_zip).is_file():
            log(f"⚠️ {stdlib_zip} 不在 embeddable 包里，._pth 可能不对")
        # ⚠️ 只要有 ._pth 文件，Python 就进入**隔离模式：PYTHONPATH 被完全忽略**。
        # 于是控制台里"启动 TUI/GUI 版"用的 `python -m dsh_console.xxx` 在 Windows 上
        # 必然失败（报 No module named 'dsh_console'）——而 .bat 走的是 `app\main.py`，
        # 所以命令行能跑、按钮不能跑，特别难查（用户报的正是这个：TUI 闪退、GUI 报错）。
        # 修法是把控制台源码目录**写进 ._pth**（相对路径是相对 python.exe 所在目录的）：
        #   runtime\win\python\ 往上三层 = 包根，再进 app 就是控制台源码。
        pth.write_text(
            f"{stdlib_zip}\n.\nLib\\site-packages\n..\\..\\..\\app\nimport site\n",
            encoding="utf-8",
        )
        log(f"python 搜索路径：{stdlib_zip} + Lib\\site-packages + ..\\..\\..\\app")
    log("Windows python(embeddable) 就位")

    # --- PySide6 轮子（下载后直接解包到 site-packages，目标机上不需要 pip）
    site = py_dir / "Lib" / "site-packages"
    site.mkdir(parents=True, exist_ok=True)
    wheels = ROOT / ".build-cache" / "wheels-win"
    wheels.mkdir(parents=True, exist_ok=True)
    # windows-curses 是必需的：**Windows 版 Python 不自带 curses**（Unix 专有），
    # 不装它「Start-DSH-Terminal.bat」会直接 ModuleNotFoundError。它按 Python 版本发轮子，
    # 所以要指定 --python-version 拿到对得上的那一个。
    cmd = [
        sys.executable, "-m", "pip", "download", "--only-binary=:all:", "--no-deps",
        "--platform", "win_amd64", "--python-version", py_version.rsplit(".", 1)[0],
        "--dest", str(wheels),
        "PySide6-Essentials", "PySide6-Addons", "shiboken6", "windows-curses",
    ]
    log("下载 Windows 版 PySide6 轮子（约 200 MB，只下一次）")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if proc.returncode != 0:
        log(f"⚠️ 轮子下载失败：{(proc.stderr or proc.stdout).strip()[-300:]}")
        return False
    for whl in sorted(wheels.glob("*.whl")):
        with zipfile.ZipFile(whl) as zf:
            zf.extractall(site)
        log(f"  解包 {whl.name}")
    log(f"Windows PySide6 就位：{human(du(site))}")
    return True


# --------------------------------------------------------------------- 公共部分
def build_harness(dst: Path, *, version: str, node_version: str,
                  platforms: tuple[str, ...] = ("linux", "win")) -> None:
    """按 npm 的 global prefix 形状摆放 harness，**两个平台各一份**。

    摆成 ``<prefix>/lib/node_modules/…`` 是为了让 ``npm install -g --prefix`` 能直接
    接管后续升级——自己拼目录的话，自我更新就得另写一套解包逻辑。

    ## 为什么必须是两份

    ``node_modules`` 里有**编译好的原生模块**，它们是平台专属的：

    * ``@img/sharp-linux-x64`` vs ``@img/sharp-win32-x64``
    * ``@koromix/koffi-linux-x64`` vs ``@koromix/koffi-win32-x64``
    * ``node-pty/prebuilds/linux-x64`` vs ``prebuilds/win32-x64``
    * ``node-addon-require-builtin-linux-x64-gnu`` vs ``…-win32-x64-msvc``

    第一版只拷了本机（Linux）那一份，结果 Windows 上 harness 根本起不来，报
    ``Could not load the "sharp" module using the win32-x64 runtime`` 和
    ``Cannot find the native Koffi module``——**这个错只有在 Windows 上跑才会出现**，
    在 Linux 上验证一万遍也看不出来（靠 wine 才抓到的）。

    Windows 那份用 ``npm install --os=win32 --cpu=x64`` 装：npm 支持跨平台拉取
    optionalDependencies，所以能在这台 Linux 机器上装出正确的 Windows 树。
    """
    root = dst / "harness"
    if "linux" in platforms:
        _build_harness_linux(root / "linux")
    if "win" in platforms:
        win_ok = _build_harness_windows(root / "win", version=version)
        if not win_ok:
            log("⚠️ Windows 版 harness 没装成——Windows 上 harness 会因为缺原生模块起不来")


def _build_harness_linux(prefix: Path) -> None:
    pkg = prefix / "lib" / "node_modules" / "@deepseek-ai" / "dsh"
    copy_tree(SYS_DSH_PKG, pkg, label="harness（Linux 原生模块）")
    (prefix / "bin").mkdir(parents=True, exist_ok=True)
    shim = prefix / "bin" / "dsh"
    shim.write_text(
        "#!/bin/sh\n"
        "# 便携包内的 dsh：直接交给包里的 node，不走 PATH。\n"
        'HERE="$(cd "$(dirname "$0")/.." && pwd)"\n'
        'NODE="$HERE/../../runtime/linux/node/bin/node"\n'
        '[ -x "$NODE" ] || NODE="$(command -v node)"\n'
        'exec "$NODE" "$HERE/lib/node_modules/@deepseek-ai/dsh/lib/bin.js" "$@"\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)


def _build_harness_windows(prefix: Path, *, version: str) -> bool:
    """用 npm 装一份 Windows 版 harness（含 win32 原生模块）。"""
    npm = shutil.which("npm")
    if not npm:
        log("⚠️ 本机没有 npm，跳过 Windows 版 harness")
        return False
    if prefix.exists():
        shutil.rmtree(prefix)
    prefix.mkdir(parents=True, exist_ok=True)
    log(f"安装 Windows 版 harness（--os=win32 --cpu=x64，约 280 MB）")
    proc = subprocess.run(
        [npm, "install", "-g", f"@deepseek-ai/dsh@{version}",
         "--os=win32", "--cpu=x64", "--prefix", str(prefix),
         "--no-audit", "--no-fund"],
        capture_output=True, text=True, timeout=3600,
    )
    if proc.returncode != 0:
        log(f"⚠️ 装失败：{(proc.stderr or proc.stdout).strip()[-200:]}")
        return False
    pkg = prefix / "lib" / "node_modules" / "@deepseek-ai" / "dsh"
    if not pkg.is_dir():
        log("⚠️ 装完了但找不到包目录")
        return False
    log(f"harness（Windows 原生模块）: {human(du(pkg))}")

    # npm 在 Linux 上只会生成软链 shim，Windows 需要的 .cmd 得自己写。
    # （.cmd **不能被 CreateProcess 直接执行**，要经 cmd /c，acp_client 里已处理。）
    (prefix / "bin").mkdir(parents=True, exist_ok=True)
    for stale in (prefix / "bin").iterdir():
        if stale.is_symlink() or stale.name == "dsh":
            stale.unlink(missing_ok=True)
    cmd = prefix / "bin" / "dsh.cmd"
    cmd.write_bytes(
        (
            "@echo off\r\n"
            "rem 便携包内的 dsh（Windows）：交给包里的 node，不走 PATH\r\n"
            'set "HERE=%~dp0.."\r\n'
            'set "NODE=%HERE%\\..\\..\\runtime\\win\\node\\node.exe"\r\n'
            'if not exist "%NODE%" set "NODE=node"\r\n'
            '"%NODE%" "%HERE%\\lib\\node_modules\\@deepseek-ai\\dsh\\lib\\bin.js" %*\r\n'
            "exit /b %ERRORLEVEL%\r\n"
        ).encode("gbk")
    )
    log("harness/win/bin/dsh.cmd（Windows 启动 shim）")
    return True


def build_profile(dst: Path) -> None:
    """把 web profile 一起带上，否则首次启动要联网装插件。"""
    target = dst / "home" / ".dsh" / "profiles" / "web"
    target.mkdir(parents=True, exist_ok=True)
    for name in ("package.json", "cordis.patch.yml", "cordis.yml", "pnpm-lock.yaml",
                 "pnpm-workspace.yaml"):
        src = DSH_PROFILE / name
        if src.is_file():
            shutil.copy2(src, target / name)
    nm = DSH_PROFILE / "node_modules"
    if nm.is_dir():
        copy_tree(nm, target / "node_modules", label="web profile 插件")
    # 不拷 sessions / credentials / browser-panel profile：
    # 前两个是隐私，第三个是 74 MB 的 Chrome 用户目录，首次运行会自己建。
    _scrub_home_paths(target)
    log("profile 就位（不含凭据与会话）")


#: 把构建机的家目录路径换成这个中性值。
#:
#: 为什么必须换：profile 里有三类文件会**记下绝对路径**——
#: ``cordis.patch.yml`` 的 ``browserPath``、pnpm 的 ``.modules.yaml``（storeDir）、
#: ``.pnpm-workspace-state-v1.json``（项目路径）。原样打进包里，等于把构建者的
#: 用户名连同家目录结构一起发给每个下载者（实测漏过：包里三处 ``/home/<用户名>``）。
#:
#: 换成中性值而不是删文件：这几个位置在目标机上本来就是"对不上"的（新机器的
#: 家目录不可能一样），pnpm 发现对不上会自己重建状态；删掉反而可能让 pnpm 认为
#: node_modules 没被纳管。改成一个显然不是真人家的路径，既不复现泄露，也不改行为。
NEUTRAL_HOME = "/home/user"

#: 只扫这些小文件；node_modules 里几万个第三方文件没必要逐个读。
SCRUB_MAX_BYTES = 2 * 1024 * 1024


def _scrub_home_paths(root: Path) -> int:
    """把 ``root`` 下文本文件里的构建机家目录路径替换掉，返回改了几个文件。"""
    home = str(Path.home()).encode()
    neutral = NEUTRAL_HOME.encode()
    if home == neutral:
        return 0
    changed = 0
    for p in root.rglob("*"):
        if not p.is_file() or p.is_symlink():
            continue
        try:
            if p.stat().st_size > SCRUB_MAX_BYTES:
                continue
            data = p.read_bytes()
        except OSError:
            continue
        if home not in data:
            continue
        try:
            p.write_bytes(data.replace(home, neutral))
            changed += 1
        except OSError:
            continue
    if changed:
        log(f"profile 里的家目录路径已中性化：{changed} 个文件（{NEUTRAL_HOME}）")
    return changed


#: Windows 上的标准用户目录。**必须建**：把 USERPROFILE 指到包内之后，
#: 任何按 ``%USERPROFILE%\Desktop`` 之类去找的地方（原生文件对话框、浏览器、
#: 各种 .NET/Win32 程序）都会因为目录不存在而报"位置不可用"。
#: 实测：harness 的「选择工作区目录」就是这么弹错的。
WINDOWS_HOME_DIRS = (
    "Desktop", "Documents", "Downloads", "Pictures", "Music", "Videos",
    "AppData/Local", "AppData/Roaming", "AppData/LocalLow",
)


def build_home_skeleton(dst: Path, platforms: tuple[str, ...] = ("linux", "win")) -> None:
    for rel in ("home/.dsh", "home/.config", "home/.cache", "run"):
        (dst / rel).mkdir(parents=True, exist_ok=True)
    if "win" in platforms:
        # 这些是 Windows 的 shell 目录（Desktop/Documents/AppData…）。Linux 专用包里
        # 建它们只会让人困惑"我这儿怎么冒出个 AppData"。
        for name in WINDOWS_HOME_DIRS:
            (dst / "home" / name).mkdir(parents=True, exist_ok=True)
    settings = DSH_PROFILE.parent.parent / "settings.yaml"
    if settings.is_file():
        shutil.copy2(settings, dst / "home" / ".dsh" / "settings.yaml")
        log("settings.yaml 就位")


def build_app(dst: Path) -> None:
    app = dst / "app"
    app.mkdir(parents=True, exist_ok=True)
    for name in ("main.py", "requirements.txt"):
        shutil.copy2(ROOT / name, app / name)
    copy_tree(ROOT / "dsh_console", app / "dsh_console", skip=COPY_SKIP, label="控制台源码")
    copy_tree(ROOT / "tools", app / "tools", skip=COPY_SKIP, label="工具脚本")
    # 捐赠页的收款码与名单。**必须放在 app/ 下**：页面按"项目根/donate"定位它，
    # 而包里项目根就是 app/，放到包根就找不到了（收款码会变成占位说明）。
    donate = ROOT / "donate"
    if donate.is_dir():
        copy_tree(donate, app / "donate", label="donate（捐赠页数据）")
    for extra in ("README.md", "LICENSE"):
        if (ROOT / extra).is_file():
            shutil.copy2(ROOT / extra, app / extra)
    # 文档与截图。**必须带上**：包里的 README 引用 docs/screenshots/*.png，
    # 不拷的话打开 README 满屏碎图。开发日志一并带上，方便用户查设计原因。
    if (ROOT / "docs").is_dir():
        copy_tree(ROOT / "docs", app / "docs", label="文档与截图")


#: 记下"包里哪些文件需要可执行位"的清单文件名。
EXEC_MANIFEST = "exec-manifest.txt"


def write_exec_manifest(dst: Path) -> None:
    """把包里所有该有可执行位的文件记成清单，给启动器自愈用。

    **不要手写这个列表。** 原来启动器里是硬编码几个路径（python、node、dsh），
    包一长大就漏——实测漏掉了 ``PySide6/Qt/libexec/QtWebEngineProcess``，
    于是内置浏览器/GUI 一起报 ``zygote_host_impl_linux.cc Check failed: ENOENT``
    （看着像"文件不存在"，其实是**没有可执行位**）。

    构建时扫一遍真实权限写下来，启动器照着 chmod，永远不会漏。
    """
    lines: list[str] = []
    for path in sorted(dst.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            if path.stat().st_mode & 0o111:
                lines.append(str(path.relative_to(dst)))
        except OSError:
            continue
    (dst / EXEC_MANIFEST).write_text("\n".join(lines) + "\n", encoding="utf-8")
    log(f"{EXEC_MANIFEST}：{len(lines)} 个文件需要可执行位")


def build_icons(dst: Path) -> None:
    out = dst / "icons"
    out.mkdir(parents=True, exist_ok=True)
    assets = ROOT / "assets"
    for name in ("icon.png", "icon-256.png", "icon-128.png", "icon-64.png",
                 "icon-48.png", "icon-32.png"):
        src = assets / name
        if src.is_file():
            shutil.copy2(src, out / name)
    # Windows 需要 .ico：用 PySide6 自己转，不引外部工具
    ico = out / "icon.ico"
    try:
        from PIL import Image  # type: ignore[import-not-found]

        Image.open(assets / "icon-256.png").save(
            ico, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
        )
        log(f"图标 icon.ico（Pillow）：{human(ico.stat().st_size)}")
    except Exception:
        _ico_from_qt(assets, ico)
    log(f"图标 {len(list(out.iterdir()))} 个")


def _ico_from_qt(assets: Path, ico: Path) -> None:
    """没有 Pillow 就用 Qt 写 ICO。

    Qt 的 QImageWriter 支持写 ico，但一次只能写一个尺寸；多尺寸 ICO 需要自己拼容器。
    这里写一个"最大尺寸"的 ICO——Windows 会缩放，够用且不引依赖。
    """
    if not (assets / "icon-256.png").is_file():
        return
    script = (
        "import sys;from PySide6.QtGui import QImage,QImageWriter\n"
        f"img=QImage({str(assets / 'icon-256.png')!r})\n"
        f"w=QImageWriter({str(ico)!r},b'ico');w.write(img)\n"
    )
    try:
        subprocess.run([sys.executable, "-c", script], check=True, timeout=60,
                       capture_output=True)
        log(f"图标 icon.ico（Qt）：{human(ico.stat().st_size)}")
    except Exception as exc:  # noqa: BLE001
        log(f"⚠️ 生成 ico 失败：{exc}")


# --------------------------------------------------------------------- 入口
#: 平台 → 目录/压缩包的后缀。产物名要一眼能看出是哪个平台的包。
PLATFORM_SUFFIX = {"linux": "-Linux", "win": "-Windows"}


def build(out: Path, *, platforms: tuple[str, ...] = ("linux", "win"),
          node_version: str, py_version: str) -> Path:
    """构建可移植包。

    ``platforms`` 决定打哪几个平台：``("linux",)`` / ``("win",)`` 出**专用包**
    （只带自己那套运行时、harness 和启动器），``("linux", "win")`` 出以前那种
    二合一的包。专用包的意义很实际：二合一的包 660 MB，而用户只用得上其中一半，
    另一半（另一个平台的 Python + node + 平台专属原生模块）纯属白下载。
    """
    single = len(platforms) == 1
    name = PROJECT_NAME + (PLATFORM_SUFFIX[platforms[0]] if single else "")
    dst = out / name
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    log(f"目标：{dst}（平台：{'、'.join(platforms)}）")

    if "linux" in platforms:
        build_linux_python(dst)
        build_linux_node(dst)
    build_app(dst)
    build_harness(dst, version=_dsh_version(), node_version=node_version,
                  platforms=platforms)
    prune_heavy_packages(dst)
    # ⚠️ 这里千万不要写 `platform`：那是 stdlib 模块（本文件 import 了它），
    # 传进去会让 `dst / platform` 炸成 TypeError。平台的真实来源是 platforms 元组。
    plat = platforms[0] if len(platforms) == 1 else ""
    if plat:
        align_dsh_deps(dst, plat)
    # ⚠️ 冒烟测试**不放在出包流程里**：跑一次 harness 会让它把缺的依赖自己装回来
    # （self-heal），交付件当场被改胖（实测 Linux 包 309 → 426 MB）。
    # 需要验证时在**副本**上手动调用：
    #   python -c "import sys;sys.path.insert(0,'tools');import build_bundle as B;    #              from pathlib import Path;B.verify_harness_starts(Path('/tmp/copy'),'win')"

    build_profile(dst)
    build_home_skeleton(dst, platforms)
    build_icons(dst)

    win_ok = False
    if "win" in platforms:
        try:
            win_ok = build_windows_runtime(dst, node_version, py_version)
        except Exception as exc:  # noqa: BLE001
            log(f"⚠️ Windows 运行时构建失败：{type(exc).__name__}: {exc}")

    from launchers import write_launchers

    # 软链落地必须在**所有拷贝都做完之后**、写启动器之前：一处漏展开，解压出来就是
    # 一个"某台机器上莫名其妙跑不起来"的包（见 dereference_symlinks 的说明）。
    dereference_symlinks(dst)

    write_launchers(dst, node_version=node_version, py_version=py_version,
                    windows_ready=win_ok, platforms=platforms)
    # Windows 的 .exe 启动器：双击不闪黑框（比 .vbs 更不容易被杀软拦），
    # 出问题还能弹原生对话框。构建机没有 clang/lld 时不算失败——.bat/.vbs 仍在。
    if "win" in platforms:
        try:
            from build_win_launcher import build as build_win_exes

            build_win_exes(dst)
        except Exception as exc:  # noqa: BLE001
            log(f"⚠️ .exe 启动器未生成：{type(exc).__name__}: {exc}（.bat/.vbs 不受影响）")
    # 清单要在**启动器之后**写：启动器自己也是可执行文件，得一起进清单
    write_exec_manifest(dst)

    # 「版本」是**控制台自己**的版本（自更新拿它比较），不是 harness 的。
    # 一开始这里填的是 harness 版本，结果"控制台 0.1.5-rc.1"看着像 harness 的版本号，
    # 升级 harness 还会让控制台自更新误判成"有新版本"。
    manifest = {
        "名称": PROJECT_NAME,
        "版本": _console_version(),
        "构建时间": time.strftime("%Y-%m-%d %H:%M:%S"),
        "构建主机": f"{platform.system()} {platform.machine()}",
        "harness": {"包": "@deepseek-ai/dsh", "版本": _dsh_version()},
        "node": node_version,
        "python": py_version,
        "适用平台": {"linux": "Linux", "win": "Windows 10/11"}.get(
            platforms[0] if len(platforms) == 1 else "", "Linux + Windows 10/11"),
        "windows_运行时": ("已内置" if win_ok else "未内置（首次启动时由 .bat 自行准备）")
        if "win" in platforms else "不适用（本包为 Linux 专用）",
    }
    (dst / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log(f"清单 {MANIFEST_NAME}：harness {manifest['harness']['版本']}")
    log(f"合计：{human(du(dst))}")
    return dst


def _console_version() -> str:
    """读控制台自己的 ``__version__``。"""
    init = ROOT / "dsh_console" / "__init__.py"
    try:
        for line in init.read_text(encoding="utf-8").splitlines():
            if line.startswith("__version__"):
                return line.split("=", 1)[1].strip().strip("\"'")
    except OSError:
        pass
    return "unknown"


def _dsh_version() -> str:
    try:
        return json.loads((SYS_DSH_PKG / "package.json").read_text(encoding="utf-8"))["version"]
    except Exception:  # noqa: BLE001
        return "unknown"


def preflight(out: Path, *, hard_gb: float = 2.0, warn_gb: float = 5.0) -> None:
    """构建前看目标盘够不够、是不是 tmpfs。

    "写到一半炸掉"最坏的情况不是丢产物，而是**把盘撑满**：本机 /tmp 是 tmpfs，
    撑满之后连 shell 都起不来（执行工具要往临时目录写脚本），于是"清理现场"
    这件本该一秒完成的事反而做不了。所以：

    * 是 tmpfs → 重点警告（但不拦，万一人家就是内存大）；
    * 剩余 < ``warn_gb`` → 警告（产物 1.5 GB + 压缩包 0.6 GB + 解压验证 1.5 GB）；
    * 剩余 < ``hard_gb`` → 直接拒绝，省得写到一半失败还留下一堆半成品。
    """
    probe = out if out.exists() else out.parent
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        usage = shutil.disk_usage(probe)
    except OSError as exc:
        log(f"⚠️ 无法确认 {probe} 的剩余空间（{exc}），继续但请留意磁盘")
        return

    free_gb = usage.free / 1024 ** 3
    if free_gb < hard_gb:
        raise SystemExit(
            f"\n目标盘空间不足：{probe} 只剩 {free_gb:.1f} GB，连构建都放不下（至少 {hard_gb:.0f} GB）。\n"
            f"换个 --out，或先清理该盘。\n"
        )
    if free_gb < warn_gb:
        log(f"⚠️ {probe} 只剩 {free_gb:.1f} GB：产物约 1.5 GB + 压缩包 0.6 GB，"
            f"再加解压验证就不够了。建议先腾点地方。")
    else:
        log(f"目标盘剩余 {free_gb:.1f} GB")

    # tmpfs 是内存盘：撑满会把整台机器的临时文件一起拖死，单独警告一次
    try:
        target = str(probe)
        for line in Path("/proc/mounts").read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.split()
            if len(parts) >= 3 and target.startswith(parts[1]) and parts[2] == "tmpfs":
                log(f"⚠️ 注意：{probe} 在 tmpfs（内存盘）上。构建 + 解压会吃掉几个 GB 内存，"
                    f"而且撑满之后连 shell 都起不来——清理现场反而做不了。"
                    f"强烈建议换到真实磁盘（默认的 <项目>/dist 就是）。")
                break
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="构建 DSH + 控制台 的可移植包")
    ap.add_argument("--out", default=str(ROOT / "dist"),
                    help="输出目录（默认 <项目>/dist；**别用 /tmp**，见模块开头）")
    ap.add_argument("--platform", choices=("all", "linux", "win"), default="all",
                    help="打哪个平台：all=二合一（默认，兼容老行为）/ linux=Linux 专用 / win=Windows 专用")
    ap.add_argument("--no-windows", action="store_true",
                    help="（旧参数）等价于 --platform linux")
    ap.add_argument("--zip", action="store_true", help="构建完顺便压成 zip")
    ap.add_argument("--node-version", default="26.8.2")
    ap.add_argument("--python-version", default="3.14.7")
    args = ap.parse_args(argv)

    out = Path(args.out).expanduser()
    preflight(out)
    sys.path.insert(0, str(Path(__file__).resolve().parent))

    if args.no_windows and args.platform == "all":
        args.platform = "linux"          # 旧参数兼容
    groups: list[tuple[str, ...]] = {
        "all": [("linux", "win")],
        # 拆开打：两个专用包**各自独立构建**。不共用中间产物是有意的——
        # 两边要装不同平台的 harness（npm --os/--cpu），共用只会互相干扰。
        "linux": [("linux",)],
        "win": [("win",)],
    }[args.platform]

    from launchers import zip_bundle

    made = []
    for platforms in groups:
        dst = build(out, platforms=platforms,
                    node_version=args.node_version, py_version=args.python_version)
        if args.zip:
            zip_path = zip_bundle(dst)
            made.append((dst.name, zip_path.stat().st_size))
            log(f"压缩包：{zip_path}（{human(zip_path.stat().st_size)}）")
    if made:
        log("\n== 产物 ==")
        for name, size in made:
            log(f"  {name}.zip  {human(size)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
