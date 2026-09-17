#!/usr/bin/env python3
"""生成可移植包的启动器、图标清单与说明文档。

## 启动器的核心就一件事：把 HOME 关进包里

``HOME``（Windows 上 ``USERPROFILE``）指到 ``<包根>/home``，``DSH_HOME`` 指到
``<包根>/home/.dsh``。控制台里十几个模块写死的 ``Path.home()`` 全都因此落进包里——
一处设置全局生效，比逐个改源码可靠得多（见 :mod:`dsh_console.portable`）。

## 顺带要处理的三件事

* **``LD_LIBRARY_PATH``**：包内的 ``python3.14`` 动态链接 ``libpython3.14.so.1.0`` 且没有
  rpath，目标机没有系统 Python 时必须靠这个变量找到包内副本（本机没有 patchelf 改不了 rpath）。
* **PATH 前置包内的 node**：免得用到目标机上另一个版本的 node。
* **ZIP 里必须保留可执行位**：Windows 的「解压」不管这个，Linux 的 unzip 会管；
  所以除了设权限，还在说明里写明 ``chmod +x`` 的补救命令。
"""

from __future__ import annotations

import os
import stat
import time
import zipfile
from pathlib import Path

def log(msg: str) -> None:
    """构建日志。build_bundle 里也有一份同名的，这里为了本模块能单独跑。"""
    print(f"  {msg}", flush=True)


LINUX = "Start-DSH-Console.sh"
LINUX_TUI = "Start-DSH-Terminal.sh"
LINUX_WEB = "Start-DSH-Web-UI.sh"
LINUX_INSTALL = "Install-Desktop-Shortcut.sh"
WIN = "Start-DSH-Console.bat"
WIN_TUI = "Start-DSH-Terminal.bat"
WIN_WEB = "Start-DSH-Web-UI.bat"
WIN_SHORTCUT = "Create-Desktop-Shortcut.bat"
DESKTOP = "dsh-console.desktop"
README = "README-Portable.md"

#: 各个启动器给 main.py 传的参数
ARGS = {"console": [], "tui": ["--tui"], "web": ["--web"]}

LINUX_ENV = """\
HERE="$(cd "$(dirname "$0")" && pwd)"
# 开发/调试时可以覆盖成 1 来看真实的家目录
export DSH_CONSOLE_PORTABLE="$HERE"
export DSH_CONSOLE_REAL_HOME="${HOME:-}"
# 把 HOME 关进包里：~/.dsh、~/.config/dsh-console、~/.cache/dsh-console 全都落在 home/ 下
export HOME="$HERE/home"
export DSH_HOME="$HERE/home/.dsh"
export XDG_CONFIG_HOME="$HERE/home/.config"
export XDG_CACHE_HOME="$HERE/home/.cache"
export XDG_DATA_HOME="$HERE/home/.local/share"
export PATH="$HERE/runtime/linux/node/bin:$HERE/harness/linux/bin:$PATH"
# 包里的 python 动态链接 libpython 且没有 rpath，靠这个变量找到包内副本
export LD_LIBRARY_PATH="$HERE/runtime/linux/python/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
PY="$HERE/runtime/linux/python/bin/python3.14"

# 自己修可执行位。
#
# **不是所有解压工具都还原权限**：Python 的 zipfile 连软链带权限一概不管
# （`_extract_member` 里既没有 chmod 也没有 symlink 处理），于是 python3.14、
# node、QtWebEngineProcess 全都变成 644——包看着解压好了，其实一个都跑不起来，
# 而且报错只说"找不到 python"。与其在说明里教用户 chmod，不如启动器自己补上：
# 这样"解压即用"就不取决于用户拿什么工具解压。
# 清单由构建时扫描真实权限生成（见 build_bundle.write_exec_manifest）。
# **不要改成手写路径列表**：漏一个就是一个"文件明明在却报 ENOENT"的怪 bug——
# 实测漏掉过 QtWebEngineProcess，内置浏览器和 GUI 一起起不来。
MANIFEST="$HERE/exec-manifest.txt"
if [ -f "$MANIFEST" ]; then
  while IFS= read -r rel; do
    [ -n "$rel" ] || continue
    f="$HERE/$rel"
    if [ -f "$f" ] && [ ! -x "$f" ]; then
      chmod +x "$f" 2>/dev/null || true
    fi
  done < "$MANIFEST"
else
  # 兜底：清单丢了也别让包彻底不能用
  for f in "$HERE"/*.sh "$HERE/runtime/linux/python/bin/python3.14" \
           "$HERE/runtime/linux/node/bin/node"; do
    [ -f "$f" ] && [ ! -x "$f" ] && chmod +x "$f" 2>/dev/null
  done
fi

if [ ! -x "$PY" ]; then
  echo "便携包不完整：找不到或无法执行 $PY" >&2
  echo "如果所在分区不允许执行（比如挂载时带了 noexec），把整个文件夹拷到本地磁盘再试。" >&2
  exit 1
fi
"""

