from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication

from compact_ui import CompactPanel
from desktop_service import DesktopService


@pytest.fixture
def panel(tmp_path):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    service = DesktopService(tmp_path / 'data/workbox.db')
    service.receive_text('\n'.join(f'{n}. 匿名材料核对项目{n:02d}' for n in range(1, 45)))
    value = CompactPanel(service)
    value.show()
    app.processEvents()
    yield value
    value.document_pane.shutdown()
    value.hide()


def test_narrow_detail_returns_to_same_reading_position_and_restores_wide_split(panel):
    panel.project_splitter.setSizes([400, 560])
    panel._remember_project_split()
    wide = panel.project_splitter.sizes()
    panel.resize(660, 480)
    QApplication.processEvents()
    assert panel.project_side.isVisible() and not panel.document_side.isVisible()
    panel.project_list.verticalScrollBar().setValue(450)
    item = panel.project_list.itemAt(QPoint(20, 20))
    point = panel.project_list.visualItemRect(item).center()
    QTest.mouseClick(panel.project_list.viewport(), Qt.LeftButton, pos=point)
    QApplication.processEvents()
    assert panel.document_side.isVisible() and not panel.project_side.isVisible()
    assert panel.project_back_button.isVisible()
    selected_id = panel.current_project['id']
    panel.project_back_button.click()
    QApplication.processEvents()
    assert panel.project_side.isVisible() and not panel.document_side.isVisible()
    assert panel.current_project['id'] == selected_id
    assert panel.project_list.itemAt(QPoint(20, 20)) is item
    panel.refresh()
    assert panel.project_side.isVisible()
    panel.resize(1200, 780)
    QApplication.processEvents()
    assert panel.project_side.isVisible() and panel.document_side.isVisible()
    assert panel.project_splitter.sizes() == wide


def test_filter_chips_are_visible_and_clear_only_their_own_condition(panel):
    panel.search.setText('匿名')
    panel.due_filter.setCurrentIndex(panel.due_filter.findData('unscheduled'))
    assert panel.active_project_filters.isVisible()
    assert '未设日期' in panel.project_filter_chips['due'].text()
    panel.project_filter_chips['due'].click()
    assert panel.due_filter.currentData() is None
    assert panel.search.text() == '匿名'
    assert not panel.active_project_filters.isVisible()


def test_selected_project_has_direct_workspace_and_guarded_delivery_actions(panel, monkeypatch):
    panel.project_list.setCurrentRow(0)
    pid = panel.current_project['id']
    assert panel.open_workspace_button.isVisible()
    spy = QSignalSpy(panel.workspace_requested)
    panel.open_workspace_button.click()
    assert spy.at(0) == [pid]
    assert not panel.confirm_delivery_button.isVisible()
    panel.service.db.set_status(pid, '待交付')
    panel.refresh()
    assert panel.confirm_delivery_button.isVisible()
    calls = []
    monkeypatch.setattr(panel, 'confirm_delivery', calls.append)
    panel.confirm_delivery_button.click()
    assert calls == [pid]
    panel.set_busy(True)
    assert not panel.confirm_delivery_button.isEnabled()
    assert not panel.open_workspace_button.isEnabled()


def test_search_keeps_a_stable_command_position_across_project_and_zip_scope(panel):
    assert panel.header_surface.isAncestorOf(panel.search)
    panel.search.setText('匿名')
    panel.tabs.setCurrentIndex(1)
    assert panel.header_surface.isAncestorOf(panel.search)
    assert panel.search.isVisible()
    panel.tabs.setCurrentIndex(0)
    assert panel.header_surface.isAncestorOf(panel.search)
    assert panel.search.text() == '匿名'


def test_dragging_project_split_keeps_all_filters_readable(panel):
    panel.resize(1200, 780)
    panel.due_filter.setCurrentIndex(panel.due_filter.findData('unscheduled'))
    QApplication.processEvents()
    handle = panel.project_splitter.handle(1)
    start = handle.rect().center()
    QTest.mousePress(handle, Qt.LeftButton, pos=start)
    QTest.mouseMove(handle, start - QPoint(600, 0))
    QTest.mouseRelease(handle, Qt.LeftButton, pos=start - QPoint(600, 0))
    QApplication.processEvents()
    controls = [*panel.status_buttons.values(), panel.added_date_filter,
                panel.project_filter_button, panel.clear_project_filters]
    for control in controls:
        owner = panel.header_surface if control in (panel.project_filter_button, panel.clear_project_filters) else panel.project_side
        top_left = control.mapTo(owner, control.rect().topLeft())
        assert owner.rect().contains(control.rect().translated(top_left))
        assert control.width() >= control.minimumSizeHint().width()


def test_tab_after_receive_files_reaches_project_controls(panel):
    panel.activateWindow()
    panel.files_button.setFocus(Qt.TabFocusReason)
    QApplication.processEvents()
    visited = []
    for _ in range(8):
        previous = QApplication.focusWidget()
        QTest.keyClick(previous, Qt.Key_Tab)
        QApplication.processEvents()
        current = QApplication.focusWidget()
        assert current is not previous
        visited.append(current)
    assert panel.search in visited or panel.bulk_mode in visited
