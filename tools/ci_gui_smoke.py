#!/usr/bin/env python3
"""离屏 GUI 冒烟测试（跨平台，CI 用）。

为什么需要它：这个项目是 **PySide6 桌面程序**，而 CI 通常只跑"能不能 import"——
那抓不到真正的平台差异（布局、字体、渲染后端、Qt 版本行为）。这里用
``QT_QPA_PLATFORM=offscreen`` 真起一遍 Qt 与几个关键界面，并把渲染结果存成 PNG
（CI 上作为 artifact 上传，出问题时能直接看图）。

覆盖：
  * 主模块与各页面模块能否导入；
  * 7 套主题能否渲染；
  * 市场源设置对话框：能否构造、是否用了滚动区、压到最小时控件**不会被压扁**
    （这条正是修过的 bug，留着当回归）；
  * 生成的 PNG 是否真的有内容（不是全黑/空白）。

本地跑： python3 tools/ci_gui_smoke.py [输出目录]
"""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# 从 tools/ 里跑时 sys.path[0] 是 tools/ —— 得把项目根加进来才 import 得到 dsh_console
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: object = "") -> None:
    RESULTS.append((name, bool(ok), str(detail)))
    print(("PASS  " if ok else "FAIL  ") + name + (("  — " + str(detail)) if detail else ""))


def theme_of(key: str):
    """稳妥地取主题对象（不同版本 API 名字不一样，别写死一种）。"""
    from dsh_console import themes

    for getter in (lambda: themes.get(key), lambda: themes.get_theme(key),
                   lambda: themes.THEMES[key], lambda: list(themes.THEMES.values())[0]):
        try:
            value = getter()
            if value is not None:
                return value
        except Exception:                                # noqa: BLE001
            continue
    raise RuntimeError("取不到主题对象")


def theme_keys() -> list[str]:
    from dsh_console import themes

    for attr in ("THEMES", "ALL", "KEYS"):
        value = getattr(themes, attr, None)
        if isinstance(value, dict):
            return list(value.keys())
        if isinstance(value, (list, tuple)) and value:
            return [getattr(t, "key", str(t)) for t in value]
    return [getattr(themes, "DEFAULT_THEME", "midnight")]


