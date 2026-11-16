"""项目新增日期的原生菜单、单日和闭区间选择。"""
from __future__ import annotations

from datetime import date, datetime
import re

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtGui import QActionGroup
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QLabel, QMenu, QToolButton,
    QVBoxLayout,
)

from database import DELIVERY_TIMEZONE
from date_edit import DateEdit
from dialog_lifecycle import transient_dialog
from theme import apply_to


def _day(value):
    """只接受明确日期，不截取时间、不修剪或猜测输入格式。"""
    if type(value) is date:
        return value.isoformat()
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
        raise ValueError('请输入有效日期（YYYY-MM-DD）。')
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as error:
        raise ValueError('请输入有效日期（YYYY-MM-DD）。') from error


def _today():
    return datetime.now(DELIVERY_TIMEZONE).date().isoformat()


class _DateSelectionDialog(QDialog):
    """输入只在应用时提交；两个日期控件不互相收紧范围。"""

    def __init__(self, start, end, *, range_mode=False, parent=None):
        super().__init__(parent)
        self.setWindowTitle('选择新增日期范围' if range_mode else '选择新增日期')
        self.setModal(True)
        self.setMinimumWidth(320)
        self._range_mode = range_mode
        self._selected_range = None
        layout = QVBoxLayout(self)
        note = QLabel('按项目首次收录的北京时间日期筛选。', self)
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        self.start_edit = self._date_edit(start, '开始日期' if range_mode else '日期')
        form.addRow('开始日期：' if range_mode else '日期：', self.start_edit)
        self.end_edit = self._date_edit(end, '结束日期') if range_mode else None
        if self.end_edit is not None:
            form.addRow('结束日期：', self.end_edit)
        layout.addLayout(form)
        self.error_label = QLabel(self)
        self.error_label.setObjectName('projectDateFilterError')
        self.error_label.setTextFormat(Qt.PlainText)
        self.error_label.setWordWrap(True)
        self.error_label.setAccessibleName('日期选择错误')
        layout.addWidget(self.error_label)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Apply | QDialogButtonBox.Cancel, self)
        self.apply_button = self.buttons.button(QDialogButtonBox.Apply)
        self.apply_button.setText('应用')
        self.apply_button.setDefault(True)
        self.buttons.button(QDialogButtonBox.Cancel).setText('取消')
        self.apply_button.clicked.connect(self._apply)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        for edit in (self.start_edit, self.end_edit):
            if edit is not None:
                edit.dateChanged.connect(self._validate)
                edit.lineEdit().textEdited.connect(self._validate)
                edit.input_rejected.connect(self._validate)
        self._validate()
        apply_to(self)

    def _date_edit(self, value, name):
        edit = DateEdit(self)
        edit.setDisplayFormat('yyyy-MM-dd')
        edit.setDateRange(QDate(1, 1, 1), QDate(9999, 12, 31))
        edit.setDate(QDate.fromString(value, 'yyyy-MM-dd'))
        edit.setCalendarPopup(True)
        edit.setAccessibleName(name)
        return edit

    def _read_range(self):
        start = _day(self.start_edit.text())
        end = _day(self.end_edit.text()) if self.end_edit is not None else start
        if end < start:
            raise ValueError('结束日期不能早于开始日期。')
        return start, end

    def _validate(self, *_):
        try:
            self._read_range()
        except ValueError as error:
            self.error_label.setText(str(error))
            self.error_label.show()
            self.apply_button.setEnabled(False)
        else:
            self.error_label.clear()
            self.error_label.hide()
            self.apply_button.setEnabled(True)

    def _apply(self):
        try:
            selected = self._read_range()
        except ValueError:
            self._validate()
            return
        self._selected_range = selected
        self.accept()

    def selected_range(self):
        return self._selected_range


class ProjectDateFilter(QToolButton):
    """选择值独立于当前查询结果，不按已有项目日期生成菜单。"""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._selection = None
        self.setAccessibleName('新增日期筛选')
        self.setCursor(Qt.PointingHandCursor)
        self.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(self)
        self.setMenu(menu)
        self._actions = QActionGroup(self)
        self._actions.setExclusive(True)
        self.all_action = self._action('全部日期', self.clear)
        self.today_action = self._action('今天', lambda: self.set_day(_today()))
        self.day_action = self._action('选择某天…', lambda: self._choose(False))
        self.range_action = self._action('选择时间段…', lambda: self._choose(True))
        self.unknown_action = self._action('日期未知', lambda: self.set_day('unknown'))
        menu.aboutToShow.connect(self._sync_actions)
        self._refresh_caption()
        apply_to(self)

    def _action(self, text, callback):
        action = self.menu().addAction(text)
        action.setCheckable(True)
        self._actions.addAction(action)
        action.triggered.connect(lambda _checked=False: callback())
        return action

    def selection(self):
        return self._selection

    def set_day(self, day):
        if day is None:
            self._set_selection(None)
        elif day == 'unknown':
            self._set_selection('unknown')
        else:
            normalized = _day(day)
            self._set_selection((normalized, normalized))

    def set_range(self, start, end):
        start, end = _day(start), _day(end)
        if end < start:
            raise ValueError('结束日期不能早于开始日期。')
        self._set_selection((start, end))

    def clear(self):
        self._set_selection(None)

    def filter_kwargs(self):
        selected = self._selection
        if selected is None:
            return {}
        if selected == 'unknown':
            return {'added_on': 'unknown'}
        start, end = selected
        return {'added_on': start} if start == end else {'added_from': start, 'added_to': end}

    def caption(self):
        selected = self._selection
        if selected is None:
            return '全部日期'
        if selected == 'unknown':
            return '日期未知'
        start, end = selected
        return start if start == end else f'{start} 至 {end}'

    def _set_selection(self, selected):
        if selected == self._selection:
            return
        self._selection = selected
        self._refresh_caption()
        self.changed.emit()

    def _refresh_caption(self):
        caption = self.caption()
        self.setText(caption)
        self.setToolTip(caption)
        self.setAccessibleDescription(caption)
        self._sync_actions()

    def _sync_actions(self):
        selected = self._selection
        if selected is None:
            checked = self.all_action
        elif selected == 'unknown':
            checked = self.unknown_action
        elif selected[0] != selected[1]:
            checked = self.range_action
        else:
            checked = self.today_action if selected[0] == _today() else self.day_action
        checked.setChecked(True)

    def _choose(self, range_mode):
        selected = self._selection
        start, end = selected if isinstance(selected, tuple) else (_today(), _today())
        with transient_dialog(_DateSelectionDialog(start, end, range_mode=range_mode, parent=self.window())) as dialog:
            if dialog.exec() == QDialog.Accepted:
                self.set_range(*dialog.selected_range())
        # QAction在打开对话框前已切换勾选，取消后须恢复当前筛选的勾选项。
        self._sync_actions()
