"""只用隔离文件验证新收录状态规则及其事务边界。"""

import sqlite3
from dataclasses import asdict
from pathlib import Path
from zipfile import ZipFile

import pytest

from app import WorkBox
from database import Database
from workspace_manager import WorkspaceManager
from zip_manager import inspect_zip


@pytest.fixture
def context(tmp_path):
    box = WorkBox(tmp_path / 'data' / 'workbox.db')
    ids = box.db.create_capture('甲系统\n乙系统', '本批', [('甲系统', False), ('乙系统', False)])['project_ids']
    return box, WorkspaceManager(box), ids


def package(box, project_id, filename='旧版.zip'):
    path = box.db.path.parent / filename
    with ZipFile(path, 'w') as archive:
        archive.writestr('说明.txt', '真实隔离夹具')
    return box.db.register_zip(asdict(inspect_zip(path)), project_id)[0]


def delivered(box, pid, filename='旧版.zip'):
    record = package(box, pid, filename)
    box.confirm_delivery(pid, record['id'], confirmed=True)
    with box.db.connect() as connection:
        box.db._approve_zip_for_projects(connection, record['id'])
    return record


def apply_counts(db, zip_id, counts):
    with db.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        db._apply_zip_file_counts(connection, zip_id, counts)


def shared_package(box, ids):
    record = package(box, ids[0], '甲系统-2.zip')
    with box.db.connect() as connection:
        connection.executemany('INSERT INTO zip_bundle_members(zip_id,project_id) VALUES (?,?)',
                               [(record['id'], pid) for pid in ids])
    return record


@pytest.mark.parametrize('duplicate', [False, True])
def test_successful_work_file_intake_returns_delivered_project_to_pending(context, tmp_path, duplicate):
    box, manager, ids = context
    pid = ids[0]
    source = tmp_path / '甲系统.txt'
    source.write_text('工作正文', encoding='utf-8')
    if duplicate:
        initial = manager.import_file(source, pid)
    old = delivered(box, pid)

    result = manager.import_file(source, pid)

    current = box.get_project(pid)
    assert result['duplicate'] is duplicate
    assert (current['status'], current['delivered_at'], current['delivered_zip_id']) == ('待交付', None, None)
    assert box.db.approved_zip_for_project(pid) == old['id']
    assert box.db.get_zip(old['id'])['sha256'] == old['sha256']
    assert source.read_text(encoding='utf-8') == '工作正文'
    assert manager.intake_log.list_recent()[0]['status'] == ('duplicate' if duplicate else 'copied')
    if duplicate:
        assert result['file']['id'] == initial['file']['id']


@pytest.mark.parametrize('starting_status', ['待开发', '开发中'])
def test_successful_work_file_intake_sets_pending_from_earlier_states(context, tmp_path, starting_status):
    box, manager, ids = context
    box.set_status(ids[0], starting_status)
    source = tmp_path / '甲系统.txt'
    source.write_bytes(b'work')
    manager.import_file(source, ids[0])
    assert box.get_project(ids[0])['status'] == '待交付'


@pytest.mark.parametrize('failure', ['log', 'status', 'copy'])
def test_work_file_failure_rolls_back_status_record_event_and_own_copy(context, tmp_path, monkeypatch, failure):
    box, manager, ids = context
    pid = ids[0]
    old = delivered(box, pid)
    before = box.get_project(pid)
    directory = Path(manager.ensure_projects([pid])[0]['path'])
    source = tmp_path / '甲系统.txt'
    source.write_bytes(b'keep source')
    if failure == 'log':
        with box.db.connect() as connection:
            connection.execute("CREATE TRIGGER reject_event BEFORE INSERT ON file_intake_events BEGIN SELECT RAISE(ABORT,'event failure'); END")
    elif failure == 'status':
        with box.db.connect() as connection:
            connection.execute("CREATE TRIGGER reject_status BEFORE UPDATE OF status ON projects WHEN NEW.status='待交付' BEGIN SELECT RAISE(ABORT,'status failure'); END")
    else:
        import workspace_manager
        def fail_copy(*args, **kwargs):
            raise OSError('copy failure')
        monkeypatch.setattr(workspace_manager, 'copy_verified', fail_copy)

    with pytest.raises((sqlite3.DatabaseError, OSError)):
        manager.import_file(source, pid)

    current = box.get_project(pid)
    assert (current['status'], current['delivered_at'], current['delivered_zip_id']) == (before['status'], before['delivered_at'], old['id'])
    with box.db.connect() as connection:
        assert connection.execute('SELECT count(*) FROM work_files').fetchone()[0] == 0
        assert connection.execute('SELECT count(*) FROM file_intake_events').fetchone()[0] == 0
    assert list(directory.iterdir()) == []
    assert source.read_bytes() == b'keep source'
    assert box.db.approved_zip_for_project(pid) == old['id']


