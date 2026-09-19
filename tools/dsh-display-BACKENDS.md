# 测试显示器的底座选型（实测结论）

给 DSH Console 做 GUI 验证时，需要一台**独立于用户桌面**的显示，并且要能
"看到画面 + 把点击/键盘注入回去"。下面是三种底座的实测结论。

## 1. Wayland（sway）单台 —— 可用，但注入前提苛刻

**可用**：画面抓取用 `grim`，键盘用 `wtype`（走合成器 virtual-keyboard 协议，无需权限）。
**指针注入**需要三件事同时满足，缺任何一个 `seat capabilities` 就是 0，**任何**指针注入
都无处投递（客户端连指针对象都不会创建）：

1. `WLR_BACKENDS=headless,libinput`（纯 headless 不含 libinput，一个设备都没有）；
2. `LIBSEAT_BACKEND=seatd`（logind 的会话被用户自己的桌面占用）；
3. 合成器进程带 `seat` 组（组变更要重新登录，故用 `-g seat` 启动）；
   另外 `ydotoold` 必须先起，合成器才枚举得到虚拟设备。

满足后实测：`seat0 capabilities = 7`，靶子窗口收到鼠标事件 ✓，
`zwlr_virtual_pointer_v1` 协议注入的落点是**恒等映射**（期望 (160,100) → 实际 (160,100)）。

参考：`dsh-display-reset.sh`（按 pidfile 精确杀进程，**别用 `pkill -f`** ——
那个模式会匹配到脚本自己的命令行，把执行者一起杀掉）。

## 2. Wayland 多台（每会话一台）—— **不可行**

`seatd` 的 seat 是 **VT-bound**，同一时刻只允许一个客户端：

```
seatd: seat is VT-bound and has an active client → Device or resource busy
```

logind 的会话又被用户桌面占着 → **多合成器方案作废**。

## 3. Xvfb + X11（每会话一台）—— ✅ 推荐

X11 **没有 seat 概念**，可以同时跑任意多个 display；输入用 `xdotool` 走 XTest，
**不需要权限、不占 seat、不碰 uinput**。

实测（2026-09-19）：

```
① Xvfb + QT_QPA_PLATFORM=xcb → 正常渲染（截图 18.4 KB）
② xdotool 点击 → 画面变化 ✓
④ :73 与 :74 两个显示的画面不同 → 完全独立 ✓
```

**两个关键坑**：

* 跑在 Xvfb 上的 Qt 程序必须显式 `QT_QPA_PLATFORM=xcb` —— 否则环境里残留的
  `WAYLAND_DISPLAY` 会让 Qt 去加载 wayland 插件并失败（实测踩过）；
* `Xvfb` 报 `Cannot establish any listening sockets` 时，先确认**检查手段本身**没错
  （`xdpyinfo` 可能没装）—— 实测 Xvfb 一直是好的，是检查方式骗了人。

代价：里面的程序跑在 X11/XWayland 下，不覆盖"原生 Wayland 渲染"。
