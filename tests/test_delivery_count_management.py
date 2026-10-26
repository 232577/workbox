"""首次有效交付及误记管理：只使用临时隔离数据库和归档。"""

import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import date
from zipfile import ZipFile

import pytest

from database import Database
from zip_manager import inspect_zip


@pytest.fixture
def db(tmp_path):
    result = Database(tmp_path / 'isolated.db')
    result.create_capture('甲乙', '交付批', [('甲系统', False), ('乙系统', False)])
    return result


def event(db, pid, when, kind='manual-files', name='甲系统'):
    with db.connect() as connection:
        return connection.execute('''INSERT INTO delivery_events(
            project_id,project_name,batch_name,kind,archive_path,occurred_at)
            VALUES (?,?,?,?,'隔离归档',?)''', (pid, name, '交付批', kind, when)).lastrowid


def raw_events(db):
    with db.connect() as connection:
        return [dict(row) for row in connection.execute('SELECT * FROM delivery_events ORDER BY id')]


def actions(db):
    with db.connect() as connection:
        return [dict(row) for row in connection.execute('SELECT * FROM delivery_event_actions ORDER BY id')]


def test_lifetime_first_delivery_uses_beijing_boundary_and_keeps_repeats(db):
    first = event(db, 1, '2026-10-22T15:59:59.999Z')
    repeat = event(db, 1, '2026-10-22T16:00:00.000Z')
    event(db, 1, '2026-10-23T15:59:59.999Z')
    event(db, 2, '2026-10-23T08:00:00.000Z', name='乙系统')
    event(db, 2, '2026-10-23T16:00:00.000Z', name='乙系统')
    summary = db.today_delivery_summary(date(2026, 10, 23))
    assert (summary['project_count'], summary['event_count'], summary['repeat_count']) == (1, 3, 2)
    assert {row['project_id']: row['counted'] for row in summary['projects']} == {1: False, 2: True}
    assert db.today_delivery_summary(date(2026, 10, 22))['project_count'] == 1
    assert db.today_delivery_summary(date(2026, 10, 24))['project_count'] == 0
    assert db.delivery_events_for_project(1, date(2026, 10, 23))[-1]['id'] == repeat
    assert db.delivery_history()['events'][-1]['id'] == first


def test_void_restore_moves_first_to_next_valid_date_without_changing_facts(db):
    first = event(db, 1, '2026-10-22T08:00:00.000Z', kind='auto-zip')
    second = event(db, 1, '2026-10-23T08:00:00.000Z', kind='manual-zip')
    third = event(db, 1, '2026-10-24T08:00:00.000Z')
    original = raw_events(db)
    assert db.void_delivery_events([first], confirmed=True, reason='误记')['changed_count'] == 1
    assert db.today_delivery_summary(date(2026, 10, 22))['project_count'] == 0
    assert db.today_delivery_summary(date(2026, 10, 23))['project_count'] == 1
    assert db.delivery_history()['events'][1]['counting_role'] == 'first'
    db.void_delivery_events([second, third], confirmed=True)
    assert db.delivery_history()['project_count'] == 0
    assert db.delivery_history()['voided_count'] == 3
    db.restore_delivery_events([first], confirmed=True, reason='核实恢复')
    assert db.delivery_history()['project_count'] == 1
    assert db.today_delivery_summary(date(2026, 10, 22))['project_count'] == 1
    assert raw_events(db) == original
    assert db.delivery_history()['events'][-1]['management_reason'] == '核实恢复'


def test_same_timestamp_uses_id_and_same_day_repeats_count_once(db):
    first = event(db, 1, '2026-10-23T08:00:00.000Z')
    second = event(db, 1, '2026-10-23T08:00:00.000Z')
    assert [row['counting_role'] for row in db.delivery_history()['events']] == ['repeat', 'first']
    db.void_delivery_events([first], confirmed=True)
    assert db.delivery_history()['events'][0]['id'] == second
    assert db.delivery_history()['events'][0]['counting_role'] == 'first'
    assert db.today_delivery_summary(date(2026, 10, 23))['project_count'] == 1
    db.restore_delivery_events([first], confirmed=True)
    assert db.delivery_history()['events'][1]['counting_role'] == 'first'


