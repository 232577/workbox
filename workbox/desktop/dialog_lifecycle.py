"""临时模态窗口的结果读取与延迟释放。"""
from contextlib import contextmanager

import shiboken6


@contextmanager
def transient_dialog(dialog):
    """调用方在作用域内执行并消费结果，离开后交由 Qt 安全销毁。"""
    try:
        yield dialog
    finally:
        if shiboken6.isValid(dialog):
            dialog.deleteLater()
