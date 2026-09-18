#!/bin/sh
# 启动/修复 DSH 测试显示器（headless Wayland + MJPEG viewer，支持双向注入）。
#
# ⚠️ 关键：合成器必须同时满足三点，否则**指针注入无处投递**（seat capabilities 会是 0）：
#   1. 多后端：WLR_BACKENDS=headless,libinput —— 纯 headless 不启动 libinput，
#      那样一个输入设备都没有，seat 就没有 pointer 能力（实测 capabilities=0）；
#   2. 会话后端：LIBSEAT_BACKEND=seatd —— logind 的会话已被用户自己的桌面占用，
#      必须用 seatd（systemctl enable --now seatd），进程需要 seat 组；
#   3. 组：以 `-g seat` 启动（组变更要重新登录，所以显式带上）。
#   另外内核里要有虚拟输入设备：先起 ydotoold。
ISO="${DSH_DISPLAY_HOME:-$HOME/.cache/dsh-display}"
mkdir -p "$ISO/run" && chmod 700 "$ISO/run"

# 1) 虚拟输入设备（ydotoold 要在合成器之前起来，合成器才枚举得到）
if ! pgrep -x ydotoold >/dev/null 2>&1; then
  sudo -n sh -c "setsid ydotoold --socket-path /tmp/.ydotool_socket \
    --socket-own $(id -u):$(id -g) --socket-perm 0660 >/tmp/.ydotoold.log 2>&1 &" 2>/dev/null
  sleep 2
fi

# 2) 合成器
if ! ls "$ISO/run"/wayland-1 >/dev/null 2>&1; then
  sudo -n -u "$(id -un)" -g seat sh -c "setsid env XDG_RUNTIME_DIR=$ISO/run \
    LIBSEAT_BACKEND=seatd WLR_BACKENDS=headless,libinput \
    WLR_RENDERER_ALLOW_SOFTWARE=1 LIBGL_ALWAYS_SOFTWARE=1 \
    sway -c $ISO/sway.conf >$ISO/sway.log 2>&1 &" 2>/dev/null \
  || setsid env XDG_RUNTIME_DIR="$ISO/run" WLR_BACKENDS=headless \
       WLR_RENDERER_ALLOW_SOFTWARE=1 LIBGL_ALWAYS_SOFTWARE=1 \
       sway -c "$ISO/sway.conf" >"$ISO/sway.log" 2>&1 &
  sleep 6
fi

# 3) viewer
if ! ss -ltn 2>/dev/null | grep -q ':8099'; then
  setsid env DSH_VIEW_PORT=8099 nohup python3 "$(dirname "$0")/dsh-display-viewer.py" \
    >"$ISO/viewer.log" 2>&1 &
  sleep 3
fi

CAP=$(SWAYSOCK=$(ls "$ISO/run"/sway-ipc.* 2>/dev/null | head -1) XDG_RUNTIME_DIR="$ISO/run" \
      swaymsg -t get_seats 2>/dev/null | grep -m1 capabilities | grep -oE '[0-9]+')
echo "显示器: socket=$(ls "$ISO/run" | grep -c '^wayland-1$') viewer=$(ss -ltn 2>/dev/null | grep -c ':8099') seat能力=${CAP:-?}（7=指针+键盘+触摸齐全） 地址 http://127.0.0.1:8099/"
