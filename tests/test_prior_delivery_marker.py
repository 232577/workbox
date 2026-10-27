"""历史已交付、首次日期未知的统计规则：只追加规则，不补造交付事实。"""

import sqlite3
from datetime import date, datetime, timezone

import pytest

from database import Database


DAY = date(2026, 10, 26)
REASON = '用户确认以前已交付，实际首次日期未知'


@pytest.fixture
def db(tmp_path):
    store = Database(tmp_path / 'isolated.db')
    store.create_capture('甲系统\n乙系统\n丙系统', '隔离批次',
                         [('甲系统', False), ('乙系统', False), ('丙系统', False)])
    return store


def event(db, pid, occurred_at):
    with db.connect() as connection:
        return connection.execute('''INSERT INTO delivery_events(
            project_id,project_name,batch_name,kind,archive_path,occurred_at)
            VALUES (?,?,'隔离批次','manual-files','隔离归档',?)''',
            (pid, f'隔离项目{pid}', occurred_at)).lastrowid


def rows(db, table):
    with db.connect() as connection:
        return [dict(row) for row in connection.execute(f'SELECT * FROM {table} ORDER BY id')]


def mark(db, ids=(1,), **kwargs):
    return db.set_prior_delivery_projects(list(ids), effective_day=DAY, confirmed=True,
                                          reason=REASON, **kwargs)


def test_known_prior_delivery_does_not_invent_date_or_count_current_correction(db):
    event_id = event(db, 1, '2026-10-26T02:00:00.000Z')
    with db.connect() as connection:
        connection.execute("UPDATE projects SET status='已交付',delivered_at='2026-10-26T02:00:00.000Z' WHERE id=1")
    before = {name: rows(db, name) for name in ('projects', 'delivery_events', 'delivery_event_actions')}
    before_time = datetime.now(timezone.utc)
    result = mark(db)
    after_time = datetime.now(timezone.utc)
    assert result == dict(changed_count=1, project_ids=[1], changed_project_ids=[1], active=True,
                          confirmed=True, effective_day='2026-10-26')
    summary = db.today_delivery_summary(DAY)
    assert (summary['project_count'], summary['repeat_count'], summary['event_count']) == (0, 1, 1)
    record, = db.delivery_history(DAY)['events']
    assert (record['id'], record['counting_role'], record['counted'], record['first_at']) == (event_id, 'repeat', False, None)
    assert record['prior_delivery'] is True and record['prior_delivery_reason'] == REASON
    assert summary['projects'][0]['prior_delivery'] is True
    assert summary['projects'][0]['prior_delivery_reason'] == REASON
    assert summary['projects'][0]['first_at'] is None
    assert {name: rows(db, name) for name in before} == before
    marker, = rows(db, 'project_delivery_history_markers')
    recorded = datetime.fromisoformat(marker['created_at'].replace('Z', '+00:00'))
    assert before_time.replace(microsecond=0) <= recorded <= after_time
    assert marker['effective_day'] == '2026-10-26' and marker['active'] == 1


def test_marker_without_events_protects_all_future_first_records_and_survives_restart(db):
    mark(db, (1, 2, 3))
    assert db.delivery_history()['events'] == []
    assert rows(db, 'delivery_events') == []
    db = Database(db.path)
    for pid in (1, 2, 3):
        event(db, pid, '2026-10-26T03:00:00.000Z')
    event(db, 1, '2026-11-01T02:00:00.000Z')
    history = db.delivery_history()
    assert (history['project_count'], history['repeat_count']) == (0, 4)
    assert all(row['prior_delivery'] and row['first_at'] is None for row in history['events'])
    assert len(rows(db, 'project_delivery_history_markers')) == 3


def test_real_first_before_effective_day_remains_counted_on_its_beijing_day(db):
    event(db, 1, '2026-10-25T15:59:59.999Z')
    event(db, 1, '2026-10-25T16:00:00.000Z')
    event(db, 2, '2026-10-25T16:00:00.000Z')
    mark(db, (1, 2))
    past = db.delivery_history(date(2026, 10, 25))
    assert past['project_count'] == 1
    first, = past['events']
    assert first['first_at'] == '2026-10-25T15:59:59.999Z'
    assert first['prior_delivery'] is False and first['prior_delivery_reason'] == ''
    today = db.delivery_history(DAY)
    assert (today['project_count'], today['repeat_count']) == (0, 2)
    project_one = next(row for row in today['events'] if row['project_id'] == 1)
    project_two = next(row for row in today['events'] if row['project_id'] == 2)
    assert project_one['first_at'] == first['first_at'] and project_one['prior_delivery'] is False
    assert project_two['first_at'] is None and project_two['prior_delivery'] is True


def test_void_and_restore_rerank_history_before_applying_unknown_date_rule(db):
    old = event(db, 1, '2026-10-25T01:00:00.000Z')
    later = event(db, 1, '2026-10-26T01:00:00.000Z')
    with db.connect() as connection:
        connection.execute("UPDATE projects SET status='已交付',delivered_at='2026-10-26T01:00:00.000Z' WHERE id=1")
    mark(db)
    assert db.delivery_history()['project_count'] == 1
    db.void_delivery_events([old], confirmed=True)
    assert db.get_project(1)['status'] == '待交付'
    history = db.delivery_history()
    assert (history['project_count'], history['repeat_count'], history['voided_count']) == (0, 1, 1)
    assert all(row['prior_delivery'] and row['first_at'] is None for row in history['events'])
    db.restore_delivery_events([old], confirmed=True)
    assert db.get_project(1)['status'] == '待交付'
    assert db.delivery_history()['project_count'] == 1
    db.void_delivery_events([old, later], confirmed=True)
    assert all(row['counting_role'] == 'voided' for row in db.delivery_history()['events'])
    assert all(row['prior_delivery'] for row in db.delivery_history()['events'])
    db.restore_delivery_events([later], confirmed=True)
    assert db.delivery_history()['project_count'] == 0
    assert db.delivery_history()['repeat_count'] == 1


