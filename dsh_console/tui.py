"""DSH 控制台 TUI —— 在终端里跟 harness 对话。

桌面版（``main.py`` + PySide6）要有图形界面；这个模块是它的终端同胞：用 ``curses``
画一个最小但够用的聊天界面。协议层一行不重写，全部复用 :mod:`dsh_console.acp_client`
（也就是 ``dsh --profile acp`` 那条 ACP v1 通道）。

启动::

    python -m dsh_console.tui
    python -m dsh_console.tui --cwd ~/some/project
    python -m dsh_console.tui --help

界面按 web 版的分栏来排：**左侧主栏**（自上而下是表头 / 对话区 / 输入行 / 状态栏）
+ **右侧整高的说明面板**。web 版是「左栏工作区 · 中间对话 · 右侧栏」，终端里没有可点的
工作区列表，于是把右侧栏做成常驻的操作说明——它一直显示，不用去翻帮助。用户消息也照
web 的样子**靠右**排。

::

    ┌────────────────────────────────────────────┬──────────────────┐
    │ DSH 对话 · 项目名        模型 · 会话 · 用量 │ 发送与退出        │
    │                       我发的消息（靠右）    │ Enter  发送…      │
    │ 模型回的消息（靠左）                       │ Ctrl+C 取消…      │
    │ ▶ bash  跑了个命令                        │ …（一直显示）     │
    ├────────────────────────────────────────────┤                  │
    │ > 在这里打字                                │                  │
    ├────────────────────────────────────────────┤                  │
    │ [就绪] · 状态                快捷键提示    │ ↓ 还有 N 行       │
    └────────────────────────────────────────────┴──────────────────┘

面板里每一条都**真的能用**（改了绑定就得同步改 :data:`HELP_SECTIONS`，否则它会开始骗人）。
鼠标是真接了的：滚轮按指针所在的区决定滚谁，点输入行把光标挪到点中的位置。
代价是终端自己的拖选会被应用接管——多数终端按住 Shift 拖动仍可选中文本（面板里写了）。

按键（完整清单常驻右侧面板，这里列常用的）::

    Enter                  发送（正在跑就先按 Ctrl+C 取消）
    Ctrl+C                 正在跑 → 取消本轮；空闲 → 退出
    Ctrl+D                 退出
    Tab                    切换焦点：对话区 ↔ 说明面板（决定 ↑↓/PgUp/PgDn 滚谁）
    ↑↓ / PgUp/PgDn         滚动当前焦点区
    ←/→/Home/End           移动输入光标（另配 Ctrl+A/Ctrl+E、Ctrl+W、Ctrl+K、Backspace、Delete）
    F1                     显示 / 隐藏右侧面板（终端里 Ctrl+H 与退格同码，绑不了）
    F2                     命令面板（新建/切换 会话·模型·工作区 都在里面）
    Ctrl+N / F6            新建会话
    F3 / F4 / F5           切换会话 / 切换模型 / 切换工作区
    Ctrl+T                 显示 / 隐藏思考过程
    Ctrl+L                 重绘（终端被别的程序弄花时用）

会话操作走**覆盖式选择器**：输入即筛选、↑↓ 选、Enter 确认、Esc 取消，鼠标也能点。
打开时会**预选当前项**——不预选的话，按一下 ↓ 很可能正好停在当前项上，看起来像"按了没反应"。
工作区的那个还兼作路径输入框：输入没匹配到任何现有目录时，列表第一行就是
「用这个路径新建会话」。

切换会话有个坑：ACP 的 ``session/resume`` **不回放旧更新**，恢复完界面是空的。
所以切过去时会顺带从会话日志（``~/.dsh/sessions/...``）把最近的对话重建成暗色历史条目，
不然"切换会话"等于什么也没切。这件事在 :mod:`dsh_console.sessions` 里做。

退出码：``0`` = 会话建立过（正常退出）；``1`` = 会话没建立起来或界面自身异常；
``2`` = 命令行参数/终端环境有问题。

线程模型（这段是重点）：curses 没有事件循环，而 ``AcpClient`` 的所有回调都跑在它的
读取线程里。**回调里绝不能碰 curses**，它们只往 :class:`queue.Queue` 塞事件，主循环
用 ``get_wch()`` 的 timeout 定期醒来消费。界面状态只由主线程改，省掉一整类竞态。
"""

from __future__ import annotations

import argparse
import json
import locale
import os
import queue
import re
import shutil
import sys
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

try:  # 关 ISIG 要用；非 POSIX 平台上没有这个模块，退化成"只靠 KeyboardInterrupt"
    import termios
except ImportError:  # pragma: no cover
    termios = None  # type: ignore[assignment]

try:
    import curses
except ImportError as _exc:  # pragma: no cover - Windows 上才走这里
    # **Windows 版 Python 不自带 curses**（它是 Unix 专有模块）。便携包里已经预装了
    # windows-curses，但如果是源码运行 / 手工装了别的 Python，就得给一句人话，
    # 而不是一句 ModuleNotFoundError 让人去猜。
    raise SystemExit(
        "TUI 需要 curses 模块，当前 Python 里没有。\n"
        "  · Windows：装一下 windows-curses（便携包已内置，无需操作）\n"
        "  · 或者改用图形界面：启动DSH控制台  /  网页界面：启动DSH网页界面\n"
    ) from _exc

try:  # 正常路径：python -m dsh_console.tui
    from .acp_client import PROFILE, AcpClient, _flatten_content
except ImportError:  # 兜底：python dsh_console/tui.py 直接跑
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from dsh_console.acp_client import PROFILE, AcpClient, _flatten_content

# ---------------------------------------------------------------- 常量

#: 主循环轮询间隔（毫秒）。curses 没有事件循环，只能定时醒来看队列。
POLL_MS = 50

#: 转录区最多保留的行数。聊久了全留着重排会越来越慢，超了就从最老的条目开始丢。
MAX_LINES = 4000

#: 工具调用最多附几行输出（要求是"简短输出"，不是把结果全倒出来）。
TOOL_OUTPUT_LINES = 6

#: 制表符按几列展开。宽度计算里 tab 必须有个确定值，否则宽度函数不可测。
TAB_WIDTH = 4

#: 退出时等启动线程收尾的上限（秒）。见 :meth:`TuiApp.shutdown`。
BOOT_JOIN_TIMEOUT = 10.0

#: 一个 ESC 之后多久没等到后续字节就算"用户真的按了 ESC"（秒）。
ESC_SEQUENCE_TIMEOUT = 0.4

#: 输入行提示符。故意用纯 ASCII：``›`` 这类字符的东亚宽度是"模糊(A)"，
#: 中文终端里可能按 2 列渲染，光标就会跟字符错位。
PROMPT = "> "

STATE_BOOT = "boot"
STATE_READY = "ready"
STATE_FAILED = "failed"
STATE_EXITED = "exited"

#: 颜色对编号（0 是 curses 的默认对，不能占用）。
PAIR_USER = 1
PAIR_THOUGHT = 2
PAIR_TOOL_RUN = 3
PAIR_TOOL_OK = 4
PAIR_TOOL_FAIL = 5
PAIR_ERROR = 6
PAIR_INFO = 7
PAIR_DIM = 8
PAIR_STATUS = 9
PAIR_PROMPT = 10
PAIR_HELP_TITLE = 11
PAIR_HELP_KEY = 12
PAIR_HEADER = 13
PAIR_FOCUS = 14

# ---------------------------------------------------------------- 版面
#: 右侧说明面板的目标宽度（列）。它和 web 版的右侧栏是同一个位置。
HELP_WIDTH = 42

#: 左侧对话区至少要留这么宽；不够就先压缩面板，再窄就整块藏起来。
HELP_MIN_MAIN = 44
#: 窄到这个宽度以下就**不显示**说明面板。
#:
#: 取 86 = 面板设计宽度 42 + 对话区最低 44。为什么不是更小的值：面板在 80 列终端上
#: 只能分到 36 列，说明文字会被折成碎片（实测出现 `重绘整屏（画面花了时用` 换行成
#: `）` 这种），滚动提示也被截断——**"压缩到看不清"比"少看一边"更糟**。
#: 藏起来之后对话区拿到整宽，两边的可读性反而都更好。
HELP_HIDE_BELOW = 86

#: 焦点可以在两个区之间切换（Tab）。焦点决定 ↑↓/PgUp/PgDn 滚谁。
FOCUS_MAIN = "main"
FOCUS_HELP = "help"

#: 右侧面板的内容。「详细说明」不是客套话——这里列的每一条都真的能用，
#: 改了按键绑定就得同步改这里，否则面板会开始骗人。
HELP_SECTIONS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    ("发送与退出", (
        ("Enter", "发送"),
        ("Ctrl+C", "跑着→取消；空闲→退出"),
        ("Ctrl+D", "退出（会收干净 harness）"),
        ("Ctrl+L", "重绘整屏（画面花了时用）"),
    )),
    ("输入行编辑", (
        ("← →", "左右移动光标"),
        ("Home / End", "行首 / 行尾（同 ^A / ^E）"),
        ("Backspace", "删光标前一个字符"),
        ("Delete", "删光标所在字符"),
        ("Ctrl+W", "删光标前一个词"),
        ("Ctrl+K", "删到行尾"),
        ("Ctrl+U", "清空整行"),
    )),
    ("滚动与焦点", (
        ("Tab", "焦点：对话区 ↔ 说明面板"),
        ("↑ ↓", "焦点区上下滚一行"),
        ("PgUp / PgDn", "焦点区翻一页"),
        ("滚轮", "指针在哪个区就滚哪个区"),
        ("↓ 一直按", "回到最新内容"),
    )),
    ("鼠标", (
        ("滚轮", "指针在哪个区滚哪个区"),
        ("点输入行", "光标跳到点中的位置"),
        ("点某个区", "焦点切到那个区"),
        ("选中复制", "Shift 拖动选中文本"),
    )),
    ("会话 · 模型 · 工作区", (
        ("F2", "命令面板（下面都在里面）"),
        ("Ctrl+N/F6", "新建会话"),
        ("F3", "切换会话（带标题和时间）"),
        ("F4", "切换模型 / 推理档位"),
        ("F5", "切换工作区（可敲路径）"),
    )),
    ("显示 / 其它", (
        ("F1", "显示 / 隐藏这个面板"),
        ("Ctrl+T", "显示 / 隐藏思考过程"),
        ("↑回滚 N 行", "状态栏提示：在看历史"),
    )),
)

#: 折行时把段落切成「非空白串」和「空白串」两类 token：英文按词断，中文没有空格
#: 就会变成一个长 token，再由 :func:`_split_by_width` 按显示宽度硬切。
_TOKEN_RE = re.compile(r"\S+|\s+")


# ---------------------------------------------------------------- 显示宽度
# 这一节是整个 TUI 的地基：curses 按**列**定位，而 len() 数的是码点个数。
# 中文一个字 2 列、组合字符 0 列，用 len() 折行必然串行/溢出。


