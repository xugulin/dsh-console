#!/usr/bin/env bash
#
# 解析本项目该用哪个 Python 解释器。
#
# 控制台对解释器有两条硬性要求，缺一不可：
#   1. Python >= 3.14 —— 会话文件是 zstd 压缩的，解压走标准库 compression.zstd
#      （3.14 才进标准库），所以版本不能降级；
#   2. 能 import PySide6 —— 界面框架。它装在 venv 里而不是系统 Python，
#      因为系统 Python 有 PEP 668 的 EXTERNALLY-MANAGED 保护，装不进去。
#
# 查找顺序（先命中先赢，每个候选都必须通过上面的检查）：
#   1. $DSH_CONSOLE_PYTHON                 显式覆盖；指定了就只认它
#   2. $DSH_CONSOLE_VENV/bin/python        可选的标准运行时（设了才查）
#   3. <项目根>/.venv/bin/python           项目自带 venv
#   4. PATH 上的 python3.14 / python3      系统解释器（通常缺 PySide6）
#
# 第 2 档**没有默认值**：源码里不写死任何绝对路径——写死了别人 clone 下来会看到
# 一条自己机器上不存在的路径，错误提示还照着它教人修（而且泄露开发机的用户名）。
#
# 用法：
#   source "$(dirname "${BASH_SOURCE[0]}")/find-python.sh"
#   PY="$(dsh_console_python "$ROOT")" || exit 1
#   exec "$PY" "$ROOT/main.py" "$@"
#
# 函数把解释器路径打到 stdout；失败时把原因打到 stderr 并返回 1。

#: 可选的标准运行时目录，由 DSH_CONSOLE_VENV 指定；没设就只查项目 .venv 和 PATH。
DSH_CONSOLE_DEFAULT_VENV="${DSH_CONSOLE_VENV:-}"

#: 解释器版本下限。与 main.py 的 MIN_PYTHON、requirements.txt、README 保持一致。
DSH_CONSOLE_MIN_PY="3.14"
DSH_CONSOLE_MIN_PY_TUPLE="3, 14"

# 探针：用 Python 自己比版本，避免在 shell 里做版本号字符串排序。
#   退出码 2 = 版本太低；3 = 缺 PySide6；1/其它 = 解释器本身跑不起来。
_DSH_CONSOLE_PROBE_GUI="
import sys
if sys.version_info < ($DSH_CONSOLE_MIN_PY_TUPLE):
    raise SystemExit(2)
try:
    import PySide6  # noqa: F401
except Exception:
    raise SystemExit(3)
"
_DSH_CONSOLE_PROBE_VER="
import sys
if sys.version_info < ($DSH_CONSOLE_MIN_PY_TUPLE):
    raise SystemExit(2)
"

# dsh_console_probe <解释器路径> [gui|nogui] → 0 可用 / 2 版本低 / 3 缺 PySide6 / 1 不可执行
dsh_console_probe() {
  local exe="${1:-}" mode="${2:-gui}"
  if [ -z "$exe" ] || [ ! -x "$exe" ]; then
    return 1
  fi
  if [ "$mode" = "nogui" ]; then
    "$exe" -c "$_DSH_CONSOLE_PROBE_VER" >/dev/null 2>&1
  else
    "$exe" -c "$_DSH_CONSOLE_PROBE_GUI" >/dev/null 2>&1
  fi
}

# dsh_console_python <项目根> [gui|nogui] → 打印解释器路径
dsh_console_python() {
  local root="${1:-$(pwd)}" mode="${2:-gui}"
  local -a candidates=() rejected=()
  local exe rc name

  if [ -n "${DSH_CONSOLE_PYTHON:-}" ]; then
    # 显式指定就不做兜底：用户说用哪个就用哪个。选了不能用的要立刻报错，
    # 而不是悄悄换一个能用的——那样最难排查。
    candidates=("$DSH_CONSOLE_PYTHON")
  else
    if [ -n "$DSH_CONSOLE_DEFAULT_VENV" ]; then
      candidates+=("$DSH_CONSOLE_DEFAULT_VENV/bin/python")
    fi
    candidates+=("$root/.venv/bin/python")
    for name in python3.14 python3; do
      exe="$(command -v "$name" 2>/dev/null || true)"
      if [ -n "$exe" ]; then
        candidates+=("$exe")
      fi
    done
  fi

  for exe in "${candidates[@]}"; do
    rc=0
    dsh_console_probe "$exe" "$mode" || rc=$?
    case "$rc" in
      0) printf '%s\n' "$exe"; return 0 ;;
      2) rejected+=("$exe — Python 版本低于 $DSH_CONSOLE_MIN_PY，没有 compression.zstd") ;;
      3) rejected+=("$exe — 没装 PySide6") ;;
      *) rejected+=("$exe — 无法执行") ;;
    esac
  done

  {
    echo "找不到可用的 Python 解释器：控制台需要 Python >= $DSH_CONSOLE_MIN_PY 且已装 PySide6。"
    if [ "${#rejected[@]}" -gt 0 ]; then
      echo
      echo "已检查："
      printf '  ✗ %s\n' "${rejected[@]}"
    fi
    echo
    echo "补齐依赖（PySide6-Essentials 约 76 MB）："
    if [ -n "$DSH_CONSOLE_DEFAULT_VENV" ]; then
      echo "  \"$DSH_CONSOLE_DEFAULT_VENV/bin/python\" -m pip install -r \"$root/requirements.txt\""
    else
      echo "  \"$root/.venv/bin/python\" -m pip install -r \"$root/requirements.txt\""
      echo "  （还没有 venv 就先建一个：python3.14 -m venv \"$root/.venv\"）"
    fi
    echo
    echo "或者换一个已经装好的解释器："
    echo "  DSH_CONSOLE_PYTHON=/path/to/python \"$root/run.sh\""
    echo "  DSH_CONSOLE_VENV=/path/to/venv    \"$root/run.sh\"    # 指定标准运行时"
  } >&2
  return 1
}
