"""当前桌面页面共用的控件辅助函数和文件工作线程。"""

from __future__ import annotations

import threading
from concurrent.futures import CancelledError

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QLabel, QPushButton

from ui_components import button as fluent_button
from branding import brand_icon


def label(text: str = "", role: str = "", wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    widget.setTextFormat(Qt.PlainText)
    widget.setWordWrap(wrap)
    if role:
        widget.setObjectName(role)
    return widget


def button(text: str, role: str = "") -> QPushButton:
    return fluent_button(text, role)


def human_size(size: int) -> str:
    for suffix in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or suffix == "TB":
            return f"{size:,.1f} {suffix}" if suffix != "B" else f"{size:,} B"
        size /= 1024
    return ""


def app_icon() -> QIcon:
    return brand_icon()


class FileJob(QThread):
    progress_changed = Signal(int)

    def __init__(self, operation, parent=None):
        super().__init__(parent)
        self.operation = operation
        self.cancel_event = threading.Event()
        self.result = None
        self.error = ""
        self.cancelled = False

    def run(self):
        last = -1

        def progress(done, total):
            nonlocal last
            percent = min(100, int(done * 100 / max(1, total)))
            if percent != last:
                self.progress_changed.emit(percent)
                last = percent

        try:
            self.result = self.operation(cancel_event=self.cancel_event, progress=progress)
        except CancelledError:
            self.cancelled = True
        except Exception as exc:
            self.error = str(exc) or type(exc).__name__