LINUX_TMPL = """#!/bin/sh
# {title} —— 便携启动器，全部依赖都在本目录内，不碰系统。
set -eu
{env}
exec "$PY" "$HERE/app/main.py"{args} "$@"
"""

WIN_TMPL = """@echo off
rem {title} - portable launcher, everything lives in this folder.
setlocal
rem 这个 .bat 是 **GBK(cp936)** 编码的（见本模块开头）。中文 Windows 默认就是 936，
rem 但英文版 Windows 默认 437/1252，不显式切一下的话下面的中文提示全是乱码。
rem 命令本身全是 ASCII，所以哪怕 chcp 失败（系统没装 936）也只是提示难看，不影响启动。
chcp 936 >nul 2>nul
set "HERE=%~dp0"
if "%HERE:~-1%"=="\\" set "HERE=%HERE:~0,-1%"

set "DSH_CONSOLE_PORTABLE=%HERE%"
set "DSH_CONSOLE_REAL_HOME=%USERPROFILE%"
rem Keep HOME inside the folder so ~/.dsh, ~/.config, ~/.cache land here
set "HOME=%HERE%\\home"
set "USERPROFILE=%HERE%\\home"
set "HOMEDRIVE="
set "HOMEPATH="
set "DSH_HOME=%HERE%\\home\\.dsh"
set "XDG_CONFIG_HOME=%HERE%\\home\\.config"
set "XDG_CACHE_HOME=%HERE%\\home\\.cache"
set "XDG_DATA_HOME=%HERE%\\home\\.local\\share"
set "PATH=%HERE%\\runtime\\win\\node;%HERE%\\harness\\win\\bin;%PATH%"
rem 补齐 Windows 的**标准用户目录**。
rem
rem 我们把 USERPROFILE 指到了包内的 home（为了不污染系统），但 Windows 上大量程序
rem 是按 %USERPROFILE%\\Desktop 这类路径去找东西的——目录不存在时，原生文件对话框
rem 会直接弹"位置不可用"（实测撞到：
rem harness 的「选择工作区目录」想打开 <包>\\home\\Desktop，而它根本没建）。
rem 这里每次启动都补一遍：老版本解压出来的包也能被修好。
for %%D in (Desktop Documents Downloads Pictures Music Videos) do (
  if not exist "%HERE%\\home\\%%~D" mkdir "%HERE%\\home\\%%~D" >nul 2>nul
)
for %%D in ("AppData\\Local" "AppData\\Roaming" "AppData\\LocalLow") do (
  if not exist "%HERE%\\home\\%%~D" mkdir "%HERE%\\home\\%%~D" >nul 2>nul
)
rem 让 Python 的 stdout/stderr 用 UTF-8，中文提示在控制台里不会变成问号
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

set "PY=%HERE%\\runtime\\win\\python\\python.exe"
rem 这里刻意**不用 if (...)** 括号块，改用 goto 标签。
rem 原因很实际：Windows 上下载重名文件会生成「DSH-Console (1)」这样的文件夹，
rem 路径里的 ) 会把括号块提前"闭合"，后面的 echo 就跑到块外面去了，行为完全错乱。
rem 单行 if + goto 没有这个问题。
if not exist "%PY%" goto :missing

"%PY%" "%HERE%\\app\\main.py"{args} %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" goto :failed
exit /b %RC%

:missing
echo.
echo 便携包不完整：这里没有运行时的 python.exe
echo 请先双击「Install-Windows-Runtime.bat」，它会把 node / Python / PySide6 装进本文件夹。
echo.
echo 期望位置：
echo   %PY%
echo.
if not "%DSH_NO_PAUSE%"=="1" pause
exit /b 1

:failed
echo.
echo 程序退出，代码 %RC%
echo.
rem 被 .vbs 隐藏启动时不要 pause：那时没有窗口，等一个看不见的按键会留下一个
rem 隐藏的 cmd 进程赖着不走（由 .vbs 设 DSH_NO_PAUSE=1 告知）
if not "%DSH_NO_PAUSE%"=="1" pause
exit /b %RC%
"""

