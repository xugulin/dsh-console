#!/usr/bin/env bash
#
# 启动 DSH 控制台。
#
# 解释器交给 tools/find-python.sh 统一解析：优先项目自带的 .venv，也可以用
# DSH_CONSOLE_VENV 指定标准运行时、DSH_CONSOLE_PYTHON 直接指定解释器。
# 控制台必须跑在 3.14 上——会话文件用标准库 compression.zstd 解压——所以这里不做
# "随便抓个 python3 试试"的兜底：找不到合格的解释器就明确报错，并打印修复命令。
#
# 换解释器：DSH_CONSOLE_PYTHON=/path/to/python ./run.sh
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=tools/find-python.sh
source "$HERE/tools/find-python.sh"

# --list-themes / --self-test 不建窗口，用不上 PySide6，只校验版本即可。
MODE="gui"
for arg in "$@"; do
  case "$arg" in
    --list-themes | --self-test) MODE="nogui" ;;
  esac
done

PY="$(dsh_console_python "$HERE" "$MODE")" || exit 1

exec "$PY" "$HERE/main.py" "$@"
