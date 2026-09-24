"""多套亮/暗主题。

每套主题是一组调色板，由 :func:`build_qss` 渲染成 Qt 样式表。所有控件只靠
objectName / 动态属性选样式，因此新增主题不需要改界面代码。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Theme:
    """一套主题的调色板。"""

    key: str
    name: str
    dark: bool
    bg: str           # 窗口底色
    surface: str      # 卡片
    surface_alt: str  # 次级卡片 / 输入框
    border: str
    text: str
    text_dim: str
    text_faint: str
    accent: str
    accent_hover: str
    accent_text: str
    ok: str
    warn: str
    danger: str
    peak: str         # 峰时段强调色
    offpeak: str      # 谷时段强调色
    sidebar: str
    sidebar_active: str
    shadow: str = "rgba(0,0,0,0.35)"

    @property
    def tag(self) -> str:
        return "暗色" if self.dark else "亮色"


THEMES: tuple[Theme, ...] = (
    Theme(
        key="midnight",
        name="午夜蓝",
        dark=True,
        bg="#0f1420", surface="#171d2c", surface_alt="#1e2637", border="#28324a",
        text="#e8edf7", text_dim="#9aa8c4", text_faint="#61708f",
        accent="#4c8dff", accent_hover="#6ba1ff", accent_text="#0b1020",
        ok="#3ecf8e", warn="#f5b93b", danger="#ff6b6b",
        peak="#ff8f5e", offpeak="#3ecf8e",
        sidebar="#121826", sidebar_active="#1e2637",
    ),
    Theme(
        key="obsidian",
        name="曜石黑",
        dark=True,
        bg="#121212", surface="#1c1c1e", surface_alt="#242426", border="#333335",
        text="#f2f2f4", text_dim="#a0a0a6", text_faint="#6b6b72",
        accent="#c8a24a", accent_hover="#dcb75f", accent_text="#1a1408",
        ok="#57c98a", warn="#e0a83c", danger="#e4626a",
        peak="#e0913f", offpeak="#57c98a",
        sidebar="#18181a", sidebar_active="#242426",
    ),
    Theme(
        key="nebula",
        name="星云紫",
        dark=True,
        bg="#151020", surface="#1e1830", surface_alt="#271f3d", border="#382d55",
        text="#ece7f8", text_dim="#a99cc7", text_faint="#6f6390",
        accent="#a97bff", accent_hover="#bb95ff", accent_text="#140c26",
        ok="#4fd6a8", warn="#f0b64a", danger="#ff7096",
        peak="#ff9a6b", offpeak="#4fd6a8",
        sidebar="#191330", sidebar_active="#271f3d",
    ),
    Theme(
        key="abyss",
        name="深海青",
        dark=True,
        bg="#0a1618", surface="#102224", surface_alt="#163032", border="#1f4245",
        text="#e2f2f1", text_dim="#93b5b4", text_faint="#5d7d7c",
        accent="#2fd4c8", accent_hover="#57e2d8", accent_text="#04201e",
        ok="#4fd18b", warn="#e8b04a", danger="#f2705f",
        peak="#ffab5e", offpeak="#2fd4c8",
        sidebar="#0d1d1f", sidebar_active="#163032",
    ),
    Theme(
        key="daylight",
        name="晨曦白",
        dark=False,
        bg="#f4f6fb", surface="#ffffff", surface_alt="#eef1f8", border="#dde3ef",
        text="#1b2233", text_dim="#5a6784", text_faint="#8d99b3",
        accent="#2f6bff", accent_hover="#1e58e8", accent_text="#ffffff",
        ok="#12a06a", warn="#c07c11", danger="#d94141",
        peak="#e2703a", offpeak="#12a06a",
        sidebar="#ffffff", sidebar_active="#eef1f8",
        shadow="rgba(20,30,60,0.12)",
    ),
    Theme(
        key="sand",
        name="暖沙",
        dark=False,
        bg="#faf6f0", surface="#fffdf9", surface_alt="#f3ece1", border="#e6dccb",
        text="#2c2620", text_dim="#6d6154", text_faint="#9c8f7e",
        accent="#c1743a", accent_hover="#a95f2c", accent_text="#ffffff",
        ok="#3f8f5f", warn="#b5821f", danger="#c4453c",
        peak="#c1743a", offpeak="#3f8f5f",
        sidebar="#fffdf9", sidebar_active="#f3ece1",
        shadow="rgba(90,70,40,0.14)",
    ),
    Theme(
        key="sakura",
        name="樱雾",
        dark=False,
        bg="#fbf5f7", surface="#ffffff", surface_alt="#f7ecf1", border="#ecd9e2",
        text="#33232b", text_dim="#7a616c", text_faint="#a8929b",
        accent="#d6538a", accent_hover="#bf4276", accent_text="#ffffff",
        ok="#3f9a76", warn="#c08820", danger="#d4444a",
        peak="#e0705a", offpeak="#3f9a76",
        sidebar="#ffffff", sidebar_active="#f7ecf1",
        shadow="rgba(120,70,90,0.14)",
    ),
)

THEME_BY_KEY = {t.key: t for t in THEMES}
DEFAULT_THEME = "midnight"


def get_theme(key: str) -> Theme:
    return THEME_BY_KEY.get(key, THEME_BY_KEY[DEFAULT_THEME])


#: 界面基准字号（px）。各主题里个别控件另有更小的字号，那是刻意的层级。
BASE_FONT_PX = 13.0

_FONT_FAMILIES = [
    "Noto Sans CJK SC", "Source Han Sans SC", "Microsoft YaHei",
    "PingFang SC", "WenQuanYi Micro Hei", "sans-serif",
]


def build_font():
    """界面的基准字体。**在 QApplication 上设一次**，控件继承。

    为什么不用样式表：`* {{ font-family: … }}` 会让 Qt 给**每个控件**单独解析字体，
    中文还要走一遍回退链。实测把它从 QSS 移到 QApplication 之后，
    换主题和滚动都明显变快。
    """
    from PySide6.QtGui import QFont

    f = QFont()
    f.setFamilies(_FONT_FAMILIES)
    f.setPixelSize(int(BASE_FONT_PX))
    return f


def build_qss(t: Theme) -> str:
    """把调色板渲染成 Qt 样式表。"""
    return f"""