WIN_VBS = (
    "' 无窗口启动（不会闪一个黑框）——把 .bat 包在 VBScript 里跑。\n"
    "' VBScript 里嵌引号靠「两个引号当一个」，所以下面那串拼出来是带引号的完整路径。\n"
    'Set shell = CreateObject("WScript.Shell")\n'
    'Set fso = CreateObject("Scripting.FileSystemObject")\n'
    "here = fso.GetParentFolderName(WScript.ScriptFullName)\n"
    "' 告诉 .bat 别 pause：隐藏运行时等一个看不见的按键会留下一个赖着不走的 cmd 进程\n"
    'shell.Environment("Process")("DSH_NO_PAUSE") = "1"\n'
    'shell.Run """" & here & "\\{bat}""", 0, False\n'
)

DESKTOP_TMPL = """[Desktop Entry]
Type=Application
Version=1.0
Name=DSH Console
Name[zh_CN]=DSH 控制台
GenericName=Harness Console
Comment=Portable DSH console (harness + console, self-contained)
Comment[zh_CN]=便携版 DSH 控制台（自带 harness 与运行时，不依赖系统环境）
Exec={exec_line}
Icon={icon}
Terminal=false
StartupNotify=true
Categories=Development;
Keywords=dsh;harness;console;deepseek;billing;pricing;控制台;账单;
"""

INSTALL_DESKTOP = """#!/bin/sh
# 把桌面条目装进 ~/.local/share/applications（**这一步会写用户目录**，
# 所以做成手动执行——便携包的默认行为是"除了自己那个文件夹哪都不碰"）。
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$APPS"
sed "s|@HERE@|$HERE|g" "$HERE/{desktop}" > "$APPS/dsh-console-portable.desktop"
chmod 644 "$APPS/dsh-console-portable.desktop"
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS" 2>/dev/null || true
echo "已安装：$APPS/dsh-console-portable.desktop"
echo "Super 键唤出启动器，搜索「DSH 控制台」即可。"
"""

WIN_SHORTCUT_PS = r"""
# 在 Windows 上创建带图标的桌面快捷方式。
# 用 PowerShell 的 WScript.Shell：.lnk 的二进制格式自己拼太脆，交给系统最稳。
$ErrorActionPreference = 'Stop'
$HERE = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$desktop = [Environment]::GetFolderPath('Desktop')
$lnk = Join-Path $desktop 'DSH 控制台.lnk'
$shell = New-Object -ComObject WScript.Shell
$sc = $shell.CreateShortcut($lnk)
$sc.TargetPath = Join-Path $HERE 'Start-DSH-Console.bat'
$sc.WorkingDirectory = $HERE
$icon = Join-Path $HERE '图标\icon.ico'
if (Test-Path $icon) { $sc.IconLocation = $icon }
$sc.Description = 'DSH 控制台（便携版）'
$sc.Save()
Write-Host "已Create-Desktop-Shortcut：$lnk"
"""


def _write(path: Path, text: str, *, executable: bool = False,
           newline: str = "\n", encoding: str = "utf-8", bom: bool = False) -> None:
    """写一个文件。

    换行和编码**必须按目标平台给对**，这不是洁癖：

    * **Windows 批处理必须是 CRLF。** cmd.exe 按 CRLF 解析 .bat，遇到纯 LF 会**吞掉行首
      字符**——`set "DSH_CONSOLE_PORTABLE=…"` 变成 `H_CONSOLE_PORTABLE=…`、`if not exist`
      变成 `xist`，然后一整屏"不是内部或外部命令"。这是实测踩到的（用户报错原文里
      `'H_CONSOLE_PORTABLE' 不是内部或外部命令` 就是它）。
    * **.bat/.vbs 要用 GBK(cp936)。** cmd.exe 按控制台代码页读批处理，中文 Windows 上是
      936；UTF-8 的中文会被当成 GBK 解出乱码（`（图形界面）` → `浘褰㈢晫闈級`）。
      GBK 是 ASCII 的超集，所以逻辑部分是纯 ASCII 就没风险。
    * **.ps1 要用带 BOM 的 UTF-8。** Windows PowerShell 5.1 没有 BOM 时会按 ANSI 读，
      中文注释和字符串全乱，甚至解析失败。
    * ``.sh``/``.desktop`` 反过来必须是 LF + UTF-8。
    """
    data = text.replace("\r\n", "\n").replace("\n", newline).encode(encoding)
    if bom:
        data = b"\xef\xbb\xbf" + data
    path.write_bytes(data)
    if executable:
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _linux_launcher(dst: Path, name: str, title: str, args: list[str]) -> None:
    arg_text = (" " + " ".join(args)) if args else ""
    _write(dst / name, LINUX_TMPL.format(title=title, env=LINUX_ENV, args=arg_text),
           executable=True)


