"""可复用的小部件。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QAbstractItemView,
    QScrollArea,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QSizePolicy,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from .. import pricing
from ..themes import Theme

if TYPE_CHECKING:                 # 只为类型注解；运行时不需要
    from .. import billing


class Card(QFrame):
    """带标题的圆角卡片。内容加到 :attr:`body` 布局里。"""

    def __init__(
        self,
        title: str = "",
        hint: str = "",
        parent: QWidget | None = None,
        *,
        spacing: int = 10,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 13, 16, 13)
        outer.setSpacing(spacing)

        self.header = QHBoxLayout()
        self.header.setSpacing(10)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("CardTitle")
        self.header.addWidget(self.title_label)
        self.header.addStretch(1)
        outer.addLayout(self.header)

        if hint:
            self.hint_label = QLabel(hint)
            self.hint_label.setObjectName("CardHint")
            self.hint_label.setWordWrap(True)
            outer.addWidget(self.hint_label)

        self.body = QVBoxLayout()
        self.body.setSpacing(spacing)
        outer.addLayout(self.body)

    def set_title(self, text: str) -> None:
        self.title_label.setText(text)

    def add_header_widget(self, w: QWidget) -> None:
        self.header.addWidget(w)


class Metric(QWidget):
    """一个大号数值 + 说明文字。"""

    def __init__(
        self,
        label: str,
        value: str = "—",
        parent: QWidget | None = None,
        *,
        accent: bool = False,
    ) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)
        self.value_label = QLabel(value)
        self.value_label.setObjectName("MetricValueAccent" if accent else "MetricValue")
        self.value_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.label_label = QLabel(label)
        self.label_label.setObjectName("MetricLabel")
        lay.addWidget(self.value_label)
        self._last_value = self.value_label.text()
        self._last_label = ""
        lay.addWidget(self.label_label)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

    def set_value(self, text: str) -> None:
        # 文字没变就不碰控件：定时刷新每 3 秒把二十几个 Metric 重刷一遍，
        # 每次 setText 都会让卡片重新算布局。内容一样的占多数，跳过它们。
        if text != self._last_value:
            self._last_value = text
            self.value_label.setText(text)

    def set_label(self, text: str) -> None:
        """改说明文字。同一格在不同状态下含义不同时用得上（如运行时长 / 上次运行）。"""
        if text != self._last_label:
            self._last_label = text
            self.label_label.setText(text)


class Pill(QLabel):
    """状态徽章。颜色由 :meth:`set_state` 依据主题色决定。"""

    def __init__(self, text: str = "—", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("Pill")
        self.setAlignment(Qt.AlignCenter)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def set_state(self, text: str, color: str, fg: str) -> None:
        self.setText(text)
        self.setStyleSheet(
            f"#Pill {{ background: {color}22; color: {color};"
            f" border: 1px solid {color}66; border-radius: 11px;"
            f" padding: 4px 13px; font-weight: 600; }}"
            + (f" QLabel {{ color: {color}; }}" if fg else "")
        )


class Divider(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("HLine")
        self.setFrameShape(QFrame.HLine)
        self.setFixedHeight(1)


def kv_row(key: str, value: str, theme: Theme) -> QWidget:
    """一行「标签 —— 值」。"""
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 2, 0, 2)
    k = QLabel(key)
    k.setStyleSheet(f"color: {theme.text_faint}; background: transparent;")
    v = QLabel(value)
    v.setStyleSheet(f"color: {theme.text}; background: transparent; font-weight: 600;")
    v.setTextInteractionFlags(Qt.TextSelectableByMouse)
    v.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    lay.addWidget(k)
    lay.addStretch(1)
    lay.addWidget(v)
    return w


def hline(theme: Theme) -> QFrame:
    f = QFrame()
    f.setFixedHeight(1)
    f.setStyleSheet(f"background: {theme.border}; border: none;")
    return f


class _TableModel(QAbstractTableModel):
    """纯数据模型：只保存 Python 列表，不创建任何 QTableWidgetItem。

    这是为了绕开一个实测到的性能悬崖：``QTableWidget`` 每行每列都要一个
    item 对象，而且表头用 ``ResizeToContents`` 时**每次 setItem 都会把所有行
    重新量一遍**，复杂度 O(n²)。市场页 250 行 × 7 列实测要 **11.2 秒**，
    直接把 GUI 冻住（早先在"未显示的表格"上量只有 3 ms，完全测不出来）。

    换成 model/view 后，填 250 行只是给列表赋值（微秒级），渲染由 Qt 按需
    只处理可见的那十几行。
    """

    def __init__(self, headers: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._headers = list(headers)
        self._rows: list[list[str]] = []
        self._right: set[int] = set()
        self._center: set[int] = set()
        self._check_col: int | None = None
        self._checks: list[bool] = []

    # --- Qt 接口
    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._headers)

    def data(self, index, role=Qt.DisplayRole):  # noqa: N802
        if not index.isValid():
            return None
        r, c = index.row(), index.column()
        if r >= len(self._rows):
            return None
        if role == Qt.DisplayRole:
            row = self._rows[r]
            return row[c] if c < len(row) else ""
        if role == Qt.CheckStateRole and self._check_col == c:
            return Qt.Checked if (r < len(self._checks) and self._checks[r]) else Qt.Unchecked
        if role == Qt.TextAlignmentRole:
            if c in self._right:
                return int(Qt.AlignRight | Qt.AlignVCenter)
            if c in self._center:
                return int(Qt.AlignCenter)
            return int(Qt.AlignLeft | Qt.AlignVCenter)
        if role == Qt.ToolTipRole:
            row = self._rows[r]
            return row[c] if c < len(row) else None
        return None

    def setData(self, index, value, role=Qt.EditRole) -> bool:  # noqa: N802
        if role == Qt.CheckStateRole and self._check_col == index.column():
            r = index.row()
            if r < len(self._checks):
                self._checks[r] = value == Qt.Checked
                self.dataChanged.emit(index, index, [role])
                return True
        return False

    def flags(self, index):  # noqa: N802
        f = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if self._check_col == index.column():
            f |= Qt.ItemIsUserCheckable
        return f

    def headerData(self, section, orientation, role=Qt.DisplayRole):  # noqa: N802
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self._headers[section] if section < len(self._headers) else ""
        return None

    # --- 便捷接口
    def set_rows(self, rows: list[list[str]]) -> None:
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def set_headers(self, headers: list[str]) -> None:
        self.beginResetModel()
        self._headers = list(headers)
        self.endResetModel()

    def set_alignment(self, right: set[int], center: set[int]) -> None:
        self._right, self._center = set(right), set(center)

    def enable_checks(self, column: int, values: list[bool]) -> None:
        self._check_col = column
        self._checks = list(values)

    def set_check(self, row: int, checked: bool) -> None:
        if 0 <= row < len(self._checks):
            self._checks[row] = checked
            idx = self.index(row, self._check_col or 0)
            self.dataChanged.emit(idx, idx, [Qt.CheckStateRole])

    def check(self, row: int) -> bool:
        return bool(0 <= row < len(self._checks) and self._checks[row])

    def text(self, row: int, col: int) -> str:
        if 0 <= row < len(self._rows):
            r = self._rows[row]
            if 0 <= col < len(r):
                return r[col]
        return ""


class FixedTable(QTableView):
    """**行数固定**的表格：高度只由可见行数决定，行数超出时内部滚动。

    与"高度贴合内容"的做法相反——插件列表、市场列表这类表格行数会随搜索/安装
    变化，如果让表格随内容长高，页面就会被撑得忽长忽短、按钮也会被顶出屏幕。
    这里锁死可见行数，多余的行用滚动条看。

    底层是 :class:`_TableModel` + ``QTableView``（渲染虚拟化），
    所以填几千行也只是给 Python 列表赋值，不会卡住 GUI。
    """

    #: 兼容 QTableWidget 的旧信号名，页面里的 connect 不用改
    itemSelectionChanged = Signal()

    def __init__(
        self,
        headers: list[str],
        parent: QWidget | None = None,
        *,
        visible_rows: int = 8,
        row_height: int = 34,
    ) -> None:
        super().__init__(parent)
        self._visible_rows = visible_rows
        self._row_height = row_height
        # 按像素滚动而不是按行跳：滚轮手感顺得多（默认 ScrollPerItem 是一格一格蹦）
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self._model = _TableModel(headers, self)
        self.setModel(self._model)

        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(row_height)
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        # 关键：绝不用 ResizeToContents —— 它会让每次填表都重新量所有行（O(n²)）
        self.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.horizontalHeader().setStretchLastSection(True)
        self._stretch_col: int | None = None
        self._last_rows: list[list[str]] = []
        # 把 selectionModel 的变化转成旧信号名
        self.selectionModel().selectionChanged.connect(
            lambda *_: self.itemSelectionChanged.emit()
        )
        self._apply_height()

    @property
    def row_height(self) -> int:
        return self._row_height

    def _apply_height(self) -> None:
        header_h = self.horizontalHeader().sizeHint().height()
        self.setFixedHeight(header_h + self._row_height * self._visible_rows + 8)

    def set_visible_rows(self, rows: int) -> None:
        self._visible_rows = max(1, rows)
        self._apply_height()

    #: 兼容旧用法：``table.model()`` 返回的是 _TableModel
    def fill(
        self,
        rows: list[list[str]],
        align_right: set[int] | None = None,
        align_center: set[int] | None = None,
    ) -> None:
        """一次性填表。填完只做**一次**列宽自适应。"""
        self._model.set_alignment(align_right or set(), align_center or set())
        self._model.set_rows(rows)
        self._last_rows = rows
        self._autosize(rows)
        # 再排一次延迟测量：切页/首次显示时布局与字体 polish 的先后顺序不确定，
        # 立即量可能拿到过渡态的字号（实测：同一份数据在孤立环境里量 136px 正确，
        # 走完整应用切页流程却偏窄）。事件循环跑完一轮后再量一次最稳。
        QTimer.singleShot(0, self._deferred_autosize)

    def showEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """首次显示时重新量一次列宽。

        原因：页面对象在 ``MainWindow.__init__`` 里就构造好了，而应用级样式表是
        之后才 ``setStyleSheet`` 的，因此在 ``fill()`` 时取到的 ``fontMetrics()``
        还是**没上样式前的字体**，算出来的列宽偏窄，渲染出来就变成 ``1,4…``
        （孤立测试里先设样式表再建表，所以量出来是对的，一直复现不了）。
        控件真正显示时字体已完成 polish，这里再量一次即修正。
        """
        super().showEvent(event)
        if self._last_rows:
            self._autosize(self._last_rows)

    def _autosize(self, rows: list[list[str]]) -> None:
        """按**采样**算列宽。

        不用 ``resizeColumnsToContents()``：它会逐行逐列量文本，250 行 × 7 列实测
        要 260 ms，是现在最大的一处停顿。列宽本来也不需要精确到每一行——
        取前 60 行做样本，宽度就足够有代表性，耗时降到几毫秒。
        """
        fm = self.fontMetrics()
        head_fm = self.horizontalHeader().fontMetrics()   # 表头是粗体，比正文宽
        ncols = self._model.columnCount()
        sample = rows[:60]
        # 单元格左右各有 8px padding（QSS 里写的），再留 6px 余量；
        # 早先只加 22px，结果像「大小」这种短列被省略成 "17…"。
        pad = 8 * 2 + 14
        for c in range(ncols):
            if c == self._stretch_col:
                continue
            width = head_fm.horizontalAdvance(self._model._headers[c]) + pad
            for row in sample:
                if c < len(row):
                    width = max(width, fm.horizontalAdvance(str(row[c])) + pad)
            self.setColumnWidth(c, max(40, min(width, 460)))

    def set_headers(self, headers: list[str]) -> None:
        self._model.set_headers(headers)

    def _deferred_autosize(self) -> None:
        if self._last_rows:
            self._autosize(self._last_rows)

    def set_column_stretch(self, column: int) -> None:
        """让某一列吃掉剩余宽度（替代原来的 Stretch 模式）。"""
        self._stretch_col = column
        hh = self.horizontalHeader()
        hh.setStretchLastSection(False)
        hh.setSectionResizeMode(column, QHeaderView.Stretch)

    def selected_row(self) -> int:
        idx = self.selectionModel().currentIndex()
        return idx.row() if idx.isValid() else -1

    # --- 复选框（批量更新对话框用）
    def enable_checks(self, column: int, values: list[bool]) -> None:
        self._model.enable_checks(column, values)

    def set_check(self, row: int, checked: bool) -> None:
        self._model.set_check(row, checked)

    def check(self, row: int) -> bool:
        return self._model.check(row)

    def cell_text(self, row: int, col: int) -> str:
        return self._model.text(row, col)


class ScrollPage(QScrollArea):
    """内容比窗口高时**滚动**而不是挤压的页面容器。

    Qt 的布局在没有滚动区时会把放不下的控件一路压扁——实测「数据源」页
    的输入框被压成一条细缝、表格与按钮互相重叠。凡是卡片较多的设置型页面
    都应该套这一层。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("PageScroll")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        # 横向**按需**出现滚动条，不要 AlwaysOff。
        #
        # 关掉横向滚动条时，窗口一旦比内容的 minimumSizeHint 窄，Qt 只能把内容压扁或
        # 裁掉——小屏上就是"右下角被切了、按钮点不到"。宁可让用户横向滚一下，
        # 也别让控件互相重叠（实测内容最小宽 886，而 1366×768 屏上窗口只有 1180 宽、
        # 更小的屏更窄）。
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        # 按像素滚动：滚轮一格挪一点，而不是整块跳
        self.verticalScrollBar().setSingleStep(24)
        self.horizontalScrollBar().setSingleStep(24)
        inner = QWidget()
        # ⚠️ **必须显式打开 WA_StyledBackground**，否则 QSS 里的 `QWidget { background }`
        # 对它不生效——纯 QWidget 默认不画样式表背景（Qt 的既定行为），于是内层容器
        # 露出底下的默认底色。表现就是：卡片只占上半屏，下面一大片**纯黑**，中间一道
        # 横向断裂（用户截图里那道）。窗口越拉伸越明显。
        inner.setObjectName("PageBody")
        inner.setAttribute(Qt.WA_StyledBackground, True)
        self.body = QVBoxLayout(inner)
        # 页面留白收紧（26/22 -> 18/14）：同样的窗口能多放小半张卡片
        self.body.setContentsMargins(18, 14, 18, 14)
        self.body.setSpacing(11)
        self.setWidget(inner)


