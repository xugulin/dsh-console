#!/usr/bin/env python3
#!/usr/bin/env python3
#!/usr/bin/env python3
# ⚠️ 规范位置：https://github.com/xugulin/dsh-display-panel
#    （插件本体、显示器服务、systemd 单元都在那个仓库；这里是开发副本，
"""DSH 测试显示器：**每个 harness 会话一台完全独立的显示**，能看画面，也能把
浏览器里的鼠标/键盘操作注入回去。
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

## Windows（win32 后端）

Windows 没有 Xvfb 这类可以任意多开的 headless 显示服务器（虚拟显示器要装内核
驱动、要管理员），所以 win32 后端**直接抓真实桌面**：GDI ``BitBlt`` 取像素 +
GDI+ 编码 JPEG，纯 ``ctypes``，不依赖 Pillow / ffmpeg / ImageMagick。

* 代价一：**所有会话看到的是同一块屏**，per-session 隔离在这条路上不存在
  （Windows 上本来也只有一块桌面可以看）。
* 代价二：注入的是**真实**鼠标键盘。因此 win32 下注入**默认关闭**（只读观看），
  要开就设 ``DSH_VIEW_INPUT=1``；页面抬头会写明当前是哪种模式。

## 环境变量

``DSH_DISPLAY_HOME``（默认 ~/.cache/dsh-display）、``DSH_VIEW_PORT``（默认 8099）、
``DSH_VIEW_SIZE``（默认 1600x1000；win32 下默认取真实屏幕）、
``DSH_VIEW_BACKEND=x11|wayland|win32|darwin``（Windows 默认 win32，macOS 默认 darwin，
其余默认 x11；macOS 后端**尚未真机验证**，属实验性）、
``DSH_VIEW_INPUT=1``（仅 win32：允许把点击/按键注入真实桌面，默认关）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
#: 抓的是**本机真实桌面**的后端（win32 / darwin）：没有"每会话一台显示"这回事，
#: 所有会话看的是同一块屏，而且注入进去就是真的动用户的鼠标键盘。
REAL_DESKTOP_BACKENDS = ("win32", "darwin")
HOME_DIR = os.environ.get("DSH_DISPLAY_HOME") or os.path.expanduser("~/.cache/dsh-display")
PORT = int(os.environ.get("DSH_VIEW_PORT", "8099"))
W, H = (int(x) for x in (os.environ.get("DSH_VIEW_SIZE") or "1600x1000").split("x"))
BACKEND = (os.environ.get("DSH_VIEW_BACKEND")
           or ("win32" if IS_WIN else ("darwin" if IS_MAC else "x11"))).strip().lower()
#: win32 / darwin 下是否允许把输入注入真实桌面（默认只读观看）。
#: 注意：这两个后端抓的是**本机真实桌面**，注入进去就是真的动用户的鼠标键盘，
#: 所以默认关闭 —— 想开显式设 DSH_VIEW_INPUT=1。
WIN_INPUT = (os.environ.get("DSH_VIEW_INPUT") or "").strip().lower() in ("1", "true", "yes", "on")
MAC_INPUT = WIN_INPUT
#: 抓帧间隔。win32 是纯本地调用（实测约 34ms/帧），可以贴着页面 130ms 的拉帧节奏来；
#: X11/Wayland 每次都要起一个外部进程，间隔给大一点，别把 CPU 烧在抓屏上。
#: win32 是进程内 GDI 调用（实测约 34ms/帧），darwin 每次要起一个 screencapture 进程，
#: X11/Wayland 也都要起外部进程 —— 后两者给大一点，别把 CPU 烧在抓屏上。
GRAB_INTERVAL = {"win32": 0.12, "darwin": 0.3}.get(BACKEND, 0.5)
#: 协议注入工具（tools/virtual-pointer 编译产物），只在 wayland 后端用得到。
VPTR = os.environ.get("DSH_VIEW_VPTR") or os.path.join(HOME_DIR, "vptr", "vptr")

#: 各后端需要的命令：(命令, 用途, 常见包名)。win32 是纯 ctypes，**零外部依赖**。
REQUIRED_TOOLS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "x11": (
        ("Xvfb", "虚拟显示服务器", "xorg-server-xvfb / xvfb"),
        ("xdotool", "鼠标与键盘注入", "xdotool"),
        ("import", "抓帧（ImageMagick）", "imagemagick"),
        ("xclip", "中文输入（剪贴板）", "xclip"),
    ),
    "wayland": (
        ("sway", "显示合成器", "sway"),
        ("grim", "抓帧", "grim"),
        ("wtype", "键盘注入", "wtype"),
    ),
    "win32": (),          # 纯 ctypes，不需要外部命令
    "darwin": (
        ("screencapture", "抓帧（macOS 自带）", "系统自带，无需安装"),
    ),
}


def ensure_token() -> str:
    """确保本用户的访问令牌存在（``<HOME_DIR>/token``，权限 600）。

    为什么要令牌：服务监听 127.0.0.1，**同一台机器上的其他用户**也能连上来 ——
    不设防的话，另一个用户的 DSH 面板会看到你的桌面（插件按端口探测，先应答者胜）。
    令牌放在本用户的 ``~/.cache/dsh-display/``（目录 700），别的用户读不到，
    于是只有本用户的面板能连上。令牌由插件的**宿主半边**（以该用户身份运行）读出、
    经宿主自己的接口转交给浏览器 —— 浏览器不需要文件系统权限。
    """
    import secrets

    path = os.path.join(HOME_DIR, "token")
    try:
        with open(path, encoding="utf-8") as fh:
            token = fh.read().strip()
        if token:
            return token
    except OSError:
        pass
    token = secrets.token_hex(16)
    try:
        os.makedirs(HOME_DIR, exist_ok=True)
        os.chmod(HOME_DIR, 0o700)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(token)
        os.chmod(path, 0o600)
    except OSError as exc:
        print(f"⚠ 写令牌文件失败（本次不强制校验）：{exc}", flush=True)
    return token


TOKEN = ensure_token()


def token_ok(path: str) -> bool:
    """请求是否带对令牌（``?k=<token>``）。没有令牌（写文件失败）时不设防。"""
    if not TOKEN:
        return True
    from urllib.parse import parse_qs, urlparse

    return parse_qs(urlparse(path).query).get("k", [""])[0] == TOKEN


def missing_tools() -> list[dict[str, str]]:
    """返回缺失的依赖（命令 / 用途 / 常见包名）。

    为什么要自检：缺工具时的表现**非常隐蔽** —— 缺 ``xclip`` 只是"中文打不进去"、
    缺 ``import`` 只是"画面一直黑"，用户根本看不出是缺东西（真实反馈过这类问题）。
    所以启动时查一遍，并且通过 ``/state`` 与页面把那句话说清楚。
    """
    import shutil

    missing: list[dict[str, str]] = []
    for name, why, pkg in REQUIRED_TOOLS.get(BACKEND, ()):
        if shutil.which(name) is None:
            missing.append({"tool": name, "why": why, "package": pkg})
    return missing


# ================================================================ darwin 后端（实验性）
# ⚠️ 这一段**没有在真机上验证过**（作者手上没有 Mac）—— 逻辑按官方文档写，
#    每一步都包了异常，失败只记日志、不影响别的后端。欢迎 macOS 用户回报结果。
#    抓帧用系统自带的 screencapture（无需安装任何东西）；注入用 Quartz 的 CGEvent。
if IS_MAC:
    import tempfile

    _cg = ctypes.cdll.LoadLibrary(
        "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
    _cf = ctypes.cdll.LoadLibrary(
        "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")

    class CGPoint(ctypes.Structure):
        _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]

    class CGSize(ctypes.Structure):
        _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]

    class CGRect(ctypes.Structure):
        _fields_ = [("origin", CGPoint), ("size", CGSize)]

    _cg.CGMainDisplayID.restype = ctypes.c_uint32
    _cg.CGDisplayBounds.restype = CGRect
    _cg.CGDisplayBounds.argtypes = [ctypes.c_uint32]
    _cg.CGEventCreateMouseEvent.restype = ctypes.c_void_p
    _cg.CGEventCreateMouseEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint32, CGPoint, ctypes.c_uint32]
    _cg.CGEventCreateKeyboardEvent.restype = ctypes.c_void_p
    _cg.CGEventCreateKeyboardEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_bool]
    _cg.CGEventKeyboardSetUnicodeString.restype = None
    _cg.CGEventKeyboardSetUnicodeString.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
                                                    ctypes.POINTER(ctypes.c_uint16)]
    _cg.CGEventCreateScrollWheelEvent.restype = ctypes.c_void_p
    _cg.CGEventCreateScrollWheelEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                                  ctypes.c_uint32, ctypes.c_int32]
    _cg.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
    _cf.CFRelease.argtypes = [ctypes.c_void_p]

    _MOVE, _LDOWN, _LUP, _RDOWN, _RUP = 5, 1, 2, 3, 4
    _BTN_LEFT, _BTN_RIGHT = 0, 1
    _HID = 0                      # kCGHIDEventTap
    _PIXEL_UNITS = 0

    def _mac_screen() -> tuple[int, int]:
        """主屏尺寸（**点**，不是像素 —— Retina 上两者不同，事件坐标用点）。"""
        r = _cg.CGDisplayBounds(_cg.CGMainDisplayID())
        return int(r.size.width), int(r.size.height)

    def _mac_mouse(x: int, y: int, kind: int, button: int = _BTN_LEFT) -> None:
        ev = _cg.CGEventCreateMouseEvent(None, kind, CGPoint(float(x), float(y)), button)
        if ev:
            _cg.CGEventPost(_HID, ev)
            _cf.CFRelease(ev)

    #: 控制键在 macOS 上的等价字符（用 Unicode 送单字符即可被 App 当成对应按键）。
    _MAC_KEY_ALIASES = {"Enter": "\r", "Backspace": "\x7f", "Tab": "\t", "Escape": "\x1b"}

    def _mac_key_text(text: str) -> None:
        """按 **Unicode 字符串**送字（不用键码表）—— 中文、符号都能过。"""
        for ch in text:
            code = ord(ch)
            ev = _cg.CGEventCreateKeyboardEvent(None, 0, True)
            if not ev:
                continue
            buf = (ctypes.c_uint16 * 1)(code)
            _cg.CGEventKeyboardSetUnicodeString(ev, 1, buf)
            _cg.CGEventPost(_HID, ev)
            _cf.CFRelease(ev)

    def _grab_darwin() -> bytes:
        """抓一帧：系统自带的 screencapture 只能写文件，所以落到临时文件再读回。"""
        fd, path = tempfile.mkstemp(suffix=".jpg")
        os.close(fd)
        try:
            subprocess.run(["screencapture", "-x", "-t", "jpeg", path],
                           capture_output=True, timeout=15)
            with open(path, "rb") as fh:
                return fh.read()
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass


# ================================================================ win32 后端
# 只放平台原语：抓一帧、注入一个事件。取舍见文件头「Windows（win32 后端）」。
if IS_WIN:
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    _gdiplus = ctypes.WinDLL("gdiplus", use_last_error=True)

    class _BMIH(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                    ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    class _BMI(ctypes.Structure):
        _fields_ = [("bmiHeader", _BMIH), ("bmiColors", wintypes.DWORD * 3)]

    class _GdipStartup(ctypes.Structure):
        _fields_ = [("GdiplusVersion", ctypes.c_uint32), ("DebugEventCallback", ctypes.c_void_p),
                    ("SuppressBackgroundThread", ctypes.c_int), ("SuppressExternalCodecs", ctypes.c_int)]

    class _GUID(ctypes.Structure):
        _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                    ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]

    class _MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.c_void_p)]

    class _KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_void_p)]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT)]

    class _INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]

    # 参数类型必须显式声明：64 位下不声明会被按 int 截断成野指针。
    _user32.GetDC.restype = wintypes.HDC
    _user32.GetDC.argtypes = [wintypes.HWND]
    _user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    _user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    _user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]
    _user32.SendInput.restype = wintypes.UINT
    _gdi32.CreateCompatibleDC.restype = wintypes.HDC
    _gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    _gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    _gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.POINTER(_BMI), wintypes.UINT,
                                        ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
    _gdi32.SelectObject.restype = wintypes.HGDIOBJ
    _gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    _gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                              wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
    _gdiplus.GdiplusStartup.argtypes = [ctypes.POINTER(ctypes.c_void_p),
                                        ctypes.POINTER(_GdipStartup), ctypes.c_void_p]
    _gdiplus.GdipCreateBitmapFromScan0.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                                   ctypes.c_int, ctypes.c_void_p,
                                                   ctypes.POINTER(ctypes.c_void_p)]
    _gdiplus.GdipSaveImageToFile.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p,
                                             ctypes.POINTER(_GUID), ctypes.c_void_p]
    _gdiplus.GdipDisposeImage.argtypes = [ctypes.c_void_p]

    _SRCCOPY = 0x00CC0020
    _DIB_RGB_COLORS = 0
    _BI_RGB = 0
    _PIXELFORMAT_32BPP_RGB = 0x00022009
    _SM_CXSCREEN, _SM_CYSCREEN = 0, 1
    # GDI+ 内置编码器的固定 CLSID
    _JPEG_CLSID = _GUID(0x557CF401, 0x1A04, 0x11D3,
                        (ctypes.c_ubyte * 8)(0x9A, 0x73, 0x00, 0x00, 0xF8, 0x1E, 0xF3, 0x2E))

    def _dpi_aware() -> str:
        """让 GetSystemMetrics/BitBlt 走物理像素；否则缩放屏上抓到的是被拉伸的画面。"""
        try:
            if _user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):  # PER_MONITOR_AWARE_V2
                return "per-monitor-v2"
        except Exception:                            # noqa: BLE001
            pass
        try:
            if _user32.SetProcessDPIAware():
                return "system"
        except Exception:                            # noqa: BLE001
            pass
        return "none"

    _DPI_MODE = _dpi_aware()

    # 没显式给尺寸就按真实屏幕来：W/H 是页面抬头和注入坐标换算的共同基准，
    # 抓多大就写多大，否则点击会落偏。
    if not os.environ.get("DSH_VIEW_SIZE"):
        W = int(_user32.GetSystemMetrics(_SM_CXSCREEN))
        H = int(_user32.GetSystemMetrics(_SM_CYSCREEN))


    class WinScreen:
        """真实桌面的抓帧器：GDI ``BitBlt`` → DIB → GDI+ 编码 JPEG。"""

        def __init__(self) -> None:
            self.lock = threading.Lock()             # 所有会话共用一块屏，一把锁够了
            os.makedirs(HOME_DIR, exist_ok=True)
            self.path = os.path.join(HOME_DIR, "win-frame.jpg")
            self.token = ctypes.c_void_p()
            startup = _GdipStartup(1, None, 0, 0)
            if _gdiplus.GdiplusStartup(ctypes.byref(self.token), ctypes.byref(startup), None) != 0:
                raise RuntimeError("GdiplusStartup 失败")
            self.w, self.h = self.size()

        def size(self) -> tuple[int, int]:
            return (int(_user32.GetSystemMetrics(_SM_CXSCREEN)),
                    int(_user32.GetSystemMetrics(_SM_CYSCREEN)))

        def grab(self) -> bytes:
            w, h = self.w, self.h
            with self.lock:
                screen_dc = _user32.GetDC(None)
                mem_dc = _gdi32.CreateCompatibleDC(screen_dc)
                bmi = _BMI()
                bmi.bmiHeader.biSize = ctypes.sizeof(_BMIH)
                bmi.bmiHeader.biWidth = w
                bmi.bmiHeader.biHeight = -h          # 负数 = 自上而下，与 PNG/JPEG 行序一致
                bmi.bmiHeader.biPlanes = 1
                bmi.bmiHeader.biBitCount = 32
                bmi.bmiHeader.biCompression = _BI_RGB
                bits = ctypes.c_void_p()
                hbmp = _gdi32.CreateDIBSection(screen_dc, ctypes.byref(bmi), _DIB_RGB_COLORS,
                                               ctypes.byref(bits), None, 0)
                old = _gdi32.SelectObject(mem_dc, hbmp)
                try:
                    if not _gdi32.BitBlt(mem_dc, 0, 0, w, h, screen_dc, 0, 0, _SRCCOPY):
                        return b""
                    bmp = ctypes.c_void_p()
                    st = _gdiplus.GdipCreateBitmapFromScan0(w, h, w * 4, _PIXELFORMAT_32BPP_RGB,
                                                            bits, ctypes.byref(bmp))
                    if st != 0:
                        print(f"[win32] GdipCreateBitmapFromScan0 失败：{st}", flush=True)
                        return b""
                    try:
                        st = _gdiplus.GdipSaveImageToFile(bmp, self.path,
                                                          ctypes.byref(_JPEG_CLSID), None)
                        if st != 0:
                            print(f"[win32] GdipSaveImageToFile 失败：{st}", flush=True)
                            return b""
                    finally:
                        _gdiplus.GdipDisposeImage(bmp)
                    with open(self.path, "rb") as fh:
                        return fh.read()
                finally:
                    _gdi32.SelectObject(mem_dc, old)
                    _gdi32.DeleteObject(hbmp)
                    _gdi32.DeleteDC(mem_dc)
                    _user32.ReleaseDC(None, screen_dc)

    # ------------------------------------------------------------ 输入注入
    _INPUT_MOUSE, _INPUT_KEYBOARD = 0, 1
    _MEF_MOVE, _MEF_ABSOLUTE = 0x0001, 0x8000
    _MEF_LEFTDOWN, _MEF_LEFTUP = 0x0002, 0x0004
    _MEF_RIGHTDOWN, _MEF_RIGHTUP = 0x0008, 0x0010
    _MEF_MIDDLEDOWN, _MEF_MIDDLEUP = 0x0020, 0x0040
    _MEF_WHEEL = 0x0800
    _KEF_EXTENDED, _KEF_KEYUP, _KEF_UNICODE = 0x0001, 0x0002, 0x0004
    _WHEEL_DELTA = 120

    #: DOM 键名 → Windows 虚拟键码（VK）。
    _VK = {
        "Enter": 0x0D, "Backspace": 0x08, "Delete": 0x2E, "Tab": 0x09, "Escape": 0x1B,
        " ": 0x20, "ArrowUp": 0x26, "ArrowDown": 0x28, "ArrowLeft": 0x25, "ArrowRight": 0x27,
        "Home": 0x24, "End": 0x23, "PageUp": 0x21, "PageDown": 0x22,
        "ctrl+": 0x11, "shift+": 0x10, "alt+": 0x12, "super+": 0x5B,
    }

    def _send_inputs(items: list[_INPUT]) -> int:
        if not items:
            return 0
        arr = (_INPUT * len(items))(*items)
        return int(_user32.SendInput(len(items), arr, ctypes.sizeof(_INPUT)))

    def _win_mouse(px: int, py: int, flags: int, data: int = 0) -> int:
        """绝对坐标要走 0..65535 归一化；先把光标定位再按键，避免点错位置。"""
        sw, sh = max(1, W - 1), max(1, H - 1)
        nx, ny = int(px * 65535 / sw), int(py * 65535 / sh)
        items = []
        if flags & (_MEF_LEFTDOWN | _MEF_RIGHTDOWN | _MEF_MIDDLEDOWN):
            items.append(_INPUT(_INPUT_MOUSE, _INPUTUNION(mi=_MOUSEINPUT(
                nx, ny, 0, _MEF_MOVE | _MEF_ABSOLUTE, 0, None))))
        items.append(_INPUT(_INPUT_MOUSE, _INPUTUNION(mi=_MOUSEINPUT(
            nx, ny, data, flags, 0, None))))
        return _send_inputs(items)

    def _win_text(text: str) -> int:
        """任意 Unicode 直接走 KEYEVENTF_UNICODE —— 中文不用剪贴板，比 X11 那条路省事。"""
        items = []
        for ch in text:
            code = ord(ch)
            if code > 0xFFFF:                        # 非 BMP 走代理对
                code -= 0x10000
                pairs = [0xD800 + (code >> 10), 0xDC00 + (code & 0x3FF)]
            else:
                pairs = [code]
            for unit in pairs:
                items.append(_INPUT(_INPUT_KEYBOARD, _INPUTUNION(ki=_KEYBDINPUT(
                    0, unit, _KEF_UNICODE, 0, None))))
                items.append(_INPUT(_INPUT_KEYBOARD, _INPUTUNION(ki=_KEYBDINPUT(
                    0, unit, _KEF_UNICODE | _KEF_KEYUP, 0, None))))
        return _send_inputs(items)

    def _win_key(key: str) -> int:
        mods, base = [], key
        for prefix in ("ctrl+", "super+", "alt+", "shift+"):
            while base.startswith(prefix):
                mods.append(_VK[prefix])
                base = base[len(prefix):]
        vk = _VK.get(base)
        if vk is None and len(base) == 1:
            vk = _user32.VkKeyScanW(ctypes.c_wchar(base)) & 0xFF
        if not vk:
            return 0
        items = [_INPUT(_INPUT_KEYBOARD, _INPUTUNION(ki=_KEYBDINPUT(m, 0, 0, 0, None))) for m in mods]
        items.append(_INPUT(_INPUT_KEYBOARD, _INPUTUNION(ki=_KEYBDINPUT(vk, 0, 0, 0, None))))
        items.append(_INPUT(_INPUT_KEYBOARD, _INPUTUNION(ki=_KEYBDINPUT(vk, 0, _KEF_KEYUP, 0, None))))
        items += [_INPUT(_INPUT_KEYBOARD, _INPUTUNION(ki=_KEYBDINPUT(m, 0, _KEF_KEYUP, 0, None)))
                  for m in reversed(mods)]
        return _send_inputs(items)

    def _win_windows() -> int:
        """可见的顶层窗口数（用于页面的"空闲"提示）。"""
        count = 0
        enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def callback(hwnd, _lparam):
            nonlocal count
            if _user32.IsWindowVisible(hwnd):
                length = _user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    count += 1
            return True

        try:
            _user32.EnumWindows(enum_proc(callback), 0)
        except Exception:                            # noqa: BLE001
            return -1
        return count

    _WIN_SCREEN: "WinScreen | None" = None
    _WIN_SCREEN_LOCK = threading.Lock()

    def win_screen() -> "WinScreen":
        global _WIN_SCREEN
        with _WIN_SCREEN_LOCK:
            if _WIN_SCREEN is None:
                _WIN_SCREEN = WinScreen()
            return _WIN_SCREEN



class Session:
    """一个 harness 会话独占的显示（X11 默认；wayland / win32 为备用后端）。"""

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
        # win32 上没有"这一路的显示号"——所有会话看的都是同一块真实桌面。
        self.display = ("真实桌面" if BACKEND in REAL_DESKTOP_BACKENDS
                        else f":{self.number}")

    # ------------------------------------------------------------------ 启动
    def ensure(self) -> bool:
        if BACKEND in REAL_DESKTOP_BACKENDS:
            self.started = True                      # 真实桌面，没有要拉起的显示服务器
            return True
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
        """显示是否真的可用。

        ⚠️ X11 下**不能只看 socket 文件是否存在**：进程被杀后 socket 文件会残留，
        于是"文件在、服务没了"，后续抓帧/注入全部失败（实测踩过：一堆遗留 Xvfb
        造成了难以理解的怪现象）。这里实际连一次确认。
        """
        if BACKEND in REAL_DESKTOP_BACKENDS:
            return True                              # 真实桌面永远"在"
        if BACKEND == "wayland":
            return os.path.exists(os.path.join(self.runtime, "wayland-1"))
        if not os.path.exists(f"/tmp/.X11-unix/X{self.number}"):
            return False
        try:
            proc = subprocess.run(["xdotool", "getdisplaygeometry"],
                                  env={**os.environ, "DISPLAY": self.display, "WAYLAND_DISPLAY": ""},
                                  capture_output=True, timeout=6)
            return proc.returncode == 0 and bool(proc.stdout.strip())
        except Exception:                            # noqa: BLE001
            return False

    def _start_xvfb(self) -> bool:
        if self._alive():
            return True
        try:
            proc = subprocess.Popen(
                ["Xvfb", self.display, "-screen", "0", f"{W}x{H}x24", "-nolisten", "tcp"],
                stdout=open(os.path.join(self.dir, "xvfb.log"), "ab"),
                stderr=subprocess.STDOUT)
            _spawned.append(proc)                    # 交给退出清理
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
        if BACKEND in REAL_DESKTOP_BACKENDS:
            return dict(os.environ)                  # 真实桌面：原样用当前环境
        if BACKEND == "wayland":
            return {**os.environ, "XDG_RUNTIME_DIR": self.runtime,
                    "WAYLAND_DISPLAY": "wayland-1"}
        return {**os.environ, "DISPLAY": self.display, "QT_QPA_PLATFORM": "xcb",
                "WAYLAND_DISPLAY": ""}


_sessions: dict[str, Session] = {}
_sessions_lock = threading.Lock()

#: 本服务拉起的显示服务器进程 —— 服务退出时要**自己收拾干净**。
#: 早先用 start_new_session=True 起 Xvfb 又配了 KillMode=process，结果停服务时只杀主进程，
#: Xvfb 全变成孤儿（systemd 日志里 "remains running after unit stopped"，
#: 既泄漏显示又让 systemd 认为服务实现有缺陷）。
_spawned: list[subprocess.Popen] = []


def _cleanup_spawned() -> None:
    """退出前把自己拉起的显示服务器一并终止。"""
    for proc in list(_spawned):
        try:
            if proc.poll() is None:
                proc.terminate()
        except Exception:                            # noqa: BLE001
            pass
    deadline = time.time() + 3
    for proc in list(_spawned):
        try:
            remaining = max(0.1, deadline - time.time())
            proc.wait(timeout=remaining)
        except Exception:                            # noqa: BLE001
            try:
                proc.kill()
            except Exception:                        # noqa: BLE001
                pass


def _install_signal_handlers() -> None:
    import signal as _signal

    def handler(_signum, _frame):
        _cleanup_spawned()
        raise SystemExit(0)

    # Windows 补丁（原写法把 SIGHUP 放进元组字面量，求值发生在 try 之外，
    # Windows 没有 signal.SIGHUP -> 服务在绑端口之前就 AttributeError 崩掉）。
    for _name in ("SIGTERM", "SIGINT", "SIGHUP"):
        sig = getattr(_signal, _name, None)
        if sig is None:
            continue
        try:
            _signal.signal(sig, handler)
        except Exception:                            # noqa: BLE001
            pass


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
    if BACKEND == "win32":
        _inject_win32(sess, obj)
        return
    if BACKEND == "darwin":
        _inject_darwin(sess, obj)
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
            _type_text(sess, text)
    elif kind == "key":
        key = str(obj.get("k") or "")
        if key:
            run_tool(sess, ["xdotool", "key", _xdotool_key(key)])


def _type_text(sess: Session, text: str) -> None:
    """把一段文本送进该会话显示上**有焦点**的那个控件。

    ASCII 直接 ``xdotool type``；**含非 ASCII（中文等）必须走剪贴板 + Ctrl+V**：
    ``xdotool type`` 是靠临时映射 keysym 打字符的，CJK 上不可靠 —— 实测
    "中文显示器" 一个字都进不去（自测第 9 项就是这条）。剪贴板路线对任意 Unicode 都稳。
    """
    if all(ord(ch) < 128 for ch in text):
        run_tool(sess, ["xdotool", "type", "--clearmodifiers", "--delay", "25", text])
        return
    # ⚠️ xclip 必须给 ``-l``（服务若干次选区请求后再退出）：Qt 读剪贴板是**分几次请求**的
    # （先问 TARGETS、再取数据），默认只服务一次就退出 → 数据还没取到就没主了，
    # 表现为"Ctrl+V 什么都没粘上"（这就是我上一版失败的原因）。
    # 又因为它会驻留，不能用 subprocess.run 等它 —— 用 Popen 喂完 stdin 就走。
    try:
        proc = subprocess.Popen(["xclip", "-selection", "clipboard", "-l", "20"],
                                env=sess.env, stdin=subprocess.PIPE,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        proc.stdin.write(text.encode("utf-8"))
        proc.stdin.close()
    except Exception as exc:                         # noqa: BLE001
        print(f"[{sess.sid}] xclip 异常：{type(exc).__name__}: {exc}", flush=True)
        return
    time.sleep(0.4)                                  # 等选区真正建立
    run_tool(sess, ["xdotool", "key", "--clearmodifiers", "ctrl+v"])


def _inject_darwin(sess: Session, obj: dict) -> None:
    """macOS 注入：Quartz CGEvent（纯 ctypes）。

    ⚠️ 与 win32 同理：注入的是**真实**鼠标键盘，所以由 DSH_VIEW_INPUT 控制，
    **默认关闭**（只读观看）。这段也**没有在真机上验证过**（作者没有 Mac）。
    """
    if not MAC_INPUT:
        return
    kind = obj.get("t")
    x, y = obj.get("x"), obj.get("y")
    try:
        sw, sh = _mac_screen()
        if kind in ("click", "move") and x is not None and y is not None:
            px, py = int(float(x) * sw), int(float(y) * sh)
            _mac_mouse(px, py, _MOVE)
            if kind == "click":
                right = int(obj.get("b") or 1) == 2
                _mac_mouse(px, py, _RDOWN if right else _LDOWN,
                           _BTN_RIGHT if right else _BTN_LEFT)
                time.sleep(0.02)
                _mac_mouse(px, py, _RUP if right else _LUP,
                           _BTN_RIGHT if right else _BTN_LEFT)
        elif kind == "wheel":
            dy = float(obj.get("dy") or 0)
            ev = _cg.CGEventCreateScrollWheelEvent(
                None, _PIXEL_UNITS, 1, int(-dy / 3) or (1 if dy > 0 else -1))
            if ev:
                _cg.CGEventPost(_HID, ev)
                _cf.CFRelease(ev)
        elif kind == "text":
            text = str(obj.get("s") or "")
            if text:
                time.sleep(0.1)                      # 给焦点一点时间（同 Linux 侧的经验）
                _mac_key_text(text)
        elif kind == "key":
            key = str(obj.get("k") or "")
            if key:
                _mac_key_text(_MAC_KEY_ALIASES.get(key, ""))
    except Exception as exc:                         # noqa: BLE001
        print(f"[darwin] 注入失败：{type(exc).__name__}: {exc}", flush=True)


def _inject_win32(sess: Session, obj: dict) -> None:
    """把事件注入**真实桌面**。

    ⚠️ 默认关闭（``DSH_VIEW_INPUT=1`` 才开）：Windows 上没有独立的虚拟显示，
    注入的就是用户本人的鼠标键盘，误开会在用户正在用的桌面上真点下去。
    """
    if not WIN_INPUT:
        return
    kind = obj.get("t")
    x, y = obj.get("x"), obj.get("y")
    if kind in ("click", "move") and x is not None and y is not None:
        px, py = int(float(x) * W), int(float(y) * H)
        down, up = {1: (_MEF_LEFTDOWN, _MEF_LEFTUP),
                    2: (_MEF_MIDDLEDOWN, _MEF_MIDDLEUP),
                    3: (_MEF_RIGHTDOWN, _MEF_RIGHTUP)}.get(
                        int(obj.get("b") or 1), (_MEF_LEFTDOWN, _MEF_LEFTUP))
        flags = (_MEF_MOVE | _MEF_ABSOLUTE) if kind == "move" else down
        if _win_mouse(px, py, flags) == 0:
            print(f"[{sess.sid}] SendInput 失败：{ctypes.get_last_error()}", flush=True)
            return
        if kind == "click":
            time.sleep(0.02)
            _win_mouse(px, py, up)
    elif kind == "wheel":
        dy = float(obj.get("dy") or 0)
        delta = _WHEEL_DELTA if dy > 0 else -_WHEEL_DELTA
        for _ in range(min(10, max(1, int(abs(dy) / _WHEEL_DELTA) or 1))):
            _send_inputs([_INPUT(_INPUT_MOUSE, _INPUTUNION(
                mi=_MOUSEINPUT(0, 0, delta, _MEF_WHEEL, 0, None)))])
    elif kind == "text":
        text = str(obj.get("s") or "")
        if text:
            time.sleep(0.1)                          # 与 X11 那条路同样的余量
            _win_text(text)
    elif kind == "key":
        key = str(obj.get("k") or "")
        if key:
            _win_key(key)


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
    if BACKEND == "win32":
        return win_screen().grab()
    if BACKEND == "darwin":
        return _grab_darwin()
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
        time.sleep(GRAB_INTERVAL)


# ---------------------------------------------------------------- 页面
PAGE = """<!doctype html><meta charset=utf-8><title>DSH 显示器 · {sid}</title>
<body style="margin:0;background:#0b0b0c;color:#ddd;font:12px system-ui">
<div style="padding:4px 8px;opacity:.75">
  会话 {sid} 的显示器 · {disp} · {w}x{h} · {note}
