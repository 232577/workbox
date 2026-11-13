"""新增日期筛选只保存明确选择，不读取业务数据库。"""
from datetime import date, datetime, timezone

import pytest
from PySide6.QtCore import QCoreApplication, QDate, QEvent, QPoint, Qt, QTimer
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCalendarWidget, QDateEdit, QDateTimeEdit, QDialog,
    QDialogButtonBox, QLabel, QMenu, QToolButton,
)


@pytest.fixture(scope='module')
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


@pytest.fixture
def picker(qapp):
    from project_date_filter import ProjectDateFilter
    widget = ProjectDateFilter()
    widget.show()
    yield widget
    widget.close()


def action(picker, text):
    return next(item for item in picker.menu().actions() if item.text() == text)


def run_dialog(picker, text, callback):
    failures = []

    def interact():
        dialog = QApplication.activeModalWidget()
        try:
            assert isinstance(dialog, QDialog)
            callback(dialog)
        except BaseException as error:
            failures.append(error)
        finally:
            if isinstance(dialog, QDialog) and dialog.isVisible():
                dialog.reject()

    QTimer.singleShot(0, interact)
    action(picker, text).trigger()
    if failures:
        raise failures[0]


def test_default_is_native_menu_and_unknown_is_explicit(picker):
    assert isinstance(picker, QToolButton)
    assert isinstance(picker.menu(), QMenu)
    assert picker.popupMode() == QToolButton.InstantPopup
    assert [item.text() for item in picker.menu().actions()] == [
        '全部日期', '今天', '选择某天…', '选择时间段…', '日期未知']
    assert picker.selection() is None
    assert picker.filter_kwargs() == {}
    assert picker.caption() == '全部日期'
    assert picker.accessibleName() == '新增日期筛选'
    action(picker, '日期未知').trigger()
    assert picker.selection() == 'unknown'
    assert picker.filter_kwargs() == {'added_on': 'unknown'}
    assert picker.caption() == '日期未知'
    action(picker, '全部日期').trigger()
    assert picker.selection() is None


def test_selection_signals_only_actual_changes_and_returns_a_fresh_filter(picker):
    changed = QSignalSpy(picker.changed)
    picker.clear()
    assert changed.count() == 0
    picker.set_day('2028-02-29')
    assert picker.selection() == ('2028-02-29', '2028-02-29')
    assert picker.filter_kwargs() == {'added_on': '2028-02-29'}
    picker.set_day(date(2028, 2, 29))
    picker.set_range('2028-02-29', '2028-02-29')
    assert changed.count() == 1
    kwargs = picker.filter_kwargs()
    kwargs.clear()
    assert picker.filter_kwargs() == {'added_on': '2028-02-29'}
    picker.set_range('2026-12-31', '2027-01-01')
    assert changed.count() == 2
    assert picker.selection() == ('2026-12-31', '2027-01-01')
    assert picker.filter_kwargs() == {'added_from': '2026-12-31', 'added_to': '2027-01-01'}
    assert picker.caption() == '2026-12-31 至 2027-01-01'
    assert picker.caption() in picker.toolTip()
    assert picker.caption() in picker.accessibleDescription()
    picker.set_day('unknown')
    picker.set_day('unknown')
    picker.set_day(None)
    assert changed.count() == 4


@pytest.mark.parametrize('day', ['2027-02-29', '2026-2-03', '2026-01-01 ', '',
                                '2026-01-01T00:00:00', 20261113, datetime(2026, 11, 13)])
def test_invalid_day_is_rejected_without_changing_selection(picker, day):
    picker.set_day('2026-11-13')
    changed = QSignalSpy(picker.changed)
    with pytest.raises(ValueError):
        picker.set_day(day)
    assert picker.selection() == ('2026-11-13', '2026-11-13')
    assert changed.count() == 0


@pytest.mark.parametrize('start,end', [('2026-11-14', '2026-11-13'),
    ('unknown', '2026-11-13'), (None, '2026-11-13'), ('2027-02-29', '2027-03-01')])
def test_invalid_or_reversed_range_is_atomic(picker, start, end):
    picker.set_day('unknown')
    changed = QSignalSpy(picker.changed)
    with pytest.raises(ValueError):
        picker.set_range(start, end)
    assert picker.selection() == 'unknown'
    assert changed.count() == 0


def test_calendar_boundaries_and_inclusive_endpoints(picker):
    picker.set_range('0001-01-01', '9999-12-31')
    assert picker.filter_kwargs() == {'added_from': '0001-01-01', 'added_to': '9999-12-31'}

    def inspect(dialog):
        edits = dialog.findChildren(QDateEdit)
        assert len(edits) == 2
        assert edits[0].date() == QDate(1, 1, 1)
        assert edits[1].date() == QDate(9999, 12, 31)
        for edit in edits:
            assert edit.calendarPopup()
            assert edit.minimumDate() == QDate(1, 1, 1)
            assert edit.maximumDate() == QDate(9999, 12, 31)
        dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Apply).click()

    run_dialog(picker, '选择时间段…', inspect)
    assert picker.selection() == ('0001-01-01', '9999-12-31')