def char_width(ch: str) -> int:
    """单个字符占几列：W/F 宽字符算 2，组合/零宽/控制字符算 0，其余算 1。"""
    if not ch:
        return 0
    cat = unicodedata.category(ch)
    if cat in ("Mn", "Me", "Cf", "Cc"):
        # Mn/Me：组合记号（é 上面那个音符）跟在基字符后面；Cf：零宽连接符/变体选择符；
        # Cc：控制字符。它们都不推进光标。
        return 0
    if unicodedata.combining(ch):
        return 0
    if unicodedata.east_asian_width(ch) in ("W", "F"):
        return 2  # 汉字、假名、全角标点
    return 1  # 含"模糊(A)"宽度：按 1 列算，与常见 Linux 终端默认行为一致


def display_width(text: str) -> int:
    """字符串在终端里占多少列。"""
    return sum(char_width(c) for c in text.expandtabs(TAB_WIDTH))


def _split_by_width(token: str, width: int) -> list[str]:
    """把一个超宽 token 硬切成每段不超过 ``width`` 列。

    宽度为 0 的字符（组合记号）永远跟着前一个字符走，否则 ``e`` + 音符会被拆到两行去。
    """
    pieces: list[str] = []
    cur: list[str] = []
    cur_w = 0
    for ch in token:
        cw = char_width(ch)
        if cw and cur_w + cw > width and cur:
            pieces.append("".join(cur))
            cur, cur_w = [], 0
        cur.append(ch)
        cur_w += cw
    pieces.append("".join(cur))
    return pieces


def wrap_display(text: str, width: int) -> list[str]:
    """按**显示宽度**折行，并保留原有的换行。

    英文优先在空格处断行（读起来正常），中文整段没有空格，于是退化成按宽度硬切。
    """
    width = max(1, int(width))
    lines: list[str] = []
    for para in text.expandtabs(TAB_WIDTH).split("\n"):
        if not para:
            lines.append("")
            continue
        cur = ""
        cur_w = 0
        wrapped = False  # 这段是否已经折过行（决定行首空白要不要吞掉）
        for token in _TOKEN_RE.findall(para):
            if token.isspace() and cur_w == 0 and wrapped:
                continue  # 折行处的空白丢掉，新行不该以空格开头
            tok_w = display_width(token)
            if cur_w + tok_w <= width:
                cur += token
                cur_w += tok_w
                continue
            if cur_w > 0:
                lines.append(cur.rstrip())
                cur, cur_w, wrapped = "", 0, True
                if token.isspace():
                    continue
            chunks = _split_by_width(token, width)
            if len(chunks) > 1:
                lines.extend(chunks[:-1])
                wrapped = True
            cur, cur_w = chunks[-1], display_width(chunks[-1])
        lines.append(cur.rstrip())
    return lines


def truncate_display(text: str, width: int, ellipsis: str = "…") -> str:
    """按显示宽度截断，超长时补省略号（放不下省略号就干脆不补）。"""
    if width <= 0:
        return ""
    text = text.expandtabs(TAB_WIDTH)
    if display_width(text) <= width:
        return text
    ew = char_width(ellipsis) if ellipsis else 0
    if ew >= width:
        ellipsis, ew = "", 0
    limit = width - ew
    out: list[str] = []
    used = 0
    for ch in text:
        cw = char_width(ch)
        if used + cw > limit:
            break
        out.append(ch)
        used += cw
    return "".join(out) + ellipsis


def pad_display(text: str, width: int) -> str:
    """截断/补齐到正好 ``width`` 列（画状态栏底色时要用）。"""
    text = truncate_display(text, width, ellipsis="")
    return text + " " * max(0, width - display_width(text))


def slice_display(text: str, start: int, width: int) -> str:
    """取出显示列区间 ``[start, start+width)`` 的那一段（输入行横向滚动用）。"""
    out: list[str] = []
    col = 0
    used = 0
    for ch in text:
        cw = char_width(ch)
        if col + cw <= start:  # 整字都在窗口左边
            col += cw
            continue
        if col < start:  # 宽字符被窗口边界切了一半：跳过，别画出半个字
            col += cw
            continue
        if used + cw > width:
            break
        out.append(ch)
        used += cw
        col += cw
    return "".join(out)


def boundary_at_or_before(text: str, col: int) -> int:
    """返回不超过 ``col`` 的最大字符边界列。

    横向滚动时如果起点落在宽字符中间，:func:`slice_display` 会跳过那个字，
    算出来的光标位置就会跟实际画出来的文字差一列。所以先把起点对齐到字符边界。
    """
    if col <= 0:
        return 0
    used = 0
    for ch in text:
        cw = char_width(ch)
        if used + cw > col:
            return used
        used += cw
    return used


def _short_cwd(path: str, limit: int = 30) -> str:
    """工作目录的短写法：家目录换成 ``~``，太长就从**左边**截。

    从左边截是因为尾部才有区分度——``~/python/a`` 和 ``~/python/b``
    的区别全在最后一段。
    """
    if not path:
        return ""
    home = str(Path.home())
    text = "~" + path[len(home):] if path == home or path.startswith(home + "/") else path
    while display_width(text) > limit and len(text) > 1:
        text = text[1:]
    return ("…" + text) if text != path and display_width(text) >= limit else text


def _fmt_tokens(n: int) -> str:
    """上下文用量压成窄格式，状态栏才放得下。"""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


# ---------------------------------------------------------------- 转录条目


@dataclass
class Item:
    """对话区里的一个块：一次用户输入 / 一段思考 / 一段正文 / 一条工具调用。"""

    kind: str  # user | thought | agent | tool | info | error
    text: str = ""
    tool_call_id: str = ""
    tool_kind: str = ""  # ACP 的 kind：bash / read / edit / ...
    title: str = ""
    status: str = ""  # in_progress | completed | failed
    output: str = ""
    revision: int = 0  # 内容每变一次 +1，用来判断折行缓存是否失效
    # 折行缓存就挂在条目上：流式输出时每个 chunk 都会重画，没有缓存就要重排整篇转录。
    cache_width: int = -1
    cache_rev: int = -1
    cache_lines: list[tuple[str, int]] = field(default_factory=list)

    @property
    def is_done(self) -> bool:
        return self.status in ("completed", "failed")


# ---------------------------------------------------------------- 选择器

@dataclass
class PickItem:
    """选择器里的一行。"""

    label: str
    value: str = ""
    hint: str = ""          # 右侧灰字（工作目录 / 时间…）
    marker: str = ""        # 例如「当前」
    keywords: str = ""      # 参与筛选的额外文本

    def matches(self, query: str) -> bool:
        if not query:
            return True
        q = query.lower()
        return q in f"{self.label} {self.hint} {self.keywords}".lower()


class Picker:
    """覆盖式选择器：列表 + 输入即筛选 + 方向键选 + 回车确认。

    为什么做成覆盖层而不是敲命令：候选得**看得见**才好选（六十多个会话、好几个模型、
    好几个工作区）。而"输入即筛选"还顺带解决了工作区——路径没匹配到时，列表里会出现
    一行「打开 <你输入的内容>」，直接当路径输入框用。
    """

    def __init__(
        self,
        title: str,
        items: list[PickItem],
        *,
        on_pick: object = None,
        allow_custom: bool = False,
        custom_hint: str = "",
        empty_text: str = "（没有可选项）",
    ) -> None:
        self.title = title
        self.items = items
        self.on_pick = on_pick            # Callable[[str], None]
        self.allow_custom = allow_custom  # 允许直接把输入的内容当结果
        self.custom_hint = custom_hint
        self.empty_text = empty_text
        self.query = ""
        self.index = 0
        self.scroll = 0

    # ---------------------------------------------------------- 数据
    def filtered(self) -> list[PickItem]:
        return [it for it in self.items if it.matches(self.query)]

    def rows(self) -> list[PickItem]:
        """实际显示的行。允许自定义输入且没匹配到时，第一行就是"用这个输入"。"""
        rows = self.filtered()
        if self.allow_custom and self.query.strip() and not rows:
            q = self.query.strip()
            return [PickItem(label=q, value=q, hint=self.custom_hint)]
        return rows

    def current(self) -> PickItem | None:
        rows = self.rows()
        if not rows:
            return None
        return rows[max(0, min(self.index, len(rows) - 1))]

    def preselect_current(self) -> "Picker":
        """把光标放到标记为「当前」的那一项上。

        打开列表时用户最想看到的是"我现在在哪"，而不是"第一项是什么"——
        不预选的话，按一下 ↓ 很可能停在当前项上，看起来像"按了没反应"（实测踩过）。
        """
        for i, item in enumerate(self.items):
            if item.marker:
                self.index = i
                break
        return self

    def move(self, delta: int, page: int = 0) -> None:
        rows = self.rows()
        if not rows:
            self.index = 0
            return
        self.index = max(0, min(len(rows) - 1, self.index + (page or delta)))

    def confirm(self) -> None:
        item = self.current()
        if item is not None and callable(self.on_pick):
            self.on_pick(item.value or item.label)


# ---------------------------------------------------------------- 主程序