def _windows_launcher(dst: Path, name: str, title: str, args: list[str],
                      *, silent: bool = True) -> None:
    """写一套 Windows 启动器：.bat（CRLF + GBK）+ .vbs。

    **默认无终端**：图形控制台和网页界面都是 GUI 程序，配一个黑框既难看也没用
    （用户明确要求"不要显示终端"）。所以：
      * ``Start-DSH-Console-Silent.vbs`` —— 主入口，双击无黑框；
      * ``Start-DSH-Console.bat`` —— 保留，出问题时用它看输出（会显示终端）。
    TUI 那条例外：它**就是**终端程序，必须有终端。

    注意 ``title`` 里的中文会被编成 GBK；万一有 GBK 编不出的字符（比如 emoji），
    这里会直接抛错而不是悄悄写坏——写坏了在 Windows 上就是一屏乱码，很难倒查。
    """
    arg_text = (" " + " ".join(args)) if args else ""
    _write(dst / name, WIN_TMPL.format(title=title, args=arg_text),
           newline="\r\n", encoding="gbk")
    # 无终端的那个用**主名字**（用户会去双击的就是它），带终端的 .bat 留着排错。
    # TUI 不生成隐藏版：**隐藏的终端等于没有 TUI**，那个文件只会让人困惑。
    if not silent:
        return
    # 后缀点明 -Silent：.bat 和 .vbs 摆在一起时，光看名字不知道双击哪个会冒黑框。
    # 名字要和 README-Portable.md 里写的一致（那边一直叫它 Silent 版）。
    _write(dst / (name[:-4] + "-Silent.vbs"), WIN_VBS.format(bat=name),
           newline="\r\n", encoding="gbk")


WIN_BOOTSTRAP_NAME = "Install-Windows-Runtime.ps1"

