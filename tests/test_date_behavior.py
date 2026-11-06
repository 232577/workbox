"""Date filters must preserve native edits while periodic results refresh."""
import pytest

from PySide6.QtCore import QDate, Qt, QTimer, QPoint, QPointF
from PySide6.QtGui import QContextMenuEvent, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDateTimeEdit, QMenu, QMessageBox, QAbstractItemView

from compact_ui import CompactPanel, ProjectDetailsDialog
from desktop_service import DesktopService


@pytest.fixture
def delivery_page(tmp_path):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    service = DesktopService(tmp_path / 'date-behavior' / 'workbox.db')
    panel = CompactPanel(service)
    panel.resize(1000, 700)
    panel.show()
    panel.show_today_deliveries()
    QTest.qWait(80)
    page = panel.today_dialog
    page.timer.stop()
    yield page
    page.shutdown()
    panel.document_pane.shutdown()
    panel.hide()
    panel.deleteLater()
    app.processEvents()


def test_today_refresh_preserves_an_unfinished_year_edit(delivery_page):
    page = delivery_page
    edit = page.date_edit
    edit.setFocus()
    edit.setSelectedSection(QDateTimeEdit.YearSection)
    QTest.keyClicks(edit, '20')
    assert edit.text().startswith('20-')

    # The same callback is run by the two-second refresh timer.
    page.refresh()

    assert edit.text().startswith('20-'), '刷新丢弃了尚未提交的年份输入'
    QTest.keyClicks(edit, '27')
    page.search.setFocus()
    QApplication.processEvents()
    assert edit.date().year() == 2027
    assert page._day.year == 2027


@pytest.mark.parametrize('target', ['2027-02-28', '2028-02-29', '2026-12-31'])
@pytest.mark.parametrize('paste', [False, True])
def test_complete_date_replacement_filters_the_exact_entered_day(delivery_page, target, paste):
    page = delivery_page
    edit = page.date_edit
    edit.setDate(QDate(2026, 11, 4))
    edit.setFocus()
    QTest.keyClick(edit, Qt.Key_A, Qt.ControlModifier)
    if paste:
        QApplication.clipboard().setText(target)
        QApplication.processEvents()
        QTest.keyClick(edit, Qt.Key_V, Qt.ControlModifier)
    else:
        QTest.keyClicks(edit, target)
    page.search.setFocus()
    QApplication.processEvents()
    assert edit.text() == target
    assert edit.date().toString('yyyy-MM-dd') == target
    assert page._day.isoformat() == target


def test_invalid_complete_date_keeps_filter_and_can_be_corrected(delivery_page):
    page = delivery_page
    edit = page.date_edit
    edit.setDate(QDate(2026, 11, 4))
    edit.setFocus()
    QTest.keyClick(edit, Qt.Key_A, Qt.ControlModifier)
    QTest.keyClicks(edit, '2027-02-29')
    page.search.setFocus()
    QApplication.processEvents()
    assert edit.text() == '2027-02-29'
    assert page._day.isoformat() == '2026-11-04'
    assert page.status_note.isVisible()
    edit.setFocus()
    QTest.keyClick(edit, Qt.Key_A, Qt.ControlModifier)
    QTest.keyClicks(edit, '2028-02-29')
    QTest.keyClick(edit, Qt.Key_Return)
    assert edit.text() == '2028-02-29'
    assert page._day.isoformat() == '2028-02-29'


def test_escape_discards_full_date_entry_without_changing_filter(delivery_page):
    page = delivery_page
    edit = page.date_edit
    edit.setDate(QDate(2026, 11, 4))
    edit.setFocus()
    QTest.keyClick(edit, Qt.Key_A, Qt.ControlModifier)
    QTest.keyClicks(edit, '2027-02-28')
    QTest.keyClick(edit, Qt.Key_Escape)
    assert edit.text() == '2026-11-04'
    assert page._day.isoformat() == '2026-11-04'


