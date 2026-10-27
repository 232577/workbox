"""交付事实、北京日统计与普通材料手动交付的隔离数据库回归。"""

import hashlib
import sqlite3
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path
from zipfile import ZipFile

import pytest

import database
from app import WorkBox
from database import Database
from zip_manager import inspect_zip


@pytest.fixture
def box(tmp_path):
    value = WorkBox(tmp_path / 'workbox.db')
    value.db.create_capture('甲系统\n乙系统', '交付批', [('甲系统', False), ('乙系统', False)])
    return value


def package(box, pid=1, name='甲系统.zip'):
    path = box.db.path.parent / name
    with ZipFile(path, 'w') as archive:
        archive.writestr('说明.txt', '隔离ZIP资料')
    return box.db.register_zip(asdict(inspect_zip(path)), pid)[0]


def apply_count(box, record, count):
    with box.db.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        box.db._apply_zip_file_counts(connection, record['id'], {record['project_id']: count})


def all_events(db):
    with db.connect() as connection:
        return [dict(row) for row in connection.execute('SELECT * FROM delivery_events ORDER BY id')]


def file_archive(tmp_path, name='材料归档'):
    root = tmp_path / name
    root.mkdir()
    body = b'isolated word body'
    (root / '交付说明.docx').write_bytes(body)
    return dict(path=str(root), signature=hashlib.sha256(body).hexdigest(), file_count=1,
                manifest=[dict(path='交付说明.docx', size=len(body), sha256=hashlib.sha256(body).hexdigest())])


def complete_files(db, pid, verified):
    with db.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        return db._complete_file_delivery(connection, pid, verified)


def insert_event(db, pid, occurred_at, *, name=None, kind='manual-files', archive_path='隔离归档'):
    with db.connect() as connection:
        return connection.execute('''INSERT INTO delivery_events(
            project_id,project_name,batch_name,kind,zip_id,archive_path,occurred_at)
            VALUES (?,?,?, ?,NULL,?,?)''',
            (pid, name or ('甲系统' if pid == 1 else '乙系统'), '交付批', kind, archive_path, occurred_at)).lastrowid


def test_auto_zip_delivery_appends_fact_only_on_actual_transition_or_new_zip(box):
    first = package(box)
    apply_count(box, first, 4)
    event, = all_events(box.db)
    assert (event['project_id'], event['project_name'], event['batch_name'], event['kind'], event['zip_id'], event['archive_path']) == (1, '甲系统', '交付批', 'auto-zip', first['id'], first['path'])
    timestamp = box.get_project(1)['delivered_at']
    assert event['occurred_at'] == timestamp
    apply_count(box, first, 4)
    assert all_events(box.db) == [event]
    assert box.get_project(1)['delivered_at'] == timestamp

    box.set_status(1, '待交付')
    apply_count(box, first, 4)
    second = package(box, name='甲系统新版.zip')
    apply_count(box, second, 5)
    events = all_events(box.db)
    assert len(events) == 3
    assert [row['zip_id'] for row in events] == [first['id'], first['id'], second['id']]
    summary = box.db.today_delivery_summary()
    assert summary['project_count'] == 1 and summary['event_count'] == 3
    assert summary['projects'][0]['delivery_count'] == 3


def test_pending_zip_does_not_create_delivery_history(box):
    record = package(box)
    apply_count(box, record, 3)
    assert box.db.today_delivery_summary()['event_count'] == 0
    assert all_events(box.db) == []


def test_manual_zip_confirmation_records_distinct_history_kind(box):
    record = package(box)
    box.confirm_delivery(1, record['id'], confirmed=True)
    event, = all_events(box.db)
    assert event['kind'] == 'manual-zip' and event['zip_id'] == record['id']
    assert event['archive_path'] == record['path']
    assert event['occurred_at'] == box.get_project(1)['delivered_at']
    with pytest.raises(ValueError):
        box.confirm_delivery(1, record['id'], confirmed=True)
    assert len(all_events(box.db)) == 1


@pytest.mark.parametrize('kind', ['auto', 'manual-zip', 'manual-files'])
def test_delivery_event_write_failure_rolls_back_project_and_count_state(box, tmp_path, kind):
    record = package(box)
    verified = file_archive(tmp_path)
    before = box.get_project(1)
    with box.db.connect() as connection:
        connection.execute("CREATE TRIGGER reject_delivery BEFORE INSERT ON delivery_events BEGIN SELECT RAISE(ABORT,'delivery fact failure'); END")
    with pytest.raises(sqlite3.DatabaseError):
        if kind == 'auto': apply_count(box, record, 4)
        elif kind == 'manual-zip': box.confirm_delivery(1, record['id'], confirmed=True)
        else: complete_files(box.db, 1, verified)
    assert box.get_project(1) == before
    assert all_events(box.db) == []
    assert box.db.get_zip_file_counts(record['id']) == []


