"""Flat, independently saved controls for the optional workbench shortcut."""
from __future__ import annotations

from sqlite3 import Error as SQLiteError

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QHBoxLayout, QKeySequenceEdit, QLabel, QLineEdit, QSizePolicy, QVBoxLayout, QWidget

from dialog_chrome import InlineError
from global_shortcut import validate_shortcut
from ui_components import CheckBox, button


class _ShortcutEdit(QKeySequenceEdit):
    """Record one chord without letting Return submit the surrounding dialog."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMaximumSequenceLength(1)
        self.setClearButtonEnabled(True)
        self.setMinimumWidth(0)
        self.setMinimumHeight(32)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        for edit in self.findChildren(QLineEdit):
            edit.setMinimumWidth(0)
            edit.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.KeyPress, QEvent.ShortcutOverride):
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            event.accept()
            return
        super().keyPressEvent(event)


class ShortcutSettings(QWidget):
    """Changes take effect only through Apply, outside the parent save action."""

    layout_changed = Signal()

    def __init__(self, service, manager=None, parent=None):
        super().__init__(parent)
        self.service = service
        self.manager = manager
        value = service.get_setting('window_shortcut', '')
        self._saved = value if isinstance(value, str) else ''
        self._saved_verified = isinstance(value, str)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.enabled_checkbox = CheckBox('启用全局快捷键显示 / 隐藏工作箱', self)
        self.enabled_checkbox.setAccessibleName('启用全局快捷键显示或隐藏工作箱')
        self.enabled_checkbox.setChecked(bool(self._saved))
        layout.addWidget(self.enabled_checkbox, 0, Qt.AlignLeft)

        self.sequence_edit = _ShortcutEdit(self)
        self.sequence_edit.setAccessibleName('显示或隐藏工作箱的全局快捷键')
        self.sequence_edit.setAccessibleDescription('按下单组组合键；至少包含 Ctrl、Alt、Shift 中的两项。按回车不会保存工作设置。')
        self.sequence_edit.setKeySequence(QKeySequence(self._saved or 'Ctrl+Alt+W'))
        sequence_label = self._label('快捷键组合')
        sequence_label.setBuddy(self.sequence_edit)
        layout.addWidget(sequence_label)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(self.sequence_edit, 1)
        self.apply_button = button('应用快捷键')
        self.apply_button.setAccessibleName('应用快捷键并立即保存')
        self.apply_button.setAutoDefault(False)
        self.apply_button.setDefault(False)
        self.apply_button.clicked.connect(self.apply_shortcut)
        row.addWidget(self.apply_button)
        layout.addLayout(row)

        layout.addWidget(self._label(
            '使用 Ctrl、Alt、Shift 中至少两项，加字母 A–Z、数字 0–9 或 F1–F11；不支持 Win 键或 F12。', 'subtle'))
        layout.addWidget(self._label(
            '点击“应用快捷键”后立即保存；关闭时也需应用。本窗口的“取消”不会撤销已应用的快捷键。', 'subtle'))
        self.status_label = self._label('')
        self.status_label.setAccessibleDescription('当前有效快捷键与已保存偏好')
        layout.addWidget(self.status_label)
        self.error = InlineError(parent=self)
        layout.addWidget(self.error)
        self.enabled_checkbox.toggled.connect(self._refresh_controls)
        self._refresh_status()
        self._refresh_controls()

    def _label(self, text, role=''):
        label = QLabel(text, self)
        label.setObjectName(role)
        label.setTextFormat(Qt.PlainText)
        label.setWordWrap(True)
        label.setMinimumWidth(0)
        label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        return label

    def _refresh_controls(self):
        available = self.manager is not None
        supported = available and bool(self.manager.supported)
        # An unsupported manager may still remove a saved preference safely.
        self.enabled_checkbox.setEnabled(available and (supported or bool(self._saved)))
        self.sequence_edit.setEnabled(supported and self.enabled_checkbox.isChecked())
        self.apply_button.setEnabled(available and (
            supported or (bool(self._saved) and not self.enabled_checkbox.isChecked())))

    def _refresh_status(self):
        effective = self.manager.shortcut if self.manager is not None else ''
        supported = self.manager is not None and bool(self.manager.supported)
        if not self._saved_verified:
            current = f'当前生效：{effective}。' if effective else '当前无生效快捷键。'
            text = current + '无法核对已保存的快捷键，请检查设置存储后重新应用。'
        elif effective and effective != self._saved:
            saved = self._saved or '关闭'
            text = f'当前生效：{effective}；已保存：{saved}。两者不一致，请重新应用快捷键。'
        elif effective:
            text = f'当前已生效：{effective}，显示 / 隐藏工作箱。'
        elif self._saved:
            if self.manager is None:
                reason = '快捷键服务未就绪，请重新打开工作箱后重试。'
            elif not supported:
                reason = '当前环境不支持全局快捷键；可取消勾选并应用以清除偏好。'
            else:
                reason = '可能被其他程序占用，请更换组合后点击“应用快捷键”重试。'
            text = f'未生效；已保存的组合：{self._saved}。{reason}'
        elif self.manager is None:
            text = '当前已关闭；快捷键服务未就绪，请重新打开工作箱后重试。'
        elif not supported:
            text = '当前已关闭；当前环境不支持全局快捷键。'
        else:
            text = '当前已关闭；勾选启用并点击“应用快捷键”后生效。'
        self.status_label.setText(text)
        self.status_label.setAccessibleName(text)
        self._notify_layout()

    def _notify_layout(self):
        self.layout().invalidate()
        self.updateGeometry()
        self.layout_changed.emit()

    def _persist(self, text):
        if self.service.set_setting('window_shortcut', text) is False:
            raise RuntimeError('快捷键设置未能保存。')

    def apply_shortcut(self):
        if self.manager is None:
            return
        desired = self.sequence_edit.keySequence().toString(QKeySequence.PortableText) if self.enabled_checkbox.isChecked() else ''
        if self.enabled_checkbox.isChecked() and not desired:
            self.error.setText('请先按下快捷键组合，再点击“应用快捷键”重试。')
            self.sequence_edit.setFocus()
            self._notify_layout()
            return
        try:
            desired = validate_shortcut(desired)
            previous_stored = self.service.get_setting('window_shortcut', '')
            if not isinstance(previous_stored, str):
                raise ValueError('无法核对原快捷键设置，请检查设置存储。')
            effective = self.manager.apply(
                desired, persist=self._persist,
                rollback=lambda: self._persist(previous_stored))
        except (ValueError, OSError, RuntimeError, SQLiteError) as error:
            try:
                value = self.service.get_setting('window_shortcut', '')
                self._saved_verified = isinstance(value, str)
                if self._saved_verified:
                    self._saved = value
            except (ValueError, OSError, RuntimeError, SQLiteError):
                self._saved_verified = False
            self.error.setText(f'快捷键未应用：{error}。请检查当前状态并重新应用，或更换组合后重试。')
            self._refresh_status()
            return
        self._saved = effective
        self._saved_verified = True
        self.enabled_checkbox.setChecked(bool(effective))
        if effective:
            self.sequence_edit.setKeySequence(QKeySequence(effective))
        self.error.clear()
        self._refresh_status()
        self._refresh_controls()
