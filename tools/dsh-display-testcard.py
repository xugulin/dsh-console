#!/usr/bin/env python3
"""显示器自检卡 —— 「人眼」那一半。

dsh-display-selftest.py 是**自动**那一半（脚本自己验 11 项功能，不碰你正在用的会话）；
本卡是**人眼**那一半：把一张固定测试图铺到某台会话显示上，你切到 DSH Web UI 的
「显示器」标签页，对照着看一眼就知道画面正不正常。

用法：
    python3 tools/dsh-display-testcard.py                 # 画在**本会话**的显示器上（读 $DSH_SESSION_ID）
    python3 tools/dsh-display-testcard.py verify-123      # 指定会话名（如 verify-123 / cjk-lab）
    python3 tools/dsh-display-testcard.py --seconds 60    # 60 秒后自动退出（默认一直显示，Ctrl-C 结束）
    python3 tools/dsh-display-testcard.py --browser       # 改用浏览器渲染（Chromium/X11 那条路，见同名 .html）
    python3 tools/dsh-display-testcard.py --list          # 列出 viewer 当前登记的所有会话与显示号

卡上要看四件事：
    · 8 条标准色条  → 颜色通道没串（红/绿/蓝任一路错位这里立刻能看出来）
    · 32 级灰阶     → 色深与压缩没把台阶抹平（台阶糊成一片 = 抓帧被压过头）
    · 秒针 + 时钟   → 是**实时**画面，不是卡住的旧帧（静止不动就说明流断了）
    · 顶部信息行    → 确认画的是哪台显示、哪个 Qt 平台、什么分辨率

⚠️ 规范位置：https://github.com/xugulin/dsh-display-panel
   （插件本体、显示器服务、systemd 单元都在那个仓库；这里是开发副本，
     改动时以插件仓库为准，改完再同步回来，别只改一边。）
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from urllib.parse import quote, urlencode

BASE = "http://127.0.0.1:8099"
COLOR_BARS = ["#ffffff", "#ffff00", "#00ffff", "#00ff00",
              "#ff00ff", "#ff0000", "#0000ff", "#000000"]


def get_json(path: str, timeout: float = 15) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def list_sessions() -> int:
    with urllib.request.urlopen(BASE + "/", timeout=15) as r:
        html = r.read().decode("utf-8", "replace")
    rows = []
    for chunk in html.split("<li>")[1:]:
        link = chunk.split('href="')[1].split('"')[0] if 'href="' in chunk else ""
        name = link.strip("/")
        if name.startswith("s/"):                                # 链接形如 /s/<会话名>/
            name = name[2:]
        rows.append((name, chunk.split("·")[-1].split("<")[0].strip()))
    print(f"viewer {BASE} 登记的会话（{len(rows)} 个）：")
    for sid, disp in rows:
        print(f"  {sid:<48} {disp}")
    print("\n挑一个：python3 tools/dsh-display-testcard.py <会话名>")
    return 0


def parse_argv(argv: list[str]) -> tuple[str, int, bool]:
    sid, seconds, browser = "", 0, False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--browser":
            browser = True
        elif a == "--seconds" and i + 1 < len(argv):
            i += 1
            seconds = int(argv[i])
        elif a.startswith("-"):
            raise SystemExit(f"未知参数：{a}（试试 --help 看 docstring）")
        else:
            sid = a
        i += 1
    return sid, seconds, browser


def main() -> int:
    # 输出立刻可见：本工具的价值就是"它到底画到哪台显示上了"，而后台运行时
    # stdout 默认是块缓冲，日志会一直空着 —— 实测踩过，别删。
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:                                           # noqa: BLE001
        pass
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        return 0
    if "--list" in sys.argv:
        return list_sessions()

    sid, seconds, browser = parse_argv(sys.argv[1:])
    if not sid:
        sid = os.environ.get("DSH_SESSION_ID", "")
    if not sid:
        print("没给会话名，也读不到 $DSH_SESSION_ID。先用 --list 看有哪些会话。", file=sys.stderr)
        return 2

    try:
        info = get_json(f"/s/{sid}/display")
    except Exception as exc:                                    # noqa: BLE001
        print(f"连不上显示器服务 {BASE}：{exc}\n先跑 tools/dsh-display-start.sh", file=sys.stderr)
        return 1

    disp = info.get("display", "")
    size = info.get("size", "1600x1000")
    if not disp:
        print(f"viewer 没给 {sid} 分配显示：{info}", file=sys.stderr)
        return 1
    w, h = (int(v) for v in size.lower().split("x"))

    print(f"会话 {sid} → 显示器 {disp}（{size}）")
    print(f"它在 DSH Web UI 里的地址：{BASE}/s/{sid}/")
    print("现在切到「显示器」标签页应该能看到这张卡（Ctrl-C 结束）")

    # Qt 在 Xvfb 上必须显式 xcb，且要清掉残留的 Wayland 变量（见 dsh-display-BACKENDS.md）
    os.environ["DISPLAY"] = disp
    os.environ["QT_QPA_PLATFORM"] = "xcb"
    os.environ.pop("WAYLAND_DISPLAY", None)
    os.environ.pop("XDG_SESSION_TYPE", None)
    runtime = os.environ.get("XDG_RUNTIME_DIR", "")
    if not runtime or not os.path.isdir(runtime) or not os.access(runtime, os.W_OK):
        runtime = f"/tmp/dsh-testcard-{os.getuid()}"
        os.makedirs(runtime, mode=0o700, exist_ok=True)
        os.environ["XDG_RUNTIME_DIR"] = runtime

    if browser:
        html = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dsh-display-testcard.html")
        # 路径必须百分号编码：本仓库的目录名带中文（…/DeepSeekHarness控制台/…），
        # 直接拼进 file:// 会让 Chromium 加载失败并退回一张"新标签页"（实测，很难看出来）。
        url = "file://" + quote(html) + "?" + urlencode({"session": sid, "display": disp})
        brave = os.environ.get("DSH_TESTCARD_BROWSER", "/opt/brave.com/brave-origin-beta/brave")
        profile = os.environ.get("DSH_TESTCARD_PROFILE", "/tmp/dsh-testcard-profile")
        os.makedirs(profile, exist_ok=True)
        common = [brave, f"--user-data-dir={profile}", "--no-first-run",
                  "--no-default-browser-check", "--disable-dev-shm-usage",
                  "--window-position=0,0", f"--window-size={w},{h}"]

        def window_titles() -> str:
            try:
                return subprocess.run(
                    ["xdotool", "search", "--name", ".", "getwindowname", "%@"],
                    capture_output=True, text=True, timeout=15,
                    env={"DISPLAY": disp, "PATH": "/usr/bin:/bin"}).stdout
            except Exception:                                   # noqa: BLE001
                return ""

        def click(x: float, y: float) -> bool:
            """借 viewer 的输入通道点一下（就是「显示器」标签页里那套注入）。"""
            try:
                req = urllib.request.Request(
                    f"{BASE}/s/{sid}/input",
                    data=json.dumps({"t": "click", "x": x, "y": y, "b": 1}).encode(), method="POST")
                with urllib.request.urlopen(req, timeout=10) as r:
                    return json.loads(r.read() or b"{}").get("ok") is True
            except Exception:                                   # noqa: BLE001
                return False

        # 新 profile 的第一次启动必须先走完 Brave Origin 的引导 —— 直接带 --app 启动的话，
        # 点掉欢迎框后 Brave 只会开一张普通"新标签页"，自检卡根本不出现（实测）。
        # 所以先空跑一次把 profile 焐热，再带 --app 启动第二遍。
        if not os.path.exists(os.path.join(profile, "Default", "Preferences")):
            print("新 profile：先空跑一次走完 Brave Origin 的引导（约 20 秒，只需一次）…")
            boot = subprocess.Popen(common, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, start_new_session=True)
            time.sleep(9)
            if "Brave Origin" in window_titles():
                print("  已替你点掉首次运行欢迎框" if click(0.5, 0.668)
                      else "  欢迎框没点掉 → 去「显示器」标签页点一下即可")
            time.sleep(7)
            boot.terminate()
            # 必须等它**真的**退干净：同一个 user-data-dir 还有活实例时，第二遍启动会被
            # 那个实例接管，只开出一张普通"新标签页"，--app 的卡窗口根本不出现（实测）。
            for _ in range(30):
                time.sleep(1)
                if boot.poll() is not None:
                    break
            time.sleep(2)                                       # 再给 profile 锁一点时间

        proc = subprocess.Popen(common + [f"--app={url}"], stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, start_new_session=True)
        print(f"已用浏览器渲染同一张卡（PID {proc.pid}，profile 在 {profile}）")
        if seconds:
            time.sleep(seconds)
            proc.terminate()
        return 0

    from PySide6.QtCore import QTimer                           # noqa: PLC0415
    from PySide6.QtGui import QColor, QFont, QPainter           # noqa: PLC0415
    from PySide6.QtWidgets import QApplication, QWidget         # noqa: PLC0415

    class Card(QWidget):
        def __init__(self) -> None:
            super().__init__()
            self.t0 = time.time()
            self.setWindowTitle("显示器自检卡 / DISPLAY TESTCARD")
            self.setGeometry(0, 0, w, h)
            self.timer = QTimer(self)
            self.timer.timeout.connect(self.update)
            self.timer.start(200)                               # 5 帧/秒，够看出"画面是活的"

        def paintEvent(self, _event) -> None:                   # noqa: N802
            p = QPainter(self)
            W, H = self.width(), self.height()
            p.fillRect(0, 0, W, H, QColor("#0d1117"))

            # 1) 8 条标准色条
            bh = int(H * 0.12)
            bw = W / len(COLOR_BARS)
            for i, c in enumerate(COLOR_BARS):
                p.fillRect(int(i * bw), 0, int(bw) + 1, bh, QColor(c))

            # 2) 32 级灰阶（台阶必须一根根数得出来）
            gy, gh, steps = bh, int(H * 0.055), 32
            for i in range(steps):
                v = round(i * 255 / (steps - 1))
                p.fillRect(int(i * W / steps), gy, int(W / steps) + 1, gh, QColor(v, v, v))

            # 3) 标题与信息行
            y = gy + gh + int(H * 0.075)
            p.setPen(QColor("#e6edf3"))
            f = QFont("DejaVu Sans"); f.setPixelSize(int(H * 0.052)); f.setBold(True)
            p.setFont(f)
            p.drawText(int(W * 0.03), y, "显示器自检卡 · DISPLAY TESTCARD")

            f2 = QFont("DejaVu Sans"); f2.setPixelSize(int(H * 0.026)); f2.setBold(False)
            p.setFont(f2)
            rows = [
                ("会话 session", sid),
                ("显示器 display", f"{disp}   {size}   Qt 平台 {os.environ['QT_QPA_PLATFORM']}"),
                ("画面地址 viewer", f"{BASE}/s/{sid}/"),
                ("已运行 uptime", f"{int(time.time() - self.t0)} 秒（秒针在走 = 画面是实时的）"),
            ]
            ly = y + int(H * 0.055)
            for k, v in rows:
                p.setPen(QColor("#8b949e")); p.drawText(int(W * 0.03), ly, f"{k}")
                p.setPen(QColor("#7ee787")); p.drawText(int(W * 0.22), ly, str(v))
                ly += int(H * 0.045)

            # 4) 时钟 + 秒针（每 5 秒扫一趟，静止不动就是流断了）
            now = time.time()
            clock = time.strftime("%H:%M:%S", time.localtime(now))
            f3 = QFont("DejaVu Sans Mono"); f3.setPixelSize(int(H * 0.09)); f3.setBold(True)
            p.setFont(f3); p.setPen(QColor("#58a6ff"))
            p.drawText(int(W * 0.03), int(H * 0.85), clock)

            tx, ty, tw, th = int(W * 0.03), int(H * 0.88), int(W * 0.94), int(H * 0.022)
            p.fillRect(tx, ty, tw, th, QColor("#161b22"))
            frac = (now % 5) / 5
            p.fillRect(tx, ty, int(tw * frac), th, QColor("#3fb950"))

            p.setFont(f2); p.setPen(QColor("#8b949e"))
            p.drawText(tx, int(H * 0.965),
                       "对照：色条不串色 · 32 级灰阶台阶清晰 · 秒针在走 · 上面几行信息与实际相符")

    app = QApplication(sys.argv[:1])
    card = Card()
    card.show()
    if seconds:
        QTimer.singleShot(seconds * 1000, app.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