/* ⚠️ 这里**不要**再用 `* {{ font-family: … }}`。
   通配规则会让每个控件都单独去解析一次字体，中文字体还要走回退链——实测这是
   换主题慢（2.3 秒/次）和滚动掉帧的主要来源之一。字体改成在 QApplication 上设一次
   （见 build_font / MainWindow.apply_theme），控件自动继承。 */
QWidget {{
    background: {t.bg};
    color: {t.text};
    outline: none;
}}
/* 滚动页的内层容器与滚动区视口都要显式给背景色。
   纯 QWidget 只有配合 WA_StyledBackground 才会画 QSS 背景，而视口默认用的是
   调色板底色——两者不一致时，卡片区下方会露出一大片异色，中间一道横向断裂。 */
/* 内容区的底色用**侧边栏色**，不要用 {t.bg}。
   {t.bg} 是整套配色里最暗的一档（比卡片和侧边栏都暗），而页面留白是围着卡片一圈的——
   那一圈就成了一目了然的"黑框"（用户圈出来问的就是它）。
   换成侧边栏色之后，整个窗口外围是一个色调，卡片靠自己的 surface + 描边浮在上面。 */
#PageBody, #PageScroll, #PageScroll > QWidget > QWidget, QStackedWidget, #RightPane {{
    background: {t.bg};
    border: none;
}}
/* 侧边栏那几层必须用**侧边栏色**，不能沿用页面色。
   之前这里统一写成了 {t.bg}，于是品牌区（顶部）和页脚（底部）是侧边栏色、
   中间的导航滚动区是更暗的页面色——看起来就像一根黑柱插在中间，
   而且和右边页面之间也露出一条黑缝（用户截图里那两处）。 */
/* 侧边栏用**抬升面**色（和卡片同一档），内容留白用底色那一档：
   两者有明显对比，又都不"发黑"，中间靠 1px 描边分界——既不是黑柱也不是糊成一片。 */
#Sidebar {{
    background: {t.bg};
    border-right: 1px solid {t.border};
}}
#SidebarNav, #SidebarScroll, #SidebarScroll > QWidget > QWidget {{
    background: {t.bg};
    border: none;
}}
/* 侧边栏里的滚动条：轨道要跟侧边栏同色，否则又是一条异色竖条 */
#SidebarScroll QScrollBar:vertical {{
    background: {t.bg};
    width: 8px;
    margin: 0;
}}
#SidebarScroll QScrollBar::handle:vertical {{
    background: {t.border};
    border-radius: 4px;
    min-height: 24px;
}}
#SidebarScroll QScrollBar::add-line, #SidebarScroll QScrollBar::sub-line {{
    height: 0; background: none;
}}
#SidebarScroll QScrollBar::add-page, #SidebarScroll QScrollBar::sub-page {{
    background: {t.bg};
}}
QToolTip {{
    background: {t.surface_alt};
    color: {t.text};
    border: 1px solid {t.border};
    border-radius: 6px;
    padding: 6px 8px;
}}

