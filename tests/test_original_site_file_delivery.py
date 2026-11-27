from pathlib import Path
from zipfile import ZipFile

import pytest

from workbox.desktop.desktop_service import DesktopService
from workbox.team_delivery.contracts import Account, Snapshot, Stage, Task
from workbox.team_delivery.personal_matching import matching_personal_word_tasks


ACCOUNT = Account('anonymous-session', '匿名账号', 'material', 'anonymous-token', 'fixture')


def test_regular_drop_after_upstream_rename_keeps_the_bound_local_project(service, site_projects, tmp_path):
    original = Task('55', '匿名旧名称系统', ACCOUNT.id, Stage.READY, 'r1')
    site_projects.sync(ACCOUNT, [original])
    project_id = site_projects.binding_for_task(ACCOUNT, original)
    renamed = Task('55', '匿名新名称系统', ACCOUNT.id, Stage.READY, 'r2')
    site_projects.sync(ACCOUNT, [renamed])
    document = tmp_path / (renamed.title + '.docx')
    document.write_bytes(b'anonymous work file')
    service.receive_path(document)
    assert service.get_project(project_id)['status'] == '待交付'
    assert service.get_project(project_id)['name'] == original.title
    assert len(service.list_projects()) == 1


@pytest.mark.parametrize('separator', ['\n', ' ', ''], ids=['separate-lines', 'same-line', 'touching-titles'])
def test_numbered_original_tasks_link_projects_from_a_pasted_list(service, site_projects, tmp_path, separator):
    title = '匿名交件系统'
    second_title = '匿名核对平台'
    project_id, second_id = service.receive_text(f'1. {title}{separator}2.{second_title}')['project_ids']
    original = Task('55', f'1. {title}', ACCOUNT.id, Stage.READY, 'r1')
    second = Task('56', f'2.{second_title}', ACCOUNT.id, Stage.READY, 'r2')
    site_projects.sync(ACCOUNT, [original, second])

    assert site_projects.binding_for_task(ACCOUNT, original) == project_id
    assert site_projects.binding_for_task(ACCOUNT, second) == second_id
    assert service.get_project(project_id)['name'] == title
    assert len(service.list_projects()) == 2

    document = tmp_path / (title + '.docx')
    document.write_bytes(b'anonymous document')
    service.receive_path(document)
    delivered = service.confirm_delivery(project_id, confirmed=True)
    action, = site_projects.pending(ACCOUNT)
    assert (action.event_id, action.task_id, action.title) == (delivered['event_id'], '55', original.title)
    assert site_projects.claim(action.event_id, ACCOUNT, original) is not None


@pytest.mark.parametrize(('title', 'filename'), [
    ('１．匿名交件系统V1.0', '匿名交件系统.docx'),
    ('匿名交件系统', '1. 匿名交件系统.docx'),
    ('1. 匿名交件系统', '1.匿名交件系统.docx'),
], ids=['number-in-task-and-optional-version', 'number-in-word', 'same-number-different-spacing'])
def test_word_routes_to_original_project_with_or_without_list_number(service, site_projects, tmp_path, title, filename):
    original = Task('55', title, ACCOUNT.id, Stage.READY, 'r1')
    site_projects.sync(ACCOUNT, [original])
    project_id = site_projects.binding_for_task(ACCOUNT, original)
    document = tmp_path / filename
    document.write_bytes(b'anonymous document')

    service.receive_path(document, personal_account=ACCOUNT.username)

    assert service.get_project(project_id)['status'] == '待交付'
    assert len(service.list_projects()) == 1


@pytest.mark.parametrize('separator', ['\n', ' '], ids=['separate-lines', 'same-line'])
def test_pasted_list_reuses_a_numbered_project_already_imported_from_the_site(service, site_projects, separator):
    first = Task('55', '1. 匿名交件系统', ACCOUNT.id, Stage.READY, 'r1')
    second = Task('56', '2.匿名核对平台', ACCOUNT.id, Stage.READY, 'r2')
    site_projects.sync(ACCOUNT, [first, second])
    first_id = site_projects.binding_for_task(ACCOUNT, first)
    second_id = site_projects.binding_for_task(ACCOUNT, second)

    result = service.receive_text(f'1.匿名交件系统{separator}2. 匿名核对平台')

    assert result['project_ids'] == result['reused_project_ids'] == [first_id, second_id]
    assert service.get_project(first_id)['name'] == first.title
    assert service.get_project(second_id)['name'] == second.title
    assert len(service.list_projects()) == 2


def test_unclear_inline_numbering_does_not_create_a_combined_project(service):
    result = service.receive_text('1. 匿名交件说明 2. 计划确认事项')

    assert result['state'] == 'needs-review'
    assert '编号' in result['reason']
    assert service.list_projects() == []


def test_decimal_version_is_kept_in_an_inline_numbered_list(service):
    result = service.receive_text('1. 匿名交件系统 V1.2 2.匿名核对平台')

    assert result['state'] == 'committed'
    assert {project['name'] for project in service.list_projects()} == {'匿名交件系统 V1.2', '匿名核对平台'}


def test_same_unnumbered_title_needs_choice_between_original_tasks(service, site_projects):
    title = '匿名交件系统'
    local_id, = service.receive_text(f'1. {title}')['project_ids']
    first = Task('55', f'1. {title}', ACCOUNT.id, Stage.READY, 'r1')
    second = Task('56', f'2. {title}', ACCOUNT.id, Stage.READY, 'r2')
    site_projects.sync(ACCOUNT, [first, second])

    assert site_projects.binding_for_task(ACCOUNT, first) is None
    assert site_projects.binding_for_task(ACCOUNT, second) is None
    assert len(service.list_projects()) == 1
    site_projects.bind_existing(ACCOUNT, second, local_id, confirmed=True)
    assert site_projects.binding_for_task(ACCOUNT, second) == local_id


