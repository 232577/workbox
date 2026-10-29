"""可恢复项目删除只改变可见性，保留项目与交付事实。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from dataclasses import asdict
from pathlib import Path
import sqlite3
from zipfile import ZipFile

import pytest

from database import Database
from zip_manager import inspect_zip


@pytest.fixture
def db(tmp_path):
    value = Database(tmp_path / 'workbox.db')
    value.create_capture('甲系统\n乙系统\n丙系统', '隔离批次',
                         [('甲系统', True), ('乙系统', False), ('丙系统', False)])
    return value


@pytest.mark.parametrize('previously_archived', [False, True])
def test_delete_restore_preserves_project_flags_and_original_archive(db, previously_archived):
    db.update_project_details(1, '原始备注', '2026-10-26')
    db.set_status(1, '开发中')
    if previously_archived:
        db.bulk_update_projects([1], archived=True)
    original = db.get_project(1)
    deleted = db.delete_project(1, confirmed=True)
    assert deleted == dict(project_id=1, deleted=True, changed=True, archived=True)
    row = db.get_project(1)
    assert row['deleted_at'] and row['archived_at']
    for key in ('status', 'urgent', 'notes', 'due_date', 'raw_text', 'updated_at'):
        assert row[key] == original[key]
    assert db.delete_project(1, confirmed=True) == dict(project_id=1, deleted=True, changed=False, archived=True)
    assert db.get_project(1)['deleted_at'] == row['deleted_at']
    assert Database(db.path).get_project(1)['deleted_at'] == row['deleted_at']
    assert db.restore_deleted_project(1) == dict(project_id=1, deleted=False, changed=True, archived=previously_archived)
    assert db.get_project(1) == original
    assert db.restore_deleted_project(1) == dict(project_id=1, deleted=False, changed=False, archived=previously_archived)


def test_deleted_filters_and_counts_keep_batch_zip_and_delivery_history(db, tmp_path):
    path = tmp_path / '甲系统.zip'
    with ZipFile(path, 'w') as archive:
        archive.writestr('说明.txt', '隔离成品')
    record, _ = db.register_zip(asdict(inspect_zip(path)), 1)
    db.complete_delivery(1, record['id'], asdict(inspect_zip(path)))
    db.set_prior_delivery_projects([1], effective_day=date(2026, 1, 1), confirmed=True)
    original = db.get_project(1)
    history = db.delivery_history()
    summary = db.today_delivery_summary()
    with db.connect() as connection:
        original_events = [dict(row) for row in connection.execute('SELECT * FROM delivery_events ORDER BY id')]
    batches = db.list_batches()
    disk = path.read_bytes()
    sentinel = tmp_path / 'workspace-state.json'
    sentinel.write_bytes(b'{"registered":"keep"}')
    db.bulk_update_projects([2], archived=True)
    db.delete_project(1, confirmed=True)
    assert [row['id'] for row in db.list_projects()] == [3]
    assert [row['id'] for row in db.list_projects(archived=True)] == [2]
    assert {row['id'] for row in db.list_projects(archived=None)} == {2, 3}
    for archived in (False, True, None):
        assert [row['id'] for row in db.list_projects(deleted=True, archived=archived)] == [1]
    assert {row['id'] for row in db.list_projects(deleted=None, archived=None)} == {1, 2, 3}
    assert [row['id'] for row in db.list_projects(deleted=True, query='甲系统', status='已交付',
                                                urgent_only=True, batch_id=1)] == [1]
    assert db.list_projects(deleted=True, query='不存在') == []
    assert db.counts() == dict(projects=2, batches=1, zips=1)
    assert db.list_batches() == batches
    # 归档/删除是新增的当前项目展示元数据；原始交付事实和所有计数必须不变。
    expected_history = history | {'events': [dict(row, project_archived=True, project_deleted=True)
        if row['project_id'] == 1 else row for row in history['events']]}
    expected_summary = summary | {'projects': [dict(row, project_archived=True, project_deleted=True)
        if row['project_id'] == 1 else row for row in summary['projects']]}
    assert db.delivery_history() == expected_history
    assert db.today_delivery_summary() == expected_summary
    with db.connect() as connection:
        assert [dict(row) for row in connection.execute('SELECT * FROM delivery_events ORDER BY id')] == original_events
    assert db.get_project(1)['zips'] == original['zips']
    assert path.read_bytes() == disk
    assert sentinel.read_bytes() == b'{"registered":"keep"}'
    db.restore_deleted_project(1)
    assert db.get_project(1) == original
    assert db.counts() == dict(projects=3, batches=1, zips=1)
    assert db.delivery_history() == history
    assert db.today_delivery_summary() == summary


@pytest.mark.parametrize('value', [True, False, 0, -1, 1.0, '1', None])
@pytest.mark.parametrize('operation', ['delete_project', 'restore_deleted_project'])
def test_deletion_requires_strict_positive_integer_id(db, value, operation):
    with pytest.raises(ValueError):
        getattr(db, operation)(value, **({'confirmed': True} if operation == 'delete_project' else {}))
    assert db.counts()['projects'] == 3


@pytest.mark.parametrize('confirmed', [False, None, 1, 'yes'])
def test_unconfirmed_delete_and_missing_id_do_not_change_database(db, confirmed):
    original = db.get_project(1)
    with pytest.raises(ValueError):
        db.delete_project(1, confirmed=confirmed)
    for operation in (lambda: db.delete_project(999, confirmed=True), lambda: db.restore_deleted_project(999)):
        with pytest.raises(KeyError):
            operation()
    assert db.get_project(1) == original
    with db.connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM project_deletions').fetchone()[0] == 0
        assert connection.execute('SELECT COUNT(*) FROM project_details').fetchone()[0] == 0


@pytest.mark.parametrize('deleted', [0, 1, 'deleted'])
def test_deletion_filter_rejects_non_boolean(db, deleted):
    with pytest.raises(ValueError):
        db.list_projects(deleted=deleted)


@pytest.mark.parametrize('action', [dict(status='开发中'), dict(urgent=False), dict(archived=False), dict(archived=True)])
def test_bulk_changes_cannot_revive_deleted_project_or_partially_edit_others(db, action):
    db.delete_project(1, confirmed=True)
    before = db.get_project(2)
    with pytest.raises(ValueError, match='删除'):
        db.bulk_update_projects([2, 1], **action)
    assert db.get_project(2) == before
    assert db.get_project(1)['deleted_at']
    with pytest.raises(ValueError, match='删除'):
        db.update_project_details(1, '不可写入', None)
    with db.connect() as connection:
        with pytest.raises(ValueError, match='删除'):
            db._assert_projects_editable(connection, 1)


def test_delete_restore_are_atomic_when_database_write_fails(db):
    with db.connect() as connection:
        connection.execute("CREATE TRIGGER fail_archive BEFORE UPDATE OF archived_at ON project_details BEGIN SELECT RAISE(ABORT,'injected archive failure'); END")
    original = db.get_project(1)
    with pytest.raises(sqlite3.IntegrityError, match='injected'):
        db.delete_project(1, confirmed=True)
    assert db.get_project(1) == original
    with db.connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM project_deletions').fetchone()[0] == 0
        connection.execute('DROP TRIGGER fail_archive')
    db.delete_project(1, confirmed=True)
    deleted = db.get_project(1)
    with db.connect() as connection:
        connection.execute("CREATE TRIGGER fail_restore BEFORE DELETE ON project_deletions BEGIN SELECT RAISE(ABORT,'injected restore failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match='injected'):
        db.restore_deleted_project(1)
    assert db.get_project(1) == deleted


def test_concurrent_repeated_delete_restore_preserves_original_archive(db):
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: db.delete_project(1, confirmed=True), range(4)))
    assert sum(row['changed'] for row in results) == 1
    assert db.get_project(1)['deleted_at']
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: db.restore_deleted_project(1), range(4)))
    assert sum(row['changed'] for row in results) == 1
    assert db.get_project(1)['deleted_at'] is None
    assert db.get_project(1)['archived_at'] is None


def test_registered_working_files_and_receipt_records_survive_delete_restore(db, tmp_path):
    from desktop_service import DesktopService

    service = DesktopService(db.path)
    source = tmp_path / '原始说明.docx'
    source.write_bytes(b'isolated original working document')
    result = service.workspaces.import_file(source, 1)
    copied = Path(result['file']['path'])
    workspace = service.workspaces.get_workspace(1)
    original = db.get_project(1)
    tables = ('project_workspaces', 'work_files', 'file_intake_events')

    def registered_records():
        with db.connect() as connection:
            return {table: [dict(row) for row in connection.execute(f'SELECT * FROM {table}')]
                    for table in tables}

    before = registered_records()
    for action in (lambda: db.delete_project(1, confirmed=True), lambda: db.restore_deleted_project(1)):
        action()
        assert source.read_bytes() == b'isolated original working document'
        assert copied.read_bytes() == source.read_bytes()
        assert registered_records() == before
        assert service.workspaces.get_workspace(1) == workspace
    assert db.get_project(1) == original
