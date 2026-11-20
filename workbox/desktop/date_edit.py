"""ISO date replacement input alongside Qt's native sections and calendar."""
import re

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtGui import QKeySequence, QValidator
from PySide6.QtWidgets import QApplication, QDateEdit, QLineEdit, QWidget


class DateEdit(QDateEdit):
    input_rejected = Signal(str)
    input_accepted = Signal()

    def __init__(self, parent=None):
        self._replacing_date = False
        super().__init__(parent)
        self.setKeyboardTracking(False)
        self.dateChanged.connect(self._date_was_set)

    def _date_was_set(self, *_):
        self._replacing_date = False
        self.input_accepted.emit()

    def validate(self, text, position):
        if self._replacing_date:
            # Qt's section validator would rewrite a partly entered whole date.
            return QValidator.Acceptable, text, position
        return super().validate(text, position)

    def pending_text(self):
        # 分段输入也可能尚未 interpretText；未保存判断应看到当前文字。
        return self.text()

    def commit_date(self):
        if not self._replacing_date:
            self.interpretText()
            return True
        text = self.lineEdit().text()
        date = QDate.fromString(text, 'yyyy-MM-dd') if re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', text) else QDate()
        if not date.isValid() or not self.minimumDate() <= date <= self.maximumDate():
            self.input_rejected.emit('请输入有效日期（YYYY-MM-DD）；当前输入尚未应用。')
            return False
        self._replacing_date = False
        self.setDate(date)
        self.input_accepted.emit()
        return True

    def discard_pending_input(self):
        self._replacing_date = False
        self.setDate(self.date())
        self.input_accepted.emit()

    def _paste_date(self):
        line = self.lineEdit()
        text = QApplication.clipboard().text().strip()
        if (not self._replacing_date and line.selectedText() != line.text()
                and re.fullmatch(r'[0-9]{1,4}', text)):
            line.paste()
            return
        self._replacing_date = True
        line.setText(text)
        line.setCursorPosition(len(line.text()))

    def contextMenuEvent(self, event):
        menu = self.lineEdit().createStandardContextMenu()
        for action in menu.actions():
            if action.objectName() == 'edit-paste':
                action.triggered.disconnect()
                action.triggered.connect(self._paste_date)
        menu.exec(event.globalPos())
        menu.deleteLater()

    def keyPressEvent(self, event):
        line = self.lineEdit()
        if event.matches(QKeySequence.SelectAll):
            line.selectAll()
            event.accept()
            return
        if event.matches(QKeySequence.Paste):
            self._paste_date()
            event.accept()
            return
        if (not self._replacing_date and line.selectedText() == line.text()
                and event.text() and event.text().isprintable()
                and not event.modifiers() & (Qt.ControlModifier | Qt.AltModifier)):
            self._replacing_date = True
        if self._replacing_date:
            if event.key() == Qt.Key_Escape:
                self.discard_pending_input()
            elif event.key() in (Qt.Key_Return, Qt.Key_Enter):
                if self.commit_date():
                    self.editingFinished.emit()
            else:
                QLineEdit.keyPressEvent(line, event)
            event.accept()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event):
        if self._replacing_date and not self.commit_date():
            # Keep invalid text visible for correction instead of Qt clamping it.
            QWidget.focusOutEvent(self, event)
            return
        super().focusOutEvent(event)

    def stepBy(self, steps):
        if self._replacing_date and not self.commit_date():
            return
        super().stepBy(steps)
