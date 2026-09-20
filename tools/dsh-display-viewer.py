#!/usr/bin/env python3
"""DSH 测试显示器：**每个 harness 会话一台完全独立的显示**，能看画面，也能把
浏览器里的鼠标/键盘操作注入回去。

## 为什么要每会话独立

早先所有会话共用一台显示，结果**别的项目的窗口混了进来**（用户实测：网盘管理项目的
登录窗口出现在他的「显示器」标签画面里），既串扰又不安全。现在每个 sessionId 有自己
的一台显示、自己的帧缓冲与输入通道 —— 一个会话里跑什么都不可能出现在另一个会话的画面上。

## 为什么底座选 Xvfb（而不是 Wayland）

三条路都实测过（详见 tools/dsh-display-BACKENDS.md）：

* **Wayland 单台**：可用，但指针注入前提苛刻 —— 必须同时满足
  ``WLR_BACKENDS=headless,libinput`` + ``LIBSEAT_BACKEND=seatd`` + 进程带 ``seat`` 组
  + 先起 ``ydotoold``；缺任何一个 ``seat capabilities`` 就是 0，**任何**指针注入都
  无处投递（客户端连指针对象都不会创建）。
* **Wayland 每会话一台**：**不可行** —— seatd 的 seat 是 VT-bound，同一时刻只允许
  一个客户端（实测 ``seat is VT-bound and has an active client``），logind 的会话
  又被用户自己的桌面占着。
* **Xvfb + X11（本文件默认）**：X11 **没有 seat 概念**，可以同时跑任意多个 display；
  输入用 ``xdotool`` 走 XTest，**不需要权限、不占 seat、不碰 uinput**。
  实测两个 display 的画面互不相同（完全独立），点击注入生效。
  代价：里面的程序跑在 X11/XWayland 下，不覆盖「原生 Wayland 渲染」。

## 路由

    /                        会话索引页
    /s/<sessionId>/          该会话的显示器页面（画面 + 输入捕获）
    /s/<sessionId>/stream    MJPEG 画面流
    /s/<sessionId>/input     POST 输入事件（click / move / wheel / text / key）
    /s/<sessionId>/display   该会话的显示号与后端（脚本用）

## 怎么在某个会话的显示上跑程序

    DISPLAY=:<该会话的号> QT_QPA_PLATFORM=xcb <程序>

⚠️ ``QT_QPA_PLATFORM=xcb`` 必须显式指定：环境里若残留 ``WAYLAND_DISPLAY``，
Qt 会去加载 wayland 插件并失败（实测踩过）。

## 环境变量

``DSH_DISPLAY_HOME``（默认 ~/.cache/dsh-display）、``DSH_VIEW_PORT``（默认 8099）、
``DSH_VIEW_SIZE``（默认 1600x1000）、``DSH_VIEW_BACKEND=x11|wayland``（默认 x11）。
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOME_DIR = os.environ.get("DSH_DISPLAY_HOME") or os.path.expanduser("~/.cache/dsh-display")
PORT = int(os.environ.get("DSH_VIEW_PORT", "8099"))
W, H = (int(x) for x in (os.environ.get("DSH_VIEW_SIZE") or "1600x1000").split("x"))
BACKEND = (os.environ.get("DSH_VIEW_BACKEND") or "x11").strip().lower()
#: 协议注入工具（tools/virtual-pointer 编译产物），只在 wayland 后端用得到。
VPTR = os.environ.get("DSH_VIEW_VPTR") or os.path.join(HOME_DIR, "vptr", "vptr")


class Session:
    """一个 harness 会话独占的显示（X11 默认；wayland 为备用后端）。"""

    def __init__(self, sid: str) -> None:
        self.sid = sid
        self.dir = os.path.join(HOME_DIR, "sessions", sid)
        self.runtime = os.path.join(self.dir, "run")
        self.latest: bytes = b""
        self.lock = threading.Lock()
        self.started = False
        self._boot_lock = threading.Lock()
        # 显示号由会话名稳定推导 —— 用 crc32 而不是 hash()：后者每个进程都加盐，
        # 重启服务后显示号会变，会话里的程序就找不回自己的显示了。
        self.number = 100 + (zlib.crc32(sid.encode("utf-8")) % 300)
        self.display = f":{self.number}"

    # ------------------------------------------------------------------ 启动
    def ensure(self) -> bool:
        if self.started and self._alive():
            return True
        with self._boot_lock:
            if self.started and self._alive():
                return True
            os.makedirs(self.runtime, exist_ok=True)
            try:
                os.chmod(self.runtime, 0o700)
            except OSError:
                pass
            ok = self._start_wayland() if BACKEND == "wayland" else self._start_xvfb()
            self.started = ok
            return ok

    def _alive(self) -> bool:
        if BACKEND == "wayland":
            return os.path.exists(os.path.join(self.runtime, "wayland-1"))
        return os.path.exists(f"/tmp/.X11-unix/X{self.number}")

    def _start_xvfb(self) -> bool:
        if self._alive():
            return True
        try:
            subprocess.Popen(
                ["Xvfb", self.display, "-screen", "0", f"{W}x{H}x24", "-nolisten", "tcp"],
                stdout=open(os.path.join(self.dir, "xvfb.log"), "ab"),
                stderr=subprocess.STDOUT, start_new_session=True)
        except Exception as exc:                     # noqa: BLE001
            print(f"[{self.sid}] Xvfb 启动失败：{exc}", flush=True)
            return False
        for _ in range(25):
            time.sleep(0.4)
            if self._alive():
                print(f"[{self.sid}] 显示就绪 {self.display}（{W}x{H}）", flush=True)
                return True
        print(f"[{self.sid}] Xvfb 没起来，看 {self.dir}/xvfb.log", flush=True)
        return False

    def _start_wayland(self) -> bool:
        """备用后端：sway + seatd + seat 组 + headless,libinput（见 dsh-display-reset.sh）。"""
        if self._alive():
            return True
        conf = os.path.join(self.dir, "sway.conf")
        with open(conf, "w", encoding="utf-8") as fh:
            fh.write(f"output HEADLESS-1 resolution {W}x{H}\n")
        cmd = (f"setsid env XDG_RUNTIME_DIR={self.runtime} LIBSEAT_BACKEND=seatd "
               f"WLR_BACKENDS=headless,libinput WLR_RENDERER_ALLOW_SOFTWARE=1 "
               f"LIBGL_ALWAYS_SOFTWARE=1 sway -c {conf} >{self.dir}/sway.log 2>&1 &")
        try:
            subprocess.run(["sudo", "-n", "-u", os.environ.get("USER") or "root", "-g", "seat",
                            "sh", "-c", cmd], capture_output=True, timeout=20)
        except Exception as exc:                     # noqa: BLE001
            print(f"[{self.sid}] 合成器启动失败：{exc}", flush=True)
            return False
        for _ in range(24):
            time.sleep(0.5)
            if self._alive():
                print(f"[{self.sid}] 显示就绪（Wayland）", flush=True)
                return True
        print(f"[{self.sid}] 合成器没起来，看 {self.dir}/sway.log", flush=True)
        return False

    @property
    def env(self) -> dict:
        """在该会话显示上跑程序时应使用的环境。"""
        if BACKEND == "wayland":
            return {**os.environ, "XDG_RUNTIME_DIR": self.runtime,
                    "WAYLAND_DISPLAY": "wayland-1"}
        return {**os.environ, "DISPLAY": self.display, "QT_QPA_PLATFORM": "xcb",
                "WAYLAND_DISPLAY": ""}


_sessions: dict[str, Session] = {}
_sessions_lock = threading.Lock()


def session(sid: str) -> Session:
    with _sessions_lock:
        sess = _sessions.get(sid)
        if sess is None:
            sess = _sessions[sid] = Session(sid)
            threading.Thread(target=_grab_loop, args=(sess,), daemon=True).start()
        return sess


# ---------------------------------------------------------------- 输入注入
def run_tool(sess: Session, argv: list[str]) -> None:
    try:
        proc = subprocess.run(argv, env=sess.env, capture_output=True, timeout=15)
        if proc.returncode != 0:
            print(f"[{sess.sid}] {argv[0]} 失败："
                  f"{proc.stderr.decode('utf-8', 'replace')[:200]}", flush=True)
    except Exception as exc:                         # noqa: BLE001
        print(f"[{sess.sid}] {argv[0]} 异常：{type(exc).__name__}: {exc}", flush=True)


#: DOM 的标准键名 → xdotool（X keysym）名。两套名字并不一样，
#: 例如 DOM 叫 Backspace / Enter / ArrowUp，而 X11 叫 BackSpace / Return / Up。
_XDOTOOL_KEY = {
    "Enter": "Return", "Backspace": "BackSpace", "Delete": "Delete",
    "Tab": "Tab", "Escape": "Escape", " ": "space",
    "ArrowUp": "Up", "ArrowDown": "Down", "ArrowLeft": "Left", "ArrowRight": "Right",
    "Home": "Home", "End": "End", "PageUp": "Prior", "PageDown": "Next",
}


def _xdotool_key(key: str) -> str:
    """把页面报上来的键（可能带 ctrl+/super+ 前缀）翻成 xdotool 认的名字。"""
    mods = ""
    base = key
    for prefix in ("ctrl+", "super+", "alt+", "shift+"):
        while base.startswith(prefix):
            mods += prefix
            base = base[len(prefix):]
    return mods + _XDOTOOL_KEY.get(base, base)


def inject(sess: Session, obj: dict) -> None:
    """把一个输入事件注入到该会话自己的显示。"""
    if not sess.ensure():
        return
    if BACKEND == "wayland":
        _inject_wayland(sess, obj)
        return
    kind = obj.get("t")
    x, y = obj.get("x"), obj.get("y")
    if kind in ("click", "move") and x is not None and y is not None:
        px, py = int(float(x) * W), int(float(y) * H)
        run_tool(sess, ["xdotool", "mousemove", str(px), str(py)])
        if kind == "click":
            # xdotool 的按键号：1=左 2=中 3=右（与 evdev 的 0x110/0x111 不是一套，别混）
            btn = {1: "1", 2: "3", 3: "2"}.get(int(obj.get("b") or 1), "1")
            run_tool(sess, ["xdotool", "click", btn])
    elif kind == "wheel":
        dy = float(obj.get("dy") or 0)
        btn = "4" if dy < 0 else "5"                 # 4=上滚 5=下滚
        for _ in range(min(10, max(1, int(abs(dy) / 60) or 1))):
            run_tool(sess, ["xdotool", "click", btn])
    elif kind == "text":
        text = str(obj.get("s") or "")
        if text:
            # 先等一下：焦点刚落定时立刻注入，开头几个字符会掉（Wayland 那边实测过，
            # X11 同样给一点余量更稳）
            time.sleep(0.1)
            run_tool(sess, ["xdotool", "type", "--clearmodifiers", "--delay", "25", text])
    elif kind == "key":
        key = str(obj.get("k") or "")
        if key:
            run_tool(sess, ["xdotool", "key", _xdotool_key(key)])


def _inject_wayland(sess: Session, obj: dict) -> None:
    """备用后端：Wayland 下的注入（协议工具优先，ydotool 兜底）。"""
    kind = obj.get("t")
    x, y = obj.get("x"), obj.get("y")
    if kind in ("click", "move") and x is not None and y is not None:
        px, py = int(float(x) * W), int(float(y) * H)
        if os.path.exists(VPTR):
            run_tool(sess, [VPTR, "absolute", str(px), str(py), str(W), str(H)])
        else:
            run_tool(sess, ["ydotool", "mousemove", "--absolute", "-x", str(px), "-y", str(py)])
        if kind == "click":
            btn = {1: 272, 2: 273, 3: 274}.get(int(obj.get("b") or 1), 272)  # evdev BTN_*
            if os.path.exists(VPTR):
                run_tool(sess, [VPTR, "button", str(btn), "press"])
                time.sleep(0.05)
                run_tool(sess, [VPTR, "button", str(btn), "release"])
            else:
                run_tool(sess, ["ydotool", "click", hex(btn)])
    elif kind == "wheel":
        dy = float(obj.get("dy") or 0)
        button = "0x4" if dy < 0 else "0x5"
        for _ in range(min(10, max(1, int(abs(dy) / 60) or 1))):
            run_tool(sess, ["ydotool", "click", button])
    elif kind == "text":
        text = str(obj.get("s") or "")
        if text:
            time.sleep(0.15)
            run_tool(sess, ["wtype", "-s", "30", "--", text])
    elif kind == "key":
        key = str(obj.get("k") or "")
        if key:
            run_tool(sess, ["wtype", "-k", key])


# ---------------------------------------------------------------- 抓帧
def _grab_once(sess: Session) -> bytes:
    if BACKEND == "wayland":
        cmd = ["grim", "-t", "jpeg", "-q", "80", "-"]
    else:
        # ImageMagick 的 import 直接抓成 JPEG 到 stdout
        cmd = ["import", "-window", "root", "-quality", "80", "JPEG:-"]
    return subprocess.run(cmd, env=sess.env, capture_output=True, timeout=15).stdout


def _grab_loop(sess: Session) -> None:
    """每个会话一个抓帧线程。"""
    while True:
        if sess.ensure():
            try:
                frame = _grab_once(sess)
                if frame:
                    with sess.lock:
                        sess.latest = frame
            except Exception:                        # noqa: BLE001
                pass
        time.sleep(0.5)


# ---------------------------------------------------------------- 页面
PAGE = """<!doctype html><meta charset=utf-8><title>DSH 显示器 · {sid}</title>
<body style="margin:0;background:#0b0b0c;color:#ddd;font:12px system-ui">
<div style="padding:4px 8px;opacity:.75">
  会话 {sid} 的显示器 · 独立显示 {display} · {w}x{h} · 点击画面即可操作（鼠标/键盘都会注入回去）
