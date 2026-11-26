"""Import selected original-site task identities into isolated personal workspaces."""

from concurrent.futures import CancelledError, ThreadPoolExecutor
import os
from pathlib import Path
import threading

import pytest

from workbox.desktop.desktop_service import DesktopService


@pytest.fixture
def service(tmp_path):
    return DesktopService(tmp_path / 'data' / 'workbox.db')


def test_new_and_existing_projects_keep_their_personal_states_and_workspaces(service):
    original = service.receive_text('1. 匿名ＡＩ资料系统')['project_ids'][0]
    service.bulk_update_projects([original], status='开发中', urgent=True)
    old_workspace = service.workspaces.get_workspace(original)

    result = service.sync_site_tasks([('101', '匿名ai资料系统'), ('102', '新示例，保留标点')])

    new, = result['created_project_ids']
    assert result['linked_project_ids'] == [original]
    assert result['unchanged_project_ids'] == result['conflicts'] == []
    assert service.workspaces.get_workspace(original) == old_workspace
    assert service.get_project(original)['status'] == '开发中'
    assert service.get_project(original)['urgent']
    assert service.get_project(new)['status'] == '待开发'
    assert service.get_project(new)['name'] == '新示例，保留标点'
    assert Path(service.workspaces.get_workspace(new)['path']).is_dir()
    assert service.get_project(new)['project_type_id'] == service.get_default_project_type()

    service.set_status(new, '待交付')
    before = service.db.counts()
    restarted = DesktopService(service.db.path)
    repeated = restarted.sync_site_tasks([('101', '异动后的上游标题'), ('102', '另一个标题')])
    assert repeated['unchanged_project_ids'] == [original, new]
    assert repeated['created_project_ids'] == repeated['linked_project_ids'] == repeated['conflicts'] == []
    assert service.get_project(new)['name'] == '新示例，保留标点'
    assert service.get_project(new)['status'] == '待交付'
    assert service.db.counts() == before


@pytest.mark.parametrize('boundary', ['archived', 'deleted', 'multiple'])
def test_name_collision_is_reported_with_task_identity_without_blocking_unrelated_tasks(service, boundary):
    title = '待核对的匿名系统'
    pid = service.receive_text('1. ' + title)['project_ids'][0]
    if boundary == 'archived':
        service.bulk_update_projects([pid], archived=True)
    elif boundary == 'deleted':
        service.delete_project(pid, confirmed=True)
    else:
        service.capture_message('1. ' + title, '已存在另一同名项目')
    count = service.db.counts()['projects']

    result = service.sync_site_tasks([('201', title), ('202', '独立示例系统')])

    conflict, = result['conflicts']
    assert conflict['task_id'] == '201' and conflict['title'] == title and conflict['reason']
    assert not result['linked_project_ids']
    assert len(result['created_project_ids']) == 1
    assert service.db.counts()['projects'] == count + 1


def test_distinct_source_tasks_are_never_combined_by_name(service):
    title = '匿名同名系统'
    ambiguous = service.sync_site_tasks([('301', title), ('302', title)])
    assert [item['task_id'] for item in ambiguous['conflicts']] == ['301', '302']
    assert service.db.counts()['projects'] == 0

    accepted = service.sync_site_tasks([('301', title), ('301', title)])
    assert len(accepted['created_project_ids']) == 1
    blocked = service.sync_site_tasks([('301', title), ('302', title)])
    assert blocked['conflicts'][0]['task_id'] == '302'
    assert not blocked['linked_project_ids']
    assert blocked['unchanged_project_ids'] == accepted['created_project_ids']
    assert service.db.counts()['projects'] == 1


def test_one_source_read_registers_independent_working_folders(service):
    first, second = service.sync_site_tasks(
        [('303', '匿名一号系统'), ('304', '匿名二号系统')])['created_project_ids']
    first_workspace = service.workspaces.get_workspace(first)
    second_workspace = service.workspaces.get_workspace(second)
    assert first_workspace['batch_id'] != second_workspace['batch_id']
    assert Path(first_workspace['path']).parent == Path(second_workspace['path']).parent
    assert first_workspace['group_path'] == first_workspace['path']
    assert second_workspace['group_path'] == second_workspace['path']


