"""起子进程的**唯一入口**：Windows 上必须不弹控制台窗口。

## 为什么需要这个模块（实测踩的坑）

控制台的图形版是 **GUI 子系统**进程（便携包里就是 `pythonw.exe`），**它自己没有控制台**。
Windows 上，一个没有控制台的进程去起**控制台程序**（`npm` / `node` / `tasklist` / `dsh.cmd` …）时，
系统会**给子进程新分配一个控制台窗口**——屏幕上就闪一个黑框。
状态轮询每 5 秒跑一次 `tasklist`，于是用户看到的是"终端窗口不停闪烁、根本没法用"。

修法只有一条：这些子进程都带上 `CREATE_NO_WINDOW`。**不能只在某一个调用点加**——
漏一处就闪一次，而且闪烁的位置和频率取决于用户在哪个页面——所以统一收口到这里。

## 怎么用

    from . import subproc
    proc = subproc.run(["npm", "view", name], capture_output=True, text=True)
    proc = subproc.popen(cmd, stdout=subprocess.PIPE, ...)

非 Windows 上这些函数就是 `subprocess.run` / `subprocess.Popen` 的**透明转发**（不多加任何参数）。

## 例外：需要**看得见**的终端

有一处**故意不用**这里：Windows 上启动 TUI 前端要走 `cmd /c start`，它要的就是一个
新控制台窗口（见 :func:`dsh_console.frontends.launch_tui` 里的注释）。那种地方请直接
用 `subprocess.Popen`，并在注释里写清楚为什么。
"""

from __future__ import annotations

import os
import subprocess
from typing import Any

#: Windows 上"不要给子进程建控制台窗口"的标志位。
#: 直接写字面量而不是 subprocess.CREATE_NO_WINDOW：后者只有 Windows 上才有这个属性，
#: 在 Linux 上引用它会在导入期就 AttributeError（这个模块是跨平台导入的）。
CREATE_NO_WINDOW = 0x08000000


def _with_flags(kw: dict[str, Any]) -> dict[str, Any]:
    """把 CREATE_NO_WINDOW 并进 creationflags（**或**上去，不是覆盖）。

    调用方可能已经带了别的标志，比如 `service.py` 起便携 harness 时用的
    `CREATE_NEW_PROCESS_GROUP`——那个要保留，两个标志位是可以并存的。
    """
    if os.name != "nt":
        return kw
    merged = dict(kw)
    merged["creationflags"] = int(kw.get("creationflags", 0)) | CREATE_NO_WINDOW
    return merged


def run(cmd, **kwargs: Any) -> subprocess.CompletedProcess:
    """等价于 ``subprocess.run``，但在 Windows 上不弹控制台窗口。"""
    return subprocess.run(cmd, **_with_flags(kwargs))


def popen(cmd, **kwargs: Any) -> subprocess.Popen:
    """等价于 ``subprocess.Popen``，但在 Windows 上不弹控制台窗口。"""
    return subprocess.Popen(cmd, **_with_flags(kwargs))


def check_output(cmd, **kwargs: Any) -> bytes:
    """等价于 ``subprocess.check_output``，但在 Windows 上不弹控制台窗口。"""
    return subprocess.check_output(cmd, **_with_flags(kwargs))
