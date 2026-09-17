#!/usr/bin/env python3
"""DSH 控制台 · 原生对话前端（GUI 版的 ``--native`` 模式）。

⚠️ 这**不是**「GUI 版」的默认形态。默认的 ``dsh_console.gui`` 把 harness 自己的
web UI 装进原生窗口——那样界面与功能才能和 web 版**完全一致**（见 :mod:`dsh_console.gui`）。
本模块是另一条路：**不用 QtWebEngine、也不依赖 web 服务**的原生客户端。

什么时候用它：

* web 服务没在跑，或者不想为一个聊天窗口多起一个浏览器引擎；
* 想直接对着 ``dsh --profile acp``（标准 ACP v1）说话，不经过 web 层；
* 排查"问题出在 web UI 还是 harness"——它绕开整个前端。

启动::

    python -m dsh_console.gui --native
    ... --native --cwd ~/proj --theme daylight

设计取舍（都是实测撞过的墙，改之前先看这里）:

* **界面全部是原生 Qt 控件**：这条路的卖点就是轻，引 QtWebEngine 就自相矛盾了。
* **对话区是 QTextBrowser + 增量插入**。QTextCursor 停在文末，来一段插一段，
  而不是把整篇对话重新拼成 HTML 再 ``setHtml`` —— 后者每来一个 chunk 都要重新解析、
  重新排版整篇文档，流式输出时肉眼可见地卡。
  唯一的折中是**攒一帧再插**（见 :data:`STREAM_FLUSH_MS`）：Qt 的富文本每次插入都要
  重排被改动的段落，实测单个 5 万字段落插一次要 10 ms，逐 chunk 直插 2000 次就是 20 秒。
* **主题只在启动时确定**（``--theme``）。运行中换主题意味着把所有历史消息重新渲染一遍，
  与"只追加"的设计冲突；宁可少一个功能，也不要一个会卡住的开关。
* **ACP 回调在读取线程里跑**，一律经 :class:`AcpBridge` 的信号转到主线程再碰控件。
"""

from __future__ import annotations

import argparse
import html
import json
import sys
import time
from pathlib import Path
from typing import Any

try:
    from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot
    from PySide6.QtGui import (
        QColor,
        QFont,
        QKeySequence,
        QShortcut,
        QTextBlockFormat,
        QTextCharFormat,
        QTextCursor,
    )
    from PySide6.QtWidgets import (
        QApplication,
        QCheckBox,
        QComboBox,
        QFrame,
        QHBoxLayout,
        QLabel,
        QMainWindow,
        QPlainTextEdit,
        QProgressBar,
        QPushButton,
        QSplitter,
        QTextBrowser,
        QVBoxLayout,
        QWidget,
    )
except ImportError as exc:  # pragma: no cover - 只在解释器选错时触发
    raise SystemExit(
        "这个前端需要 Python 3.14 + PySide6 6.11，当前解释器不满足：\n"
        f"  {sys.executable}\n"
        f"  {type(exc).__name__}: {exc}\n"
        "换个解释器再跑：DSH_CONSOLE_PYTHON=/path/to/python ./run.sh --gui --native"
    ) from exc

from . import themes
from .screenfit import apply_screen_fit
from .acp_client import (
    START_TIMEOUT,
    AcpClient,
    ToolCall,
    _flatten_content,
    default_workspace,
    find_dsh,
)
from .themes import Theme
from .ui.components import Pill
from .workers import drain, run_async

#: 界面里显示思考与否的默认值（模型思考往往比正文还长，给个开关）。
SHOW_THOUGHT_DEFAULT = True

#: 单轮提示词的上限等待（秒）。ACP 的 prompt 请求会一直挂到本轮结束，
#: 正常情况下永不超时；给个 1 小时纯粹是为了不在客户端侧留下永久挂起的等待。
PROMPT_TIMEOUT = 3600.0

#: 流式文本的合并窗口（毫秒）。为什么需要它，实测数据（本机 1080×780、offscreen）：
#:
#:     QTextEdit/QTextBrowser 每次 insertText 都要重排**整个被改动的段落**
#:     并重算滚动范围：一个 5 万字的段落追加一次要 10 ms，
#:     而 100 段 × 500 字同样总量的文档只要 0.9 ms，QPlainTextEdit 更是 0.11 ms。
#:
#: 也就是说"每个 chunk 直接插一次"在单个超长段落上会退化成 10 ms/chunk，
#: 2000 个 chunk 就卡 20 秒。所以视图内部把 chunk 先攒进缓冲，最多每
#: STREAM_FLUSH_MS 往文档里插一次（同段落的连续 chunk 合并成一次 insertText）——
#: 依然是增量追加、绝不整篇重排，只是把 Qt 的重排次数从"每 chunk"降到"每帧"。
STREAM_FLUSH_MS = 30

#: 合并窗口的上限。插入越贵窗口越长（自适应，见 ConversationView.flush_stream）：
#: 宁可让文字以 4 Hz 冒出来，也不要让界面卡成幻灯片。
STREAM_FLUSH_MAX_MS = 250

#: 缓冲超过这么多字符就先插一次，避免超长段落全堆在一次插入里。
STREAM_FLUSH_CHARS = 2000

#: 关窗时留给 ``dsh`` 收尾的时间（秒）。
CLOSE_GRACE = 6.0

#: 出错时往界面上贴的 stderr 行数。
STDERR_TAIL_LINES = 15

#: 控制台的配置文件（存主题）。这里直接读 JSON 而不是 import ui.main_window：
#: 那个模块会把整个控制台界面（9 个页面）拖进来，一个聊天窗口不需要。
CONFIG_PATH = Path.home() / ".config" / "dsh-console" / "config.json"


def _config_theme() -> str:
    """沿用控制台里选过的主题；读不到就用默认。"""
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return str(cfg.get("theme") or themes.DEFAULT_THEME)
    except Exception:
        return themes.DEFAULT_THEME


def _short(session_id: str | None, n: int = 8) -> str:
    return (session_id or "")[:n] or "—"


def _fmt_tokens(n: int) -> str:
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def _tail(text: str, lines: int = STDERR_TAIL_LINES, width: int = 400) -> str:
    """取尾部若干行，并裁掉过宽的行（stderr 里有 ANSI/巨长堆栈时界面会被撑爆）。"""
    rows = [r[:width] for r in (text or "").splitlines() if r.strip()][-lines:]
    return "\n".join(rows)