#: Windows 侧的运行时补装脚本。
#:
#: 什么时候用得上：构建这套包时没能把 Windows 运行时打进去（比如构建机下载失败），
#: 或者你只想要一个精简包、首次运行时再补。**它只写本文件夹**，不碰系统。
#:
#: 为什么不用 pip 装 PySide6：embeddable 版 Python 没有 pip，装 pip 再 pip install
#: 多两道容易坏的环节。而 **wheel 本质就是 zip**——直接从 PyPI 拿 whl 解包到
#: site-packages 更短更稳。
WIN_BOOTSTRAP_PS = r'''
# 补装 Windows 运行时（node + Python + PySide6），全部装进本文件夹的 runtime\win\。
# 只写本文件夹，不碰系统。
#
# 注意 param() **必须是脚本的第一条语句**（注释可以，别的语句不行）——写成
# `$ErrorActionPreference = 'Stop'` 在前、param 在后，PowerShell 直接报语法错，
# 而且错误信息指向最后一行，很难联想到这里。
param(
  [string]$NodeVersion = '26.8.2',
  [string]$PythonVersion = '3.14.7'
)
$ErrorActionPreference = 'Stop'
# $PSScriptRoot 比 $MyInvocation.MyCommand.Path 可靠：后者在某些调用方式下是空的
$HERE = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$WIN  = Join-Path $HERE 'runtime\win'
$TMP  = Join-Path $HERE 'run\_bootstrap'
New-Item -ItemType Directory -Force -Path $WIN, $TMP | Out-Null

function Get-First {
  param([string[]]$Urls, [string]$Out)
  foreach ($u in $Urls) {
    try {
      Write-Host "  下载 $u"
      Invoke-WebRequest -Uri $u -OutFile $Out -UseBasicParsing
      return
    } catch { Write-Host "    失败：$($_.Exception.Message)" }
  }
  throw "所有源都下不动：$Out"
}

Write-Host "==> node $NodeVersion"
$nodeZip = Join-Path $TMP 'node.zip'
Get-First @(
  "https://nodejs.org/dist/v$NodeVersion/node-v$NodeVersion-win-x64.zip",
  "https://npmmirror.com/mirrors/node/v$NodeVersion/node-v$NodeVersion-win-x64.zip"
) $nodeZip
Expand-Archive -Path $nodeZip -DestinationPath $TMP -Force
$nodeDir = Join-Path $WIN 'node'
if (Test-Path $nodeDir) { Remove-Item -Recurse -Force $nodeDir }
Move-Item (Join-Path $TMP "node-v$NodeVersion-win-x64") $nodeDir

Write-Host "==> Python $PythonVersion (embeddable)"
$pyZip = Join-Path $TMP 'py.zip'
Get-First @(
  "https://mirrors.huaweicloud.com/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip",
  "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip"
) $pyZip
$pyDir = Join-Path $WIN 'python'
if (Test-Path $pyDir) { Remove-Item -Recurse -Force $pyDir }
Expand-Archive -Path $pyZip -DestinationPath $pyDir -Force
$site = Join-Path $pyDir 'Lib\site-packages'
New-Item -ItemType Directory -Force -Path $site | Out-Null
# embeddable 把搜索路径写死在 ._pth 里，必须显式加上 site-packages
Get-ChildItem $pyDir -Filter 'python*._pth' | ForEach-Object {
  $tag = ($_.BaseName -replace 'python', '')
  Set-Content -Path $_.FullName -Encoding ASCII -Value @(
    "python$tag.zip", ".", "Lib\site-packages", "import site"
  )
}

Write-Host "==> PySide6（从 PyPI 取 whl 直接解包）"
foreach ($pkg in @('shiboken6', 'PySide6-Essentials', 'PySide6-Addons')) {
  Write-Host "  $pkg"
  $meta = Invoke-RestMethod -Uri "https://pypi.org/pypi/$pkg/json" -UseBasicParsing
  $whl = $meta.urls | Where-Object { $_.filename -like '*win_amd64.whl' } | Select-Object -First 1
  if (-not $whl) { throw "$pkg 没有 Windows 轮子" }
  $out = Join-Path $TMP $whl.filename
  Invoke-WebRequest -Uri $whl.url -OutFile $out -UseBasicParsing
  Expand-Archive -Path $out -DestinationPath $site -Force
}

Remove-Item -Recurse -Force $TMP -ErrorAction SilentlyContinue
Write-Host ""
Write-Host "完成。现在可以双击 Start-DSH-Console.bat 了。"
'''


def _check_windows_files(dst: Path) -> None:
    """构建时逐字节检查 Windows 脚本，不合格就让构建**失败**。

    这个检查是被一次真实事故逼出来的：`.bat` 写成了纯 LF，cmd.exe 会吞掉每行的头几个
    字符，于是 `set "DSH_CONSOLE_PORTABLE=…"` 变成 `H_CONSOLE_PORTABLE=…`，用户看到的
    是一屏"不是内部或外部命令"。而这种错在 Linux 上**完全看不出来**——文件能读、能显示、
    语法看着也对。所以规则必须机器来查。
    """
    problems: list[str] = []

    for path in sorted(dst.iterdir()):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        raw = path.read_bytes()

        if suffix in (".bat", ".cmd", ".vbs"):
            # 1) 不允许裸 LF：每一处 \n 前面必须是 \r
            if raw.replace(b"\r\n", b"").count(b"\n"):
                problems.append(f"{path.name}: 有裸 LF（Windows 脚本必须全是 CRLF）")
            if not raw.endswith(b"\r\n") and raw.count(b"\r\n"):
                problems.append(f"{path.name}: 最后一行没有 CRLF 结尾")
            # 2) 必须能按 GBK 解码（中文 Windows 的控制台代码页）
            try:
                text = raw.decode("gbk")
            except UnicodeDecodeError as exc:
                problems.append(f"{path.name}: 不是合法 GBK（{exc}）")
                continue
            # 3) **命令动词**必须是 ASCII。
            #
            #    只查第一个词，不查整行：启动器里的**中文注释和提示**是刻意保留的
            #    （GBK + 936 代码页下完全正确），文件名已经全是 ASCII。真正的风险是
            #    命令本身被代码页带坏——那才是"不是内部或外部命令"的来源。
            for i, line in enumerate(text.splitlines(), 1):
                stripped = line.strip()
                if not stripped or stripped.startswith(("rem ", "rem\t", "::", "'")):
                    continue          # rem / :: / ' 都是注释
                if stripped.lower().startswith(("echo", "pause", "title")):
                    continue          # 纯输出的行可以是中文
                verb = stripped.split(None, 1)[0]
                if any(ord(ch) > 127 for ch in verb):
                    problems.append(f"{path.name}:{i}: 命令动词不是 ASCII：{verb[:30]}")

        elif suffix == ".ps1":
            if not raw.startswith(b"\xef\xbb\xbf"):
                problems.append(f"{path.name}: 缺少 UTF-8 BOM（PowerShell 5.1 会按 ANSI 读）")
            if raw.replace(b"\r\n", b"").count(b"\n"):
                problems.append(f"{path.name}: 有裸 LF")
            body = raw[3:].decode("utf-8", "replace")
            first_code = next(
                (ln.strip() for ln in body.splitlines()
                 if ln.strip() and not ln.strip().startswith("#")), ""
            )
            # param() 必须是第一条语句，否则 PowerShell 报语法错且指向最后一行
            if "param(" in body and not first_code.lower().startswith("param("):
                problems.append(f"{path.name}: param() 不是第一条语句（PowerShell 会报语法错）")

    if problems:
        raise SystemExit(
            "Windows 启动器检查未通过：\n" + "\n".join(f"  ✗ {p}" for p in problems) +
            "\n（这类问题在 Linux 上肉眼看不出，但到了 Windows 就是一屏报错）"
        )
    log("Windows 脚本检查通过（CRLF / GBK / BOM / param 位置）")


