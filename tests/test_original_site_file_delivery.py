from pathlib import Path
from zipfile import ZipFile

import pytest

from workbox.desktop.desktop_service import DesktopService
from workbox.team_delivery.contracts import Account, Stage, Task


ACCOUNT = Account('anonymous-session', '匿名账号', 'material', 'anonymous-token', 'fixture')


@pytest.fixture
def service(tmp_path):
    return DesktopService(tmp_path / 'data' / 'anonymous.db')


@pytest.fixture
def site_projects(service):
    from workbox.projects.original_site import OriginalSiteProjects

    return OriginalSiteProjects(service.db, service.workspaces)


@pytest.fixture
def bind_site_project(site_projects):
    def bind(name, project_id=None):
        task = Task('55', name, ACCOUNT.id, Stage.READY, 'fixture-revision')
        if project_id is None:
            site_projects.sync(ACCOUNT, [task])
        else:
            site_projects.bind_existing(ACCOUNT, task, project_id, confirmed=True)
        return site_projects.binding_for_task(ACCOUNT, task)

    return bind


def test_bound_project_file_reuse_preserves_delivery_and_new_revision_returns_pending(
    service, tmp_path, bind_site_project, site_projects,
):
    pid = bind_site_project('匿名甲项目')
    source = tmp_path / '匿名甲项目.txt'
    source.write_bytes(b'first revision')

    first = service.receive_path(source)
    assert not first['duplicate']
    assert service.get_project(pid)['status'] == '待交付'
    assert not site_projects.pending(ACCOUNT)
    delivered = service.confirm_delivery(pid, confirmed=True)
    committed = service.get_project(pid)
    action, = site_projects.pending(ACCOUNT)
    assert (action.event_id, action.project_id, action.task_id) == (delivered['event_id'], pid, '55')

    again = service.receive_path(source)
    assert again['duplicate']
    assert again['file']['id'] == first['file']['id']
    assert service.get_project(pid) == committed
    assert service.confirm_delivery(pid, confirmed=True)['event_id'] == delivered['event_id']

    source.write_bytes(b'new revision')
    revised = service.receive_path(source)
    assert not revised['duplicate']
    assert Path(revised['file']['path']).read_bytes() == b'new revision'
    assert service.get_project(pid)['status'] == '待交付'
    assert len(service.db.delivery_events_for_project(pid)) == 1
    assert not site_projects.pending(ACCOUNT)

    delivered_revision = service.confirm_delivery(pid, confirmed=True)
    replacement, = site_projects.pending(ACCOUNT)
    assert replacement.event_id == delivered_revision['event_id'] != action.event_id


def test_bound_folder_intake_reports_per_file_failure_and_preserves_duplicate_delivery(
    service, tmp_path, bind_site_project, monkeypatch,
):
    pid = bind_site_project('匿名甲项目')
    folder = tmp_path / '匿名甲项目'
    folder.mkdir()
    (folder / '甲.txt').write_bytes(b'original A')
    (folder / '乙.txt').write_bytes(b'original B')
    result = service.receive_path(folder)
    assert (result['copied'], result['failed']) == (2, 0)
    service.confirm_delivery(pid, confirmed=True)
    committed = service.get_project(pid)

    rejected = folder / '拒收.txt'
    rejected.write_bytes(b'new but unreadable')
    original_import = service.workspaces.import_file

    def fail_rejected(source, *args, **kwargs):
        if Path(source).name == rejected.name:
            raise OSError('isolated copy failure')
        return original_import(source, *args, **kwargs)

    monkeypatch.setattr(service.workspaces, 'import_file', fail_rejected)
    again = service.receive_path(folder)
    assert (again['duplicate'], again['failed']) == (2, 1)
    assert service.get_project(pid) == committed

    (folder / '甲.txt').write_bytes(b'revised A')
    updated = service.receive_path(folder)
    assert (updated['copied'], updated['duplicate'], updated['failed']) == (1, 1, 1)
    assert service.get_project(pid)['status'] == '待交付'


def test_unmatched_file_does_not_change_a_bound_delivered_project(service, tmp_path, bind_site_project):
    pid = bind_site_project('匿名甲项目')
    source = tmp_path / '匿名甲项目.txt'
    source.write_bytes(b'file')
    service.receive_path(source)
    service.confirm_delivery(pid, confirmed=True)
    committed = service.get_project(pid)
    settings = service.workspaces.settings()
    service.workspaces.configure(settings['work_root'], settings['archive_root'], auto_create_projects=False)
    unmatched = tmp_path / '无关联的内容.md'
    unmatched.write_bytes(b'unmatched')

    result = service.receive_path(unmatched)

    assert result['intake_choice'] == str(unmatched)
    assert service.get_project(pid) == committed


