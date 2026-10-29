"""类型统计按当前分类汇总，保留历史首次认定及真实交付事实。"""
from datetime import date
from contextlib import contextmanager
import sqlite3

import pytest

from database import Database


DAY = date(2026, 10, 23)


def test_type_catalog_and_delivery_rows_share_one_read_snapshot(db, monkeypatch):
    kind = db.create_project_type('分类前')
    db.set_project_types([1], kind['id'])
    event(db, 1)
    original_connect = db.connect
    renamed = False

    @contextmanager
    def interleaved_connect():
        with original_connect() as con:
            def interleave(sql):
                nonlocal renamed
                if not renamed and sql.startswith('SELECT id,name FROM project_types WHERE active=1'):
                    with sqlite3.connect(db.path) as writer:
                        writer.execute('UPDATE project_types SET name=?,normalized_name=? WHERE id=?',
                                       ('分类后', '分类后', kind['id']))
                    renamed = True
            con.set_trace_callback(interleave)
            yield con

    monkeypatch.setattr(db, 'connect', interleaved_connect)
    result = db.delivery_history(DAY)
    assert renamed
    current = next(row for row in result['type_counts'] if row['type_id'] == kind['id'])
    assert current['type_name'] == result['events'][0]['project_type_name'] == '分类前'
    assert counts(result)['分类前'] == 1
    assert counts(db.delivery_history(DAY))['分类后'] == 1


@pytest.fixture
def db(tmp_path):
    result = Database(tmp_path / 'delivery-types.db')
    result.create_capture('隔离类型统计', '隔离批', [(name, False) for name in ('甲系统', '乙系统', '丙系统', '丁系统')])
    return result


def event(db, pid, when='2026-10-23T08:00:00.000Z', name='隔离交付'):
    with db.connect() as con:
        return con.execute('''INSERT INTO delivery_events
            (project_id,project_name,batch_name,kind,archive_path,occurred_at)
            VALUES (?,?,'隔离批','manual-files','隔离归档',?)''', (pid, name, when)).lastrowid


def counts(result):
    rows = result['type_counts']
    assert len({row['type_id'] for row in rows}) == len(rows)
    assert sum(row['project_count'] for row in rows) == result['project_count']
    return {row['type_name']: row['project_count'] for row in rows}


def test_empty_counts_include_all_active_types_and_unassigned_but_no_empty_inactive(db):
    unused = db.create_project_type('未使用')
    db.set_project_type_active(unused['id'], False)
    assert counts(db.delivery_history(DAY)) == {'材料': 0, '可运行': 0, '未分类': 0}
    assert counts(db.today_delivery_summary(DAY)) == {'材料': 0, '可运行': 0, '未分类': 0}


def test_counts_unique_first_projects_include_inactive_assigned_and_unassigned(db):
    material, runnable = db.list_project_types()
    custom = db.create_project_type('其他')
    db.set_project_types([1], material['id'])
    db.set_project_types([2], runnable['id'])
    db.set_project_types([4], custom['id'])
    db.set_project_type_active(custom['id'], False)
    for pid in range(1, 5):
        event(db, pid)
        event(db, pid)
    result = db.delivery_history(DAY)
    assert counts(result) == {'材料': 1, '可运行': 1, '其他': 1, '未分类': 1}
    assert result['project_count'] == 4 and result['event_count'] == 8
    assert next(row for row in result['type_counts'] if row['type_id'] == custom['id'])['active'] is False
    assert counts(db.today_delivery_summary(DAY)) == counts(result)