def test_duplicate_failure_keeps_previous_work_file_event_and_delivery(context, tmp_path):
    box, manager, ids = context
    source = tmp_path / '甲系统.txt'
    source.write_bytes(b'keep')
    original = manager.import_file(source, ids[0])
    old = delivered(box, ids[0])
    before = box.get_project(ids[0])
    with box.db.connect() as connection:
        connection.execute("CREATE TRIGGER reject_status BEFORE UPDATE OF status ON projects WHEN NEW.status='待交付' BEGIN SELECT RAISE(ABORT,'status failure'); END")
    with pytest.raises(sqlite3.DatabaseError):
        manager.import_file(source, ids[0])
    assert box.get_project(ids[0])['delivered_at'] == before['delivered_at']
    assert box.get_project(ids[0])['delivered_zip_id'] == old['id']
    assert Path(original['file']['path']).read_bytes() == b'keep'
    assert [row['status'] for row in manager.intake_log.list_recent()] == ['copied']


@pytest.mark.parametrize('count,expected', [(0, '待交付'), (3, '待交付'), (4, '已交付'), (7, '已交付')])
def test_zip_count_boundary_records_automatic_fact_and_keeps_approval(context, count, expected):
    box, _, ids = context
    old = delivered(box, ids[0])
    record = package(box, ids[0], '新版.zip')

    apply_counts(box.db, record['id'], {ids[0]: count})

    project = box.get_project(ids[0])
    assert project['status'] == expected
    assert project['delivered_zip_id'] == (record['id'] if count > 3 else None)
    assert bool(project['delivered_at']) is (count > 3)
    assert box.db.approved_zip_for_project(ids[0]) == old['id']
    fact, = box.db.get_zip_file_counts(record['id'])
    assert (fact['project_id'], fact['project_name'], fact['file_count'], fact['decided_status']) == (ids[0], '甲系统', count, expected)
    assert fact['rule'] == 'zip-file-count-v1'
    assert fact['checked_at']


def test_shared_zip_applies_each_project_count_independently(context):
    box, _, ids = context
    record = shared_package(box, ids)
    apply_counts(box.db, record['id'], {ids[0]: 4, ids[1]: 3})
    assert [box.get_project(pid)['status'] for pid in ids] == ['已交付', '待交付']
    assert [(row['project_id'], row['file_count']) for row in box.db.get_zip_file_counts(record['id'])] == [(ids[0], 4), (ids[1], 3)]
    assert box.db.approved_zip_for_project(ids[0]) is None


def test_repeated_same_zip_keeps_delivery_and_fact_timestamps(context):
    box, _, ids = context
    record = package(box, ids[0])
    apply_counts(box.db, record['id'], {ids[0]: 4})
    with box.db.connect() as connection:
        connection.execute("UPDATE projects SET delivered_at='2000-01-01T00:00:00Z',updated_at='2000-01-01T00:00:00Z' WHERE id=?", (ids[0],))
        connection.execute("UPDATE zip_project_file_counts SET checked_at='2000-01-01T00:00:00Z' WHERE zip_id=?", (record['id'],))
    apply_counts(box.db, record['id'], {ids[0]: 4})
    assert box.get_project(ids[0])['delivered_at'] == '2000-01-01T00:00:00Z'
    assert box.get_project(ids[0])['updated_at'] == '2000-01-01T00:00:00Z'
    assert box.db.get_zip_file_counts(record['id'])[0]['checked_at'] == '2000-01-01T00:00:00Z'