def test_resync_never_revives_a_deleted_source_link(service):
    pid, = service.sync_site_tasks([('401', '匿名待办系统')])['created_project_ids']
    service.delete_project(pid, confirmed=True)

    result = service.sync_site_tasks([('401', '原站更名的系统')])

    assert result['conflicts'][0]['task_id'] == '401'
    assert not result['created_project_ids']
    assert service.list_projects() == []
    assert [project['id'] for project in service.list_projects(deleted=True)] == [pid]


@pytest.mark.skipif(os.name != 'nt', reason='Directory leases require Windows')
def test_source_identity_follows_explicit_project_merge(service):
    old, = service.sync_site_tasks([('501', '旧匿名待办系统')])['created_project_ids']
    new = service.receive_text('1. 保留匿名项目系统')['project_ids'][0]
    preview = service.preview_project_rename(old, '保留匿名项目系统', new)
    service.rename_project(old, '保留匿名项目系统', merge_target_id=new,
                           expected_token=preview['token'], confirmed_merge=True)

    result = service.sync_site_tasks([('501', '旧匿名待办系统')])

    assert result['unchanged_project_ids'] == [new]
    assert result['conflicts'] == []
    assert [row['id'] for row in service.list_projects()] == [new]


def test_two_workers_import_one_source_identity_once(service):
    tasks = [('601', '匿名并发同步系统')]
    barrier = threading.Barrier(2)

    def sync():
        barrier.wait(timeout=5)
        return service.sync_site_tasks(tasks)

    with ThreadPoolExecutor(2) as pool:
        jobs = [pool.submit(sync) for _ in range(2)]
        outcomes = [job.result(timeout=8) for job in jobs]

    pid, = [item for outcome in outcomes for item in outcome['created_project_ids']]
    assert [item for outcome in outcomes for item in outcome['unchanged_project_ids']] == [pid]
    assert service.db.counts()['projects'] == 1


def test_workspace_retry_recovers_file_folder_without_duplicate_project(service, monkeypatch):
    tasks = [('701', '匿名目录重试系统')]

    def fail_workspace(*args):
        raise OSError('匿名目录暂时不可用')

    with monkeypatch.context() as patch:
        patch.setattr(service.workspaces, 'ensure_projects', fail_workspace)
        result = service.sync_site_tasks(tasks)
        pid, = result['created_project_ids']
        assert '匿名目录暂时不可用' in result['workspace_error']
    assert service.workspaces.get_workspace(pid) is None

    repeated = service.sync_site_tasks(tasks)
    assert repeated['unchanged_project_ids'] == [pid]
    assert 'workspace_error' not in repeated
    assert Path(service.workspaces.get_workspace(pid)['path']).is_dir()
    assert service.db.counts()['projects'] == 1


def test_cancellation_leaves_no_personal_project(service):
    cancel_event = threading.Event()
    cancel_event.set()
    with pytest.raises(CancelledError):
        service.sync_site_tasks([('801', '匿名取消系统')], cancel_event=cancel_event)
    assert service.db.counts()['projects'] == 0


def test_large_task_list_has_one_import_and_bounded_workspace_calls(service, monkeypatch):
    workspace_calls = []

    def record_workspace_call(project_ids):
        workspace_calls.append(list(project_ids))
        return []

    monkeypatch.setattr(service.workspaces, 'ensure_projects', record_workspace_call)
    tasks = [(str(number), f'匿名待办 {number}') for number in range(1, 504)]
    result = service.sync_site_tasks(tasks)

    assert result['conflicts'] == []
    assert len(result['created_project_ids']) == service.db.counts()['projects'] == 503
    assert [len(ids) for ids in workspace_calls] == [500, 3]
    assert [pid for ids in workspace_calls for pid in ids] == result['created_project_ids']