</div>
<canvas id="screen" style="width:100%;display:block;cursor:crosshair"></canvas>
<div id="offline" style="display:none;padding:10px;opacity:.7">正在连接显示器…</div>
<div id="idle" style="display:none;position:fixed;left:0;right:0;bottom:12px;text-align:center;
     font-size:12px;opacity:.45;pointer-events:none">
  显示器空闲 —— 这台显示上还没有程序在运行（让 AI 帮你拉起来即可）
</div>
<textarea id="sink" aria-label="keyboard sink"
  style="position:fixed;left:-1000px;top:0;width:10px;height:10px;opacity:0"></textarea>
<script>
(function () {{
  var BASE = '{base}';
  var K = '{k}';            // 访问令牌：宿主半边交给浏览器，页面自己也带着它去拉帧
  function withToken(url) {{ return K ? url + (url.indexOf('?') < 0 ? '?' : '&') + 'k=' + K : url; }}
  var canvas = document.getElementById('screen');
  var ctx = canvas.getContext('2d');
  var offline = document.getElementById('offline');
  // 画面自愈：**主动拉单帧画到 canvas**，而不是把 MJPEG 塞进 <img>。
  // 原因：<img> 上的 MJPEG 一旦断开（例如 viewer 重启）不会重连，画面就永远黑着
  // —— 用户实测到的"一片漆黑"有一部分就是这个。canvas 方案断了会自动续上。
  (function pull() {{
    var im = new Image();
    im.onload = function () {{
      offline.style.display = 'none';
      if (canvas.width !== im.naturalWidth || canvas.height !== im.naturalHeight) {{
        canvas.width = im.naturalWidth; canvas.height = im.naturalHeight;
      }}
      ctx.drawImage(im, 0, 0);
      setTimeout(pull, 130);            // 约 7~8 帧/秒，看操作足够
    }};
    im.onerror = function () {{
      offline.style.display = 'block';
      setTimeout(pull, 1000);           // 失败就重试
    }};
    im.src = withToken(BASE + '/snapshot?t=' + Date.now());
  }})();
  var img = canvas;
  // 空闲提示：每 2 秒问一次该显示上有几个窗口；没有窗口就提示，避免"全黑=坏了"的误解
  (function pollState() {{
    fetch(withToken(BASE + '/state?t=' + Date.now()), {{ cache: 'no-store' }})
      .then(function (r) {{ return r.json(); }})
      .then(function (d) {{
        document.getElementById('idle').style.display =
          (d && d.idle === true) ? 'block' : 'none';
      }})
      .catch(function () {{}})
      .then(function () {{ setTimeout(pollState, 2000); }});
  }})();
  var sink = document.getElementById('sink');
  function norm(ev) {{
    var r = img.getBoundingClientRect();
    return {{ x: (ev.clientX - r.left) / r.width, y: (ev.clientY - r.top) / r.height }};
  }}
  function send(o) {{
    try {{
      fetch(withToken(BASE + '/input'), {{ method: 'POST', body: JSON.stringify(o) }});
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
        # ⚠️ 必须禁用缓存：页面里写着"本会话的显示号"，而显示号/服务状态会变。
        # 早先没设这些头，Chromium 缓存了旧页面 —— 用户刷新后仍看到旧显示号
        # （页头写着 :233、而实际已是 :265），旧显示又已被清理 → 满屏漆黑。
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def _split(path: str) -> tuple[str | None, str]:
        """把 /s/<sid>/<rest> 拆成 (sid, rest)；非会话路径返回 (None, path)。

        ⚠️ **必须先剥掉查询串**：页面为了防缓存会带 ``?t=<时间戳>`` / ``?v=…``，
        早先拿整条 path（含 ?…）去比对 → 所有带参数的请求统统 404，
        表现为"页面一直在连接显示器…"（实测：连新开的浏览器里也是黑的）。
        """
        path = path.split("?", 1)[0]
        parts = [p for p in path.split("/") if p]
        if len(parts) >= 2 and parts[0] == "s":
            return parts[1], "/" + "/".join(parts[2:])
        return None, path

    def _deny(self) -> None:
        body = json.dumps({
            "ok": False, "error": "missing or bad token",
            "hint": "本机其它用户访问不了本服务；请用 DSH 的「显示器」面板，"
                    "或带上 ?k=<~/.cache/dsh-display/token 的内容>",
        }).encode()
        self.send_response(403)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:                       # noqa: N802 - BaseHTTPRequestHandler
        if not token_ok(self.path):
            self._deny()
            return
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
        if not token_ok(self.path):
            self._deny()
            return
        sid, rest = self._split(self.path)
        if sid is None:
            with _sessions_lock:
                items = sorted(_sessions.items())
            rows = "".join(
                f'<li><a href="/s/{k}/">{k}</a> · {v.display} '
                f'{"（就绪）" if v.started else "（未启动）"}</li>' for k, v in items)
            if BACKEND == "darwin":
                howto = ("<p style='opacity:.65'>本机是 macOS：没有可多开的 headless 显示，"
                         "darwin 后端抓的是<strong>真实桌面</strong> —— 所有会话看到的是"
                         "同一块屏。<br>"
                         "输入注入：" + ("<strong>已开启</strong>（DSH_VIEW_INPUT=1）"
                                        "，页面上的点击/按键会落在真实桌面上。"
                                        if MAC_INPUT else "关闭（只读观看；要开设 DSH_VIEW_INPUT=1）")
                         + "<br><em>darwin 后端尚未在真机验证过，欢迎回报结果。</em></p>")
                rows = rows + howto
            elif BACKEND == "win32":
                howto = ("<p style='opacity:.65'>本机是 Windows：没有 Xvfb 这类可多开的 "
                         "headless 显示，win32 后端抓的是<strong>真实桌面</strong> —— "
                         "所有会话看到的是同一块屏。<br>"
                         "输入注入：" + ("<strong>已开启</strong>（DSH_VIEW_INPUT=1）"
                                        "，页面上的点击/按键会落在真实桌面上。"
                                        if WIN_INPUT else
                                        "默认<strong>关闭</strong>（只读观看），"
                                        "要开就带 <code>DSH_VIEW_INPUT=1</code> 重启本服务。")
                         + "</p>")
                title = "DSH 显示器（Windows · 真实桌面）"
            else:
                howto = ("<p style='opacity:.65'>每个 harness 会话有自己的显示："
                         "<code>/s/&lt;sessionId&gt;/</code> —— 互不可见、互不污染。<br>"
                         "在该会话显示上跑程序："
                         "<code>DISPLAY=:&lt;号&gt; QT_QPA_PLATFORM=xcb 程序</code></p>")
                title = "DSH 测试显示器（每会话独立）"
            self._send(
                ("<!doctype html><meta charset=utf-8><title>DSH 显示器</title>"
                 "<body style='background:#0b0b0c;color:#ddd;font:13px system-ui;padding:16px'>"
                 f"<h3>{title}</h3>"
                 f"<ul>{rows or '<li>（暂无会话）</li>'}</ul>"
                 f"{howto}").encode(),
                "text/html; charset=utf-8")
            return

        sess = session(sid)
        rest = rest.rstrip("/")
        if rest == "/stream":
            sess.ensure()
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
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
        if rest == "/snapshot":
            # 单帧 JPEG：页面用 canvas 主动拉帧（MJPEG 放进 <img> 一旦断开不会自愈，
            # 实测表现为"viewer 重启后画面永远黑着"）。
            sess.ensure()
            for _ in range(20):
                with sess.lock:
                    frame = sess.latest
                if frame:
                    self._send(frame, "image/jpeg")
                    return
                time.sleep(0.25)
            self.send_error(503, "no frame yet")
            return
        if rest == "/state":
            # 供页面判断"这台显示上有没有程序在跑" —— 全黑时到底是空闲还是坏了，
            # 用户一眼就能分清（反复被"一片漆黑"困惑过）。
            sess.ensure()
            count = -1
            if BACKEND == "win32":
                count = _win_windows()               # 真实桌面上永远有窗口，不会误报"空闲"
            elif BACKEND != "wayland":
                try:
                    out = subprocess.run(["xdotool", "search", "--name", "."],
                                         env=sess.env, capture_output=True, timeout=8)
                    count = len([ln for ln in out.stdout.decode().splitlines() if ln.strip()])
                except Exception:                    # noqa: BLE001
                    count = -1
            self._send(json.dumps({"session": sid, "display": sess.display,
                                   "windows": count, "idle": count == 0,
                                   "backend": BACKEND, "port": PORT,
                                   "missing": missing_tools()}).encode(),
                       "application/json")
            return
        if rest == "/display":
            # 便于脚本查询"这个会话的显示号是多少"。
            # ⚠️ 这里也要 ensure()：否则调用方拿到号就去启动程序，而显示还没被拉起来，
            # X 客户端会直接连不上（Xvfb 是按需创建的）。
            sess.ensure()
            self._send(json.dumps({"session": sid, "display": sess.display,
                                   "backend": BACKEND, "size": f"{W}x{H}"}).encode(),
                       "application/json")
            return
        sess.ensure()
        if BACKEND == "win32":
            disp = "真实桌面（所有会话共用）"
            note = ("点击画面即可操作（会注入到本机真实的鼠标键盘）" if WIN_INPUT
                    else "只读观看 —— 未开启输入注入（DSH_VIEW_INPUT=1 可开）")
        else:
            disp = f"独立显示 {sess.display}"
            note = "点击画面即可操作（鼠标/键盘都会注入回去）"
        from urllib.parse import parse_qs, urlparse

        k = parse_qs(urlparse(self.path).query).get("k", [""])[0]
        self._send(PAGE.format(base=f"/s/{sid}", sid=sid, disp=disp,
                               w=W, h=H, note=note, k=k).encode(),
                   "text/html; charset=utf-8")


def _redirect_log() -> None:
    """把输出落到文件（``DSH_VIEW_LOG``）。

    无控制台启动时用得上：Windows 上便携包用 ``pythonw.exe`` 起服务（不留黑框），
    那样 stdout 是空设备，启动失败就什么线索都没有。systemd 那边本来就是 journal，
    不受影响。打开失败就算了 —— 日志绝不该拦住服务本身。
    """
    path = os.environ.get("DSH_VIEW_LOG")
    if not path:
        return
    try:
        stream = open(path, "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = stream
    except Exception:                                # noqa: BLE001
        pass


def _bind(preferred: int, tries: int = 12) -> "ThreadingHTTPServer":
    """从 preferred 开始找一个能绑的端口，并把最终端口写进 ``<HOME_DIR>/port``。

    为什么：同一台机器上可能有多个实例（多个用户、或手工起了两次）。原来的行为是
    端口被占就**直接崩** —— 用户只会看到"面板连不上"，完全看不出原因。
    现在自动往后找，插件侧则在 8099..8111 范围内探测第一个应答的服务。
    """
    last: Exception | None = None
    for port in range(preferred, preferred + tries):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        except OSError as exc:                       # 端口被占：换下一个
            last = exc
            continue
        global PORT
        PORT = port
        try:
            os.makedirs(HOME_DIR, exist_ok=True)
            with open(os.path.join(HOME_DIR, "port"), "w", encoding="utf-8") as fh:
                fh.write(str(port))
        except OSError:
            pass
        if port != preferred:
            print(f"  （{preferred} 被占用，改用 {port}）", flush=True)
        return srv
    raise SystemExit(f"{preferred}..{preferred + tries - 1} 全部被占用：{last}")


def main() -> None:
    import atexit

    _redirect_log()
    _install_signal_handlers()
    atexit.register(_cleanup_spawned)
    if BACKEND == "darwin":
        print(f"viewer on http://127.0.0.1:{PORT}/ · 后端 darwin（真实桌面，实验性）· "
              f"输入注入{'已开启' if MAC_INPUT else '关闭（只读）'}", flush=True)
    elif BACKEND == "win32":
        print(f"viewer on http://127.0.0.1:{PORT}/ · 后端 win32（真实桌面 {W}x{H}，"
              f"DPI {_DPI_MODE}）· 所有会话共用这块屏 · "
              f"输入注入{'已开启' if WIN_INPUT else '关闭（只读）'}", flush=True)
    else:
        print(f"viewer on http://127.0.0.1:{PORT}/ · 后端 {BACKEND} · 每会话独立 "
              f"（/s/<sessionId>/）· {W}x{H} · 支持双向注入", flush=True)
    print(f"访问令牌：{os.path.join(HOME_DIR, 'token')}（600；本机其它用户读不到）", flush=True)
    miss = missing_tools()
    if miss:
        print("⚠ 缺少依赖，部分功能不可用：", flush=True)
        for m in miss:
            print(f"    {m['tool']} —— {m['why']}（安装：{m['package']}）", flush=True)
    _bind(PORT, tries=12).serve_forever()


if __name__ == "__main__":
    main()
