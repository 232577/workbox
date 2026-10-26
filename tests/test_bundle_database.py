"""组合包关系使用隔离 SQLite，绝不接触现有用户资料。"""

import sqlite3
from zipfile import ZipFile

import pytest

from app import WorkBox
from database import Database
from zip_manager import inspect_zip
from dataclasses import asdict


@pytest.fixture
def box(tmp_path):
    service = WorkBox(tmp_path / 'workbox.db')
    service.capture_message('1. 甲系统\n2. 乙系统', '组合批次')
    service.capture_message('丙系统', '其他批次')
    return service


def package(box, name='组合交付.zip', project_id=2):
    path = box.db.path.parent / name
    with ZipFile(path, 'w') as archive:
        archive.writestr('甲系统/readme.txt', '甲资料')
        archive.writestr('乙系统/readme.txt', '乙资料')
    return box.db.register_zip(asdict(inspect_zip(path)), project_id)[0]


def plan():
    return {'is_bundle': True, 'declared_count': 2,
            'member_names': ['甲系统', '乙系统'], 'project_ids': [1, 2],
            'matches': [{'folder_name': '甲系统', 'project_id': 1, 'project_name': '甲系统', 'batch_id': 1},
                        {'folder_name': '乙系统', 'project_id': 2, 'project_name': '乙系统', 'batch_id': 1}],
            'state': 'matched', 'reason': ''}


def test_shared_package_visible_once_from_both_projects_and_search(box):
    record = package(box)
    box.db.apply_zip_bundle(record['id'], plan())
    assert box.db.zip_project_ids(record['id']) == [1, 2]
    assert box.db.zip_belongs_to_project(record['id'], 1)
    for pid in (1, 2):
        project = box.get_project(pid)
        assert project['status'] == '待交付'
        assert [row['id'] for row in project['zips']] == [record['id']]
    rows = box.list_projects(query='组合交付')
    assert {row['id'] for row in rows} == {1, 2}
    assert {row['zip_count'] for row in rows} == {1}
    assert {row['zip_name'] for row in rows} == {'组合交付.zip'}
    rows = box.list_zips(query='甲系统')
    assert len(rows) == 1
    assert all(name in rows[0]['project_name'] for name in ('甲系统', '乙系统'))
    assert box.list_zips(query='组合批次')[0]['batch_name'] == '组合批次'
    assert box.db.get_zip(record['id'])['project_id'] == 2


def test_delivery_accepts_secondary_member_and_preserves_prior_delivery(box):
    old = package(box, '旧版.zip', 1)
    box.confirm_delivery(1, old['id'], confirmed=True)
    before = box.get_project(1)
    record = package(box)
    box.db.apply_zip_bundle(record['id'], plan())
    current = box.get_project(1)
    assert current['status'] == '已交付'
    assert current['delivered_zip_id'] == old['id']
    assert current['delivered_at'] == before['delivered_at']
    box.confirm_delivery(2, record['id'], confirmed=True)
    box.set_status(1, '待交付')
    box.confirm_delivery(1, record['id'], confirmed=True)
    assert box.get_project(1)['delivered_zip_id'] == record['id']


@pytest.mark.parametrize('mutation', ['rename', 'batch', 'archived', 'duplicate', 'count'])
def test_bundle_validation_is_atomic(box, mutation):
    record = package(box)
    candidate = plan()
    if mutation == 'rename':
        with box.db.connect() as connection:
            connection.execute("UPDATE projects SET name='甲已改名' WHERE id=1")
    elif mutation == 'batch':
        with box.db.connect() as connection:
            connection.execute('UPDATE projects SET batch_id=2 WHERE id=1')
    elif mutation == 'archived':
        box.bulk_update_projects([1], archived=True)
    elif mutation == 'duplicate':
        candidate['project_ids'] = [2, 2]
    else:
        candidate['declared_count'] = 3
    with pytest.raises((ValueError, KeyError)):
        box.db.apply_zip_bundle(record['id'], candidate)
    assert box.db.zip_project_ids(record['id']) == [2]
    assert box.db.get_zip_bundle(record['id']) is None
    assert box.get_project(1)['status'] == '待开发'


