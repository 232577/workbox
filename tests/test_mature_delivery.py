"""交付页的查询范围、全量导出提示和可收起的明细。"""
import pytest
from PySide6.QtCore import QDate, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from compact_ui import CompactPanel
from delivery_ui import TodayDeliveryDialog
from desktop_service import DesktopService


@pytest.fixture
def page(tmp_path):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    service = DesktopService(tmp_path / 'data' / 'isolated.db')
    service.receive_text('1. 匿名目标项目\n2. 匿名其他项目')
    with service.db.connect() as connection:
        connection.executemany('''INSERT INTO delivery_events
            (project_id,project_name,batch_name,kind,archive_path,occurred_at)
            VALUES (?,?,'匿名批次','manual-files','',?)''', (
            (1, '匿名目标项目', '2026-10-25T04:00:00Z'),
            (1, '匿名目标项目', '2026-10-25T05:00:00Z'),
            (2, '匿名其他项目', '2026-10-26T04:00:00Z'),
        ))
    dialog = TodayDeliveryDialog(service)
    dialog.resize(980, 680)
    dialog.date_edit.setDate(QDate(2026, 10, 25))
    dialog.show()
    app.processEvents()
    yield dialog
    dialog.shutdown()


def test_all_dates_replaces_the_single_day_input(page):
    assert page.date_edit.isVisible() and page.table.rowCount() == 1
    page.all_dates.trigger()
    assert page.table.rowCount() == 2
    assert not page.date_edit.isVisible()
    assert page.date_scope_button.isVisible() and '全部日期' in page.date_scope_button.text()
    choose_date = next(action for action in page.date_scope_button.menu().actions()
                       if '选择日期' in action.text())
    choose_date.trigger()
    assert page.date_edit.isVisible() and page.date_edit.date() == QDate(2026, 10, 25)
    assert not page.date_scope_button.isVisible()
    assert page.table.rowCount() == 1


def test_current_query_and_whole_report_have_distinct_scope_cues(page):
    page.all_dates.trigger()
    page.show_voided.trigger()
    page.search.setText('目标')
    assert page.table.rowCount() == 1
    assert '已撤销' in page.compact_filter_button.text()
    assert '当前筛选' in page.type_total.text() and '合计 1 个项目' in page.type_total.text()
    assert '全部' in page.export_button.text()
    assert '不受当前筛选影响' in page.export_button.toolTip()
    page.show_voided.trigger()
    assert '已撤销' not in page.compact_filter_button.text()


def test_empty_search_describes_current_results(page):
    page.all_dates.trigger()
    assert page.table.rowCount() == 2
    page.search.setText('没有这个项目')
    assert page.table.rowCount() == 0
    assert page.summary.isVisible() and '当前筛选' in page.summary.text()
    page.search.clear()
    assert page.table.rowCount() == 2 and not page.summary.isVisible()


def test_detail_can_be_closed_and_reopened_without_losing_the_selected_event(page):
    page.table.selectRow(0)
    QTest.qWait(80)
    project_height, detail_height = page.record_splitter.sizes()
    assert project_height > detail_height > 0
    page.details.selectRow(1)
    selected = page.details.currentItem().data(Qt.UserRole)
    page.details_toggle.click()
    assert page.table.isVisible() and not page.details.isVisible()
    assert page.table.currentRow() == 0
    page.refresh()
    assert not page.details.isVisible()
    page.details_toggle.click()
    QTest.qWait(80)
    assert page.details.isVisible()
    assert page.details.currentItem().data(Qt.UserRole) == selected
    assert page.record_splitter.sizes() == [project_height, detail_height]


def test_delivery_sections_follow_the_shell_title_alignment(page):
    page.hide()
    panel = CompactPanel(page.service)
    panel.resize(1200, 780)
    panel.show()
    try:
        panel.today_button.click()
        delivery = panel.today_dialog
        delivery.all_dates.trigger()
        for width, height in ((1200, 780), (660, 480)):
            panel.resize(width, height)
            QTest.qWait(80)
            title_left = panel.page_title.mapTo(panel, QPoint()).x()
            assert delivery.date_scope_button.mapTo(panel, QPoint()).x() == title_left
            if delivery.project_heading.isVisible():
                assert delivery.project_heading.mapTo(panel, QPoint()).x() == title_left
    finally:
        panel.today_dialog.shutdown()
        panel.document_pane.shutdown()
        panel.hide()