def test_import_order_does_not_override_real_delivery_time_and_search_does_not_reset_first(db):
    repeat = event(db, 1, '2026-10-24T08:00:00.000Z', name='新名称')
    first = event(db, 1, '2026-10-22T08:00:00.000Z', name='旧名称')
    assert db.delivery_history()['events'][0]['id'] == repeat
    assert db.delivery_history()['events'][1]['id'] == first
    filtered = db.delivery_history(day=date(2026, 10, 24), keyword='新名称')
    assert filtered['event_count'] == 1 and filtered['project_count'] == 0
    assert filtered['events'][0]['counting_role'] == 'repeat'
    db.void_delivery_events([first], confirmed=True)
    assert db.delivery_history(day=date(2026, 10, 24), keyword='新名称')['project_count'] == 1


def test_history_filters_match_snapshot_names_keep_archived_and_distinct_ids(db):
    first = event(db, 1, '2026-10-23T08:00:00.000Z', name='同名系统')
    second = event(db, 2, '2026-10-23T09:00:00.000Z', name='同名系统')
    db.bulk_update_projects([1], archived=True)
    result = db.delivery_history(keyword='同名')
    assert result['project_count'] == 2
    assert {row['id'] for row in result['events']} == {first, second}
    assert db.delivery_history(keyword='交付批', project_id=1)['event_count'] == 1
    assert db.delivery_history(keyword='%')['event_count'] == 0
    assert db.delivery_history(day=date(2026, 10, 24))['event_count'] == 0
    db.void_delivery_events([first], confirmed=True)
    assert db.delivery_history(project_id=1)['events'][0]['voided'] is True


def test_cancel_and_idempotent_actions_do_not_write(db):
    eid = event(db, 1, '2026-10-23T08:00:00.000Z')
    assert db.void_delivery_events([eid])['changed_count'] == 0
    assert db.void_delivery_events([eid], confirmed=False)['confirmed'] is False
    assert actions(db) == []
    assert db.restore_delivery_events([eid], confirmed=True)['changed_count'] == 0
    assert db.void_delivery_events([eid, eid], confirmed=True)['changed_count'] == 1
    assert db.void_delivery_events([eid], confirmed=True)['changed_count'] == 0
    assert len(actions(db)) == 1
    db.restore_delivery_events([eid], confirmed=True)
    assert len(actions(db)) == 2


@pytest.mark.parametrize('kwargs', [
    {'event_ids': []}, {'event_ids': [True]}, {'event_ids': [0]}, {'event_ids': [1, 999]},
    {'event_ids': [1], 'confirmed': 'yes'}, {'event_ids': [1], 'voided': 1},
    {'event_ids': [1], 'reason': 'x' * 1001}, {'event_ids': [1], 'reason': None},
])
def test_invalid_management_requests_are_atomic(db, kwargs):
    event(db, 1, '2026-10-23T08:00:00.000Z')
    args = dict(event_ids=[1], voided=True, confirmed=True)
    args.update(kwargs)
    with pytest.raises(ValueError):
        db.set_delivery_events_voided(**args)
    assert actions(db) == []
    assert db.delivery_history()['project_count'] == 1


def test_management_failure_rolls_back_whole_selection(db):
    first = event(db, 1, '2026-10-23T08:00:00.000Z')
    second = event(db, 2, '2026-10-23T09:00:00.000Z')
    with db.connect() as connection:
        connection.execute(f'''CREATE TRIGGER reject_second BEFORE INSERT ON delivery_event_actions
            WHEN NEW.event_id={second} BEGIN SELECT RAISE(ABORT,'fixture failure'); END''')
    with pytest.raises(sqlite3.DatabaseError):
        db.void_delivery_events([first, second], confirmed=True)
    assert actions(db) == []
    assert db.delivery_history()['project_count'] == 2


def test_concurrent_same_operation_writes_one_action(db):
    eid = event(db, 1, '2026-10-23T08:00:00.000Z')
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: db.void_delivery_events([eid], confirmed=True), range(8)))
    assert sum(result['changed_count'] for result in results) == 1
    assert len(actions(db)) == 1
    assert db.delivery_history()['project_count'] == 0


