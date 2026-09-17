"""Harness 页：``dsh`` 自身的版本、安装细节、运行状态与升级。

原先这些挤在「控制台」页的一张小卡片里。挪出来是因为它和"服务启停"不是一类东西：
控制台页回答"服务活着吗、怎么用"，这一页回答"跑的是哪个版本、装在哪、要不要升级、
升级完要不要重启"。信息量差一个数量级，卡片塞不下。

从左侧面板的**版本按钮**进来（那个按钮平时就显示当前版本）。

这一页有一处判断值得单独说：**磁盘上的代码可能比正在跑的进程新**。
升级只替换文件，已经在跑的进程还是旧代码，直到重启才换。所以这里拿安装目录里
``package.json`` 的 mtime 和服务的启动时刻比一比——新就是"有更新没生效"，会明说。
没有这个判断，用户升级完看不到版本号变化，只会以为升级失败了。
"""

from __future__ import annotations

import html
import json
import subprocess
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QProgressBar,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import subproc
from .. import (browser, config, frontends, harness, plugin_manager, service, portable,
                service, updater)
from ..themes import Theme
from ..workers import run_async
from .components import Card, Metric, ScrollPage, kv_row

#: web profile 的目录（bundle 列表从这里读）。
PROFILE_DIR = Path.home() / ".dsh" / "profiles" / "web"


def _short_url(url: str, limit: int = 42) -> str:
    """发布地址通常很长，界面上只留头尾。"""
    url = (url or "").strip()
    if len(url) <= limit:
        return url
    keep = (limit - 3) // 2
    return f"{url[:keep]}…{url[-keep:]}"


def _run_browser_update() -> tuple[bool, str]:
    """真的去升级包内的 PySide6（内置浏览器的引擎）。

    放在模块级而不是方法里：它要丢进线程池跑，方法会拖着整个页面对象不放。
    """
    cmd = browser.update_command()
    try:
        proc = subproc.run(cmd, capture_output=True, text=True, timeout=3600)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-4:]
        return False, "升级命令返回非零：<br>" + "<br>".join(html.escape(x) for x in tail)
    now = browser.installed_engine_version()
    return True, (f"引擎升级完成，当前 <b>{now or '?'}</b>。"
                  f"重启控制台与内置浏览器后生效。")


