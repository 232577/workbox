"""项目合并的隔离数据库回归；不操作真实工作区。"""
from datetime import date
import sqlite3

import pytest

from database import Database
from workspace_manager import SCHEMA as WORKSPACE_SCHEMA
from snapshot_manager import SCHEMA as SNAPSHOT_SCHEMA
from delivery_archive import SCHEMA as DELIVERY_ARCHIVE_SCHEMA
from managed_archive import JOURNAL as MANAGED_SCHEMA


@pytest.fixture
def db(tmp_path):
    value = Database(tmp_path / 'merge.db')
    value.set_default_project_type(None)
    value.create_capture('隔离合并', '原批次', [('原项目', False), ('目标项目', False), ('第三项目', False)])
    with value.connect() as connection:
        connection.executescript(WORKSPACE_SCHEMA + SNAPSHOT_SCHEMA + DELIVERY_ARCHIVE_SCHEMA)
    return value


def merge(db, source=1, target=2, operation='merge-1', status='待交付'):
    with db.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        return db._merge_project_records(connection, source, target, operation_id=operation,
            result_status=status, snapshot={'source_id': source, 'target_id': target})


def event(db, project, when):
    with db.connect() as connection:
        return connection.execute('''INSERT INTO delivery_events
            (project_id,project_name,batch_name,kind,archive_path,occurred_at)
            VALUES (?,?,'原批次','manual-files','不可变归档',?)''',
            (project, f'交付时名称{project}', when)).lastrowid


def test_merge_hides_source_keeps_history_and_counts_first_per_canonical_project(db):
    first = event(db, 1, '2026-10-01T08:00:00Z')
    second = event(db, 2, '2026-10-02T08:00:00Z')
    with db.connect() as connection:
        original = [dict(row) for row in connection.execute('SELECT * FROM delivery_events ORDER BY id')]
    merge(db)
    assert [row['id'] for row in db.list_projects()] == [3, 2]
    assert db.counts()['projects'] == 2
    assert db.list_batches()[0]['project_count'] == 2
    assert db.get_project(1)['merged_into_id'] == 2
    assert db.get_project(2)['merged_into_id'] is None
    history = db.delivery_history()
    assert history['project_count'] == 1 and history['event_count'] == 2
    assert {row['project_id'] for row in history['events']} == {2}
    assert {row['original_project_id'] for row in history['events']} == {1, 2}
    assert {row['project_name'] for row in history['events']} == {'目标项目'}
    assert {row['original_project_name'] for row in history['events']} == {'交付时名称1', '交付时名称2'}
    assert next(row for row in history['events'] if row['id'] == first)['counted']
    assert not next(row for row in history['events'] if row['id'] == second)['counted']
    assert db.delivery_history(date(2026, 10, 2))['project_count'] == 0
    assert len(db.delivery_history(project_id=2)['events']) == 2
    assert len(db.delivery_history(project_id=1)['events']) == 2
    assert len(db.delivery_history(date(2026, 10, 1), project_id=1)['events']) == 1
    with db.connect() as connection:
        assert [dict(row) for row in connection.execute('SELECT * FROM delivery_events ORDER BY id')] == original


def test_chain_aliases_and_void_original_event_affect_canonical_target(db):
    first = event(db, 1, '2026-10-01T08:00:00Z')
    event(db, 2, '2026-10-02T08:00:00Z')
    merge(db)
    merge(db, 2, 3, 'merge-2', '已交付')
    assert db.canonical_project_id(1) == 3
    assert db.project_family_ids(3) == [1, 2, 3]
    assert db.project_family_ids(1) == [1, 2, 3]
    assert {(row['project_id'], row['alias_name']) for row in db.list_project_aliases()} >= {(3, '原项目'), (3, '目标项目')}
    result = db.void_delivery_events([first], confirmed=True)
    assert result['reset_project_ids'] == [3]
    assert db.get_project(3)['status'] == '待交付'
    assert db.delivery_history(date(2026, 10, 2))['project_count'] == 1