def test_today_explicitly_discards_invalid_pending_date(delivery_page):
    page = delivery_page
    edit = page.date_edit
    today = edit.date()
    edit.setFocus()
    QTest.keyClick(edit, Qt.Key_A, Qt.ControlModifier)
    QTest.keyClicks(edit, '2027-02-29')
    page.search.setFocus()
    QApplication.processEvents()
    page.today_action.trigger()
    assert edit.text() == today.toString('yyyy-MM-dd')
    assert page._day == today.toPython()


def test_context_menu_paste_keeps_invalid_date_for_correction(delivery_page):
    page = delivery_page
    edit = page.date_edit
    edit.setDate(QDate(2026, 11, 4))
    edit.setFocus()
    QTest.keyClick(edit, Qt.Key_A, Qt.ControlModifier)
    QApplication.clipboard().setText('2027-02-29')
    QApplication.processEvents()
    def choose_paste():
        menu = QApplication.activePopupWidget()
        assert isinstance(menu, QMenu)
        next(a for a in menu.actions() if a.objectName() == 'edit-paste').trigger()
        menu.close()
    QTimer.singleShot(30, choose_paste)
    event = QContextMenuEvent(QContextMenuEvent.Mouse, edit.rect().center(), edit.mapToGlobal(edit.rect().center()))
    QApplication.sendEvent(edit, event)
    page.search.setFocus()
    QApplication.processEvents()
    assert edit.text() == '2027-02-29'
    assert page._day.isoformat() == '2026-11-04'


@pytest.mark.parametrize('section, text, expected', [
    (QDateTimeEdit.YearSection, '2027', '2027-11-04'),
    (QDateTimeEdit.MonthSection, '12', '2026-12-04'),
    (QDateTimeEdit.DaySection, '15', '2026-11-15'),
])
def test_pasting_one_selected_section_keeps_native_editing(delivery_page, section, text, expected):
    page = delivery_page
    edit = page.date_edit
    edit.setDate(QDate(2026, 11, 4))
    edit.setFocus()
    edit.setSelectedSection(section)
    QApplication.clipboard().setText(text)
    QApplication.processEvents()
    QTest.keyClick(edit, Qt.Key_V, Qt.ControlModifier)
    page.search.setFocus()
    QApplication.processEvents()
    assert edit.text() == expected
    assert page._day.isoformat() == expected


@pytest.fixture
def deadline_dialog(delivery_page):
    service = delivery_page.service
    service.receive_text('1. 匿名截止日期验证项目')
    dialog = ProjectDetailsDialog(service, service.get_project(1), delivery_page.window())
    dialog.due_enabled.setChecked(True)
    dialog.due_date.setDate(QDate(2026, 11, 4))
    dialog.show()
    QTest.qWait(40)
    yield dialog
    dialog.original = dialog.values()
    dialog.close()


@pytest.mark.parametrize('target', ['2027-02-28', '2028-02-29'])
@pytest.mark.parametrize('paste', [False, True])
def test_deadline_complete_date_is_saved_exactly(deadline_dialog, target, paste):
    dialog = deadline_dialog
    edit = dialog.due_date
    edit.setFocus()
    QTest.keyClick(edit, Qt.Key_A, Qt.ControlModifier)
    if paste:
        QApplication.clipboard().setText(target)
        QApplication.processEvents()
        QTest.keyClick(edit, Qt.Key_V, Qt.ControlModifier)
    else:
        QTest.keyClicks(edit, target)
    dialog.save_button.click()
    assert dialog.service.get_project(1)['due_date'] == target


def test_invalid_deadline_cannot_save_then_can_be_corrected(deadline_dialog):
    dialog = deadline_dialog
    edit = dialog.due_date
    edit.setFocus()
    QTest.keyClick(edit, Qt.Key_A, Qt.ControlModifier)
    QTest.keyClicks(edit, '2027-02-29')
    dialog.save_button.click()
    assert dialog.isVisible() and dialog.error.text()
    assert dialog.service.get_project(1)['due_date'] is None
    assert edit.text() == '2027-02-29'
    QTest.keyClick(edit, Qt.Key_A, Qt.ControlModifier)
    QTest.keyClicks(edit, '2028-02-29')
    dialog.save_button.click()
    assert dialog.service.get_project(1)['due_date'] == '2028-02-29'


