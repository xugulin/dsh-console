#!/usr/bin/env python3
"""「强制修复端口占用」的功能测试 —— **不碰用户的 harness**。

做法：起一个"假 harness"（只监听端口、不干别的），然后让
``service.force_free_port(port, restart=False)`` 去收拾它。``restart=False``
刻意不碰 systemd：真正的修复一定会重启服务，而重启服务会把测试环境一起带走。

跑法：
    ./.venv/bin/python tools/ci_portfix_test.py
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dsh_console import service  # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{('  —— ' + detail) if detail else ''}")


def free_port(start: int = 39100) -> int:
    """找一个真没人听的端口。"""
    for port in range(start, start + 200):
        if service.port_is_free(port):
            return port
    raise RuntimeError("找不到空闲端口")


def spawn_decoy(port: int, *, stubborn: bool = False) -> subprocess.Popen:
    """起一个占着端口的假 harness。

    ``stubborn=True`` 时忽略 SIGTERM，用来验证"TERM 不退就 KILL"这条兜底路径。
    """
    code = (
        "import socket,signal,sys,time\n"
        "port=int(sys.argv[1]); stubborn=sys.argv[2]=='1'\n"
        "if stubborn: signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "s=socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
        "s.bind(('127.0.0.1', port)); s.listen(5)\n"
        "print('ready', flush=True)\n"
        "time.sleep(600)\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", code, str(port), "1" if stubborn else "0"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        start_new_session=True,          # 独立会话：杀它不会波及测试自己
    )
    assert proc.stdout is not None
    if proc.stdout.readline().strip() != "ready":
        raise RuntimeError("假 harness 没起来")
    for _ in range(25):                  # 等它真的进入 LISTEN
        if not service.port_is_free(port):
            break
        time.sleep(0.1)
    return proc


def main() -> int:
    print(f"平台: {sys.platform}　（/proc 路线仅 Linux；macOS 靠 lsof 兜底）")
    print("== 1. 端口探测 ==")
    port = free_port()
    check("空闲端口判定为可用", service.port_is_free(port), f"port={port}")
    # 注意：不要拿真实的 3080 当"被占用"的样本——CI 机器上根本没有 harness 在跑，
    # 那样写出来的测试会在 CI 上必挂。用下面自己起的假 harness 来验证。
    print("== 2. 找出端口的主人 ==")
    proc = spawn_decoy(port)
    try:
        check("被占端口判定为不可用", not service.port_is_free(port), f"port={port}")
        holder = service.port_holder(port)
        check("能反查到占用者 pid", holder is not None and holder.pid == proc.pid,
              f"holder={holder}")
        check("占用者命令行可读", bool(holder and "python" in (holder.cmdline or "").lower()),
              (holder.cmdline if holder else "")[:70])

        print("== 3. 强制清端口（TERM 路径）==")
        rep = service.force_free_port(port, restart=False)
        check("报告里记录了占用者", rep.holder is not None and rep.holder.pid == proc.pid)
        check("端口实测已空出", rep.free, rep.summary())
        check("进程确实退出了", not service.pid_alive(proc.pid))
        check("留下了操作记录", len(rep.actions) >= 2, " / ".join(rep.actions))
        check("识别为祖先判定为假", not service.is_ancestor(proc.pid))
    finally:
        if service.pid_alive(proc.pid):
            os.kill(proc.pid, signal.SIGKILL)
        proc.wait(timeout=10)

    print("== 4. 顽固进程走 KILL 兜底 ==")
    port2 = free_port(port + 1)
    stubborn = spawn_decoy(port2, stubborn=True)
    try:
        t0 = time.monotonic()
        rep2 = service.force_free_port(port2, restart=False)
        took = time.monotonic() - t0
        check("顽固进程最终被结束", not service.pid_alive(stubborn.pid))
        check("端口已空出", rep2.free, rep2.summary())
        check("走了 KILL（耗时 >= TERM 等待）", took >= 7.0, f"耗时 {took:.1f}s")
        check("操作记录里写明用了 KILL",
              any("KILL" in a for a in rep2.actions), " / ".join(rep2.actions))
    finally:
        if service.pid_alive(stubborn.pid):
            os.kill(stubborn.pid, signal.SIGKILL)
        stubborn.wait(timeout=10)

    print("== 5. 端口本来就没被占 ==")
    port3 = free_port(port2 + 1)
    rep3 = service.force_free_port(port3, restart=False)
    check("空端口不报错且判定为空", rep3.free and rep3.holder is None, rep3.summary())
    check("明确说明本来就没被占",
          any("本来就没被占用" in a for a in rep3.actions), " / ".join(rep3.actions))

    print("== 6. 保命判断：拒绝杀受保护的进程 ==")
    try:
        service._graceful_kill(os.getpid())
        check("拒绝结束控制台自己", False, "居然没抛异常")
    except service.ServiceError as exc:
        check("拒绝结束控制台自己", "受保护" in str(exc), str(exc))

    print("== 7. 祖先判定（控制台跑在 harness 进程树里时的保命逻辑）==")
    chain = service.process_ancestry()
    check("能算出自己的祖先链", len(chain) >= 1 and chain[0] == os.getpid(), str(chain[:5]))
    check("自己不是自己的祖先", not service.is_ancestor(os.getpid()))
    check("1 号进程不是祖先", not service.is_ancestor(1))
    if len(chain) > 1:
        check("真正的父进程被认作祖先", service.is_ancestor(chain[1]), f"ppid={chain[1]}")

    print("== 8. lsof 兜底解析（macOS 走这条；本机没装 lsof 也要能验）==")
    fake = (
        "COMMAND   PID   USER   FD   TYPE             DEVICE SIZE/OFF NODE NAME\n"
        "node     4242    xgl   23u  IPv4 0x1234567890abcdef      0t0  TCP 127.0.0.1:3080 (LISTEN)\n"
    )

    class _CP:
        def __init__(self, out: str) -> None:
            self.stdout, self.returncode = out, 0

    original = service.subproc.run
    try:
        service.subproc.run = lambda *a, **k: _CP(fake)          # type: ignore[assignment]
        holder2 = service._holder_via_lsof(3080)
        check("能从 lsof 输出里解析出 pid", holder2 is not None and holder2.pid == 4242,
              str(holder2))
        check("lsof 的 COMMAND 列被当作进程名兜底",
              bool(holder2 and holder2.name), holder2.name if holder2 else "")
        service.subproc.run = lambda *a, **k: _CP(                  # type: ignore[assignment]
            "COMMAND   PID USER   FD   TYPE DEVICE SIZE/OFF NODE NAME\n")
        check("没有监听者时返回 None", service._holder_via_lsof(3080) is None)
    finally:
        service.subproc.run = original                          # type: ignore[assignment]

    print()
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        for name in FAIL:
            print(f"  失败：{name}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
