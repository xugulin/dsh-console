"""内置浏览器的配色：30 套主题 + 深/浅/跟随系统。

## 为什么单独一个模块

主题是**纯数据 + 纯函数**（调色板 → 样式表），和窗口逻辑放一起会把 ``browser.py``
撑得很难读；而且 30 套主题的数值是要反复对着看的，单独一个文件才好维护。

## 三件事分开管

1. **外观模式**（``外观``）：深色 / 浅色 / 跟随系统。跟随系统时用
   ``QGuiApplication.styleHints().colorScheme()`` 问系统——Qt 6.5+ 才有这个 API，
   取不到就退回深色（宁可猜错成深色，也别给一个刺眼的浅色）。
2. **具体主题**（``主题``）：30 套里挑一套。模式决定**候选范围**：选深色就只在深色
   主题里挑，选浅色只在浅色里挑——所以切模式时会自动换到该模式下的一套，
   而不是把一个深色主题硬套在浅色模式上。
3. **记住了**：两个键都写进控制台的 ``config.json``（``browserTheme`` / ``browserMode``），
   下次开还是这套。**不写浏览器 profile**，免得和网页自己的主题混在一起。

## 网页内容也跟着深

深色主题时给 Chromium 加 ``--force-dark-mode``，这样会让 CSS 的
``prefers-color-scheme: dark`` 命中——支持深色的站点（包括 harness 自己的界面）
就会按深色渲染。**注意它不会把网页反色**（那是另一个实验特性），所以不会有
"图片被反相"那种灾难。这个开关只在进程启动时读，换主题后要重开浏览器才对网页生效。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BrowserTheme:
    """一套浏览器配色。颜色都是 CSS 十六进制串（QSS 直接用）。"""

    key: str
    name: str
    dark: bool
    bg: str          # 窗口底、状态栏
    surface: str     # 工具栏、标签栏
    field: str       # 地址栏输入框
    hover: str       # 悬停
    border: str
    text: str        # 主文字
    dim: str         # 次要文字、未选中标签
    accent: str      # 强调色（选中标签的下划线、按钮）
    accent_text: str  # 强调色上的文字


#: 30 套主题。深色 20 套（需求方偏好深色），浅色 10 套。
THEMES: tuple[BrowserTheme, ...] = (
    # ---------------------------------------------------------------- 深色
    BrowserTheme("graphite", "石墨", True, "#16181d", "#1d2026", "#12141a",
                 "#272b33", "#2c313a", "#e6e9ee", "#9aa3b0", "#4f8cff", "#ffffff"),
    BrowserTheme("dracula", "德古拉", True, "#21222c", "#282a36", "#191a21",
                 "#343746", "#3a3d4d", "#f8f8f2", "#9aa0b5", "#bd93f9", "#21222c"),
    BrowserTheme("nord", "北境", True, "#2e3440", "#3b4252", "#292e39",
                 "#434c5e", "#4c566a", "#eceff4", "#a3adc2", "#88c0d0", "#2e3440"),
    BrowserTheme("gruvbox-dark", "格鲁夫暗", True, "#282828", "#32302f", "#1d2021",
                 "#3c3836", "#504945", "#ebdbb2", "#a89984", "#d79921", "#282828"),
    BrowserTheme("tokyo-night", "东京夜", True, "#1a1b26", "#1f2335", "#16161e",
                 "#292e42", "#3b4261", "#c0caf5", "#7f8cb5", "#7aa2f7", "#1a1b26"),
    BrowserTheme("catppuccin-mocha", "摩卡猫", True, "#1e1e2e", "#181825", "#11111b",
                 "#313244", "#45475a", "#cdd6f4", "#9399b2", "#cba6f7", "#1e1e2e"),
    BrowserTheme("one-dark", "原子暗", True, "#21252b", "#282c34", "#1b1e23",
                 "#2f333d", "#3e4451", "#abb2bf", "#828997", "#61afef", "#21252b"),
    BrowserTheme("solarized-dark", "日光暗", True, "#002b36", "#073642", "#00252e",
                 "#0a4351", "#586e75", "#eee8d5", "#93a1a1", "#268bd2", "#002b36"),
    BrowserTheme("monokai", "莫诺凯", True, "#1e1f1c", "#272822", "#171814",
                 "#3a3b34", "#49483e", "#f8f8f2", "#a6a28c", "#a6e22e", "#1e1f1c"),
    BrowserTheme("material-dark", "材质暗", True, "#212121", "#263238", "#1a1a1a",
                 "#2e3a40", "#37474f", "#eceff1", "#90a4ae", "#80cbc4", "#212121"),
    BrowserTheme("ayu-dark", "阿玉暗", True, "#0d1017", "#131721", "#0a0d13",
                 "#1c2330", "#273240", "#bfbdb6", "#7d8590", "#ffb454", "#0d1017"),
    BrowserTheme("night-owl", "夜枭", True, "#011627", "#0b253f", "#01121f",
                 "#123a5c", "#1d4e79", "#d6deeb", "#8296b0", "#82aaff", "#011627"),
    BrowserTheme("palenight", "淡夜", True, "#292d3e", "#2f3447", "#1f2231",
                 "#3a4055", "#4a5170", "#d0d3e2", "#959dcb", "#c792ea", "#292d3e"),
    BrowserTheme("synthwave", "合成波", True, "#241b2f", "#2b213a", "#1c1428",
                 "#3a2d50", "#4d3a68", "#f0e6ff", "#a995c9", "#ff7edb", "#241b2f"),
    BrowserTheme("rose-pine", "玫瑰松", True, "#191724", "#1f1d2e", "#14121f",
                 "#26233a", "#403d52", "#e0def4", "#908caa", "#ebbcba", "#191724"),
    BrowserTheme("kanagawa", "神奈川", True, "#1f1f28", "#2a2a37", "#181820",
                 "#363646", "#54546d", "#dcd7ba", "#9a9cae", "#7e9cd8", "#1f1f28"),
    BrowserTheme("everforest-dark", "常青暗", True, "#2b3339", "#323c41", "#232a2e",
                 "#3d484d", "#4f5b58", "#d3c6aa", "#a7c080", "#83c092", "#2b3339"),
    BrowserTheme("iceberg-dark", "冰山暗", True, "#161821", "#1e2132", "#0f1117",
                 "#2a2f45", "#3d4663", "#c6c8d1", "#8b91a8", "#84a0c6", "#161821"),
    BrowserTheme("github-dark", "GitHub 暗", True, "#0d1117", "#161b22", "#010409",
                 "#21262d", "#30363d", "#e6edf3", "#8b949e", "#58a6ff", "#0d1117"),
    BrowserTheme("vscode-dark", "VS Code 暗", True, "#1e1e1e", "#252526", "#181818",
                 "#2d2d30", "#3e3e42", "#d4d4d4", "#8a8a8a", "#007acc", "#ffffff"),
    # ---------------------------------------------------------------- 浅色
    BrowserTheme("github-light", "GitHub 亮", False, "#ffffff", "#f6f8fa", "#ffffff",
                 "#eaeef2", "#d0d7de", "#1f2328", "#656d76", "#0969da", "#ffffff"),
    BrowserTheme("solarized-light", "日光亮", False, "#fdf6e3", "#eee8d5", "#fffbf0",
                 "#e4ddc8", "#d3cbb7", "#586e75", "#93a1a1", "#268bd2", "#fdf6e3"),
    BrowserTheme("catppuccin-latte", "拿铁猫", False, "#eff1f5", "#e6e9ef", "#ffffff",
                 "#dce0e8", "#ccd0da", "#4c4f69", "#8c8fa1", "#8839ef", "#ffffff"),
    BrowserTheme("one-light", "原子亮", False, "#fafafa", "#eaeaeb", "#ffffff",
                 "#e0e0e1", "#d4d4d5", "#383a42", "#a0a1a7", "#4078f2", "#ffffff"),
    BrowserTheme("gruvbox-light", "格鲁夫亮", False, "#fbf1c7", "#f2e5bc", "#fffbef",
                 "#ebdbb2", "#d5c4a1", "#3c3836", "#7c6f64", "#b57614", "#fbf1c7"),
    BrowserTheme("ayu-light", "阿玉亮", False, "#fafafa", "#f0f0f0", "#ffffff",
                 "#e6e6e6", "#d9d9d9", "#242936", "#8a9199", "#ff9940", "#ffffff"),
    BrowserTheme("rose-pine-dawn", "玫瑰晨", False, "#faf4ed", "#f2e9e1", "#fffaf3",
                 "#e8dfd7", "#dfdad9", "#575279", "#9893a5", "#907aa9", "#faf4ed"),
    BrowserTheme("everforest-light", "常青亮", False, "#fdf6e3", "#f4f0d9", "#fffbef",
                 "#eae5cd", "#ddd8be", "#5c6a72", "#829181", "#8da101", "#fdf6e3"),
    BrowserTheme("paper", "纸白", False, "#f7f7f5", "#efefec", "#ffffff",
                 "#e5e5e1", "#d8d8d3", "#2f2f2c", "#77776f", "#3b7dd8", "#ffffff"),
    BrowserTheme("quiet-light", "静白", False, "#f5f5f4", "#ececeb", "#ffffff",
                 "#e2e2e0", "#d6d6d4", "#333333", "#8a8a86", "#7a5af5", "#ffffff"),
)

#: 主题 key → 主题。
BY_KEY: dict[str, BrowserTheme] = {t.key: t for t in THEMES}

#: 默认主题（深色系，需求方偏好）。
DEFAULT_THEME = "graphite"

#: 外观模式。
MODE_DARK = "dark"
MODE_LIGHT = "light"
MODE_SYSTEM = "system"
MODES: tuple[tuple[str, str], ...] = (
    (MODE_DARK, "深色"),
    (MODE_LIGHT, "浅色"),
    (MODE_SYSTEM, "跟随系统"),
)


def system_is_dark() -> bool:
    """系统是不是深色。取不到就当作深色——宁可猜成深色也别突然给一片白。"""
    try:
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtCore import Qt

        app = QGuiApplication.instance()
        if app is None:
            return True
        scheme = app.styleHints().colorScheme()
        if scheme == Qt.ColorScheme.Light:
            return False
        if scheme == Qt.ColorScheme.Dark:
            return True
        return True                      # Unknown：按深色处理
    except Exception:  # noqa: BLE001 - 老版本 Qt 没有这个 API
        return True


def resolve(theme_key: str, mode: str) -> BrowserTheme:
    """把 ``(主题, 模式)`` 解析成一套实际配色。

    模式决定**候选范围**：选了深色模式却拿着一套浅色主题是没意义的，所以这里会
    在候选范围里挑——优先用户指定的那套（它符合模式就用它），否则退到该模式的默认。
    """
    theme = BY_KEY.get(theme_key)
    want_dark = {"dark": True, "light": False}.get(mode, system_is_dark())

    if theme is not None and theme.dark == want_dark:
        return theme
    # 指定的主题和模式对不上：在该模式里挑第一套
    for candidate in THEMES:
        if candidate.dark == want_dark:
            return candidate
    return BY_KEY[DEFAULT_THEME]


def themes_for(mode: str) -> list[BrowserTheme]:
    """该模式下的候选主题。"""
    want_dark = {"dark": True, "light": False}.get(mode, system_is_dark())
    return [t for t in THEMES if t.dark == want_dark]


def mode_label(mode: str) -> str:
    return dict(MODES).get(mode, "深色")


def qss(t: BrowserTheme) -> str:
    """把一套配色变成 Qt 样式表。

    只作用于浏览器自己的控件（工具栏、标签栏、地址栏、菜单、状态栏）——
    **不影响网页内容**，那是 Chromium 自己的事。
    """
    return f"""
