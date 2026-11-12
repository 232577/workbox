"""Anonymous regressions for read feedback, queue cleanup and recovery blocking."""
import sqlite3
import sys
import weakref
from threading import Event
from time import monotonic, sleep

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QDialog

from desktop_controller import FloatingController
from desktop_service import DesktopService


@pytest.fixture
def controller(tmp_path):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    value = FloatingController(DesktopService(tmp_path / 'anonymous.db'), tray_enabled=False)
    assert value.start()
    value.monitor_timer.stop()
    value.service.receive_text('1. Anonymous audit system')
    value.panel.refresh()
    value.panel.project_list.setCurrentRow(0)
    yield value
    value.shutdown()
    value.dispose_ui()


def wait_until(predicate):
    deadline = monotonic() + 5
    while monotonic() < deadline:
        QApplication.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        if predicate():
            return
        sleep(.01)
    assert predicate(), 'Anonymous desktop operation timed out'


@pytest.mark.parametrize('error_type', [sqlite3.OperationalError, OSError, RuntimeError])
def test_primary_read_failure_has_persistent_feedback_and_clears_after_recovery(controller, monkeypatch, error_type):
    panel = controller.panel
    errors = []
    monkeypatch.setattr(sys, 'excepthook', lambda cls, value, tb: errors.append(str(value)))
    before = panel.current_project
    original = controller.service.project_workbench_snapshot

    def fail(*args, **kwargs):
        raise error_type('anonymous snapshot unavailable')

    monkeypatch.setattr(controller.service, 'project_workbench_snapshot', fail)
    panel.search.setText('nonmatching')
    assert errors == []
    assert panel.current_project is before
    assert panel.project_list.count() == 1
    assert '读取失败' in panel.notice.text()
    assert 'anonymous snapshot unavailable' in panel.notice.toolTip()
    assert not panel._notice_timer.isActive()
    panel.show_notice('anonymous intake success', kind='intake-success')
    assert '读取失败' in panel.notice.text()
    assert not panel._notice_timer.isActive()
    monkeypatch.setattr(controller.service, 'project_workbench_snapshot', original)
    panel.refresh()
    assert panel.project_list.count() == 0
    assert panel.current_project is None
    assert 'anonymous snapshot unavailable' not in panel.notice.toolTip()


def test_primary_read_failure_does_not_stall_remaining_queue(controller, monkeypatch):
    release, first_entered, second_entered = Event(), Event(), Event()
    errors = []
    monkeypatch.setattr(sys, 'excepthook', lambda cls, value, tb: errors.append(str(value)))

    def first(**kwargs):
        first_entered.set()
        assert release.wait(3)

    controller.enqueue('anonymous first', first)
    assert first_entered.wait(3)
    controller.enqueue('anonymous second', lambda **_: second_entered.set())
    original = controller.service.project_workbench_snapshot

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError('anonymous snapshot unavailable')

    monkeypatch.setattr(controller.service, 'project_workbench_snapshot', fail)
    release.set()
    wait_until(lambda: not controller.busy)
    assert errors == []
    assert second_entered.is_set()
    assert controller.worker is None and not controller.queue
    assert not controller.panel.busy
    assert 'anonymous snapshot unavailable' in controller.panel.notice.toolTip()
    monkeypatch.setattr(controller.service, 'project_workbench_snapshot', original)
    controller.refresh()
    assert 'anonymous snapshot unavailable' not in controller.panel.notice.toolTip()


@pytest.mark.parametrize('callback_kind', ['completion', 'preview'])
def test_failed_result_callback_reports_error_and_advances_queue(controller, monkeypatch, callback_kind):
    release, entered, second_entered = Event(), Event(), Event()
    errors = []
    monkeypatch.setattr(sys, 'excepthook', lambda cls, value, tb: errors.append(str(value)))

    def fail(*args):
        raise RuntimeError('anonymous result display failure')

    dialog = QDialog(controller.panel)
    dialog.show_preview = fail
    dialog.show()

    def first(**kwargs):
        entered.set()
        assert release.wait(3)
        if callback_kind == 'preview':
            return {'project_change_preview': (weakref.ref(dialog), 1, {}, None)}

    controller.enqueue('anonymous first', first,
                       on_complete=fail if callback_kind == 'completion' else None)
    assert entered.wait(3)
    old_worker = controller.worker
    destroyed = []
    old_worker.destroyed.connect(lambda *_: destroyed.append(True))
    controller.enqueue('anonymous second', lambda **_: second_entered.set())
    release.set()
    wait_until(lambda: not controller.busy)
    assert errors == []
    assert second_entered.is_set()
    assert 'anonymous result display failure' in controller.panel.notice.toolTip()
    wait_until(lambda: bool(destroyed))
    dialog.hide()


@pytest.mark.parametrize('raise_after_enqueue', [False, True])
def test_completion_callback_can_start_new_worker_without_losing_its_owner(controller, raise_after_enqueue):
    entered, release = Event(), Event()

    def next_operation(**kwargs):
        entered.set()
        assert release.wait(3)

    def completed(_):
        controller.enqueue('anonymous next', next_operation)
        if raise_after_enqueue:
            raise RuntimeError('anonymous callback failure after enqueue')

    controller.enqueue('anonymous callback', lambda **_: None, on_complete=completed)
    wait_until(entered.is_set)
    worker = controller.worker
    assert worker is not None and worker.isRunning()
    release.set()
    wait_until(lambda: not controller.busy)
    if raise_after_enqueue:
        assert 'anonymous callback failure after enqueue' in controller.panel.notice.toolTip()


