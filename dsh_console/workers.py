"""把阻塞调用丢到线程池，保持界面不卡。

界面里所有耗时操作（systemctl、HTTP 请求、扫描会话文件）都走这里：
worker 线程里执行函数，结果通过信号回到主线程。

⚠️ 这里有一个必须保留的**强引用**。踩过的坑：``QThreadPool.start(task)``
并不阻止 Python 侧回收那个 ``Task`` 对象；一旦调用方丢弃返回值，``Task``（连同
它持有的 ``_Signals``）就被 GC 掉，信号连接随之失效——现象是**任务照跑，
回调永远不触发**，而且因为 GC 时机不定，表现为「有时好有时坏」。
所以下面用 ``_PENDING`` 持有引用直到任务结束，并关掉 ``setAutoDelete``
以免 C++ 侧提前销毁对象。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

#: 正在运行的任务。**不要删** —— 见模块开头说明。
_PENDING: set["Task"] = set()


class _Signals(QObject):
    finished = Signal(object)
    failed = Signal(str)


class Task(QRunnable):
    """在后台线程里跑一个可调用对象。"""

    def __init__(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self.signals = _Signals()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        # 生命周期由 _PENDING 管理：任务结束后我们从集合里移除，Python GC 随即回收。
        self.setAutoDelete(False)

    @Slot()
    def run(self) -> None:  # pragma: no cover - 线程内执行
        try:
            result = self._fn(*self._args, **self._kwargs)
        except Exception as exc:  # noqa: BLE001 - 任何异常都要回到界面
            self._emit(self.signals.failed, f"{type(exc).__name__}: {exc}")
        else:
            self._emit(self.signals.finished, result)

    @staticmethod
    def _emit(signal: Any, payload: Any) -> None:
        """发射信号，并容忍「宿主对象已被销毁」。

        关窗退出时 QApplication 先于工作线程析构，此时 ``self.signals`` 的 C++ 对象
        已经没了，直接 emit 会抛 ``RuntimeError: Signal source has been deleted``
        并在终端打出堆栈。任务结果此时已无意义，静默丢弃即可。
        """
        try:
            signal.emit(payload)
        except RuntimeError:
            pass


def run_async(
    fn: Callable[..., Any],
    on_done: Callable[[Any], None] | None = None,
    on_error: Callable[[str], None] | None = None,
    *args: Any,
    **kwargs: Any,
) -> Task:
    """在全局线程池里跑 ``fn``，结果经信号回到主线程。"""
    task = Task(fn, *args, **kwargs)
    _PENDING.add(task)

    def _settle(*_: Any) -> None:
        _PENDING.discard(task)

    if on_done is not None:
        task.signals.finished.connect(on_done)
    if on_error is not None:
        task.signals.failed.connect(on_error)
    # 无论成功失败都要释放引用，否则 _PENDING 会一直增长
    task.signals.finished.connect(_settle)
    task.signals.failed.connect(_settle)

    QThreadPool.globalInstance().start(task)
    return task


def pending_count() -> int:
    """当前在跑的任务数（自检/调试用）。"""
    return len(_PENDING)


def drain(timeout_ms: int = 3000) -> bool:
    """等所有后台任务结束（退出前调用，避免线程被硬杀）。"""
    QThreadPool.globalInstance().waitForDone(timeout_ms)
    return not _PENDING