QMainWindow, QWidget#BrowserRoot, QDialog {{ background: {t.bg}; color: {t.text}; }}

/* 外观与主题对话框 */
QDialog QLabel {{ color: {t.text}; }}
QRadioButton {{ color: {t.text}; padding: 2px 4px; }}
QRadioButton::indicator {{ width: 13px; height: 13px; }}
QListWidget {{
    background: {t.surface}; color: {t.text}; border: 1px solid {t.border};
    border-radius: 8px; padding: 4px; outline: none;
}}
QListWidget::item {{ padding: 5px 8px; border-radius: 6px; }}
QListWidget::item:hover {{ background: {t.hover}; }}
QListWidget::item:selected {{ background: {t.accent}; color: {t.accent_text}; }}
QPushButton {{
    background: {t.surface}; color: {t.text}; border: 1px solid {t.border};
    border-radius: 7px; padding: 5px 14px;
}}
QPushButton:hover {{ background: {t.hover}; }}
QPushButton:pressed {{ background: {t.border}; }}

QToolBar {{
    background: {t.surface}; border: none; border-bottom: 1px solid {t.border};
    padding: 4px 6px; spacing: 2px;
}}
QToolBar QToolButton {{
    background: transparent; color: {t.text}; border: 1px solid transparent;
    border-radius: 6px; padding: 3px 9px; font-size: 14px;
}}
QToolBar QToolButton:hover {{ background: {t.hover}; }}
QToolBar QToolButton:pressed {{ background: {t.border}; }}
QToolBar QToolButton::menu-indicator {{ image: none; }}