def write_launchers(dst: Path, *, node_version: str, py_version: str,
                    windows_ready: bool, platforms: tuple[str, ...] = ("linux", "win")) -> None:
    """写启动器。

    ``platforms`` 决定写哪一套 —— 拆成"Linux 专用包 / Windows 专用包"之后，
    各自的包里**只该有自己那套启动器**：一个 Linux 用户看到一堆 `.bat`/`.vbs`
    只会困惑"我该点哪个"，反之亦然。
    """
    if "linux" in platforms:
        _linux_launcher(dst, LINUX, "启动 DSH 控制台（图形界面）", ARGS["console"])
        _linux_launcher(dst, LINUX_TUI, "启动 DSH 终端界面（TUI）", ARGS["tui"])
        _linux_launcher(dst, LINUX_WEB, "启动 DSH 网页界面（浏览器）", ARGS["web"])
        _write(dst / LINUX_INSTALL, INSTALL_DESKTOP.replace("{desktop}", DESKTOP),
               executable=True)

    if "win" in platforms:
        _windows_launcher(dst, WIN, "启动 DSH 控制台（图形界面）", ARGS["console"], silent=True)
        # TUI 例外：它本身就是终端程序，藏掉终端就没得用了
        _windows_launcher(dst, WIN_TUI, "启动 DSH 终端界面（TUI）", ARGS["tui"], silent=False)
        _windows_launcher(dst, WIN_WEB, "启动 DSH 网页界面（浏览器）", ARGS["web"], silent=True)

    # .bat/.vbs 一律 CRLF + GBK，.ps1 一律 CRLF + 带 BOM 的 UTF-8（理由见 _write）
    if "win" in platforms:
        _write(dst / WIN_SHORTCUT,
               "@echo off\n"
               "rem 在桌面创建带图标的快捷方式\n"
               'powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Create-Desktop-Shortcut.ps1"\n'
               "pause\n",
               newline="\r\n", encoding="gbk")
        _write(dst / "Create-Desktop-Shortcut.ps1", WIN_SHORTCUT_PS,
               newline="\r\n", encoding="utf-8", bom=True)
        _write(dst / WIN_BOOTSTRAP_NAME, WIN_BOOTSTRAP_PS,
               newline="\r\n", encoding="utf-8", bom=True)
        _write(dst / "Install-Windows-Runtime.bat",
               "@echo off\n"
               "rem 下载并安装 Windows 侧运行时到本文件夹（只写这里，不碰系统）\n"
               f'powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0{WIN_BOOTSTRAP_NAME}"\n'
               "pause\n",
               newline="\r\n", encoding="gbk")

    if "linux" in platforms:
        # Linux 的桌面条目：Exec 用包内启动器的绝对路径（@HERE@ 由安装脚本替换）
        if (dst / "icons" / "icon.png").is_file():
            icon = "@HERE@/icons/icon.png"
        else:
            icon = "@HERE@/icons/icon-256.png"
        _write(dst / DESKTOP, DESKTOP_TMPL.format(exec_line="@HERE@/" + LINUX, icon=icon))

    _check_windows_files(dst)

    _write(dst / README, _readme(dst, node_version, py_version, windows_ready,
                                 platforms=platforms))
    counts = " + ".join(x for x in (
        "Linux 4 个" if "linux" in platforms else "",
        "Windows 8 个" if "win" in platforms else "",
    ) if x)
    print(f"  启动器：{counts} + 说明")


