#!/usr/bin/env python3
"""离屏渲染各页面为 PNG，用于快速检查界面。

    QT_QPA_PLATFORM=offscreen python tools/screenshot.py [主题key ...]

产物写到 ``shots/`` 目录。不依赖显示器，适合在没有图形环境时自查布局。
解释器必须是 3.14 且装了 PySide6（跑 ./run.sh 的那个就行）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# 强制离屏，**不能**用 os.environ.setdefault：环境里已经有 QT_QPA_PLATFORM=wayland;xcb
# （桌面会话会把它带进来）时 setdefault 不会覆盖，Qt 转而去连 Wayland/X11，
# 在没有显示器的环境里直接 abort——实测退出码 134 + core dump。
# 这个工具的用途就是"没有显示器也能渲染"，所以默认必须是离屏；
# 真要换平台请显式设 DSH_CONSOLE_QPA。
os.environ["QT_QPA_PLATFORM"] = os.environ.get("DSH_CONSOLE_QPA") or "offscreen"

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from dsh_console import themes  # noqa: E402
from dsh_console.ui.main_window import MainWindow  # noqa: E402

OUT = ROOT / "shots"
PAGES = ["dashboard", "models", "billing", "plugins", "market", "skills",
         "sysinfo", "logs", "settings", "donate", "harness"]

#: 每页额外的等待时间。插件市场要等 npm 搜索回来（本机网络慢，给足）。
#: harness 页要等 `npm view` 取 dist-tags，也得给足。
EXTRA_WAIT = {"market": 20000, "skills": 20000, "plugins": 2500,
              "billing": 2500, "sysinfo": 2500, "dashboard": 3000,
              "harness": 8000}


def settle(app: QApplication, ms: int) -> None:
    """空转事件循环，让后台任务把数据填进界面。"""
    loops = max(1, ms // 50)
    for _ in range(loops):
        app.processEvents()
        QTimer.singleShot(0, lambda: None)
        import time

        time.sleep(0.05)


def main() -> int:
    theme_keys = sys.argv[1:] or ["midnight", "daylight"]
    OUT.mkdir(exist_ok=True)
    app = QApplication(sys.argv[:1])
    win = MainWindow()
    win.resize(1180, 800)
    win.show()
    settle(app, 9000)  # 等首屏数据（服务状态 / 余额 / 会话扫描）回来

    written = []
    for key in theme_keys:
        # persist=False：这是自查工具，不该把主题写进用户的 ~/.config/dsh-console/config.json。
        # 之前走默认的 persist=True，跑一次截图就把控制台的启动主题改成了最后渲染的那套。
        win.apply_theme(key, persist=False)
        settle(app, 1200)
        for idx, name in enumerate(PAGES):
            win.stack.setCurrentIndex(idx)
            btn = win.nav_group.buttons()[idx]
            btn.setChecked(True)
            page = win.pages[idx]
            # 懒加载之后 activate() 才是正确入口（refresh/reload 只是退路）
            for method in ("activate", "refresh", "reload"):
                fn = getattr(page, method, None)
                if callable(fn):
                    fn()
                    break
            settle(app, 1500 + EXTRA_WAIT.get(name, 0))
            path = OUT / f"{key}-{name}.png"
            win.grab().save(str(path))
            written.append(path)
    for p in written:
        print(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
