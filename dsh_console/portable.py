"""便携模式：整套东西都躺在解压出来的目录里，不碰系统。

## 怎么判定

启动器设一个环境变量 ``DSH_CONSOLE_PORTABLE=<解压出来的根目录>``，这里就以它为准。
没设就是普通安装模式（走 systemd、用 ``~/.dsh``），行为完全不变。

## 路径怎么重定向的（这是个刻意的取舍）

**不**去改十几个模块里写死的 ``Path.home()``，而是让启动器把 ``HOME``（Windows 上是
``USERPROFILE``）指到 ``<包根>/home``。于是 ``~/.dsh``、``~/.config/dsh-console``、
``~/.cache/dsh-console`` 全都自动落进包里——一处设置，全局生效，也不容易漏。

代价是子树里的程序（git、ssh 之类）也会看到这个 HOME。对便携应用来说这正是想要的：
"解压到 U 盘，插到哪台机器都不留痕"。真要找用户真正的家目录，用
:func:`real_home`，它读的是启动器存下来的原始值。
"""

from __future__ import annotations

import json
import os
import platform
import sys
from pathlib import Path

#: 启动器设置的环境变量：值是解压出来的根目录。
ENV_ROOT = "DSH_CONSOLE_PORTABLE"

#: 启动器把用户**真实**的家目录存在这里（改 HOME 之前记下来的）。
ENV_REAL_HOME = "DSH_CONSOLE_REAL_HOME"

#: 包内清单文件，记录版本与构建时间。
MANIFEST = "版本信息.json"


def root() -> Path | None:
    """便携根目录；没启用便携模式返回 ``None``。"""
    value = (os.environ.get(ENV_ROOT) or "").strip()
    if not value:
        return None
    path = Path(value).expanduser()
    try:
        return path.resolve()
    except OSError:
        return path


def enabled() -> bool:
    return root() is not None


def real_home() -> Path:
    """用户真正的家目录（改 HOME 之前的那一个）。"""
    saved = (os.environ.get(ENV_REAL_HOME) or "").strip()
    if saved:
        return Path(saved)
    if platform.system() == "Windows":
        drive = os.environ.get("HOMEDRIVE") or ""
        path = os.environ.get("HOMEPATH") or ""
        if drive or path:
            return Path(drive + path)
    return Path.home()


# ------------------------------------------------------------------ 包内位置
def _sub(*parts: str) -> Path | None:
    r = root()
    return (r.joinpath(*parts)) if r else None


def home_dir() -> Path | None:
    """包内的 DSH_HOME（``<根>/home/.dsh``）。"""
    return _sub("home", ".dsh")


def run_dir() -> Path | None:
    """运行时目录：pid 文件、日志、临时文件。"""
    return _sub("run")


def app_dir() -> Path | None:
    """控制台源码所在目录。"""
    return _sub("app")


def harness_prefix() -> Path | None:
    """本平台那份 harness 的 npm global prefix。

    包里**两个平台各一份**（``harness/linux``、``harness/win``），因为 node_modules 里
    有编译好的原生模块，是平台专属的：``@img/sharp-*-x64``、``@koromix/koffi-*-x64``、
    ``node-pty/prebuilds/*``。只带一份的话，另一个平台上 harness 直接起不来
    （报 "Could not load the sharp module using the win32-x64 runtime"）。

    做成 npm 的 global prefix 形状，是为了升级只用 ``npm --prefix``。
    """
    return _sub("harness", os_key())


def harness_package() -> Path | None:
    r = harness_prefix()
    return (r / "lib" / "node_modules" / "@deepseek-ai" / "dsh") if r else None


def harness_entry() -> Path | None:
    pkg = harness_package()
    return (pkg / "lib" / "bin.js") if pkg else None


# ------------------------------------------------------------------ 平台
def os_key() -> str:
    """运行时的平台目录名：``linux`` / ``win`` / ``macos``。"""
    system = platform.system()
    return {"Linux": "linux", "Windows": "win", "Darwin": "macos"}.get(system, system.lower())


def runtime_dir() -> Path | None:
    return _sub("runtime", os_key())


def node_bin() -> Path | None:
    """包内的 node 可执行文件。"""
    rt = runtime_dir()
    if rt is None:
        return None
    exe = "node.exe" if os_key() == "win" else "bin/node"
    cand = rt / "node" / exe
    if cand.is_file():
        return cand
    # Windows 的 node zip 解出来是 node-vXX-win-x64/node.exe，构建脚本已经拍平，
    # 这里再兜一层，免得目录结构变了就找不到
    for hit in (rt / "node").glob("**/node.exe"):
        return hit
    return None


def dsh_bin() -> str | None:
    """包内 ``dsh`` 可执行文件的位置。

    POSIX 上是 ``harness/bin/dsh``（一个 sh 脚本），**Windows 上必须是 ``dsh.cmd``**——
    Windows 没有 sh，而 ``.cmd`` 又不能被 ``CreateProcess`` 直接执行（得走 ``cmd /c``，
    见 :meth:`AcpClient.start`）。
    """
    prefix = harness_prefix()
    if prefix is None:
        return None
    names = ("dsh.cmd", "dsh") if os_key() == "win" else ("dsh",)
    for name in names:
        cand = prefix / "bin" / name
        if cand.is_file():
            return str(cand)
    return None


def npm_cli() -> Path | None:
    """包内的 npm CLI 入口（``npm-cli.js``）。

    用 ``node npm-cli.js`` 而不是 ``npm`` 那个 shell 脚本：Windows 上没有 shell 脚本，
    而且直接调 js 不依赖 PATH。
    """
    rt = runtime_dir()
    if rt is None:
        return None
    for cand in (
        rt / "node" / "lib" / "node_modules" / "npm" / "bin" / "npm-cli.js",
        rt / "node" / "node_modules" / "npm" / "bin" / "npm-cli.js",
    ):
        if cand.is_file():
            return cand
    for hit in (rt / "node").glob("**/npm-cli.js"):
        return hit
    return None


def python_bin() -> Path | None:
    rt = runtime_dir()
    if rt is None:
        return None
    cand = rt / "python" / ("python.exe" if os_key() == "win" else "bin/python3.14")
    return cand if cand.is_file() else None


# ------------------------------------------------------------------ 清单
def manifest() -> dict:
    """读包内的 ``版本信息.json``；读不到就给个空 dict，别让界面炸。"""
    r = root()
    if r is None:
        return {}
    try:
        return json.loads((r / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def describe() -> str:
    """一行摘要，给自检/关于页用。"""
    r = root()
    if r is None:
        return "普通安装模式（未启用便携包）"
    m = manifest()
    bits = [f"便携模式：{r}"]
    if m.get("版本"):
        bits.append(f"包版本 {m['版本']}")
    if m.get("构建时间"):
        bits.append(f"构建于 {m['构建时间']}")
    return "　·　".join(bits)


def ensure_dirs() -> None:
    """把运行时需要的目录建好（首次启动时）。"""
    for d in (home_dir(), run_dir()):
        if d is not None:
            try:
                d.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass


def pythonw() -> str:
    """当前解释器（便携模式下就是包内那个）。"""
    return sys.executable or "python3"