@pytest.mark.parametrize('mutation', ['missing', 'extra', 'negative', 'bool', 'float', 'key', 'archived', 'unmatched', 'no-zip', 'empty'])
def test_invalid_zip_counts_rejected_before_any_project_changes(context, mutation):
    box, _, ids = context
    record = shared_package(box, ids)
    counts = {ids[0]: 4, ids[1]: 3}
    if mutation == 'missing': counts.pop(ids[1])
    elif mutation == 'extra': counts[999] = 4
    elif mutation == 'negative': counts[ids[1]] = -1
    elif mutation == 'bool': counts[ids[1]] = True
    elif mutation == 'float': counts[ids[1]] = 4.0
    elif mutation == 'key': counts = {str(ids[0]): 4, ids[1]: 3}
    elif mutation == 'archived': box.bulk_update_projects([ids[1]], archived=True)
    elif mutation == 'unmatched':
        with box.db.connect() as connection:
            connection.execute("INSERT INTO zip_bundles(zip_id,member_names,match_state) VALUES (?,'[]','needs-review')", (record['id'],))
    elif mutation == 'no-zip': record['id'] = 999
    else: counts = {}
    before = [box.get_project(pid) for pid in ids]
    with pytest.raises((ValueError, KeyError)):
        apply_counts(box.db, record['id'], counts)
    assert [box.get_project(pid) for pid in ids] == before
    assert box.db.get_zip_file_counts(record['id']) == []


@pytest.mark.parametrize('failure', ['fact', 'status'])
def test_zip_status_or_fact_failure_rolls_back_whole_group(context, failure):
    box, _, ids = context
    record = shared_package(box, ids)
    with box.db.connect() as connection:
        if failure == 'fact':
            connection.execute(f"CREATE TRIGGER reject_second_fact BEFORE INSERT ON zip_project_file_counts WHEN NEW.project_id={ids[1]} BEGIN SELECT RAISE(ABORT,'fact failure'); END")
        else:
            connection.execute(f"CREATE TRIGGER reject_second_status BEFORE UPDATE OF status ON projects WHEN NEW.id={ids[1]} AND NEW.status='已交付' BEGIN SELECT RAISE(ABORT,'status failure'); END")
    before = [box.get_project(pid) for pid in ids]
    with pytest.raises(sqlite3.DatabaseError):
        apply_counts(box.db, record['id'], {ids[0]: 4, ids[1]: 4})
    assert [box.get_project(pid) for pid in ids] == before
    assert box.db.get_zip_file_counts(record['id']) == []


@pytest.mark.parametrize('mutation', ['missing-file', 'other-project', 'archived', 'invalid-duplicate'])
def test_pending_helper_requires_existing_active_project_work_file(context, tmp_path, mutation):
    box, manager, ids = context
    source = tmp_path / '甲系统.txt'
    source.write_bytes(b'work')
    saved = manager.import_file(source, ids[0])['file']
    old = delivered(box, ids[0])
    pid, file_id, duplicate = ids[0], saved['id'], True
    if mutation == 'missing-file': file_id = 999
    elif mutation == 'other-project': pid = ids[1]
    elif mutation == 'archived': box.bulk_update_projects([ids[0]], archived=True)
    else: duplicate = 1
    before = [box.get_project(project_id) for project_id in ids]
    with pytest.raises((ValueError, KeyError)):
        with box.db.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            box.db._mark_work_file_pending(connection, pid, file_id, duplicate=duplicate)
    assert [box.get_project(project_id) for project_id in ids] == before
    assert box.db.approved_zip_for_project(ids[0]) == old['id']


def test_restart_creates_no_historical_count_or_status_facts(context, tmp_path):
    box, manager, ids = context
    record = package(box, ids[0])
    source = tmp_path / '历史.txt'
    source.write_bytes(b'old')
    manager.import_file(source, ids[1])
    box.set_status(ids[1], '开发中')
    before = [box.get_project(pid) for pid in ids]
    restarted = WorkBox(box.db.path)
    WorkspaceManager(restarted)
    assert [restarted.get_project(pid) for pid in ids] == before
    assert restarted.db.get_zip_file_counts(record['id']) == []


def test_legacy_register_zip_keeps_manual_delivery_contract(context):
    box, _, ids = context
    old = delivered(box, ids[0])
    current = package(box, ids[0], '仅登记.zip')
    assert box.get_project(ids[0])['delivered_zip_id'] == old['id']
    assert box.db.get_zip_file_counts(current['id']) == []