@pytest.mark.parametrize('silent', [False, True])
def test_manual_and_timer_scan_observe_runtime_recovery_block(controller, monkeypatch, silent):
    scans = []
    monkeypatch.setattr(controller.service, 'scan_archives', lambda **_: scans.append(True))
    controller.service.workspaces.document_versions.blocked[None] = 'anonymous recovery required'
    controller.check_archives(silent=silent)
    assert controller.storage_blocked
    assert scans == [] and controller.worker is None and not controller.queue
    assert controller.panel.busy
    assert '需要恢复' in controller.panel.notice.toolTip()
    assert not controller.panel._notice_timer.isActive()
    assert not controller.monitor_timer.isActive()


def test_runtime_recovery_block_cancels_pending_writes_and_allows_new_work_after_recovery(controller):
    entered, release, second_entered, cancelled, recovered_work = (Event() for _ in range(5))

    def first(**kwargs):
        entered.set()
        assert release.wait(3)
        controller.service.workspaces.document_versions.blocked[None] = 'anonymous recovery required'

    controller.enqueue('anonymous first', first)
    assert entered.wait(3)
    controller.enqueue('anonymous second', lambda **_: second_entered.set(), on_cancel=cancelled.set)
    release.set()
    wait_until(lambda: controller.worker is None)
    assert not second_entered.is_set()
    assert cancelled.is_set() and not controller.queue
    assert controller.storage_blocked and controller.foreground_busy
    assert '需要恢复' in controller.panel.notice.toolTip()
    assert controller.service.recover_project_changes() == []
    controller.refresh()
    assert not controller.storage_blocked and not controller.panel.busy
    assert controller.monitor_timer.isActive()
    controller.enqueue('anonymous recovered', lambda **_: recovered_work.set())
    wait_until(lambda: not controller.busy)
    assert recovered_work.is_set()
    assert not controller.storage_blocked and not controller.panel.busy
    assert controller.monitor_timer.isActive()
    assert '需要恢复' not in controller.panel.notice.toolTip()


def test_runtime_block_cancels_active_scan_even_with_only_queued_export(controller, monkeypatch):
    entered, cancelled, exported = Event(), Event(), Event()

    def scan(*, cancel_event, **kwargs):
        entered.set()
        if cancel_event.wait(3):
            cancelled.set()

    monkeypatch.setattr(controller.service, 'scan_archives', scan)
    controller.enqueue('anonymous scan', controller.service.scan_archives, silent=True)
    assert entered.wait(3)
    controller.enqueue('anonymous export', lambda **_: exported.set(),
                       on_complete=controller._pending_export_finished)
    controller.service.workspaces.document_versions.blocked[None] = 'anonymous recovery required'
    controller.check_archives()
    wait_until(lambda: controller.worker is None)
    assert cancelled.is_set() and not exported.is_set() and not controller.queue
    assert '需要恢复' in controller.panel.notice.text()


def test_quit_finishes_even_when_completed_result_cannot_refresh(controller, monkeypatch):
    entered = Event()

    def first(*, cancel_event, **kwargs):
        entered.set()
        assert cancel_event.wait(3)

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError('anonymous snapshot unavailable')

    controller.enqueue('anonymous first', first)
    assert entered.wait(3)
    monkeypatch.setattr(controller.service, 'project_workbench_snapshot', fail)
    controller.request_quit()
    wait_until(lambda: controller.closed)
    assert not controller.owns_lock and not controller.queue and controller.worker is None


def test_read_recovery_preserves_unrelated_persistent_warning(controller, monkeypatch):
    panel = controller.panel
    panel.show_notice('anonymous existing warning')
    original = controller.service.project_workbench_snapshot

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError('anonymous snapshot unavailable')

    monkeypatch.setattr(controller.service, 'project_workbench_snapshot', fail)
    panel.refresh()
    assert 'anonymous snapshot unavailable' in panel.notice.toolTip()
    monkeypatch.setattr(controller.service, 'project_workbench_snapshot', original)
    panel.refresh()
    assert panel.notice.toolTip() == 'anonymous existing warning'
    assert not panel._notice_timer.isActive()


def test_storage_recovery_notice_has_priority_over_primary_read_failure(controller, monkeypatch):
    panel = controller.panel
    original = controller.service.project_workbench_snapshot

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError('anonymous snapshot unavailable')

    monkeypatch.setattr(controller.service, 'project_workbench_snapshot', fail)
    panel.refresh()
    controller.service.workspaces.document_versions.blocked[None] = 'anonymous recovery required'
    controller.check_archives()
    assert '需要恢复' in panel.notice.text()
    assert 'anonymous snapshot unavailable' in panel.notice.toolTip()
    monkeypatch.setattr(controller.service, 'project_workbench_snapshot', original)
    panel.refresh()
    assert '需要恢复' in panel.notice.text()
    assert 'anonymous snapshot unavailable' not in panel.notice.toolTip()
    assert not panel._notice_timer.isActive()


def test_startup_storage_warning_clears_after_explicit_recovery(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    service = DesktopService(tmp_path / 'anonymous.db')
    recover = service.recover_project_changes

    def blocked_recovery():
        service.workspaces.document_versions.blocked[None] = 'anonymous recovery required'
        return [{'global_block': True, 'error': 'anonymous recovery required'}]

    monkeypatch.setattr(service, 'recover_project_changes', blocked_recovery)
    value = FloatingController(service, tray_enabled=False)
    try:
        assert value.start()
        assert value.storage_blocked and not value.monitor_timer.isActive()
        assert '需要恢复' in value.panel.notice.text()
        monkeypatch.setattr(service, 'recover_project_changes', recover)
        assert service.recover_project_changes() == []
        value.refresh()
        assert not value.storage_blocked and value.monitor_timer.isActive()
        assert '需要恢复' not in value.panel.notice.toolTip()
    finally:
        value.shutdown()
        value.dispose_ui()