def test_needs_review_preserves_explicit_link_and_states(box):
    record = package(box)
    candidate = plan()
    candidate.update(state='needs-review', project_ids=[], matches=[], reason='名称存在歧义')
    box.db.apply_zip_bundle(record['id'], candidate)
    assert box.db.zip_project_ids(record['id']) == [2]
    assert box.get_project(1)['status'] == '待开发'
    assert box.db.get_zip_bundle(record['id'])['reason'] == '名称存在歧义'


def test_needs_review_keeps_old_records_but_blocks_approval_and_delivery(box):
    record = package(box)
    snapshots(box, record)
    with box.db.connect() as connection:
        box.db._approve_zip_for_projects(connection, record['id'])
    box.confirm_delivery(2, record['id'], confirmed=True)
    delivered = box.get_project(2)
    candidate = plan()
    candidate.update(state='needs-review', project_ids=[], matches=[], reason='组合包尚待核对')
    box.db.apply_zip_bundle(record['id'], candidate)
    assert box.db.approved_zip_for_project(2) is None
    assert box.db.zip_project_ids(record['id']) == [2]
    with pytest.raises(ValueError):
        with box.db.connect() as connection:
            box.db._approve_zip_for_projects(connection, record['id'])
    with pytest.raises(ValueError):
        box.confirm_delivery(2, record['id'], confirmed=True)
    current = box.get_project(2)
    assert current['status'] == delivered['status'] == '已交付'
    assert current['delivered_zip_id'] == delivered['delivered_zip_id'] == record['id']
    assert current['delivered_at'] == delivered['delivered_at']
    with box.db.connect() as connection:
        assert connection.execute('SELECT zip_id FROM zip_project_approvals WHERE project_id=2').fetchone()[0] == record['id']
        assert connection.execute('SELECT approved FROM zip_snapshots WHERE zip_id=?', (record['id'],)).fetchone()[0] == 1


@pytest.mark.parametrize('operation', ['confirm', 'complete', 'approve'])
def test_needs_review_blocks_pending_operations_and_recovers_when_matched(box, operation):
    record = package(box)
    snapshots(box, record)
    candidate = plan()
    candidate.update(state='needs-review', project_ids=[], matches=[], reason='项目数量尚待核对')
    box.db.apply_zip_bundle(record['id'], candidate)
    with pytest.raises(ValueError):
        if operation == 'confirm':
            box.confirm_delivery(2, record['id'], confirmed=True)
        elif operation == 'complete':
            box.db.complete_delivery(2, record['id'], record)
        else:
            with box.db.connect() as connection:
                box.db._approve_zip_for_projects(connection, record['id'])
    assert box.get_project(2)['status'] == '待交付'
    assert box.get_project(2)['delivered_zip_id'] is None
    box.db.apply_zip_bundle(record['id'], plan())
    with box.db.connect() as connection:
        box.db._approve_zip_for_projects(connection, record['id'])
    assert box.db.approved_zip_for_project(2) == record['id']
    box.confirm_delivery(2, record['id'], confirmed=True)
    assert box.get_project(2)['status'] == '已交付'


def test_shared_bundle_refuses_single_relink(box):
    record = package(box)
    box.db.apply_zip_bundle(record['id'], plan())
    with pytest.raises(ValueError):
        box.link_zip(record['id'], 3)
    assert box.db.zip_project_ids(record['id']) == [1, 2]


def test_needs_review_bundle_cannot_be_downgraded_by_single_relink(box):
    record = package(box)
    candidate = plan()
    candidate.update(state='needs-review', project_ids=[], matches=[], reason='组合包存在歧义')
    box.db.apply_zip_bundle(record['id'], candidate)
    with pytest.raises(ValueError):
        box.link_zip(record['id'], 3)
    assert box.db.zip_project_ids(record['id']) == [2]
    assert box.db.get_zip_bundle(record['id'])['state'] == 'needs-review'
    assert box.get_project(3)['status'] == '待开发'


