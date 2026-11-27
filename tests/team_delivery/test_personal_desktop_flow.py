"""The office is never configured in this end-to-end test of the two desktop entry points."""
from pathlib import Path
from time import monotonic, sleep

import pytest
from PySide6.QtCore import QMimeData, QPointF, Qt, QTimer, QUrl
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QApplication, QDialog, QInputDialog, QMessageBox

from anonymous_document import make_document
from test_qt_site import install_completion_view, page_script, site, writes
from workbox.desktop.compact_ui import ProjectDeliveryDialog
from workbox.desktop.desktop_controller import FloatingController
from workbox.desktop.desktop_service import DesktopService
from workbox.team_delivery import application
from workbox.team_delivery.contracts import Account, Snapshot, Stage, Task


def until(predicate, seconds=15):
    deadline = monotonic() + seconds
    while monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return
        sleep(.01)
    assert predicate()


@pytest.mark.parametrize('target', ['workspace', 'team'])
@pytest.mark.parametrize(('number', 'version'), [('', ''), ('', 'V1.0'), ('1. ', '')],
                         ids=['exact', 'versionless-word', 'pasted-numbered-title'])
def test_drop_then_confirm_submits_only_the_bound_original_task(site, target, number, version, tmp_path, monkeypatch):
    browser, _, _, _, rows, history, _ = site
    rows[0].update(id=55, name=number + '匿名待交件系统' + version)
    monkeypatch.setattr(application, 'LIVE_SITE_URL', browser.origin)
    service = DesktopService(tmp_path / 'data' / 'workbox.db')
    pasted_project_id = service.receive_text(rows[0]['name'])['project_ids'][0] if number else None
    desktop = FloatingController(service, tray_enabled=False)
    try:
        assert desktop.start()
        desktop.monitor_timer.stop()
        desktop.panel.nav_team.click()
        client = desktop.panel.team_controller
        page = desktop.panel.team_page
        until(lambda: not client._busy)
        page._login.click()
        until(lambda: any(item[1] == '/' for item in history))
        until(lambda: client.current_personal_snapshot() is not None, seconds=22)
        site_page = client._site_session.page
        install_completion_view(site_page)
        until(lambda: len(service.list_projects()) == 2 and not desktop.busy)
        project = next(p for p in service.list_projects() if p['original_task_id'] == '55')
        project_id = project['id']
        if pasted_project_id is not None:
            assert project_id == pasted_project_id and project['name'] == '匿名待交件系统'
        assert project['status'] == '待开发' and not writes(history)
        path = tmp_path / '匿名待交件系统.docx'
        make_document(path)
        original = path.read_bytes()
        if target == 'workspace':
            desktop.panel.nav_projects.click()
            desktop.panel.intake.emit({'kind': 'files', 'paths': [str(path)]})
        else:
            until(lambda: not page.busy and not page.intake_block)
            mime = QMimeData()
            mime.setUrls([QUrl.fromLocalFile(str(path))])
            event = QDropEvent(QPointF(5, 5), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
            page.dropEvent(event)
            assert event.isAccepted()
        until(lambda: service.get_project(project_id)['status'] == '待交付' and not desktop.busy)
        stored = Path(service.workspaces.get_workspace(project_id)['path']) / path.name
        assert stored.read_bytes() == path.read_bytes() == original
        assert not writes(history) and rows[0]['matStatus'] == '待制作'

        desktop.panel.nav_projects.click()
        if number:
            monkeypatch.setattr(ProjectDeliveryDialog, 'exec', lambda *_: pytest.fail('Unexpected delivery dialog'))
            desktop.panel.show_project_board()
            lanes = desktop.panel.project_board.lanes
            lanes['待交付'].actions[project_id].click()
            assert QApplication.activeModalWidget() is None
            until(lambda: project_id in lanes['已完成'].rows and project_id not in lanes['待交付'].rows)
        else:
            def accept_delivery():
                dialog = QApplication.activeModalWidget()
                if isinstance(dialog, QDialog):
                    dialog.accept()
            QTimer.singleShot(50, accept_delivery)
            desktop.panel.confirm_delivery(project_id)
        until(lambda: service.get_project(project_id)['status'] == '已交付')
        until(lambda: rows[0]['matStatus'] == '已完成')
        until(lambda: tuple(task.id for task in page._own_tasks()) == ('202',))
        until(lambda: page_script(site_page, """
            document.querySelector('.is-completed-project input.mat-check[value="55"]')?.dataset.status === '已完成'
        """))
        until(lambda: not desktop.personal_site.projects.pending(client.current_personal_snapshot().account))
        assert writes(history) == [('/api/start-material', {'ids': [55]}),
                                   ('/api/complete-material', {'ids': [55]})]
        assert service.db.delivery_history()['event_count'] == 1
        desktop.panel.intake.emit({'kind': 'files', 'paths': [str(path)]})
        until(lambda: not desktop.busy)
        assert service.get_project(project_id)['status'] == '已交付'
        assert len(service.list_projects()) == 2
        assert len(writes(history)) == 2
    finally:
        desktop.shutdown()


def test_supplemental_sync_submits_only_existing_delivery_bound_after_archive(site, tmp_path, monkeypatch):
    browser, _, _, _, rows, history, _ = site
    title = '匿名旧交付系统'
    rows[0].update(id=55, name=title)
    monkeypatch.setattr(application, 'LIVE_SITE_URL', browser.origin)
    service = DesktopService(tmp_path / 'data' / 'workbox.db')
    project_id, = service.receive_text(title)['project_ids']
    source = Path(service.workspaces.get_workspace(project_id)['path']) / (title + '.docx')
    make_document(source)
    original_bytes = source.read_bytes()
    original_delivery = service.confirm_delivery(project_id, confirmed=True)
    archive = Path(original_delivery['archive']['path']) / source.name
    event_id = original_delivery['event_id']
    original_events = service.db.delivery_events_for_project(project_id)
    original_time = service.get_project(project_id)['delivered_at']
    desktop = FloatingController(service, tray_enabled=False)
    confirm_timer = QTimer()
    confirmations = []

    def confirm_existing_delivery():
        dialog = QApplication.activeModalWidget()
        if isinstance(dialog, QMessageBox):
            confirm_timer.stop()
            confirmations.append((dialog.text(), dialog.standardButton(dialog.defaultButton())))
            dialog.button(QMessageBox.Yes).click()

    try:
        assert desktop.start()
        desktop.monitor_timer.stop()
        desktop.panel.nav_team.click()
        client = desktop.panel.team_controller
        until(lambda: not client._busy)
        desktop.panel.team_page._login.click()
        until(lambda: client.current_personal_snapshot() is not None, seconds=22)
        until(lambda: service.get_project(project_id)['original_task_id'] == '55' and not desktop.busy)
        assert not writes(history)
        assert desktop.personal_site.projects.delivery_for_event(event_id) is None

        desktop.panel.nav_projects.click()
        menu = desktop.panel.project_context_menu(project_id)
        action, = [item for item in menu.actions() if item.text() == '补同步到这是家…']
        confirm_timer.timeout.connect(confirm_existing_delivery)
        confirm_timer.start(30)
        action.trigger()
        until(lambda: rows[0]['matStatus'] == '已完成')
        until(lambda: desktop.personal_site.projects.delivery_for_event(event_id).status == 'completed')

        (message, default), = confirmations
        assert f'这是家 #55 · {title}' in message and default == QMessageBox.No
        assert writes(history) == [('/api/start-material', {'ids': [55]}),
                                   ('/api/complete-material', {'ids': [55]})]
        assert rows[1]['matStatus'] == '待制作'
        assert service.db.delivery_events_for_project(project_id) == original_events
        project = service.get_project(project_id)
        assert project['status'] == '已交付' and project['delivered_at'] == original_time
        assert archive.read_bytes() == source.read_bytes() == original_bytes
    finally:
        confirm_timer.stop()
        desktop.shutdown()


@pytest.mark.parametrize('versioned', [False, True], ids=['exact', 'versionless-word'])
def test_home_drop_with_two_unlinked_namesakes_uses_the_chosen_site_number(site, tmp_path, monkeypatch, versioned):
    browser, _, _, _, rows, history, _ = site
    monkeypatch.setattr(application, 'LIVE_SITE_URL', browser.origin)
    service = DesktopService(tmp_path / 'data' / 'workbox.db')
    desktop = FloatingController(service, tray_enabled=False)
    choices = []

    def select(_parent, title, _prompt, items, *_):
        choices.append(title)
        if '新建独立本机项目' in items:
            return '新建独立本机项目', True
        return next(item for item in items if '#202' in item), True

    monkeypatch.setattr(QInputDialog, 'getItem', select)
    try:
        title = rows[0]['name']
        if versioned:
            for row in rows[:2]:
                row['name'] = title + 'V1.0'
        assert desktop.start()
        desktop.monitor_timer.stop()
        desktop.panel.nav_team.click()
        page = desktop.panel.team_page
        until(lambda: not page.busy)
        page._login.click()
        until(lambda: len(desktop.personal_site._unlinked) == 2, seconds=25)
        path = tmp_path / (title + '.docx')
        make_document(path)
        desktop.panel.nav_projects.click()
        desktop.panel.intake.emit({'kind': 'files', 'paths': [str(path)]})
        until(lambda: not desktop.busy and bool(service.list_projects()))
        project, = service.list_projects()
        assert project['original_task_id'] == '202' and project['status'] == '待交付'
        assert choices == ['核对交件项目', '关联本机项目']
        assert not writes(history)
    finally:
        desktop.shutdown()


def test_home_drop_checks_versioned_binding_from_a_previous_account(site, tmp_path, monkeypatch):
    browser, _, _, _, rows, history, _ = site
    rows[0]['name'], rows[1]['name'] = '当前账号匿名任务', '当前账号其他任务'
    monkeypatch.setattr(application, 'LIVE_SITE_URL', browser.origin)
    service = DesktopService(tmp_path / 'data' / 'workbox.db')
    desktop = FloatingController(service, tray_enabled=False)
    previous = Account('previous', 'Previous', 'material', 'previous-session', 'previous')
    task = Task('55', '匿名其他账号系统V1.0', previous.id, Stage.READY, 'r1')
    service.sync_personal_site_snapshot(Snapshot(previous, (task,)))
    project_id = next(row['id'] for row in service.list_projects() if row['original_task_id'] == '55')
    path = tmp_path / '匿名其他账号系统.docx'
    make_document(path)
    choices = []
    try:
        assert desktop.start()
        desktop.monitor_timer.stop()
        monkeypatch.setattr(desktop.panel, 'choose_file_project', choices.append)
        desktop.panel.nav_team.click()
        page = desktop.panel.team_page
        until(lambda: not page.busy)
        page._login.click()
        until(lambda: len(service.list_projects()) == 3 and not desktop.busy, seconds=25)
        assert desktop.panel.team_controller.current_personal_snapshot().account.username == 'fixture'
        desktop.panel.nav_projects.click()
        desktop.panel.intake.emit({'kind': 'files', 'paths': [str(path)]})
        until(lambda: not desktop.busy)
        assert choices == [path.name]
        assert service.get_project(project_id)['status'] == '待开发'
        assert not writes(history)
    finally:
        desktop.shutdown()