def _readme(dst: Path, node_version: str, py_version: str, windows_ready: bool,
            platforms: tuple[str, ...] = ("linux", "win")) -> str:
    win_note = (
        "已随包附带，**无需联网**。"
        if windows_ready
        else "**未随包附带**（构建时没下下来）：双击一次 `Install-Windows-Runtime.bat`，"
             "它会用系统自带的 PowerShell 把 node / Python / PySide6 装进 `runtime\\win\\`，"
             "**只写本文件夹**，需要联网一次。"
    )
    # 专用包里只写自己平台的启动方式：给 Linux 用户看一堆 .bat 只会让人困惑
    _linux_howto = """### Linux

```sh
./Start-DSH-Console.sh      # 图形控制台
./Start-DSH-Terminal.sh    # 终端界面（TUI）
./Start-DSH-Web-UI.sh    # 起 harness 并用浏览器打开
```

> **权限不用管**：启动器每次启动都会自己把 python / node / QtWebEngineProcess 的
> 可执行位补上。有些解压工具（比如 Python 的 `zipfile`）不还原权限，补这一步
> 是为了"解压即用"不取决于你拿什么工具解压。
> 万一连启动器都跑不起来（显示 `Permission denied`），用 `sh Start-DSH-Console.sh`
> 绕过可执行位，或者 `chmod +x *.sh`。

想在应用启动器里搜到它，执行一次 `./Install-Desktop-Shortcut.sh`
""" if "linux" in platforms else ""

    _perm_row = ("| `Permission denied` | 用 `sh Start-DSH-Console.sh` 绕过可执行位，"
                 "或 `chmod +x *.sh` |") if "linux" in platforms else ""
    _win_row = ("| Windows 上冒出终端黑框 | 双击 **`.exe`**（或同名的 `.vbs`）而不是 `.bat`。"
                "TUI 例外——它本身就是终端程序 |") if "win" in platforms else ""

    _win_howto = """### Windows 10 / 11

**推荐双击 `.exe`**（原生启动器，双击不会闪黑框，也不需要脚本宿主）：

| 双击 | 启动什么 |
|---|---|
| `Start-DSH-Console.exe` | 图形控制台（无控制台窗口） |
| `Start-DSH-Web-UI.exe` | 起 harness 并用浏览器打开界面 |
| `Start-DSH-Terminal.exe` | 终端界面（TUI，自带终端窗口） |
| `Start-DSH-Console-Debug.exe` | **出问题时用这个**：带控制台窗口的图形控制台，报错能看见 |

`.vbs`（`Start-DSH-Console-Silent.vbs`）与 `.bat` 仍然保留：`.vbs` 效果和 `.exe` 一样；
`.bat` 会显示终端，适合排查。终端界面也可以用 `Start-DSH-Terminal.bat`。

想要桌面图标：双击 `Create-Desktop-Shortcut.bat`。
""" if "win" in platforms else ""

    return f"""# DSH Console · Portable

**Unzip and run.** No Python, Node.js or any dependency needs to be installed, and nothing is
written outside this folder — the whole package is self-contained, so a USB stick works too.

| | |
|---|---|
| **Start DSH Console** | `./Start-DSH-Console.sh` (Linux) · **`Start-DSH-Console.exe`** (Windows, no console window) |
| **Start DSH Terminal** | `./Start-DSH-Terminal.sh` · `Start-DSH-Terminal.bat` — curses TUI |
| **Start DSH Web UI** | `./Start-DSH-Web-UI.sh` · `Start-DSH-Web-UI.bat` — runs the harness and opens your browser |
| **Desktop icon** | `./Install-Desktop-Shortcut.sh` · `Create-Desktop-Shortcut.bat` |
| **Troubleshooting** | `Start-DSH-Console-Debug.exe` (Windows) — same GUI, but keeps a console so you can read the error |

`HOME` / `USERPROFILE` are redirected into the package's `home/`, so `~/.dsh`, `~/.config` and
`~/.cache` all land here. Delete the folder and it is gone without a trace.

*Console usage below is in Chinese; the app UI is Chinese as well.*

---

# DSH 控制台 · 便携版

解压即用：**不需要**预装 Python、Node.js 或任何依赖，也**不会**往系统里写东西。
整个包是自包含的，拷到 U 盘插到另一台机器上照样跑。

## 怎么启动

{_linux_howto}
{_win_howto}
## 三个界面的区别

| 启动器 | 是什么 |
|---|---|
| **控制台** | 管 harness 的桌面应用：服务启停、账单、插件市场、Harness 版本与升级 |
| **终端界面** | 在终端里直接跟 agent 对话（curses TUI，自带会话/模型/工作区切换） |
| **网页界面** | 起 harness 的 web 服务并用浏览器打开带 token 的地址 |

## 不污染系统环境

启动器把 `HOME`（Windows 上是 `USERPROFILE`）指到包内的 `home/`，并把 `DSH_HOME`
指到 `home/.dsh`。于是：

* `~/.dsh`（会话、配置、插件）→ `home/.dsh/`
* `~/.config/dsh-console`、`~/.cache/dsh-console` → `home/.config/`、`home/.cache/`
* 系统目录、注册表：完全不碰

想确认？删掉整个文件夹就干净了，没有任何残留。

## 自我更新

* **harness**：控制台左侧点「Harness 版本」按钮 → 选通道 → 「更新 harness」。
  用的是包内的 npm（`--prefix` 指向包内 `harness/`），只写本文件夹。
* **控制台自身**：同一页有「检查控制台更新」，指向一个发布地址（可在
  `home/.config/dsh-console/config.json` 里配 `consoleUpdateUrl`）；
  下载新包后只替换 `app/` 与 `tools/`，`home/`、`runtime/`、`harness/` 原样保留。

## 目录说明

```
app/          控制台源码          harness/   harness（npm prefix 形状）
runtime/      自带的 node/python  home/      重定向后的家目录（.dsh 在这里）
run/          运行时 pid 与日志   icons/     各尺寸图标与 .ico
```

## 出问题怎么办

| 现象 | 处理 |
|---|---|
{_perm_row}
| `无法执行` / noexec | 分区被挂载成 `noexec`；把整个文件夹拷到本地磁盘 |
| 起不来，缺 `libpython3.14.so.1.0` | 必须用启动器（它设了 `LD_LIBRARY_PATH`），别直接跑 `runtime/.../python` |
| 网页界面打不开 | 看 `run/web.log` 的最后几十行 |
| 端口 3080 被占 | 系统里另有 harness 在跑。设环境变量 `DSH_CONSOLE_WEB_PORT=3081` 换一个 |
| Windows 说找不到 runtime\\win\\python | 双击一次 `Install-Windows-Runtime.bat` |

---

构建信息：node {node_version} · Python {py_version} · 详见 `version.json`
"""