def test_only_word_can_be_manually_delivered_without_a_zip(box, tmp_path):
    verified = file_archive(tmp_path)
    event_id = complete_files(box.db, 1, verified)
    project = box.get_project(1)
    event, = all_events(box.db)
    assert project['status'] == '已交付' and project['delivered_at']
    assert project['delivered_zip_id'] is None and project['zips'] == []
    assert event['id'] == event_id and event['kind'] == 'manual-files' and event['zip_id'] is None
    assert event['archive_path'] == verified['path']
    assert event['archive_signature'] == verified['signature']
    assert complete_files(box.db, 1, verified) == event_id
    assert all_events(box.db) == [event]
    assert box.get_project(1)['delivered_at'] == project['delivered_at']
    box.set_status(1, '待交付')
    assert complete_files(box.db, 1, verified) != event_id
    assert len(all_events(box.db)) == 2


def test_manual_files_keeps_old_zip_versions_and_approval(box, tmp_path):
    old = package(box)
    with box.db.connect() as connection:
        box.db._approve_zip_for_projects(connection, old['id'])
    complete_files(box.db, 1, file_archive(tmp_path))
    assert box.get_project(1)['delivered_zip_id'] is None
    assert box.db.approved_zip_for_project(1) == old['id']
    assert box.get_project(1)['zips'][0]['id'] == old['id']


@pytest.mark.parametrize('mutation', ['empty', 'count', 'bool-count', 'traversal', 'absolute', 'missing', 'size', 'hash', 'signature', 'duplicate', 'archived', 'not-dict'])
def test_unverified_file_archive_is_rejected_without_delivery_event(box, tmp_path, mutation):
    verified = file_archive(tmp_path)
    if mutation == 'empty': verified['manifest'] = []; verified['file_count'] = 0
    elif mutation == 'count': verified['file_count'] = 2
    elif mutation == 'bool-count': verified['file_count'] = True
    elif mutation == 'traversal': verified['manifest'][0]['path'] = '../outside.docx'
    elif mutation == 'absolute': verified['manifest'][0]['path'] = str(tmp_path / 'outside.docx')
    elif mutation == 'missing': Path(verified['path'], '交付说明.docx').unlink()
    elif mutation == 'size': verified['manifest'][0]['size'] += 1
    elif mutation == 'hash': verified['manifest'][0]['sha256'] = 'b' * 64
    elif mutation == 'signature': verified['signature'] = 'unverified'
    elif mutation == 'duplicate': verified['manifest'] *= 2; verified['file_count'] = 2
    elif mutation == 'archived': box.bulk_update_projects([1], archived=True)
    else: verified = None
    before = box.get_project(1)
    with pytest.raises((ValueError, FileNotFoundError, OSError)):
        complete_files(box.db, 1, verified)
    assert box.get_project(1) == before
    assert all_events(box.db) == []


def test_delivery_summary_uses_beijing_midnight_and_counts_lifetime_first_projects(box):
    insert_event(box.db, 1, '2026-10-22T15:59:59.999Z')
    first = insert_event(box.db, 1, '2026-10-22T16:00:00.000Z', name='甲交付时旧名')
    insert_event(box.db, 1, '2026-10-23T15:59:59.999Z', name='甲交付时新名', archive_path='最新归档')
    insert_event(box.db, 2, '2026-10-23T08:00:00.000Z')
    insert_event(box.db, 2, '2026-10-23T16:00:00.000Z')
    summary = box.db.today_delivery_summary(date(2026, 10, 23))
    assert (summary['day'], summary['project_count'], summary['event_count']) == ('2026-10-23', 1, 3)
    assert summary['projects'] == [
        {'project_id': 1, 'project_name': '甲交付时新名', 'batch_name': '交付批', 'delivery_count': 2,
         'latest_at': '2026-10-23T15:59:59.999Z', 'archive_path': '最新归档', 'kind': 'manual-files',
         'counted': False, 'first_at': '2026-10-22T15:59:59.999Z', 'repeat_count': 2, 'voided_count': 0,
         'last_action_id': None, 'last_action': None, 'management_reason': '', 'managed_at': None,
         'prior_delivery': False, 'prior_delivery_reason': ''},
        {'project_id': 2, 'project_name': '乙系统', 'batch_name': '交付批', 'delivery_count': 1,
         'latest_at': '2026-10-23T08:00:00.000Z', 'archive_path': '隔离归档', 'kind': 'manual-files',
         'counted': True, 'first_at': '2026-10-23T08:00:00.000Z', 'repeat_count': 0, 'voided_count': 0,
         'last_action_id': None, 'last_action': None, 'management_reason': '', 'managed_at': None,
         'prior_delivery': False, 'prior_delivery_reason': ''},
    ]
    details = box.db.delivery_events_for_project(1, date(2026, 10, 23))
    assert [row['project_name'] for row in details] == ['甲交付时新名', '甲交付时旧名']
    assert details[-1]['id'] == first
    assert box.db.today_delivery_summary(date(2026, 10, 22))['project_count'] == 1
    assert box.db.today_delivery_summary(date(2026, 10, 24))['project_count'] == 0