QLineEdit {{
    background: {t.field}; color: {t.text}; border: 1px solid {t.border};
    border-radius: 8px; padding: 4px 10px; selection-background-color: {t.accent};
    selection-color: {t.accent_text};
}}
QLineEdit:focus {{ border: 1px solid {t.accent}; }}

QTabWidget::pane {{ border: none; background: {t.bg}; }}
QTabBar {{ background: {t.surface}; }}
QTabBar::tab {{
    background: {t.surface}; color: {t.dim}; border: none;
    border-right: 1px solid {t.border}; padding: 6px 14px; margin: 0;
    min-width: 90px; max-width: 220px;
}}
QTabBar::tab:hover {{ background: {t.hover}; color: {t.text}; }}
QTabBar::tab:selected {{
    background: {t.bg}; color: {t.text};
    border-bottom: 2px solid {t.accent};
}}
QTabBar::close-button {{
    subcontrol-position: right; border-radius: 3px;
}}
QTabBar::close-button:hover {{ background: {t.border}; }}

QMenuBar {{ background: {t.surface}; color: {t.text}; }}
QMenuBar::item:selected {{ background: {t.hover}; }}
QMenu {{
    background: {t.surface}; color: {t.text}; border: 1px solid {t.border};
    padding: 4px;
}}
QMenu::item {{ padding: 5px 22px; border-radius: 5px; }}
/* 选中标记由菜单项文字里的 ●/○ 表示，关掉 Qt 自带的对勾，免得出现"● ●" */
QMenu::indicator {{ width: 0; height: 0; }}
QMenu::item:selected {{ background: {t.accent}; color: {t.accent_text}; }}
QMenu::separator {{ height: 1px; background: {t.border}; margin: 4px 8px; }}

QStatusBar {{ background: {t.surface}; color: {t.dim}; border-top: 1px solid {t.border}; }}
QStatusBar QLabel {{ color: {t.dim}; }}
QLabel {{ color: {t.text}; background: transparent; }}

QToolTip {{
    background: {t.surface}; color: {t.text};
    border: 1px solid {t.border}; padding: 3px 6px;
}}
QScrollBar:vertical {{ background: {t.surface}; width: 11px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {t.border}; border-radius: 5px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {t.accent}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar:horizontal {{ background: {t.surface}; height: 11px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {t.border}; border-radius: 5px; min-width: 24px; }}
QScrollBar::handle:horizontal:hover {{ background: {t.accent}; }}
"""