class HarnessPage(ScrollPage):
    """harness 版本、安装详情、运行状态与升级。"""

    #: 版本变化时通知外面（侧边栏的版本按钮要跟着改写）
    version_changed = Signal(str)

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme = theme
        self._info: harness.HarnessInfo | None = None
        self._releases: harness.Releases | None = None
        self._updating = False
        self._loaded = False
        self._build()
        # 与其它页面一致：不在 __init__ 里发请求，等第一次真正显示时 activate()
        self._timer = QTimer(self)
        self._timer.setInterval(5000)
        self._timer.timeout.connect(self._refresh_runtime)

    # ------------------------------------------------- 页面生命周期
    def activate(self) -> None:
        if not self._loaded:
            self._loaded = True
            self.reload()
        else:
            self._refresh_runtime()
        if not self._timer.isActive():
            self._timer.start()

    def deactivate(self) -> None:
        self._timer.stop()

    # ------------------------------------------------------------------ UI
    def _build(self) -> None:
        root = self.body


        self._build_version_card(root)
        self._build_browser_card(root)
        self._build_console_card(root)
        self._build_install_card(root)
        self._build_runtime_card(root)
        self._build_profile_card(root)
        root.addStretch(1)

    # ---- 版本 + 升级
    def _build_version_card(self, root: QVBoxLayout) -> None:
        card = Card(
            "harness 版本与升级",
            "**包里自带的那份**和**系统里 npm 装的那份**是两个独立的安装，可以同时存在、分别安装、分别升级。升级只替换磁盘上的代码；正在跑的进程要用新版本必须重启。",
        )
        self.lbl_channels = QLabel("")
        self.lbl_channels.setTextFormat(Qt.RichText)
        self.lbl_channels.setObjectName("CardHint")
        self.lbl_channels.setTextInteractionFlags(Qt.TextSelectableByMouse)
        card.body.addWidget(self.lbl_channels)

        self.lbl_pending = QLabel("")
        self.lbl_pending.setWordWrap(True)
        self.lbl_pending.setTextFormat(Qt.RichText)
        self.lbl_pending.setVisible(False)
        self.lbl_pending.setStyleSheet(
            f"color: {self.theme.warn}; background: transparent; font-size: 12.5px;"
        )
        card.body.addWidget(self.lbl_pending)

        # ---- 两份 harness：各自一块，各自的状态与按钮
        #
        # 原来这里是"两个版本数字 + 一行三选一的升级目标"，信息挤在一起，
        # 「已安装版本」到底指哪一份也看不出来。改成**一份一块**：
        # 每块自己的版本、位置、权限，以及自己的按钮——没装时按钮是「安装」，
        # 装了是「升级」（对 npm 来说这两件事是同一条命令，只是语义不同）。
        self.source_blocks: dict[str, dict] = {}
        blocks = QHBoxLayout()
        blocks.setSpacing(14)
        for key, label in harness.SOURCES:
            frame = QFrame()
            frame.setObjectName("SourceBlock")
            box = QVBoxLayout(frame)
            box.setContentsMargins(14, 12, 14, 12)
            box.setSpacing(6)

            head = QHBoxLayout()
            title = QLabel(f"<b>{label}</b>")
            title.setTextFormat(Qt.RichText)
            head.addWidget(title)
            head.addStretch(1)
            badge = QLabel("")
            badge.setTextFormat(Qt.RichText)
            head.addWidget(badge)
            box.addLayout(head)

            detail = QLabel("")
            detail.setTextFormat(Qt.RichText)
            detail.setWordWrap(True)
            detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
            detail.setObjectName("CardHint")
            box.addWidget(detail)

            brow = QHBoxLayout()
            brow.setSpacing(8)
            btn = QPushButton("安装")
            btn.clicked.connect(lambda _c=False, k=key: self._install_or_upgrade(k))
            brow.addWidget(btn)
            brow.addStretch(1)
            box.addLayout(brow)

            blocks.addWidget(frame, 1)
            self.source_blocks[key] = {
                "frame": frame, "badge": badge, "detail": detail, "button": btn,
                "label": label,
            }
        card.body.addLayout(blocks)

        # 进度区：装的时候才显示。npm 的输出没有规整百分比，所以用
        # **不确定进度条 + 实时日志尾巴**——比编一个假百分比诚实，也比干等强。
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)          # 0,0 = 不确定模式（来回跑的那种）
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(4)
        self.progress.setVisible(False)
        card.body.addWidget(self.progress)

        self.lbl_progress = QLabel("")
        self.lbl_progress.setObjectName("CardHint")
        self.lbl_progress.setWordWrap(True)
        self.lbl_progress.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_progress.setVisible(False)
        card.body.addWidget(self.lbl_progress)

        # 「都升级」留一个小按钮：两份都装了才有意义
        self.btn_update_all = QPushButton("两份都升级")
        self.btn_update_all.setObjectName("Ghost")
        self.btn_update_all.clicked.connect(lambda: self._install_or_upgrade("both"))

        actions = QHBoxLayout()
        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.btn_check = QPushButton("检查更新")
        self.btn_check.setObjectName("Ghost")
        self.btn_check.setToolTip("绕过 10 分钟缓存，重新向 npm 查询各通道版本")
        self.channel_box = QComboBox()
        for key, label in (
            ("latest", "latest（稳定）"),
            ("next", "next（预览）"),
            ("alpha", "alpha（内测）"),
        ):
            self.channel_box.addItem(label, key)
        self.channel_box.setToolTip("升级到哪个通道；也可以直接选某个具体版本")
        self.btn_restart = QPushButton("重启使其生效")
        self.btn_restart.setObjectName("Danger")
        self.btn_restart.setToolTip("systemctl --user restart dsh-web（会中断正在进行的会话）")
        for w in (self.btn_check, self.channel_box, self.btn_update_all,
                  self.btn_restart):
            actions.addWidget(w)
        actions.addStretch(1)
        card.body.addLayout(actions)

        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        self.hint.setTextFormat(Qt.RichText)
        self.hint.setTextInteractionFlags(Qt.TextSelectableByMouse)
        card.body.addWidget(self.hint)
        root.addWidget(card)

        self._target = harness.current_source()
        self._progress_text = ""      # 安装线程写、界面读（只存字符串，不碰控件）
        self._render_targets()
        self.btn_check.clicked.connect(lambda: self.reload(force=True))
        self.btn_restart.clicked.connect(self._restart)

    # ---- 升级目标（内置 / 系统 / 两份）
    def _render_targets(self) -> None:
        """刷新两份 harness 的区块：状态、位置、按钮文案与可用性。

        **没装就显示「安装」，装了显示「升级」**——对 npm 来说这是同一条命令
        （``npm install -g pkg@ver`` 装到空目录就是安装、装到已有目录就是升级），
        但对用户是两件事，按钮文案必须跟着状态变。
        """
        rel = self._releases
        channel = self.channel_box.currentData() or "latest"
        target = (rel.tags.get(channel, "") if rel else "") or channel
        both_ok = all(ok for _k, _l, ok, _n in harness.available_sources())

        for key, label, ok, note in harness.available_sources():
            blk = self.source_blocks.get(key)
            if blk is None:
                continue
            version = harness.installed_version(key) if ok else ""
            root = harness.source_root(key)

            if ok:
                blk["badge"].setText(
                    f'<span style="color:{self.theme.ok}">已安装</span>')
                where = f"<br><span style=\"font-size:11.5px\">{root}</span>" if root else ""
                blk["detail"].setText(f"版本 <b>{version or '?'}</b>{where}")
                blk["button"].setText("升级" if version else "重装")
                blk["button"].setObjectName("Primary")
                blk["button"].setToolTip(
                    f"装到 {root}\n目标：{channel} → {target}")
                blk["button"].setEnabled(not self._updating)
            else:
                blk["badge"].setText(
                    f'<span style="color:{self.theme.text_faint}">未安装</span>')
                # 说明为什么"不可用"、以及点安装会装到哪
                if key == harness.SOURCE_BUNDLED:
                    where = root or harness.portable.harness_prefix() or "包内 harness/"
                    blk["detail"].setText(
                        f"包里没有内置 harness。<br>"
                        f"<span style=\"font-size:11.5px\">安装到 {where}</span>")
                    blk["button"].setToolTip(f"用包里的 node/npm 装到 {where}，不需要 sudo")
                else:
                    blk["detail"].setText(
                        "系统里没有安装 harness（npm -g）。<br>"
                        "<span style=\"font-size:11.5px\">安装到系统全局目录，需要 sudo</span>")
                    blk["button"].setToolTip("npm install -g @deepseek-ai/dsh（需要免密 sudo）")
                # **能不能装，和"装没装"是两件事。**
                # 原来这里无条件 setEnabled(not self._updating)，于是没有便携包的环境里
                # 「安装内置 harness」也是可按的——点下去东西装到了系统里（用户实测踩到：
                # 点内置的安装，系统 harness 被升成了内测版）。
                installable, why = harness.can_install(key)
                blk["button"].setText("安装" if installable else "不可用")
                blk["button"].setObjectName("Primary")
                blk["button"].setEnabled(installable and not self._updating)
                if not installable:
                    blk["detail"].setText(f"{why}<br>"
                                          f'<span style="font-size:11.5px">{note}</span>')
                    blk["button"].setToolTip(why)
            # 样式表是按 objectName 选的，改了名字要重新套一遍
            blk["button"].style().unpolish(blk["button"])
            blk["button"].style().polish(blk["button"])

        self.btn_update_all.setEnabled(both_ok and not self._updating)
        self.btn_update_all.setToolTip(
            "两份都升级（串行执行，避免两个 npm 抢缓存）" if both_ok
            else "两份都装了才能用")
        self._render_channel_notes()

    def _render_channel_notes(self) -> None:
        """把各通道版本写进提示行（原来挤在四个 Metric 里）。"""
        rel = self._releases
        inf = self._info
        bits = []
        for channel in ("latest", "next", "alpha"):
            version = rel.tags.get(channel, "") if rel else ""
            if not version:
                bits.append(f"{channel} —")
            elif inf is not None and inf.installed and harness.is_newer(version, inf.installed):
                bits.append(f"{channel} <b>{version} ↑</b>")
            else:
                bits.append(f"{channel} {version}")
        self.lbl_channels.setText("npm 通道：　" + "　·　".join(bits))

    def _install_or_upgrade(self, key: str) -> None:
        """安装或升级指定来源（``both`` = 两份都来一遍），**装完自动重启**。"""
        if self._updating:
            return
        sources = ([harness.SOURCE_BUNDLED, harness.SOURCE_SYSTEM]
                   if key == "both" else [key])
        channel = self.channel_box.currentData() or "latest"
        rel = self._releases
        target_version = (rel.tags.get(channel, "") if rel else "") or channel

        # 已经是要装的版本就**不重复下载**，一个字节都不下
        todo = [s for s in sources
                if not harness._same_version(harness.installed_version(s) or "",
                                             target_version)]
        skips = [s for s in sources if s not in todo]
        skip_note = ("<br><br>已经是要装的版本、**跳过不重复下载**："
                     + "、".join(harness.source_label(s) for s in skips)) if skips else ""
        if not todo:
            self._set_hint(
                "已经是要装的版本（" + "、".join(harness.source_label(s) for s in skips) +
                "），没有重复下载。", ok=True)
            return

        rows = []
        for src in todo:
            installed = harness.installed_version(src) or ""
            verb = "升级" if installed else "安装"
            rows.append(
                f"· <b>{verb} {harness.source_label(src)}</b>"
                f"（{installed or '未安装'} → {channel} → {target_version}）"
                f"<br><code>{' '.join(harness.update_command(target_version, source=src))}</code>"
            )
        ans = QMessageBox.question(
            self, "确认安装 / 升级",
            "将执行：<br>" + "<br><br>".join(rows) + skip_note +
            "<br><br>内置那份装进包内 <b>harness/</b>，不需要 sudo；"
            "系统那份装进系统全局目录，通常需要免密 sudo。<br>"
            "<b>装完会自动重启 harness 让新版本生效</b>（会中断正在进行的会话）。"
            "<br><br>确定吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if ans != QMessageBox.Yes:
            return

        self._updating = True
        self._progress_show(f"正在处理 {len(todo)} 份 harness…")
        self._render_targets()

        def work() -> str:
            notes = []
            for src in todo:
                label = harness.source_label(src)
                self._progress_post(f"正在处理 {label}…")
                try:
                    # **传解析后的版本号，不要传通道名。**
                    # 传通道名的话 `_same_version` 里的 `_parse("latest")` 拿不到版本，
                    # "同版本就跳过"这条永远不成立——按钮点几次就重复下载几次（实测）。
                    # 传版本号还顺带把安装钉死在对话框里显示的那个版本上。
                    notes.append(f"{label}：{harness.update(
                        target_version, source=src, on_progress=self._progress_post)}")
                except Exception as exc:  # noqa: BLE001 - 一份失败不影响另一份
                    notes.append(f"<b>{label} 失败</b>：{exc}")
            if skips:
                notes.append("跳过（已是要装的版本）："
                             + "、".join(harness.source_label(s) for s in skips))
            return "<br>".join(notes)

        def done(msg: str) -> None:
            self._progress_hide()
            self._updating = False
            # 版本信息要**马上**跟着变：两份区块、通道行、侧边栏版本号全刷一遍
            harness.invalidate()
            self.reload(force=True)
            self._restart_after_update(msg)

        def fail(msg: str) -> None:
            self._progress_hide()
            self._updating = False
            self._render_targets()
            self._set_hint(f"失败：{msg}", error=True)

        run_async(work, done, fail)

    def _restart_after_update(self, msg: str) -> None:
        """装完自动重启，让新版本立刻生效。"""
        try:
            running = service.get_status().is_running
        except Exception:  # noqa: BLE001
            running = False
        if not running:
            self._set_hint(msg + "<br><br>harness 当前没在运行，不用重启；"
                                 "下次启动就是新版本。", ok=True)
            return
        self._set_hint(msg + "<br><br>正在重启 harness 让新版本生效…")
        self._restart(quiet=True, on_done=lambda: self._after_restart_ok(msg))

    def _after_restart_ok(self, msg: str) -> None:
        harness.invalidate()
        self.reload(force=True)
        self._set_hint(msg + "<br><br>✓ 已重启，新版本已生效。", ok=True)

    # ---------------------------------------------------------- 进度显示
    def _progress_show(self, text: str) -> None:
        self.progress.setVisible(True)
        self.lbl_progress.setVisible(True)
        self.lbl_progress.setText(text)

    def _progress_hide(self) -> None:
        self.progress.setVisible(False)
        self.lbl_progress.setVisible(False)
        self.lbl_progress.setText("")

    def _progress_post(self, text: str) -> None:
        """从工作线程收到一行进度。

        **只存字符串、不碰控件**：这个回调跑在安装线程里，直接改 QLabel 是跨线程操作 UI。
        真正的刷新交给下一次 reload（安装过程本来就是"等结果"，不差这点实时性）。
        """
        self._progress_text = text

    def _build_browser_card(self, root: QVBoxLayout) -> None:
        """内置浏览器：引擎信息 + 升级。

        为什么要单独一张卡片：内置浏览器是**随包携带的一整套 Chromium**（QtWebEngine），
        它有自己的版本、自己的 profile 目录、自己的升级路径（升级 PySide6 引擎）。
        这些信息散在别处用户根本看不到，而"我的浏览器是什么版本"恰恰是出问题时第一个要问的。
        """
        card = Card(
            "内置浏览器",
            "随包携带的 Chromium（QtWebEngine），**独立 profile**——不读也不写你的浏览器数据。"
            "机器上没装任何浏览器也能用；控制台「控制台」页有「用内置浏览器打开」按钮。",
        )
        row = QHBoxLayout()
        row.setSpacing(30)
        self.m_browser_ver = Metric("内置浏览器", accent=True)
        self.m_chromium = Metric("Chromium 引擎")
        self.m_qt = Metric("Qt 运行时")
        self.m_profile_size = Metric("profile 占用")
        for m in (self.m_browser_ver, self.m_chromium, self.m_qt, self.m_profile_size):
            row.addWidget(m)
        row.addStretch(1)
        card.body.addLayout(row)

        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.btn_browser_open = QPushButton("用内置浏览器打开")
        self.btn_browser_check = QPushButton("检查引擎更新")
        self.btn_browser_update = QPushButton("升级引擎")
        self.btn_browser_info = QPushButton("详情 / 打开 profile 目录")
        for w in (self.btn_browser_open, self.btn_browser_check,
                  self.btn_browser_update, self.btn_browser_info):
            actions.addWidget(w)
        actions.addStretch(1)
        card.body.addLayout(actions)

        self.browser_hint = QLabel("")
        self.browser_hint.setWordWrap(True)
        self.browser_hint.setTextFormat(Qt.RichText)
        self.browser_hint.setTextInteractionFlags(Qt.TextSelectableByMouse)
        card.body.addWidget(self.browser_hint)
        root.addWidget(card)

        self.btn_browser_open.clicked.connect(self._open_browser)
        self.btn_browser_check.clicked.connect(self._check_browser_update)
        self.btn_browser_update.clicked.connect(self._update_browser)
        self.btn_browser_info.clicked.connect(self._show_browser_info)
        self._render_browser()

    def _render_browser(self) -> None:
        info = browser.engine_info()
        self.m_browser_ver.set_value(str(info.get("浏览器版本") or "—"))
        self.m_chromium.set_value(str(info.get("chromium") or "不可用"))
        self.m_qt.set_value(str(info.get("qt") or "—"))
        self.m_profile_size.set_value(browser.human_size(int(info.get("缓存大小") or 0)))

    def _browser_hint_text(self, text: str, *, error: bool = False, ok: bool = False) -> None:
        color = self.theme.danger if error else (self.theme.ok if ok else self.theme.text_dim)
        self.browser_hint.setStyleSheet(
            f"color: {color}; background: transparent; font-size: 12.5px;"
        )
        self.browser_hint.setText(text)

    def _open_browser(self) -> None:
        self._browser_hint_text("正在打开内置浏览器…")

        def done(result) -> None:
            ok, msg = frontends.as_ok_msg(result)   # 对象/元组两种形状都吃
            self._browser_hint_text(msg, error=not ok, ok=ok)

        run_async(frontends.launch_browser, done)

    def _update_browser(self) -> None:
        """升级内置浏览器的引擎（= 包内的 PySide6）。"""
        cmd = browser.update_command()
        ans = QMessageBox.question(
            self, "升级内置浏览器引擎",
            "将执行：<br><code>" + " ".join(cmd) + "</code><br><br>"
            "内置浏览器的引擎就是 QtWebEngine（Chromium），所以升级它 = 升级包内的 "
            "PySide6。便携包里这会写进 <b>runtime/</b>，**不会碰系统 Python**。<br><br>"
            "升级完需要重启控制台和内置浏览器。<br><br>确定吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if ans != QMessageBox.Yes:
            return
        self.btn_browser_update.setEnabled(False)
        self._browser_hint_text("正在升级引擎（可能要几分钟，会下载一两百 MB）…")

        def done(result) -> None:
            self.btn_browser_update.setEnabled(True)
            ok, msg = frontends.as_ok_msg(result)   # 对象/元组两种形状都吃
            self._render_browser()
            self._browser_hint_text(msg, error=not ok, ok=ok)

        def fail(msg: str) -> None:
            self.btn_browser_update.setEnabled(True)
            self._browser_hint_text(f"升级失败：{msg}", error=True)

        run_async(_run_browser_update, done, fail)

    def _check_browser_update(self) -> None:
        self.btn_browser_check.setEnabled(False)
        self._browser_hint_text("正在向 PyPI 查最新引擎版本…")

        def work():
            return browser.installed_engine_version(), browser.releases()

        def done(result) -> None:
            self.btn_browser_check.setEnabled(True)
            mine, latest = result
            if latest and mine and latest != mine:
                self._browser_hint_text(
                    f"引擎包 PySide6-Essentials：当前 <b>{mine}</b>，最新 <b>{latest}</b> —— "
                    f"可以点「升级引擎」。（升级只写包内 runtime/，不影响系统 Python。）",
                    ok=True)
            elif latest:
                self._browser_hint_text(f"引擎已经是最新（{mine or latest}）。")
            else:
                self._browser_hint_text("查到了响应但没解析出版本号。", error=True)

        def fail(msg: str) -> None:
            self.btn_browser_check.setEnabled(True)
            self._browser_hint_text(f"检查失败：{msg}", error=True)

        run_async(work, done, fail)

    def _show_browser_info(self) -> None:
        info = browser.engine_info()
        lines = "<br>".join(f"<b>{k}</b>：{html.escape(str(v))}" for k, v in info.items())
        QMessageBox.information(
            self, "内置浏览器详情",
            lines + "<br><br>profile 目录是独立的：删掉它就等于把这个浏览器恢复出厂，"
                    "你自己的浏览器完全不受影响。",
        )

    # ---- 控制台自身（仅便携包）
    def _build_console_card(self, root: QVBoxLayout) -> None:
        """便携包才有的"控制台自更新"。

        非便携模式（源码跑在开发机上）没有"包"的概念，显示这张卡片只会让人困惑，
        所以直接不建。
        """
        if not portable.enabled():
            return
        card = Card(
            "控制台自身",
            "从发布地址整包更新。只替换 app/ 与 tools/（含启动器），"
            "**home/、harness/、runtime/ 原样保留**，换之前会把旧 app/ 备份成 app.bak-<时间>。",
        )
        row = QHBoxLayout()
        row.setSpacing(30)
        self.m_console_ver = Metric("控制台版本", accent=True)
        self.m_console_url = Metric("发布地址")
        row.addWidget(self.m_console_ver)
        row.addWidget(self.m_console_url)
        row.addStretch(1)
        card.body.addLayout(row)

        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.btn_set_url = QPushButton("设置发布地址…")
        self.btn_set_url.setObjectName("Ghost")
        self.btn_check_console = QPushButton("检查控制台更新")
        self.btn_update_console = QPushButton("更新控制台")
        self.btn_update_console.setObjectName("Primary")
        for w in (self.btn_set_url, self.btn_check_console, self.btn_update_console):
            actions.addWidget(w)
        actions.addStretch(1)
        card.body.addLayout(actions)

        self.console_hint = QLabel("")
        self.console_hint.setWordWrap(True)
        self.console_hint.setTextFormat(Qt.RichText)
        self.console_hint.setTextInteractionFlags(Qt.TextSelectableByMouse)
        card.body.addWidget(self.console_hint)
        root.addWidget(card)

        self._remote_version = ""          # 「检查更新」查到的远端版本号
        self.btn_set_url.clicked.connect(self._set_update_url)
        self.btn_check_console.clicked.connect(self._check_console_update)
        self.btn_update_console.clicked.connect(self._apply_console_update)
        self._render_console()

    def _render_console(self) -> None:
        if not portable.enabled():
            return
        self.m_console_ver.set_value(updater.current_version() or "—")
        # 显示**实际生效**的地址：用户没配就用内置的 GitHub 默认地址（见 updater.check_url）
        cfg = updater.configured_url()
        effective = updater.check_url()
        self.m_console_url.set_value(
            _short_url(effective) + ("" if cfg else "（默认）")
        )

    def _set_update_url(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        current = config.get(config.KEY_UPDATE_URL)
        text, ok = QInputDialog.getText(
            self, "控制台发布地址",
            "填 zip 或 json 地址；留空 = 用内置的 GitHub 发布地址：",
            text=current,
        )
        if not ok:
            return
        config.put(config.KEY_UPDATE_URL, text.strip())
        self._render_console()
        self._set_console_hint("发布地址已保存。" if text.strip() else "已清除发布地址。")

    def _set_console_hint(self, text: str, *, error: bool = False, ok: bool = False) -> None:
        if not portable.enabled():
            return
        color = self.theme.danger if error else (self.theme.ok if ok else self.theme.text_dim)
        self.console_hint.setStyleSheet(
            f"color: {color}; background: transparent; font-size: 12.5px;"
        )
        self.console_hint.setText(text)

    def _check_console_update(self) -> None:
        url = updater.check_url()      # 没配就用内置的 GitHub 默认地址
        self.btn_check_console.setEnabled(False)
        self._set_console_hint("正在检查…")

        def done(result: tuple[str, str]) -> None:
            self.btn_check_console.setEnabled(True)
            version, note = result
            mine = updater.current_version()
            if updater.is_newer(version, mine):
                self._remote_version = version      # 记住它：应用更新时要拼 Release 地址
                self._set_console_hint(
                    f"{note}；当前 {mine or '?'} —— <b>可以更新</b>，点「更新控制台」。", ok=True
                )
            else:
                self._set_console_hint(f"{note}；当前已经是最新（{mine or '?'}）。")

        def fail(msg: str) -> None:
            self.btn_check_console.setEnabled(True)
            self._set_console_hint(f"检查失败：{msg}", error=True)

        run_async(lambda: updater.check(url), done, fail)

    def _apply_console_update(self) -> None:
        if not self._remote_version:
            self._set_console_hint("先点「检查更新」，拿到新版本号之后再更新。", error=True)
            return
        url = updater.package_url(self._remote_version)
        ans = QMessageBox.question(
            self, "确认更新控制台",
            f"将从<br><code>{url}</code><br>下载新包并替换 <b>app/</b> 与 <b>tools/</b>。<br><br>"
            "你的 <b>home/</b>（会话、配置、插件）、<b>harness/</b>、<b>runtime/</b> 都不会动，"
            "旧代码会备份成 app.bak-&lt;时间&gt;。<br><br>确定吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if ans != QMessageBox.Yes:
            return
        self.btn_update_console.setEnabled(False)
        self.btn_check_console.setEnabled(False)
        self._set_console_hint("正在更新…")

        def done(msg: str) -> None:
            self.btn_update_console.setEnabled(True)
            self.btn_check_console.setEnabled(True)
            self._render_console()
            self._set_console_hint(msg, ok=True)

        def fail(msg: str) -> None:
            self.btn_update_console.setEnabled(True)
            self.btn_check_console.setEnabled(True)
            self._set_console_hint(f"更新失败：{msg}", error=True)

        run_async(lambda: updater.apply(url, on_progress=self._console_progress), done, fail)

    def _console_progress(self, text: str) -> None:
        """更新过程中的进度回调。

        它在**工作线程**里被调用，所以只写一个字符串字段，真正的界面更新交给
        下一个事件循环——直接碰控件就是跨线程改 UI。
        """
        self._progress_text = text

    # ---- 安装详情
    def _build_install_card(self, root: QVBoxLayout) -> None:
        card = Card("安装详情", "全部读本机文件与环境，不发网络请求")
        self.install_grid = QGridLayout()
        self.install_grid.setHorizontalSpacing(38)
        self.install_grid.setVerticalSpacing(2)
        card.body.addLayout(self.install_grid)
        root.addWidget(card)

    # ---- 运行状态
    def _build_runtime_card(self, root: QVBoxLayout) -> None:
        card = Card(
            "运行状态",
            "systemd 单元 dsh-web 的实况；「代码是否已生效」用安装时间的先后判断",
        )
        row = QHBoxLayout()
        row.setSpacing(30)
        self.m_state = Metric("单元状态")
        self.m_pid = Metric("进程 PID")
        self.m_uptime = Metric("运行时长")
        self.m_memory = Metric("内存占用")
        self.m_port = Metric("监听端口", accent=True)
        for m in (self.m_state, self.m_pid, self.m_uptime, self.m_memory, self.m_port):
            row.addWidget(m)
        row.addStretch(1)
        card.body.addLayout(row)
        self.lbl_effective = QLabel("")
        self.lbl_effective.setWordWrap(True)
        self.lbl_effective.setTextFormat(Qt.RichText)
        card.body.addWidget(self.lbl_effective)
        root.addWidget(card)

    # ---- profile
    def _build_profile_card(self, root: QVBoxLayout) -> None:
        card = Card(
            "Profile",
            f"web profile 的组成（{PROFILE_DIR}）；bundle 按顺序叠加成配置树",
        )
        self.profile_grid = QGridLayout()
        self.profile_grid.setHorizontalSpacing(38)
        self.profile_grid.setVerticalSpacing(2)
        card.body.addLayout(self.profile_grid)
        root.addWidget(card)

    # -------------------------------------------------------------- 数据
    def reload(self, force: bool = False) -> None:
        """读版本信息 + 网络查 dist-tags + 刷新运行状态。"""
        self.btn_check.setEnabled(False)
        self._set_hint("正在读取 harness 信息…")

        def job() -> tuple[harness.HarnessInfo, harness.Releases]:
            return harness.info(), harness.releases(force=force)

        def done(result: tuple[harness.HarnessInfo, harness.Releases]) -> None:
            self._info, self._releases = result
            self.btn_check.setEnabled(True)
            self._render_version(result[0], result[1])
            self._render_install(result[0])
            self._render_profile()

        def fail(msg: str) -> None:
            self.btn_check.setEnabled(True)
            self._set_hint(f"读取失败：{msg}", error=True)

        run_async(job, done, fail)
        self._refresh_runtime()

    def _refresh_runtime(self) -> None:
        """运行状态走 2 秒 TTL 的共享快照，和侧边栏/控制台页不重复 fork。"""
        run_async(service.get_status_cached, self._render_runtime, None)

    def _set_hint(self, text: str, *, error: bool = False, ok: bool = False) -> None:
        color = self.theme.danger if error else (self.theme.ok if ok else self.theme.text_dim)
        self.hint.setStyleSheet(
            f"color: {color}; background: transparent; font-size: 12.5px;"
        )
        self.hint.setText(text)

    # ------------------------------------------------------------ 渲染
    def _render_version(self, inf: harness.HarnessInfo, rel: harness.Releases) -> None:
        """刷新整个版本卡片。

        ``inf`` 是**当前选中来源**的信息（侧边栏版本号、需重启判断都用它）；
        两份各自的展示交给 :meth:`_render_targets`——它自己会去查两份的状态。
        """
        if inf.installed:
            self.version_changed.emit(inf.installed)
        self._refill_channels(rel)
        self._render_targets()

        if rel.error:
            self._set_hint(f"取不到 npm 版本信息：{rel.error}", error=True)
        else:
            newer = [c for c in harness.CHANNELS
                     if harness.is_newer(rel.tags.get(c, ""), inf.installed)]
            if newer:
                self._set_hint(
                    f"已安装 {inf.installed}；"
                    + "、".join(f"{c} 有 {rel.tags[c]}" for c in newer)
                    + "。升级不会自动重启，见下方「运行状态」。"
                )
            else:
                self._set_hint(f"已安装 {inf.installed}，各通道都不比它新。")

    def _refill_channels(self, rel: harness.Releases) -> None:
        """把各通道当前版本填进下拉框。

        只改文字、不重建控件——用户可能正开着这个下拉，clear() 会把选择弹回去。
        """
        base = {"latest": "latest（稳定）", "next": "next（预览）", "alpha": "alpha（内测）"}
        for i in range(self.channel_box.count()):
            channel = self.channel_box.itemData(i)
            version = rel.tags.get(channel, "")
            text = base.get(channel, channel) + (f" → {version}" if version else "")
            self.channel_box.setItemText(i, text)

    def _render_install(self, inf: harness.HarnessInfo) -> None:
        while self.install_grid.count():
            item = self.install_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        rows = [
            ("安装路径", inf.install_path or "—"),
            ("可执行文件", inf.bin_path or "—"),
            ("Node", inf.node_version or "—"),
            ("npm", inf.npm_version or "—"),
            ("npm registry", inf.registry or "—"),
            ("升级权限", inf.permission_note),
            ("包名", harness.PKG),
        ]
        if inf.error:
            rows.append(("错误", inf.error))
        for i, (k, v) in enumerate(rows):
            self.install_grid.addWidget(kv_row(k, v, self.theme), i, 0)

    def _render_runtime(self, st: service.ServiceStatus) -> None:
        if st.unit_running:
            self.m_state.set_value("运行中")
        elif st.foreign_running:
            self.m_state.set_value("运行中（非 systemd）")
        else:
            self.m_state.set_value(st.label)
        self.m_pid.set_value(str(st.effective_pid) if st.effective_pid else "—")
        self.m_uptime.set_value(st.uptime_text)
        self.m_memory.set_value(st.memory_text)
        self.m_port.set_value(str(st.port) if st.port else "—")
        self._render_effective(st)

    def _render_effective(self, st: service.ServiceStatus) -> None:
        """磁盘上的代码 vs 正在跑的进程，谁新？

        升级只替换文件，已启动的进程仍是旧代码。拿 package.json 的 mtime 和进程
        启动时刻比：文件更新 ⇒ 新代码还没生效，必须重启。
        """
        pkg_time = harness.package_mtime()
        started = None
        if st.unit_running and st.active_enter is not None:
            started = st.active_enter.timestamp()
        elif st.listener_pid:
            # 非托管实例：用 /proc 推出的已运行秒数反算启动时刻
            started = datetime.now().timestamp() - st.listener_uptime

        if not pkg_time or started is None:
            self.lbl_effective.setText("")
            return
        pkg_text = datetime.fromtimestamp(pkg_time).strftime("%m-%d %H:%M")
        run_text = datetime.fromtimestamp(started).strftime("%m-%d %H:%M")
        if pkg_time > started + 1:
            self.lbl_effective.setStyleSheet(
                f"color: {self.theme.warn}; background: transparent; font-size: 12.5px;"
            )
            self.lbl_effective.setText(
                f"⚠️ <b>磁盘上的代码比正在运行的进程新</b>"
                f"（安装于 {pkg_text}，进程启动于 {run_text}）——"
                f"新版本还没生效，点上面的「重启使其生效」。"
            )
        else:
            self.lbl_effective.setStyleSheet(
                f"color: {self.theme.text_dim}; background: transparent; font-size: 12.5px;"
            )
            self.lbl_effective.setText(
                f"运行中的就是当前安装的版本（安装于 {pkg_text}，进程启动于 {run_text}）。"
            )

    def _render_profile(self) -> None:
        while self.profile_grid.count():
            item = self.profile_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        bundles: list[str] = []
        try:
            data = json.loads((PROFILE_DIR / "package.json").read_text(encoding="utf-8"))
            bundles = list(data.get("dsh", {}).get("profile", {}).get("bundles", []))
        except (OSError, json.JSONDecodeError):
            pass
        rows = [
            ("profile 目录", str(PROFILE_DIR)),
            ("bundle 数量", str(len(bundles)) if bundles else "—"),
            ("bundle 顺序", " → ".join(bundles) if bundles else "—"),
        ]
        try:
            plugins = plugin_manager.installed()
            rows.append(("已安装插件", f"{len(plugins)} 个"))
            rows.append((
                "patch 层托管停用",
                "、".join(plugin_manager.managed_disabled_ids()) or "（空）",
            ))
        except Exception as exc:  # noqa: BLE001 - 读插件失败不该让整页塌掉
            rows.append(("已安装插件", f"读取失败：{type(exc).__name__}"))
        for i, (k, v) in enumerate(rows):
            self.profile_grid.addWidget(kv_row(k, v, self.theme), i, 0)

    # ------------------------------------------------------------ 动作
    def _restart(self, quiet: bool = False, on_done=None) -> None:
        """重启 harness。``quiet`` = 升级后的自动重启（不弹二次确认）。

        升级完的那次重启是**用户已经确认过**的（确认框里写明了会自动重启），
        再弹一次"确定要重启吗"就是重复打扰。
        """
        if not quiet:
            ans = QMessageBox.question(
                self,
                "确认重启",
                "重启 harness 会断开所有正在进行的会话（包括浏览器里开着的界面）。\n"
                "浏览器登录态会在干净退出时保存。确定重启吗？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if ans != QMessageBox.Yes:
                return
        self.btn_restart.setEnabled(False)
        self._set_hint("正在重启 harness…")

        def job() -> None:
            service.restart()

        def done(_: object) -> None:
            service.wait_for_url(attempts=40, delay=1.0)
            self.btn_restart.setEnabled(True)
            self._set_hint("已重启，新版本应当已经生效。", ok=True)
            self._refresh_runtime()
            if callable(on_done):
                on_done()

        def fail(msg: str) -> None:
            self.btn_restart.setEnabled(True)
            self._set_hint(f"重启失败：{msg}", error=True)

        run_async(job, done, fail)

    # ------------------------------------------------------------ 主题
    def apply_theme(self, theme: Theme) -> None:
        self.theme = theme
        self.lbl_pending.setStyleSheet(
            f"color: {theme.warn}; background: transparent; font-size: 12.5px;"
        )
        # 安装/profile 两栏的 kv_row 里存着旧主题的颜色，得重建
        if self._info is not None:
            self._render_install(self._info)
        self._render_profile()