def test_latest_marker_wins_and_cancel_is_reversible_without_changing_facts(db):
    event(db, 1, '2026-10-26T01:00:00.000Z')
    before = rows(db, 'delivery_events')
    mark(db)
    assert mark(db)['changed_count'] == 0
    assert mark(db, active=False)['changed_project_ids'] == [1]
    assert mark(db, active=False)['changed_count'] == 0
    record, = db.delivery_history()['events']
    assert record['counted'] is True and record['first_at'] == '2026-10-26T01:00:00.000Z'
    assert record['prior_delivery'] is False and record['prior_delivery_reason'] == ''
    assert mark(db)['changed_count'] == 1
    assert len(rows(db, 'project_delivery_history_markers')) == 3
    assert rows(db, 'delivery_events') == before
    assert Database(db.path).delivery_history()['project_count'] == 0


def test_effective_day_change_is_new_rule_but_reason_only_change_is_idempotent(db):
    event(db, 1, '2026-10-26T01:00:00.000Z')
    mark(db)
    unchanged = db.set_prior_delivery_projects([1], effective_day=DAY, confirmed=True, reason='重复确认')
    assert unchanged['changed_count'] == 0
    changed = db.set_prior_delivery_projects([1], effective_day=date(2026, 10, 27), confirmed=True, reason='调整规则生效日')
    assert changed['changed_count'] == 1
    assert db.delivery_history()['project_count'] == 1
    assert len(rows(db, 'project_delivery_history_markers')) == 2


def test_unconfirmed_request_and_cancel_without_marker_do_not_write(db):
    result = db.set_prior_delivery_projects([1], effective_day=DAY)
    assert result['confirmed'] is False and result['changed_count'] == 0
    assert mark(db, active=False)['changed_count'] == 0
    assert rows(db, 'project_delivery_history_markers') == []


@pytest.mark.parametrize('ids,options', [
    ([], {}), ([1] * 501, {}), ([1, 1], {}), ('1', {}), ([True], {}), ([0], {}),
    ([-1], {}), (['1'], {}), ([1.0], {}), ([1], {'active': 1}),
    ([1], {'confirmed': 1}), ([1], {'reason': None}), ([1], {'reason': '字' * 1001}),
    ([1], {'effective_day': '2026-10-26'}), ([1], {'effective_day': datetime(2026, 10, 26)}),
    ([1], {'effective_day': None}),
])
def test_invalid_marker_requests_are_rejected_before_any_write(db, ids, options):
    args = dict(effective_day=DAY, confirmed=True)
    args.update(options)
    with pytest.raises(ValueError):
        db.set_prior_delivery_projects(ids, **args)
    assert rows(db, 'project_delivery_history_markers') == []


def test_missing_project_and_write_failure_roll_back_whole_batch(db):
    with pytest.raises(ValueError):
        mark(db, (1, 999))
    assert rows(db, 'project_delivery_history_markers') == []
    with db.connect() as connection:
        connection.execute('''CREATE TRIGGER reject_second_marker BEFORE INSERT ON project_delivery_history_markers
            WHEN NEW.project_id=2 BEGIN SELECT RAISE(ABORT,'isolated marker failure'); END''')
    with pytest.raises(sqlite3.DatabaseError):
        mark(db, (1, 2))
    assert rows(db, 'project_delivery_history_markers') == []


@pytest.mark.parametrize('operation', ['update', 'delete'])
def test_marker_log_is_append_only(db, operation):
    mark(db)
    before = rows(db, 'project_delivery_history_markers')
    with pytest.raises(sqlite3.DatabaseError):
        with db.connect() as connection:
            if operation == 'update':
                connection.execute('UPDATE project_delivery_history_markers SET active=0')
            else:
                connection.execute('DELETE FROM project_delivery_history_markers')
    assert rows(db, 'project_delivery_history_markers') == before


def test_archived_project_marker_keeps_project_and_file_links_unchanged(db):
    db.bulk_update_projects([1], archived=True)
    before = db.get_project(1)
    mark(db)
    assert db.get_project(1) == before


def test_five_hundred_distinct_projects_are_accepted_in_one_batch(db):
    db.create_capture('隔离上限批次', '统计规则上限', [(f'新增隔离项目{i}', False) for i in range(497)])
    result = mark(db, range(1, 501))
    assert result['changed_count'] == 500
    assert len(rows(db, 'project_delivery_history_markers')) == 500
    assert mark(db, range(1, 501))['changed_count'] == 0


def test_filters_do_not_hide_real_earlier_first_and_make_rule_exclude_later_record(db):
    with db.connect() as connection:
        connection.execute('''INSERT INTO delivery_events(
            project_id,project_name,batch_name,kind,archive_path,occurred_at)
            VALUES (1,'早期名称','隔离批次','manual-files','隔离归档','2026-10-25T01:00:00.000Z')''')
    event(db, 1, '2026-10-26T01:00:00.000Z')
    mark(db)
    result = db.delivery_history(DAY, keyword='隔离项目1', project_id=1)
    record, = result['events']
    assert result['project_count'] == 0 and result['repeat_count'] == 1
    assert record['first_at'] == '2026-10-25T01:00:00.000Z'
    assert record['prior_delivery'] is False

