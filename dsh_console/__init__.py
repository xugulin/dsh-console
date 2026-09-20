"""DSH 控制台 —— 一个用于管理 DSH Harness 的 PySide6 桌面控制台。

模块划分::

    service.py   与 systemd user 服务 dsh-web 交互（启停重启、状态、日志）
    pricing.py   DeepSeek 模型与峰谷价格（纯函数，逻辑对齐官方口径）
    billing.py   账户余额（官方 API）与会话成本聚合（本地会话文件）
    themes.py    多套亮/暗主题
    workers.py   把阻塞调用丢到线程池，保持界面不卡
    ui/          界面层
"""

__version__ = "1.1.6"
__all__ = ["__version__"]