# --------------------------------------------------------------------------- 桥
class AcpBridge(QObject):
    """把读取线程里的 ACP 回调转成 Qt 信号。

    ⚠️ 所有 ``on_*`` 回调都**在读取线程里执行**，所以这里只允许 emit，
    绝不能直接改控件——Qt 会把跨线程的信号投递排进主线程的事件队列。
    """

    update = Signal(object)          # session/update 的 update 字典
    exited = Signal(int)             # 服务端进程退出码
    permission = Signal(object)      # 权限请求（仅用于显示，见 on_permission）
    turn_done = Signal(str, object)  # (stopReason, Exception|None)

    @Slot(object)
    def on_update(self, update: dict) -> None:
        self.update.emit(update)

    @Slot(int)
    def on_exit(self, code: int) -> None:
        self.exited.emit(code)

    @Slot(object)
    def on_permission(self, params: dict) -> None:
        """报告权限请求，并**故意返回 None**。

        ``AcpClient`` 拿到 None 会退回自己的 ``permission_policy``（默认 allow），
        也就是由协议层按策略自动选一个 allow 项。这里不自己挑选项，是为了不把
        "什么算允许"的规则复制成两份——界面上只负责让用户看见发生了什么。
        """
        self.permission.emit(params)
        return None

    def on_turn_done(self, reason: str, err: Exception | None) -> None:
        self.turn_done.emit(reason, err)


