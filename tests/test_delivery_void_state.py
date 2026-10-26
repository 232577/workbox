"""撤销误点交付时同步退回状态，全部材料都来自隔离目录。"""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from zipfile import ZipFile

import pytest

from database import Database
from delivery_archive import ProjectDeliveryArchive
from desktop_service import DesktopService
from snapshot_manager import SnapshotManager
from workspace_manager import WorkspaceManager


@pytest.fixture
def context(tmp_path):
    service = DesktopService(tmp_path / 'data' / 'workbox.db')
    ids = service.db.create_capture('甲系统\n乙系统', '隔离交付批', [('甲系统', False), ('乙系统', False)])['project_ids']
    workspaces = WorkspaceManager(service)
    workspaces.ensure_projects(ids)
    return service, workspaces, ids


def delivered_zip(context, tmp_path, pid, name='甲系统.zip'):
    service, workspaces, _ = context
    source = tmp_path / name
    with ZipFile(source, 'w') as archive:
        for index in range(4):
            archive.writestr(f'材料/{index}.txt', f'隔离材料 {index}')
    manager = SnapshotManager(service, workspaces)
    record = manager.archive_now(source, pid)['zip']
    return source, record, manager


def history_rows(db, table):
    with db.connect() as connection:
        return [dict(row) for row in connection.execute(f'SELECT * FROM {table} ORDER BY rowid')]


def assert_pending(project):
    assert project['status'] == '待交付'
    assert project['delivered_at'] is None
    assert project['delivered_zip_id'] is None


def test_void_zip_resets_current_delivery_and_keeps_original_archive_approval_and_details(context, tmp_path):
    service, workspaces, ids = context
    pid = ids[0]
    source, record, manager = delivered_zip(context, tmp_path, pid)
    manager.approve_version(record['id'])
    service.db.update_project_details(pid, '保留备注', '2026-11-01')
    service.db.set_urgent(pid, True)
    before = service.db.get_project(pid)
    events = history_rows(service.db, 'delivery_events')
    snapshots = history_rows(service.db, 'zip_snapshots')
    original_bytes = source.read_bytes()

    result = service.db.void_delivery_events([events[0]['id']], confirmed=True)

    assert_pending(service.db.get_project(pid))
    assert result['changed_event_ids'] == [events[0]['id']]
    assert result['reset_project_ids'] == [pid]
    current = service.db.get_project(pid)
    for field in ('name', 'batch_id', 'urgent', 'notes', 'due_date', 'archived_at', 'created_at'):
        assert current[field] == before[field]
    assert service.db.approved_zip_for_project(pid) == record['id']
    assert source.read_bytes() == Path(record['path']).read_bytes() == original_bytes
    assert history_rows(service.db, 'delivery_events') == events
    assert history_rows(service.db, 'zip_snapshots') == snapshots
    assert_pending(Database(service.db.path).get_project(pid))


def test_void_manual_materials_keeps_work_files_archive_and_original_event(context):
    service, workspaces, ids = context
    pid = ids[0]
    root = Path(workspaces.get_workspace(pid)['path'])
    source = root / '操作手册.docx'
    source.write_bytes(b'isolated document material')
    service.set_status(pid, '待交付')
    delivered = ProjectDeliveryArchive(service, workspaces).confirm(pid, confirmed=True)
    events = history_rows(service.db, 'delivery_events')
    archives = history_rows(service.db, 'project_delivery_archives')

    result = service.db.void_delivery_events([delivered['event_id']], confirmed=True)

    assert_pending(service.db.get_project(pid))
    assert result['reset_project_ids'] == [pid]
    assert source.read_bytes() == (Path(delivered['archive']['path']) / source.name).read_bytes()
    assert history_rows(service.db, 'delivery_events') == events
    assert history_rows(service.db, 'project_delivery_archives') == archives


def test_cancel_is_read_only_and_restore_only_restores_statistics(context, tmp_path):
    service, _, ids = context
    _, _, _ = delivered_zip(context, tmp_path, ids[0])
    event_id = history_rows(service.db, 'delivery_events')[0]['id']
    before = service.db.get_project(ids[0])
    result = service.db.void_delivery_events([event_id], confirmed=False)
    assert result['reset_project_ids'] == []
    assert result['changed_count'] == 0
    assert service.db.get_project(ids[0]) == before
    assert history_rows(service.db, 'delivery_event_actions') == []
    service.db.void_delivery_events([event_id], confirmed=True)
    pending = service.db.get_project(ids[0])
    result = service.db.restore_delivery_events([event_id], confirmed=True)
    assert result['reset_project_ids'] == []
    assert result['changed_event_ids'] == [event_id]
    assert service.db.get_project(ids[0]) == pending
    assert_pending(pending)
    assert service.db.delivery_history()['project_count'] == 1


def test_void_after_statistic_restore_skips_already_pending_project_update(context, tmp_path):
    service, _, ids = context
    delivered_zip(context, tmp_path, ids[0])
    event_id = history_rows(service.db, 'delivery_events')[0]['id']
    service.db.void_delivery_events([event_id], confirmed=True)
    service.db.restore_delivery_events([event_id], confirmed=True)
    before = service.db.get_project(ids[0])
    assert_pending(before)
    result = service.db.void_delivery_events([event_id], confirmed=True)
    assert result['changed_event_ids'] == [event_id]
    assert result['reset_project_ids'] == []
    assert service.db.get_project(ids[0]) == before


