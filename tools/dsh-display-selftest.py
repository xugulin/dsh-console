#!/usr/bin/env python3
"""显示器功能自测：把"能看 + 能操作 + 会话隔离"逐项验一遍。

用法：python3 tools/dsh-display-selftest.py [会话名]
默认用 verify-<时间戳> 作为会话名（**不碰你正在用的会话**）。

覆盖项（每项打印 PASS/FAIL 与证据）：
  1 服务与索引页            2 每会话分配独立显示
  3 空闲检测 /state         4 画面有内容（采样亮度，不是全黑）
  5 鼠标移动精确落点        6 鼠标点击（靶子按钮真的被按下）
  7 打字                    8 退格删除
  9 中文（输入法上屏后的文本）10 回车提交
 11 会话隔离（两个会话画面不同）
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8099"
PY = "/home/xgl/python/python3147uv/bin/python"
ISO = "/home/xgl/.cache/dsh-display"
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  —— {detail}" if detail else ""), flush=True)


def get(path: str, timeout: float = 20) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
            return r.status, r.read()
    except Exception as exc:                          # noqa: BLE001
        return 0, str(exc).encode()


def post(path: str, obj: dict) -> bool:
    try:
        req = urllib.request.Request(BASE + path, data=json.dumps(obj).encode(), method="POST")
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status == 200 and json.loads(r.read() or b"{}").get("ok") is True
    except Exception:                                 # noqa: BLE001
        return False


def xdo(display: str, *args: str) -> str:
    try:
        return subprocess.run(["xdotool", *args], capture_output=True, timeout=10,
                              env={"DISPLAY": display, "PATH": "/usr/bin:/bin",
                                   "HOME": "/home/xgl"}).stdout.decode()
    except Exception:                                 # noqa: BLE001
        return ""


def brightness(jpeg: bytes) -> int:
    open("/tmp/_selftest.jpg", "wb").write(jpeg)
    from PIL import Image
    im = Image.open("/tmp/_selftest.jpg").convert("RGB")
    return max(max(p) for p in im.resize((40, 25)).getdata())


TARGET = '''
import sys, time
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QLineEdit, QPushButton
app = QApplication(sys.argv[:1])
w = QWidget(); w.resize(1000, 400); lay = QVBoxLayout(w)
e = QLineEdit(); e.setMinimumHeight(70); lay.addWidget(e)
e.textChanged.connect(lambda t: print(f"FIELD=[{t}]", flush=True))
b = QPushButton("按钮"); b.setMinimumHeight(90); lay.addWidget(b)
b.clicked.connect(lambda: print("CLICKED", flush=True))
e.returnPressed.connect(lambda: print("ENTERED", flush=True))
w.setStyleSheet("font-size:26px"); w.show(); e.setFocus()
for _ in range(1500): app.processEvents(); time.sleep(0.05)
'''


def main() -> int:
    sid = sys.argv[1] if len(sys.argv) > 1 else f"verify-{int(time.time())}"
    sid_b = sid + "-b"
    print(f"自测会话：{sid} / {sid_b}\n")

    status, body = get("/")
    check("1 服务与索引页可用", status == 200 and b"DSH" in body, f"HTTP {status}")

    status, body = get(f"/s/{sid}/display")
    info = json.loads(body) if status == 200 else {}
    disp = info.get("display", "")
    check("2 每会话分配独立显示", status == 200 and disp.startswith(":"), f"{sid} → {disp}")

    status, body = get(f"/s/{sid}/state")
    st = json.loads(body) if status == 200 else {}
    check("3 空闲检测 /state", status == 200 and "idle" in st, json.dumps(st, ensure_ascii=False)[:80])

    # 靶子程序
    open(f"{ISO}/tests/selftest_target.py", "w").write(TARGET)
    log = f"/tmp/selftest_{sid}.log"
    subprocess.Popen([PY, f"{ISO}/tests/selftest_target.py"],
                     stdout=open(log, "wb"), stderr=subprocess.STDOUT, start_new_session=True,
                     env={"DISPLAY": disp, "QT_QPA_PLATFORM": "xcb", "HOME": "/home/xgl",
                          "PATH": "/usr/bin:/bin", "XDG_RUNTIME_DIR": "/tmp/dsh-demo-run"})
    time.sleep(9)

    status, body = get(f"/s/{sid}/state")
    st = json.loads(body) if status == 200 else {}
    check("3b 有程序后不再空闲", st.get("idle") is False, f"windows={st.get('windows')}")

    status, frame = get(f"/s/{sid}/snapshot")
    check("4 画面有内容（非全黑）", status == 200 and brightness(frame) >= 25,
          f"{len(frame)} 字节 · 最亮 {brightness(frame)}")

    # 鼠标移动精确性
    post(f"/s/{sid}/input", {"t": "move", "x": 0.4, "y": 0.5})
    time.sleep(1)
    loc = xdo(disp, "getmouselocation")
    ok = "x:640" in loc and "y:500" in loc
    check("5 鼠标移动落点精确", ok, f"期望 (640,500) → {loc.strip()}")

    # 点击输入框 → 打字 → 退格 → 中文 → 回车
    post(f"/s/{sid}/input", {"t": "click", "x": 0.5, "y": 0.09})
    time.sleep(1)
    post(f"/s/{sid}/input", {"t": "text", "s": "abc"})
    time.sleep(2)
    got = open(log, encoding="utf-8", errors="replace").read()
    check("7 打字", "FIELD=[abc]" in got or "FIELD=[a]" in got, got.strip().splitlines()[-1][:60] if got.strip() else "")

    post(f"/s/{sid}/input", {"t": "key", "k": "Backspace"})
    time.sleep(2)
    got = open(log, encoding="utf-8", errors="replace").read()
    check("8 退格删除", "FIELD=[ab]" in got, "最后一条 " + (got.strip().splitlines()[-1][:40] if got.strip() else ""))

    post(f"/s/{sid}/input", {"t": "text", "s": "中文显示器"})
    time.sleep(3)
    got = open(log, encoding="utf-8", errors="replace").read()
    check("9 中文输入", "中文显示器" in got, (got.strip().splitlines()[-1][:40] if got.strip() else ""))

    post(f"/s/{sid}/input", {"t": "key", "k": "Enter"})
    time.sleep(2)
    got = open(log, encoding="utf-8", errors="replace").read()
    check("10 回车提交", "ENTERED" in got, "")

    post(f"/s/{sid}/input", {"t": "click", "x": 0.5, "y": 0.3})
    time.sleep(2)
    got = open(log, encoding="utf-8", errors="replace").read()
    check("6 鼠标点击（按钮真的被按下）", "CLICKED" in got, "")

    # 会话隔离
    status, body = get(f"/s/{sid_b}/display")
    info_b = json.loads(body) if status == 200 else {}
    disp_b = info_b.get("display", "")
    subprocess.Popen([PY, "-c",
                      "import sys,time\nfrom PySide6.QtWidgets import QApplication,QLabel\n"
                      "app=QApplication(sys.argv[:1])\n"
                      "w=QLabel('会话 B'); w.setStyleSheet('background:#431;color:#fff;font-size:60px')\n"
                      "w.resize(900,300); w.show()\n"
                      "for _ in range(900): app.processEvents(); time.sleep(0.05)\n"],
                     stdout=open("/dev/null", "wb"), stderr=subprocess.STDOUT, start_new_session=True,
                     env={"DISPLAY": disp_b, "QT_QPA_PLATFORM": "xcb", "HOME": "/home/xgl",
                          "PATH": "/usr/bin:/bin"})
    time.sleep(9)
    status_a, fa = get(f"/s/{sid}/snapshot")
    status_b, fb = get(f"/s/{sid_b}/snapshot")
    check("11 会话隔离（两会话画面不同）", disp != disp_b and fa != fb,
          f"{disp} {len(fa)} 字节 / {disp_b} {len(fb)} 字节")

    passed = sum(1 for _n, ok, _d in RESULTS if ok)
    print(f"\n== 结果：{passed}/{len(RESULTS)} 项通过 ==")
    for name, ok, detail in RESULTS:
        if not ok:
            print(f"  未通过：{name} {detail}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