# ------------------------------------------------------------------- 对话视图
class ConversationView(QTextBrowser):
    """对话区：用户 / 正文 / 思考 / 工具调用 分区显示。

    渲染方式是**只追加**：内部维护一个停在文末的 ``QTextCursor``，流式文本攒一小会儿
    再 ``insertText``（见 :meth:`flush_stream`），工具行则用块号就地重画。
    QTextCursor 会随文档编辑自动修正位置，所以回头改写某个工具行不会让流式游标跑偏。
    """

    tool_clicked = Signal(str)   # toolCallId

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme = theme
        self.setObjectName("Conversation")
        self.setReadOnly(True)
        self.setFrameShape(QFrame.NoFrame)
        # 锚点是我们自己的交互信号，不能让 QTextBrowser 真去导航（会清空视图）
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setTextInteractionFlags(
            Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard | Qt.LinksAccessibleByMouse
        )
        self.document().setDocumentMargin(18)
        # 主题 QSS 把 QTextEdit 统一设成了等宽 12px（那是给日志/代码框用的）。
        # 对话正文用等宽中文字体既难读又浪费宽度，这里按 ID 覆盖回正文字体。
        self.setStyleSheet(
            f"#Conversation {{ background: {theme.surface}; border: 1px solid {theme.border};"
            " border-radius: 10px;"
            ' font-family: "Noto Sans CJK SC", "Source Han Sans SC", "Microsoft YaHei",'
            ' "PingFang SC", sans-serif;'
            " font-size: 13px; }"
        )

        self._cursor = QTextCursor(self.document())
        self._cursor.movePosition(QTextCursor.End)
        self._mode = ""              # 正在追加的段落：body / thought / ""（未开始）
        self._head_open = False      # 本轮是否已经插过 "DSH" 抬头
        self._tool_blocks: dict[str, int] = {}

        # 流式缓冲：见 STREAM_FLUSH_MS 的说明。这里存 [[mode, text], ...]，
        # 相邻同段落的片段直接拼接，一次 flush 只做几次 insertText。
        self._pending: list[list[str]] = []
        self._pending_chars = 0
        self._flush_ms = STREAM_FLUSH_MS
        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.timeout.connect(self.flush_stream)
        #: 自检用：真正落到文档上的插入次数（对比 chunk 数就能看出合并效果）
        self.stream_flushes = 0

        # 自动跟随：只有当视图本来就贴在底部时才跟着新内容滚，
        # 否则用户往上翻历史会被一直拽回底部（流式输出时尤其难受）。
        self._follow = True
        bar = self.verticalScrollBar()
        bar.rangeChanged.connect(self._on_range_changed)
        bar.valueChanged.connect(self._on_scrolled)
        self.anchorClicked.connect(self._on_anchor)

        self._make_formats()

    # ------------------------------------------------------------ 格式
    def _make_formats(self) -> None:
        """主题色只在启动时确定，所以字符/段落格式一次性做好反复用。

        每次 append 都新建 QTextCharFormat 也不是不行，但流式场景下每个 chunk
        都来一次就是白白烧 CPU；这里缓存成属性，追加路径上零分配。
        """
        t = self.theme

        self.f_body = QTextCharFormat()
        self.f_body.setForeground(QColor(t.text))

        self.f_thought = QTextCharFormat()
        self.f_thought.setForeground(QColor(t.text_faint))
        self.f_thought.setFontItalic(True)

        self.f_head_user = QTextCharFormat()
        self.f_head_user.setForeground(QColor(t.accent))
        self.f_head_user.setFontWeight(QFont.DemiBold)

        self.f_head_agent = QTextCharFormat()
        self.f_head_agent.setForeground(QColor(t.text_dim))
        self.f_head_agent.setFontWeight(QFont.DemiBold)

        self.b_user = QTextBlockFormat()
        self.b_user.setBackground(QColor(t.surface_alt))
        self.b_user.setTopMargin(2)
        self.b_user.setBottomMargin(8)
        self.b_user.setLeftMargin(10)
        self.b_user.setRightMargin(10)

        self.b_head = QTextBlockFormat()
        self.b_head.setTopMargin(12)
        self.b_head.setBottomMargin(2)

        self.b_body = QTextBlockFormat()
        self.b_body.setTopMargin(1)
        self.b_body.setBottomMargin(6)

        self.b_thought = QTextBlockFormat()
        self.b_thought.setTopMargin(1)
        self.b_thought.setBottomMargin(6)
        self.b_thought.setLeftMargin(10)

        self.b_tool = QTextBlockFormat()
        self.b_tool.setTopMargin(3)
        self.b_tool.setBottomMargin(3)
        self.b_tool.setLeftMargin(10)

        self.b_notice = QTextBlockFormat()
        self.b_notice.setTopMargin(4)
        self.b_notice.setBottomMargin(4)
        self.b_notice.setLeftMargin(10)
        self.b_notice.setRightMargin(10)
        self.b_notice.setBackground(QColor(t.surface_alt))

    # ------------------------------------------------------------ 滚动
    def _on_range_changed(self, _lo: int, hi: int) -> None:
        if self._follow:
            bar = self.verticalScrollBar()
            if bar.value() != hi:
                bar.setValue(hi)

    def _on_scrolled(self, value: int) -> None:
        bar = self.verticalScrollBar()
        self._follow = value >= bar.maximum() - 12

    def _on_anchor(self, url) -> None:  # noqa: ANN001 - QUrl
        text = url.toString()
        if text.startswith("tool:"):
            self.tool_clicked.emit(text[len("tool:"):])

    @Slot()
    def scroll_to_bottom(self) -> None:
        bar = self.verticalScrollBar()
        self._follow = True
        bar.setValue(bar.maximum())

    # ------------------------------------------------------------ 追加原语
    def _block(self, fmt: QTextBlockFormat, char: QTextCharFormat) -> None:
        """开一个新段落并把游标放进去。

        ``insertBlock`` 会继承上一个段落的格式，所以每次都必须把格式显式写全，
        否则正文会莫名其妙带上用户消息的底色。

        例外：文档自带的第一个空段落不新开块，直接改它的格式，
        否则对话顶部会永远吊着一行空白。
        """
        block = self._cursor.block()
        if block.blockNumber() == 0 and self.document().isEmpty():
            self._cursor.setBlockFormat(fmt)
            self._cursor.setBlockCharFormat(char)
            return
        self._cursor.insertBlock(fmt, char)

    def _text(self, text: str, fmt: QTextCharFormat) -> None:
        if text:
            self._cursor.insertText(text, fmt)

    # ------------------------------------------------------------ 对外接口
    def add_user(self, text: str) -> None:
        """用户消息：抬头 "你" + 带底色的正文块。"""
        self.flush_stream()          # 先吐缓冲，保证顺序 = 到达顺序
        self._mode = ""
        self._head_open = False
        self._block(self.b_head, self.f_head_user)
        self._text("你", self.f_head_user)
        self._block(self.b_user, self.f_body)
        self._text(text, self.f_body)

    def _ensure_agent_head(self) -> None:
        """本轮第一次产出内容时才插 "DSH" 抬头。

        提前插的话，一轮里如果模型只调工具不说话，就会在界面上留下一堆空抬头。
        """
        if not self._head_open:
            self._head_open = True
            self._block(self.b_head, self.f_head_agent)
            self._text("DSH", self.f_head_agent)

    def add_body(self, chunk: str) -> None:
        """助手正文（流式）：进缓冲，由 flush_stream 合并后落盘。"""
        if chunk:
            self._queue("body", chunk)

    def add_thought(self, chunk: str) -> None:
        """模型思考（流式）：弱化成灰色斜体。"""
        if chunk:
            self._queue("thought", chunk)

    # ------------------------------------------------------------ 流式缓冲
    def _queue(self, mode: str, chunk: str) -> None:
        if self._pending and self._pending[-1][0] == mode:
            self._pending[-1][1] += chunk
        else:
            self._pending.append([mode, chunk])
        self._pending_chars += len(chunk)
        if self._pending_chars >= STREAM_FLUSH_CHARS:
            # 一次来太多（例如粘贴/回放）就别等定时器了，立刻插一次
            self.flush_stream()
            return
        if not self._flush_timer.isActive():
            # 注意是"启动"不是"重启"：持续来流时也要保证 STREAM_FLUSH_MS 内至少吐一次，
            # 否则文字会一直憋到模型说完才出现。
            self._flush_timer.start(self._flush_ms)

    @Slot()
    def flush_stream(self) -> None:
        """把缓冲里的流式文本插进文档（同段落的连续 chunk 合并成一次插入）。"""
        self._flush_timer.stop()
        if not self._pending:
            return
        pending, self._pending = self._pending, []
        self._pending_chars = 0
        t0 = time.perf_counter()
        for mode, text in pending:
            if mode == "body":
                self._insert_body(text)
            else:
                self._insert_thought(text)
        self.stream_flushes += 1
        cost_ms = (time.perf_counter() - t0) * 1000
        # 自适应：这次插入花了多少，下次就攒到它的 3 倍时间再插，
        # 让排版最多占掉约 1/3 的时间片。段落越长重排越贵，窗口自动变大。
        self._flush_ms = int(
            max(STREAM_FLUSH_MS, min(STREAM_FLUSH_MAX_MS, cost_ms * 3))
        )

    def _insert_body(self, text: str) -> None:
        self._ensure_agent_head()
        if self._mode != "body":
            self._mode = "body"
            self._block(self.b_body, self.f_body)
        self._text(text, self.f_body)

    def _insert_thought(self, text: str) -> None:
        self._ensure_agent_head()
        if self._mode != "thought":
            self._mode = "thought"
            self._block(self.b_thought, self.f_thought)
            self._text("💭 思考  ", self.f_thought)
        self._text(text, self.f_thought)

    def add_tool(self, tc: ToolCall) -> None:
        """工具调用：一行标题 + 状态徽章，整行是锚点，点开看输出。"""
        self.flush_stream()
        self._ensure_agent_head()
        self._mode = ""
        self._block(self.b_tool, self.f_body)
        self._cursor.insertHtml(self._tool_html(tc))
        self._tool_blocks[tc.tool_call_id] = self._cursor.blockNumber()

    def update_tool(self, tc: ToolCall) -> None:
        """就地重画某个工具行（状态从 in_progress 变成 completed/failed）。

        用**块号**定位而不是字符位置：块号只受"在中间增删段落"影响，而我们只会在
        文末追加段落，所以已记录的块号始终有效。
        """
        n = self._tool_blocks.get(tc.tool_call_id)
        if n is None:
            return
        self.flush_stream()     # 缓冲里的正文在工具行之后，先落地再改这一行
        block = self.document().findBlockByNumber(n)
        if not block.isValid():
            return
        cur = QTextCursor(self.document())
        cur.setPosition(block.position())
        cur.movePosition(QTextCursor.EndOfBlock, QTextCursor.KeepAnchor)
        cur.insertHtml(self._tool_html(tc))   # insertHtml 会替换掉选区

    def _tool_html(self, tc: ToolCall) -> str:
        t = self.theme
        status = tc.status or "in_progress"
        icon, color, label = {
            "completed": ("✓", t.ok, "完成"),
            "failed": ("✗", t.danger, "失败"),
            "in_progress": ("◐", t.accent, "进行中"),
        }.get(status, ("•", t.text_dim, status))
        title = html.escape(tc.title or "（未命名工具）")
        kind = f'<span style="color:{t.text_faint}"> · {html.escape(tc.kind)}</span>' if tc.kind else ""
        return (
            f'<a href="tool:{html.escape(tc.tool_call_id)}" '
            f'style="color:{t.text}; text-decoration:none;">🔧 {title}</a>{kind} '
            f'<span style="color:{color}; font-weight:600;">[{icon} {label}]</span>'
            f'<span style="color:{t.text_faint}"> ›</span>'
        )

    def add_notice(self, text: str, kind: str = "error") -> None:
        """系统提示块：连接失败、进程退出、本轮报错都走这里，保证错误不会被吞掉。"""
        t = self.theme
        color = {"error": t.danger, "warn": t.warn, "info": t.text_dim}.get(kind, t.text_dim)
        icon = {"error": "⚠", "warn": "!", "info": "ℹ"}.get(kind, "•")
        self.flush_stream()
        self._mode = ""
        self._head_open = True     # 提示块自己带抬头，不蹭 "DSH"
        self._block(self.b_notice, self.f_body)
        self._cursor.insertHtml(
            f'<span style="color:{color}; font-weight:600;">{icon} '
            f'{html.escape(text).replace(chr(10), "<br>")}</span>'
        )

    def clear_all(self) -> None:
        self._flush_timer.stop()
        self._pending.clear()          # 清屏时缓冲直接丢掉，不要"复活"到新内容里
        self._pending_chars = 0
        self.clear()
        self._cursor = QTextCursor(self.document())
        self._cursor.movePosition(QTextCursor.End)
        self._tool_blocks.clear()
        self._mode = ""
        self._head_open = False
        self._follow = True