@pytest.mark.parametrize(('suffix', 'reuse_local'), [('', False), ('（加急）', False), ('', True)],
                         ids=['numbered-receipt', 'numbered-receipt-with-urgency', 'numbered-receipt-reusing-local'])
def test_pasted_number_does_not_auto_bind_to_a_different_visible_number(
    service, site_projects, tmp_path, suffix, reuse_local,
):
    title = '匿名交件系统'
    existing_ids = service.receive_text(title)['project_ids'] if reuse_local else None
    local_id, = service.receive_text(f'2. {title}{suffix}')['project_ids']
    if reuse_local:
        assert existing_ids == [local_id]
    first = Task('55', f'1. {title}', ACCOUNT.id, Stage.READY, 'r1')
    intended = Task('56', f'2.{title}', ACCOUNT.id, Stage.READY, 'r2')

    site_projects.sync(ACCOUNT, [first])
    assert site_projects.binding_for_task(ACCOUNT, first) is None
    assert site_projects.unlinked_tasks(ACCOUNT, [first]) == (first,)
    assert len(service.list_projects()) == 1

    source = tmp_path / (title + '.docx')
    source.write_bytes(b'anonymous document')
    service.receive_path(source)
    event_id = service.confirm_delivery(local_id, confirmed=True)['event_id']
    assert not site_projects.pending(ACCOUNT)

    site_projects.sync(ACCOUNT, [intended])
    assert site_projects.binding_for_task(ACCOUNT, intended) == local_id
    assert site_projects.preview_delivery_sync(local_id, ACCOUNT, intended).event_id == event_id


def test_word_number_preserves_exact_task_choice_and_decimal_title():
    first = Task('55', '1. 匿名系统', ACCOUNT.id, Stage.READY, 'r1')
    second = Task('56', '2.匿名系统', ACCOUNT.id, Stage.READY, 'r2')
    another_owner = Task('57', '3.匿名系统', 'other', Stage.READY, 'r3')
    decimal = Task('58', '3.0匿名系统', ACCOUNT.id, Stage.READY, 'r4')
    snapshot = Snapshot(ACCOUNT, (first, second, another_owner, decimal))

    assert matching_personal_word_tasks('匿名系统.docx', snapshot) == (first, second)
    assert matching_personal_word_tasks('2.匿名系统.docx', snapshot) == (second,)
    assert matching_personal_word_tasks('3.匿名系统.docx', snapshot) == ()
    assert matching_personal_word_tasks('0匿名系统.docx', snapshot) == ()


def test_versionless_word_requires_selection_between_bound_versions(service, site_projects, tmp_path):
    older = Task('55', '匿名系统V1.0', ACCOUNT.id, Stage.READY, 'r1')
    newer = Task('56', '匿名系统 V2.0', ACCOUNT.id, Stage.READY, 'r2')
    site_projects.sync(ACCOUNT, [older, newer])
    project_id = site_projects.binding_for_task(ACCOUNT, older)
    source = Path(service.workspaces.get_workspace(project_id)['path']) / '匿名系统.docx'
    source.write_bytes(b'anonymous work file')

    result = service.receive_path(source)

    assert result['intake_choice'] == str(source)
    assert {row['original_task_id'] for row in result['routing']['candidates']} == {'55', '56'}
    assert all(row['status'] == '待开发' for row in service.list_projects())


@pytest.mark.parametrize('exact_exists', [False, True])
def test_wrong_explicit_word_version_cannot_automatically_join_renamed_bound_project(
    service, site_projects, bind_site_project, tmp_path, exact_exists,
):
    project_id = bind_site_project('匿名系统V1.0')
    plan = service.preview_project_rename(project_id, '匿名系统')
    service.rename_project(project_id, '匿名系统', expected_token=plan['token'])
    source_folder = tmp_path
    if exact_exists:
        exact = Task('56', '匿名系统 V2.0', ACCOUNT.id, Stage.READY, 'r2')
        site_projects.sync(ACCOUNT, [exact])
        exact_id = site_projects.binding_for_task(ACCOUNT, exact)
        preview = service.preview_project_rename(exact_id, '匿名其他名称')
        service.rename_project(exact_id, '匿名其他名称', expected_token=preview['token'])
        source_folder = Path(service.workspaces.get_workspace(project_id)['path'])
    source = source_folder / '匿名系统 V2.0.docx'
    source.write_bytes(b'anonymous work file')

    result = service.receive_path(source)

    assert result['intake_choice'] == str(source)
    assert service.get_project(project_id)['status'] == '待开发'


@pytest.mark.parametrize('drop_folder', [False, True])
def test_versionless_word_in_personal_mode_requires_selection_for_binding_from_other_account(
    service, bind_site_project, tmp_path, drop_folder,
):
    project_id = bind_site_project('匿名系统V1.0')
    folder = tmp_path / '收到的文件'
    folder.mkdir()
    source = folder / '匿名系统.docx'
    source.write_bytes(b'anonymous work file')

    result = service.receive_path(folder if drop_folder else source, personal_account='current')

    if drop_folder:
        assert result['needs_choice'] == 1
        assert result['items'][0]['retry_path'] == str(source)
    else:
        assert result['intake_choice'] == str(source)
    assert service.get_project(project_id)['status'] == '待开发'


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