def png_has_content(path: Path) -> tuple[bool, str]:
    """PNG 是不是真有内容（最大通道值不是全 0）。"""
    try:
        from PySide6.QtGui import QImage

        img = QImage(str(path))
        if img.isNull():
            return False, "读不出图"
        step = max(1, img.width() // 40)
        top = 0
        for y in range(0, img.height(), step):
            for x in range(0, img.width(), step):
                c = img.pixelColor(x, y)
                top = max(top, c.red(), c.green(), c.blue())
        return top >= 25, f"最亮 {top}"
    except Exception as exc:                             # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/shots")
    out.mkdir(parents=True, exist_ok=True)

    from PySide6.QtWidgets import QApplication, QScrollArea

    app = QApplication(sys.argv[:1])
    print(f"  Qt 平台: {app.platformName()} · 输出目录: {out}\n")

    # ---- 导入检查（把常见的"某个模块炸了"挡在前面）
    mods = ["dsh_console", "dsh_console.themes", "dsh_console.i18n", "dsh_console.service",
            "dsh_console.subproc", "dsh_console.display", "dsh_console.catalog",
            "dsh_console.ui.components", "dsh_console.ui.main_window",
            "dsh_console.ui.dialogs_sources", "dsh_console.ui.page_market"]
    bad = []
    for mod in mods:
        try:
            __import__(mod)
        except Exception as exc:                         # noqa: BLE001
            bad.append(f"{mod}: {type(exc).__name__}: {exc}")
    check("关键模块全部可导入", not bad, "; ".join(bad)[:200])

    from dsh_console import __version__

    check("版本号可读", bool(__version__), __version__)

    # ---- 主题渲染
    keys = theme_keys()
    failed = []
    for key in keys:
        try:
            theme = theme_of(key)
            from dsh_console.ui.components import Card, Pill

            card = Card("主题渲染冒烟", "CI 离屏渲染")
            pill = Pill("测试")
            pill.set_state("测试", theme.text_faint, theme.text)
            card.body.addWidget(pill)
            card.resize(420, 160)
            card.show()
            for _ in range(5):
                app.processEvents()
            path = out / f"theme-{key}.png"
            card.grab().save(str(path))
            card.hide()
            ok, detail = png_has_content(path)
            if not ok:
                failed.append(f"{key}({detail})")
        except Exception as exc:                         # noqa: BLE001
            failed.append(f"{key}({type(exc).__name__}: {exc})")
            traceback.print_exc()
    check(f"{len(keys)} 套主题全部渲染并有内容", not failed, "; ".join(failed)[:200])

    # ---- 市场源设置对话框：回归"控件被压扁"那个 bug
    try:
        from dsh_console.ui.dialogs_sources import PluginSourceDialog

        dialog = PluginSourceDialog(theme_of(keys[0]))
        dialog.resize(560, 420)                          # 故意压到最小
        dialog.show()
        for _ in range(10):
            app.processEvents()
        area = getattr(dialog, "_source_scroll", None)
        check("对话框使用滚动区（压小时不压扁控件）", isinstance(area, QScrollArea),
              type(area).__name__)
        if isinstance(area, QScrollArea):
            check("窗口压小时出现滚动条",
                  area.verticalScrollBar().isVisible() or
                  area.widget().minimumSizeHint().height() <= area.viewport().height(),
                  f"内容最小高 {area.widget().minimumSizeHint().height()} "
                  f"视口 {area.viewport().height()}")
        table = getattr(dialog, "probe_table", None)
        if table is not None:
            check("表格未被压扁（保持 230px）", table.height() == 230, f"{table.height()}px")
        path = out / "dialog-sources-small.png"
        dialog.grab().save(str(path))
        ok, detail = png_has_content(path)
        check("对话框能渲染出内容", ok, detail)
        dialog.close()
    except Exception as exc:                             # noqa: BLE001
        check("市场源设置对话框冒烟", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 控制台页：构造 +「强制修复端口占用」按钮（救援按钮，不能在重构里丢）
    try:
        from dsh_console import i18n, themes
        from dsh_console.ui.page_dashboard import DashboardPage

        page_theme = theme_of(keys[0])
        # ⚠️ QSS 是**应用级**的（真实程序里由 MainWindow.apply_theme 设到 QApplication 上）。
        # 不设它，孤立构造出来的页面全是没有样式的原生控件——截图会骗人：
        # 按钮看着是灰的，让人误以为样式没生效（实测踩过）。
        app.setStyleSheet(themes.build_qss(page_theme))
        page = DashboardPage(page_theme)
        page.resize(1000, 760)
        page.show()
        for _ in range(10):
            app.processEvents()
        btn = getattr(page, "btn_forcefix", None)
        check("控制台页有「强制修复端口占用」按钮", btn is not None, type(btn).__name__)
        if btn is not None:
            check("按钮文案正确", btn.text() == "强制修复端口占用", btn.text())
            check("按钮用了醒目的 ForceFix 样式", btn.objectName() == "ForceFix", btn.objectName())
            check("按钮默认可用（救援按钮不该被灰掉）", btn.isEnabled())
            check("按钮有说明性 tooltip", len(btn.toolTip()) > 20, btn.toolTip()[:40])
            # 样式**真的**生效了吗：按钮底色应该是主题的 warn 色（"够猛"就靠这个撞色）
            shot = page.grab().toImage()
            center = btn.mapTo(page, btn.rect().center())
            pix = shot.pixelColor(center.x(), center.y())
            want = page_theme.warn.lstrip("#")
            wr, wg, wb = (int(want[i:i + 2], 16) for i in (0, 2, 4))
            got = (pix.red(), pix.green(), pix.blue())
            # 中心可能有文字（文字是深色），所以判"是不是偏暖的实心块"而不是精确比对
            check("按钮底色是主题 warn 色（实心，够醒目）",
                  pix.red() > 120 and pix.red() > pix.blue() + 60,
                  f"实测 rgb{got}，主题 warn rgb=({wr},{wg},{wb})")
        path = out / "page-dashboard.png"
        page.grab().save(str(path))
        ok, detail = png_has_content(path)
        check("控制台页能渲染出内容", ok, detail)
        # 英文界面下这句文案要能翻译过去（i18n 表的键必须与代码一字不差）
        try:
            i18n.set_current(i18n.LANG_EN)
            en = i18n.replace_all("强制修复端口占用")
            check("按钮文案有英文翻译", en != "强制修复端口占用", en)
        except Exception as exc:                         # noqa: BLE001
            check("按钮文案有英文翻译", False, f"{type(exc).__name__}: {exc}")
        finally:
            i18n.set_current(i18n.LANG_ZH)
        page.hide()
        page.deactivate()
    except Exception as exc:                             # noqa: BLE001
        check("控制台页冒烟", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    passed = sum(1 for _n, ok, _d in RESULTS if ok)
    print(f"\n== {passed}/{len(RESULTS)} 项通过 ==")
    for name, ok, detail in RESULTS:
        if not ok:
            print(f"  未通过：{name} {detail}")
    print(f"  截图目录：{out}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
