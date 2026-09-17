"""界面语言：内置**简体中文 / 英语（美国）**两种，首次启动默认中文。

## 语言影响三件不同的事

1. **Qt 自己的文案**（右键菜单、文件对话框、标准按钮……）——靠 Qt 的 `.qm` 翻译文件，
   用 :func:`install_translators` 装上 ``qtbase_<lang>.qm`` 与 ``qtwebengine_<lang>.qm``。
   **内置浏览器的右键菜单英文就是这一条缺失导致的**：项目此前从没装过翻译器。
2. **Chromium（QtWebEngine）的界面语言**——靠启动参数 ``--lang=zh-CN``，必须在
   **QApplication / web 引擎初始化之前**通过 ``QTWEBENGINE_CHROMIUM_FLAGS`` 设好
   （见 :func:`prepare_environment`）。它同时决定 ``navigator.language``，
   于是 harness 的 web UI 也会跟着变中文。
3. **本项目自己的界面文字**——靠 :func:`tr` 查表（``_EN`` 里是中文 → 英文的对照）。

## 为什么要自己查表而不是用 Qt 的 tr()

项目里的界面文案是直接写在代码里的中文字面量（293 处），没有 ``self.tr(...)`` 包裹。
用它自己的表可以让**已经写好的中文原文直接当 key**，逐页替换时不必一次改完：
没查到的串原样返回中文，不会出现空白或 ``%1`` 之类的占位符。

## 加一门语言 / 补一批翻译

* 新语言：往 ``LANGUAGES`` 加一项、建一张表，`tr` 里按当前语言选表即可。
* 补翻译：往 ``_EN`` 里加「中文原文: English」；键必须与代码里的字面量**一字不差**。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import config

if TYPE_CHECKING:                     # 只为类型注解，运行时不需要 Qt
    from PySide6.QtCore import QTranslator

#: 语言 key（存进 config.json 的 ``language``）
LANG_ZH = "zh_CN"
LANG_EN = "en_US"
#: 内置语言。第一项是默认值——**首次启动默认简体中文**。
LANGUAGES: tuple[tuple[str, str], ...] = (
    (LANG_ZH, "简体中文"),
    (LANG_EN, "English (US)"),
)
DEFAULT_LANG = LANG_ZH

#: Qt 翻译文件名里的语言段（qtbase_zh_CN.qm / qtbase_en.qm）
_QT_SUFFIX = {LANG_ZH: "zh_CN", LANG_EN: "en"}
#: Chromium 的 --lang 取值（BCP 47）
_CHROMIUM = {LANG_ZH: "zh-CN", LANG_EN: "en-US"}

#: 中文原文 → English。**键要和代码里的字符串一字不差。**
_EN: dict[str, str] = {
    # ---- 侧边栏 / 顶栏 / 窗口
    "控制台": "Dashboard",
    "模型与价格": "Models & Pricing",
    "账单": "Billing",
    "插件与技能": "Plugins & Skills",
    "插件市场": "Plugin Market",
    "技能市场": "Skill Market",
    "本机信息": "System Info",
    "日志": "Logs",
    "设置": "Settings",
    "捐赠支持": "Donate",
    "Harness": "Harness",
    "harness 版本、安装位置、运行状态与升级": "Harness version, install location, run status and upgrade",
    "状态：读取中…": "Status: loading…",
    "DSH 控制台": "DSH Console",
    # ---- 设置页
    "语言": "Language",
    "界面语言": "Interface language",
    "同时作用于控制台界面、内置浏览器（含右键菜单）与网页界面；切换后立即生效。":
        "Applies to the console UI, the built-in browser (including its context menu) and the web UI. Takes effect immediately.",
    "外观主题": "Appearance",
    "选择后立即生效。深色主题共 4 套，浅色 3 套。":
        "Applies immediately. 4 dark themes and 3 light themes.",
    "已安装插件": "Installed plugins",
    "完整管理（安装 / 卸载 / 更新 / 启用停用 / 体检）见左侧「插件」页":
        "Full management (install / uninstall / update / enable / disable / checkup) is on the Plugins page",
    "读取中…": "Loading…",
    "关于": "About",
    "（没有已安装的插件）": "(no plugins installed)",
    # ---- 捐赠页
    "❤️ 感谢你的捐赠，这是我不断打磨的动力": "❤️ Thanks for your support — it keeps me improving this",
    "如果这个控制台帮到了你，欢迎请我喝杯咖啡 ☕ 每一份支持我都会记在下面的名单里。":
        "If this console helps you, you're welcome to buy me a coffee ☕ Every bit of support is listed below.",
    "📮 联系我：QQ 894597841 　·　 邮箱 894597841@163.com":
        "📮 Contact: QQ 894597841  ·  Email 894597841@163.com",
    "收款码": "Donation codes",
    "微信 / 支付宝都可以。点一下图片放大，手机更容易扫上。":
        "WeChat or Alipay. Click an image to enlarge it — easier to scan with a phone.",
    "微信收款码": "WeChat Pay",
    "支付宝收款码": "Alipay",
    "点击放大": "Click to enlarge",
    "感谢者名单": "Thank-you list",
    "还没有记录": "No records yet",
    # ---- 内置浏览器：右键菜单（Qt 自带那份永远是英文，所以这些标签是我们自己给的）
    "后退": "Back",
    "前进": "Forward",
    "重新加载": "Reload",
    "在新标签页中打开链接": "Open link in new tab",
    "复制链接地址": "Copy link address",
    "复制图片": "Copy image",
    "复制图片地址": "Copy image address",
    "剪切": "Cut",
    "复制": "Copy",
    "粘贴": "Paste",
    "全选": "Select all",
    "另存为…": "Save page…",
    "打印…": "Print…",
    "查看网页源代码": "View page source",
    "检查元素": "Inspect",
    # ---- 内置浏览器（窗口本身）
    "DSH 内置浏览器": "DSH Built-in Browser",
    "就绪": "Ready",
    "更多（开发者工具 / 缩放 / 退出）": "More (dev tools / zoom / quit)",
    "内置浏览器不可用：QtWebEngine 导入失败": "Built-in browser unavailable: QtWebEngine failed to import",
    "重新加载": "Reload",
}


def current() -> str:
    """当前语言 key。没设过就是默认（简体中文）。"""
    value = (config.get(config.KEY_LANGUAGE) or "").strip()
    return value if value in dict(LANGUAGES) else DEFAULT_LANG


def set_current(key: str) -> None:
    """记住语言选择（下次启动沿用）。"""
    if key in dict(LANGUAGES):
        config.put(config.KEY_LANGUAGE, key)


def is_en() -> bool:
    return current() == LANG_EN


def tr(zh: str) -> str:
    """把界面上的中文原文翻成当前语言；查不到就原样返回。

    查不到时**返回中文**是刻意的：本项目正在逐页补翻译，没补到的地方宁可显示中文，
    也不该出现空白或 key 本身。
    """
    if current() == LANG_ZH:
        return zh
    return _EN.get(zh, zh)


def chromium_lang(lang: str | None = None) -> str:
    return _CHROMIUM.get(lang or current(), "zh-CN")


def prepare_environment() -> None:
    """**必须在 QApplication / QtWebEngine 初始化之前**调用。

    做两件事：
    * 把 ``--lang=zh-CN`` 追加进 ``QTWEBENGINE_CHROMIUM_FLAGS``（已是最后一件事的机会，
      引擎一起来就读不到了）；
    * 定下 ``QLocale``，让 Qt 的数字/日期格式和标准对话框跟着语言走。
    """
    import os

    from PySide6.QtCore import QLocale

    lang = current()
    flag = f"--lang={chromium_lang(lang)}"
    existing = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "").strip()
    if "--lang=" not in existing:
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (existing + " " + flag).strip()
    QLocale.setDefault(QLocale(lang))


def install_translators(app) -> list[QTranslator]:
    """给 QApplication 装上 Qt 自己的翻译（右键菜单、文件对话框等就靠它）。

    返回装着翻译器的列表——**调用方必须持有引用**：``QTranslator`` 一旦被回收，
    翻译立刻失效（这类"装完没反应"的坑很常见）。
    """
    from PySide6.QtCore import QLibraryInfo, QTranslator

    lang = current()
    suffix = _QT_SUFFIX.get(lang, "zh_CN")
    base = QLibraryInfo.path(QLibraryInfo.TranslationsPath)
    keep: list[QTranslator] = []
    # qtbase: 标准对话框与按钮；qtwebengine: **内置浏览器的右键菜单**就在这里面
    for name in ("qtbase", "qtwebengine", "qt"):
        tr_obj = QTranslator(app)
        if tr_obj.load(f"{name}_{suffix}", base):
            app.installTranslator(tr_obj)
            keep.append(tr_obj)
    return keep