def test_default_day_follows_beijing_even_when_utc_is_previous_date(box, monkeypatch):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            fixed = datetime(2026, 10, 22, 16, 1, tzinfo=timezone.utc)
            return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)
    monkeypatch.setattr(database, 'datetime', FrozenDateTime, raising=False)
    insert_event(box.db, 1, '2026-10-22T16:00:00.000Z')
    assert box.db.today_delivery_summary()['day'] == '2026-10-23'
    assert len(box.db.delivery_events_for_project(1)) == 1


def test_later_rename_archive_and_status_reset_never_erase_delivery_facts(box):
    insert_event(box.db, 1, '2026-10-23T08:00:00.000Z', name='交付时甲系统')
    before = box.db.today_delivery_summary(date(2026, 10, 23))
    with box.db.connect() as connection:
        connection.execute("UPDATE projects SET name='后来改名' WHERE id=1")
        connection.execute("UPDATE batches SET name='后来批次名' WHERE id=1")
    box.set_status(1, '待交付')
    box.bulk_update_projects([1], archived=True)
    assert box.db.today_delivery_summary(date(2026, 10, 23)) == before
    assert box.db.delivery_events_for_project(1, date(2026, 10, 23))[0]['project_name'] == '交付时甲系统'


@pytest.mark.parametrize('operation', ['update', 'delete'])
def test_delivery_event_table_is_append_only(box, operation):
    event_id = insert_event(box.db, 1, '2026-10-23T08:00:00.000Z')
    with pytest.raises(sqlite3.DatabaseError):
        with box.db.connect() as connection:
            if operation == 'update':
                connection.execute("UPDATE delivery_events SET project_name='改写' WHERE id=?", (event_id,))
            else:
                connection.execute('DELETE FROM delivery_events WHERE id=?', (event_id,))
    assert all_events(box.db)[0]['project_name'] == '甲系统'


def test_upgrade_imports_saved_delivery_fact_once_without_changing_projects(box):
    record = package(box)
    with box.db.connect() as connection:
        connection.execute("UPDATE projects SET status='已交付',delivered_at='2026-10-23T08:00:00.000Z',delivered_zip_id=? WHERE id=1", (record['id'],))
        connection.execute('DELETE FROM delivery_event_migrations')
    before = box.get_project(1)
    restarted = Database(box.db.path)
    assert restarted.get_project(1) == before
    assert restarted.today_delivery_summary(date(2026, 10, 23))['event_count'] == 1
    event, = all_events(restarted)
    assert event['kind'] == 'legacy' and event['occurred_at'] == before['delivered_at']
    assert event['zip_id'] == record['id'] and event['archive_path'] == record['path']
    Database(box.db.path)
    assert all_events(restarted) == [event]


def test_upgrade_does_not_invent_cleared_or_missing_delivery_history(box):
    with box.db.connect() as connection:
        connection.execute("UPDATE projects SET status='已交付',delivered_at=NULL WHERE id=1")
        connection.execute("UPDATE projects SET status='待交付',delivered_at='2026-10-23T08:00:00.000Z' WHERE id=2")
        connection.execute('DELETE FROM delivery_event_migrations')
    before = [box.get_project(pid) for pid in (1, 2)]
    restarted = Database(box.db.path)
    assert [restarted.get_project(pid) for pid in (1, 2)] == before
    assert all_events(restarted) == []
