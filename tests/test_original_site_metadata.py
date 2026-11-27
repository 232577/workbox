from dataclasses import replace
from pathlib import Path

from workbox.desktop.desktop_service import DesktopService
from workbox.projects.original_site import OriginalSiteProjects
from workbox.team_delivery.contracts import Account, Snapshot, Stage, Task


def test_original_batch_updates_existing_workspace_without_changing_local_identity(tmp_path):
    path = tmp_path / 'data' / 'anonymous.db'
    service = DesktopService(path)
    projects = OriginalSiteProjects(service.db, service.workspaces)
    account = Account('session', '匿名材料员', 'material', 'anonymous-browser', 'fixture-material')
    task = Task('55', '匿名项目', account.id, Stage.READY, 'revision-55')
    assert service.sync_personal_site_snapshot(Snapshot(account, (task,))) == 1
    project_id = projects.binding_for_task(account, task)
    before = service.get_project(project_id)
    assert before['original_batch_id'] == ''
    directory = Path(service.workspaces.get_workspace(project_id)['path'])
    (directory / '材料.txt').write_text('匿名夹具', encoding='utf-8')
    service.set_status(project_id, '待交付')
    event_id = service.confirm_delivery(project_id, confirmed=True)['event_id']

    # Simulate upgrading a database with a task binding and local delivery from an older release.
    with service.db.connect() as connection:
        names = {row['name'] for row in connection.execute('PRAGMA table_info(original_site_bindings)')}
        for name in ('source_batch_id', 'source_batch_name', 'source_urgency'):
            if name in names:
                connection.execute(f'ALTER TABLE original_site_bindings DROP COLUMN {name}')

    reopened = DesktopService(path)
    projects = OriginalSiteProjects(reopened.db, reopened.workspaces)
    assert projects.delivery_for_event(event_id).status == 'pending'
    renamed = replace(task, original_batch_id='batch-55', original_batch_name='匿名原站批次',
                      original_urgency='非常紧急')
    snapshot = Snapshot(account, (renamed,))
    assert reopened.sync_personal_site_snapshot(snapshot) == 1
    assert reopened.sync_personal_site_snapshot(snapshot) == 0
    assert projects.binding_for_task(account, renamed) == project_id
    project = reopened.get_project(project_id)
    (row,) = reopened.project_workbench_snapshot()['projects']
    for result in (project, row):
        assert (result['original_batch_id'], result['original_batch_name'], result['original_urgency']) == (
            'batch-55', '匿名原站批次', '非常紧急')
        assert result['batch_id'] == before['batch_id']
        assert result['batch_name'] == before['batch_name']
        assert result['urgent'] == before['urgent']
        assert result['status'] == '已交付'
    assert projects.delivery_for_event(event_id).status == 'pending'
    assert Path(reopened.workspaces.get_workspace(project_id)['path']) == directory
    assert reopened.db.delivery_history()['event_count'] == 1
