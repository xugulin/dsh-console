#!/usr/bin/env python3
"""把 tools/win_launcher/dsh_launcher.c 编译成 Windows 的 .exe 启动器。

## 为什么不用 mingw

目标机不需要任何东西，构建机也不该为了打个启动器去装几百 MB 的 mingw-w64。这套流程
只用 **clang + lld-link**（LLVM 自带，多数发行版 `clang`/`lld` 包里就有）：

1. 从两个 ``.def`` 生成**导入库**（``lld-link /dll /noentry /def:… /implib:…``）；
2. ``clang --target=x86_64-pc-windows-msvc -c`` 编译（源码里自己声明了用到的那十来个
   Win32 函数，所以**不需要 Windows SDK 头文件**）；
3. ``lld-link /nodefaultlib`` 链接 —— 不链 CRT，只依赖 kernel32 / user32。

## 产出三个名字，两个二进制

前端由**自身文件名**决定（见 C 源码），所以：

* ``Start-DSH-Console.exe`` / ``Start-DSH-Web-UI.exe`` —— WINDOWS 子系统，**天生没有
  控制台**（这就是比 .bat 强的地方：双击不会闪黑框，也不需要 .vbs 那种会被杀软盯上的技巧）；
* ``Start-DSH-Terminal.exe`` —— CONSOLE 子系统，TUI 必须有终端。

构建机没有 clang/lld 时**不算失败**：包里的 .bat / .vbs 仍然能用，只跳过 .exe 并说明原因。

命令行::

    python tools/build_win_launcher.py --out <目录>      # 直接产出到某目录（自测用）
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC_DIR = HERE / "win_launcher"
SOURCE = SRC_DIR / "dsh_launcher.c"
DEFS = {"kernel32.lib": SRC_DIR / "kernel32.def", "user32.lib": SRC_DIR / "user32.def"}

#: 图形版（WINDOWS 子系统，无控制台）与终端版（CONSOLE 子系统）
GUI_EXES = ("Start-DSH-Console.exe", "Start-DSH-Web-UI.exe")
#: 终端版必须带控制台；Debug 版也是控制台子系统，但**不注入任何前端参数**，
#: 于是它就是"带输出窗口的图形控制台"——出问题时让用户跑这个看报错，
#: 比 .bat 更直接（.bat 还得先 chcp、还要担心代码页）。
CONSOLE_EXES = ("Start-DSH-Terminal.exe", "Start-DSH-Console-Debug.exe")


def log(msg: str) -> None:
    print(f"  {msg}", flush=True)


def _tools() -> tuple[str, str] | None:
    clang = shutil.which("clang")
    lld = shutil.which("lld-link")
    if clang and lld:
        return clang, lld
    return None


def _run(cmd: list[str], what: str) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{what} 失败（退出码 {r.returncode}）\n"
                           f"  cmd: {' '.join(cmd)}\n"
                           f"  out: {r.stdout.strip()}\n"
                           f"  err: {r.stderr.strip()}")


def build(out_dir: Path, work: Path | None = None) -> list[Path]:
    """编译并写进 ``out_dir``；返回写出的 exe 列表。工具缺失时返回空列表。"""
    tools = _tools()
    if tools is None or not SOURCE.is_file():
        log("跳过 .exe 启动器：构建机没有 clang / lld-link（或源码缺失）"
            "——包里的 .bat / .vbs 仍可用")
        return []
    clang, lld = tools

    tmp = Path(work) if work else Path(tempfile.mkdtemp(prefix="dsh-launcher-"))
    tmp.mkdir(parents=True, exist_ok=True)

    # 1) 导入库：从 .def 生成，省掉整个 Windows SDK
    for lib, deffile in DEFS.items():
        _run([lld, "/nologo", "/dll", "/noentry", "/machine:x64",
              f"/def:{deffile}", f"/out:{tmp / (lib + '.dll')}",
              f"/implib:{tmp / lib}"], f"生成导入库 {lib}")

    # 2) 目标文件：-ffreestanding 不链 CRT，Win32 函数在源码里自己声明
    #    DSH_GUI 决定"用 pythonw 还是 python"——两个子系统各编一份
    objs: dict[str, Path] = {}
    for gui in (0, 1):
        obj = tmp / f"dsh_launcher-{'gui' if gui else 'console'}.obj"
        _run([clang, "--target=x86_64-pc-windows-msvc", "-c", "-O1",
              "-ffreestanding", "-fno-stack-protector", "-fno-builtin",
              f"-DDSH_GUI={gui}", "-Wall", "-o", str(obj), str(SOURCE)],
             f"编译 dsh_launcher.c（DSH_GUI={gui}）")
        objs["gui" if gui else "console"] = obj

    # 3) 链接：只有子系统不同（GUI 版另用 DSH_GUI=1 的 obj，去起 pythonw.exe）
    written: list[Path] = []
    for subsystem, names in (("windows", GUI_EXES), ("console", CONSOLE_EXES)):
        key = "gui" if subsystem == "windows" else "console"
        exe = tmp / f"launcher-{subsystem}.exe"
        _run([lld, "/nologo", f"/subsystem:{subsystem}", "/entry:launcher_entry",
              "/nodefaultlib", "/machine:x64", "/opt:ref",
              f"/out:{exe}", str(objs[key]), str(tmp / "kernel32.lib"), str(tmp / "user32.lib")],
             f"链接 {subsystem} 子系统启动器")
        for name in names:
            target = Path(out_dir) / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(exe, target)
            written.append(target)
    log(f"Windows .exe 启动器：{len(written)} 个（{', '.join(p.name for p in written)}）")
    return written


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="编译 Windows .exe 启动器")
    ap.add_argument("--out", required=True, help="输出目录")
    ap.add_argument("--work", help="中间产物目录（默认临时目录）")
    args = ap.parse_args(argv)
    files = build(Path(args.out), Path(args.work) if args.work else None)
    if not files:
        return 1
    for p in files:
        print(f"    {p}  {p.stat().st_size} 字节")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
