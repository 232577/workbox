"""任务组入口、准确导航与后台退出使用匿名工作箱。"""
import threading
import time

import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QObject, Signal
from types import SimpleNamespace

from desktop_controller import FloatingController
from desktop_service import DesktopService
from ui import FileJob


@pytest.fixture
def controller(tmp_path):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    value = FloatingController(DesktopService(tmp_path / 'data/workbox.db'), tray_enabled=False)
    assert value.start()
    value.monitor_timer.stop()
    yield value
    value.shutdown()
    value.dispose_ui()


def wait(controller):
    deadline = time.monotonic() + 6
    while controller.busy and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(.01)
    assert not controller.busy


def test_global_text_and_explicit_continuation_use_tasks(controller):
    controller.receive({'kind': 'text', 'text': '1. 匿名收件链路系统'})
    wait(controller)
    groups = controller.service.task_groups.snapshot(open_only=False)
    assert len(groups) == 1 and len(groups[0]['tasks']) == 1
    first = groups[0]
    controller.panel.task_text_requested.emit('1. 匿名收件链路系统\n2. 匿名续入系统', first['id'], False)
    wait(controller)
    assert len(controller.service.task_groups.snapshot(open_only=False)) == 1
    assert len(controller.service.task_groups.snapshot()[0]['tasks']) == 2
    result = controller.service.task_groups.receive('1. 匿名定位系统')
    target = controller._result_target(result)
    assert target[0] == 'task-group' and target[1] == result['group_id']
    controller.bubble.navigation_target = target
    controller.show_feedback()
    assert controller.panel.tabs.currentWidget() is controller.panel.task_groups_page


def test_task_intake_keeps_global_storage_recovery_guard(controller):
    service = controller.service
    service._project_changes_ready = True
    service.project_change_errors = [{'global_block': True}]
    with pytest.raises(RuntimeError, match='恢复'):
        service.receive_task_text('1. 匿名恢复保护系统')
    assert service.db.counts()['projects'] == 0
    service.project_change_errors = []


def test_business_page_worker_counts_as_busy_and_shutdown_drains(controller):
    page = controller.panel.task_groups_page
    entered, release = threading.Event(), threading.Event()
    def operation(**_):
        entered.set()
        release.wait(4)
        return {}
    page._run(operation)
    assert entered.wait(2)
    try:
        assert controller.busy
    finally:
        release.set()
        controller.shutdown()
    assert page.worker is None and controller.closed


def test_classification_stays_in_main_window(controller):
    controller.panel.classification_requested.emit()
    page = getattr(controller.panel, 'archive_classification_page', None)
    assert page is not None
    assert controller.panel.tabs.currentWidget() is page
    assert page.window() is controller.panel


def test_pending_feedback_selects_exact_receipt(controller):
    controller.service.receive_task_text('第一条尚需核对的匿名需求')
    result = controller.service.receive_task_text('第二条尚需核对的匿名需求')
    controller.refresh()
    target = controller._result_target(result)
    assert target == ('receipt', result['receipt_id'])
    controller.bubble.navigation_target = target
    controller.show_feedback()
    from PySide6.QtCore import Qt
    assert controller.panel.receipt_list.currentItem().data(Qt.UserRole) == result['receipt_id']


def test_late_page_wait_callback_never_replaces_active_file_worker(controller, monkeypatch):
    import desktop_controller
    callbacks, started = [], []
    class Job(QObject):
        finished = Signal()
        progress_changed = Signal(int)
        def __init__(self, operation, parent):
            super().__init__(parent)
            self.operation = operation
            self.cancel_event = threading.Event()
        def start(self):
            started.append(self)
        def wait(self, *_):
            return True
    monkeypatch.setattr(desktop_controller, 'FileJob', Job)
    monkeypatch.setattr(desktop_controller, 'QTimer', SimpleNamespace(
        singleShot=lambda *args: callbacks.append(args[-1])))
    page = controller.panel.task_groups_page
    page.worker = object()
    try:
        controller.enqueue('匿名A', lambda **_: {})
        assert len(callbacks) == 1
        page.worker = None
        controller.enqueue('匿名B', lambda **_: {})
        first = controller.worker
        assert len(started) == 1
        callbacks[0]()
        assert len(started) == 1 and controller.worker is first
        assert len(controller.queue) == 1
    finally:
        page.worker = None
        controller.worker = None
        controller.queue.clear()


def test_update_backup_failure_restores_business_pages_and_unsaved_rules(controller, monkeypatch, tmp_path):
    import sys
    import update_client
    import update_install
    controller.show_panel()
    controller.panel.show_archive_classification()
    wait(controller)
    page = controller.panel.archive_classification_page
    page.minimum_files.setValue(5)
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(update_client, 'verify_installer', lambda *args: None)
    def fail(*args):
        raise OSError('anonymous backup failure')
    monkeypatch.setattr(update_install, 'backup_database', fail)
    with pytest.raises(OSError, match='backup failure'):
        controller.prepare_update_install(object(), tmp_path / 'installer.exe')
    assert not controller.closed and controller.owns_lock
    assert not page._shutting_down and not controller.panel.task_groups_page._shutting_down
    assert page.minimum_files.value() == 5 and page._dirty
    assert page.save_button.isEnabled()
