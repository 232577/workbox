"""释放每个已结束用例创建的 Qt 窗口，避免共享 QApplication 累积界面。"""
import pytest
import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent, QThread
from PySide6.QtWidgets import QApplication, QWidget


def _window_addresses():
    if QApplication.instance() is None:
        return set()
    return {shiboken6.getCppPointer(widget)[0] for widget in QApplication.topLevelWidgets()}


@pytest.fixture(autouse=True)
def release_test_windows():
    # 高作用域 fixture 先于此处执行，其已有窗口保留给后续用例。
    existing = _window_addresses()
    yield
    if QApplication.instance() is None:
        return
    from delivery_ui import TodayDeliveryDialog
    from project_documents_ui import ProjectDocumentsPane

    windows = [widget for widget in QApplication.topLevelWidgets()
               if shiboken6.getCppPointer(widget)[0] not in existing]
    for window in windows:
        if not shiboken6.isValid(window):
            continue
        # 此时函数级 fixture 已完成 shutdown；额外停止独立面板的文档读取。
        for widget in [window, *window.findChildren(QWidget)]:
            if isinstance(widget, (ProjectDocumentsPane, TodayDeliveryDialog)):
                widget.shutdown()
        running = [worker for worker in window.findChildren(QThread) if worker.isRunning()]
        assert not running, '用例结束后仍有工作线程运行，不能直接销毁其窗口。'
        window.hide()
        window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