/* ---------- 顶部功能区 ----------
   通栏一条，和下面的页面紧贴，靠 1px 下边线分界（harness 的顶栏就是这个做法）。 */
#TopBar {{
    background: {t.bg};
    border-bottom: 1px solid {t.border};
}}
#TopBarTitle {{
    font-size: 13.5px;
    font-weight: 600;
    color: {t.text};
    background: transparent;
}}
#TopBarStatus {{
    font-size: 12px;
    color: {t.text_dim};
    background: transparent;
}}

/* ---------- 侧边栏 ---------- */
#Brand {{
    font-size: 17px;
    font-weight: 700;
    color: {t.text};
    padding: 18px 18px 4px 18px;
    background: transparent;
}}
#BrandSub {{
    font-size: 11px;
    color: {t.text_faint};
    padding: 0 18px 16px 18px;
    background: transparent;
}}
#NavButton {{
    background: transparent;
    border: none;
    border-radius: 9px;
    padding: 10px 14px;
    margin: 3px 10px;
    text-align: left;
    font-size: 13.5px;
    color: {t.text_dim};
}}
#NavButton:hover {{
    background: {t.surface_alt};
    color: {t.text};
}}
#NavButton:checked {{
    background: {t.sidebar_active};
    color: {t.accent};
    font-weight: 600;
}}
/* ---------- 侧边栏底部的版本按钮 ----------
   它同时是"当前 harness 版本"的展示和进入 Harness 页的入口，所以比导航项更收敛：
   字号小一号、用等宽感的分隔、默认低对比，悬停/选中才亮起来。 */
#VersionButton {{
    background: {t.surface_alt};
    border: 1px solid {t.border};
    border-radius: 9px;
    padding: 8px 12px;
    margin: 6px 12px 2px 12px;
    text-align: left;
    font-size: 12px;
    color: {t.text_faint};
}}
#VersionButton:hover {{
    border-color: {t.accent};
    color: {t.accent};
}}
#VersionButton:checked {{
    border-color: {t.accent};
    color: {t.accent};
    font-weight: 600;
}}

#SidebarFooter {{
    color: {t.text_faint};
    font-size: 11px;
    padding: 12px 18px;
    background: transparent;
}}

/* ---------- 卡片 ---------- */
/* harness 风格的卡片：**不是浮动的圆角盒子**，而是一段扁平内容，
   靠一条 1px 上边线和上面的内容分开。整页看起来是一整块，而不是一堆方块。 */
#Card {{
    background: {t.bg};      /* 不透明：看起来和 transparent 一样（页面底就是它），
                                但省掉了每次重绘都要合成父级背景的开销 */
    border: none;
    border-top: 1px solid {t.border};
    border-radius: 0;
}}
/* 每页**第一张**卡片不画上边线。
   它头顶就是顶部功能区的 border-bottom，两条线只隔一个页面留白——
   视觉上就是"一长一短两根线"，很碍眼。留长的那条（通栏的），去掉短的。 */
#Card[firstCard="true"] {{
    border-top: none;
}}
/* 标题下加一条极淡的分隔线：卡片内部"标题 / 说明 / 内容"三段本来只靠留白分，
   窗口一拉长就糊成一片。加条线，分区一眼可见又不抢眼。 */
#CardTitle {{
    background: transparent;
    padding-bottom: 2px;
}}
#CardTitle {{
    font-size: 14px;
    font-weight: 600;
    color: {t.text};
    background: transparent;
}}
#CardHint {{
    font-size: 11.5px;
    color: {t.text_faint};
    background: transparent;
}}
#PageTitle {{
    font-size: 21px;
    font-weight: 700;
    color: {t.text};
    background: transparent;
}}
#PageSubtitle {{
    font-size: 12.5px;
    color: {t.text_dim};
    background: transparent;
}}

/* ---------- 指标 ---------- */
#MetricValue {{
    font-size: 21px;
    font-weight: 700;
    color: {t.text};
    background: transparent;
}}
#MetricLabel {{
    font-size: 11.5px;
    color: {t.text_faint};
    background: transparent;
}}
#MetricValueAccent {{ font-size: 21px; font-weight: 700; color: {t.accent}; background: transparent; }}