def test_cancel_pending_deadline_does_not_save(deadline_dialog, monkeypatch):
    dialog = deadline_dialog
    edit = dialog.due_date
    edit.setFocus()
    QTest.keyClick(edit, Qt.Key_A, Qt.ControlModifier)
    QTest.keyClicks(edit, '2027-02-28')
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.Yes)
    dialog.reject()
    assert not dialog.isVisible()
    assert dialog.service.get_project(1)['due_date'] is None


@pytest.mark.parametrize('text', ['2027-13-01', '2027-02-30', '2027-00-01', '2027-11-00', '2027-01-32', 'abc', '2027', '2027-2-3', '0000-01-01'])
@pytest.mark.parametrize('submit', ['blur', 'return', 'tab'])
def test_invalid_whole_dates_never_change_the_filter(delivery_page, text, submit):
    page = delivery_page
    edit = page.date_edit
    edit.setDate(QDate(2026, 11, 4))
    edit.setFocus()
    QTest.keyClick(edit, Qt.Key_A, Qt.ControlModifier)
    QTest.keyClicks(edit, text)
    if submit == 'blur':
        page.search.setFocus()
    else:
        QTest.keyClick(edit, Qt.Key_Return if submit == 'return' else Qt.Key_Tab)
    QApplication.processEvents()
    assert edit.text() == text
    assert edit.date() == QDate(2026, 11, 4)
    assert page._day.isoformat() == '2026-11-04'


