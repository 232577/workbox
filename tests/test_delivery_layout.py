"""交付列表把可用空间交给记录，并保留用户拖动后的上下分配。"""
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QSplitter

from delivery_ui import TodayDeliveryDialog
from compact_ui import CompactPanel
from desktop_service import DesktopService


@pytest.fixture
def service(tmp_path):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    value = DesktopService(tmp_path / 'data/workbox.db')
    value.receive_text('1. 列表空间验证')
    with value.db.connect() as connection:
        for minute in range(20):
            connection.execute('''INSERT INTO delivery_events
                (project_id,project_name,batch_name,kind,archive_path,occurred_at)
                VALUES (1,'列表空间验证','隔离批次','manual-files','',?)''',
                (f'2026-11-03T03:{minute:02d}:00Z',))
    return value


def opened(service, *, embedded=False):
    page = TodayDeliveryDialog(service, embedded=embedded)
    page.resize(980, 680)
    page.all_dates.setChecked(True)
    page.show()
    page.table.selectRow(0)
    QTest.qWait(100)
    return page


def drag_up(page):
    split = page.findChild(QSplitter, 'deliveryPanels')
    assert split is not None, '项目和交付明细之间需要可拖动的分隔条'
    handle = split.handle(1)
    start = handle.rect().center()
    QTest.mousePress(handle, Qt.LeftButton, pos=start)
    QTest.mouseMove(handle, start - QPoint(0, 85), 50)
    QTest.mouseRelease(handle, Qt.LeftButton, pos=handle.rect().center())
    QTest.qWait(100)
    return split


def test_detail_height_can_be_dragged_and_survives_refresh_and_reopen(service):
    page = opened(service)
    assert page.details.viewport().height() >= 140
    assert page.type_summary_card.height() <= 84
    before = page.details.height()
    visible_rows = page.details.viewport().height() // page.details.rowHeight(0)
    split = drag_up(page)
    assert page.details.height() > before
    assert page.details.viewport().height() // page.details.rowHeight(0) >= visible_rows + 1
    chosen = page.details.currentItem().data(Qt.UserRole)
    sizes = split.sizes()
    page.refresh()
    assert split.sizes() == sizes
    assert page.details.currentItem().data(Qt.UserRole) == chosen
    page.close()
    page.shutdown()
    reopened = opened(service)
    restored = reopened.findChild(QSplitter, 'deliveryPanels').sizes()
    assert abs(restored[0] / sum(restored) - sizes[0] / sum(sizes)) < .03
    reopened.shutdown()


def test_compact_tab_keeps_full_height_and_restores_manual_split(service):
    page = opened(service, embedded=True)
    split = drag_up(page)
    normal = split.sizes()
    page.resize(660, 480)
    QTest.qWait(100)
    page.compact_tabs.setCurrentIndex(1)
    QTest.qWait(80)
    assert page.details.isVisible() and not page.table.isVisible()
    assert page.details.viewport().height() >= 150
    page.resize(980, 680)
    QTest.qWait(100)
    restored = split.sizes()
    assert page.table.isVisible() and page.details.isVisible()
    assert abs(restored[0] / sum(restored) - normal[0] / sum(normal)) < .03
    page.shutdown()


def test_list_height_setting_is_validated_without_changing_business_facts(service):
    with service.db.connect() as connection:
        before = [tuple(row) for row in connection.execute('SELECT * FROM delivery_events')]
    service.set_setting('delivery_panel_sizes', [180, 320])
    assert service.get_setting('delivery_panel_sizes') == [180, 320]
    for invalid in ([0, 20], [-1, 10], [True, 20], [1], [1, 2, 3], [1, 100001], '180,320'):
        with pytest.raises(ValueError):
            service.set_setting('delivery_panel_sizes', invalid)
    with service.db.connect() as connection:
        assert [tuple(row) for row in connection.execute('SELECT * FROM delivery_events')] == before


def test_real_shell_allows_more_detail_rows_and_full_detail_view(service):
    panel = CompactPanel(service)
    panel.resize(1000, 700)
    panel.show()
    panel.today_button.click()
    page = panel.today_dialog
    page.all_dates.setChecked(True)
    page.table.selectRow(0)
    QTest.qWait(150)
    initial = page.details.viewport().height()
    split = drag_up(page)
    assert page.details.viewport().height() >= initial + 20
    sizes = split.sizes()
    selected = page.details.currentItem().data(Qt.UserRole)
    before_focus = page.details.height()
    summary_height = page.type_summary_card.height()
    page.expand_details_button.click()
    QTest.qWait(80)
    assert not page.table.isVisible() and page.details.isVisible()
    assert page.details.height() >= before_focus + 90
    page.refresh()
    assert page.details.currentItem().data(Qt.UserRole) == selected
    page.expand_details_button.click()
    QTest.qWait(80)
    assert page.type_summary_card.height() == summary_height
    assert all(abs(actual - expected) <= 1 for actual, expected in zip(split.sizes(), sizes))
    page.expand_details_button.click()
    QTest.qWait(80)
    panel.resize(660, 480)
    QTest.qWait(80)
    assert page.details.isVisible() and not page.table.isVisible()
    assert not page.expand_details_button.isVisible()
    panel.resize(1000, 700)
    QTest.qWait(80)
    assert page.details.isVisible() and not page.table.isVisible()
    page.expand_details_button.click()
    QTest.qWait(80)
    restored = split.sizes()
    assert page.table.isVisible()
    assert abs(restored[0] / sum(restored) - sizes[0] / sum(sizes)) < .03
    assert panel.delivery_page_container.verticalScrollBar().maximum() == 0
    page.shutdown()
    panel.document_pane.shutdown()
    panel.hide()


def test_saved_split_ratio_survives_opening_in_a_different_window(service):
    panel = CompactPanel(service)
    panel.resize(1000, 700)
    panel.show()
    panel.today_button.click()
    page = panel.today_dialog
    page.all_dates.setChecked(True)
    page.table.selectRow(0)
    QTest.qWait(150)
    sizes = drag_up(page).sizes()
    panel.hide()
    page.shutdown()
    panel.document_pane.shutdown()
    reopened = opened(service)
    restored = reopened.record_splitter.sizes()
    assert abs(restored[0] / sum(restored) - sizes[0] / sum(sizes)) < .02
    reopened.close()
    reopened.shutdown()