def test_reopen_preserves_actions_history_and_current_project_fields(db):
    first = event(db, 1, '2026-10-23T08:00:00.000Z')
    event(db, 1, '2026-10-24T08:00:00.000Z')
    original = raw_events(db)
    db.void_delivery_events([first], confirmed=True, reason='保留说明')
    before_project = db.get_project(1)
    assert before_project['status'] == '待交付'
    before = db.delivery_history()
    before_actions = actions(db)
    reopened = Database(db.path)
    assert reopened.get_project(1) == before_project
    assert raw_events(reopened) == original
    assert actions(reopened) == before_actions
    assert reopened.delivery_history() == before
    assert reopened.today_delivery_summary(date(2026, 10, 24))['project_count'] == 1


def test_queries_expose_last_management_action_and_never_write(db):
    eid = event(db, 1, '2026-10-23T08:00:00.000Z')
    pristine = db.delivery_history()['events'][0]
    assert pristine['last_action_id'] is None and pristine['last_action'] is None
    assert actions(db) == []
    db.void_delivery_events([eid], confirmed=True, reason='统计误记')
    changed = db.delivery_history()['events'][0]
    assert changed['last_action_id'] == actions(db)[-1]['id']
    assert changed['last_action'] == 'void'
    summary_row = db.today_delivery_summary(date(2026, 10, 23))['projects'][0]
    assert summary_row['last_action'] == 'void'
    assert summary_row['management_reason'] == '统计误记'
    db.restore_delivery_events([eid], confirmed=True)
    assert db.delivery_history()['events'][0]['last_action'] == 'restore'
    assert len(actions(db)) == 2


def test_selection_bound_and_query_validation_do_not_write(db):
    eid = event(db, 1, '2026-10-23T08:00:00.000Z')
    with pytest.raises(ValueError):
        db.void_delivery_events([eid] * 501, confirmed=True)
    assert db.void_delivery_events([eid] * 500, confirmed=True)['changed_count'] == 1
    for kwargs in ({'day': '2026-10-23'}, {'keyword': None}, {'keyword': 'x' * 201}, {'project_id': True}):
        with pytest.raises(ValueError):
            db.delivery_history(**kwargs)
    assert len(actions(db)) == 1


def test_real_zip_manual_word_and_legacy_keep_individual_delivery_events(db, tmp_path):
    source = tmp_path / '甲系统.zip'
    with ZipFile(source, 'w') as archive:
        for i in range(4):
            archive.writestr(f'{i}.txt', '隔离资料')
    record, _ = db.register_zip(asdict(inspect_zip(source)), 1)
    with db.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        db._apply_zip_file_counts(connection, record['id'], {1: 4})
    root = tmp_path / 'Word归档'
    root.mkdir()
    body = b'isolated word body'
    word = root / '资料.docx'
    word.write_bytes(body)
    digest = hashlib.sha256(body).hexdigest()
    manifest = dict(path=str(root), signature=digest, file_count=1,
                    manifest=[dict(path=word.name, size=len(body), sha256=digest)])
    with db.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        db._complete_file_delivery(connection, 1, manifest)
    with db.connect() as connection:
        connection.execute("UPDATE projects SET status='已交付',delivered_at='2026-10-20T08:00:00.000Z' WHERE id=2")
        connection.execute('DELETE FROM delivery_event_migrations')
    restarted = Database(db.path)
    assert {row['kind'] for row in restarted.delivery_history()['events']} == {'auto-zip', 'manual-files', 'legacy'}
    originals = raw_events(restarted)
    restarted.void_delivery_events([row['id'] for row in originals], confirmed=True)
    assert restarted.get_project(1)['status'] == '待交付'
    assert restarted.get_project(1)['delivered_at'] is None
    assert restarted.get_project(1)['delivered_zip_id'] is None
    assert source.is_file() and word.read_bytes() == body
    assert raw_events(restarted) == originals
    assert restarted.delivery_history()['project_count'] == 0


@pytest.mark.parametrize('table', ['delivery_events', 'delivery_event_actions'])
@pytest.mark.parametrize('operation', ['UPDATE', 'DELETE'])
def test_events_and_management_log_are_append_only(db, table, operation):
    eid = event(db, 1, '2026-10-23T08:00:00.000Z')
    db.void_delivery_events([eid], confirmed=True)
    with pytest.raises(sqlite3.DatabaseError):
        with db.connect() as connection:
            statement = f'DELETE FROM {table}' if operation == 'DELETE' else f'UPDATE {table} SET id=id'
            connection.execute(statement)