def test_calendar_can_replace_an_invalid_pending_date(delivery_page):
    page = delivery_page
    edit = page.date_edit
    edit.setDate(QDate(2026, 11, 4))
    edit.setFocus()
    QTest.keyClick(edit, Qt.Key_A, Qt.ControlModifier)
    QTest.keyClicks(edit, '2027-02-29')
    QTest.mouseClick(edit, Qt.LeftButton, pos=QPoint(edit.width()-8, edit.height()//2))
    QApplication.processEvents()
    calendar = edit.calendarWidget()
    assert calendar.isVisible()
    assert edit.date() == QDate(2026, 11, 4)
    assert page._day.isoformat() == '2026-11-04'
    view = calendar.findChild(QAbstractItemView)
    target = next(view.model().index(row, col)
                  for row in range(1, view.model().rowCount())
                  for col in range(view.model().columnCount())
                  if str(view.model().index(row, col).data()) == '15')
    QTest.mouseClick(view.viewport(), Qt.LeftButton, pos=view.visualRect(target).center())
    assert edit.text() == '2026-11-15'
    assert page._day.isoformat() == '2026-11-15'


def test_date_scope_switches_return_matching_delivery_rows(delivery_page):
    page = delivery_page
    service = page.service
    service.db.create_capture('匿名日期验收', '匿名批次', [('匿名跨年项目', False), ('匿名历史项目', False), ('匿名今天项目', False)])
    today = page.date_edit.date().toString('yyyy-MM-dd')
    with service.db.connect() as connection:
        for pid, name, day in ((1, '匿名跨年项目', '2027-02-28'), (2, '匿名历史项目', '2026-11-03'), (3, '匿名今天项目', today)):
            connection.execute('''INSERT INTO delivery_events
                (project_id,project_name,batch_name,kind,archive_path,occurred_at)
                VALUES (?,?,'匿名批次','manual-files','',?)''', (pid, name, day+'T04:00:00Z'))
    page.refresh()
    assert [row['project_id'] for row in page.rows] == [3]
    edit = page.date_edit
    edit.setFocus()
    QTest.keyClick(edit, Qt.Key_A, Qt.ControlModifier)
    QTest.keyClicks(edit, '2027-02-28')
    page.search.setFocus()
    QApplication.processEvents()
    assert [row['project_id'] for row in page.rows] == [1]
    page.all_dates.setChecked(True)
    assert {row['project_id'] for row in page.rows} == {1, 2, 3}
    assert not edit.isEnabled()
    page.all_dates.setChecked(False)
    assert [row['project_id'] for row in page.rows] == [1]
    page.today_action.trigger()
    assert edit.isEnabled() and not page.all_dates.isChecked()
    assert edit.text() == today
    assert [row['project_id'] for row in page.rows] == [3]


def test_wheel_cannot_clamp_an_invalid_full_date_into_a_different_day(delivery_page):
    page = delivery_page
    edit = page.date_edit
    edit.setDate(QDate(2026, 11, 4))
    edit.setFocus()
    QTest.keyClick(edit, Qt.Key_A, Qt.ControlModifier)
    QTest.keyClicks(edit, '2027-02-29')
    event = QWheelEvent(QPointF(edit.rect().center()), QPointF(edit.mapToGlobal(edit.rect().center())), QPoint(), QPoint(0, 120), Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    QApplication.sendEvent(edit, event)
    assert edit.text() == '2027-02-29'
    assert edit.date() == QDate(2026, 11, 4)
    assert page._day.isoformat() == '2026-11-04'


@pytest.mark.parametrize('clear_key', [Qt.Key_Backspace, Qt.Key_Delete])
def test_clear_all_then_type_complete_date_keeps_the_exact_year(delivery_page, clear_key):
    page = delivery_page
    edit = page.date_edit
    edit.setDate(QDate(2026, 11, 4))
    edit.setFocus()
    QTest.keyClick(edit, Qt.Key_A, Qt.ControlModifier)
    QTest.keyClick(edit, clear_key)
    QTest.keyClicks(edit, '2028-02-29')
    page.search.setFocus()
    QApplication.processEvents()
    assert edit.text() == '2028-02-29'
    assert page._day.isoformat() == '2028-02-29'


@pytest.fixture
def existing_deadline_dialog(delivery_page):
    service = delivery_page.service
    service.receive_text('1. 匿名既有截止日期项目')
    service.update_project_details(1, '', '2026-11-04')
    dialog = ProjectDetailsDialog(service, service.get_project(1), delivery_page.window())
    dialog.show()
    QTest.qWait(40)
    yield dialog
    dialog.original = dialog.values()
    dialog.close()


@pytest.mark.parametrize('exit_path', ['escape', 'reject', 'close'])
def test_pending_native_year_edit_can_refuse_discard(existing_deadline_dialog, monkeypatch, exit_path):
    dialog = existing_deadline_dialog
    edit = dialog.due_date
    edit.setFocus()
    edit.setSelectedSection(QDateTimeEdit.YearSection)
    QTest.keyClicks(edit, '2027')
    assert edit.text() == '2027-11-04'
    assert edit.date() == QDate(2026, 11, 4)
    questions = []
    def keep_editing(*args):
        questions.append(args)
        return QMessageBox.No
    monkeypatch.setattr(QMessageBox, 'question', keep_editing)

    if exit_path == 'escape':
        QTest.keyClick(edit, Qt.Key_Escape)
    elif exit_path == 'reject':
        dialog.reject()
    else:
        dialog.close()

    assert len(questions) == 1, '尚未提交的原生分段输入必须参与未保存判断'
    assert dialog.isVisible()
    assert edit.text() == '2027-11-04'
    assert dialog.service.get_project(1)['due_date'] == '2026-11-04'


def test_saving_pending_native_year_edit_interprets_the_displayed_value(existing_deadline_dialog):
    dialog = existing_deadline_dialog
    edit = dialog.due_date
    edit.setFocus()
    edit.setSelectedSection(QDateTimeEdit.YearSection)
    QTest.keyClicks(edit, '2027')
    assert edit.date() == QDate(2026, 11, 4)
    dialog.save_button.click()
    assert dialog.service.get_project(1)['due_date'] == '2027-11-04'