class HourBars(QWidget):
    """24 小时的消费柱状图（自绘，无第三方图表依赖）。"""

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme = theme
        self._hours: list[billing.HourBucket] = []
        self.setMinimumHeight(150)

    def set_hours(self, hours: list[billing.HourBucket]) -> None:
        self._hours = hours
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        pad_l, pad_b, pad_t = 34, 22, 10
        plot_h = max(10, h - pad_b - pad_t)
        plot_w = max(10, w - pad_l - 6)

        peak = max((x.cost_usd for x in self._hours), default=0.0)
        f = QFont()
        f.setPixelSize(10)
        p.setFont(f)

        # 基线
        p.setPen(QColor(self.theme.border))
        p.drawLine(pad_l, pad_t + plot_h, w - 6, pad_t + plot_h)

        if peak <= 0:
            p.setPen(QColor(self.theme.text_faint))
            p.drawText(self.rect(), Qt.AlignCenter, "今天还没有调用记录")
            p.end()
            return

        n = 24
        gap = 3.0
        bar_w = max(2.0, (plot_w - gap * (n - 1)) / n)
        for i, hb in enumerate(self._hours):
            x = pad_l + i * (bar_w + gap)
            frac = hb.cost_usd / peak if peak else 0.0
            bh = max(1.0, frac * plot_h) if hb.calls else 1.0
            y = pad_t + plot_h - bh
            color = self.theme.peak if hb.peak else self.theme.accent
            if not hb.calls:
                color = self.theme.border
            path = QPainterPath()
            path.addRoundedRect(x, y, bar_w, bh, min(3.0, bar_w / 2), min(3.0, bar_w / 2))
            p.fillPath(path, QColor(color))
            if i % 3 == 0:
                p.setPen(QColor(self.theme.text_faint))
                p.drawText(
                    int(x - 6), pad_t + plot_h + 4, int(bar_w + 14), 16,
                    Qt.AlignHCenter | Qt.AlignTop, f"{i}",
                )
        # 峰值标注
        p.setPen(QColor(self.theme.text_faint))
        p.drawText(0, pad_t, pad_l - 6, 14, Qt.AlignRight | Qt.AlignTop,
                   f"¥{pricing.cny(peak):.2f}")
        p.end()


# 屏幕适配搬到了 dsh_console.screenfit（GUI 外壳也要用，不该被拖进 ui 包）。
# 这里重新导出，老的 `from .components import apply_screen_fit` 仍然有效。
from ..screenfit import apply_screen_fit, fit_to_screen  # noqa: E402,F401