def test_confirmed_member_cannot_be_removed(box):
    record = package(box)
    box.db.apply_zip_bundle(record['id'], plan())
    box.confirm_delivery(1, record['id'], confirmed=True)
    candidate = plan()
    candidate.update(declared_count=1, member_names=['乙系统'], project_ids=[2], matches=[candidate['matches'][1]])
    with pytest.raises(ValueError):
        box.db.apply_zip_bundle(record['id'], candidate)
    assert box.db.zip_project_ids(record['id']) == [1, 2]


def test_cross_batch_matching_is_rejected_even_with_current_names(box):
    record = package(box)
    candidate = plan()
    candidate.update(member_names=['甲系统', '丙系统'], project_ids=[1, 3],
                     matches=[candidate['matches'][0], {'folder_name': '丙系统', 'project_id': 3,
                                                        'project_name': '丙系统', 'batch_id': 2}])
    with pytest.raises(ValueError):
        box.db.apply_zip_bundle(record['id'], candidate)
    assert box.db.zip_project_ids(record['id']) == [2]
    assert box.get_project(1)['status'] == '待开发'


def test_delivery_rechecks_membership_after_hashing(box):
    record = package(box)
    box.db.apply_zip_bundle(record['id'], plan())

    def remove_member(done, total):
        candidate = plan()
        candidate.update(declared_count=1, member_names=['乙系统'], project_ids=[2], matches=[candidate['matches'][1]])
        box.db.apply_zip_bundle(record['id'], candidate)

    with pytest.raises(ValueError):
        box.confirm_delivery(1, record['id'], confirmed=True, progress=remove_member)
    assert box.get_project(1)['status'] == '待交付'
    assert box.get_project(1)['delivered_zip_id'] is None


def snapshots(box, *records):
    from snapshot_manager import SCHEMA
    with box.db.connect() as connection:
        connection.executescript(SCHEMA)
        source_id = connection.execute("INSERT INTO zip_sources(source_key,source_path) VALUES ('fixture','fixture')").lastrowid
        for record in records:
            connection.execute('INSERT INTO zip_snapshots(zip_id,source_id,source_path,approved) VALUES (?,?,?,1)', (record['id'], source_id, record['path']))


def test_project_approval_is_independent_after_shared_bundle(box):
    shared = package(box)
    box.db.apply_zip_bundle(shared['id'], plan())
    newer = package(box, '甲新版.zip', 1)
    snapshots(box, shared, newer)
    with box.db.connect() as connection:
        box.db._approve_zip_for_projects(connection, shared['id'])
    assert box.db.approved_zip_for_project(1) == shared['id']
    assert box.db.approved_zip_for_project(2) == shared['id']
    with box.db.connect() as connection:
        box.db._approve_zip_for_projects(connection, newer['id'])
    assert box.db.approved_zip_for_project(1) == newer['id']
    assert box.db.approved_zip_for_project(2) == shared['id']


def test_legacy_approval_migration_preserves_old_rows_and_runs_once(box):
    old = package(box, '旧版.zip', 1)
    snapshots(box, old)
    with box.db.connect() as connection:
        box.db.migrate_zip_approvals(connection)
    assert box.db.approved_zip_for_project(1) == old['id']
    with box.db.connect() as connection:
        connection.execute('DELETE FROM zip_project_approvals WHERE project_id=1')
        box.db.migrate_zip_approvals(connection)
        assert connection.execute('SELECT approved FROM zip_snapshots').fetchone()[0] == 1
    assert box.db.approved_zip_for_project(1) is None
    assert Database(box.db.path).counts() == {'projects': 3, 'batches': 2, 'zips': 1}


def test_existing_version_one_database_extends_without_losing_rows(tmp_path):
    path = tmp_path / 'old.db'
    db = Database(path)
    db.create_capture('旧项目', '原批次', [('旧项目', False)])
    with sqlite3.connect(path) as connection:
        connection.execute('DROP VIEW IF EXISTS zip_project_links')
        for table in ('zip_bundle_members', 'zip_bundles', 'zip_project_approvals', 'zip_approval_migrations'):
            connection.execute(f'DROP TABLE IF EXISTS {table}')
    restored = Database(path)
    assert restored.get_project(1)['name'] == '旧项目'
    assert restored.zip_project_ids(999) == []
    with restored.connect() as connection:
        assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert connection.execute('PRAGMA user_version').fetchone()[0] == 1