# ------------------------------------------------------------------- 输入框
class PromptEdit(QPlainTextEdit):
    """多行输入框：Enter / Ctrl+Enter 发送，Shift+Enter 换行。"""

    submitted = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        #: 输入法组字中（中文候选还没上屏）。此时回车是"选词"，不能当发送。
        self._preedit = False

    def inputMethodEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self._preedit = bool(event.preeditString())
        super().inputMethodEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        key = event.key()
        if key in (Qt.Key_Return, Qt.Key_Enter):
            if self._preedit or (event.modifiers() & Qt.ShiftModifier):
                super().keyPressEvent(event)
                return
            self.submitted.emit()
            return
        super().keyPressEvent(event)


# ------------------------------------------------------------------- 详情面板
class ToolDetail(QFrame):
    """工具调用详情：入参 + 输出。默认隐藏，点工具行才出现。"""

    closed = Signal()

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme = theme
        self.setObjectName("Card")
        self._tc: ToolCall | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 10, 14, 12)
        root.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(8)
        self.title = QLabel("工具输出")
        self.title.setObjectName("CardTitle")
        self.title.setStyleSheet("background: transparent;")
        self.pill = Pill("—")
        self.pill.set_state("—", theme.text_faint, theme.text)
        self.kind = QLabel("")
        self.kind.setStyleSheet(f"color: {theme.text_faint}; background: transparent;")
        head.addWidget(self.title)
        head.addWidget(self.pill)
        head.addWidget(self.kind)
        head.addStretch(1)
        self.btn_copy = QPushButton("复制输出")
        self.btn_close = QPushButton("收起")
        self.btn_close.setObjectName("Ghost")
        head.addWidget(self.btn_copy)
        head.addWidget(self.btn_close)
        root.addLayout(head)

        self.body = QPlainTextEdit()
        self.body.setReadOnly(True)
        self.body.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.body.setMinimumHeight(90)
        root.addWidget(self.body, 1)

        self.btn_close.clicked.connect(self._on_close)
        self.btn_copy.clicked.connect(self._copy)

    def _on_close(self) -> None:
        self.closed.emit()

    def _copy(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.clipboard().setText(self.body.toPlainText())

    def show_tool(self, tc: ToolCall) -> None:
        self._tc = tc
        self.title.setText(tc.title or "（未命名工具）")
        status = tc.status or "in_progress"
        color = {"completed": self.theme.ok, "failed": self.theme.danger}.get(
            status, self.theme.accent
        )
        label = {"completed": "完成", "failed": "失败", "in_progress": "进行中"}.get(status, status)
        self.pill.set_state(label, color, self.theme.text)
        self.kind.setText(f"kind={tc.kind or '—'}  id={tc.tool_call_id[:8]}")

        parts: list[str] = []
        if tc.raw_input:
            try:
                parts.append("── 入参 ──\n" + json.dumps(tc.raw_input, ensure_ascii=False, indent=2))
            except (TypeError, ValueError):
                parts.append("── 入参 ──\n" + str(tc.raw_input))
        parts.append("── 输出 ──\n" + (tc.output or "（工具还没有返回内容）"))
        self.body.setPlainText("\n\n".join(parts))


# --------------------------------------------------------------------- 主窗口
class ChatWindow(QMainWindow):
    """对话窗口：顶部状态条 + 对话区 + 输入区 + 状态栏。"""

    def __init__(
        self,
        *,
        cwd: str | None = None,
        dsh_bin: str | None = None,
        profile: str = "acp",
        theme_key: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.theme = themes.get_theme(theme_key or _config_theme())
        self.cwd = str(cwd or default_workspace())
        self.dsh_bin = dsh_bin or find_dsh() or "dsh"
        self.profile = profile

        self._client: AcpClient | None = None
        self._session_id: str | None = None
        self._bridge = AcpBridge()
        self._bridge.update.connect(self._on_update)
        self._bridge.exited.connect(self._on_exit)
        self._bridge.permission.connect(self._on_permission)
        self._bridge.turn_done.connect(self._on_turn_done)

        self._tools: dict[str, ToolCall] = {}
        self._detail_id: str | None = None
        self._running = False
        self._booting = False
        self._closing = False
        self._boot_holder: dict[str, Any] = {}   # 启动线程 → 主线程 的对象中转
        self._turn_start = 0.0
        self._last_elapsed = "—"
        self._other_updates: dict[str, int] = {}  # 没处理的 sessionUpdate 计数（排查用）
        self._prompt_thread: Any = None           # prompt_async 的线程（只为持有引用）

        self.setWindowTitle("DSH 对话 · Harness 客户端")
        # 按屏幕自适应（小屏上别让窗口跑到屏幕外，和控制台主窗口同一套逻辑）
        apply_screen_fit(self, (1080, 780), (880, 600))
        self._build()

        # 本轮耗时：运行中每 200 ms 刷一次状态栏
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(200)
        self._tick_timer.timeout.connect(self._tick)

        # 首帧显示后再连 harness：先把窗口画出来，用户不会对着空白屏等握手
        QTimer.singleShot(0, self.connect_harness)

    # ------------------------------------------------------------------ UI
    def _build(self) -> None:
        t = self.theme
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(18, 16, 18, 12)
        root.setSpacing(12)

        # ---- 标题行
        head = QHBoxLayout()
        head.setSpacing(10)
        title = QLabel("DSH 对话")
        title.setObjectName("PageTitle")
        subtitle = QLabel(f"{self.profile} profile · {self.cwd}")
        subtitle.setObjectName("PageSubtitle")
        subtitle.setToolTip(f"工作目录：{self.cwd}\n服务端：{self.dsh_bin} --profile {self.profile}")
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(title)
        col.addWidget(subtitle)
        head.addLayout(col)
        head.addStretch(1)
        self.pill = Pill("连接中…")
        self.pill.set_state("连接中…", t.text_faint, t.text)
        head.addWidget(self.pill)
        root.addLayout(head)

        # ---- 控制条（模型 / 思考强度 / 上下文用量 / 重连）
        bar = QFrame()
        bar.setObjectName("Card")
        row = QHBoxLayout(bar)
        row.setContentsMargins(14, 10, 14, 10)
        row.setSpacing(10)

        self._combos: dict[str, QComboBox] = {}
        self._combo_row = row          # 配置下拉框动态插到这一行
        self.model_label = QLabel("模型")
        self.model_label.setStyleSheet(f"color: {t.text_faint}; background: transparent;")
        self.model_combo = QComboBox()
        self.model_combo.setMinimumWidth(220)
        self.model_combo.setEnabled(False)
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        self._combos["model"] = self.model_combo
        self._build_combo(self.model_combo, None)
        row.addWidget(self.model_label)
        row.addWidget(self.model_combo)

        self.chk_thought = QCheckBox("显示思考")
        self.chk_thought.setChecked(SHOW_THOUGHT_DEFAULT)
        self.chk_thought.setStyleSheet("background: transparent;")
        row.addWidget(self.chk_thought)

        row.addStretch(1)

        self.usage_label = QLabel("上下文 —")
        self.usage_label.setStyleSheet(f"color: {t.text_faint}; background: transparent;")
        self.usage = QProgressBar()
        self.usage.setRange(0, 1000)
        self.usage.setValue(0)
        self.usage.setTextVisible(False)
        self.usage.setFixedHeight(8)
        self.usage.setFixedWidth(150)
        self.usage.setToolTip("上下文用量（来自服务端的 usage_update）")
        row.addWidget(self.usage_label)
        row.addWidget(self.usage)

        self.btn_reconnect = QPushButton("重连")
        self.btn_reconnect.setObjectName("Ghost")
        self.btn_reconnect.setToolTip("重新拉起 dsh --profile acp 并新建会话")
        self.btn_reconnect.clicked.connect(self.connect_harness)
        row.addWidget(self.btn_reconnect)
        root.addWidget(bar)

        # ---- 对话区 + 工具详情
        self.view = ConversationView(t, self)
        self.view.tool_clicked.connect(self._show_tool)
        self.view.setMinimumHeight(240)

        self.detail = ToolDetail(t, self)
        self.detail.closed.connect(self._hide_detail)
        self.detail.setVisible(False)

        self.split = QSplitter(Qt.Vertical)
        self.split.setChildrenCollapsible(False)
        self.split.addWidget(self.view)
        self.split.addWidget(self.detail)
        self.split.setStretchFactor(0, 4)
        self.split.setStretchFactor(1, 1)
        root.addWidget(self.split, 1)

        # ---- 输入区
        box = QFrame()
        box.setObjectName("Card")
        bl = QVBoxLayout(box)
        bl.setContentsMargins(14, 12, 14, 12)
        bl.setSpacing(8)
        self.input = PromptEdit()
        self.input.setPlaceholderText("说点什么…（Enter 或 Ctrl+Enter 发送，Shift+Enter 换行）")
        self.input.setFixedHeight(92)
        self.input.submitted.connect(self.send)
        bl.addWidget(self.input)

        btns = QHBoxLayout()
        btns.setSpacing(10)
        self.hint = QLabel("点击工具行可在下方查看输出")
        self.hint.setObjectName("CardHint")
        self.hint.setStyleSheet(f"color: {t.text_faint}; background: transparent;")
        btns.addWidget(self.hint)
        btns.addStretch(1)
        self.btn_stop = QPushButton("停止")
        self.btn_stop.setObjectName("Danger")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop_turn)
        self.btn_send = QPushButton("发送")
        self.btn_send.setObjectName("Primary")
        self.btn_send.setEnabled(False)
        self.btn_send.clicked.connect(self.send)
        btns.addWidget(self.btn_stop)
        btns.addWidget(self.btn_send)
        bl.addLayout(btns)
        root.addWidget(box)

        self.setCentralWidget(central)

        # ---- 状态栏
        sb = self.statusBar()
        self.lbl_sid = QLabel("session —")
        self.lbl_cwd = QLabel(f"cwd {self.cwd}")
        self.lbl_elapsed = QLabel("耗时 —")
        self.lbl_error = QLabel("")
        self.lbl_error.setStyleSheet(f"color: {t.danger};")
        for w in (self.lbl_sid, self.lbl_cwd, self.lbl_elapsed):
            w.setStyleSheet(f"color: {t.text_faint};")
        sb.addWidget(self.lbl_sid)
        sb.addWidget(self.lbl_cwd)
        sb.addWidget(self.lbl_elapsed)
        sb.addPermanentWidget(self.lbl_error)

        QShortcut(QKeySequence("Ctrl+L"), self, self.view.scroll_to_bottom)
        QShortcut(QKeySequence("Esc"), self, self._hide_detail)

    # ------------------------------------------------------------ 状态显示
    def _set_status(self, text: str, color: str) -> None:
        self.pill.set_state(text, color, self.theme.text)

    def _set_error(self, text: str) -> None:
        self.lbl_error.setText(text)
        self.lbl_error.setToolTip(text)

    def _busy_ui(self, running: bool) -> None:
        self._running = running
        self.btn_send.setEnabled(not running and self._session_id is not None)
        self.btn_stop.setEnabled(running)
        self.input.setEnabled(True)
        if running:
            self._set_status("运行中…", self.theme.accent)
            self._turn_start = time.monotonic()
            self._tick_timer.start()
        else:
            self._tick_timer.stop()

    def _tick(self) -> None:
        if self._running:
            self._last_elapsed = f"{time.monotonic() - self._turn_start:.1f}s"
            self.lbl_elapsed.setText(f"本轮 {self._last_elapsed}")

    # ------------------------------------------------------------ 连接 harness
    def connect_harness(self) -> None:
        """后台线程里拉起 ``dsh --profile acp`` + 建会话；结果经信号回主线程。"""
        if self._closing or self._booting:
            return
        if self._running:
            self._set_error("本轮还没结束，先停止再重连")
            return
        self._booting = True
        self.btn_reconnect.setEnabled(False)
        self.btn_send.setEnabled(False)
        self.model_combo.setEnabled(False)
        self._set_status("连接中…", self.theme.text_faint)
        self._set_error("")
        self.lbl_cwd.setText(f"cwd {self.cwd}")

        old = self._client
        holder: dict[str, Any] = {"old": old}
        self._boot_holder = holder
        cwd, dsh_bin, profile = self.cwd, self.dsh_bin, self.profile
        bridge = self._bridge

        def boot() -> dict:
            if old is not None:
                # 旧进程要先收干净，否则重连会攒下一串 dsh 子进程
                try:
                    old.stop(timeout=3.0)
                except Exception:
                    pass
            client = AcpClient(
                dsh_bin=dsh_bin,
                profile=profile,
                cwd=cwd,
                on_update=bridge.on_update,
                on_permission=bridge.on_permission,
                on_exit=bridge.on_exit,
            )
            holder["client"] = client     # 出错时主线程还能读到 stderr_tail
            client.start(timeout=START_TIMEOUT)
            result = client.request("session/new", {"cwd": cwd, "mcpServers": []})
            return {"client": client, "result": result}

        run_async(boot, self._on_booted, self._on_boot_failed)

    @Slot(object)
    def _on_booted(self, payload: dict) -> None:
        self._booting = False
        client: AcpClient = payload["client"]
        if self._closing:
            # 关窗和握手撞在一起了：控件正在销毁，这里只能把刚拉起来的进程收掉，
            # 否则会留下一个没人管的 dsh 子进程。
            try:
                client.stop(timeout=3.0)
            except Exception:
                pass
            return
        self.btn_reconnect.setEnabled(True)
        client: AcpClient = payload["client"]
        result: dict = payload["result"]
        self._client = client
        self._session_id = str(result.get("sessionId") or "")
        self.lbl_sid.setText(f"session {_short(self._session_id)}")
        self.lbl_sid.setToolTip(self._session_id)
        self._fill_config_options(result.get("configOptions") or [])
        info = client.agent_info or {}
        self._set_status("就绪", self.theme.ok)
        self._set_error("")
        self.view.add_notice(
            f"已连接 {info.get('name', 'dsh')} {info.get('version', '')}"
            f"（session {_short(self._session_id)}，cwd {self.cwd}）",
            "info",
        )
        self.btn_send.setEnabled(True)
        self.input.setFocus()

    @Slot(str)
    def _on_boot_failed(self, message: str) -> None:
        self._booting = False
        if self._closing:
            return
        self.btn_reconnect.setEnabled(True)
        self._set_status("出错", self.theme.danger)
        self._set_error(f"连接失败：{message}")
        holder = self._boot_holder or {}
        client = holder.get("client") or holder.get("old")
        tail = _tail(client.stderr_text() if client is not None else "")
        detail = f"连接 dsh 失败：{message}"
        if tail:
            detail += f"\n\n--- dsh stderr 末尾 ---\n{tail}"
        self.view.add_notice(detail, "error")
        self.view.scroll_to_bottom()

    # ------------------------------------------------------------ 配置下拉框
    def _fill_config_options(self, options: list[dict]) -> None:
        """用 session/new 返回的 configOptions 填下拉框。

        ACP 的 select 项可能是**分组**的（模型按 provider 分组，实测
        ``options[i] = {group, name, options: [...]}``），所以两种形状都要认。
        """
        self._config_options = list(options)
        self._build_combo(self.model_combo, self._find_option("model"))

        # reasoning_effort 这类不在需求里，但既然是同一个协议字段，顺手也做成下拉框，
        # 用户就不用为了换个思考强度去改配置文件。放在模型后面，宽度小一些。
        for cfg in options:
            if cfg.get("category") in ("model", None) or cfg.get("id") == "model":
                continue
            if cfg.get("type") != "select":
                continue
            combo = self._combos.get(str(cfg.get("id")))
            if combo is None:
                label = QLabel(str(cfg.get("name") or cfg.get("id")))
                label.setStyleSheet(
                    f"color: {self.theme.text_faint}; background: transparent;"
                )
                combo = QComboBox()
                combo.setMinimumWidth(120)
                combo.currentIndexChanged.connect(
                    lambda _i, oid=str(cfg.get("id")): self._on_option_changed(oid)
                )
                self._combos[str(cfg.get("id"))] = combo
                # 插在弹性空白之前，避免把"重连"挤出屏幕
                self._combo_row.insertWidget(self._combo_row.count() - 4, label)
                self._combo_row.insertWidget(self._combo_row.count() - 4, combo)
            self._build_combo(combo, cfg)

    def _find_option(self, option_id: str) -> dict | None:
        for cfg in getattr(self, "_config_options", []):
            if str(cfg.get("id")) == option_id:
                return cfg
        return None

    def _build_combo(self, combo: QComboBox, cfg: dict | None) -> None:
        combo.blockSignals(True)     # 重填期间别触发 set_config_option
        combo.clear()
        if not cfg:
            # 还没连上（或服务端没给这个 category）时给一句人话，
            # 空下拉框看起来像坏了。
            combo.addItem("（连接后填充）")
            combo.setEnabled(False)
            combo.blockSignals(False)
            return
        current = str(cfg.get("currentValue") or "")
        for entry in cfg.get("options") or []:
            if entry.get("group") is not None:
                combo.addItem(f"— {entry.get('group')} —")
                idx = combo.count() - 1
                combo.model().item(idx).setEnabled(False)   # 组标题不可选
                for sub in entry.get("options") or []:
                    self._add_combo_item(combo, sub)
            else:
                self._add_combo_item(combo, entry)
        pos = combo.findData(current)
        if pos >= 0:
            combo.setCurrentIndex(pos)
        combo.setEnabled(True)
        combo.blockSignals(False)

    @staticmethod
    def _add_combo_item(combo: QComboBox, entry: dict) -> None:
        name = str(entry.get("name") or entry.get("value") or "")
        desc = str(entry.get("description") or "")
        combo.addItem(name, str(entry.get("value") or ""))
        if desc:
            combo.setItemData(combo.count() - 1, desc, Qt.ToolTipRole)

    def _on_model_changed(self, _index: int) -> None:
        self._on_option_changed("model")

    def _on_option_changed(self, option_id: str) -> None:
        combo = self._combos.get(option_id)
        if combo is None or not combo.isEnabled() or self._session_id is None:
            return
        value = combo.currentData()
        if value is None:
            return          # 落在分组标题上
        sid = self._session_id

        def work() -> dict:
            assert self._client is not None
            return self._client.set_config_option(sid, option_id, str(value))

        def done(result: dict) -> None:
            # 服务端会回一份新的 configOptions，用它校准界面（例如它拒绝了某个值）
            opts = (result or {}).get("configOptions")
            if opts:
                self._config_options = list(opts)
                for cfg in opts:
                    cid = str(cfg.get("id"))
                    if cid in self._combos:
                        self._build_combo(self._combos[cid], cfg)

        def failed(msg: str) -> None:
            self._set_error(f"切换 {option_id} 失败：{msg}")

        run_async(work, done, failed)

    # ------------------------------------------------------------ 发送/停止
    def send(self) -> None:
        text = self.input.toPlainText().strip()
        if not text:
            return
        if self._client is None or self._session_id is None:
            self._set_error("还没连上 harness，点右上角「重连」")
            return
        if self._running:
            return
        self.input.clear()
        self.view.add_user(text)
        self.view.scroll_to_bottom()
        self._last_elapsed = "—"
        self.lbl_elapsed.setText("本轮 0.0s")
        self._busy_ui(True)
        sid = self._session_id
        thread = self._client.prompt_async(
            sid, text, on_done=self._bridge.on_turn_done, timeout=PROMPT_TIMEOUT
        )
        self._prompt_thread = thread   # 持引用，纯粹为了排查方便

    def stop_turn(self) -> None:
        if self._client is not None and self._session_id is not None and self._running:
            self._client.cancel(self._session_id)
            self.hint.setText("已请求取消…")
            self._set_status("取消中…", self.theme.warn)

    @Slot(str, object)
    def _on_turn_done(self, reason: str, err: Exception | None) -> None:
        """一轮结束（正文流已停止）。跑在主线程。"""
        self.view.flush_stream()   # 收尾：把最后攒着的 chunk 立刻显示出来
        if self._running:
            # 计时器每 200ms 才刷一次，最后这一下自己算准，别让"本轮 0.8s"比真实耗时少一截
            self._last_elapsed = f"{time.monotonic() - self._turn_start:.1f}s"
        self._busy_ui(False)
        self.hint.setText("点击工具行可在下方查看输出")
        if err is not None:
            self._set_status("出错", self.theme.danger)
            self._set_error(f"本轮出错：{err}")
            tail = _tail(self._client.stderr_text() if self._client else "")
            detail = f"本轮出错：{type(err).__name__}: {err}"
            if tail:
                detail += f"\n\n--- dsh stderr 末尾 ---\n{tail}"
            self.view.add_notice(detail, "error")
        else:
            label = {"end_turn": "完成", "max_tokens": "达到长度上限", "cancelled": "已取消",
                     "refusal": "被拒绝"}.get(reason, reason or "完成")
            self._set_status("就绪", self.theme.ok if reason != "cancelled" else self.theme.warn)
            self.lbl_elapsed.setText(f"本轮 {self._last_elapsed}（{label}）")
        self.view.scroll_to_bottom()

    # ------------------------------------------------------------ ACP 事件
    @Slot(object)
    def _on_update(self, update: dict) -> None:
        """处理一条 session/update。**主线程**执行。"""
        kind = str(update.get("sessionUpdate") or "")
        if kind == "agent_message_chunk":
            self.view.add_body(_flatten_content(update.get("content")))
        elif kind == "agent_thought_chunk":
            if self.chk_thought.isChecked():
                self.view.add_thought(_flatten_content(update.get("content")))
        elif kind == "tool_call":
            tcid = str(update.get("toolCallId") or "")
            if not tcid:
                return
            tc = ToolCall(
                tool_call_id=tcid,
                title=str(update.get("title") or ""),
                kind=str(update.get("kind") or ""),
                status=str(update.get("status") or ""),
                raw_input=update.get("rawInput") or {},
            )
            self._tools[tcid] = tc
            self.view.add_tool(tc)
            self.view.scroll_to_bottom()
        elif kind == "tool_call_update":
            tcid = str(update.get("toolCallId") or "")
            tc = self._tools.get(tcid)
            if tc is None:      # 没收到过 tool_call 就先补一条，别丢信息
                tc = self._tools[tcid] = ToolCall(tool_call_id=tcid)
            if update.get("status"):
                tc.status = str(update["status"])
            if update.get("title"):
                tc.title = str(update["title"])
            if update.get("rawInput"):
                tc.raw_input = update["rawInput"]
            text = _flatten_content(update.get("content"))
            if text:
                tc.output = (tc.output + text) if tc.output else text
            self.view.update_tool(tc)
            if self._detail_id == tcid and self.detail.isVisible():
                self.detail.show_tool(tc)   # 详情面板开着就跟到最新
        elif kind == "usage_update":
            self._update_usage(int(update.get("used") or 0), int(update.get("size") or 0))
        else:
            # 别的通知（available_commands_update 之类）不影响对话，但要留痕：
            # 只计数 + 打 stderr，避免未知消息被静默吞掉。
            self._other_updates[kind] = self._other_updates.get(kind, 0) + 1
            print(f"[gui] 忽略的 sessionUpdate：{kind}", file=sys.stderr)

    def _update_usage(self, used: int, size: int) -> None:
        if size <= 0:
            self.usage_label.setText(f"上下文 {_fmt_tokens(used)}")
            return
        pct = min(100.0, used * 100.0 / size)
        self.usage.setValue(int(pct * 10))
        self.usage_label.setText(
            f"上下文 {_fmt_tokens(used)}/{_fmt_tokens(size)} · {pct:.0f}%"
        )
        color = self.theme.danger if pct >= 90 else (
            self.theme.warn if pct >= 70 else self.theme.text_faint
        )
        self.usage_label.setStyleSheet(f"color: {color}; background: transparent;")

    @Slot(int)
    def _on_exit(self, code: int) -> None:
        """服务端进程没了。分两种：我们主动关的，和它自己挂的。"""
        if self._closing:
            return
        self._busy_ui(False)
        self._session_id = None
        self.btn_send.setEnabled(False)
        self._set_status("出错", self.theme.danger)
        self._set_error(f"harness 进程退出（code={code}）")
        tail = _tail(self._client.stderr_text() if self._client else "")
        detail = f"harness 进程已退出（code={code}）。点「重连」可以重新拉起。"
        if tail:
            detail += f"\n\n--- dsh stderr 末尾 ---\n{tail}"
        self.view.add_notice(detail, "error")
        self.view.scroll_to_bottom()

    @Slot(object)
    def _on_permission(self, params: dict) -> None:
        """权限请求只做提示：真正的选择交给协议层的 permission_policy（默认允许）。"""
        what = str(params.get("toolCall") or params.get("title") or "工具调用")
        n = len(params.get("options") or [])
        self._set_error(f"已自动授权：{what}（{n} 个选项）")
        print(f"[gui] 权限请求已按策略自动应答：{what}", file=sys.stderr)

    # ------------------------------------------------------------ 工具详情
    def _show_tool(self, tcid: str) -> None:
        tc = self._tools.get(tcid)
        if tc is None:
            return
        self._detail_id = tcid
        self.detail.show_tool(tc)
        if not self.detail.isVisible():
            self.detail.setVisible(True)
            total = max(1, self.split.height())
            self.split.setSizes([int(total * 0.7), int(total * 0.3)])

    def _hide_detail(self) -> None:
        self.detail.setVisible(False)
        self._detail_id = None

    # ------------------------------------------------------------ 收尾
    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """关窗：先取消在跑的一轮，再关会话、停进程。

        ``stop()`` 会阻塞等子进程退出（最多 CLOSE_GRACE 秒）。这里宁可在关窗时
        多等一会儿，也不要留一个孤儿 ``dsh`` 在后台——本机还跑着服务用户会话的
        harness，多出来的进程没人认领最麻烦。
        """
        self._closing = True
        self._tick_timer.stop()
        self.view.flush_stream()
        client = self._client
        if client is not None:
            try:
                if self._running and self._session_id:
                    client.cancel(self._session_id)
                elif self._session_id:
                    # 只在空闲时关会话：harness 正忙着一轮时，session/close 可能要等它
                    # 把手头的活干完，关窗就会卡住。反正接着就 stop 了，会话不会泄漏。
                    client.close_session(self._session_id)
            except Exception:
                pass
            try:
                client.stop(timeout=CLOSE_GRACE)
            except Exception:
                pass
        self._client = None

        # 关窗时握手可能还在后台线程里跑（initialize 要 ~1 秒）。这时 `_client` 还是
        # None，但线程一会儿就会拉起一个 dsh——必须等它落地再收掉，否则用户快速关窗
        # 就会留下一个孤儿进程。drain 是项目里现成的等待手段。
        if self._booting:
            drain(5000)
            late = (self._boot_holder or {}).get("client")
            if late is not None and late is not client:
                try:
                    late.stop(timeout=3.0)
                except Exception:
                    pass
        super().closeEvent(event)


# ----------------------------------------------------------------------- 入口
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m dsh_console.gui",
        description="DSH 控制台 · 对话前端：用原生 Qt 控件跟 DSH Harness 对话（ACP v1）。",
        epilog="示例：python -m dsh_console.gui --cwd ~/proj --theme daylight",
    )
    p.add_argument("--cwd", metavar="DIR", help="会话工作目录（默认本项目根目录）")
    p.add_argument("--dsh", metavar="PATH", help="dsh 可执行文件路径（默认从 PATH 里找）")
    p.add_argument("--profile", metavar="NAME", default="acp",
                   help="harness profile 名，默认 acp（即 dsh --profile acp）")
    p.add_argument("--theme", metavar="KEY",
                   help="配色主题：" + "、".join(f"{t.key}({t.name})" for t in themes.THEMES))
    return p