def test_repeating_void_does_not_undo_a_later_real_delivery(context, tmp_path):
    service, _, ids = context
    _, record, _ = delivered_zip(context, tmp_path, ids[0])
    event_id = history_rows(service.db, 'delivery_events')[0]['id']
    service.db.void_delivery_events([event_id], confirmed=True)
    service.confirm_delivery(ids[0], record['id'], confirmed=True)
    current = service.db.get_project(ids[0])

    result = service.db.void_delivery_events([event_id], confirmed=True)

    assert result['changed_count'] == 0
    assert result['reset_project_ids'] == []
    assert service.db.get_project(ids[0]) == current
    assert current['status'] == '已交付'
    assert len(history_rows(service.db, 'delivery_event_actions')) == 1
    assert service.db.delivery_history()['project_count'] == 1


def test_batch_void_resets_only_changed_events_projects_once_and_keeps_archived_flag(context, tmp_path):
    service, _, ids = context
    _, first, _ = delivered_zip(context, tmp_path, ids[0])
    old_event = history_rows(service.db, 'delivery_events')[0]['id']
    service.db.void_delivery_events([old_event], confirmed=True)
    service.confirm_delivery(ids[0], first['id'], confirmed=True)
    service.set_status(ids[0], '待交付')
    service.confirm_delivery(ids[0], first['id'], confirmed=True)
    _, _, _ = delivered_zip(context, tmp_path, ids[1], '乙系统.zip')
    service.db.bulk_update_projects([ids[1]], archived=True)
    archived_at = service.db.get_project(ids[1])['archived_at']
    events = history_rows(service.db, 'delivery_events')

    result = service.db.void_delivery_events([row['id'] for row in reversed(events)] * 2, confirmed=True)

    assert result['changed_event_ids'] == [row['id'] for row in events[1:]]
    assert result['reset_project_ids'] == ids
    for pid in ids:
        assert_pending(service.db.get_project(pid))
    assert service.db.get_project(ids[1])['archived_at'] == archived_at
    assert history_rows(service.db, 'delivery_events') == events


@pytest.mark.parametrize('failed_table', ['projects', 'delivery_event_actions'])
def test_batch_failure_rolls_back_both_project_states_and_correction_log(context, tmp_path, failed_table):
    service, _, ids = context
    delivered_zip(context, tmp_path, ids[0])
    delivered_zip(context, tmp_path, ids[1], '乙系统.zip')
    before = [service.db.get_project(pid) for pid in ids]
    event_ids = [row['id'] for row in history_rows(service.db, 'delivery_events')]
    with service.db.connect() as connection:
        operation = 'UPDATE' if failed_table == 'projects' else 'INSERT'
        condition = f'NEW.id={ids[1]}' if failed_table == 'projects' else f'NEW.event_id={event_ids[1]}'
        connection.execute(f'''CREATE TRIGGER fail_void BEFORE {operation} ON {failed_table}
            WHEN {condition} BEGIN SELECT RAISE(ABORT,'isolated rollback check'); END''')

    with pytest.raises(sqlite3.DatabaseError, match='isolated rollback check'):
        service.db.void_delivery_events(event_ids, confirmed=True)

    assert [service.db.get_project(pid) for pid in ids] == before
    assert history_rows(service.db, 'delivery_event_actions') == []
    assert service.db.delivery_history()['project_count'] == 2


def test_concurrent_void_resets_once_and_appends_only_one_action(context, tmp_path):
    service, _, ids = context
    delivered_zip(context, tmp_path, ids[0])
    event_id = history_rows(service.db, 'delivery_events')[0]['id']
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: service.db.void_delivery_events([event_id], confirmed=True), range(8)))
    assert sum(result['changed_count'] for result in results) == 1
    assert [pid for result in results for pid in result['reset_project_ids']] == [ids[0]]
    assert_pending(service.db.get_project(ids[0]))
    assert len(history_rows(service.db, 'delivery_event_actions')) == 1


def test_unchanged_background_scans_and_restarts_keep_revoked_zip_pending(context, tmp_path):
    service, workspaces, ids = context
    source = tmp_path / '甲系统.zip'
    with ZipFile(source, 'w') as archive:
        for index in range(4):
            archive.writestr(f'{index}.txt', 'isolated')
    manager = SnapshotManager(service, workspaces)
    manager.watch_zip(source, ids[0])
    manager.scan(now=0)
    assert len(manager.scan(now=11)['archived']) == 1
    event_id = history_rows(service.db, 'delivery_events')[0]['id']
    service.db.void_delivery_events([event_id], confirmed=True)
    assert manager.scan(now=22)['archived'] == []
    assert_pending(service.db.get_project(ids[0]))
    reopened = DesktopService(service.db.path)
    restarted = SnapshotManager(reopened, WorkspaceManager(reopened))
    restarted.scan(now=33)
    assert restarted.scan(now=44)['archived'] == []
    assert_pending(reopened.get_project(ids[0]))
    assert len(history_rows(reopened.db, 'delivery_events')) == 1
