#!/bin/sh
# 启动/修复 DSH 测试显示器（headless Wayland + MJPEG viewer）。
# 用途：DSH Web UI 的「显示器」标签就是看这里；GUI 相关测试也跑在这个显示上。
# 独立 runtime dir，不影响桌面会话。
ISO="$(cd "$(dirname "$0")" && pwd)"
export XDG_RUNTIME_DIR="$ISO/run"
mkdir -p "$ISO/run" && chmod 700 "$ISO/run"
if ! ls "$ISO/run"/wayland-1 >/dev/null 2>&1; then
  setsid env XDG_RUNTIME_DIR="$ISO/run" WLR_BACKENDS=headless WLR_LIBINPUT_NO_DEVICES=1 \
    WLR_RENDERER_ALLOW_SOFTWARE=1 LIBGL_ALWAYS_SOFTWARE=1 \
    sway -c "$ISO/sway.conf" >"$ISO/sway.log" 2>&1 &
  sleep 6
fi
if ! ss -ltn 2>/dev/null | grep -q ':8099'; then
  setsid env DSH_VIEW_PORT=8099 nohup python3 /tmp/dshview/view.py >"$ISO/view.log" 2>&1 &
  sleep 3
fi
echo "显示器: $(ls "$ISO/run" | grep -c '^wayland-1$') 个 socket · viewer: $(ss -ltn 2>/dev/null | grep -c ':8099') · 地址 http://127.0.0.1:8099/"