@pytest.mark.parametrize('operation', [
    lambda db: db.set_status(1, '开发中'),
    lambda db: db.set_urgent(1, True),
    lambda db: db.bulk_update_projects([1], archived=False),
    lambda db: db.update_project_details(1, '旧任务', None),
    lambda db: db.set_project_types([1], None),
    lambda db: db.set_project_delivery_counting([1], False, confirmed=True),
    lambda db: db.set_prior_delivery_projects([1], effective_day=date(2026, 9, 1), confirmed=True),
    lambda db: db.delete_project(1, confirmed=True),
    lambda db: db.restore_deleted_project(1),
])
def test_merged_source_cannot_be_written_by_old_queued_actions(db, operation):
    merge(db)
    with pytest.raises(ValueError, match='合并'):
        operation(db)


@pytest.mark.parametrize('operation', [
    lambda db: db.set_status(1, '开发中'),
    lambda db: db.bulk_update_projects([1], archived=True),
    lambda db: db.set_project_delivery_counting([1], False, confirmed=True),
    lambda db: db.set_prior_delivery_projects([1], effective_day=date(2026, 9, 1), confirmed=True),
    lambda db: db.delete_project(1, confirmed=True),
    lambda db: db.restore_deleted_project(1),
])
def test_failed_filesystem_rollback_blocks_current_project_writes(db, operation):
    with db.connect() as connection:
        connection.execute("INSERT INTO project_change_blocks(project_id,operation_id,reason) VALUES (1,'failed-rename','目录待恢复')")
    assert db.get_project(1)['block_reason'] == '目录待恢复'
    with pytest.raises(ValueError, match='目录待恢复'):
        operation(db)


def test_merge_preserves_target_type_combines_details_and_inherits_exclusions_and_prior_marker(db):
    source_type = db.create_project_type('源类型')
    target_type = db.create_project_type('目标类型')
    db.set_project_types([1], source_type['id'])
    db.set_project_types([2], target_type['id'])
    db.update_project_details(1, '源备注', '2026-10-01')
    db.update_project_details(2, '目标备注', '2026-11-01')
    db.set_urgent(1, True)
    db.set_project_delivery_counting([1], False, confirmed=True, reason='原资格')
    db.set_prior_delivery_projects([1], effective_day=date(2026, 9, 1), confirmed=True)
    event(db, 1, '2026-10-01T08:00:00Z')
    event(db, 2, '2026-10-02T08:00:00Z')
    merge(db)
    target = db.get_project(2)
    assert target['project_type_id'] == target_type['id']
    assert target['urgent'] and target['due_date'] == '2026-10-01'
    assert all(text in target['notes'] for text in ('目标备注', '原项目', '源备注'))
    assert next(row for row in db.list_project_types() if row['id'] == source_type['id'])['project_count'] == 0
    assert next(row for row in db.list_project_types() if row['id'] == target_type['id'])['project_count'] == 1
    rows = db.delivery_history()['events']
    assert all(not row['included_in_workload'] and row['prior_delivery'] for row in rows)
    assert {row['project_type_id'] for row in rows} == {target_type['id']}
    db.set_prior_delivery_projects([2], effective_day=date(2026, 9, 1), active=False, confirmed=True)
    db.set_project_delivery_counting([2], True, confirmed=True)
    assert db.delivery_history()['project_count'] == 1


