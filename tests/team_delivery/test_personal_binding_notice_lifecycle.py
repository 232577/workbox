"""Exercise the original-site review notice with anonymous snapshots and isolated storage."""

from dataclasses import replace
from threading import Event
from time import monotonic, sleep

import pytest
from PySide6.QtWidgets import QApplication

from workbox.desktop.desktop_controller import FloatingController
from workbox.desktop.desktop_service import DesktopService
from workbox.team_delivery.application import IntakeController
from workbox.team_delivery.contracts import Account, Snapshot, Stage, Task


def wait_for(predicate, seconds=8):
    deadline = monotonic() + seconds
    while monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return
        sleep(0.01)
    assert predicate()


@pytest.fixture
def personal_binding_review(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    service = DesktopService(tmp_path / 'data' / 'workbox.db')
    desktop = FloatingController(service, tray_enabled=False)
    monkeypatch.setattr(IntakeController, 'start', lambda self: None)
    assert desktop.start()
    try:
        desktop.monitor_timer.stop()
        desktop.panel.nav_team.click()
        desktop.panel.hide()
        page = desktop.panel.team_page
        site = desktop.panel.team_controller
        workspace = desktop.personal_site
        account = Account('local-session', '匿名账号', 'material', 'browser-session', 'anonymous')
        first = Task('15', '匿名同名项目', account.id, Stage.READY, 'first-revision')
        second = Task('16', '匿名同名项目', account.id, Stage.READY, 'second-revision')

        def publish(snapshot, *, wait=True):
            site._show_snapshot(snapshot or Snapshot(None))
            if wait:
                wait_for(lambda: not workspace._loading and workspace._imported == snapshot)

        publish(Snapshot(account, (first, second)))
        assert not service.list_projects()
        assert '待办需要核对' in page._result.text() and '#15' in page._result.text()
        assert '待办需要核对' in desktop.panel.notice.toolTip()
        assert desktop.bubble.isVisible() and '#15' in desktop.bubble.text.toolTip()
        page._tasks.selectRow(0)
        assert not page._bind_task.isHidden()
        yield desktop, page, publish, Snapshot(account, (first,))
    finally:
        desktop.shutdown()
        app.processEvents()


@pytest.mark.parametrize('resolved', ['linked_after_refresh', 'sign_out'])
def test_review_notice_clears_when_binding_no_longer_needs_review(personal_binding_review, resolved):
    desktop, page, publish, snapshot = personal_binding_review
    publish(snapshot if resolved == 'linked_after_refresh' else None)

    assert page._bind_task.isHidden()
    if resolved == 'linked_after_refresh':
        project, = desktop.service.list_projects()
        assert project['original_task_id'] == '15'
    messages = (page._result.text(), desktop.panel.notice.toolTip())
    assert [message for message in messages if '待办需要核对' in message] == []
    assert desktop.bubble.isHidden()


def test_switching_accounts_clears_previous_review_before_showing_new_review(personal_binding_review):
    desktop, page, publish, _ = personal_binding_review
    account = Account('next-session', '另一匿名账号', 'material', 'next-browser', 'another')
    tasks = tuple(Task(task_id, '另一组匿名同名项目', account.id, Stage.READY, 'revision')
                  for task_id in ('25', '26'))
    snapshot = Snapshot(account, tasks)

    publish(snapshot, wait=False)

    assert '#15' not in page._result.text()
    assert '#15' not in desktop.panel.notice.toolTip()
    assert desktop.bubble.isHidden()

    publish(snapshot)

    messages = (page._result.text(), desktop.panel.notice.toolTip(), desktop.bubble.text.toolTip())
    assert all('#25' in message and '#15' not in message for message in messages)
    assert desktop.bubble.isVisible()


def test_old_snapshot_error_after_account_switch_does_not_block_new_sync(personal_binding_review, monkeypatch):
    desktop, page, publish, original = personal_binding_review
    workspace = desktop.personal_site
    sync = desktop.service.sync_personal_site_snapshot
    old_started, release_old = Event(), Event()

    def sync_or_fail(snapshot):
        if snapshot.account == original.account:
            old_started.set()
            assert release_old.wait(8)
            raise RuntimeError('匿名旧会话同步失败。')
        return sync(snapshot)

    monkeypatch.setattr(desktop.service, 'sync_personal_site_snapshot', sync_or_fail)
    refreshed = replace(original, tasks=(replace(original.tasks[0], revision='old-refresh'),))
    account = Account('next-session', '另一匿名账号', 'material', 'next-browser', 'another')
    incoming = Snapshot(account, (Task('25', '匿名独立项目', account.id, Stage.READY, 'revision'),))
    try:
        publish(refreshed, wait=False)
        wait_for(old_started.is_set)
        publish(incoming, wait=False)
    finally:
        release_old.set()

    wait_for(lambda: workspace._imported == incoming and not workspace._loading)
    project, = desktop.service.list_projects()
    assert project['original_task_id'] == '25'
    messages = (page._result.text(), desktop.panel.notice.toolTip())
    assert all('匿名旧会话同步失败' not in message for message in messages)


@pytest.mark.parametrize('still_unlinked', [False, True], ids=['resolved', 'refreshed-conflict'])
def test_review_refresh_preserves_later_unrelated_notices(personal_binding_review, still_unlinked):
    desktop, page, publish, snapshot = personal_binding_review
    page.show_personal_result('文件未收好', '匿名文件被占用。')
    desktop.notify('确认交付失败。匿名文件被占用。')

    if still_unlinked:
        current = desktop.panel.team_controller.current_personal_snapshot()
        snapshot = replace(current, tasks=tuple(replace(task, revision='refreshed') for task in current.tasks))
    publish(snapshot)

    assert page._bind_task.isHidden() == (not still_unlinked)
    assert page._result.text() == '文件未收好\n匿名文件被占用。'
    assert desktop.panel.notice.toolTip() == '确认交付失败。匿名文件被占用。'
    assert desktop.bubble.isVisible() and desktop.bubble.text.toolTip() == '确认交付失败。匿名文件被占用。'
