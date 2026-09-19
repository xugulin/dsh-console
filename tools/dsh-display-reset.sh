#!/bin/bash
# 安全地重置 DSH 测试显示器。
#
# 为什么需要它：沙箱的合成器依赖 seatd，而 seatd 的 seat 是 **VT-bound**，
# 同一时刻只允许一个客户端。反复重启后常留下"active client"占位，
# 新合成器就会报：
#     seatd: seat is VT-bound and has an active client → Device or resource busy
#     wlr:   Unable to create seat: Broken pipe → failed to add backend 'libinput'
# 这个脚本按 **pidfile** 精确杀进程（绝不用 pkill -f 模糊匹配 —— 那个模式会匹配到
# 脚本自己的命令行，把执行者一起杀掉，我在这上面栽过三次）。
set -u
ISO="${DSH_DISPLAY_HOME:-$HOME/.cache/dsh-display}"
PIDFILE="$ISO/session.pid"

# 1) 精确停掉上一个合成器
if [ -f "$PIDFILE" ]; then
  OLD=$(cat "$PIDFILE" 2>/dev/null || true)
  if [ -n "${OLD:-}" ] && kill -0 "$OLD" 2>/dev/null; then
    kill "$OLD" 2>/dev/null; sleep 2; kill -9 "$OLD" 2>/dev/null; echo "  已停旧合成器 pid=$OLD"
  fi
  rm -f "$PIDFILE"
fi

# 2) 重启 seatd，清掉它的客户端记账（VT-bound 的 seat 需要这一步）
sudo -n systemctl restart seatd 2>/dev/null && echo "  ✓ seatd 已重启"
sleep 2

# 3) 虚拟输入设备必须先就位
if ! pgrep -x ydotoold >/dev/null 2>&1; then
  sudo -n sh -c "setsid ydotoold --socket-path /tmp/.ydotool_socket \
    --socket-own $(id -u):$(id -g) --socket-perm 0660 >/tmp/.ydotoold.log 2>&1 &" 2>/dev/null
  sleep 2
fi
echo "  ✓ ydotoold: $(pgrep -c -x ydotoold 2>/dev/null || echo 0) 个"

# 4) 启动合成器（多后端 + seatd + seat 组），记下 pid
mkdir -p "$ISO/run" && chmod 700 "$ISO/run"
rm -f "$ISO/run/wayland-1" "$ISO/run/wayland-1.lock" "$ISO/run"/sway-ipc.* 2>/dev/null
sudo -n -u "$(id -un)" -g seat sh -c "setsid env XDG_RUNTIME_DIR=$ISO/run \
  LIBSEAT_BACKEND=seatd WLR_BACKENDS=headless,libinput \
  WLR_RENDERER_ALLOW_SOFTWARE=1 LIBGL_ALWAYS_SOFTWARE=1 \
  sway -c $ISO/sway.conf >$ISO/sway.log 2>&1 & echo \$! > $PIDFILE" 2>/dev/null
sleep 9

if [ -S "$ISO/run/wayland-1" ]; then
  CAP=$(SWAYSOCK=$(ls "$ISO/run"/sway-ipc.* 2>/dev/null | head -1) XDG_RUNTIME_DIR="$ISO/run" \
        swaymsg -t get_seats 2>/dev/null | grep -m1 capabilities | grep -oE '[0-9]+')
  echo "  ✓ 合成器就绪，seat 能力=${CAP:-?}（7 = 指针+键盘+触摸，指针注入可用的前提）"
else
  echo "  ✗ 合成器未起来，看 $ISO/sway.log："
  tail -4 "$ISO/sway.log" 2>/dev/null | sed 's/^/    /'
fi
