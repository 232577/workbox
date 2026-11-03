"""Native date popups must keep readable cells and editable four-digit years."""
import pytest

from PySide6.QtCore import QDate, QPoint, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QLineEdit, QSpinBox,
                              QStyle, QStyleOptionFrame, QStyleOptionViewItem, QToolButton)

from compact_ui import ProjectDetailsDialog
from delivery_ui import TodayDeliveryDialog
from desktop_service import DesktopService
from theme import install_theme, set_theme, tokens


@pytest.fixture(scope='module')
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


@pytest.fixture(params=['delivery', 'deadline'])
def date_dialog(qapp, tmp_path, request):
    service = DesktopService(tmp_path / 'calendar' / 'workbox.db')
    install_theme()
    if request.param == 'delivery':
        dialog = TodayDeliveryDialog(service)
        edit = dialog.date_edit
    else:
        service.receive_text('1. 日期验证示例系统')
        dialog = ProjectDetailsDialog(service, service.get_project(1))
        dialog.due_enabled.setChecked(True)
        edit = dialog.due_date
    edit.setDate(QDate(2026, 11, 3))
    dialog.show()
    if hasattr(dialog, 'timer'):
        dialog.timer.stop()
    qapp.processEvents()
    yield dialog, edit
    edit.calendarWidget().hide()
    if isinstance(dialog, ProjectDetailsDialog):
        dialog.original = dialog.values()
    dialog.close()
    dialog.deleteLater()
    qapp.processEvents()
    install_theme()


def open_calendar(edit):
    QTest.mouseClick(edit, Qt.LeftButton, pos=QPoint(edit.width()-8, edit.height()//2))
    QApplication.processEvents()
    calendar = edit.calendarWidget()
    assert calendar.isVisible()
    return calendar


@pytest.mark.parametrize('mode', ['light', 'dark', 'system'])
def test_calendar_cells_fit_their_text(date_dialog, mode):
    _, edit = date_dialog
    set_theme(mode, persist=False)
    calendar = open_calendar(edit)
    view = calendar.findChild(QAbstractItemView)
    model = view.model()
    for row in range(model.rowCount()):
        for column in range(model.columnCount()):
            index = model.index(row, column)
            text = str(index.data() or '')
            if not text:
                continue
            option = QStyleOptionViewItem()
            option.initFrom(view)
            size = view.itemDelegateForIndex(index).sizeHint(option, index)
            cell = view.visualRect(index)
            assert cell.width() >= size.width(), (text, cell, size)
            assert cell.height() >= size.height(), (text, cell, size)


@pytest.mark.parametrize('mode', ['light', 'dark', 'system'])
def test_calendar_every_label_has_visible_glyphs(date_dialog, mode):
    _, edit = date_dialog
    set_theme(mode, persist=False)
    calendar = open_calendar(edit)
    view = calendar.findChild(QAbstractItemView)
    image = view.viewport().grab().toImage().scaled(view.viewport().size())
    background = QColor(tokens()['surface'])
    for row in range(view.model().rowCount()):
        for column in range(view.model().columnCount()):
            index = view.model().index(row, column)
            if not str(index.data() or ''):
                continue
            cell = view.visualRect(index).adjusted(4, 4, -4, -4)
            contrasting_pixels = sum(
                max(abs(image.pixelColor(x, y).red()-background.red()),
                    abs(image.pixelColor(x, y).green()-background.green()),
                    abs(image.pixelColor(x, y).blue()-background.blue())) > 60
                for y in range(cell.top(), cell.bottom()+1)
                for x in range(cell.left(), cell.right()+1))
            assert contrasting_pixels >= 3, (index.data(), row, column)


@pytest.mark.parametrize('mode', ['light', 'dark', 'system'])
def test_calendar_year_editor_fits_four_digits(date_dialog, mode):
    _, edit = date_dialog
    set_theme(mode, persist=False)
    calendar = open_calendar(edit)
    year_button = calendar.findChild(QToolButton, 'qt_calendar_yearbutton')
    QTest.mouseClick(year_button, Qt.LeftButton)
    QApplication.processEvents()
    year = calendar.findChild(QSpinBox, 'qt_calendar_yearedit')
    assert year.isVisible()
    line = year.findChild(QLineEdit)
    frame = QStyleOptionFrame()
    frame.initFrom(line)
    frame.rect = line.rect()
    contents = line.style().subElementRect(QStyle.SE_LineEditContents, frame, line)
    margins = line.textMargins()
    assert contents.width()-margins.left()-margins.right() >= line.fontMetrics().horizontalAdvance('2026')+2


@pytest.mark.parametrize('mode', ['light', 'dark', 'system'])
def test_calendar_native_navigation_and_date_selection(date_dialog, mode):
    dialog, edit = date_dialog
    set_theme(mode, persist=False)
    calendar = open_calendar(edit)
    next_month = calendar.findChild(QToolButton, 'qt_calendar_nextmonth')
    previous_month = calendar.findChild(QToolButton, 'qt_calendar_prevmonth')
    QTest.mouseClick(next_month, Qt.LeftButton)
    assert (calendar.yearShown(), calendar.monthShown()) == (2026, 12)
    if hasattr(dialog, 'refresh'):
        dialog.refresh()
        assert calendar.monthShown() == 12
    QTest.mouseClick(previous_month, Qt.LeftButton)
    assert (calendar.yearShown(), calendar.monthShown()) == (2026, 11)
    year_button = calendar.findChild(QToolButton, 'qt_calendar_yearbutton')
    QTest.mouseClick(year_button, Qt.LeftButton)
    year = calendar.findChild(QSpinBox, 'qt_calendar_yearedit')
    year_line = year.findChild(QLineEdit)
    year_line.selectAll()
    QTest.keyClicks(year_line, '2027')
    QTest.keyClick(year_line, Qt.Key_Return)
    assert calendar.yearShown() == 2027
    QTest.mouseClick(year_button, Qt.LeftButton)
    year_line.selectAll()
    QTest.keyClicks(year_line, '2026')
    QTest.keyClick(year_line, Qt.Key_Return)
    assert calendar.yearShown() == 2026
    view = calendar.findChild(QAbstractItemView)
    model = view.model()
    target = next(model.index(row, column)
                  for row in range(1, model.rowCount())
                  for column in range(model.columnCount())
                  if str(model.index(row, column).data()) == '15')
    changed = QSignalSpy(edit.dateChanged)
    QTest.mouseClick(view.viewport(), Qt.LeftButton, pos=view.visualRect(target).center())
    QApplication.processEvents()
    assert edit.date() == QDate(2026, 11, 15)
    assert changed.count() == 1
    assert not calendar.isVisible()
    if isinstance(dialog, TodayDeliveryDialog):
        assert dialog._day.isoformat() == '2026-11-15'
    else:
        assert dialog.values()[1] == '2026-11-15'