/* ---------- 状态徽章 ---------- */
#Pill {{
    border-radius: 11px;
    padding: 4px 13px;
    font-size: 12px;
    font-weight: 600;
}}

/* ---------- 按钮 ---------- */
QPushButton {{
    background: {t.surface_alt};
    color: {t.text};
    border: 1px solid {t.border};
    border-radius: 9px;
    padding: 9px 18px;
    font-size: 13px;
}}
QPushButton:hover {{ border-color: {t.accent}; color: {t.accent}; }}
QPushButton:pressed {{ background: {t.border}; }}
QPushButton:disabled {{ color: {t.text_faint}; border-color: {t.border}; background: {t.surface}; }}

QPushButton#Primary {{
    background: {t.accent};
    color: {t.accent_text};
    border: 1px solid {t.accent};
    font-weight: 600;
}}
QPushButton#Primary:hover {{ background: {t.accent_hover}; border-color: {t.accent_hover}; }}
QPushButton#Primary:disabled {{ background: {t.surface_alt}; color: {t.text_faint}; border-color: {t.border}; }}

QPushButton#Danger {{ color: {t.danger}; }}
QPushButton#Danger:hover {{ border-color: {t.danger}; background: {t.surface_alt}; }}
/* #Danger 是 ID 选择器，优先级高于 `QPushButton:disabled`（CSS 里 ID > 类型+伪类），
   不单独写禁用态的话「停止」按钮在不可用时仍是鲜红色，看着像能点。 */
QPushButton#Danger:disabled {{ color: {t.text_faint}; border-color: {t.border}; background: {t.surface}; }}

/* 「强制修复端口占用」：救援按钮，要够显眼才找得到（用户原话："必须要够猛"）。
   实心 warn 色（不是描边）：同排的按钮清的清一色是描边样式，实心块才一眼能认出来。
   颜色也不用 #Danger 的纯红——那和旁边的「停止」撞脸，而且它动手的是**别人的**进程，
   语义是"抢救"而不是"关停"。hover/按下加亮，按下去有分量。 */
QPushButton#ForceFix {{
    background: {t.warn};
    color: {t.accent_text};
    border: 1px solid {t.warn};
    font-weight: 700;
    padding: 7px 18px;
}}
QPushButton#ForceFix:hover {{
    background: {t.danger};
    border-color: {t.danger};
    color: #ffffff;
}}
QPushButton#ForceFix:pressed {{ background: {t.danger}; border-color: {t.danger}; color: #ffffff; }}
QPushButton#ForceFix:disabled {{ color: {t.text_faint}; border-color: {t.border}; background: {t.surface}; }}
QPushButton#Ghost {{ background: transparent; }}

/* 分段切换（插件 / 技能 二合一页） */
QPushButton#Segment {{
    background: {t.surface_alt};
    border: 1px solid {t.border};
    border-radius: 8px;
    padding: 7px 16px;
    margin-right: 6px;
    color: {t.text_dim};
}}
QPushButton#Segment:hover {{ color: {t.text}; }}
QPushButton#Segment:checked {{
    background: {t.accent};
    border-color: {t.accent};
    color: {t.accent_text};
    font-weight: 600;
}}

/* ---------- 输入 ---------- */
QComboBox, QLineEdit, QSpinBox {{
    background: {t.surface_alt};
    border: 1px solid {t.border};
    border-radius: 8px;
    padding: 7px 10px;
    color: {t.text};
    selection-background-color: {t.accent};
    selection-color: {t.accent_text};
}}
QComboBox:hover, QLineEdit:focus {{ border-color: {t.accent}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {t.surface};
    border: 1px solid {t.border};
    border-radius: 8px;
    selection-background-color: {t.accent};
    selection-color: {t.accent_text};
    padding: 4px;
}}

/* ---------- 表格 ---------- */
/* 注意：FixedTable 用的是 QTableView + 自定义模型（渲染虚拟化），
   所以选择器必须同时覆盖 QTableView —— 只写 QTableWidget 的话，
   QTableView 会退回系统默认样式，隔行变成刺眼的白底。 */
