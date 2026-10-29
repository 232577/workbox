"""个人任务量资格独立于真实交付与项目状态。"""
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest

from app import WorkBox
from database import Database


@pytest.fixture
def db(tmp_path):
    result = Database(tmp_path / 'workload.db')
    result.create_capture('隔离', '隔离批', [('甲系统', False), ('乙系统', False)])
    return result


def event(db, pid=1, when='2026-10-22T15:59:59.999Z'):
    with db.connect() as con:
        con.execute("UPDATE projects SET status='已交付',delivered_at=? WHERE id=?", (when, pid))
        return con.execute('''INSERT INTO delivery_events
            (project_id,project_name,batch_name,kind,archive_path,occurred_at)
            VALUES (?,'隔离项目','隔离批','manual-files','隔离归档',?)''', (pid, when)).lastrowid


def rows(db, table):
    with db.connect() as con:
        return [dict(row) for row in con.execute('SELECT * FROM '+table+' ORDER BY 1')]


def test_exclusion_keeps_facts_state_and_future_deliveries_and_restores_original_day(db):
    first = event(db)
    before = rows(db, 'projects'), rows(db, 'delivery_events')
    assert db.delivery_history()['events'][0]['included_in_workload'] is True
    result = db.set_project_delivery_counting([1], False, confirmed=True, reason='代他人交付')
    assert result == dict(changed_count=1, project_ids=[1], changed_project_ids=[1], included=False, confirmed=True)
    assert (rows(db, 'projects'), rows(db, 'delivery_events')) == before
    record = db.delivery_history()['events'][0]
    assert record['counting_role'] == 'first' and not record['counted']
    assert record['count_exclusion_reason'] == '代他人交付' and record['count_action_id']
    event(db, when='2026-10-23T08:00:00.000Z')
    assert db.delivery_history()['project_count'] == 0
    assert not db.today_delivery_summary(date(2026, 10, 23))['projects'][0]['included_in_workload']
    assert not Database(db.path).delivery_history()['events'][0]['included_in_workload']
    db.set_project_delivery_counting([1], True, confirmed=True)
    assert db.today_delivery_summary(date(2026, 10, 22))['project_count'] == 1
    assert db.today_delivery_summary(date(2026, 10, 23))['project_count'] == 0
    assert db.delivery_history()['events'][-1]['id'] == first
    assert db.delivery_history(date(2026, 10, 23), keyword='隔离')['project_count'] == 0


def test_qualification_is_independent_of_event_void_restore_and_unknown_history(db):
    first = event(db)
    db.set_project_delivery_counting([1], False, confirmed=True)
    db.void_delivery_events([first], confirmed=True)
    db.restore_delivery_events([first], confirmed=True)
    assert db.get_project(1)['status'] == '待交付'
    assert db.delivery_history()['project_count'] == 0
    db.set_prior_delivery_projects([1], effective_day=date(2026, 10, 22), confirmed=True)
    db.set_project_delivery_counting([1], True, confirmed=True)
    assert db.delivery_history()['project_count'] == 0
    db.set_prior_delivery_projects([1], effective_day=date(2026, 10, 22), active=False, confirmed=True)
    assert db.delivery_history()['project_count'] == 1


def test_archived_deleted_and_no_event_projects_can_change_count_qualification(db):
    event(db)
    db.bulk_update_projects([1], archived=True)
    db.delete_project(2, confirmed=True)
    before = rows(db, 'projects'), rows(db, 'project_details'), rows(db, 'project_deletions')
    db.set_project_delivery_counting([1, 2], False, confirmed=True)
    assert (rows(db, 'projects'), rows(db, 'project_details'), rows(db, 'project_deletions')) == before
    event(db, pid=2)
    assert db.delivery_history()['project_count'] == 0


@pytest.mark.parametrize('ids,options', [([], {}), ([1, 1], {}), ([True], {}), ([0], {}),
    ([1], {'included': 0}), ([1], {'confirmed': 1}), ([1], {'reason': 'x'*1001}),
    ([1], {'reason': None}), (list(range(1, 502)), {})])
def test_invalid_requests_are_read_only(db, ids, options):
    kwargs = dict(included=False, confirmed=True) | options
    with pytest.raises(ValueError):
        db.set_project_delivery_counting(ids, **kwargs)
    assert rows(db, 'project_delivery_count_actions') == []


def test_cancel_idempotency_atomic_failure_concurrency_and_append_only(db):
    assert db.set_project_delivery_counting([1], False)['changed_count'] == 0
    assert db.set_project_delivery_counting([1], True, confirmed=True)['changed_count'] == 0
    with pytest.raises((ValueError, KeyError)):
        db.set_project_delivery_counting([1, 999], False, confirmed=True)
    with db.connect() as con:
        con.execute("CREATE TRIGGER fail_count BEFORE INSERT ON project_delivery_count_actions WHEN NEW.project_id=2 BEGIN SELECT RAISE(ABORT,'isolated'); END")
    with pytest.raises(sqlite3.DatabaseError):
        db.set_project_delivery_counting([1, 2], False, confirmed=True)
    assert rows(db, 'project_delivery_count_actions') == []
    with db.connect() as con:
        con.execute('DROP TRIGGER fail_count')
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: db.set_project_delivery_counting([1], False, confirmed=True), range(2)))
    assert sum(result['changed_count'] for result in results) == 1
    for sql in ('UPDATE project_delivery_count_actions SET included=1', 'DELETE FROM project_delivery_count_actions'):
        with pytest.raises(sqlite3.DatabaseError), db.connect() as con:
            con.execute(sql)


def test_service_forwards_counting_api(tmp_path):
    service = WorkBox(tmp_path / 'service.db')
    service.capture_message('隔离项目', '隔离批')
    assert service.set_project_delivery_counting([1], False, confirmed=True)['changed_count'] == 1


def test_five_hundred_project_changes_are_atomic_and_reason_only_is_idempotent(db):
    db.create_capture('隔离批量', '隔离批量', [('项目'+str(i), False) for i in range(3, 501)])
    selected = list(range(1, 501))
    assert db.set_project_delivery_counting(selected, False, confirmed=True)['changed_count'] == 500
    before = rows(db, 'project_delivery_count_actions')
    assert db.set_project_delivery_counting(selected, False, confirmed=True, reason='重复说明')['changed_count'] == 0
    assert rows(db, 'project_delivery_count_actions') == before
    assert db.set_project_delivery_counting(selected, True, confirmed=True)['changed_count'] == 500
