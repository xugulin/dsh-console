#!/usr/bin/env sh
#
# 「伪 shell」：给那些只认 $SHELL、不提供 exec 参数的终端模拟器用。
#
# 为什么需要它：COSMIC 桌面上只装了 cosmic-term，而它的命令行只有
# ``-w/--working-directory``——**没有任何执行命令的开关**（``--help`` 里就这三个选项）。
# 所有终端都会用 $SHELL 启动会话，所以把 $SHELL 指到这个脚本，就等于让它"启动"
# 我们的 TUI。实测（Xvfb :99 + cosmic-term）确认 cosmic-term 确实读 $SHELL。
#
# 要跑什么由 $DSH_CONSOLE_TUI_CMD 给出，控制台会用 shlex 规则拼好整条命令。
# 这里显式用 /bin/sh -c 而不是 "$@"：某些终端会给 shell 传 -l 之类的参数，
# 直接 exec "$@" 会把这些参数当成命令名。
#
# exec 让 TUI 顶替掉本脚本：TUI 一退出，终端会话就结束，窗口自己关掉。
#
exec /bin/sh -c "${DSH_CONSOLE_TUI_CMD:?DSH_CONSOLE_TUI_CMD 未设置}"
