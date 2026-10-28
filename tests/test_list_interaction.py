import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication, QAbstractItemView, QListWidget, QStyle, QStyleOptionSlider

from compact_ui import CompactPanel, populate, selected
from desktop_service import DesktopService


@pytest.fixture(scope='module')
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def rows(count=100):
    return [dict(id=i + 1, name=f'项目{i + 1:03d}系统') for i in range(count)]


def test_unchanged_refresh_keeps_scroll_selection_and_item_objects(qapp):
    widget = QListWidget()
    widget.resize(400, 260)
    widget.show()
    values = rows()
    populate(widget, values, lambda row: row['name'])
    widget.setCurrentRow(0)
    qapp.processEvents()
    bar = widget.verticalScrollBar()
    bar.setValue(65)
    original = widget.item(0)
    reset = QSignalSpy(widget.model().modelReset)
    populate(widget, values, lambda row: row['name'])
    assert bar.value() == 65
    assert reset.count() == 0 and widget.item(0) is original
    assert selected(widget) == 1
    widget.close()


def test_caption_change_updates_in_place_without_scrolling(qapp):
    widget = QListWidget()
    widget.resize(400, 260)
    widget.show()
    values = rows()
    populate(widget, values, lambda row: row['name'])
    original = widget.item(50)
    widget.setCurrentRow(0)
    qapp.processEvents()
    widget.verticalScrollBar().setValue(60)
    values[50]['name'] = '已更新的项目备注'
    populate(widget, values, lambda row: row['name'])
    assert widget.verticalScrollBar().value() == 60
    assert widget.item(50) is original and original.text() == '已更新的项目备注'
    widget.close()


@pytest.fixture
def panel(qapp, tmp_path):
    service = DesktopService(tmp_path / 'workbox.db')
    service.db.create_capture('隔离滚动验证', '隔离批次', [(r['name'], False) for r in rows(100)])
    panel = CompactPanel(service)
    panel.resize(840, 670)
    panel.show()
    qapp.processEvents()
    yield panel
    panel.hide()


def test_lists_use_standard_scrollbars_and_pixel_scrolling(panel, qapp):
    for widget in (panel.project_list, panel.zip_list, panel.receipt_list):
        assert widget.verticalScrollMode() == QAbstractItemView.ScrollPerPixel
        assert widget.uniformItemSizes() == (widget is not panel.project_list)
    bar = panel.project_list.verticalScrollBar()
    assert bar.width() >= 16 and bar.hasTracking()
    bar.setValue(120)
    before = panel.project_list.visualItemRect(panel.project_list.item(3)).top()
    bar.setValue(121)
    after = panel.project_list.visualItemRect(panel.project_list.item(3)).top()
    assert before - after == 1
    option = QStyleOptionSlider()
    bar.initStyleOption(option)
    handle = bar.style().subControlRect(QStyle.CC_ScrollBar, option, QStyle.SC_ScrollBarSlider, bar)
    assert handle.height() >= 30, '大量项目时滑块仍须有足够高度，便于鼠标抓住'


def test_refresh_during_drag_defers_structural_change_until_release(panel, qapp):
    widget = panel.project_list
    widget.setCurrentRow(0)
    selected_id = selected(widget)
    bar = widget.verticalScrollBar()
    bar.setValue(bar.maximum() // 2)
    value = bar.value()
    bar.setSliderDown(True)
    panel.service.db.create_capture('新增隔离项目', '新批次', [('新增隔离项目系统', False)])
    panel.refresh()
    assert widget.count() == 100 and bar.value() == value and bar.isSliderDown()
    bar.setSliderDown(False)
    qapp.processEvents()
    assert widget.count() == 101
    assert selected(widget) == selected_id


def test_new_rows_preserve_visible_anchor_and_multi_selection(panel, qapp):
    widget = panel.project_list
    panel.bulk_mode.setChecked(True)
    widget.item(0).setSelected(True)
    widget.item(2).setSelected(True)
    selected_ids = {item.data(Qt.UserRole) for item in widget.selectedItems()}
    widget.verticalScrollBar().setValue(widget.verticalScrollBar().maximum() // 2)
    anchor = widget.itemAt(QPoint(5, 5))
    anchor_id, top = anchor.data(Qt.UserRole), widget.visualItemRect(anchor).top()
    panel.service.db.create_capture('加急隔离项目', '加急批次', [('加急隔离项目系统', True)])
    panel.refresh()
    qapp.processEvents()
    anchor = next(widget.item(i) for i in range(widget.count()) if widget.item(i).data(Qt.UserRole) == anchor_id)
    assert widget.visualItemRect(anchor).top() == top
    assert {item.data(Qt.UserRole) for item in widget.selectedItems()} == selected_ids


def test_focus_receipt_clears_hidden_filters_selects_first_and_scrolls(panel, qapp):
    panel.search.setText('不存在')
    panel.status_buttons['已交付'].setChecked(True)
    panel.due_filter.setCurrentIndex(1)
    panel.urgent_filter.setChecked(True)
    panel.bulk_mode.setChecked(True)
    panel.tabs.setCurrentIndex(1)
    assert panel.focus_projects([90, 91])
    qapp.processEvents()
    assert panel.tabs.currentIndex() == 0
    assert panel.current_project['id'] == 90
    assert panel.batch_filter.currentData() == panel.service.get_project(90)['batch_id']
    assert not panel.bulk_mode.isChecked()
    item = panel.project_list.currentItem()
    assert panel.project_list.viewport().rect().intersects(panel.project_list.visualItemRect(item))
    assert '2' in panel.notice.text() and '已定位' in panel.notice.text()


def test_focusing_archived_receipt_shows_it_without_restoring(panel):
    panel.service.bulk_update_projects([90], archived=True)
    assert panel.focus_projects([90])
    assert panel.current_project['id'] == 90 and panel.current_project['archived_at']
    assert panel.archive_filter.currentData() is None
    assert not panel.focus_projects([99999])


def test_focus_new_batch_not_present_in_previous_filter_options(panel):
    result = panel.service.db.create_capture('新收件', '刚刚收到', [('新甲系统', False), ('新乙系统', False)])
    assert panel.focus_projects(result['project_ids'])
    assert panel.batch_filter.currentData() == result['batch_id']
    assert panel.project_list.count() == 2


def test_compact_panel_keeps_selected_row_fully_readable(panel, qapp):
    panel.focus_projects([90, 91])
    panel.resize(660, 492)
    qapp.processEvents()
    qapp.processEvents()
    assert panel.width() == 660 and panel.height() == 492
    viewport = panel.project_list.viewport().rect()
    item_rect = panel.project_list.visualItemRect(panel.project_list.currentItem())
    assert viewport.height() >= item_rect.height()
    assert viewport.contains(item_rect.topLeft()) and viewport.contains(item_rect.bottomRight())