def test_bound_zip_drop_waits_for_manual_delivery_and_unchanged_zip_does_not_reset_it(
    service, tmp_path, bind_site_project, site_projects,
):
    pid = bind_site_project('匿名甲项目')
    source = tmp_path / '匿名甲项目.zip'
    with ZipFile(source, 'w') as archive:
        for index in range(6):
            archive.writestr(f'isolated-{index}.txt', f'anonymous content {index}')
    service.receive_path(source)
    service.scan_archives(now=0)
    service.scan_archives(now=11)

    assert service.get_project(pid)['status'] == '待交付'
    assert service.db.delivery_events_for_project(pid) == []
    assert not site_projects.pending(ACCOUNT)
    record, = service.list_zips()
    delivered = service.confirm_delivery(pid, record['id'], confirmed=True)
    service.receive_path(source)
    repeated = service.scan_archives(now=22)

    assert repeated['errors'] == []
    assert [item['id'] for item in repeated['archived']] == [record['id']]
    assert service.get_project(pid) == delivered
    event, = service.db.delivery_events_for_project(pid)
    action, = site_projects.pending(ACCOUNT)
    assert event['kind'] == 'manual-zip'
    assert action.event_id == event['id']

    with ZipFile(source, 'a') as archive:
        archive.writestr('new-revision.txt', 'revised contents')
    service.receive_path(source)
    service.scan_archives(now=44)
    service.scan_archives(now=55)
    assert service.get_project(pid)['status'] == '待交付'
    assert service.db.delivery_events_for_project(pid) == [event]
    assert not site_projects.pending(ACCOUNT)


def test_mixed_zip_applies_local_automatic_policy_only_to_unbound_project(service, tmp_path, bind_site_project):
    names = ('匿名甲项目', '匿名乙项目')
    capture = service.db.create_capture('\n'.join(names), '匿名批次', [(name, False) for name in names])
    bound, unbound = capture['project_ids']
    assert bind_site_project('匿名甲项目', bound) == bound
    source = tmp_path / '匿名甲项目-2.zip'
    with ZipFile(source, 'w') as archive:
        for project in names:
            for index in range(4):
                archive.writestr(f'{project}/fixture-{index}.txt', 'anonymous content')

    service.receive_path(source)
    service.scan_archives(now=0)
    archived = service.scan_archives(now=11)

    assert archived['errors'] == []
    record, = archived['archived']
    facts = service.db.get_zip_file_counts(record['id'])
    assert [(fact['file_count'], fact['decided_status'], fact['auto_deliver_zip']) for fact in facts] == [
        (4, '待交付', False), (4, '已交付', True),
    ]
    assert service.get_project(bound)['status'] == '待交付'
    assert service.db.delivery_events_for_project(bound) == []
    assert service.get_project(unbound)['status'] == '已交付'
    assert [event['kind'] for event in service.db.delivery_events_for_project(unbound)] == ['auto-zip']


def test_batch_confirmation_commits_site_action_with_local_history_and_rolls_both_back_on_failure(
    service, tmp_path, bind_site_project, site_projects, monkeypatch,
):
    bound = bind_site_project('匿名甲项目')
    unbound, = service.capture_message('匿名乙项目', '匿名批次')['project_ids']
    for name in ('匿名甲项目', '匿名乙项目'):
        source = tmp_path / f'{name}.txt'
        source.write_text('匿名材料', encoding='utf-8')
        service.receive_path(source)

    original = service.db._complete_file_delivery

    def fail_second(connection, project_id, verified_archive):
        event_id = original(connection, project_id, verified_archive)
        if project_id == unbound:
            raise RuntimeError('isolated batch failure')
        return event_id

    with monkeypatch.context() as patch:
        patch.setattr(service.db, '_complete_file_delivery', fail_second)
        with pytest.raises(RuntimeError, match='isolated batch failure'):
            service.confirm_project_deliveries([bound, unbound], confirmed=True)

    assert not site_projects.pending(ACCOUNT)
    assert service.db.delivery_events_for_project(bound) == []
    assert [service.get_project(pid)['status'] for pid in (bound, unbound)] == ['待交付', '待交付']
    result = service.confirm_project_deliveries([bound, unbound], confirmed=True)
    action, = site_projects.pending(ACCOUNT)
    assert action.event_id == result['event_ids'][0]
    assert site_projects.delivery_for_event(result['event_ids'][1]) is None
    assert [service.get_project(pid)['status'] for pid in (bound, unbound)] == ['已交付', '已交付']