QTableView, QTableWidget, QTreeWidget {{
    background: {t.surface};
    alternate-background-color: {t.surface_alt};
    border: 1px solid {t.border};
    border-radius: 10px;
    gridline-color: {t.border};
    selection-background-color: {t.surface_alt};
    selection-color: {t.text};
}}
QTableView::item, QTableWidget::item, QTreeWidget::item {{
    padding: 7px 8px;
    border: none;
    color: {t.text};
}}
QTableView::item:selected, QTableWidget::item:selected {{ color: {t.text}; }}
QHeaderView::section {{
    background: {t.surface_alt};
    color: {t.text_dim};
    border: none;
    border-bottom: 1px solid {t.border};
    padding: 9px 8px;
    font-size: 12px;
    font-weight: 600;
}}
QTableCornerButton::section {{ background: {t.surface_alt}; border: none; }}

/* ---------- 滚动条 ---------- */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{
    background: {t.border}; border-radius: 5px; min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{ background: {t.text_faint}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {t.border}; border-radius: 5px; min-width: 28px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ---------- 其它 ---------- */
QPlainTextEdit, QTextEdit {{
    background: {t.surface};
    border: 1px solid {t.border};
    border-radius: 10px;
    color: {t.text};
    font-family: "JetBrains Mono", "Fira Code", "Noto Sans Mono CJK SC", monospace;
    font-size: 12px;
    selection-background-color: {t.accent};
    selection-color: {t.accent_text};
}}
QProgressBar {{
    background: {t.surface_alt};
    border: none;
    border-radius: 5px;
    height: 8px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{ background: {t.accent}; border-radius: 5px; }}
QCheckBox {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator {{
    width: 16px; height: 16px;
    border: 1px solid {t.border};
    border-radius: 4px;
    background: {t.surface_alt};
}}
QCheckBox::indicator:checked {{ background: {t.accent}; border-color: {t.accent}; }}
QSplitter::handle {{ background: {t.border}; }}

/* ---------- 捐赠页 ---------- */
/* 感谢语：这一页唯一的大字，别再给它加卡片标题（会和大字抢层级） */
#DonateThanks {{
    font-size: 19px;
    font-weight: 700;
    color: {t.text};
    background: transparent;
}}
#DonateSub {{
    font-size: 12.5px;
    color: {t.text_dim};
    background: transparent;
}}
/* 联系方式：用强调色，让"想支持的人怎么找到我"一眼可见，但不抢感谢语的字号 */
#DonateContact {{
    font-size: 12.5px;
    color: {t.accent};
    background: transparent;
    padding-top: 3px;
}}
/* 收款码：两张等大的框，靠 surface + 描边从页面底色上浮起来。
   图片本身是**整张海报**（自带品牌底色和白卡），所以框内不再垫白底——
   垫了会在海报的四个圆角处露出一圈白边。 */
#QrBox {{
    background: {t.surface};
    border: 1px solid {t.border};
    border-radius: 12px;
}}
#QrBox:hover {{ border-color: {t.accent}; }}
#QrTitle {{
    font-size: 13px;
    font-weight: 600;
    color: {t.text};
    background: transparent;
}}
#QrTip {{
    font-size: 11.5px;
    color: {t.text_faint};
    background: transparent;
}}
#QrImage {{
    background: transparent;
    border: none;
}}
/* 图片缺失时的占位说明：用警示色 + 虚线框，一眼看出是"缺文件"而不是"排版坏了" */
#QrMissing {{
    background: {t.surface_alt};
    border: 1px dashed {t.border};
    border-radius: 10px;
    color: {t.warn};
    font-size: 11.5px;
}}
/* 感谢者名单 */
#DonorList {{ background: transparent; }}
#DonorRow {{
    background: transparent;
    border-bottom: 1px solid {t.border};
}}
/* 最后一行不画线，否则名单底下会多出一条悬空的横线 */
#DonorRow[lastRow="true"] {{ border-bottom: none; }}
#DonorRank {{
    color: {t.text_faint};
    font-size: 11.5px;
    font-weight: 600;
    background: transparent;
}}
#DonorName {{
    color: {t.text};
    font-size: 13.5px;
    font-weight: 600;
    background: transparent;
}}
#DonorAmount {{
    color: {t.ok};
    font-size: 12.5px;
    font-weight: 600;
    background: transparent;
}}
#DonorDate {{
    color: {t.text_faint};
    font-size: 11.5px;
    background: transparent;
}}
/* 留言缩进到和名字对齐（让开编号那一段） */
#DonorMsg {{
    color: {t.text_dim};
    font-size: 12.5px;
    background: transparent;
    padding-left: 26px;
}}
#DonorEmpty {{
    color: {t.text_dim};
    font-size: 12.5px;
    background: transparent;
    padding: 8px 2px;
}}

QFrame#HLine {{ background: {t.border}; max-height: 1px; border: none; }}
"""
