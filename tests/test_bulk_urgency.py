"""Selected-row urgency actions use one atomic batch, with isolated project data."""
import pytest
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from compact_ui import CompactPanel
from desktop_controller import FloatingController
from desktop_service import DesktopService


@pytest.fixture(scope='module')
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


@pytest.fixture
def service(tmp_path):
    result = DesktopService(tmp_path / 'workbox.db')
    result.receive_text('1. 匿名甲系统\n2. 匿名乙系统\n3. 匿名丙系统')
    return result


@pytest.fixture
def panel(qapp, service):
    widget = CompactPanel(service)
    widget.bulk_mode.setChecked(True)
    select(widget, (1, 2))
    yield widget
    widget.close()


def select(panel, ids):
    for index in range(panel.project_list.count()):
        item = panel.project_list.item(index)
        item.setSelected(item.data(Qt.UserRole) in ids)


def action(menu, text):
    return next((item for item in menu.actions() if item.text() == text), None)


def wait(controller):
    for _ in range(800):
        QApplication.processEvents()
        if not controller.busy:
            return
        QTest.qWait(10)
    pytest.fail('Batch urgency operation did not finish')


@pytest.mark.parametrize('initial', [(False, False), (True, False), (True, True)])
@pytest.mark.parametrize('urgent', [True, False])
def test_context_urgency_updates_exact_selection_and_preserves_other_facts(qapp, service, initial, urgent):
    controller = FloatingController(service, tray_enabled=False)
    assert controller.start()
    try:
        for pid, value in zip((1, 2), initial):
            service.set_urgent(pid, value)
        service.set_status(2, '开发中')
        service.update_project_details(1, '保留备注', '2026-12-01')
        before = {pid: service.get_project(pid) for pid in (1, 2, 3)}
        panel = controller.panel
        panel.refresh()
        panel.bulk_mode.setChecked(True)
        select(panel, (1, 2))
        menu = panel.project_context_menu(2)
        choice = action(menu, '标记加急' if urgent else '取消加急')
        assert choice is not None, '多选项目右键菜单缺少批量加急入口'
        assert choice.isEnabled() and not choice.isCheckable()
        assert '所选 2 个项目' in menu.accessibleName()
        choice.trigger()
        wait(controller)
        assert [bool(service.get_project(pid)['urgent']) for pid in (1, 2)] == [urgent, urgent]
        assert service.get_project(3) == before[3]
        for pid in (1, 2):
            after = service.get_project(pid)
            assert {k: v for k, v in after.items() if k not in ('urgent', 'updated_at')} == {
                k: v for k, v in before[pid].items() if k not in ('urgent', 'updated_at')}
        assert {item.data(Qt.UserRole) for item in panel.project_list.selectedItems()} == {1, 2}
    finally:
        controller.shutdown()


def test_menu_keeps_its_selection_snapshot_and_rechecks_busy(panel):
    calls = []
    panel.bulk_requested.connect(lambda ids, options: calls.append((set(ids), options)))
    menu = panel.project_context_menu(1)
    choice = action(menu, '标记加急')
    assert choice is not None
    select(panel, (3,))
    panel.set_busy(True)
    choice.trigger()
    assert calls == []
    panel.set_busy(False)
    choice.trigger()
    assert calls == [({1, 2}, {'urgent': True})]


def test_batch_and_single_scopes_are_visible_menu_labels(panel):
    menu = panel.project_context_menu(2)
    for text in ('所选 2 个项目', '当前项目：匿名乙系统'):
        heading = action(menu, text)
        assert heading is not None and not heading.isSeparator()
        assert not heading.isEnabled()


@pytest.mark.parametrize('field', ['archived_at', 'deleted_at', 'merged_into_id', 'block_reason', 'missing'])
def test_batch_menu_disables_whole_selection_when_any_snapshot_is_uneditable(panel, field):
    if field == 'missing':
        panel._project_rows.pop(1)
    else:
        panel._project_rows[1][field] = 'protected'
    menu = panel.project_context_menu(2)
    for text in ('标记加急', '取消加急'):
        choice = action(menu, text)
        assert choice is not None and not choice.isEnabled()