def main(argv: list[str] | None = None) -> int:
    """程序入口：解析参数、起窗口、跑事件循环。返回进程退出码。"""
    args = build_parser().parse_args(argv)

    # 先校验工作目录：目录不存在时 Popen 会报「拉不起 dsh: ... No such file or directory」，
    # 那句错误信息指向可执行文件，跟真正的原因（cwd 写错了）差着十万八千里。
    cwd = Path(args.cwd).expanduser() if args.cwd else default_workspace()
    if not cwd.is_dir():
        print(f"工作目录不存在或不是目录：{cwd}", file=sys.stderr)
        return 2
    if args.theme and args.theme not in themes.THEME_BY_KEY:
        # get_theme 会静默退回默认主题，那样用户只会觉得 --theme 没生效
        keys = "、".join(t.key for t in themes.THEMES)
        print(f"没有主题 {args.theme!r}，可选：{keys}（本次用默认主题）", file=sys.stderr)

    # 后台线程里有子进程等待和 JSON 解析；把 GIL 切换间隔调小，界面更跟手
    # （理由同 main.py：默认 5 ms 会让 GUI 线程最坏等几十毫秒）。
    sys.setswitchinterval(0.001)

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("DSH 对话")
    app.setApplicationDisplayName("DSH 对话")
    theme = themes.get_theme(args.theme or _config_theme())
    app.setStyleSheet(themes.build_qss(theme))

    win = ChatWindow(
        cwd=str(cwd), dsh_bin=args.dsh, profile=args.profile, theme_key=theme.key
    )
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
