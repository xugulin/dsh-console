"""控制台页：服务状态与启停重启。"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import billing, frontends, harness, pricing, service
from ..themes import Theme
from ..workers import run_async
from .components import Card, HourBars, Metric, Pill, ScrollPage


class DashboardPage(ScrollPage):
    """服务状态 + 启停控制 + 前端启动。

    harness 的版本与升级**不在这里**——那是独立的一页（:mod:`page_harness`），
    入口是侧边栏底部的版本按钮。

    继承 ScrollPage 而不是裸 QWidget：卡片多了以后，窗口不够高时 Qt 会把里面的控件
    一路压扁——实测"启动前端"卡片进来后，两行指标被裁得只剩半行字。套一层滚动区
    才是这个项目既有的做法（见 ScrollPage）。
    """

    status_changed = Signal(object)  # ServiceStatus

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme = theme
        self._busy = False
        self._last: service.ServiceStatus | None = None   # 最近一次状态快照
        self._balance_loaded = False     # 余额走网络，整个会话只拉一次
        self._browser_after = False      # 启动完成后是否自动开内置浏览器
        self._build()
        # 刻意**不在** __init__ 里发请求：构造全部页面时不产生任何子进程/网络任务。
        # 由 MainWindow 在页面首次真正显示时调 activate()。
        self._timer = QTimer(self)
        self._timer.setInterval(3000)
        self._timer.timeout.connect(self.refresh)

    # ------------------------------------------------- 页面生命周期
    def activate(self) -> None:
        """页面首次/再次显示：立刻刷一次，并开始轮询。"""
        self.refresh()
        if not self._timer.isActive():
            self._timer.start()

    def deactivate(self) -> None:
        """切走时停掉轮询，避免在后台空转抢线程与 GIL。"""
        self._timer.stop()

    # ------------------------------------------------------------------ UI
    def _build(self) -> None:
        root = self.body
        root.setSpacing(16)


        # ---- 状态卡片
        status_card = Card(
            "服务状态",
            "停止/重启会断开正在进行的会话；浏览器登录态在干净退出时才会落盘。",
        )
        head = QHBoxLayout()
        self.pill = Pill("读取中…")
        self.pill.set_state("读取中…", self.theme.text_faint, self.theme.text)
        head.addWidget(self.pill)
        self.unit_label = QLabel("dsh-web.service")
        self.unit_label.setStyleSheet(
            f"color: {self.theme.text_faint}; background: transparent;"
        )
        head.addWidget(self.unit_label)
        head.addStretch(1)
        self.error_label = QLabel("")
        self.error_label.setStyleSheet(
            f"color: {self.theme.danger}; background: transparent; font-size: 12px;"
        )
        head.addWidget(self.error_label)
        status_card.body.addLayout(head)

        grid = QGridLayout()
        grid.setHorizontalSpacing(38)
        grid.setVerticalSpacing(14)
        self.m_pid = Metric("进程 PID")
        self.m_uptime = Metric("运行时长")
        self.m_memory = Metric("内存占用")
        self.m_restarts = Metric("异常重启次数")
        self.m_port = Metric("监听端口", accent=True)
        self.m_autostart = Metric("登录自启")
        for i, m in enumerate(
            (self.m_pid, self.m_uptime, self.m_memory, self.m_restarts, self.m_port, self.m_autostart)
        ):
            grid.addWidget(m, i // 3, i % 3)
        status_card.body.addLayout(grid)

        # 只有"harness 活着但不由这个单元托管"时才显示（手工在终端敲 dsh web 就是这样）。
        # 这时启停按钮都不能用，必须说清楚为什么，否则用户只会觉得按钮坏了。
        self.status_note = QLabel("")
        self.status_note.setObjectName("CardHint")
        self.status_note.setWordWrap(True)
        self.status_note.setVisible(False)
        status_card.body.addWidget(self.status_note)

        # ---- 按钮
        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        # 「启动」按钮被**来源选择组**取代：启动哪一份 harness 本来就是这个按钮
        # 唯一的悬念，拆成"先在上面选来源、再回来点启动"两步纯属多余。
        # 现在点哪一份就直接启动哪一份，没装的那份禁用（需求明确要求）。
        self.source_buttons: dict[str, QPushButton] = {}
        for key, label in harness.SOURCES:
            btn = QPushButton(f"启动{label}")
            btn.setObjectName("Primary" if key == harness.SOURCE_SYSTEM else "Segment")
            btn.setCheckable(True)
            btn.clicked.connect(lambda _c=False, k=key: self._start_source(k))
            buttons.addWidget(btn)
            self.source_buttons[key] = btn
        self.btn_restart = QPushButton("重启")
        self.btn_stop = QPushButton("停止")
        self.btn_stop.setObjectName("Danger")
        self.btn_open = QPushButton("内置浏览器打开界面")
        self.btn_url = QPushButton("复制访问地址")
        self.btn_url.setObjectName("Ghost")

        for b in (self.btn_restart, self.btn_stop, self.btn_open, self.btn_url):
            buttons.addWidget(b)
        buttons.addStretch(1)
        status_card.body.addLayout(buttons)
        root.addWidget(status_card)

        # ---- 启动前端（web / TUI / GUI 三种用法）
        # 与上面的服务控制分开：那组按钮管的是"后台进程活不活"，这组管的是"用哪种界面"。
        front_card = Card(
            "启动前端",
            "同一个 harness，三种用法。TUI 与 GUI 是控制台自带的前端，"
            "都通过 dsh --profile acp（标准 ACP v1）连过去。"
            "用哪一份 harness 由上面的**服务状态**决定。",
        )

        frow = QHBoxLayout()
        frow.setSpacing(10)
        self.btn_web = QPushButton("用系统浏览器打开")
        self.btn_web.setObjectName("Primary")
        self.btn_web.setToolTip(
            "保证 harness 在跑，然后用**系统默认浏览器**打开带 token 的界面。\n"
            "机器上没有浏览器时用左边那个内置的。"
        )
        # 紧挨着 web 版：同一件事的两种打开方式，放一起才好对比
        self.btn_browser = QPushButton("用内置浏览器打开")
        self.btn_browser.setToolTip(
            "用随包携带的 Chromium（QtWebEngine）打开界面。\n"
            "独立 profile，不碰你自己的浏览器；机器上没装浏览器也能用。"
        )
        self.btn_tui = QPushButton("启动 TUI 版")
        self.btn_tui.setToolTip("在终端里打开对话界面（需要一个终端模拟器）")
        self.btn_gui = QPushButton("启动 GUI 版")
        self.btn_gui.setToolTip("原生窗口里跑 harness 自己的 web UI——界面与功能同 web 版")
        # 内置的排前面：它不依赖系统里装没装浏览器
        for b in (self.btn_browser, self.btn_web, self.btn_tui, self.btn_gui):
            frow.addWidget(b)
        frow.addStretch(1)
        front_card.body.addLayout(frow)

        self.front_card = front_card
        self._render_sources()
        self.front_hint = QLabel("")
        self.front_hint.setObjectName("CardHint")
        self.front_hint.setWordWrap(True)
        self.front_hint.setTextInteractionFlags(Qt.TextSelectableByMouse)
        front_card.body.addWidget(self.front_hint)

        root.addWidget(front_card)

        # ---- 今日消费（原先的「最近日志」已移除；日志有独立页面）
        today_card = Card("今日消费", "按事件发生时刻的峰谷档位计费；只统计本机会话记录")
        trow = QHBoxLayout()
        trow.setSpacing(30)
        self.t_cost = Metric("今日花费（¥）", accent=True)
        self.t_calls = Metric("模型调用")
        self.t_tokens = Metric("总 tokens")
        self.t_hit = Metric("缓存命中率")
        for x in (self.t_cost, self.t_calls, self.t_tokens, self.t_hit):
            trow.addWidget(x)
        trow.addStretch(1)
        today_card.body.addLayout(trow)

        trow2 = QHBoxLayout()
        trow2.setSpacing(30)
        self.t_peak = Metric("峰时段")
        self.t_off = Metric("谷时段")
        self.t_busy = Metric("最忙时段")
        # 余额和今日花费并排看才有意义——"今天花了多少"和"账上还剩多少"是两个连着的问题，
        # 搁在两个页面就得来回切。
        self.t_balance = Metric("账户余额")
        for x in (self.t_peak, self.t_off, self.t_busy, self.t_balance):
            trow2.addWidget(x)
        trow2.addStretch(1)
        today_card.body.addLayout(trow2)

        self.t_bars = HourBars(self.theme)
        self.t_bars.setFixedHeight(96)
        today_card.body.addWidget(self.t_bars)

        self.t_hint = QLabel("")
        self.t_hint.setObjectName("CardHint")
        self.t_hint.setWordWrap(True)
        today_card.body.addWidget(self.t_hint)
        root.addWidget(today_card)
        root.addStretch(1)

        self.btn_stop.clicked.connect(lambda: self._act("stop"))
        self.btn_restart.clicked.connect(lambda: self._act("restart"))
        # 打开界面走**内置浏览器**（随包携带，不依赖系统里装没装浏览器）
        self.btn_open.clicked.connect(self._open_in_browser)
        self.btn_url.clicked.connect(self._copy_url)
        self.btn_web.clicked.connect(self._launch_web)
        self.btn_browser.clicked.connect(self._launch_browser)
        self.btn_tui.clicked.connect(self._launch_tui)
        self.btn_gui.clicked.connect(self._launch_gui)

    # -------------------------------------------------------------- 数据
    def refresh(self) -> None:
        if self._busy:
            return
        # 与侧边栏共享快照，避免两边各 fork 一遍 systemctl/ss/journalctl/loginctl
        run_async(service.get_status_cached, self._on_status, self._on_error)
        run_async(billing.today_usage_cached, self._on_today, None)  # 60 秒 TTL，别每 3 秒重扫
        if not self._balance_loaded:
            self._balance_loaded = True
            self._refresh_balance()
            # 余额走网络，整个会话只拉一次

    def _refresh_balance(self) -> None:
        """查账户余额。

        放在「今日消费」这张卡里："今天花了多少"和"账上还剩多少"是两个连着的问题，
        搁在两个页面就得来回切。余额走网络，所以**只在页面激活时拉一次**，不跟着 3 秒轮询。
        """
        run_async(billing.fetch_balance, self._on_balance, None)

    def _on_balance(self, bal) -> None:
        if not getattr(bal, "ok", False):
            self.t_balance.set_value("—")
            # 取不到就把原因写在说明行里（"—" 本身不说明任何问题）
            self.t_balance.set_label("账户余额（查不到）")
            return
        info = bal.primary
        if info is None:
            self.t_balance.set_value("—")
            return
        self.t_balance.set_value(f"{info.symbol}{info.total:,.2f}")

    def _on_status(self, st: service.ServiceStatus) -> None:
        self._last = st
        if st.unit_running:
            src = harness.source_label(harness.current_source())
            self.pill.set_state(f"运行中（{src}）", self.theme.ok, self.theme.text)
        elif st.foreign_running:
            # harness 活着，但 systemd 那边是 inactive（终端里手敲 dsh web 就是这样）。
            # 用 warn 色而不是 ok 色：能用，但不归这个单元管，启停按钮也不生效。
            self.pill.set_state(st.label, self.theme.warn, self.theme.text)
        elif st.is_failed:
            self.pill.set_state("启动失败", self.theme.danger, self.theme.text)
        else:
            self.pill.set_state(st.label, self.theme.text_faint, self.theme.text)

        # ① 「两份 harness 都报出来」。便携包默认管的是**内置**那份，而用户很可能在跑
        # **系统**那份——只报当前这份，就会看到"系统 harness 明明在跑却显示已停止"
        # （实测反馈）。这里把两份各报一行，且**不改变**用户当前的选择。
        try:
            other = (harness.SOURCE_SYSTEM
                     if harness.current_source() == harness.SOURCE_BUNDLED
                     else harness.SOURCE_BUNDLED)
            cur_name = harness.source_label(harness.current_source())
            oth_name = harness.source_label(other)
            oth = service.get_status_cached(source=other)
            oth_txt = "运行中" if oth.is_running else ("已停止" if not oth.is_failed else "启动失败")
            oth_pid = f"（pid {oth.effective_pid}）" if oth.is_running and oth.effective_pid else ""
            self.unit_label.setText(
                f"当前管理：{cur_name} · {st.label}"
                f"　｜　{oth_name}：{oth_txt}{oth_pid}")
        except Exception:                      # noqa: BLE001 - 查不到就别动这行字
            pass

        self.m_pid.set_value(str(st.effective_pid) if st.effective_pid else "—")
        # 停着的时候别把这一格还叫"运行时长"：service.uptime_seconds 对停止的服务
        # 返回 0，硬显示会变成"运行时长 —"；而 ActiveEnterTimestamp 在停止后指的是
        # 停止时刻，直接算差值又会显示成"已停止 50 分钟"。所以停止时改成"上次运行"。
        if st.is_running:
            self.m_uptime.set_label("运行时长")
            self.m_uptime.set_value(st.uptime_text)
        else:
            self.m_uptime.set_label("上次运行")
            self.m_uptime.set_value(
                f"{st.last_run_text}（已停 {st.stopped_for_text}）"
                if st.stopped_for_seconds >= 60
                else st.last_run_text
            )
        self.m_memory.set_value(st.memory_text)
        self.m_restarts.set_value(str(st.n_restarts))
        self.m_port.set_value(str(st.port) if st.port else "—")
        self.m_autostart.set_value("已启用" if st.unit_file_state == "enabled" else st.unit_file_state)
        self.error_label.setText(st.raw_error or "")

        # 单元没在跑但 harness 活着：systemctl 的 start/stop/restart 都动不了它
        # （start 会 EADDRINUSE，stop 是空转）。按钮就别装作能用，并把原因写明白。
        foreign = st.foreign_running
        if foreign:
            owner = st.listener_name or "另一个进程"
            port_part = f"，占用端口 {st.port}" if st.port else ""
            # 这里的强调要用 HTML：QLabel 的 AutoText 只认标签，写成 markdown 的
            # **粗体** 会把星号原样显示出来。
            self.status_note.setTextFormat(Qt.RichText)
            self.status_note.setText(
                f"⚠️ harness 正在运行，但<b>不是</b> dsh-web.service 拉起来的"
                f"（PID {st.listener_pid}，进程 {owner}{port_part}）。"
                f"「启动 / 重启」会因端口被占而失败，「停止」也停不掉它——"
                f"要接管得先在启动它的那个终端里结束进程。"
                f"带 token 的地址只打印在它自己的终端里，这里取不到，「打开界面」因此不可用。"
            )
            tip = "该实例不由 systemd 托管，systemctl 动不了它"
        else:
            tip = ""
        self.status_note.setVisible(foreign)
        for btn in (self.btn_restart, self.btn_stop, self.btn_open, self.btn_url):
            btn.setToolTip(tip)
        # 启动：单元和 harness 都没在跑才允许
        for key, btn in self.source_buttons.items():
            # 已经在跑的那一份不能再"启动"（点它只会报 EADDRINUSE）
            installed = harness.source_root(key) is not None
            running = st.is_running and key == self._running_source()
            btn.setEnabled(installed and not running and not self._busy)
        # 停止/重启：只在单元真的管着它的时候才有意义
        self.btn_stop.setEnabled(st.unit_running and not self._busy)
        self.btn_restart.setEnabled(st.unit_running and not self._busy)
        # 打开界面/复制地址：没有可用地址（非托管实例取不到 token）就禁用
        has_url = bool(st.url)
        self.btn_open.setEnabled(has_url and not self._busy)
        self.btn_url.setEnabled(has_url and not self._busy)

        self.status_changed.emit(st)

    def _on_today(self, t) -> None:
        self.t_cost.set_value(pricing.fmt_cny(t.cost_cny))
        self.t_calls.set_value(f"{t.calls:,}")
        self.t_tokens.set_value(f"{t.tokens:,}")
        self.t_hit.set_value(f"{t.cache_hit_rate:.1f}%")
        self.t_peak.set_value(pricing.fmt_cny(t.peak_cny))
        self.t_off.set_value(pricing.fmt_cny(t.offpeak_cny))
        bh = t.busiest_hour
        self.t_busy.set_value(f"{bh.hour}:00" if bh else "—")
        self.t_bars.set_hours(t.hours)
        self.t_hint.setText(
            "今天还没有模型调用" if not t.calls
            else f"峰时段 {pricing.fmt_cny(t.peak_cny)} · 谷时段 {pricing.fmt_cny(t.offpeak_cny)}"
                 f" · 缓存命中 {t.cache_hit_rate:.1f}%　（详细拆分见「账单」页）"
        )

    def _on_error(self, msg: str) -> None:
        self.error_label.setText(msg)

    # ------------------------------------------------------------ 动作
    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        for b in (self.btn_stop, self.btn_restart, self.btn_open, self.btn_url):
            b.setEnabled(not busy)
        if not busy and self._last is not None:
            self._on_status(self._last)

    def _act(self, verb: str, open_after: bool = False,
             browser_after: bool = False) -> None:
        if self._busy:
            return
        if verb == "stop":
            ans = QMessageBox.question(
                self,
                "确认停止",
                "停止 dsh-web 会断开所有正在进行的会话。\n"
                "浏览器登录态会在干净退出时保存，确定停止吗？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if ans != QMessageBox.Yes:
                return
        label = {"start": "启动", "stop": "停止", "restart": "重启"}[verb]
        self._set_busy(True)
        self._browser_after = browser_after
        self.error_label.setText(f"{label}中…")

        def job() -> str:
            getattr(service, verb)()
            return label

        def done(result: str) -> None:
            # 重启/启动后等地址就绪，让"打开界面"立刻可用
            if verb in ("start", "restart"):
                service.wait_for_url(attempts=30, delay=1.0)
            self._set_busy(False)
            self.error_label.setText("")
            self.refresh()
            if open_after:
                self._open()

        def fail(msg: str) -> None:
            self._set_busy(False)
            self.error_label.setText(f"{label}失败：{msg}")

        run_async(job, done, fail)

    # ---------------------------------------------------------- 启动前端
    def _front_hint(self, text: str, *, error: bool = False) -> None:
        """前端卡片的反馈行。失败时把可粘贴的命令也带上，别只说"失败了"。"""
        color = self.theme.danger if error else self.theme.text_dim
        self.front_hint.setStyleSheet(
            f"color: {color}; background: transparent; font-size: 12.5px;"
        )
        self.front_hint.setText(text)

    def _render_sources(self) -> None:
        """刷新"启动哪一份 harness"这组按钮。

        **没装的那份直接禁用**（需求明确要求），并把版本写进 tooltip——
        两个按钮长得一样，不写清楚点的是哪一份、什么版本，用户没法判断。
        """
        current = harness.current_source()
        notes = {key: (label, ok, note) for key, label, ok, note in harness.available_sources()}
        for key, btn in self.source_buttons.items():
            label, ok, note = notes.get(key, (key, False, ""))
            btn.setEnabled(ok)
            btn.setChecked(key == current and ok)
            btn.setToolTip(note or label)

        parts = []
        for label, ok, note in notes.values():
            parts.append(f"{label}：{note}" if ok else f"{label}：不可用（{note}）")
        if hasattr(self, "source_hint"):
            self.source_hint.setText(
                f"当前用 **{harness.source_label(current)}**。　" + "　".join(parts)
            )

    def _pick_source(self, key: str) -> None:
        """切换来源。

        切完要**把状态重新读一遍**：两份 harness 的服务是各自独立的
        （内置那份是子进程、系统那份是 systemd 单元），不重读会显示上一份的状态。
        """
        if harness.source_root(key) is None:
            return
        harness.set_source(key)
        service.invalidate_status()
        self._render_sources()
        self._front_hint(f"已切换到 {harness.source_label(key)}，正在重读状态…")
        self.refresh()

    def _running_source(self) -> str:
        """当前跑着的是哪一份。

        子进程后端只可能是内置那份；systemd 单元那份就是系统装的。
        """
        return harness.SOURCE_BUNDLED if service.use_child_backend() else harness.SOURCE_SYSTEM

    def _start_source(self, key: str) -> None:
        """启动指定来源的 harness。

        如果它不是当前选中的那份，**先切过去再启动**——否则会出现"点了启动内置，
        结果起来的是系统那份"这种事（服务是从当前来源读的）。
        """
        if harness.source_root(key) is None:
            self._front_hint(f"{harness.source_label(key)}没有安装，无法启动。", error=True)
            return
        if key != harness.current_source():
            harness.set_source(key)
            service.invalidate_status()
        self._render_sources()
        self._front_hint(f"正在启动 {harness.source_label(key)}…")
        # 启动完**接着用内置浏览器打开界面**：点了"启动"的人下一步一定是想看界面，
        # 让他再去找另一个按钮纯属多余。用内置浏览器而不是系统浏览器，是因为
        # 它是随包携带的——不依赖目标机器上装没装浏览器。
        self._act("start", browser_after=True)

    def _launch_web(self) -> None:
        """保证 web 在跑，然后把带 token 的界面开出来。"""
        st = self._last
        if st is not None and st.unit_running and st.url:
            self._front_hint("web 版已经在跑，正在打开界面…")
            self._open()
            return
        self._front_hint("正在启动 web 版…")
        self._act("start", open_after=True)

    def _launch_browser(self) -> None:
        """用内置浏览器打开。

        服务没起就先把服务起起来——否则内置浏览器打开的是一个"取不到地址"的错误页，
        用户会以为是浏览器坏了。
        """
        self.btn_browser.setEnabled(False)
        self._front_hint("正在打开内置浏览器…")

        def work():
            try:
                st = service.get_status()
                if not st.is_running:
                    service.start()
            except Exception as exc:  # noqa: BLE001
                return False, f"启动 harness 失败：{exc}"
            res = frontends.launch_browser(dsh=frontends.dsh_executable())
            if res.ok:
                return True, res.message
            return False, res.message

        def done(result) -> None:
            self.btn_browser.setEnabled(True)
            ok, msg = frontends.as_ok_msg(result)   # 对象/元组两种形状都吃
            self._front_hint(msg, error=not ok)

        run_async(work, done)

    def _launch_tui(self) -> None:
        """在终端里打开 TUI 版。

        Popen 很快，但仍然丢到后台线程：找终端、写日志都可能踩到慢磁盘，
        界面线程不该为这种事停顿。
        """
        cwd = str(frontends.DEFAULT_WORKSPACE)
        self.btn_tui.setEnabled(False)
        self._front_hint("正在打开 TUI 版…")

        def done(res: frontends.LaunchResult) -> None:
            self.btn_tui.setEnabled(True)
            if res.ok:
                self._front_hint(f"{res.message}（工作目录 {cwd}）")
            else:
                self._front_hint(f"{res.message}", error=True)

        def fail(msg: str) -> None:
            self.btn_tui.setEnabled(True)
            self._front_hint(f"打开 TUI 版失败：{msg}", error=True)

        run_async(lambda: frontends.launch_tui(cwd), done, fail)

    def _launch_gui(self) -> None:
        """作为独立进程拉起 GUI 版。"""
        cwd = str(frontends.DEFAULT_WORKSPACE)
        self.btn_gui.setEnabled(False)
        self._front_hint("正在启动 GUI 版…")

        def done(res: frontends.LaunchResult) -> None:
            self.btn_gui.setEnabled(True)
            if res.ok:
                self._front_hint(
                    f"{res.message}　日志：{frontends.log_path('gui')}"
                )
            else:
                self._front_hint(f"{res.message}", error=True)

        def fail(msg: str) -> None:
            self.btn_gui.setEnabled(True)
            self._front_hint(f"启动 GUI 版失败：{msg}", error=True)

        run_async(lambda: frontends.launch_gui(cwd), done, fail)

    def _open_in_browser(self) -> None:
        """用**内置浏览器**打开 harness 界面。

        和 :meth:`_open`（系统默认浏览器）并列：内置那个随包携带，
        目标机器上没装任何浏览器也能用，而且不碰用户自己的浏览器数据。
        """
        self.btn_open.setEnabled(False)
        self.error_label.setText("正在打开内置浏览器…")

        def work():
            try:
                st = service.get_status()
                if not st.is_running:
                    service.start()
                    service.wait_for_url(attempts=30, delay=1.0)
            except Exception as exc:  # noqa: BLE001
                return False, f"启动 harness 失败：{exc}"
            res = frontends.launch_browser()
            return res.ok, res.message

        def done(result) -> None:
            self.btn_open.setEnabled(True)
            ok, msg = frontends.as_ok_msg(result)   # 对象/元组两种形状都吃
            self.error_label.setText("" if ok else msg.splitlines()[0])

        run_async(work, done)

    def _open(self) -> None:
        def job() -> None:
            url = self._last.url if self._last else None
            if not url or not service.url_is_alive(url):
                url = service.wait_for_url(attempts=20, delay=1.0)
            service.open_in_browser(url)

        run_async(job, lambda _: None, lambda m: self.error_label.setText(m))

    def _copy_url(self) -> None:
        from PySide6.QtWidgets import QApplication

        url = self._last.url if self._last else None
        if not url:
            self.error_label.setText("当前没有可用地址（服务未运行？）")
            return
        QApplication.clipboard().setText(url)
        self.error_label.setText("访问地址已复制到剪贴板")
        QTimer.singleShot(2500, lambda: self.error_label.setText(""))

    def apply_theme(self, theme: Theme) -> None:
        self.theme = theme
        self.error_label.setStyleSheet(
            f"color: {theme.danger}; background: transparent; font-size: 12px;"
        )
        self.unit_label.setStyleSheet(
            f"color: {theme.text_faint}; background: transparent;"
        )
        self.t_bars.theme = theme
        self.t_bars.update()
        if self._last is not None:
            self._on_status(self._last)
