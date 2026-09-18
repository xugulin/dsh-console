# virtual-pointer —— 走合成器协议的指针注入

## 为什么是它

在 Wayland 下注入鼠标有两条路：

| 路 | 机制 | 问题 |
|---|---|---|
| `ydotool` | `/dev/uinput` 造虚拟设备 | 合成器要能读 `/dev/input/*`（需 `input` 组）；沙箱里 libinput 一个设备都看不到 ✗ |
| **本工具** | 合成器的 `zwlr_virtual_pointer_v1` 协议（`wtype` 的指针版） | **不需要额外权限** ✓ |

## 来源与构建

* 协议 XML 与示例客户端取自 **wlroots 源码**（`protocol/wlr-virtual-pointer-unstable-v1.xml`、
  `examples/virtual-pointer.c`）—— 该协议是 wlroots 自带的，**不在 wayland-protocols 包里**。
* 用 `wayland-scanner` 生成绑定后编译：

```sh
wayland-scanner client-header wlr-virtual-pointer-unstable-v1.xml wlr-virtual-pointer-unstable-v1-client-protocol.h
wayland-scanner private-code  wlr-virtual-pointer-unstable-v1.xml wlr-virtual-pointer-unstable-v1-protocol.c
cc virtual-pointer.c wlr-virtual-pointer-unstable-v1-protocol.c \
   $(pkg-config --cflags --libs wayland-client) -o vptr
```

## 用法

```sh
WAYLAND_DISPLAY=<显示> XDG_RUNTIME_DIR=<运行时目录> ./vptr absolute <x> <y> <宽> <高>
WAYLAND_DISPLAY=<显示> XDG_RUNTIME_DIR=<运行时目录> ./vptr button 272 press    # 272 = 0x110 = BTN_LEFT
WAYLAND_DISPLAY=<显示> XDG_RUNTIME_DIR=<运行时目录> ./vptr button 272 release
WAYLAND_DISPLAY=<显示> XDG_RUNTIME_DIR=<运行时目录> ./vptr axis 0 <值>          # 滚轮
```

## ⚠️ 关键前提：合成器的 seat 必须有指针能力

实测（沙箱里的 headless sway）：

```
swaymsg -t get_seats → { "name": "seat0", "capabilities": 0, "devices": [] }
```

seat 的能力**来自它挂着的输入设备**。一个输入设备都没有时 `capabilities = 0`，
**任何**指针注入（uinput 也好、本协议也好）都无处投递 —— 客户端连指针对象都不会创建。
所以本工具在"有真实输入设备的桌面会话"里可用；在纯 headless 沙箱里，
需要先让合成器能读到一个输入设备（例如把用户加入 `input` 组，再用 `sg input` 启动合成器）。
