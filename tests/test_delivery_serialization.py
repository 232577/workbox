"""交付更正与主控制器共用串行门槛，测试只使用隔离项目。"""
import os
from pathlib import Path
from threading import Event
from time import monotonic, sleep

import pytest
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication

from desktop_controller import FloatingController
from desktop_service import DesktopService


@pytest.fixture
def controller(tmp_path):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    service = DesktopService(tmp_path / 'data' / 'isolated.db')
    service.receive_text('1. 匿名来源项目\n2. 匿名保留项目')
    for pid in (1, 2):
        workspace = Path(service.workspaces.get_workspace(pid)['path'])
        (workspace / f'材料{pid}.txt').write_text('隔离材料', encoding='utf-8')
        service.confirm_delivery(pid, confirmed=True)
    value = FloatingController(service, tray_enabled=False)
    assert value.start()
    value.monitor_timer.stop()
    value.show_panel()
    yield value
    value.shutdown()
    value.dispose_ui()


def wait_for(predicate):
    deadline = monotonic() + 8
    while not predicate() and monotonic() < deadline:
        QApplication.processEvents()
        sleep(.01)
    return predicate()


def show_project_delivery(controller, project_id):
    controller.panel.show_today_deliveries()
    page = controller.panel.today_dialog
    page.all_dates.setChecked(True)
    row = next(index for index in range(page.table.rowCount())
               if page.table.item(index, 0).data(Qt.UserRole) == project_id)
    page.table.selectRow(row)
    assert page.details.rowCount() == 1
    page.details.selectRow(0)
    return page


@pytest.mark.skipif(os.name != 'nt', reason='项目合并使用 Windows 目录身份租约')
@pytest.mark.parametrize('existing_page', [False, True], ids=['first-open', 'reopen'])
def test_foreground_merge_blocks_correction_before_project_identity_changes(controller, existing_page):
    service = controller.service
    if existing_page:
        show_project_delivery(controller, 1)
        controller.panel.nav_projects.click()
    plan = service.preview_project_rename(1, '匿名保留项目', 2)
    assert plan['kind'] == 'merge' and plan['result_status'] == '已交付'
    merge_entered, release_merge = Event(), Event()

    def delayed_merge(**kwargs):
        merge_entered.set()
        if not release_merge.wait(8):
            raise RuntimeError('隔离合并未释放')
        return service.rename_project(1, '匿名保留项目', expected_token=plan['token'],
                                      merge_target_id=2, confirmed_merge=True, **kwargs)

    controller.enqueue('合并匿名项目', delayed_merge)
    try:
        assert wait_for(merge_entered.is_set)
        page = show_project_delivery(controller, 1)
        blocked = not page.void_action.isEnabled()
        if not blocked:
            # 旧实现会让确认框持有来源项目的事件，而事务已将它解析到保留项目。
            def confirm_after_merge():
                release_merge.set()
                finished = wait_for(lambda: controller.worker is None)
                dialog = QApplication.activeModalWidget()
                if dialog is not None:
                    dialog.accept() if finished else dialog.reject()

            QTimer.singleShot(0, confirm_after_merge)
            page.void_action.trigger()
    finally:
        release_merge.set()
    assert wait_for(lambda: not controller.busy)
    assert service.db.canonical_project_id(1) == 2
    assert service.db.get_project(2)['status'] == '已交付'
    assert service.db.delivery_history()['voided_count'] == 0
    assert blocked, '合并进行中时不允许打开旧归属的更正确认'


def test_correction_confirmation_defers_new_foreground_task_until_cancel(controller):
    page = show_project_delivery(controller, 1)
    observed, calls = [], []

    def queue_while_confirming():
        controller.enqueue('匿名后续工作', lambda **_: calls.append('ran'))

        def cancel_confirmation():
            observed.append((controller.worker is None, len(controller.queue), list(calls)))
            dialog = QApplication.activeModalWidget()
            if dialog is not None:
                dialog.reject()

        QTimer.singleShot(160, cancel_confirmation)

    assert page.void_action.isEnabled()
    QTimer.singleShot(0, queue_while_confirming)
    page.void_action.trigger()
    assert wait_for(lambda: not controller.busy)
    assert observed == [(True, 1, [])]
    assert calls == ['ran']
    assert page.void_action.isEnabled()
    assert controller.service.db.delivery_history()['voided_count'] == 0


def test_new_foreground_task_waits_for_confirmed_correction_write(controller, monkeypatch):
    page = show_project_delivery(controller, 1)
    db = controller.service.db
    original = db.set_delivery_events_voided
    writing, release_write = Event(), Event()
    observed = []

    def delayed_write(*args, **kwargs):
        writing.set()
        if not release_write.wait(8):
            raise RuntimeError('隔离更正未释放')
        return original(*args, **kwargs)

    def confirm_then_continue():
        controller.enqueue('匿名后续工作', lambda **_: observed.append(db.delivery_history()['voided_count']))
        dialog = QApplication.activeModalWidget()
        if dialog is not None:
            dialog.accept()

    monkeypatch.setattr(db, 'set_delivery_events_voided', delayed_write)
    QTimer.singleShot(0, confirm_then_continue)
    page.void_action.trigger()
    try:
        assert wait_for(writing.is_set)
        assert page.worker is not None and controller.worker is None
        assert len(controller.queue) == 1 and observed == []
    finally:
        release_write.set()
    assert wait_for(lambda: not controller.busy)
    assert observed == [1]
    assert db.get_project(1)['status'] == '待交付' and db.get_project(2)['status'] == '已交付'


def test_silent_archive_poll_keeps_delivery_actions_available(controller):
    entered, release = Event(), Event()

    def poll(**_):
        entered.set()
        if not release.wait(8):
            raise RuntimeError('隔离轮询未释放')
        return dict(archived=[], pending=0, errors=[])

    controller.enqueue('检查 ZIP 归档', poll, silent=True)
    try:
        assert wait_for(entered.is_set)
        page = show_project_delivery(controller, 1)
        assert page.void_action.isEnabled() and not controller.panel.busy
    finally:
        release.set()
    assert wait_for(lambda: not controller.busy)


def test_quit_rejects_unconfirmed_correction_without_starting_a_write(controller):
    page = show_project_delivery(controller, 1)
    assert page.void_action.isEnabled()
    QTimer.singleShot(0, controller.request_quit)
    page.void_action.trigger()
    assert controller.closed and not controller.owns_lock
    assert page.worker is None and not page.timer.isActive()
    assert controller.service.db.delivery_history()['voided_count'] == 0
