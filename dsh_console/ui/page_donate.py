"""捐赠页：感谢语 + 微信 / 支付宝收款码 + 感谢者名单。

**名单和收款码都是数据，不是代码**：它们放在项目根的 ``donate/`` 目录里，
再多一位捐赠者只要改 ``donors.json``，不必碰源码、也不必重新打包。
名字和金额一律不写死在这里——写死就意味着每次有人捐赠都要改代码重发版本。

目录与文件名一律 ASCII（``donate/wechat-qr.png`` 这种）：这个仓库按全英文名发布，
中文路径在 URL、zip 和别人的命令行里都会被转义成一长串 %E6%8E%A7…。

资源目录可用环境变量 ``DSH_CONSOLE_DONATE_DIR`` 覆盖：便携包里数据目录有时
不在 ``app/`` 下面，留一个出口，不用为此改代码。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..screenfit import apply_screen_fit
from .. import i18n
from ..themes import Theme
from .components import Card, ScrollPage

#: 项目根目录（dsh_console/ui/page_donate.py 往上两级）
ROOT = Path(__file__).resolve().parents[2]

DIR_ENV = "DSH_CONSOLE_DONATE_DIR"
#: 目录与文件名一律用 ASCII：仓库要按全英文名发布，中文路径在 URL、zip、
#: 以及别人的命令行里都会被转义成一长串 %E6%8E%A7…，分享和排错都难受。
DIR_NAME = "donate"
WECHAT_IMAGE = "wechat-qr.png"
ALIPAY_IMAGE = "alipay-qr.jpg"
DONORS_FILE = "donors.json"

#: 收款码展示区尺寸。**写死**而不是按宽度缩放：两张海报比例不同
#: （微信 1118×1524、支付宝 1080×1620），按宽度缩放会得到两个不同的高度，
#: 并排时下边缘一高一低。固定展示区 + 图片等比居中，两张卡片就一定一样大。
QR_W, QR_H = 280, 390


def donate_dir() -> Path:
    """收款码与名单所在的目录。"""
    override = os.environ.get(DIR_ENV)
    return Path(override).expanduser() if override else ROOT / DIR_NAME


def load_donors(path: Path) -> tuple[list[dict[str, str]], str]:
    """读捐赠名单，返回 ``(名单, 错误说明)``；错误说明为空串表示一切正常。

    容错是刻意的：这个文件是**用户手改的**，写成纯字符串数组、用中文字段名、
    甚至干脆没建，都应该能用——只有真正解析不了时才报错，而且报错要让人知道
    问题出在哪个文件、哪一行，而不是悄悄显示成"还没有捐赠者"。
    """
    if not path.is_file():
        return [], ""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [], f"{type(exc).__name__}: {exc}"

    if isinstance(raw, dict):                      # 允许 {"donors": [...]}
        raw = raw.get("donors", raw.get("捐赠者", raw.get("名单", [])))
    if not isinstance(raw, list):
        return [], '名单应当是数组，或 {"donors": [...]} 这种形式'

    donors: list[dict[str, str]] = []
    for item in raw:
        if isinstance(item, str):                  # 允许 ["张三", "李四"]
            item = {"name": item}
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("名字") or "").strip()
        if not name:
            continue                               # 没名字的行直接跳过，不当成错误
        donors.append({
            "name": name,
            "amount": str(item.get("amount") or item.get("金额") or "").strip(),
            "date": str(item.get("date") or item.get("日期") or "").strip(),
            "message": str(item.get("message") or item.get("留言") or "").strip(),
        })
    return donors, ""


class _DonorRow(QFrame):
    """名单里的一行：编号 / 名字 / 金额 / 日期，留言另起一行。"""

    def __init__(self, index: int, donor: dict[str, str], last: bool,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DonorRow")
        self.setAttribute(Qt.WA_StyledBackground, True)
        # 最后一行不画分隔线（否则名单底下会多出一条悬空的线）。
        # 动态属性要在**加进布局之前**设好：控件第一次 polish 时才会匹配到样式。
        self.setProperty("lastRow", last)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 9, 2, 9)
        lay.setSpacing(3)

        top = QHBoxLayout()
        top.setSpacing(10)

        rank = QLabel(f"{index:02d}")
        rank.setObjectName("DonorRank")
        top.addWidget(rank)

        name = QLabel(donor["name"])
        name.setObjectName("DonorName")
        name.setTextInteractionFlags(Qt.TextSelectableByMouse)
        top.addWidget(name)

        if donor["amount"]:
            amount = QLabel(donor["amount"])
            amount.setObjectName("DonorAmount")
            top.addWidget(amount)

        top.addStretch(1)

        if donor["date"]:
            date = QLabel(donor["date"])
            date.setObjectName("DonorDate")
            top.addWidget(date)

        lay.addLayout(top)

        if donor["message"]:
            msg = QLabel(f"“{donor['message']}”")
            msg.setObjectName("DonorMsg")
            msg.setWordWrap(True)
            msg.setTextInteractionFlags(Qt.TextSelectableByMouse)
            # 左边留出编号那一段的宽度，留言才对得齐名字而不是编号
            lay.addWidget(msg)


class _QrBox(QFrame):
    """一张收款码：标题 + 图片 + 提示；图片缺失时降级成一句说明，不是空白。

    不接 ``theme``：配色全归 ``themes.build_qss`` 按 objectName 管，
    控件自己拿一份主题色反而会在换主题时留下没刷新的旧颜色。
    """

    clicked = None      # 由 DonatePage 填：点击放大

    def __init__(self, title: str, filename: str,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("QrBox")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.title = title
        self.path = donate_dir() / filename
        self.pixmap = QPixmap(str(self.path)) if self.path.is_file() else QPixmap()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(9)

        cap = QLabel(i18n.tr(title))
        cap.setObjectName("QrTitle")
        self.cap = cap            # 切语言时要改它
        cap.setAlignment(Qt.AlignCenter)
        lay.addWidget(cap)

        self.pic = QLabel()
        self.pic.setObjectName("QrImage")
        self.pic.setAlignment(Qt.AlignCenter)
        self.pic.setFixedSize(QR_W, QR_H)
        if self.pixmap.isNull():
            self.pic.setText(f"未找到收款码图片\n{self.path.name}\n\n期望位置：\n{self.path.parent}")
            self.pic.setWordWrap(True)
            self.pic.setObjectName("QrMissing")
        else:
            self.pic.setPixmap(
                self.pixmap.scaled(QR_W, QR_H, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
            self.setCursor(Qt.PointingHandCursor)
            self.setToolTip(f"点击放大{title}（方便手机扫码）")
        lay.addWidget(self.pic)

        tip = QLabel(i18n.tr("点击放大") if not self.pixmap.isNull()
                     else "把图片放进上面这个目录即可显示")
        tip.setObjectName("QrTip")
        tip.setAlignment(Qt.AlignCenter)
        lay.addWidget(tip)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if callable(self.clicked) and not self.pixmap.isNull():
            self.clicked(self)
        super().mousePressEvent(event)


class _QrZoom(QDialog):
    """把收款码放大到手机能扫的尺寸。

    为什么必须有这一步：整张海报在页面里只有 280px 宽，而码本身只占海报宽度的
    一半左右——盯着屏幕扫很容易识别不出来。放大到接近屏幕高度，码就有一两百
    像素以上，手机才扫得动。
    """

    def __init__(self, title: str, pixmap: QPixmap, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{title} · 扫码支持")
        # 上限按屏幕算：1080p 上放到 ~930px 高，笔记本小屏上也不会顶出屏幕外
        screen = QGuiApplication.primaryScreen()
        avail = screen.availableGeometry() if screen is not None else None
        max_w = int(avail.width() * 0.42) if avail is not None else 620
        max_h = int(avail.height() * 0.84) if avail is not None else 700
        apply_screen_fit(self, (max_w + 60, max_h + 96), (320, 420))

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(12)

        pic = QLabel()
        pic.setObjectName("QrImage")
        pic.setAlignment(Qt.AlignCenter)
        pic.setPixmap(pixmap.scaled(max_w, max_h, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        lay.addWidget(pic, 0, Qt.AlignCenter)

        tip = QLabel("用手机相机或微信 / 支付宝「扫一扫」对着屏幕扫码")
        tip.setObjectName("QrTip")
        tip.setAlignment(Qt.AlignCenter)
        lay.addWidget(tip)

        btn = QPushButton("关闭")
        btn.setObjectName("Primary")
        btn.clicked.connect(self.accept)
        lay.addWidget(btn, 0, Qt.AlignCenter)


class DonatePage(ScrollPage):
    """捐赠页：感谢语、收款码、感谢者名单（数据在 donate/）。"""

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme = theme
        self.donors: list[dict[str, str]] = []
        self._build()
        self.reload()

    # ------------------------------------------------------------------ 构建
    def _build(self) -> None:
        root = self.body
        root.setSpacing(16)

        # ---- 感谢语
        # 第一张卡片：MainWindow._mark_first_cards() 会给它去掉上边线
        # （它头顶就是顶部功能区的下边线，再画一条就成了"一长一短两根线"）。
        thanks = Card()
        thanks.title_label.hide()        # 这块用大字，不要卡片标题
        self.thanks_line = QLabel(i18n.tr("❤️ 感谢你的捐赠，这是我不断打磨的动力"))
        self.thanks_line.setObjectName("DonateThanks")
        self.thanks_line.setWordWrap(True)
        thanks.body.addWidget(self.thanks_line)

        sub = QLabel(i18n.tr(
            "如果这个控制台帮到了你，欢迎请我喝杯咖啡 ☕ "
            "每一份支持我都会记在下面的名单里。"
        ))
        self.sub_line = sub
        sub.setObjectName("DonateSub")
        sub.setWordWrap(True)
        thanks.body.addWidget(sub)

        # 联系方式：捐赠页是"想支持我"的人落脚的地方，把怎么找到我写在这儿最合理。
        # 单独一行、字号小一档，不和感谢语抢视觉。
        contact = QLabel(i18n.tr(
            "📮 联系我：QQ 894597841 　·　 邮箱 894597841@163.com"
        ))
        self.contact_line = contact
        contact.setObjectName("DonateContact")
        contact.setWordWrap(True)
        contact.setTextInteractionFlags(Qt.TextSelectableByMouse)
        thanks.body.addWidget(contact)
        root.addWidget(thanks)

        # ---- 收款码（两张并排）
        self.qr_card = Card(i18n.tr("收款码"),
                            i18n.tr("微信 / 支付宝都可以。点一下图片放大，手机更容易扫上。"))
        row = QHBoxLayout()
        row.setSpacing(16)
        self.box_wechat = _QrBox("微信收款码", WECHAT_IMAGE)
        self.box_alipay = _QrBox("支付宝收款码", ALIPAY_IMAGE)
        # 两张固定尺寸的码**居中**放：卡片是通栏的，左对齐时右边会空出一大块，
        # 看着像没排完版。居中之后这一块才像个"展台"。
        row.addStretch(1)
        for box in (self.box_wechat, self.box_alipay):
            box.clicked = self._zoom
            # 顶部对齐：两张海报高度本就不同，居中对齐会让标题一高一低
            row.addWidget(box, 0, Qt.AlignTop)
        row.addStretch(1)
        self.qr_card.body.addLayout(row)
        root.addWidget(self.qr_card)

        # ---- 感谢者名单
        # 卡片右上角**不放按钮**：这一页是给访客看的，不是维护面板。
        # 名单改动由 activate() 兜住——每次切进这一页都会重读一次文件，
        # 所以改完 JSON 只要切走再切回来就是最新的，不需要「刷新」按钮。
        self.donor_card = Card(i18n.tr("感谢者名单"), i18n.tr("读取中…"))
        self._donors_host = QWidget()
        self._donors_host.setObjectName("DonorList")
        self._donors_lay = QVBoxLayout(self._donors_host)
        self._donors_lay.setContentsMargins(0, 0, 0, 0)
        self._donors_lay.setSpacing(0)
        self.donor_card.body.addWidget(self._donors_host)
        root.addWidget(self.donor_card)
        root.addStretch(1)

    # ------------------------------------------------------------------ 行为
    def activate(self) -> None:
        """每次切进这一页都重读名单：用户很可能刚在编辑器里改过 JSON。"""
        self.reload()

    def _zoom(self, box: _QrBox) -> None:
        _QrZoom(box.title, box.pixmap, self).exec()

    @staticmethod
    def _clear(lay: QVBoxLayout) -> None:
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)                 # 先脱离父级，否则 deleteLater 前还会画一帧
                w.deleteLater()

    def reload(self) -> None:
        """重读名单文件并重建整个列表。"""
        path = donate_dir() / DONORS_FILE
        donors, err = load_donors(path)
        self._clear(self._donors_lay)

        if err:
            self.donors = []
            self.donor_card.hint_label.setText(f"⚠️ {DONORS_FILE} 读不出来")
            self._donors_lay.addWidget(self._notice(
                f"<b>名单文件解析失败</b>，暂时按空名单显示。<br>"
                f"<code>{path}</code><br>{err}"
            ))
            return

        self.donors = donors
        where = f"{DIR_NAME}/{DONORS_FILE}"
        if not donors:
            self.donor_card.hint_label.setText(i18n.tr("还没有记录") + f" · {where}")
            # 空态只留一句话。**不在界面上写名单路径和 JSON 格式**：
            # 名单格式是给维护者看的，写在 README 和 donors.json 自己的
            # _说明 / _字段 / _示例 字段里就够了，摆在页面上是给访客看噪声。
            self._donors_lay.addWidget(self._notice("🌟 <b>虚位以待</b> —— 还没有捐赠记录。"))
            return

        self.donor_card.hint_label.setText(f"共 {len(donors)} 位捐赠者 · 数据来自 {where}")
        for i, d in enumerate(donors, 1):
            self._donors_lay.addWidget(_DonorRow(i, d, last=(i == len(donors))))

    def _notice(self, html: str) -> QLabel:
        lab = QLabel(html)
        lab.setObjectName("DonorEmpty")
        lab.setWordWrap(True)
        lab.setTextFormat(Qt.RichText)
        lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lab.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        return lab

    def retranslate(self) -> None:
        """按当前语言刷新本页文案（切语言时由 MainWindow 调用）。"""
        self.thanks_line.setText(i18n.tr("❤️ 感谢你的捐赠，这是我不断打磨的动力"))
        self.sub_line.setText(i18n.tr(
            "如果这个控制台帮到了你，欢迎请我喝杯咖啡 ☕ "
            "每一份支持我都会记在下面的名单里。"))
        self.contact_line.setText(
            i18n.tr("📮 联系我：QQ 894597841 　·　 邮箱 894597841@163.com"))
        self.qr_card.set_title(i18n.tr("收款码"))
        self.qr_card.hint_label.setText(
            i18n.tr("微信 / 支付宝都可以。点一下图片放大，手机更容易扫上。"))
        self.box_wechat.cap.setText(i18n.tr("微信收款码"))
        self.box_alipay.cap.setText(i18n.tr("支付宝收款码"))
        self.donor_card.set_title(i18n.tr("感谢者名单"))
        self.reload()                      # 名单说明/空态文案也跟着换

    def apply_theme(self, theme: Theme) -> None:
        # 页面本身没有写死颜色：配色全在 themes.build_qss 里按 objectName 选，
        # 换主题时 MainWindow 会重建整张样式表，这里只要记住新主题即可。
        self.theme = theme
