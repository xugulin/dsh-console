"""按屏幕分辨率适配窗口尺寸。

单独一个模块、**不放在 ui/ 下**：GUI 外壳（`gui.py`）、内置浏览器（`browser.py`）、
原生客户端（`gui_native.py`）都要用它，而那些模块不该为了一个尺寸计算把整个
`dsh_console.ui` 包（含所有页面）拖进来。

逻辑是**纯函数**（只吃尺寸、只吐尺寸），因为"小屏上会不会被切掉"全由它决定，
而真去改显示器分辨率来验证不现实——纯函数就能把 3840×2160 到 800×600 一整套跑一遍。
"""

from __future__ import annotations

# --------------------------------------------------------------------- 屏幕适配
#
# 控制台原来写死 `resize(1180, 800)` + `setMinimumSize(940, 640)`。在 1366×768 这种
# 屏幕上，窗口比屏幕还大——**右下角直接被切掉，按钮点不到**。
#
# 这里的策略是两条一起做，缺一不可：
#   1. **初始尺寸按屏幕算**：拿"想要的尺寸"和"屏幕可用区域的 92%"取小；
#   2. **最小尺寸也跟着降**：只改初始尺寸没用——Qt 不允许窗口缩到内容的
#      minimumSizeHint 以下，写死的最小尺寸会把窗口撑在屏幕外面。
# 屏幕取不到时一律退回原来的值，绝不因为"量不到屏幕"把窗口弄成 0×0。

#: 初始尺寸占屏幕可用区域的比例（留出任务栏/边框的余地）。
SCREEN_FIT = 0.92
#: 最小尺寸占屏幕的比例——比初始值更宽松，保证还能再手动缩小。
SCREEN_MIN_FIT = 0.62


def fit_to_screen(want: tuple[int, int], floor: tuple[int, int]) -> tuple[int, int, int, int]:
    """按当前屏幕算出 ``(宽, 高, 最小宽, 最小高)``。

    做成**纯函数**（只吃尺寸、只吐尺寸）是为了能直接测：真去改显示器分辨率不现实，
    而这个逻辑恰恰是"小屏上会不会被切掉"的唯一决定因素。
    """
    try:
        from PySide6.QtGui import QGuiApplication

        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return want[0], want[1], floor[0], floor[1]
        avail = screen.availableGeometry()
        aw, ah = avail.width(), avail.height()
        if aw <= 0 or ah <= 0:
            return want[0], want[1], floor[0], floor[1]
    except Exception:  # noqa: BLE001 - 取不到屏幕就按原样来
        return want[0], want[1], floor[0], floor[1]

    width = min(want[0], int(aw * SCREEN_FIT))
    height = min(want[1], int(ah * SCREEN_FIT))
    # 最小尺寸：既不能超过"想要的尺寸"，也不能超过屏幕
    min_w = min(floor[0], int(aw * SCREEN_MIN_FIT), width)
    min_h = min(floor[1], int(ah * SCREEN_MIN_FIT), height)
    return max(480, width), max(360, height), max(420, min_w), max(300, min_h)


def apply_screen_fit(widget, want: tuple[int, int], floor: tuple[int, int]) -> None:
    """把 :func:`fit_to_screen` 的结果应用到窗口，并居中。"""
    width, height, min_w, min_h = fit_to_screen(want, floor)
    widget.setMinimumSize(min_w, min_h)
    widget.resize(width, height)
    try:
        from PySide6.QtGui import QGuiApplication

        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            widget.move(
                avail.x() + max(0, (avail.width() - width) // 2),
                avail.y() + max(0, (avail.height() - height) // 2),
            )
    except Exception:  # noqa: BLE001
        pass

def prefer_xwayland(env_var: str, *, default: str = "xcb") -> str:
    """在 Wayland 会话里**默认退回 XWayland**，返回最终生效的平台名。

    为什么（本机 headless Wayland 实测，Qt 自己报的错）：

        qt.qpa.wayland: Failed to create grabbing popup. Ensure popup ...
        has a transientParent set and that parent window has received input.

    Wayland 规定：客户端**只有在窗口"收到过输入"之后**才能创建 grabbing popup。
    窗口刚 show() 出来、合成器还没把输入给它时，**用户第一次点击会被合成器吃掉
    用于激活窗口**，不会递到控件——于是"菜单点了没反应，要先失去一次焦点"。
    实测 ``popup()`` 与 ``exec()`` 在该状态下**都失败**（两条路各测一次，
    ``isVisible()`` 均为 False），所以只能从平台层面绕开：XWayland 没有这条限制。

    尊重用户设置：``env_var`` 有值就用它（例如 ``DSH_BROWSER_QPA=wayland`` 切回原生）。
    """
    import os

    override = (os.environ.get(env_var) or "").strip()
    if override:
        os.environ["QT_QPA_PLATFORM"] = override
        return override
    qpa = (os.environ.get("QT_QPA_PLATFORM") or "").strip()
    on_wayland = bool(os.environ.get("WAYLAND_DISPLAY")) or "wayland" in qpa
    # 没有 XWayland（DISPLAY 为空）时不能切，否则 Qt 起不来
    if not (on_wayland and os.environ.get("DISPLAY")):
        return qpa or ""
    # ⚠️ 不要**替换**成单个 "xcb"：那样会把 Qt 的平台回退链拆掉——万一 DISPLAY 指向
    # 一个已失效的 XWayland（换过会话、Xwayland 崩过都很常见），Qt 会**直接起不来**，
    # 而不是退回 Wayland。Qt 的多值写法是"按顺序尝试"，所以写成 "xcb;wayland"：
    # 有 XWayland 就用它（菜单能弹），没有就自动退回原生 Wayland（至少能启动）。
    parts = [p.strip() for p in qpa.split(";") if p.strip()]
    chain = [default] + [p for p in parts if p != default]
    if not chain[1:]:
        chain.append("wayland")
    os.environ["QT_QPA_PLATFORM"] = ";".join(chain)
    return os.environ["QT_QPA_PLATFORM"]