def test_merge_transfers_mutable_links_without_changing_paths_or_old_workspace(db):
    with db.connect() as connection:
        connection.execute("INSERT INTO project_workspaces VALUES (1,'D:/isolated/source','D:/isolated/source',1)")
        connection.execute("INSERT INTO work_files(project_id,source_path,path,filename,size,sha256,mtime_ns) VALUES (1,'external','old-path','a.txt',1,?,1)", ('a' * 64,))
        zip_id = connection.execute("INSERT INTO zips(project_id,path,filename,size,sha256,mtime_ns) VALUES (1,'old-zip-path','a.zip',1,?,1)", ('b' * 64,)).lastrowid
        connection.execute('INSERT INTO zip_project_approvals VALUES (1,?)', (zip_id,))
        connection.execute("INSERT INTO zip_project_file_counts(zip_id,project_id,file_count,decided_status,rule) VALUES (?,1,4,'已交付','zip-file-count-v1')", (zip_id,))
        connection.execute('INSERT INTO zip_file_count_policies VALUES (?,1,1,4)', (zip_id,))
        connection.execute("INSERT INTO zip_sources(source_key,source_path,project_id) VALUES ('old-key','old-source-path',1)")
        connection.execute("INSERT INTO project_delivery_archives(project_id,path,source_root,signature,manifest) VALUES (1,'archive','source-root',?,'[]')", ('c' * 64,))
    merge(db)
    with db.connect() as connection:
        assert connection.execute('SELECT project_id,path FROM work_files').fetchone()[:] == (2, 'old-path')
        assert connection.execute('SELECT project_id,path FROM zips').fetchone()[:] == (2, 'old-zip-path')
        assert connection.execute('SELECT project_id,source_key FROM zip_sources').fetchone()[:] == (2, 'old-key')
        assert connection.execute('SELECT project_id,path FROM project_workspaces').fetchone()[:] == (1, 'D:/isolated/source')
        assert connection.execute('SELECT project_id FROM project_delivery_archives').fetchone()[0] == 2
        assert connection.execute('SELECT zip_id FROM zip_project_approvals WHERE project_id=2').fetchone()[0] == zip_id
        assert connection.execute('SELECT project_id FROM zip_project_file_counts').fetchone()[0] == 2


def test_merge_rejects_common_zip_and_oversized_notes_atomically(db):
    with db.connect() as connection:
        zip_id = connection.execute("INSERT INTO zips(project_id,path,filename,size,sha256,mtime_ns) VALUES (1,'archive','a.zip',1,?,1)", ('a' * 64,)).lastrowid
        connection.execute('INSERT INTO zip_bundle_members VALUES (?,2)', (zip_id,))
    with pytest.raises(ValueError, match='ZIP'):
        merge(db)
    assert db.canonical_project_id(1) == 1
    with db.connect() as connection:
        connection.execute('DELETE FROM zip_bundle_members')
    db.update_project_details(1, '甲' * 6000, None)
    db.update_project_details(2, '乙' * 6000, None)
    with pytest.raises(ValueError, match='10000'):
        merge(db)
    assert db.canonical_project_id(1) == 1


def test_merge_requires_caller_transaction_and_append_only_mapping(db):
    with db.connect() as connection:
        with pytest.raises(ValueError, match='事务'):
            db._merge_project_records(connection, 1, 2, operation_id='no-transaction',
                result_status='待交付', snapshot={})
    merge(db)
    with db.connect() as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute('DELETE FROM project_merges')
    with pytest.raises(ValueError, match='合并'):
        merge(db, 2, 1, 'cycle')


@pytest.mark.parametrize('boundary', ['archived', 'deleted'])
@pytest.mark.parametrize('project_id', [1, 2])
def test_merge_refuses_archive_and_deletion_boundaries(db, boundary, project_id):
    if boundary == 'archived':
        db.bulk_update_projects([project_id], archived=True)
    else:
        db.delete_project(project_id, confirmed=True)
    with pytest.raises(ValueError, match='恢复'):
        merge(db)
    assert db.canonical_project_id(1) == 1
    assert db.get_project(2)['name'] == '目标项目'


def test_caller_rollback_undoes_merge_and_every_inherited_action(db):
    db.set_project_delivery_counting([1], False, confirmed=True)
    db.set_prior_delivery_projects([1], effective_day=date(2026, 9, 1), confirmed=True)
    with db.connect() as connection:
        before = {table: [tuple(row) for row in connection.execute(f'SELECT * FROM {table} ORDER BY rowid')]
                  for table in ('projects', 'project_details', 'project_merges', 'project_name_aliases',
                                'project_delivery_count_actions', 'project_delivery_history_markers')}
    with pytest.raises(RuntimeError):
        with db.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            db._merge_project_records(connection, 1, 2, operation_id='rollback', result_status='待交付', snapshot={})
            raise RuntimeError('隔离模拟提交失败')
    with db.connect() as connection:
        for table, rows in before.items():
            assert [tuple(row) for row in connection.execute(f'SELECT * FROM {table} ORDER BY rowid')] == rows


