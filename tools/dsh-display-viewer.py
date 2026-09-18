#!/usr/bin/env python3
"""DSH 测试显示器：headless Wayland 的画面推到浏览器，并把浏览器上的**鼠标/键盘**
操作注入回那台显示（双向）。

## 为什么需要它

DSH Console 的 GUI 相关验证跑在一台独立的 headless Wayland 显示上（sway）。只看画面
不够——偶尔需要**点一下按钮、输账号密码/验证码**（用户明确提出的需求）。本文件把
"看"和"操作"都做掉。

## 三块实现

1. **抓画面**：``grim``（wlroots 原生截图）→ MJPEG 推给浏览器（``/stream``）。
2. **捕获输入**（页面里，纯 JS）：鼠标按下/移动/抬起/滚轮 + 一个隐藏输入框
   （**中文/输入法合成必须靠它**，dsh-browser-panel 的 ``dbp-sink`` 也是这个道理）。
   坐标一律换算成 **0..1 的归一化值**再发回来 —— 页面缩放（面板大小、DPR）就不用管了。
3. **注入输入**（本进程）：
   * 鼠标点击/滚轮 → ``ydotool mousemove --absolute`` + ``ydotool click``（需要 ydotoold，
     用 uinput ✓ 要 root）；
   * 文字/按键 → ``wtype``（走合成器的 virtual-keyboard 协议，不需要 root ✓）。

## 用法

    DSH_VIEW_PORT=8099 python3 tools/dsh-display-viewer.py
环境：``DSH_VIEW_RUNTIME``（默认 ~/.cache/dsh-display/run）、``DSH_VIEW_SOCKET``（默认 wayland-1）、
``DSH_VIEW_SIZE``（默认 1600x1000，用于把归一化坐标换回像素）。
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

RUNTIME = os.environ.get("DSH_VIEW_RUNTIME") or os.path.expanduser("~/.cache/dsh-display/run")
SOCK = os.environ.get("DSH_VIEW_SOCKET") or "wayland-1"
PORT = int(os.environ.get("DSH_VIEW_PORT", "8099"))
W, H = (int(x) for x in (os.environ.get("DSH_VIEW_SIZE") or "1600x1000").split("x"))
ENV = {**os.environ, "XDG_RUNTIME_DIR": RUNTIME, "WAYLAND_DISPLAY": SOCK,
       "YDOTOOL_SOCKET": os.environ.get("YDOTOOL_SOCKET", "/tmp/.ydotool_socket")}

_latest: bytes = b""
_lock = threading.Lock()


def run_tool(argv: list[str]) -> None:
    try:
        proc = subprocess.run(argv, env=ENV, capture_output=True, timeout=10)
        if proc.returncode != 0:
            print(f"[inject] {argv[0]} 失败：{proc.stderr.decode('utf-8', 'replace')[:200]}",
                  flush=True)
    except Exception as exc:                      # noqa: BLE001
        print(f"[inject] {argv[0]} 异常：{type(exc).__name__}: {exc}", flush=True)


def inject(obj: dict) -> None:
    """把页面报上来的一个输入事件注入到虚拟显示。"""
    kind = obj.get("t")
    x, y = obj.get("x"), obj.get("y")
    if kind in ("click", "move") and x is not None and y is not None:
        run_tool(["ydotool", "mousemove", "--absolute",
                  "-x", str(int(float(x) * W)), "-y", str(int(float(y) * H))])
        if kind == "click":
            # 1=左键 2=右键 3=中键：ydotool click 收的是按键码
            btn = {1: "0x1", 2: "0x2", 3: "0x4"}.get(int(obj.get("b") or 1), "0x1")
            run_tool(["ydotool", "click", btn])
    elif kind == "wheel":
        dy = float(obj.get("dy") or 0)
        button = "0x4" if dy < 0 else "0x5"      # 4=上滚 5=下滚
        for _ in range(min(10, max(1, int(abs(dy) / 60) or 1))):
            run_tool(["ydotool", "click", button])
    elif kind == "text":
        text = str(obj.get("s") or "")
        if text:
            run_tool(["wtype", "--", text])
    elif kind == "key":
        key = str(obj.get("k") or "")
        if key:
            run_tool(["wtype", "-k", key])


def _grab_loop() -> None:
    global _latest
    while True:
        try:
            out = subprocess.run(["grim", "-t", "jpeg", "-q", "80", "-"],
                                 env=ENV, capture_output=True, timeout=10).stdout
            if out:
                with _lock:
                    _latest = out
        except Exception:                          # noqa: BLE001
            pass
        time.sleep(0.5)


PAGE = """<!doctype html><meta charset=utf-8><title>DSH 测试显示器</title>
<body style="margin:0;background:#0b0b0c;color:#ddd;font:12px system-ui">
<div style="padding:4px 8px;opacity:.75">DSH 测试显示器 · headless Wayland ({sock}) ·
  画面 {w}x{h} · 点击画面即可操作（键盘/鼠标都会注入回去）</div>
