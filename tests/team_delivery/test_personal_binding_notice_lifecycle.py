"""Exercise the original-site review notice with anonymous snapshots and isolated storage."""

from dataclasses import replace
from pathlib import Path
from threading import Event
from time import monotonic, sleep

import pytest
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox

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
def binding_desktop(tmp_path, monkeypatch):
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
        def publish(snapshot, *, wait=True):
            site._show_snapshot(snapshot or Snapshot(None))
            if wait:
                wait_for(lambda: not workspace._loading and workspace._imported == snapshot)

        yield desktop, page, publish
    finally:
        desktop.shutdown()
        app.processEvents()


@pytest.fixture
def personal_binding_review(binding_desktop):
    desktop, page, publish = binding_desktop
    account = Account('local-session', '匿名账号', 'material', 'browser-session', 'anonymous')
    first = Task('15', '匿名同名项目', account.id, Stage.READY, 'first-revision')
    second = Task('16', '匿名同名项目', account.id, Stage.READY, 'second-revision')

    publish(Snapshot(account, (first, second)))
    assert not desktop.service.list_projects()
    assert '待办需要核对' in page._result.text() and '#15' in page._result.text()
    assert '待办需要核对' in desktop.panel.notice.toolTip()
    assert desktop.bubble.isVisible() and '#15' in desktop.bubble.text.toolTip()
    page._tasks.selectRow(0)
    assert not page._bind_task.isHidden()
    return desktop, page, publish, Snapshot(account, (first,))


def test_explicit_binding_reuses_existing_project_without_title_version(binding_desktop, monkeypatch):
    desktop, page, publish = binding_desktop
    service = desktop.service
    account = Account('local-session', '匿名账号', 'material', 'browser-session', 'anonymous')
    title = '匿名材料项目'
    task = Task('27', title + 'V1.0', account.id, Stage.READY, 'first-revision')
    project_id, = service.capture_message(title, '匿名原批次')['project_ids']
    service.bulk_update_projects([project_id], status='开发中')
    workspace = service.workspaces.get_workspace(project_id)
    working_file = Path(workspace['path']) / '保留材料.txt'
    working_file.write_bytes(b'original work')

    publish(Snapshot(account, (task,)))

    assert [project['id'] for project in service.list_projects()] == [project_id]
    assert not service.get_project(project_id)['original_task_id']
    assert '#27' in page._result.text()
    page._tasks.selectRow(0)
    assert not page._bind_task.isHidden()

    def select(_parent, dialog_title, prompt, items, *_):
        assert dialog_title == '关联本机项目'
        assert task.title in prompt and '#27' in prompt
        option, = [item for item in items if item.startswith(f'#{project_id} ·')]
        assert title in option and '匿名原批次' in option
        return option, True

    monkeypatch.setattr(QInputDialog, 'getItem', select)
    page._bind_task.click()
    wait_for(lambda: '已关联本机项目' in page._result.text())

    project, = service.list_projects()
    assert project['id'] == project_id and project['name'] == title
    assert project['status'] == '开发中' and project['original_task_id'] == '27'
    assert service.workspaces.get_workspace(project_id) == workspace
    assert working_file.read_bytes() == b'original work'
    assert page._bind_task.isHidden()
    assert '待办需要核对' not in desktop.panel.notice.toolTip()


@pytest.mark.parametrize('decision', ['cancel', 'confirm', 'changed-session'])
def test_supplemental_site_sync_uses_selected_existing_delivery(binding_desktop, monkeypatch, decision):
    desktop, _, publish = binding_desktop
    service = desktop.service
    title = '匿名已交付项目'
    project_id, = service.capture_message(title, '匿名旧交付批次')['project_ids']
    working_file = Path(service.workspaces.get_workspace(project_id)['path']) / '原材料.txt'
    working_file.write_bytes(b'original work')
    delivery = service.confirm_delivery(project_id, confirmed=True)
    event_id = delivery['event_id']
    original_events = service.db.delivery_events_for_project(project_id)
    original_time = service.get_project(project_id)['delivered_at']
    archived_file = Path(delivery['archive']['path']) / working_file.name
    account = Account('material-session', '匿名材料账号', 'material', 'material-browser', 'material')
    task = Task('624', title, account.id, Stage.READY, 'revision-624')
    other = Task('641', '匿名其他任务', account.id, Stage.READY, 'revision-641')
    publish(Snapshot(account, (task, other)))
    workspace = desktop.personal_site
    assert not workspace.projects.pending(account)
    assert workspace.projects.delivery_for_event(event_id) is None
    desktop.panel.nav_projects.click()
    desktop.panel.project_list.selectAll()
    caption = '补同步到这是家…'
    assert all(action.text() != caption for action in desktop.panel.project_context_menu(project_id).actions())
    desktop.panel.project_list.clearSelection()
    action, = [item for item in desktop.panel.project_context_menu(project_id).actions() if item.text() == caption]
    assert action.isEnabled()
    prompts, woken = [], []

    def answer(dialog):
        message = dialog.text()
        prompts.append(message)
        assert '#624' in message and title in message and '#641' not in message
        assert '本机交付时间' in message
        assert dialog.standardButtons() == QMessageBox.Yes | QMessageBox.No
        assert dialog.standardButton(dialog.defaultButton()) == QMessageBox.No
        assert dialog.button(QMessageBox.Yes).text() == '确认补同步'
        assert dialog.button(QMessageBox.No).text() == '取消'
        if decision == 'changed-session':
            publish(Snapshot(replace(account, session='new-material-browser'), (task, other)), wait=False)
            woken.clear()
        return QMessageBox.No if decision == 'cancel' else QMessageBox.Yes

    monkeypatch.setattr(QMessageBox, 'exec', answer)
    monkeypatch.setattr(workspace, 'wake', lambda: woken.append(True))
    action.trigger()
    wait_for(lambda: prompts and not desktop.busy)

    assert len(prompts) == 1
    if decision == 'confirm':
        queued, = workspace.projects.pending(account)
        assert (queued.project_id, queued.event_id, queued.task_id) == (project_id, event_id, '624')
        assert woken
    else:
        assert not workspace.projects.pending(account)
        assert not woken
        if decision == 'changed-session':
            assert '原站账号或任务已变化' in desktop.panel.notice.toolTip()
    assert service.db.delivery_events_for_project(project_id) == original_events
    assert service.get_project(project_id)['status'] == '已交付'
    assert service.get_project(project_id)['delivered_at'] == original_time
    assert archived_file.read_bytes() == working_file.read_bytes() == b'original work'


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
