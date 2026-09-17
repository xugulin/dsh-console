#!/usr/bin/env bash
#
# 安装桌面快捷方式（COSMIC / 任何 XDG 桌面环境都适用）。
#
# 做四件事：
#   1. 生成应用图标（与深色主题配色一致）
#   2. 用 tools/dsh-console.desktop.in 模板**生成两个**可用的条目：
#        <项目>/dsh-console.desktop                      ← 在项目目录里双击用的
#        ~/.local/share/applications/dsh-console.desktop  ← 启动器/Super 键搜到的
#   3. 刷新桌面数据库
#   4. 校验条目
#
# 说明：COSMIC 没有桌面图标层（不跑 cosmic-desktop），所以"桌面快捷方式"实际是
# 应用启动器条目——Super 键唤出后搜索「DSH 控制台」即可，右键可固定到面板/Dock。
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APPS="$HOME/.local/share/applications"
TARGET="$APPS/dsh-console.desktop"
#: 项目内那份：用户会直接在文件管理器里双击它，所以也必须是真的、能跑的条目
LOCAL="$HERE/dsh-console.desktop"
TEMPLATE="$HERE/tools/dsh-console.desktop.in"

# shellcheck source=tools/find-python.sh
source "$HERE/tools/find-python.sh"

echo "==> 生成图标"
# 图标要真的画出来才叫"生成"，所以这里必须有 PySide6（gui 模式）；找不到就只告警，
# 不让整条安装流程挂掉——用旧图标总比装不上启动器好。
if PY_ICON="$(dsh_console_python "$HERE" gui 2>/dev/null)"; then
  QT_QPA_PLATFORM=offscreen "$PY_ICON" "$HERE/tools/make_icon.py" midnight >/dev/null
  echo "    $HERE/assets/icon.png（用 $PY_ICON）"
else
  echo "    跳过（没有可用的 Python 解释器，沿用 assets/ 里已有的图标）" >&2
fi

echo "==> 生成启动器条目"
mkdir -p "$APPS"
if [ ! -f "$TEMPLATE" ]; then
  echo "找不到模板：$TEMPLATE" >&2
  exit 1
fi
# Exec/Icon 在模板里是 @HERE@ 占位符，这里替换成**本份代码**的实际路径。
# 直接 install 模板是不行的：模板带着占位符，启动器根本跑不起来。
# 项目内那份要**可执行**：文件管理器只把可执行的 .desktop 当程序，
# 否则双击会拿文本编辑器打开它（这正是"双击没反应"的常见原因之一）。
sed "s|@HERE@|$HERE|g" "$TEMPLATE" > "$LOCAL"
chmod 755 "$LOCAL"
echo "    $LOCAL（项目内可直接双击）"
sed "s|@HERE@|$HERE|g" "$TEMPLATE" > "$TARGET"
chmod 644 "$TARGET"
echo "    $TARGET"
echo "    Exec=$(grep -m1 '^Exec=' "$TARGET" | cut -d= -f2-)"

# 落点必须真的存在且可执行，否则双击只会"什么都没发生"
EXEC_PATH="$(grep -m1 '^Exec=' "$TARGET" | cut -d= -f2-)"
if [ -x "$EXEC_PATH" ]; then
  echo "==> 落点检查通过（$EXEC_PATH）"
else
  echo "==> ⚠️ 落点不可执行：$EXEC_PATH" >&2
fi

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$APPS" 2>/dev/null || true
  echo "==> 已刷新桌面数据库"
fi

if command -v desktop-file-validate >/dev/null 2>&1; then
  ok=1
  for f in "$LOCAL" "$TARGET"; do
    desktop-file-validate "$f" || ok=0
  done
  if [ "$ok" = 1 ]; then
    echo "==> 两个条目都校验通过"
  else
    echo "==> 校验有告警（见上）" >&2
  fi
fi

echo
echo "完成。两种用法都行："
echo "  · 在文件管理器里双击 $LOCAL"
echo "  · Super 键唤出启动器，搜索「DSH 控制台」"
