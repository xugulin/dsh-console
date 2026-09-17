"""本机信息页：系统 / CPU / 内存 / 磁盘 / 网络 / DSH 相关。"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import sysinfo
from ..themes import Theme
from ..workers import run_async
from .components import Card, FixedTable, Metric, ScrollPage, kv_row


class SysInfoPage(ScrollPage):
    """电脑信息详情。"""

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme = theme
        self._data: sysinfo.SysInfo | None = None
        self._build()

    # ------------------------------------------------------------------ UI
    def _build(self) -> None:
        root = self.body

        self.btn_refresh = QPushButton("刷新")
        self.btn_refresh.setObjectName("Primary")

        # ---- 系统
        syscard = Card("系统")
        # 刷新放在"系统"卡片右上角：它刷新的是整页，但视觉上跟第一张卡走最自然
        syscard.header.addWidget(self.btn_refresh)
        self.sys_grid = QVBoxLayout()
        self.sys_grid.setSpacing(2)
        syscard.body.addLayout(self.sys_grid)
        root.addWidget(syscard)

        # ---- CPU / 内存

        cpucard = Card("CPU")
        crow = QHBoxLayout()
        crow.setSpacing(22)
        self.m_cores = Metric("核心")
        self.m_load = Metric("负载 1/5/15")
        self.m_usage = Metric("占用率", accent=True)
        self.m_mhz = Metric("主频")
        for m in (self.m_cores, self.m_load, self.m_usage, self.m_mhz):
            crow.addWidget(m)
        crow.addStretch(1)
        cpucard.body.addLayout(crow)
        self.cpu_model = QLabel("")
        self.cpu_model.setObjectName("CardHint")
        self.cpu_model.setWordWrap(True)
        cpucard.body.addWidget(self.cpu_model)
        root.addWidget(cpucard)

        memcard = Card("内存")
        mrow = QHBoxLayout()
        mrow.setSpacing(22)
        self.m_mem_total = Metric("总量")
        self.m_mem_used = Metric("已用", accent=True)
        self.m_mem_avail = Metric("可用")
        self.m_swap = Metric("Swap")
        for m in (self.m_mem_total, self.m_mem_used, self.m_mem_avail, self.m_swap):
            mrow.addWidget(m)
        mrow.addStretch(1)
        memcard.body.addLayout(mrow)
        root.addWidget(memcard)

        # ---- 磁盘
        disk_card = Card("磁盘", "排除 tmpfs / proc 等虚拟文件系统")
        self.disk_table = FixedTable(
            ["挂载点", "文件系统", "总量", "已用", "可用"], visible_rows=4
        )
        self.disk_table.set_column_stretch(0)
        disk_card.body.addWidget(self.disk_table)
        root.addWidget(disk_card)

        # ---- 网络
        net_card = Card("网络接口", "按累计流量排序")
        self.net_table = FixedTable(
            ["接口", "状态", "IPv4", "MAC", "累计接收", "累计发送"], visible_rows=4
        )
        self.net_table.set_column_stretch(2)
        net_card.body.addWidget(self.net_table)
        root.addWidget(net_card)

        # ---- DSH
        dsh_card = Card("DSH 相关")
        self.dsh_grid = QVBoxLayout()
        self.dsh_grid.setSpacing(2)
        dsh_card.body.addLayout(self.dsh_grid)
        root.addWidget(dsh_card)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet(
            f"color: {self.theme.text_dim}; background: transparent; font-size: 12.5px;"
        )
        root.addWidget(self.status)
        root.addStretch(1)

        self.btn_refresh.clicked.connect(self.refresh)

    def activate(self) -> None:
        self.refresh()

    # -------------------------------------------------------------- 数据
    def refresh(self) -> None:
        self.status.setText("正在读取系统信息…")
        run_async(sysinfo.collect, self._on_data, self._on_error)

    def _clear(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _on_data(self, info: sysinfo.SysInfo) -> None:
        self._data = info
        self._clear(self.sys_grid)
        import datetime

        rows = [
            ("主机名", info.hostname),
            ("操作系统", info.os_name),
            ("内核", f"{info.kernel}　·　{info.arch}"),
            ("桌面环境", info.desktop or "—"),
            ("Python", info.python),
            ("运行时长", info.uptime_text),
            (
                "开机时间",
                datetime.datetime.fromtimestamp(info.boot_time).strftime("%Y-%m-%d %H:%M")
                if info.boot_time
                else "—",
            ),
        ]
        for k, v in rows:
            self.sys_grid.addWidget(kv_row(k, v, self.theme))

        c = info.cpu
        self.m_cores.set_value(c.cores_text)
        self.m_load.set_value(c.load_text)
        self.m_usage.set_value(f"{c.usage_percent:.0f}%")
        self.m_mhz.set_value(f"{c.mhz:.0f} MHz" if c.mhz else "—")
        self.cpu_model.setText(c.model or "（读不到型号）")

        m = info.mem
        self.m_mem_total.set_value(m.total_text)
        self.m_mem_used.set_value(m.used_text)
        self.m_mem_avail.set_value(m.available_text)
        self.m_swap.set_value(m.swap_text)

        self.disk_table.fill(
            [[d.mount, d.fstype, d.total_text, d.used_text, d.free_text] for d in info.disks],
            align_right={2, 3, 4},
        )
        self.net_table.fill(
            [
                [n.name, n.state, n.ipv4 or "—", n.mac or "—", n.rx_text, n.tx_text]
                for n in info.net
            ],
            align_right={4, 5},
        )

        self._clear(self.dsh_grid)
        from .. import service

        dsh_rows = [
            ("DSH 主目录", f"{info.dsh_home}（{info.dsh_home_size}）"),
            ("控制台缓存", info.cache_size or "—"),
            ("harness 单元", "dsh-web.service"),
            ("harness 内存", sysinfo.dsh_service_memory()),
            ("本机区域", self._region_text()),
        ]
        for k, v in dsh_rows:
            self.dsh_grid.addWidget(kv_row(k, v, self.theme))

        self.status.setText(
            f"磁盘 {len(info.disks)} 个 · 网卡 {len(info.net)} 个 · "
            f"CPU 占用按 1 分钟负载折算"
        )

    @staticmethod
    def _region_text() -> str:
        from .. import sources

        cfg = sources.load()
        reg, _ = cfg.effective_registry()
        proxy, _ = cfg.effective_proxy()
        return f"{cfg.preset.name}　（npm: {reg}　GitHub 加速: {proxy or '不使用'}）"

    def _on_error(self, msg: str) -> None:
        self.status.setText(f"读取失败：{msg}")

    def apply_theme(self, theme: Theme) -> None:
        self.theme = theme
        self.status.setStyleSheet(
            f"color: {theme.text_dim}; background: transparent; font-size: 12.5px;"
        )
        if self._data is not None:
            self._on_data(self._data)