def test_reclassification_renaming_and_disable_regroup_without_rewriting_facts(db):
    material, runnable = db.list_project_types()
    first = event(db, 1)
    with db.connect() as con:
        before = [tuple(row) for row in con.execute('SELECT * FROM delivery_events')]
    assert counts(db.delivery_history(DAY))['未分类'] == 1
    db.set_project_types([1], material['id'])
    assert counts(db.delivery_history(DAY))['材料'] == 1
    db.set_project_types([1], runnable['id'])
    db.rename_project_type(runnable['id'], '运行软件')
    db.set_project_type_active(runnable['id'], False)
    result = db.delivery_history(DAY)
    assert counts(result) == {'材料': 0, '运行软件': 1, '未分类': 0}
    row = result['events'][0]
    assert (row['id'], row['project_type_id'], row['project_type_name'], row['project_type_active']) == (first, runnable['id'], '运行软件', False)
    assert row['first_at'] == '2026-10-23T08:00:00.000Z'
    with db.connect() as con:
        assert [tuple(row) for row in con.execute('SELECT * FROM delivery_events')] == before
    assert Database(db.path).delivery_history(DAY) == result


def test_counts_keep_lifetime_first_before_day_keyword_and_project_filters(db):
    material = db.list_project_types()[0]
    db.set_project_types([1], material['id'])
    first = event(db, 1, '2026-10-22T15:59:59.999Z', '旧名称')
    event(db, 1, '2026-10-22T16:00:00.000Z', '新名称')
    event(db, 2, name='新名称')
    assert counts(db.delivery_history(DAY, keyword='新名称')) == {'材料': 0, '可运行': 0, '未分类': 1}
    assert counts(db.delivery_history(DAY, keyword='新名称', project_id=1))['材料'] == 0
    assert counts(db.delivery_history(date(2026, 10, 22)))['材料'] == 1
    assert counts(db.delivery_history(keyword='旧名称'))['材料'] == 1
    db.void_delivery_events([first], confirmed=True)
    assert counts(db.delivery_history(DAY, keyword='新名称'))['材料'] == 1
    db.restore_delivery_events([first], confirmed=True)
    assert counts(db.delivery_history(DAY))['材料'] == 0


def test_eligibility_revocation_and_prior_markers_preserve_existing_count_rules(db):
    material = db.list_project_types()[0]
    db.set_project_types([1, 2], material['id'])
    first = event(db, 1)
    event(db, 2)
    db.set_project_delivery_counting([1], False, confirmed=True)
    assert counts(db.delivery_history(DAY))['材料'] == 1
    db.void_delivery_events([first], confirmed=True)
    db.restore_delivery_events([first], confirmed=True)
    assert counts(db.delivery_history(DAY))['材料'] == 1
    db.set_project_delivery_counting([1], True, confirmed=True)
    assert counts(db.delivery_history(DAY))['材料'] == 2
    db.set_prior_delivery_projects([1], effective_day=DAY, confirmed=True)
    assert counts(db.delivery_history(DAY))['材料'] == 1
    db.set_prior_delivery_projects([1], effective_day=DAY, active=False, confirmed=True)
    assert counts(db.delivery_history(DAY))['材料'] == 2
    assert db.get_project(1)['status'] == '待交付'


def test_current_archive_delete_metadata_and_summary_do_not_change_counts_or_edit_boundaries(db):
    material = db.list_project_types()[0]
    db.set_project_types([1, 2], material['id'])
    event(db, 1)
    event(db, 2)
    event(db, 3)
    db.bulk_update_projects([1], archived=True)
    db.delete_project(2, confirmed=True)
    result = db.delivery_history(DAY)
    assert counts(result) == {'材料': 2, '可运行': 0, '未分类': 1}
    summary = db.today_delivery_summary(DAY)
    for collection in (result['events'], summary['projects']):
        by_id = {row['project_id']: row for row in collection}
        assert (by_id[1]['project_archived'], by_id[1]['project_deleted']) == (True, False)
        assert (by_id[2]['project_archived'], by_id[2]['project_deleted']) == (True, True)
        assert by_id[3]['project_type_id'] is None and by_id[3]['project_type_active'] is None
        for row in collection:
            assert type(row['project_archived']) is bool and type(row['project_deleted']) is bool
    for pid in (1, 2):
        with pytest.raises(ValueError):
            db.set_project_types([pid], None)
    db.bulk_update_projects([1], archived=False)
    db.restore_deleted_project(2)
    assert counts(db.today_delivery_summary(DAY)) == counts(summary)
