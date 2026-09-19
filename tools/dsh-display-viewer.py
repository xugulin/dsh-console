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

#: 所有会话的根目录；每个会话一套完全独立的显示，**互不污染**。
HOME_DIR = os.environ.get("DSH_DISPLAY_HOME") or os.path.expanduser("~/.cache/dsh-display")
PORT = int(os.environ.get("DSH_VIEW_PORT", "8099"))
W, H = (int(x) for x in (os.environ.get("DSH_VIEW_SIZE") or "1600x1000").split("x"))
SOCK = "wayland-1"
#: 协议注入工具（tools/virtual-pointer 编译产物）。
VPTR = os.environ.get("DSH_VIEW_VPTR") or os.path.join(HOME_DIR, "vptr", "vptr")


class Session:
    """一个 harness 会话独占的测试显示。

    为什么必须按会话隔离：早先所有会话共用一台显示，结果**别的项目的窗口混了进来**
    （用户实测：网盘管理的登录窗口出现在他的「显示器」标签里），既串扰又不安全。
    现在每个 sessionId 有自己的 runtime dir、自己的 sway、自己的帧与输入通道；
    一个会话里跑什么都不可能出现在另一个会话的画面上。
    """

    def __init__(self, sid: str) -> None:
        self.sid = sid
        self.dir = os.path.join(HOME_DIR, "sessions", sid)
        self.runtime = os.path.join(self.dir, "run")
        self.latest: bytes = b""
        self.lock = threading.Lock()
        self.proc: subprocess.Popen | None = None
        self.started = False

    @property
    def env(self) -> dict:
        return {**os.environ, "XDG_RUNTIME_DIR": self.runtime, "WAYLAND_DISPLAY": SOCK,
                "YDOTOOL_SOCKET": "/tmp/.ydotool_socket"}

    def ensure(self) -> bool:
        """确保这台显示已经起来（幂等）。"""
        os.makedirs(self.runtime, exist_ok=True)
        try:
            os.chmod(self.runtime, 0o700)
        except OSError:
            pass
        if os.path.exists(os.path.join(self.runtime, SOCK)):
            self.started = True
            return True
        if self.proc is not None and self.proc.poll() is None:
            return False
        conf = os.path.join(self.dir, "sway.conf")
        with open(conf, "w", encoding="utf-8") as fh:
            fh.write(f"output HEADLESS-1 resolution {W}x{H}\n")
        # 与 tools/dsh-display-reset.sh 同一套"三要素"：多后端 headless,libinput +
        # seatd 会话后端 + seat 组 —— 缺任何一个，seat 能力就是 0，指针注入无处投递。
        cmd = (f"setsid env XDG_RUNTIME_DIR={self.runtime} LIBSEAT_BACKEND=seatd "
               f"WLR_BACKENDS=headless,libinput WLR_RENDERER_ALLOW_SOFTWARE=1 "
               f"LIBGL_ALWAYS_SOFTWARE=1 sway -c {conf}")
        try:
            subprocess.run(["sudo", "-n", "-u", os.environ.get("USER") or "root", "-g", "seat",
                            "sh", "-c", cmd + f" >{self.dir}/sway.log 2>&1 &"],
                           capture_output=True, timeout=20)
        except Exception as exc:                     # noqa: BLE001
            print(f"[session {sid}] 启动合成器失败: {exc}", flush=True)
            return False
        for _ in range(30):
            time.sleep(0.5)
            if os.path.exists(os.path.join(self.runtime, SOCK)):
                self.started = True
                print(f"[session {sid}] 显示就绪（{self.runtime}）", flush=True)
                return True
        print(f"[session {sid}] 合成器没起来，看 {self.dir}/sway.log", flush=True)
        return False


_sessions: dict[str, Session] = {}
_sessions_lock = threading.Lock()


def session(sid: str) -> Session:
    with _sessions_lock:
        sess = _sessions.get(sid)
        if sess is None:
            sess = _sessions[sid] = Session(sid)
        return sess


def grab_loop(sess: Session) -> None:
    """每个会话一个抓帧线程。"""
    while True:
        if sess.ensure():
            try:
                out = subprocess.run(["grim", "-t", "jpeg", "-q", "80", "-"],
                                     env=sess.env, capture_output=True, timeout=10).stdout
                if out:
                    with sess.lock:
                        sess.latest = out
            except Exception:                        # noqa: BLE001
                pass
        time.sleep(0.5)


def run_tool(sess: Session, argv: list[str]) -> None:
    try:
        proc = subprocess.run(argv, env=sess.env, capture_output=True, timeout=10)
        if proc.returncode != 0:
            print(f"[inject] {argv[0]} 失败：{proc.stderr.decode('utf-8', 'replace')[:200]}",
                  flush=True)
    except Exception as exc:                      # noqa: BLE001
        print(f"[inject] {argv[0]} 异常：{type(exc).__name__}: {exc}", flush=True)