def test_merge_preserves_original_management_facts_and_earliest_marker(db):
    event_id = event(db, 1, '2026-10-01T08:00:00Z')
    db.void_delivery_events([event_id], confirmed=True, reason='原撤销')
    db.restore_delivery_events([event_id], confirmed=True, reason='原恢复')
    db.set_project_delivery_counting([1], False, confirmed=True)
    db.set_project_delivery_counting([2], False, confirmed=True)
    db.set_prior_delivery_projects([1], effective_day=date(2026, 9, 10), confirmed=True)
    db.set_prior_delivery_projects([2], effective_day=date(2026, 9, 20), confirmed=True)
    tables = ('delivery_events', 'delivery_event_actions', 'project_delivery_count_actions', 'project_delivery_history_markers')
    with db.connect() as connection:
        original = {table: [dict(row) for row in connection.execute(f'SELECT * FROM {table} ORDER BY id')] for table in tables}
    merge(db)
    with db.connect() as connection:
        for table in tables:
            rows = [dict(row) for row in connection.execute(f'SELECT * FROM {table} ORDER BY id')]
            assert rows[:len(original[table])] == original[table]
        assert connection.execute('SELECT COUNT(*) FROM project_delivery_count_actions').fetchone()[0] == 2
        assert connection.execute('SELECT effective_day FROM project_delivery_history_markers WHERE project_id=2 ORDER BY id DESC').fetchone()[0] == '2026-09-10'


def test_merge_without_desktop_extension_tables_is_supported(tmp_path):
    store = Database(tmp_path / 'basic.db')
    store.create_capture('最小数据库', '隔离', [('源', False), ('目标', False)])
    merge(store)
    assert store.canonical_project_id(1) == 2


def test_delivered_merge_reuses_existing_delivery_marker_without_fabricating_event(db):
    event_id = event(db, 1, '2026-10-01T08:00:00Z')
    with db.connect() as connection:
        connection.execute("UPDATE projects SET status='已交付',delivered_at='2026-10-01T08:00:00Z' WHERE id=1")
    merge(db, status='已交付')
    target = db.get_project(2)
    assert target['status'] == '已交付' and target['delivered_at'] == '2026-10-01T08:00:00Z'
    assert [row['id'] for row in db.delivery_history()['events']] == [event_id]


@pytest.mark.parametrize('table,terminal', [('zip_snapshot_attempts', 'registered'),
    ('managed_transfers', 'complete'), ('project_delivery_archive_attempts', 'registered')])
@pytest.mark.parametrize('pid', [1, 2])
def test_pending_archive_operations_block_merge_without_rewriting_attempts(db, table, terminal, pid):
    with db.connect() as connection:
        connection.executescript(MANAGED_SCHEMA)
        if table == 'zip_snapshot_attempts':
            source_id = connection.execute("INSERT INTO zip_sources(source_key,source_path,project_id) VALUES ('key','source',?)", (pid,)).lastrowid
            connection.execute("INSERT INTO zip_snapshot_attempts(id,source_id,project_id,target,metadata,state) VALUES ('pending',?,?, 'target','{}','copied')", (source_id, pid))
        elif table == 'managed_transfers':
            connection.execute("INSERT INTO managed_transfers(id,source,destination,metadata,project_id,state) VALUES ('pending','source','target','{}',?,'registered')", (pid,))
        else:
            connection.execute("INSERT INTO project_delivery_archive_attempts(id,project_id,source_root,signature,path,manifest,state) VALUES ('pending',?,'source',?,'target','[]','prepared')", (pid, 'a' * 64))
        before = [tuple(row) for row in connection.execute(f'SELECT * FROM {table}')]
    with pytest.raises(ValueError, match='未完成'):
        merge(db)
    with db.connect() as connection:
        assert [tuple(row) for row in connection.execute(f'SELECT * FROM {table}')] == before
        connection.execute(f'UPDATE {table} SET state=?', (terminal,))
    merge(db)
    assert db.canonical_project_id(1) == 2