class TuiApp:
    """curses 界面 + 会话状态。

    界面状态只由主线程修改；acp_client 读取线程的一切都先经 ``self.events`` 队列。
    """

    def __init__(self, *, dsh: str, profile: str, cwd: str) -> None:
        self.dsh = dsh
        self.profile = profile
        self.cwd = cwd

        # 跨线程事件：("update"|"ready"|"fatal"|"done"|"stderr"|"exit"|"notice", payload)
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.client: AcpClient | None = None
        self.sid = ""
        self.model = ""
        self.options: list = []      # session/new 回来的 configOptions

        self.stdscr: curses.window | None = None
        self.width = 80
        self.view_h = 20
        self._scr_size = (0, 0)

        self.items: list[Item] = []
        self._lines: list[tuple[str, int]] = []
        self._built_width = -1
        self._dropped = False
        self._dirty = True

        self.scroll = 0  # 距底部往上滚了多少行（0 = 停在最新处）
        # --- 右侧说明面板 / 焦点
        self.help_visible = True
        self.help_scroll = 0        # 面板内容距底部滚了多少行
        self.focus = FOCUS_MAIN     # ↑↓/PgUp/PgDn 滚哪个区
        self.help_w = 0             # 本帧面板占多少列（含分隔线），0 = 不显示
        self.main_w = 80            # 本帧左侧对话区的宽度
        self.show_thoughts = True   # Ctrl+T 可关掉思考
        self.picker: Picker | None = None   # 覆盖式选择器（会话/模型/工作区/命令）
        self.busy_note = ""                 # 后台在忙什么（列会话、切会话…）
        self._help_cache: tuple[int, list[tuple[str, int]]] = (-1, [])
        self.buf: list[str] = []
        self.cursor = 0
        self.input_offset = 0  # 输入行横向滚动的起始列
        self._esc = ""  # 正在吞的转义序列状态（"" / "esc" / "seq"）
        self._esc_at = 0.0

        self.state = STATE_BOOT
        self.busy = False
        self.busy_since = 0.0
        self.used = 0
        self.size = 0
        self.harness_exit_code: int | None = None  # harness 的退出码，出错报告里要用
        self.stderr_count = 0
        self.session_established = False
        self.quitting = False

        self._stream_closed = True  # True = 下一个 chunk 要另起一个块
        self._boot_thread: threading.Thread | None = None
        self._shut = False
        self._t0 = time.monotonic()

        # 颜色属性；_setup_colors 里按终端能力填
        self.a_user = curses.A_BOLD
        self.a_thought = curses.A_DIM
        self.a_agent = curses.A_NORMAL
        self.a_tool_run = curses.A_BOLD
        self.a_tool_ok = curses.A_NORMAL
        self.a_tool_fail = curses.A_BOLD
        self.a_error = curses.A_BOLD
        self.a_info = curses.A_DIM
        self.a_dim = curses.A_DIM
        self.a_status = curses.A_REVERSE
        self.a_input = curses.A_NORMAL
        self.a_help_title = curses.A_BOLD
        self.a_help_key = curses.A_BOLD
        self.a_header = curses.A_BOLD
        self.a_focus = curses.A_BOLD

    # ------------------------------------------------------------ 生命周期
    def run(self, stdscr: curses.window) -> None:
        """curses 主入口（交给 ``curses.wrapper`` 调用）。"""
        self.stdscr = stdscr
        self._setup_screen()
        self._setup_colors()
        self._banner()
        self._start_client()
        try:
            self._loop()
        finally:
            # 无论如何都要把 harness 子进程收掉：异常退出也不留孤儿 dsh。
            self._draw_closing()
            self.shutdown()

    def _draw_closing(self) -> None:
        """退出前先画一行"正在收尾"。

        收尾最长要等启动线程 10 秒（BOOT_JOIN_TIMEOUT），期间界面一动不动的话，
        用户会以为卡死了。
        """
        scr = self.stdscr
        if scr is None:
            return
        try:
            h, w = scr.getmaxyx()
            self._put(h - 1, 0, pad_display("正在关闭 harness…", w), self.a_status)
            scr.noutrefresh()
            curses.doupdate()
        except curses.error:
            pass

    def _setup_screen(self) -> None:
        scr = self.stdscr
        assert scr is not None
        try:
            curses.curs_set(1)  # 输入行要看得见光标；某些终端不支持，忽略即可
        except curses.error:
            pass
        curses.noecho()
        curses.cbreak()
        self._disable_isig()
        scr.keypad(True)
        scr.timeout(POLL_MS)
        # 打开鼠标：滚轮滚动、点击定位光标。**不订阅 REPORT_MOUSE_POSITION**——
        # 那会让终端在每次移动鼠标时都发事件，白白刷屏，而我们只需要按下/滚轮。
        # 代价是终端自己的拖选会被应用接管；多数终端按住 Shift 拖动可绕过（说明面板里写了）。
        try:
            # 注意：Python binding 的 mousemask 只收**一个**参数（返回旧的掩码），
            # 不像 C API 那样还有 oldmask 出参。
            curses.mousemask(
                curses.BUTTON1_CLICKED | curses.BUTTON1_PRESSED
                | curses.BUTTON4_PRESSED | curses.BUTTON5_PRESSED
            )
            curses.mouseinterval(0)   # 不等双击判定，点一下立刻响应
        except curses.error:
            pass                       # 终端不支持鼠标就算了，键盘照样能用
        self._resize()

    def _disable_isig(self) -> None:
        """关掉终端驱动层的 ISIG，让 Ctrl+C 只作为一个字节送进来。

        为什么非关不可（实测踩到的坑）：curses 默认（cbreak）保留 ISIG，按 Ctrl+C 时内核
        给**整个前台进程组**发 SIGINT——而 harness 子进程跟 TUI 同组（``AcpClient`` 用默认
        的 ``Popen`` 参数拉起它，没有另开进程组）。后果是"取消本轮"会顺手把 harness 打死：
        假 harness 直接以 KeyboardInterrupt 退出（退出码 -2），真 harness 也一样会死，
        会话就丢了。关掉 ISIG 后 Ctrl+C 变成普通字节 0x03，由主循环自己决定是"取消本轮"
        还是"退出"，子进程毫发无损。

        代价是 Ctrl+C 不再能硬中断本进程；但退出路径都是有界的（见 BOOT_JOIN_TIMEOUT），
        不会卡死。键盘中断那条路仍保留为兜底：万一 ISIG 没关成功，KeyboardInterrupt
        一样会被 :meth:`_poll_key` 接住。终端设置由 curses 在 endwin 时还原。
        """
        if termios is None:
            return
        try:
            fd = sys.stdin.fileno()
            attrs = termios.tcgetattr(fd)
            attrs[3] &= ~termios.ISIG  # attrs[3] 是 lflag
            termios.tcsetattr(fd, termios.TCSANOW, attrs)
        except (termios.error, OSError, ValueError):
            pass  # 拿不到终端（stdin 不是 tty）就算了，还有 KeyboardInterrupt 兜底

    def _setup_colors(self) -> None:
        """尽量上色；终端不支持就退化成 A_DIM / A_BOLD / A_REVERSE。"""
        if not curses.has_colors():
            return
        curses.start_color()
        try:
            curses.use_default_colors()  # 背景透明，跟着终端自己的主题走
            bg = -1
        except curses.error:
            bg = curses.COLOR_BLACK

        def pair(idx: int, fg: int, attr: int = 0) -> int:
            curses.init_pair(idx, fg, bg)
            return curses.color_pair(idx) | attr

        self.a_user = pair(PAIR_USER, curses.COLOR_CYAN, curses.A_BOLD)
        self.a_thought = pair(PAIR_THOUGHT, curses.COLOR_WHITE, curses.A_DIM)
        self.a_tool_run = pair(PAIR_TOOL_RUN, curses.COLOR_YELLOW)
        self.a_tool_ok = pair(PAIR_TOOL_OK, curses.COLOR_GREEN)
        self.a_tool_fail = pair(PAIR_TOOL_FAIL, curses.COLOR_RED, curses.A_BOLD)
        self.a_error = pair(PAIR_ERROR, curses.COLOR_RED, curses.A_BOLD)
        self.a_info = pair(PAIR_INFO, curses.COLOR_BLUE, curses.A_DIM)
        self.a_dim = pair(PAIR_DIM, curses.COLOR_WHITE, curses.A_DIM)
        self.a_status = pair(PAIR_STATUS, curses.COLOR_CYAN, curses.A_REVERSE)
        self.a_input = pair(PAIR_PROMPT, curses.COLOR_WHITE)
        self.a_help_title = pair(PAIR_HELP_TITLE, curses.COLOR_YELLOW, curses.A_BOLD)
        self.a_help_key = pair(PAIR_HELP_KEY, curses.COLOR_CYAN)
        self.a_header = pair(PAIR_HEADER, curses.COLOR_WHITE, curses.A_BOLD)
        self.a_focus = pair(PAIR_FOCUS, curses.COLOR_CYAN, curses.A_BOLD)
        self.a_agent = curses.color_pair(0) | curses.A_NORMAL

    def _banner(self) -> None:
        self._add_item("info", f"DSH 控制台 TUI · profile={self.profile}")
        self._add_item("info", f"工作目录 {self.cwd}")
        self._add_item("info", "正在启动 harness…（首次运行要准备 profile，可能要等一会儿）")

    def _start_client(self) -> None:
        """在后台线程里拉起 harness：initialize + session/new 加起来可能要好几秒。"""
        self.client = AcpClient(
            dsh_bin=self.dsh,
            profile=self.profile,
            cwd=self.cwd,
            on_update=lambda upd: self.events.put(("update", upd)),
            on_permission=self._on_permission,
            on_stderr=lambda line: self.events.put(("stderr", line)),
            on_exit=lambda code: self.events.put(("exit", code)),
        )
        self._boot_thread = threading.Thread(target=self._boot_worker, daemon=True)
        self._boot_thread.start()

    def _boot_worker(self) -> None:
        """读取线程之外的后台线程：启动 + 建会话，结果一律回主线程处理。"""
        client = self.client
        assert client is not None
        try:
            client.start()
            if self.quitting:  # 用户在启动过程中就退了：这里负责把刚拉起的进程收掉
                client.stop()
                return
            # 不用 client.new_session()：状态栏要显示模型名，而 configOptions 只在
            # session/new 的原始结果里，new_session() 把它丢掉了。
            result = client.request("session/new", {"cwd": self.cwd, "mcpServers": []})
            sid = str(result.get("sessionId") or "")
            if self.quitting:
                client.close_session(sid)
                client.stop()
                return
            self.events.put(("ready", (sid, result.get("configOptions") or [])))
        except Exception as exc:  # noqa: BLE001 - 启动失败也要变成界面上的一行字
            self.events.put(("fatal", f"{type(exc).__name__}: {exc}"))

    def shutdown(self) -> None:
        """退出前的收尾：等启动线程收干净，再 stop() 一次（幂等）。"""
        self.quitting = True
        if self._shut:
            return
        self._shut = True
        thread = self._boot_thread
        if thread is not None and thread.is_alive():
            # 启动线程可能正卡在 initialize 里；它一旦返回就会看到 quitting 并自行 stop()。
            thread.join(BOOT_JOIN_TIMEOUT)
        client = self.client
        if client is not None:
            client.stop()

    def _loop(self) -> None:
        while not self.quitting:
            self._drain_events()
            self._draw()
            self._poll_key()

    # ------------------------------------------------------------ 事件消费
    def _drain_events(self, limit: int = 400) -> None:
        """把读取线程塞进来的事件搬到界面上（一次最多搬 limit 条，别饿死键盘）。"""
        for _ in range(limit):
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                return
            if kind == "update":
                self._on_update(payload)  # type: ignore[arg-type]
            elif kind == "ready":
                sid, options = payload  # type: ignore[misc]
                self._on_ready(str(sid), list(options))
            elif kind == "fatal":
                self._on_fatal(str(payload))
            elif kind == "done":
                reason, err = payload  # type: ignore[misc]
                self._on_done(str(reason), err)  # type: ignore[arg-type]
            elif kind == "stderr":
                self.stderr_count += 1  # 不往转录里灌日志，只在出错时把尾巴贴出来
            elif kind == "exit":
                self._on_exit(int(payload))  # type: ignore[arg-type]
            elif kind == "notice":
                self._add_item("info", str(payload))
            elif kind == "busy":
                self.busy_note = str(payload)
            elif kind == "open_picker":
                self.picker = payload  # type: ignore[assignment]
                self.busy_note = ""
            elif kind == "switch":
                self.busy_note = ""
                self._on_switched(payload)  # type: ignore[arg-type]
            elif kind == "resumed":
                self.busy_note = ""
                self._on_resumed(payload)  # type: ignore[arg-type]
            elif kind == "options":
                self.options = list(payload)  # type: ignore[arg-type]
                self.model = _model_label(self.options)
                self._dirty = True

    def _on_ready(self, sid: str, options: list) -> None:
        self.sid = sid
        self.session_established = True
        self.state = STATE_READY
        self.options = list(options)
        self.model = _model_label(options)
        info = self.client.agent_info if self.client else {}
        name = str(info.get("name") or "harness")
        version = str(info.get("version") or "")
        self._add_item("info", f"已连接 {name} {version}".rstrip())
        self._add_item("info", f"会话 {sid}")
        self._add_item("info", "输入内容后按 Enter 发送；Ctrl+D 退出。")
        self._dirty = True

    def _on_fatal(self, message: str) -> None:
        self.state = STATE_FAILED
        self.busy = False
        text = f"harness 启动失败：{message}"
        if self.harness_exit_code is not None:
            # 进程已经退了：退出码补在同一行，省得用户在两行之间自己拼因果
            text += f"（harness 进程退出码 {self.harness_exit_code}）"
        self._add_item("error", text)
        self._append_stderr_tail()
        self._add_item("info", "按 Ctrl+D（或 Ctrl+C）退出。")

    def _on_exit(self, code: int) -> None:
        if self.quitting:
            return  # 我们自己关掉的，不用再报告一遍
        self.harness_exit_code = code
        if self.state == STATE_BOOT:
            self.state = STATE_FAILED
            self._add_item("error", f"harness 进程在启动阶段退出（退出码 {code}）")
            self._append_stderr_tail()
        elif self.state == STATE_FAILED:
            # 启动报错可能先到（典型是往已死的进程写管道：Broken pipe）。退出码才是根因，
            # 所以补一行，别让用户只看到"写入失败"这种二手症状。
            self._add_item("error", f"harness 进程已经退出（退出码 {code}）")
        elif self.session_established:
            self.state = STATE_EXITED
            self._add_item("error", f"harness 进程退出了（退出码 {code}）")
            self._append_stderr_tail()
        self.busy = False
        self._dirty = True

    def _append_stderr_tail(self) -> None:
        """把 harness 的 stderr 尾巴贴进对话区——否则用户只看到"失败了"三个字。"""
        client = self.client
        if client is None:
            return
        tail = client.stderr_text(12).strip()
        if tail:
            self._add_item("error", "harness stderr 尾巴：\n" + tail)
        else:
            self._add_item("info", "（harness 没有输出 stderr）")

    def _on_done(self, reason: str, err: Exception | None) -> None:
        self.busy = False
        self._stream_closed = True
        elapsed = time.monotonic() - self.busy_since if self.busy_since else 0.0
        if err is not None:
            self._add_item("error", f"这一轮出错：{type(err).__name__}: {err}")
            self._append_stderr_tail()
        elif reason == "cancelled":
            self._add_item("info", f"本轮已取消（{elapsed:.1f}s）")
        else:
            self._add_item("info", f"本轮结束：{reason or '未知原因'}（{elapsed:.1f}s）")
        self._dirty = True

    def _on_update(self, upd: dict) -> None:
        """渲染一条 session/update（主线程里跑，可以放心改界面状态）。"""
        kind = upd.get("sessionUpdate")
        if kind in ("agent_message_chunk", "agent_thought_chunk"):
            text = _flatten_content(upd.get("content"))
            if text:
                self._append_stream("thought" if kind == "agent_thought_chunk" else "agent", text)
        elif kind == "tool_call":
            self._upsert_tool(upd, create=True)
        elif kind == "tool_call_update":
            self._upsert_tool(upd, create=False)
        elif kind == "usage_update":
            self.used = int(upd.get("used") or 0)
            self.size = int(upd.get("size") or 0)
            self._dirty = True
        else:
            # 没见过的新形状不要静默丢掉：留一行线索，方便回头补渲染。
            self._add_item("info", f"[未渲染的 update] {kind}")

    def _on_permission(self, params: dict) -> str | None:
        """权限询问：回调在读取线程里，不能弹交互框，只记一行并走客户端默认策略。

        返回 None 时 AcpClient 会自己挑第一个 ``allow*`` 选项（permission_policy="allow"）。
        """
        tool = params.get("toolCall") or {}
        title = str(tool.get("title") or params.get("title") or "未知工具")
        count = len(params.get("options") or [])
        self.events.put(("notice", f"[权限] {title} → 按默认策略自动允许（{count} 个选项）"))
        return None

    # ------------------------------------------------------------ 转录内容
    def _add_item(self, kind: str, text: str) -> Item:
        item = Item(kind=kind, text=text)
        self.items.append(item)
        self._stream_closed = True
        self._dirty = True
        return item

    def _append_stream(self, kind: str, text: str) -> None:
        """流式 chunk 追加：能并进上一个同类块就并，避免几千个碎块。"""
        item = self.items[-1] if self.items else None
        if item is None or item.kind != kind or self._stream_closed:
            item = Item(kind=kind)
            self.items.append(item)
        item.text += text
        item.revision += 1
        self._stream_closed = False
        self._dirty = True

    def _upsert_tool(self, upd: dict, create: bool) -> None:
        tcid = str(upd.get("toolCallId") or "")
        item = self._find_tool(tcid)
        if item is None:
            if not create and not tcid:
                return
            item = Item(kind="tool", tool_call_id=tcid)
            self.items.append(item)
            self._stream_closed = True
        if upd.get("title"):
            item.title = str(upd["title"])
        if upd.get("kind"):
            item.tool_kind = str(upd["kind"])
        if upd.get("status"):
            item.status = str(upd["status"])
        text = _flatten_content(upd.get("content"))
        if text:
            item.output += text
        item.revision += 1
        self._dirty = True

    def _find_tool(self, tcid: str) -> Item | None:
        if not tcid:
            return None
        for item in reversed(self.items):
            if item.kind == "tool" and item.tool_call_id == tcid:
                return item
        return None

    # ------------------------------------------------------------ 折行 / 组行
    def _item_lines(self, item: Item, width: int) -> list[tuple[str, int]]:
        if item.cache_width == width and item.cache_rev == item.revision:
            return item.cache_lines
        lines = self._build_item_lines(item, width)
        item.cache_width, item.cache_rev, item.cache_lines = width, item.revision, lines
        return lines

    def _build_item_lines(self, item: Item, width: int) -> list[tuple[str, int]]:
        if item.kind == "tool":
            return self._tool_lines(item, width)
        if item.kind == "user":
            return self._user_lines(item, width)
        if item.kind == "thought" and not self.show_thoughts:
            return []
        prefix, attr = {
            "thought": (". ", self.a_thought),
            "info": (". ", self.a_info),
            "agent": ("", self.a_agent),
            "error": ("", self.a_error),
            # 从会话日志重建的历史：统一暗色 + 加"我/它/工具"标记，
            # 一眼能和本次的新对话分开
            "history_user": ("· 我  ", self.a_dim),
            "history_agent": ("· 它  ", self.a_dim),
            "history_tool": ("· 工具 ", self.a_dim),
        }.get(item.kind, ("", self.a_agent))
        pad = " " * display_width(prefix)
        body_w = max(1, width - display_width(prefix))
        out: list[tuple[str, int]] = []
        for i, row in enumerate(wrap_display(item.text, body_w)):
            out.append(((prefix if i == 0 else pad) + row, attr))
        return out

    def _user_lines(self, item: Item, width: int) -> list[tuple[str, int]]:
        """用户消息**靠右**排，像 web 版那样。

        web 版把用户消息放进右侧气泡，一眼就能分开"我说的"和"它说的"。TUI 里没有
        气泡，用右对齐 + 右侧留白达到同样效果；但**不铺满整宽**——终端里整行右对齐的
        长文本会变成左边缘参差，反而难读，所以按 72% 折行。
        """
        bubble = max(16, int(width * 0.72))
        rows = wrap_display(item.text, bubble) or [""]
        out: list[tuple[str, int]] = []
        for row in rows:
            pad = " " * max(0, width - display_width(row) - 2)
            out.append((pad + row + "  ", self.a_user))
        return out

    def _tool_lines(self, item: Item, width: int) -> list[tuple[str, int]]:
        kind = item.tool_kind or "tool"
        if item.status == "completed":
            head, attr = f"  ✓ {kind}  {item.title}", self.a_tool_ok
        elif item.status == "failed":
            head, attr = f"  ✗ {kind}  {item.title}", self.a_tool_fail
        else:
            head, attr = f"▶ {kind}  {item.title}", self.a_tool_run
        rows: list[tuple[str, int]] = [(head.rstrip(), attr)]
        out = (item.output or "").strip("\n").rstrip()
        if out:
            body = wrap_display(out, max(1, width - 4))
            for line in body[:TOOL_OUTPUT_LINES]:
                rows.append(("    " + line, self.a_dim))
            if len(body) > TOOL_OUTPUT_LINES:
                rows.append((f"    … 其余 {len(body) - TOOL_OUTPUT_LINES} 行省略", self.a_dim))
        return rows

    def _rebuild_lines(self) -> None:
        """把条目拼成一行行文本。只在内容变化或宽度变化时做，避免每帧重排。"""
        while True:
            lines: list[tuple[str, int]] = []
            if self._dropped:
                lines.append(("…（更早的内容已省略）", self.a_dim))
            prev = ""
            for item in self.items:
                # 用户输入和工具调用前留一个空行分组；连续的多个工具调用之间不留，
                # 否则一次多工具调用会被拆得七零八落。
                if lines and item.kind in ("user", "tool") and prev != "tool":
                    lines.append(("", self.a_agent))
                lines.extend(self._item_lines(item, self.main_w))
                prev = item.kind
            if len(lines) <= MAX_LINES or len(self.items) <= 1:
                break
            # 一次丢掉最老的四分之一：临界点上一条条丢会反复重排。
            del self.items[: max(1, len(self.items) // 4)]
            self._dropped = True
        self._lines = lines
        # 记的是**对话区列宽**而不是终端宽度：右侧面板占掉的那几十列不能让正文折进去，
        # 否则右对齐的用户消息会直接飘到面板上（实测踩过）。
        self._built_width = self.main_w

    # ------------------------------------------------------------ 输入
    def _poll_key(self) -> None:
        scr = self.stdscr
        assert scr is not None
        try:
            ch = scr.get_wch()
        except curses.error:
            return  # 超时：这段时间没有按键
        except KeyboardInterrupt:
            # cbreak 保留了 ISIG，Ctrl+C 会走信号这条路
            self._on_interrupt()
            return
        # 选择器打开时它就是唯一的输入目标；但窗口尺寸变化仍然要处理，
        # 否则调整窗口大小后浮层会画错位。
        if self.picker is not None and ch != curses.KEY_RESIZE:
            self._picker_key(ch)
            return
        if isinstance(ch, int):
            self._on_key_code(ch)
        else:
            self._on_char(ch)

    def _on_key_code(self, code: int) -> None:
        self._esc = ""  # curses 认出按键，说明转义序列已被它消费掉
        if code == curses.KEY_RESIZE:
            self._resize()
        elif code == curses.KEY_MOUSE:
            self._on_mouse()
        elif code == curses.KEY_F1:
            self._toggle_help()
        elif code == curses.KEY_F2:
            self._open_palette()
        elif code == curses.KEY_F3:
            self._pick_session()
        elif code == curses.KEY_F4:
            self._pick_model()
        elif code == curses.KEY_F5:
            self._pick_workspace()
        elif code == curses.KEY_F6:
            self._new_session()
        elif code == curses.KEY_BACKSPACE:
            self._backspace()
        elif code == curses.KEY_DC:
            if self.cursor < len(self.buf):
                del self.buf[self.cursor]
        elif code == curses.KEY_LEFT:
            self.cursor = max(0, self.cursor - 1)
        elif code == curses.KEY_RIGHT:
            self.cursor = min(len(self.buf), self.cursor + 1)
        elif code in (curses.KEY_HOME,):
            self.cursor = 0
        elif code in (curses.KEY_END,):
            self.cursor = len(self.buf)
        elif code == curses.KEY_UP:
            self._scroll(1)
        elif code == curses.KEY_DOWN:
            self._scroll(-1)
        elif code == curses.KEY_PPAGE:
            self._scroll(max(1, self.view_h - 1))
        elif code == curses.KEY_NPAGE:
            self._scroll(-max(1, self.view_h - 1))
        elif code == curses.KEY_ENTER:
            self._submit()
        self._dirty = True

    def _on_char(self, ch: str) -> None:
        if self._esc and self._feed_escape(ch):
            return
        if ch in ("\n", "\r"):
            self._submit()
        elif ch in ("\x7f", "\x08"):  # 退格：DEL 和 BS 两种终端都照顾到
            self._backspace()
        elif ch == "\x03":
            self._on_interrupt()
        elif ch == "\x04":
            self.quitting = True
        elif ch == "\x01":
            self.cursor = 0
        elif ch == "\x05":
            self.cursor = len(self.buf)
        elif ch == "\x0b":  # Ctrl+K：删到行尾
            del self.buf[self.cursor :]
        elif ch == "\x15":  # Ctrl+U：清空输入行
            self._clear_input()
        elif ch == "\x17":  # Ctrl+W：删掉光标前的一个词
            self._kill_word()
        elif ch == "\x0c":  # Ctrl+L：重绘
            self._redraw()
        elif ch == "\x0e":  # Ctrl+N：新建会话（最常用的那个，给个直键）
            self._new_session()
        elif ch == "\x14":  # Ctrl+T：显示/隐藏思考
            self._toggle_thoughts()
        elif ch == "\t":  # Tab：切换焦点
            self._toggle_focus()
        elif ch == "\x1b":
            self._esc, self._esc_at = "esc", time.monotonic()
        elif ch.isprintable():
            self.buf.insert(self.cursor, ch)
            self.cursor += 1
        self._dirty = True

    def _feed_escape(self, ch: str) -> bool:
        """吞掉一个 curses 没认出来的转义序列，返回 True 表示这个字符已被吃掉。

        实测踩到的坑：终端的 ``khome``/方向键序列跟 terminfo 对不上时，curses 只把 ESC
        当普通字符返回，**序列剩下的字节会被当成正文插进输入行**（输入行里出现 ``[H``、
        ``[D`` 这种垃圾）。所以这里自己吃掉 ``ESC [ ... 终结字节`` 整段。

        超时也算：用户单按一下 ESC 之后隔一会儿再打字，那是两个独立按键，不该被吞。
        """
        if time.monotonic() - self._esc_at > ESC_SEQUENCE_TIMEOUT:
            self._esc = ""  # 上一个 ESC 是很久以前的事，当前按键跟它无关
            return False
        if self._esc == "esc":
            self._esc = "seq" if ch in "[O" else ""
            return True
        if "\x40" <= ch <= "\x7e":  # CSI 的终结字节（字母或 ~），序列到此为止
            self._esc = ""
        return True

    def _backspace(self) -> None:
        if self.cursor > 0:
            del self.buf[self.cursor - 1]
            self.cursor -= 1

    def _kill_word(self) -> None:
        i = self.cursor
        while i > 0 and self.buf[i - 1].isspace():
            i -= 1
        while i > 0 and not self.buf[i - 1].isspace():
            i -= 1
        del self.buf[i : self.cursor]
        self.cursor = i

    def _clear_input(self) -> None:
        self.buf.clear()
        self.cursor = 0
        self.input_offset = 0

    # ------------------------------------------------------ 会话 / 模型 / 工作区
    #
    # 这一组是"换一个东西接着聊"。四条路径都走同一个套路：**后台线程拿数据 →
    # 事件回主线程 → 弹选择器 → 回调里发 ACP 请求**。界面线程一次都不许等网络。
    #
    # 键位安排：F1 说明面板，F2..F6 依次是 命令 / 会话 / 模型 / 工作区 / 新建。
    # F 键是终端里少数几个"每个终端都发得出来、又不和编辑键打架"的键。

    def _busy(self, note: str) -> None:
        """显示"后台在忙什么"。不引入新的 state——省得所有判断 state 的地方都要改。"""
        self.busy_note = note
        self._dirty = True

    def _need_client(self) -> "AcpClient | None":
        if self.client is None or not self.session_established:
            self._add_item("info", "harness 还没就绪，先等一会儿再试。")
            return None
        return self.client

    def _open_palette(self) -> None:
        """命令面板：所有会话操作都从这里进得去，不用记 F 键。"""
        items = [
            PickItem("新建会话", "new", hint="Ctrl+N", keywords="xinjian new session"),
            PickItem("切换会话…", "sessions", hint="F3", keywords="qiehuan session resume"),
            PickItem("切换模型…", "model", hint="F4", keywords="qiehuan model"),
            PickItem("切换工作区…", "workspace", hint="F5", keywords="qiehuan workspace cwd"),
            PickItem("显示 / 隐藏说明面板", "help", hint="F1", keywords="help panel"),
            PickItem("显示 / 隐藏思考过程", "thoughts", hint="Ctrl+T", keywords="thinking"),
            PickItem("退出", "quit", hint="Ctrl+D", keywords="quit exit"),
        ]
        actions = {
            "new": self._new_session,
            "sessions": self._pick_session,
            "model": self._pick_model,
            "workspace": self._pick_workspace,
            "help": self._toggle_help,
            "thoughts": self._toggle_thoughts,
            "quit": lambda: setattr(self, "quitting", True),
        }
        self.picker = Picker("命令", items, on_pick=lambda v: actions.get(v, lambda: None)())

    def _new_session(self) -> None:
        """在当前工作区新建一个会话，并把对话区清空。"""
        client = self._need_client()
        if client is None:
            return
        if self.busy:
            self._add_item("info", "上一轮还在跑，先 Ctrl+C 取消再新建会话。")
            return
        cwd = self.cwd
        self._busy(f"正在新建会话（{cwd}）…")

        def job() -> None:
            try:
                sid = client.new_session(cwd)
                self._carry_over(client, sid)
                result = ("ok", sid, cwd, "")
            except Exception as exc:  # noqa: BLE001
                result = ("err", "", cwd, f"{type(exc).__name__}: {exc}")
            self.events.put(("switch", result))

        threading.Thread(target=job, daemon=True).start()

    def _pick_session(self) -> None:
        """列出可恢复的会话，选一个恢复。"""
        client = self._need_client()
        if client is None:
            return
        if self.busy:
            self._add_item("info", "上一轮还在跑，先 Ctrl+C 取消再切换会话。")
            return
        self._busy("正在读取会话列表…")

        def job() -> None:
            try:
                import dsh_console.sessions as S

                raw = client.list_sessions()
                merged = S.merge(raw)
                items = [
                    PickItem(
                        label=m.label(),
                        value=m.session_id,
                        hint=f"{_short_cwd(m.cwd)} · {m.when()}",
                        marker="当前" if m.session_id == self.sid else "",
                        keywords=f"{m.cwd} {m.session_id}",
                    )
                    for m in merged
                ]
                self.events.put(("open_picker", Picker(
                    f"切换会话（{len(items)} 个）", items,
                    on_pick=self._resume_session, empty_text="（没有可恢复的会话）",
                ).preselect_current()))
            except Exception as exc:  # noqa: BLE001
                self.events.put(("notice", f"读取会话列表失败：{type(exc).__name__}: {exc}"))

        threading.Thread(target=job, daemon=True).start()

    def _resume_session(self, session_id: str) -> None:
        client = self._need_client()
        if client is None or not session_id:
            return
        if session_id == self.sid:
            self._add_item("info", "就是当前会话，没有切换。")
            return
        self._busy("正在恢复会话…")

        def job() -> None:
            try:
                import dsh_console.sessions as S

                info = client.load_session(session_id) or {}
                cwd = str(info.get("cwd") or "") or self._cwd_of(session_id) or self.cwd
                title, hist = S.history(session_id)
                self.events.put(("resumed", (session_id, cwd, title, hist, "")))
            except Exception as exc:  # noqa: BLE001
                self.events.put(("switch", ("err", "", self.cwd,
                                            f"{type(exc).__name__}: {exc}")))

        threading.Thread(target=job, daemon=True).start()

    def _carry_over(self, client: "AcpClient", sid: str) -> None:
        """把当前会话的配置（模型、推理档位…）带到新会话上。

        ACP 的 ``session/new`` **没有**模型参数——查过 schema，它只收 ``cwd`` /
        ``additionalDirectories`` / ``mcpServers``。模型只能在建完会话之后用
        ``session/set_config_option`` 设。不搬这一下，用户切完模型再新建会话
        就会发现模型被打回默认值，很莫名其妙。
        """
        for opt in self.options or []:
            if not isinstance(opt, dict) or opt.get("type") != "select":
                continue
            opt_id = str(opt.get("id") or "")
            value = str(opt.get("currentValue") or "")
            if not opt_id or not value:
                continue
            try:
                client.set_config_option(sid, opt_id, value)
            except Exception:  # noqa: BLE001 - 新会话不认这个项就算了，不该让建会话失败
                continue

    def _cwd_of(self, session_id: str) -> str:
        try:
            import dsh_console.sessions as S

            return S.cached_meta().get(session_id, S.SessionMeta(session_id)).cwd
        except Exception:  # noqa: BLE001
            return ""

    def _pick_model(self) -> None:
        """列出模型（和别的可选项），选一个切过去。"""
        client = self._need_client()
        if client is None:
            return
        items = self._option_items()
        if not items:
            self._add_item("info", "这个会话没有可切换的配置项。")
            return
        self.picker = Picker("切换模型 / 配置", items, on_pick=self._set_option,
                             empty_text="（没有匹配的配置项）").preselect_current()

    def _option_items(self) -> list[PickItem]:
        """把 ``configOptions`` 拍成候选行。

        它是两类形状的混合：分组的 select（模型）是 ``options[].options[]``，
        普通的（推理档位之类）是平铺的 ``options[]``。两种都要认。
        """
        items: list[PickItem] = []
        for opt in self.options or []:
            if not isinstance(opt, dict):
                continue
            opt_id = str(opt.get("id") or "")
            if not opt_id or opt.get("type") != "select":
                continue
            current = str(opt.get("currentValue") or "")
            title = str(opt.get("name") or opt_id)
            for entry in opt.get("options") or []:
                if not isinstance(entry, dict):
                    continue
                group = entry.get("group")
                if group and entry.get("options"):
                    for sub in entry["options"]:
                        value = str(sub.get("value") or "")
                        items.append(PickItem(
                            label=f"{title} · {sub.get('name') or value}",
                            value=f"{opt_id}\u0000{value}",
                            hint=str(group),
                            marker="当前" if value == current else "",
                            keywords=f"{opt_id} {group}",
                        ))
                elif entry.get("value") is not None:
                    value = str(entry.get("value"))
                    items.append(PickItem(
                        label=f"{title} · {entry.get('name') or value}",
                        value=f"{opt_id}\u0000{value}",
                        marker="当前" if value == current else "",
                        keywords=opt_id,
                    ))
        return items

    def _set_option(self, packed: str) -> None:
        client = self._need_client()
        if client is None or "\u0000" not in packed:
            return
        opt_id, value = packed.split("\u0000", 1)
        sid = self.sid
        self._busy("正在切换…")

        def job() -> None:
            try:
                result = client.set_config_option(sid, opt_id, value) or {}
                self.events.put(("options", result.get("configOptions") or []))
                self.events.put(("notice", f"已切换 {opt_id}"))
            except Exception as exc:  # noqa: BLE001
                self.events.put(("notice", f"切换失败：{type(exc).__name__}: {exc}"))

        threading.Thread(target=job, daemon=True).start()

    def _pick_workspace(self) -> None:
        """选工作区。候选是"用过的目录"，也可以直接敲一个路径。

        复用同一个输入框做筛选 + 路径输入：没匹配到时第一行就是「打开 <输入>」，
        所以"切到一个从没用过的目录"和"切回常用目录"是同一个操作。
        """
        client = self._need_client()
        if client is None:
            return
        self._busy("正在读取工作区…")

        def job() -> None:
            try:
                import dsh_console.sessions as S

                merged = S.merge(client.list_sessions())
                items = [
                    PickItem(label=cwd, value=cwd, hint=f"{n} 个会话",
                             marker="当前" if cwd == self.cwd else "")
                    for cwd, n in S.workspaces(merged)
                ]
                if not any(i.value == self.cwd for i in items):
                    items.insert(0, PickItem(label=self.cwd, value=self.cwd,
                                             hint="当前", marker="当前"))
                home = str(Path.home())
                if not any(i.value == home for i in items):
                    items.append(PickItem(label=home, value=home, hint="家目录"))
                self.events.put(("open_picker", Picker(
                    "切换工作区", items, on_pick=self._switch_workspace,
                    allow_custom=True, custom_hint="用这个路径新建会话",
                    empty_text="输入一个目录路径，回车即用",
                ).preselect_current()))
            except Exception as exc:  # noqa: BLE001
                self.events.put(("notice", f"读取工作区失败：{type(exc).__name__}: {exc}"))

        threading.Thread(target=job, daemon=True).start()

    def _switch_workspace(self, cwd: str) -> None:
        """切工作区 = 在那个目录**新建**一个会话（会话与工作目录是绑定的）。"""
        client = self._need_client()
        if client is None or not cwd:
            return
        if not os.path.isdir(cwd):
            self._add_item("error", f"目录不存在：{cwd}")
            return
        self._busy(f"正在 {cwd} 新建会话…")

        def job() -> None:
            try:
                sid = client.new_session(cwd)
                self._carry_over(client, sid)
                self.events.put(("switch", ("ok", sid, cwd, "")))
            except Exception as exc:  # noqa: BLE001
                self.events.put(("switch", ("err", "", cwd, f"{type(exc).__name__}: {exc}")))

        threading.Thread(target=job, daemon=True).start()

    def _on_switched(self, payload: tuple) -> None:
        """切会话/换工作区成功了：换 id、换工作目录、清空转录。"""
        status, sid, cwd, err = payload
        if status != "ok":
            self._add_item("error", f"切换失败：{err}")
            return
        self.sid = sid
        self.cwd = cwd or self.cwd
        self.items.clear()
        self._lines = []
        self._built_width = -1
        self.scroll = 0
        self._dropped = False
        self._stream_closed = True
        self._add_item("info", f"新会话 {sid[:8]} · 工作目录 {self.cwd}")

    def _on_resumed(self, payload: tuple) -> None:
        """恢复会话成功：把从会话日志里重建的历史**整段铺出来**。

        ACP 的 resume 不回放旧更新，不自己读日志的话切过去就是一片空白——
        那等于什么也没切。历史统一标成暗色，和本次的新对话区分开。
        """
        session_id, cwd, title, hist, err = payload
        if err:
            self._add_item("error", f"恢复会话失败：{err}")
            return
        self.sid = session_id
        self.cwd = cwd or self.cwd
        self.items.clear()
        self._lines = []
        self._built_width = -1
        self.scroll = 0
        self._dropped = False
        self._stream_closed = True
        head = f"已恢复会话 {session_id[:8]}"
        if title:
            head += f"「{title}」"
        head += f" · 工作目录 {self.cwd}"
        self._add_item("info", head)
        if hist:
            self._add_item("info", f"—— 以下是这个会话的历史（最近 {len(hist)} 条，暗色显示）——")
            for kind, text in hist:
                self._add_item({"user": "history_user", "agent": "history_agent"}.get(
                    kind, "history_tool"), text)
        else:
            self._add_item("info", "（这个会话没有可显示的历史）")

    def _submit(self) -> None:
        text = "".join(self.buf).strip()
        self._clear_input()
        if not text:
            return
        if self.state != STATE_READY or self.busy:
            self._add_item("info", "harness 还没就绪或上一轮还在跑，先 Ctrl+C 取消再来。")
            return
        client = self.client
        if client is None or not self.sid:
            return
        self._add_item("user", text)
        self.scroll = 0
        self.busy = True
        self.busy_since = time.monotonic()
        client.prompt_async(self.sid, text, on_done=self._prompt_done)

    def _prompt_done(self, reason: str, err: Exception | None) -> None:
        """在 prompt 的工作线程里被调用：只投事件，绝不碰界面。"""
        self.events.put(("done", (reason, err)))

    def _on_interrupt(self) -> None:
        if self.busy and self.client is not None and self.sid:
            self.client.cancel(self.sid)
            self._add_item("info", "已请求取消本轮…（等 harness 收尾）")
        else:
            self.quitting = True

    def _scroll(self, delta: int) -> None:
        """滚动**当前焦点区**。

        ⚠️ 两个区的滚动量**约定相反**：对话区的 ``scroll`` 是"距底部多少行"
        （越大越往历史里走），面板的 ``help_scroll`` 是"距顶部多少行"
        （越大越往下看）。所以调用方传同一个 delta（正 = 向上/向前），
        面板这边必须反号——不反的话 PgDn 会把面板往上滚，越按越回到开头。
        """
        if self.focus == FOCUS_HELP:
            self.help_scroll = max(0, min(self._help_max_scroll(), self.help_scroll - delta))
        else:
            max_scroll = max(0, len(self._lines) - self.view_h)
            self.scroll = max(0, min(max_scroll, self.scroll + delta))

    def _help_body_rows(self) -> int:
        """面板里真正放内容的行数（溢出时最后一行让给滚动指示）。"""
        _, lines = self._help_lines_for(self.help_w)
        rows = self._panel_rows()
        return rows - 1 if len(lines) > rows else rows

    def _panel_rows(self) -> int:
        scr = self.stdscr
        if scr is None:
            return 1
        try:
            h, _ = scr.getmaxyx()
        except curses.error:
            return 1
        return max(1, h)

    def _help_max_scroll(self) -> int:
        _, lines = self._help_lines_for(self.help_w)
        return max(0, len(lines) - max(1, self._help_body_rows()))

    def _toggle_help(self) -> None:
        self.help_visible = not self.help_visible
        if not self.help_visible:
            self.focus = FOCUS_MAIN
        self._built_width = -1      # 对话区宽度变了，折行缓存要作废
        self._help_cache = (-1, [])
        self._dirty = True

    def _toggle_focus(self) -> None:
        self.focus = FOCUS_HELP if self.focus == FOCUS_MAIN else FOCUS_MAIN
        self._dirty = True

    def _toggle_thoughts(self) -> None:
        self.show_thoughts = not self.show_thoughts
        self._dirty = True

    # ------------------------------------------------------------ 版面几何
    def _layout(self, width: int) -> tuple[int, int]:
        """算出这一帧的 ``(main_w, help_w)``。

        规则：面板优先给足 42 列；挤到对话区不足 44 列就压缩面板；再窄（< 78 列）
        干脆不显示——两边都看不清，比少看一边更糟。
        """
        if not self.help_visible or width < HELP_HIDE_BELOW:
            return width, 0
        help_w = min(HELP_WIDTH, max(0, width - HELP_MIN_MAIN))
        if help_w < 24:              # 压得太狠就失去意义了，不如藏起来
            return width, 0
        return width - help_w, help_w

    # ------------------------------------------------------------ 说明面板
    def _help_lines_for(self, help_w: int) -> tuple[int, list[tuple[str, int]]]:
        """把 :data:`HELP_SECTIONS` 折成一行行，按面板宽度缓存。"""
        cache_w, cached = self._help_cache
        if cache_w == help_w and cached:
            return help_w, cached
        body_w = max(8, help_w - 3)          # 一格分隔线 + 两边留白
        key_w = min(15, max(9, body_w // 3))
        lines: list[tuple[str, int]] = []
        for si, (title, rows) in enumerate(HELP_SECTIONS):
            if si:
                lines.append(("", self.a_dim))
            lines.append((title, self.a_help_title))
            for key, desc in rows:
                # 键名固定栏宽、说明从同一列起——像手册一样对齐才好扫。
                # 键名比栏宽还长时要**让位**：否则 pad_display 一截断，
                # 说明的第一个字就贴到键名尾巴上（实测出现过「Pg回到最新内容」）。
                kw = max(key_w, display_width(key) + 1)
                head = pad_display(key, kw)
                wrapped = wrap_display(desc, max(6, body_w - kw))
                lines.append((head + wrapped[0], self.a_help_key))
                for cont in wrapped[1:]:
                    lines.append((" " * kw + cont, self.a_dim))
        self._help_cache = (help_w, lines)
        return help_w, lines

    def _draw_help(self, x: int, width: int, height: int) -> None:
        """画右侧面板：一条竖分隔线 + 可滚动的说明内容。

        **从顶部开始读**（不是像对话区那样锚在底部）：说明是给人从头看的，
        一进来就看到中段会莫名其妙。内容装不下时最后一行变成滚动指示。
        """
        rows = max(1, height)
        _, lines = self._help_lines_for(width)
        overflow = len(lines) > rows
        # 溢出时最后一行让给滚动指示。这个行数必须和 _help_max_scroll 用同一个口径，
        # 否则"能滚到哪"和"实际画到哪"对不上，滚到底会看到空白。
        body_rows = rows - 1 if overflow else rows
        self.help_scroll = max(0, min(self._help_max_scroll(), self.help_scroll))
        focused = self.focus == FOCUS_HELP
        for i in range(body_rows):
            idx = self.help_scroll + i
            text, attr = lines[idx] if idx < len(lines) else ("", self.a_dim)
            self._put(i, x + 2, text, attr)
            self._put(i, x, "│", self.a_focus if focused else self.a_dim)
        if overflow:
            below = len(lines) - (self.help_scroll + body_rows)
            above = self.help_scroll
            bits = []
            if above:
                bits.append(f"↑ 上面还有 {above} 行")
            if below:
                bits.append(f"↓ 下面还有 {below} 行")
            # 提示按面板实际宽度取舍：窄的时候先把括号里的操作说明去掉，
            # 别让它被 truncate 切一半（切一半比不显示更难看）
            tip = "·".join(bits)
            if display_width(tip) + 8 <= width - 3:
                tip += "（滚轮 / PgDn）"
            self._put(rows - 1, x + 2, truncate_display(tip, max(4, width - 3)), self.a_dim)
            self._put(rows - 1, x, "│", self.a_focus if focused else self.a_dim)

    # ------------------------------------------------------------ 选择器
    def _draw_picker(self, height: int, width: int) -> None:
        """把选择器画成一个居中的浮层。

        屏幕不够大时退化成"铺满可用区域"，绝不画到屏幕外——curses 在右下角写字符会
        抛错，而这时候正是最该好好显示内容的时候。
        """
        pk = self.picker
        if pk is None:
            return
        rows = pk.rows()
        # 宽度**按内容自适应**而不是一律取屏幕的 72%：命令面板只有几个短标签，
        # 撑到 86 列会让标签和快捷键隔着半个屏幕，读起来费劲。
        widest = max((display_width(it.label) + display_width(it.marker or it.hint) + 6
                      for it in rows), default=20)
        box_w = max(30, min(width - 4, int(width * 0.72), widest + 6))
        max_h = max(5, int(height * 0.7))
        want_h = len(rows) + 4                      # 边框 2 + 标题 1 + 输入行 1
        box_h = max(5, min(max_h, want_h))
        list_h = max(1, box_h - 4)                  # 去掉边框、标题、输入行
        x = max(0, (width - box_w) // 2)
        y = max(0, (height - box_h) // 2)

        # 铺底要铺**整行**，不只是浮层那一段：浮层通常比屏幕窄，只铺自己那块的话
        # 右侧说明面板的文字会从浮层边上露出来，看起来像串行。整行留白才像个模态框。
        for i in range(box_h):
            self._put(y + i, 0, " " * width, self.a_input)

        title = f" {pk.title} "
        self._put(y, x + 2, title, self.a_help_title)
        self._put(y, x, "┌" + "─" * (box_w - 2) + "┐", self.a_help_title)
        self._put(y + box_h - 1, x, "└" + "─" * (box_w - 2) + "┘", self.a_help_title)

        # 输入行（筛选）：光标紧跟在文字后面，不是在框边——
        # 放在框边会让人以为"输入会跑到右边去"。
        prompt = "筛选: " if not pk.allow_custom else "输入: "
        typed = truncate_display(prompt + pk.query, box_w - 6)
        self._put(y + 1, x + 2, typed + "▏", self.a_input)

        # 候选列表
        if not rows:
            self._put(y + 2 + list_h // 2, x + 3, truncate_display(pk.empty_text, box_w - 6),
                      self.a_dim)

        # 让高亮项始终可见
        pk.index = max(0, min(pk.index, len(rows) - 1)) if rows else 0
        if pk.index < pk.scroll:
            pk.scroll = pk.index
        if pk.index >= pk.scroll + list_h:
            pk.scroll = pk.index - list_h + 1
        pk.scroll = max(0, min(pk.scroll, max(0, len(rows) - list_h)))

        # 标签列别无限宽：浮层一宽，标题和右侧的快捷键就会被拉成天各一方
        inner_w = max(10, box_w - 4)
        for i in range(list_h):
            idx = pk.scroll + i
            if idx >= len(rows):
                break
            item = rows[idx]
            selected = idx == pk.index
            pointer = "▸ " if selected else "  "
            tail = item.marker or item.hint
            text = truncate_display(item.label, max(8, inner_w - display_width(tail) - 4))
            gap = max(1, inner_w - display_width(text) - display_width(tail) - 2)
            line = pointer + text + " " * gap + tail     # 提示右对齐到浮层内边缘
            attr = self.a_focus if selected else (self.a_input if item.marker else self.a_dim)
            self._put(y + 2 + i, x + 2, pad_display(line, inner_w), attr)

        # 底部提示（借最后一行边框上面的空间）
        more = len(rows) - (pk.scroll + list_h)
        tip = "↑↓ 选择 · Enter 确认 · Esc 取消" + (f" · 还有 {more} 项" if more > 0 else "")
        self._put(y + box_h - 2, x + 2, truncate_display(tip, box_w - 4), self.a_dim)

    def _picker_key(self, ch: object) -> None:
        """选择器打开时，按键先给它。"""
        pk = self.picker
        if pk is None:
            return
        if isinstance(ch, int):
            if ch == curses.KEY_UP:
                pk.move(-1)
            elif ch == curses.KEY_DOWN:
                pk.move(1)
            elif ch == curses.KEY_PPAGE:
                pk.move(0, -10)
            elif ch == curses.KEY_NPAGE:
                pk.move(0, 10)
            elif ch in (curses.KEY_ENTER, curses.KEY_BACKSPACE):
                if ch == curses.KEY_ENTER:
                    self._picker_confirm()
                else:
                    pk.query = pk.query[:-1]
                    pk.index = 0
            elif ch == curses.KEY_MOUSE:
                self._picker_mouse()
        else:
            if ch in ("\n", "\r"):
                self._picker_confirm()
            elif ch == "\x1b":          # Esc 取消
                self.picker = None
            elif ch in ("\x7f", "\x08"):
                pk.query = pk.query[:-1]
                pk.index = 0
            elif ch == "\x0c":          # Ctrl+L：重绘（浮层开着时也得能用）
                self._redraw()
            elif ch == "\x15":          # Ctrl+U 清空筛选
                pk.query = ""
                pk.index = 0
            elif ch.isprintable():
                pk.query += ch
                pk.index = 0                  # 每次输入都回到第一项，符合直觉
        self._dirty = True

    def _picker_confirm(self) -> None:
        pk = self.picker
        if pk is None:
            return
        self.picker = None                    # 先关掉：回调里可能又开一个新的
        self.busy_note = "处理中…"
        pk.confirm()
        self.busy_note = ""
        self._dirty = True

    def _picker_mouse(self) -> None:
        pk = self.picker
        if pk is None:
            return
        try:
            _, mx, my, _, bstate = curses.getmouse()
        except curses.error:
            return
        if bstate & (curses.BUTTON4_PRESSED | getattr(curses, "BUTTON4_CLICKED", 0)):
            pk.move(-1)
        elif bstate & (curses.BUTTON5_PRESSED | getattr(curses, "BUTTON5_CLICKED", 0)):
            pk.move(1)
        elif bstate & curses.BUTTON1_CLICKED:
            row = self.picker_row_at(my)
            if row is not None:
                pk.index = row
                self._picker_confirm()

    def _picker_row_at(self, my: int) -> int | None:
        """屏幕行 → 候选下标（点空白处返回 None）。"""
        scr = self.stdscr
        pk = self.picker
        if scr is None or pk is None:
            return None
        try:
            h, _ = scr.getmaxyx()
        except curses.error:
            return None
        rows = pk.rows()
        box_h = max(5, min(max(5, int(h * 0.7)), len(rows) + 4))
        y = max(0, (h - box_h) // 2)
        idx = pk.scroll + (my - (y + 2))
        if y + 2 <= my < y + 2 + max(1, box_h - 4) and 0 <= idx < len(rows):
            return idx
        return None

    # ------------------------------------------------------------ 鼠标
    def _on_mouse(self) -> None:
        """鼠标事件。

        滚轮按**指针所在的区**决定滚谁——这比"先点一下再滚"符合直觉，
        而且 `BUTTON4/5` 事件本身就带着坐标，不需要跟踪悬停。
        """
        try:
            _, mx, my, _, bstate = curses.getmouse()
        except curses.error:
            return
        in_help = bool(self.help_w) and mx >= self.main_w
        wheel_up = bstate & (curses.BUTTON4_PRESSED | getattr(curses, "BUTTON4_CLICKED", 0))
        wheel_down = bstate & (curses.BUTTON5_PRESSED | getattr(curses, "BUTTON5_CLICKED", 0))
        if wheel_up or wheel_down:
            self.focus = FOCUS_HELP if in_help else FOCUS_MAIN
            self._scroll(3 if wheel_up else -3)
        elif bstate & (curses.BUTTON1_CLICKED | curses.BUTTON1_PRESSED):
            if in_help:
                self.focus = FOCUS_HELP
            else:
                self.focus = FOCUS_MAIN
                if my == self._input_row():
                    self._cursor_from_click(mx)
        self._dirty = True

    def _cursor_from_click(self, mx: int) -> None:
        """点输入行 → 把光标挪到点中的位置。"""
        col = max(0, mx - display_width(PROMPT)) + self.input_offset
        self.cursor = boundary_at_or_before("".join(self.buf), col)

    def _input_row(self) -> int:
        scr = self.stdscr
        if scr is None:
            return 0
        try:
            h, _ = scr.getmaxyx()
        except curses.error:
            return 0
        return max(0, h - 2)

    def _resize(self) -> None:
        scr = self.stdscr
        if scr is None:
            return
        try:
            h, w = scr.getmaxyx()
        except curses.error:
            return
        self.width = max(1, w)
        self.view_h = max(1, h - 3)          # 表头 1 行 + 输入行 + 状态栏
        self.scroll = max(0, min(self.scroll, max(0, len(self._lines) - self.view_h)))
        self.help_scroll = max(0, min(self.help_scroll, self._help_max_scroll()))
        self._built_width = -1  # 宽度变了，所有折行缓存作废
        self._help_cache = (-1, [])
        scr.clearok(True)
        self._dirty = True

    def _redraw(self) -> None:
        scr = self.stdscr
        if scr is None:
            return
        scr.clearok(True)
        self._built_width = -1
        self._dirty = True

    # ------------------------------------------------------------ 绘制
    def _put(self, row: int, col: int, text: str, attr: int = 0) -> None:
        """写一段文本，自动按显示宽度裁剪并吞掉 curses 的边角报错。"""
        scr = self.stdscr
        if scr is None or not text:
            return
        try:
            h, w = scr.getmaxyx()
        except curses.error:
            return
        if row < 0 or row >= h or col < 0 or col >= w:
            return
        try:
            # 右下角最后一个字符写不进去是 curses 的常态，不是错误
            scr.addstr(row, col, truncate_display(text, w - col, ellipsis=""), attr)
        except curses.error:
            pass

    def _draw(self) -> None:
        scr = self.stdscr
        if scr is None:
            return
        try:
            h, w = scr.getmaxyx()
        except curses.error:
            return
        resized = (h, w) != self._scr_size
        if resized:
            self._scr_size = (h, w)
            self._resize()
        # 计时（启动中/运行中的秒数）会自己变，所以这两种状态下每帧都要重画
        ticking = self.busy or self.state == STATE_BOOT
        if not (self._dirty or resized or ticking):
            return

        self.width = max(1, w)
        # 版面自上而下：表头 / 对话区 / 输入行 / 状态栏；右侧是整高的说明面板
        main_w, help_w = self._layout(self.width)
        view_h = max(1, h - 3)
        # 先定几何再重排：_rebuild_lines 按 self.main_w 折行
        self.main_w, self.help_w, self.view_h = main_w, help_w, view_h
        if self._dirty or self._built_width != main_w:
            self._rebuild_lines()
        max_scroll = max(0, len(self._lines) - view_h)
        self.scroll = max(0, min(self.scroll, max_scroll))

        scr.erase()
        self._draw_header(0, main_w)
        top = max(0, len(self._lines) - view_h - self.scroll)
        for i in range(view_h):
            idx = top + i
            if idx >= len(self._lines):
                break
            text, attr = self._lines[idx]
            self._put(1 + i, 0, text, attr)

        cursor_col = 0
        if h >= 3:
            cursor_col = self._draw_input(h - 2, main_w)
            self._draw_status(h - 1, main_w)
        if help_w:
            self._draw_help(main_w, help_w, h)
        if self.picker is not None:
            self._draw_picker(h, w)

        try:
            scr.move(max(0, h - 2), max(0, min(cursor_col, main_w - 1)))
        except curses.error:
            pass
        scr.noutrefresh()
        curses.doupdate()
        self._dirty = False

    def _draw_header(self, row: int, width: int) -> None:
        """顶部信息条：左边是身份，右边是模型 / 会话 / 上下文用量。

        与 web 版的顶栏同一个位置、同一类信息；原来这些挤在底部状态栏里，
        加了右侧面板之后底部只剩"当前状态"，分工更清楚。
        """
        left = "DSH 对话"
        if self.cwd:
            name = self.cwd.rstrip("/").rsplit("/", 1)[-1] or self.cwd
            left += f" · {name}"
        if self.focus == FOCUS_MAIN:
            left += "  ◂焦点"
        usage = ""
        if self.size:
            pct = int(self.used * 100 / self.size) if self.size else 0
            usage = f"上下文 {_fmt_tokens(self.used)}/{_fmt_tokens(self.size)} {pct}%"
        # 加右侧面板之后主栏变窄了，三个字段不一定都放得下。**降级而不是丢弃**：
        # 先砍会话号、再砍模型名，用量留到最后——它每轮都在变，最该看见。
        # （一开始这里是"放不下就整块不画"，结果窄终端上用量直接消失了。）
        for keep in ((self.model, self.sid[:8] if self.sid else "", usage),
                     (self.model, "", usage),
                     ("", "", usage)):
            right = " · ".join(p for p in keep if p)
            lw, rw = display_width(left), display_width(right)
            if not right:
                break
            if lw + rw + 2 <= width:
                self._put(row, 0, pad_display(left + " " * (width - lw - rw) + right, width),
                          self.a_header)
                return
        # 连"只剩用量"都放不下：把左边裁短，用量右对齐保住
        lw, rw = display_width(left), display_width(usage)
        if usage and rw + 2 < width:
            head = truncate_display(left, max(0, width - rw - 1))
            line = head + " " * max(1, width - display_width(head) - rw) + usage
        else:
            line = left
        self._put(row, 0, pad_display(line, width), self.a_header)

    def _draw_input(self, row: int, width: int) -> int:
        """画输入行，返回光标应处的列。"""
        pw = display_width(PROMPT)
        avail = width - pw
        if avail <= 0:
            self._put(row, 0, PROMPT, self.a_input)
            return 0
        text = "".join(self.buf)
        cur_col = display_width("".join(self.buf[: self.cursor]))
        # 让光标始终留在可见区内，否则左右移动时看不见自己在哪
        if cur_col < self.input_offset:
            self.input_offset = cur_col
        if cur_col > self.input_offset + avail - 1:
            self.input_offset = boundary_at_or_before(text, max(0, cur_col - avail + 1))
        self._put(row, 0, PROMPT, self.a_input | curses.A_BOLD)
        self._put(row, pw, slice_display(text, self.input_offset, avail), self.a_input)
        return pw + (cur_col - self.input_offset)

    def _draw_status(self, row: int, width: int) -> None:
        # 快捷键不再铺在这里——右侧面板一直显示完整清单，状态栏只留"当前状态"。
        right = "F2 命令 · Tab 切焦点 · F1 面板"
        left = self._status_left()
        lw, rw = display_width(left), display_width(right)
        if lw + rw + 1 <= width:
            line = left + " " * (width - lw - rw) + right
        else:
            line = left
        self._put(row, 0, pad_display(line, width), self.a_status)

    def _status_left(self) -> str:
        if self.state == STATE_BOOT:
            label = f"[启动中 {time.monotonic() - self._t0:.0f}s]"
        elif self.state == STATE_FAILED:
            label = "[启动失败]"
        elif self.state == STATE_EXITED:
            label = "[harness 已退出]"
        elif self.busy:
            label = f"[运行中 {time.monotonic() - self.busy_since:.1f}s]"
        elif self.busy_note:
            label = "[处理中]"
        else:
            label = "[就绪]"
        parts = [label]
        if self.busy_note:
            parts.append(self.busy_note)
        if self.focus == FOCUS_HELP:
            parts.append("焦点：说明面板")
        if not self.show_thoughts:
            parts.append("思考已隐藏")
        if self.scroll:
            parts.append(f"↑回滚 {self.scroll} 行")
        if self.stderr_count and self.state in (STATE_FAILED, STATE_EXITED):
            parts.append(f"stderr {self.stderr_count} 行")
        return " · ".join(parts)

    def exit_code(self) -> int:
        """会话建立过就算成功；否则说明 harness 根本没起来。"""
        return 0 if self.session_established else 1


def _model_label(options: list) -> str:
    """从 session/new 的 configOptions 里挖出模型名。

    ``currentValue`` 是形如 ``["deepseek-official","deepseek-v4-flash"]`` 的 JSON 串，
    拿它去 options（可能是扁平的，也可能按 group 套一层）里换成人类可读的名字。
    """
    entry = None
    for opt in options:
        if not isinstance(opt, dict):
            continue
        if opt.get("category") == "model" or opt.get("id") == "model":
            entry = opt
            break
    if entry is None:
        return ""
    current = entry.get("currentValue")
    flat: list[dict] = []

    def collect(items: object) -> None:
        for it in items or []:  # type: ignore[union-attr]
            if not isinstance(it, dict):
                continue
            if isinstance(it.get("options"), list):
                collect(it["options"])
            elif it.get("value") is not None:
                flat.append(it)

    collect(entry.get("options"))
    for it in flat:
        if str(it.get("value")) == str(current):
            return str(it.get("name") or current)
    # 匹配不上就退回原始值：至少还能看出用的是哪个模型
    text = str(current or "")
    if text.startswith("["):
        try:
            parts = json.loads(text)
            if isinstance(parts, list) and parts:
                return str(parts[-1])
        except ValueError:
            pass
    return text


# ---------------------------------------------------------------- 命令行


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m dsh_console.tui",
        description="DSH 控制台 TUI：在终端里跟 harness（dsh --profile acp）对话。",
        epilog=(
            "快捷键：Enter 发送 · Ctrl+C 取消本轮/空闲时退出 · Ctrl+D 退出 · "
            "↑↓/PgUp/PgDn 滚动 · Ctrl+L 重绘 · Ctrl+U 清空输入行"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--cwd",
        metavar="DIR",
        default=os.getcwd(),
        help="会话工作目录（agent 在这个目录里干活），默认当前目录",
    )
    parser.add_argument(
        "--dsh",
        metavar="PATH",
        default=None,
        help="dsh 可执行文件路径，默认取 PATH 里的 dsh",
    )
    parser.add_argument(
        "--profile",
        metavar="NAME",
        default=PROFILE,
        help=f"harness profile 名，默认 {PROFILE}",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)  # --help / 参数错误由 argparse 负责退出

    cwd = os.path.abspath(os.path.expanduser(args.cwd))
    if not os.path.isdir(cwd):
        print(f"错误：--cwd 不是一个目录：{cwd}", file=sys.stderr)
        return 2
    # 显式给路径就用路径（也允许只给个名字，再去 PATH 里找一次），否则直接查 PATH
    if args.dsh:
        dsh = shutil.which(args.dsh) or args.dsh
    else:
        dsh = shutil.which("dsh") or ""
    if not dsh:
        print("错误：PATH 里找不到 dsh，用 --dsh PATH 指定它的位置。", file=sys.stderr)
        return 2
    if not (os.path.isfile(dsh) and os.access(dsh, os.X_OK)):
        print(f"错误：dsh 不是可执行文件：{dsh}", file=sys.stderr)
        return 2

    # get_wch() 要靠 locale 才会把 UTF-8 字节解成宽字符；C/POSIX 下中文输入会散架。
    try:
        locale.setlocale(locale.LC_ALL, "")
    except locale.Error:
        try:
            locale.setlocale(locale.LC_ALL, "C.UTF-8")
        except locale.Error:
            pass

    app = TuiApp(dsh=dsh, profile=args.profile, cwd=cwd)
    try:
        curses.wrapper(app.run)
    except KeyboardInterrupt:
        pass  # 退出途中又中一次 Ctrl+C：正常，忽略
    except curses.error as exc:
        print(f"错误：当前终端不支持 curses（TERM={os.environ.get('TERM', '')}）：{exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - 界面崩了也要给人话，不要甩堆栈
        print(f"TUI 异常退出：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        app.shutdown()  # 双保险：任何路径退出都不留孤儿 dsh 进程
    return app.exit_code()


if __name__ == "__main__":
    sys.exit(main())
