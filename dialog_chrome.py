"""Shared native-dialog presentation; no service calls or implicit persistence."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QSizePolicy, QVBoxLayout
from qfluentwidgets import FluentIcon

from ui_components import DialogHeader, StatusBanner, LineEdit


class FilterEdit(LineEdit):
    """A filter is not a form submission; retain native editing for other keys."""
    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            event.accept()
            return
        super().keyPressEvent(event)


def add_header(layout, title, subtitle='', *, icon=FluentIcon.DOCUMENT, eyebrow='WORKBOX / 工作空间'):
    """Add consistent hierarchy without changing flags or default actions."""
    layout.setContentsMargins(20, 18, 20, 16)
    layout.setSpacing(12)
    header = DialogHeader(title, subtitle, icon=icon, eyebrow=eyebrow)
    layout.addWidget(header)
    return header


def add_actions(layout, row):
    """Keep existing buttons and signal connections in a fixed footer."""
    frame = QFrame()
    frame.setObjectName('dialogActions')
    frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    row.setContentsMargins(0, 12, 0, 0)
    row.setSpacing(8)
    frame.setLayout(row)
    layout.addWidget(frame)
    return frame


class InlineError(StatusBanner):
    """An empty error occupies no space; supplied messages are plain text."""
    def __init__(self, text='', parent=None):
        super().__init__('', parent)
        self.setTone('error')
        self.setText(text)

    def setText(self, text):
        super().setText(str(text))
        self.setAccessibleName(str(text))
        self.setVisible(bool(str(text).strip()))

    def clear(self):
        self.setText('')


class InstructionCard(QFrame):
    def __init__(self, title, text, parent=None):
        super().__init__(parent)
        self.setObjectName('dialogSection')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(6)
        for content, role in ((title, 'sectionTitle'), (text, '')):
            label = QLabel(content)
            label.setTextFormat(Qt.PlainText)
            label.setWordWrap(True)
            label.setObjectName(role)
            layout.addWidget(label)