<img id="screen" src="/stream" style="width:100%;display:block;cursor:crosshair">
<textarea id="sink" aria-label="keyboard sink"
  style="position:fixed;left:-1000px;top:0;width:10px;height:10px;opacity:0"></textarea>
<script>
(function () {{
  var img = document.getElementById('screen');
  var sink = document.getElementById('sink');
  function norm(ev) {{
    var r = img.getBoundingClientRect();
    return {{ x: (ev.clientX - r.left) / r.width, y: (ev.clientY - r.top) / r.height }};
  }}
  function send(o) {{
    try {{ fetch('/input', {{ method: 'POST', body: JSON.stringify(o) }}); }} catch (e) {{}}
  }}
  img.addEventListener('mousedown', function (ev) {{
    ev.preventDefault(); sink.focus();
    var p = norm(ev); p.t = 'click'; p.b = ev.button + 1; send(p);
  }});
  img.addEventListener('mousemove', function (ev) {{
    if (ev.buttons) {{ var p = norm(ev); p.t = 'move'; send(p); }}
  }});
  img.addEventListener('contextmenu', function (ev) {{
    ev.preventDefault(); var p = norm(ev); p.t = 'click'; p.b = 2; send(p);
  }});
  img.addEventListener('wheel', function (ev) {{
    ev.preventDefault(); send({{ t: 'wheel', dy: ev.deltaY }});
  }}, {{ passive: false }});
  // 键盘：普通字符走 input（含输入法合成结果），控制键走 keydown
  var SPECIAL = ['Return','BackSpace','Tab','Escape','Up','Down','Left','Right','Home','End'];
  sink.addEventListener('keydown', function (ev) {{
    if (SPECIAL.indexOf(ev.key) >= 0) {{ ev.preventDefault(); send({{ t: 'key', k: ev.key }}); }}
  }});
  sink.addEventListener('input', function () {{
    if (sink.value) {{ send({{ t: 'text', s: sink.value }}); sink.value = ''; }}
  }});
}})();
</script>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:          # 静音：日志留给注入错误
        pass

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")

    def do_POST(self) -> None:                     # noqa: N802 - BaseHTTPRequestHandler
        if self.path.startswith("/input"):
            try:
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length) or b"{}")
                threading.Thread(target=inject, args=(payload,), daemon=True).start()
                body = b'{"ok":true}'
            except Exception as exc:               # noqa: BLE001
                body = json.dumps({"ok": False, "error": str(exc)}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self._cors()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)

    def do_GET(self) -> None:                      # noqa: N802
        if self.path.startswith("/stream"):
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self._cors()
            self.end_headers()
            while True:
                with _lock:
                    frame = _latest
                if frame:
                    try:
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                         + str(len(frame)).encode() + b"\r\n\r\n" + frame + b"\r\n")
                    except Exception:              # noqa: BLE001
                        return
                time.sleep(0.4)
            return
        body = PAGE.format(sock=SOCK, w=W, h=H).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self._cors()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    threading.Thread(target=_grab_loop, daemon=True).start()
    print(f"viewer on http://127.0.0.1:{PORT}/  (socket {SOCK}, {W}x{H}, 支持输入注入)", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
