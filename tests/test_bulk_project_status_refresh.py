from contextlib import contextmanager
from time import monotonic

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractItemView, QApplication, QMessageBox

from workbox.desktop.desktop_controller import FloatingController
from workbox.desktop.desktop_service import DesktopService


@pytest.fixture
def prepared_controller(tmp_path):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    service = DesktopService(tmp_path / 'data' / 'workbox.db')
    controller = FloatingController(service, tray_enabled=False)
    assert controller.start()
    controller.monitor_timer.stop()
    try:
        ids = service.receive_text('1. 匿名一号管理系统\n2. 匿名二号管理系统\n3. 匿名三号管理系统')['project_ids']
        controller.refresh()
        yield controller, ids
    finally:
        controller.shutdown()


def wait_for_update(controller):
    deadline = monotonic() + 5
    while controller.busy and monotonic() < deadline:
        QTest.qWait(10)
    assert not controller.busy


def test_bulk_status_uses_visible_snapshot_for_confirmation(prepared_controller, monkeypatch):
    controller, ids = prepared_controller
    panel = controller.panel
    operations = []
    panel.bulk_requested.disconnect(controller.update_projects)
    panel.bulk_requested.connect(lambda *args: operations.append(args))

    def unexpected_project_read(*_):
        pytest.fail('bulk status made a separate foreground query for a project')

    monkeypatch.setattr(controller.service, 'get_project', unexpected_project_read)
    panel.request_bulk(ids[:2], {'status': '开发中'})
    assert operations == [(ids[:2], {'status': '开发中'})]


def test_status_transaction_rechecks_a_new_delivery_before_writing(prepared_controller, monkeypatch):
    controller, ids = prepared_controller
    panel = controller.panel
    with controller.service.db.connect() as connection:
        connection.execute("UPDATE projects SET status='已交付' WHERE id=?", (ids[1],))
    monkeypatch.setattr(QMessageBox, 'question', lambda *_: pytest.fail('the list still contains the earlier state'))

    panel.request_bulk(ids[:2], {'status': '开发中'})
    wait_for_update(controller)
    assert controller.service.get_project(ids[0])['status'] == '待开发'
    assert controller.service.get_project(ids[1])['status'] == '已交付'


def test_already_delivered_project_can_be_reset_after_confirmation(prepared_controller, monkeypatch):
    controller, ids = prepared_controller
    with controller.service.db.connect() as connection:
        connection.execute("UPDATE projects SET status='已交付' WHERE id=?", (ids[1],))
    controller.refresh()
    monkeypatch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.Yes)
    controller.panel.request_bulk(ids[:2], {'status': '开发中'})
    wait_for_update(controller)
    assert [controller.service.get_project(pid)['status'] for pid in ids] == ['开发中', '开发中', '待开发']


def test_batch_status_refresh_skips_unrelated_views_and_preserves_selection(prepared_controller, monkeypatch):
    controller, ids = prepared_controller
    panel = controller.panel

    for index in range(panel.project_list.count()):
        item = panel.project_list.item(index)
        item.setSelected(item.data(Qt.UserRole) in ids[:2])
    calls = []
    monkeypatch.setattr(panel, '_refresh_other_views', lambda: calls.append('unrelated'))
    monkeypatch.setattr(controller.service, 'list_projects', lambda *args, **kwargs: calls.append('all rows') or [])
    monkeypatch.setattr(controller.orb, 'set_summary', lambda count: calls.append(count))
    panel.request_bulk(ids[:2], {'status': '开发中'})
    wait_for_update(controller)

    assert calls == [3]
    assert panel.project_list.selectionMode() == QAbstractItemView.ExtendedSelection
    assert {item.data(Qt.UserRole) for item in panel.project_list.selectedItems()} == set(ids[:2])
    assert panel.project_status_counts['开发中'] == 2
    assert controller.service.get_project(ids[2])['status'] == '待开发'


def test_bulk_transaction_checks_project_change_blocks_in_one_query(prepared_controller, monkeypatch):
    controller, ids = prepared_controller
    db = controller.service.db
    connect = db.connect
    guard_reads = []

    @contextmanager
    def traced_connect():
        with connect() as connection:
            def trace(sql):
                if sql.lstrip().upper().startswith('SELECT') and (
                        'project_merges' in sql or 'project_change_blocks' in sql):
                    guard_reads.append(sql)
            connection.set_trace_callback(trace)
            yield connection

    monkeypatch.setattr(db, 'connect', traced_connect)
    db.bulk_update_projects(ids, status='开发中')
    assert len(guard_reads) == 1