def zip_bundle(src: Path) -> Path:
    """压成 zip。**要显式写可执行位**——zip 格式本身不保留它，靠 external_attr。"""
    out = src.parent / f"{src.name}.zip"
    if out.exists():
        out.unlink()
    total = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in sorted(src.rglob("*")):
            rel = path.relative_to(src.parent)
            if path.is_symlink():
                # 正常构建流程到这里已经没有软链了（build_bundle 会先落地）。
                # 保留这段是为了手动 zip 别的目录时也不至于把链接写成坏文件。
                info = zipfile.ZipInfo(str(rel))
                info.create_system = 3
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                zf.writestr(info, os.readlink(path))
                continue
            if path.is_dir():
                # **空目录也要写进去**：`home/Desktop` 这种空目录如果不入包，
                # 解压出来就没有它，而 Windows 的文件对话框会在那里报"位置不可用"。
                # （启动器也会补，但入包一份更稳——用户可能先看目录再启动。）
                info = zipfile.ZipInfo(str(rel) + "/", date_time=(2020, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = (stat.S_IFDIR | 0o755) << 16
                zf.writestr(info, b"")
                continue
            info = zipfile.ZipInfo(str(rel), date_time=time.localtime(path.stat().st_mtime)[:6])
            info.create_system = 3
            mode = path.stat().st_mode
            info.external_attr = (mode & 0xFFFF) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            with open(path, "rb") as fh:
                zf.writestr(info, fh.read())
            total += path.stat().st_size
    return out