def test_today_uses_beijing_calendar_day(picker, monkeypatch):
    import project_date_filter
    from database import DELIVERY_TIMEZONE

    class FixedDateTime:
        @classmethod
        def now(cls, tz):
            assert tz is DELIVERY_TIMEZONE
            return datetime(2026, 11, 13, 17, 0, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(project_date_filter, 'datetime', FixedDateTime)
    action(picker, '今天').trigger()
    assert picker.selection() == ('2026-11-14', '2026-11-14')


@pytest.mark.parametrize('text,count', [('选择某天…', 1), ('选择时间段…', 2)])
def test_cancel_discards_all_dialog_edits(picker, text, count):
    picker.set_range('2026-11-01', '2026-11-03')
    changed = QSignalSpy(picker.changed)

    def cancel(dialog):
        edits = dialog.findChildren(QDateEdit)
        assert len(edits) == count
        edits[0].setDate(QDate(2028, 2, 29))
        dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Cancel).click()

    run_dialog(picker, text, cancel)
    assert picker.selection() == ('2026-11-01', '2026-11-03')
    assert changed.count() == 0


def test_reversed_dialog_range_disables_apply_and_preserves_entered_dates(picker):
    changed = QSignalSpy(picker.changed)

    def choose(dialog):
        start, end = dialog.findChildren(QDateEdit)
        start.setDate(QDate(2028, 3, 2))
        end.setDate(QDate(2028, 3, 1))
        apply = dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Apply)
        assert not apply.isEnabled()
        error = dialog.findChild(QLabel, 'projectDateFilterError')
        assert error.isVisible() and '结束日期不能早于开始日期' in error.text()
        assert start.date() == QDate(2028, 3, 2) and end.date() == QDate(2028, 3, 1)
        end.setDate(QDate(2028, 3, 4))
        assert apply.isEnabled() and not error.isVisible()
        apply.click()

    run_dialog(picker, '选择时间段…', choose)
    assert picker.selection() == ('2028-03-02', '2028-03-04')
    assert changed.count() == 1


def test_single_day_accepts_native_keyboard_and_calendar_input(picker):
    picker.set_day('2028-02-28')

    def choose(dialog):
        edit, = dialog.findChildren(QDateEdit)
        edit.setFocus()
        edit.setSelectedSection(QDateTimeEdit.DaySection)
        QTest.keyClick(edit, Qt.Key_Up)
        assert edit.date() == QDate(2028, 2, 29)
        QTest.mouseClick(edit, Qt.LeftButton, pos=QPoint(edit.width() - 8, edit.height() // 2))
        QApplication.processEvents()
        calendar = edit.calendarWidget()
        assert isinstance(calendar, QCalendarWidget) and calendar.isVisible()
        calendar_view = calendar.findChild(QAbstractItemView)
        calendar_view.setFocus()
        QTest.keyClick(calendar_view, Qt.Key_Right)
        assert calendar.selectedDate() == QDate(2028, 3, 1)
        QTest.keyClick(calendar_view, Qt.Key_Return)
        QApplication.processEvents()
        assert edit.date() == QDate(2028, 3, 1)
        dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Apply).click()

    run_dialog(picker, '选择某天…', choose)
    assert picker.selection() == ('2028-03-01', '2028-03-01')


def test_menu_opens_and_chooses_unknown_with_keyboard(picker):
    failures = []

    def choose():
        menu = QApplication.activePopupWidget()
        try:
            assert menu is picker.menu() and menu.isVisible()
            menu.setActiveAction(action(picker, '日期未知'))
            QTest.keyClick(menu, Qt.Key_Return)
        except BaseException as error:
            failures.append(error)
        finally:
            menu.close() if isinstance(menu, QMenu) else None

    QTimer.singleShot(0, choose)
    picker.setFocus()
    QTest.keyClick(picker, Qt.Key_Space)
    if failures:
        raise failures[0]
    assert picker.selection() == 'unknown'


def test_invalid_typed_date_is_visible_and_cannot_silently_apply(picker):
    picker.set_day('2026-11-13')
    changed = QSignalSpy(picker.changed)

    def choose(dialog):
        edit, = dialog.findChildren(QDateEdit)
        apply = dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Apply)
        edit.setFocus()
        QTest.keyClick(edit, Qt.Key_A, Qt.ControlModifier)
        QTest.keyClicks(edit, '2027-02-29')
        QTest.keyClick(edit, Qt.Key_Tab)
        assert edit.text() == '2027-02-29'
        assert not apply.isEnabled()
        assert dialog.findChild(QLabel, 'projectDateFilterError').isVisible()
        assert picker.selection() == ('2026-11-13', '2026-11-13')
        edit.setFocus()
        QTest.keyClick(edit, Qt.Key_A, Qt.ControlModifier)
        QTest.keyClicks(edit, '2028-02-29')
        QTest.keyClick(edit, Qt.Key_Return)
        assert apply.isEnabled()
        apply.click()

    run_dialog(picker, '选择某天…', choose)
    assert picker.selection() == ('2028-02-29', '2028-02-29')
    assert changed.count() == 1


def test_cancel_restores_menu_check_and_releases_transient_dialog(picker):
    import shiboken6
    picker.set_day('unknown')
    dialogs = []

    def cancel(dialog):
        dialogs.append(dialog)
        QTest.keyClick(dialog, Qt.Key_Escape)

    run_dialog(picker, '选择时间段…', cancel)
    assert picker.selection() == 'unknown'
    assert action(picker, '日期未知').isChecked()
    assert not action(picker, '选择时间段…').isChecked()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert len(dialogs) == 1 and not shiboken6.isValid(dialogs[0])