def test_cross_batch_multi_project_bundle_blocks_merge_but_same_batch_is_allowed(db):
    with db.connect() as connection:
        other_batch = connection.execute("INSERT INTO batches(name) VALUES ('目标独立批次')").lastrowid
        connection.execute('UPDATE projects SET batch_id=? WHERE id=2', (other_batch,))
        zip_id = connection.execute("INSERT INTO zips(project_id,path,filename,size,sha256,mtime_ns) VALUES (1,'bundle','原项目-2.zip',1,?,1)", ('a' * 64,)).lastrowid
        connection.execute("INSERT INTO zip_bundles VALUES (?,2,'[\"原项目\",\"第三项目\"]','matched','')", (zip_id,))
        connection.execute('INSERT INTO zip_bundle_members VALUES (?,1)', (zip_id,))
        connection.execute('INSERT INTO zip_bundle_members VALUES (?,3)', (zip_id,))
    with pytest.raises(ValueError, match='跨批次多项目合包'):
        merge(db)
    with db.connect() as connection:
        connection.execute('UPDATE projects SET batch_id=1 WHERE id=2')
        db._assert_project_merge_ready(connection, 1, 2)
    merge(db)
    assert db.zip_project_ids(zip_id) == [2, 3]


def test_readiness_check_is_read_only_and_supports_absent_extension_tables(tmp_path):
    store = Database(tmp_path / 'readiness.db')
    store.create_capture('隔离', '批次', [('源', False), ('目标', False)])
    with store.connect() as connection:
        total = connection.total_changes
        store._assert_project_merge_ready(connection, 1, 2)
        assert connection.total_changes == total and not connection.in_transaction


def test_retained_source_approval_is_not_counted_as_a_current_version_owner(db):
    from snapshot_manager import SnapshotManager
    with db.connect() as connection:
        zip_id = connection.execute("INSERT INTO zips(project_id,path,filename,size,sha256,mtime_ns) VALUES (1,'bundle','原项目-2.zip',1,?,1)", ('a' * 64,)).lastrowid
        connection.execute("INSERT INTO zip_bundles VALUES (?,2,'[\"原项目\",\"第三项目\"]','matched','')", (zip_id,))
        connection.execute('INSERT INTO zip_bundle_members VALUES (?,1)', (zip_id,))
        connection.execute('INSERT INTO zip_bundle_members VALUES (?,3)', (zip_id,))
        source_id = connection.execute("INSERT INTO zip_sources(source_key,source_path,project_id) VALUES ('key','source',1)").lastrowid
        connection.execute("INSERT INTO zip_snapshots(zip_id,source_id,source_path) VALUES (?,?,'source')", (zip_id, source_id))
        connection.executemany('INSERT INTO zip_project_approvals VALUES (?,?)', [(1, zip_id), (3, zip_id)])
    merge(db)
    manager = SnapshotManager.__new__(SnapshotManager)
    manager.db = db
    version, = manager.list_versions()
    assert version['approved_count'] == version['project_count'] == 2
    with db.connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM zip_project_approvals').fetchone()[0] == 3
        connection.execute('DELETE FROM zip_project_approvals WHERE project_id IN (2,3)')
    assert not manager.list_versions()[0]['approved']
    assert manager.list_versions()[0]['approved_count'] == 0


def test_pending_bundle_snapshot_blocks_secondary_member_merge(db):
    import json
    with db.connect() as connection:
        source_id = connection.execute("INSERT INTO zip_sources(source_key,source_path,project_id) VALUES ('key','source',3)").lastrowid
        metadata = json.dumps({'bundle': {'project_ids': [3, 1], 'state': 'matched'}})
        connection.execute("INSERT INTO zip_snapshot_attempts(id,source_id,project_id,target,metadata,state) VALUES ('pending',?,3,'target',?,'copied')", (source_id, metadata))
    with pytest.raises(ValueError, match='未完成'):
        merge(db)