</div>
<img id="screen" src="{base}/stream" style="width:100%;display:block;cursor:crosshair">
<textarea id="sink" aria-label="keyboard sink"
  style="position:fixed;left:-1000px;top:0;width:10px;height:10px;opacity:0"></textarea>
<script>
(function () {{
  var BASE = '{base}';
  var img = document.getElementById('screen');
  var sink = document.getElementById('sink');
  function norm(ev) {{
    var r = img.getBoundingClientRect();
    return {{ x: (ev.clientX - r.left) / r.width, y: (ev.clientY - r.top) / r.height }};
  }}
  function send(o) {{
    try {{
      fetch(BASE + '/input', {{ method: 'POST', body: JSON.stringify(o) }});
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
  // 输入法（中文）合成：**合成期间绝不能发送**。
  //
  // 踩过的坑：打拼音时隐藏输入框会不断触发 input 事件，值是**未上屏的拼音**
  // （如 "xianshiqi"）→ 早先直接发出去，等上屏后又发一次中文 → 远端同时收到
  // 拼音和中文（用户实测："我只想输入中文的显示器，结果字母也输进去了"）。
  // 正确做法：compositionstart 到 compositionend 之间一律不发，上屏时只发最终文本。
  var composing = false;
  sink.addEventListener('compositionstart', function () {{ composing = true; }});
  sink.addEventListener('compositionend', function () {{
    composing = false;
    if (sink.value) {{ send({{ t: 'text', s: sink.value }}); sink.value = ''; }}
  }});

  // 键盘：普通字符走 input（含输入法合成结果），控制键与组合键走 keydown。
  //
  // ⚠️ 键名必须用 **DOM 的标准名**：退格是 'Backspace'（小写 s）、回车是 'Enter'。
  // 早先写成 'BackSpace' / 'Return'（X11 的 keysym 名）→ indexOf 永远不命中，
  // 于是退格和回车完全没反应（用户实测："打错了删不掉"）。X11 名字的换算放在宿主侧。
  var NAMED = {{ Enter: 1, Backspace: 1, Delete: 1, Tab: 1, Escape: 1,
                ArrowUp: 1, ArrowDown: 1, ArrowLeft: 1, ArrowRight: 1,
                Home: 1, End: 1, PageUp: 1, PageDown: 1 }};
  sink.addEventListener('keydown', function (ev) {{
    // 合成中的按键交给输入法处理（例如回车用于选词），不要转发
    if (ev.isComposing || composing) {{ return; }}
    var mod = ev.ctrlKey ? 'ctrl+' : (ev.metaKey ? 'super+' : '');
    // 组合键（Ctrl+C/V/A…）也走 key 通道；单个可打印字符仍交给 input，避免重复输入
    if (NAMED[ev.key] || (mod && ev.key.length === 1)) {{
      ev.preventDefault();
      send({{ t: 'key', k: mod + ev.key }});
    }}
  }});
  sink.addEventListener('input', function (ev) {{
    // 合成中（含 isComposing 标记）一律不发，避免把拼音当正文送出去
    if (composing || (ev && ev.isComposing)) {{ return; }}
    if (sink.value) {{ send({{ t: 'text', s: sink.value }}); sink.value = ''; }}
  }});
}})();
</script>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:            # 静音：日志留给注入错误
        pass

    def _send(self, body: bytes, ctype: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def _split(path: str) -> tuple[str | None, str]:
        """把 /s/<sid>/<rest> 拆成 (sid, rest)；非会话路径返回 (None, path)。"""
        parts = [p for p in path.split("/") if p]
        if len(parts) >= 2 and parts[0] == "s":
            return parts[1], "/" + "/".join(parts[2:])
        return None, path

    def do_POST(self) -> None:                       # noqa: N802 - BaseHTTPRequestHandler
        sid, rest = self._split(self.path)
        if sid and rest.rstrip("/") == "/input":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length) or b"{}")
                sess = session(sid)
                threading.Thread(target=inject, args=(sess, payload), daemon=True).start()
                body = b'{"ok":true}'
            except Exception as exc:                 # noqa: BLE001
                body = json.dumps({"ok": False, "error": str(exc)}).encode()
            self._send(body, "application/json")
            return
        self.send_error(404)

    def do_GET(self) -> None:                        # noqa: N802
        sid, rest = self._split(self.path)
        if sid is None:
            with _sessions_lock:
                items = sorted(_sessions.items())
            rows = "".join(
                f'<li><a href="/s/{k}/">{k}</a> · {v.display} '
                f'{"（就绪）" if v.started else "（未启动）"}</li>' for k, v in items)
            self._send(
                ("<!doctype html><meta charset=utf-8><title>DSH 显示器</title>"
                 "<body style='background:#0b0b0c;color:#ddd;font:13px system-ui;padding:16px'>"
                 "<h3>DSH 测试显示器（每会话独立）</h3>"
                 f"<ul>{rows or '<li>（暂无会话）</li>'}</ul>"
                 "<p style='opacity:.65'>每个 harness 会话有自己的显示："
                 "<code>/s/&lt;sessionId&gt;/</code> —— 互不可见、互不污染。<br>"
                 "在该会话显示上跑程序："
                 "<code>DISPLAY=:&lt;号&gt; QT_QPA_PLATFORM=xcb 程序</code></p>").encode(),
                "text/html; charset=utf-8")
            return

        sess = session(sid)
        rest = rest.rstrip("/")
        if rest == "/stream":
            sess.ensure()
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            while True:
                with sess.lock:
                    frame = sess.latest
                if frame:
                    try:
                        self.wfile.write(
                            b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                            + str(len(frame)).encode() + b"\r\n\r\n" + frame + b"\r\n")
                    except Exception:                # noqa: BLE001
                        return
                time.sleep(0.4)
            return
        if rest == "/display":
            # 便于脚本查询"这个会话的显示号是多少"
            self._send(json.dumps({"session": sid, "display": sess.display,
                                   "backend": BACKEND, "size": f"{W}x{H}"}).encode(),
                       "application/json")
            return
        sess.ensure()
        self._send(PAGE.format(base=f"/s/{sid}", sid=sid, display=sess.display,
                               w=W, h=H).encode(), "text/html; charset=utf-8")


def main() -> None:
    print(f"viewer on http://127.0.0.1:{PORT}/ · 后端 {BACKEND} · 每会话独立 "
          f"（/s/<sessionId>/）· {W}x{H} · 支持双向注入", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