def inject(sess: Session, obj: dict) -> None:
    """把页面报上来的一个输入事件注入到**该会话自己的**显示。"""
    kind = obj.get("t")
    x, y = obj.get("x"), obj.get("y")
    if kind in ("click", "move") and x is not None and y is not None:
        px, py = int(float(x) * W), int(float(y) * H)
        if os.path.exists(VPTR):
            # **首选**：走合成器的虚拟指针协议（与 wtype 同理，不需要额外权限）
            run_tool(sess, [VPTR, "absolute", str(px), str(py), str(W), str(H)])
        else:
            run_tool(sess, ["ydotool", "mousemove", "--absolute", "-x", str(px), "-y", str(py)])
        if kind == "click":
            # ⚠️ ydotool click 收的是 **evdev 按键码**：BTN_LEFT=0x110、BTN_RIGHT=0x111、
            # BTN_MIDDLE=0x112。早先传 0x1/0x2/0x3 是无效码 —— 命令"成功"但什么都不发生
            # （排查了很久的"点击没反应"就是这个）。
            btn = {1: 272, 2: 273, 3: 274}.get(int(obj.get("b") or 1), 272)  # evdev BTN_*
            if os.path.exists(VPTR):
                run_tool(sess, [VPTR, "button", str(btn), "press"])
                time.sleep(0.05)
                run_tool(sess, [VPTR, "button", str(btn), "release"])
            else:
                run_tool(sess, ["ydotool", "click", hex(btn)])
    elif kind == "wheel":
        dy = float(obj.get("dy") or 0)
        button = "0x4" if dy < 0 else "0x5"      # 4=上滚 5=下滚
        for _ in range(min(10, max(1, int(abs(dy) / 60) or 1))):
            run_tool(sess, ["ydotool", "click", button])
    elif kind == "text":
        text = str(obj.get("s") or "")
        if text:
            # 先等一下再打字：焦点刚落定时立刻注入，开头几个字符会掉
            # （实测："zhanghao@example.com" 只进去 ".com"）。用 wtype 自带的延迟参数
            # 最稳（`-s` 是每次按键之间的间隔）。
            time.sleep(0.15)
            run_tool(sess, ["wtype", "-s", "30", "--", text])
    elif kind == "key":
        key = str(obj.get("k") or "")
        if key:
            run_tool(sess, ["wtype", "-k", key])



PAGE = """<!doctype html><meta charset=utf-8><title>DSH 测试显示器</title>
<body style="margin:0;background:#0b0b0c;color:#ddd;font:12px system-ui">
<div style="padding:4px 8px;opacity:.75">DSH 测试显示器 · 会话 {sid} · headless Wayland ({sock}) ·
  画面 {w}x{h} · 点击画面即可操作（键盘/鼠标都会注入回去）</div>
<img id="screen" src="{base}/stream" style="width:100%;display:block;cursor:crosshair">
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
    try {{
      fetch('{base}/input', {{ method: 'POST', body: JSON.stringify(o) }});
    }} catch (e) {{}}
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

    def _send(self, body: bytes, ctype: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self._cors()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def _split(path: str) -> tuple[str | None, str]:
        """把 /s/<sid>/<rest> 拆成 (sid, rest)；不是会话路径则 sid=None。"""
        parts = [p for p in path.split("/") if p]
        if len(parts) >= 2 and parts[0] == "s":
            return parts[1], "/" + "/".join(parts[2:])
        return None, path

    def do_POST(self) -> None:                     # noqa: N802 - BaseHTTPRequestHandler
        sid, rest = self._split(self.path)
        if sid and rest.rstrip("/") == "/input":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length) or b"{}")
                sess = session(sid)
                sess.ensure()
                threading.Thread(target=inject, args=(sess, payload), daemon=True).start()
                body = b'{"ok":true}'
            except Exception as exc:               # noqa: BLE001
                body = json.dumps({"ok": False, "error": str(exc)}).encode()
            self._send(body, "application/json")
            return
        self.send_error(404)

    def do_GET(self) -> None:                      # noqa: N802
        sid, rest = self._split(self.path)
        if sid is None:
            # 索引页：列出当前有哪些会话显示（每个会话一套独立显示）
            with _sessions_lock:
                rows = "".join(
                    f'<li><a href="/s/{k}/">{k}</a> '
                    f'{"（已就绪）" if v.started else "（未启动）"}</li>'
                    for k, v in sorted(_sessions.items()))
            self._send(f"<!doctype html><meta charset=utf-8><title>DSH 显示器</title>"
                       f"<body style='background:#0b0b0c;color:#ddd;font:13px system-ui;padding:16px'>"
                       f"<h3>DSH 测试显示器（每会话独立）</h3><ul>{rows or '<li>（暂无会话）</li>'}</ul>"
                       f"<p style='opacity:.6'>每个 harness 会话有自己的显示：<code>/s/&lt;sessionId&gt;/</code>"
                       f" —— 互不可见、互不污染。</p>".encode(), "text/html; charset=utf-8")
            return
        sess = session(sid)
        if rest.rstrip("/") == "/stream":
            sess.ensure()
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self._cors()
            self.end_headers()
            while True:
                with sess.lock:
                    frame = sess.latest
                if frame:
                    try:
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                         + str(len(frame)).encode() + b"\r\n\r\n" + frame + b"\r\n")
                    except Exception:              # noqa: BLE001
                        return
                time.sleep(0.4)
            return
        sess.ensure()
        self._send(PAGE.format(base=f"/s/{sid}", sock=SOCK, w=W, h=H, sid=sid).encode(),
                   "text/html; charset=utf-8")


if __name__ == "__main__":
    print(f"viewer on http://127.0.0.1:{PORT}/  （每会话独立：/s/<sessionId>/ ，"
          f"{W}x{H}，支持输入注入）", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
