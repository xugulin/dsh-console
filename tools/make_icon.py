#!/usr/bin/env python3
"""生成应用图标（与主题配色一致），供 .desktop 使用。

    QT_QPA_PLATFORM=offscreen python tools/make_icon.py [主题key]

解释器必须是 3.14 且装了 PySide6（跑 ./run.sh 的那个就行）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# 同 screenshot.py：必须强制离屏，setdefault 会被环境里已有的
# QT_QPA_PLATFORM=wayland;xcb 顶掉，然后在没有显示器时 abort。
os.environ["QT_QPA_PLATFORM"] = os.environ.get("DSH_CONSOLE_QPA") or "offscreen"
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtGui import QIcon  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from dsh_console import themes  # noqa: E402
from dsh_console.ui.main_window import make_icon  # noqa: E402


def main() -> int:
    key = sys.argv[1] if len(sys.argv) > 1 else "midnight"
    app = QApplication(sys.argv[:1])  # noqa: F841 - 需要 QApplication 才能建 QPixmap
    theme = themes.get_theme(key)
    out_dir = ROOT / "assets"
    out_dir.mkdir(exist_ok=True)
    written: list[Path] = []
    # 直接按每个目标尺寸绘制，而不是放大 64px 的图——放大后文字比例会失真、
    # 并且 QIcon.pixmap() 在只有小图时并不保证会放大。
    for size in (256, 128, 64, 48, 32):
        icon: QIcon = make_icon(theme, size)
        out = out_dir / f"icon-{size}.png"
        icon.pixmap(size, size).save(str(out))
        written.append(out)
    # 桌面文件引用的主图标用 256 那版
    main_icon = out_dir / "icon.png"
    main_icon.write_bytes((out_dir / "icon-256.png").read_bytes())
    written.append(main_icon)
    for p in written:
        print(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