def test_unselected_right_click_and_single_selection_keep_single_urgency(panel):
    calls, batches = [], []
    panel.urgent_requested.connect(lambda *args: calls.append(args))
    panel.bulk_requested.connect(lambda *args: batches.append(args))
    menu = panel.project_context_menu(3)
    assert action(menu, '标记加急') is None
    action(menu, '加急').trigger()
    select(panel, (1,))
    action(panel.project_context_menu(1), '加急').trigger()
    panel.bulk_mode.setChecked(False)
    action(panel.project_context_menu(2), '加急').trigger()
    assert calls == [(3, True), (1, True), (2, True)]
    assert batches == []


@pytest.mark.parametrize('keyboard', [False, True])
def test_context_event_route_preserves_batch_selection(panel, monkeypatch, keyboard):
    panel.show()
    QApplication.processEvents()
    calls = []
    panel.bulk_requested.connect(lambda ids, options: calls.append((set(ids), options)))
    target = next(panel.project_list.item(i) for i in range(panel.project_list.count())
                  if panel.project_list.item(i).data(Qt.UserRole) == 2)
    panel.project_list.setCurrentItem(target)
    select(panel, (1, 2))
    factory = panel.project_context_menu
    def prepare(pid):
        menu = factory(pid)
        choice = action(menu, '标记加急')
        assert choice is not None
        def trigger():
            choice.trigger()
            menu.close()
        QTimer.singleShot(0, trigger)
        return menu
    monkeypatch.setattr(panel, 'project_context_menu', prepare)
    point = panel.project_list.visualItemRect(target).center()
    reason = QContextMenuEvent.Keyboard if keyboard else QContextMenuEvent.Mouse
    panel.project_list.contextMenuEvent(QContextMenuEvent(reason, point, point))
    assert calls == [({1, 2}, {'urgent': True})]
    assert {item.data(Qt.UserRole) for item in panel.project_list.selectedItems()} == {1, 2}


def test_batch_menu_rejects_more_than_500_selected_projects(panel):
    panel.service.db.create_capture('匿名容量核验', '匿名批次', [
        (f'匿名容量项目{i}', False) for i in range(498)])
    panel.refresh()
    panel.project_list.selectAll()
    assert len(panel.project_list.selectedItems()) == 501
    menu = panel.project_context_menu(2)
    assert not action(menu, '标记加急').isEnabled()
    assert not action(menu, '取消加急').isEnabled()


def test_archival_after_menu_open_rejects_entire_batch(qapp, service):
    controller = FloatingController(service, tray_enabled=False)
    assert controller.start()
    try:
        panel = controller.panel
        panel.bulk_mode.setChecked(True)
        select(panel, (1, 2))
        menu = panel.project_context_menu(2)
        service.bulk_update_projects([1], archived=True)
        action(menu, '标记加急').trigger()
        wait(controller)
        assert [service.get_project(pid)['urgent'] for pid in (1, 2, 3)] == [0, 0, 0]
        assert '失败' in panel.notice.text()
        assert '请先恢复归档项目' in panel.notice.toolTip()
        assert panel.notice.accessibleDescription() == panel.notice.toolTip()
    finally:
        controller.shutdown()


def test_cancel_urgency_under_filter_removes_only_selected_results(qapp, service):
    service.bulk_update_projects([1, 2, 3], urgent=True)
    controller = FloatingController(service, tray_enabled=False)
    assert controller.start()
    try:
        panel = controller.panel
        panel.urgent_filter.setChecked(True)
        panel.bulk_mode.setChecked(True)
        select(panel, (1, 2))
        action(panel.project_context_menu(2), '取消加急').trigger()
        wait(controller)
        assert [service.get_project(pid)['urgent'] for pid in (1, 2, 3)] == [0, 0, 1]
        assert panel.project_list.count() == 1
        assert panel.project_list.item(0).data(Qt.UserRole) == 3
        assert not panel.project_list.selectedItems()
    finally:
        controller.shutdown()
