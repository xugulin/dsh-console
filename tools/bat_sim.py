#!/usr/bin/env python3
"""模拟 cmd.exe 跑一遍启动器 .bat，把最终的环境变量和命令行算出来。

## 为什么需要这个

本机没有 Windows（也没有 wine），`.bat` 写错了在这边**完全看不出来**——文件能读、能
显示、语法看着也对，到了用户那里就是一屏"不是内部或外部命令"。第一版就是这么翻车的：
`.bat` 写成了纯 LF，而 cmd.exe 按 CRLF 解析批处理，遇到 LF 会**吞掉行首字符**，
`set "DSH_CONSOLE_PORTABLE=…"` 变成 `H_CONSOLE_PORTABLE=…`。

字节层的检查（``launchers._check_windows_files``）能挡住格式问题；这一层挡**逻辑**问题：
变量名拼错、路径少一层、`%~dp0` 的尾反斜杠没处理、`%VAR:~0,-1%` 这类子串展开写反。

## 覆盖的语法（启动器用到的全部）

``@echo off`` · ``rem`` / ``::`` · ``set "K=V"`` · ``%VAR%`` · ``%~dp0`` ·
``%VAR:~-1%`` / ``%VAR:~0,-1%`` · 单行 ``if "A"=="B" cmd`` · ``if not exist "X" goto :label`` ·
``echo`` · ``pause`` · ``exit /b N`` · ``:label`` · 直接执行命令。

用法::

    $PY tools/bat_sim.py <解压出来的包目录>
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# cmd 的子串语法是 %VAR:~start,length%，那个 ~ 是语法标记、不属于数字；
# 而 %~dp0 **只有一个 %**（不是 %...% 那种配对形式）
VAR = re.compile(r"%~dp0|%([A-Za-z_][A-Za-z0-9_]*)(?::~(-?\d+)(?:,(-?\d+))?)?%")
SET = re.compile(r'set\s+"([^"=]+)=(.*)"\s*$', re.I)
SET_EMPTY = re.compile(r'set\s+"([^"=]+)="\s*$', re.I)
IF_EQ = re.compile(r'if\s+"([^"]*)"=="([^"]*)"\s+(.*)$', re.I)
IF_NOT_EXIST_GOTO = re.compile(r'if\s+not\s+exist\s+"([^"]*)"\s+goto\s+:?(\w+)', re.I)

#: 只跳过、不执行的分支（出错提示与暂停），不影响环境计算
SKIP_MARKERS = (":missing", ":failed")


def expand(text: str, env: dict[str, str], here: str) -> str:
    """展开 ``%VAR%``、``%~dp0`` 和子串形式。cmd 会反复展开，这里迭代到稳定。"""
    def sub(m: re.Match) -> str:
        if m.group(0) == "%~dp0":
            return here
        value = env.get(m.group(1).upper(), "")
        start, length = m.group(2), m.group(3)
        if start is None:
            return value
        if length is None:
            n = int(start)
            return value[n:] if n < 0 else value[n:]
        a, b = int(start), int(length)
        if a == 0 and b < 0:
            return value[:b]
        return value

    for _ in range(4):
        new = VAR.sub(sub, text)
        if new == text:
            break
        text = new
    return text


def _sim_path(win_path: str):
    """把 Windows 路径映射回真实目录树（模拟时 ``here`` 就是真实的绝对路径）。"""
    rel = re.sub(r"^[A-Za-z]:", "", win_path).replace("\\", "/")
    return Path(re.sub(r"/+", "/", rel))


def run_bat(path: Path, here: str) -> tuple[dict[str, str], list[str], int]:
    env: dict[str, str] = {
        "PATH": r"C:\Windows\system32;C:\Windows",
        "USERPROFILE": r"C:\Users\Test",
        "ERRORLEVEL": "0",
    }
    commands: list[str] = []
    rc = 0
    lines = path.read_bytes().decode("gbk").splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if not line or line.startswith(("@echo", "rem ", "rem\t", "::")):
            continue
        low = line.lower()

        if low.startswith(":") or low.startswith("goto"):
            continue

        m = IF_NOT_EXIST_GOTO.match(line)
        if m:
            target = expand(m.group(1), env, here)
            if not _sim_path(target).exists():
                # 跳到出错标签：那里只有 echo/pause，不影响环境
                rc = 1
                break
            continue

        m = IF_EQ.match(line)
        if m:
            if expand(m.group(1), env, here) == expand(m.group(2), env, here):
                inner = m.group(3).strip()
                ms = SET.match(inner)
                if ms:
                    env[ms.group(1).upper()] = expand(ms.group(2), env, here)
                else:
                    commands.append(expand(inner, env, here))
            continue

        if low.startswith("set "):
            ms = SET.match(line) or SET_EMPTY.match(line)
            if ms:
                env[ms.group(1).upper()] = expand(ms.group(2) or "", env, here)
            continue

        if low.startswith(("echo", "pause", "title", "endlocal")):
            continue
        if low.startswith(("if ", "for ", "call ")):
            commands.append(f"[未模拟] {line}")
            continue
        if low.startswith("exit /b"):
            break
        commands.append(expand(line, env, here))
    return env, commands, rc


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    if not argv:
        print(__doc__)
        return 2
    root = Path(argv[0]).resolve()
    # 模拟 %~dp0：脚本所在目录，**带尾反斜杠**
    here = str(root).replace("/", "\\") + "\\"
    bad = 0
    for name in ("启动DSH控制台.bat", "启动DSH终端界面.bat", "启动DSH网页界面.bat"):
        path = root / name
        if not path.is_file():
            print(f"  ✗ 缺文件 {name}")
            bad += 1
            continue
        env, commands, rc = run_bat(path, here)
        print(f"\n  ── {name}  (预判退出码 {rc}) ──")
        for key in ("DSH_CONSOLE_PORTABLE", "HOME", "DSH_HOME", "PY"):
            if key in env:
                print(f"     {key:20} = {env[key]}")
        for cmd in commands:
            print(f"     ▶ {cmd[:100]}")
        py = env.get("PY", "")
        if not py:
            print("     ✗ 没算出 PY")
            bad += 1
        elif _sim_path(py).exists():
            print("     ✓ PY 指向的 python.exe 在包里真实存在")
        else:
            print(f"     ✗ PY 指向的路径不存在：{py}")
            bad += 1
        home = env.get("HOME", "")
        if not home.endswith("\\home"):
            print(f"     ✗ HOME 没指到包内：{home}")
            bad += 1
    print()
    if bad:
        print(f"❌ {bad} 项有问题")
        return 1
    print("✅ 批处理逻辑模拟通过（变量展开 / 子串 / 路径存在性 / HOME 重定向）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
