"""归档类型规则的匿名数据库回归。"""
import importlib
import hashlib
import json

import pytest

from database import Database
from delivery_archive import SCHEMA as MATERIAL_SCHEMA


def backend():
    return importlib.import_module('archive_classification')


@pytest.fixture
def db(tmp_path):
    result = Database(tmp_path / 'anonymous.db')
    result.create_capture('匿名项目甲\n匿名项目乙', '匿名批次', [('匿名项目甲', False), ('匿名项目乙', False)])
    with result.connect() as connection:
        connection.executescript(MATERIAL_SCHEMA)
    return result


def zip_event(db, count, *, project_id=1, hook=False, path=None):
    with db.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        zip_id = connection.execute('''INSERT INTO zips(project_id,path,filename,size,sha256,mtime_ns)
            VALUES (?,?,?,1,?,0)''', (project_id, path or f'Z:/anonymous/{count}-{project_id}.zip', 'archive.zip', 'a' * 64)).lastrowid
        if count is not None:
            connection.execute('''INSERT INTO zip_project_file_counts
                (zip_id,project_id,file_count,decided_status,rule) VALUES (?,?,?,'已交付','zip-file-count-v1')''',
                (zip_id, project_id, count))
        event_id = connection.execute('''INSERT INTO delivery_events(project_id,project_name,batch_name,kind,zip_id,archive_path)
            VALUES (?,'匿名项目','匿名批次','manual-zip',?,'Z:/anonymous/archive.zip')''', (project_id, zip_id)).lastrowid
        if hook:
            backend().classify_new_delivery(connection, event_id)
        return event_id


def type_id(db, name):
    return next(row['id'] for row in db.list_project_types(include_inactive=True) if row['name'] == name)


def current(db, project_id=1):
    with db.connect() as connection:
        row = connection.execute('SELECT type_id FROM project_type_links WHERE project_id=?', (project_id,)).fetchone()
        return row[0] if row else None


def manual(db, project_id, selected):
    before = {project_id: current(db, project_id)}
    with db.connect() as connection:
        backend().record_manual_type_selection(connection, [project_id], before, selected)
    db.set_project_types([project_id], selected)


def test_backend_is_available():
    import importlib.util
    assert importlib.util.find_spec('archive_classification') is not None


def test_policy_defaults_off_and_does_not_reenable_inactive_types(db):
    high = type_id(db, '可运行')
    db.set_project_type_active(high, False)
    classifier = backend().ArchiveClassification(db)
    assert classifier.policy()['enabled'] is False
    assert classifier.policy()['minimum_files'] == 4
    assert classifier.policy()['high_type_id'] is None
    assert classifier.policy()['low_type_id'] == type_id(db, '生成材料')
    assert next(row for row in db.list_project_types(include_inactive=True) if row['id'] == high)['active'] is False


@pytest.mark.parametrize('count,expected', [(0, '生成材料'), (3, '生成材料'), (4, '可运行')])
def test_preview_and_apply_use_exact_threshold_without_business_mutations(db, count, expected):
    zip_event(db, count)
    classifier = backend().ArchiveClassification(db)
    manual(db, 1, None)
    before = db.delivery_history()
    states = [(p['id'], p['status']) for p in db.list_projects()]
    preview = classifier.preview()
    row = next(row for row in preview['rows'] if row['project_id'] == 1)
    assert row['count'] == count and row['new_type_name'] == expected and row['eligible']
    with pytest.raises(ValueError, match='确认'):
        classifier.apply(preview['token'])
    result = classifier.apply(preview['token'], confirmed=True)
    assert result['changed_project_ids'] == [1]
    assert current(db) == type_id(db, expected)
    assert classifier.policy()['enabled']
    assert [(p['id'], p['status']) for p in db.list_projects()] == states
    after = db.delivery_history()
    assert [(e['id'], e['kind'], e['voided'], e['archive_path']) for e in before['events']] == [
        (e['id'], e['kind'], e['voided'], e['archive_path']) for e in after['events']]


def test_latest_valid_event_is_not_sum_and_void_restore_changes_preview(db):
    first = zip_event(db, 4)
    second = zip_event(db, 3)
    classifier = backend().ArchiveClassification(db)
    assert classifier.preview()['rows'][0]['event_id'] == second
    assert classifier.preview()['rows'][0]['count'] == 3
    db.void_delivery_events([second], confirmed=True)
    assert classifier.preview()['rows'][0]['event_id'] == first
    db.restore_delivery_events([second], confirmed=True)
    assert classifier.preview()['rows'][0]['event_id'] == second


def test_unknown_and_protected_rows_are_skipped(db):
    zip_event(db, None)
    zip_event(db, 4, project_id=2)
    with db.connect() as connection:
        connection.execute("INSERT INTO project_change_blocks VALUES (2,'anonymous','待恢复')")
    classifier = backend().ArchiveClassification(db)
    preview = classifier.preview()
    assert preview['changed_count'] == 0
    assert len(preview['skipped']) == 2
    assert preview['rows'][0]['count'] is None
    assert all(row['skip_reason'] for row in preview['skipped'])


@pytest.mark.parametrize('mutation', ['type', 'catalog', 'facts', 'action', 'manual'])
def test_preview_rejects_stale_sources_or_intent(db, mutation):
    event_id = zip_event(db, 4)
    classifier = backend().ArchiveClassification(db)
    preview = classifier.preview()
    if mutation == 'type':
        db.set_project_types([1], None)
    elif mutation == 'catalog':
        db.rename_project_type(type_id(db, '可运行'), '运行交付')
    elif mutation == 'facts':
        with db.connect() as connection:
            connection.execute('UPDATE zip_project_file_counts SET file_count=5')
    elif mutation == 'manual':
        manual(db, 1, current(db))
    else:
        db.void_delivery_events([event_id], confirmed=True)
    with pytest.raises(ValueError, match='过期'):
        classifier.apply(preview['token'], confirmed=True)


def test_apply_noop_and_undo_are_idempotent_and_preserve_same_value_manual_intent(db):
    zip_event(db, 4)
    zip_event(db, 4, project_id=2)
    classifier = backend().ArchiveClassification(db)
    result = classifier.apply(classifier.preview()['token'], confirmed=True)
    assert result['changed_count'] == 2
    assert classifier.apply(classifier.preview()['token'], confirmed=True)['changed_count'] == 0
    manual(db, 1, current(db))
    with pytest.raises(ValueError, match='确认'):
        classifier.undo(result['operation_id'])
    undone = classifier.undo(result['operation_id'], confirmed=True)
    assert undone['changed_project_ids'] == [2]
    assert current(db) == type_id(db, '可运行')
    assert current(db, 2) == type_id(db, '生成材料')
    assert classifier.undo(result['operation_id'], confirmed=True)['changed_count'] == 0


def test_automatic_only_new_events_idempotent_and_in_original_transaction(db):
    old = zip_event(db, 4)
    classifier = backend().ArchiveClassification(db)
    classifier.set_policy(True)
    with db.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        assert backend().classify_new_delivery(connection, old)['changed_count'] == 0
    assert current(db) == type_id(db, '生成材料')
    new = zip_event(db, 4, hook=True, path='Z:/anonymous/new.zip')
    assert current(db) == type_id(db, '可运行')
    manual(db, 1, type_id(db, '生成材料'))
    with db.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        assert backend().classify_new_delivery(connection, new)['changed_count'] == 0
    assert current(db) == type_id(db, '生成材料')
    with pytest.raises(RuntimeError):
        with db.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            event_id = connection.execute("INSERT INTO delivery_events(project_id,project_name,batch_name,kind,zip_id,archive_path) SELECT project_id,project_name,batch_name,kind,zip_id,archive_path FROM delivery_events WHERE id=?", (new,)).lastrowid
            backend().classify_new_delivery(connection, event_id)
            raise RuntimeError('匿名回滚')
    assert current(db) == type_id(db, '生成材料')


def test_disabled_and_unknown_events_never_replayed_after_enable_or_fact_fix(db):
    classifier = backend().ArchiveClassification(db)
    disabled = zip_event(db, 4, hook=True)
    classifier.set_policy(True)
    unknown = zip_event(db, None, hook=True)
    with db.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        for event_id in (disabled, unknown):
            assert backend().classify_new_delivery(connection, event_id)['changed_count'] == 0
    assert current(db) == type_id(db, '生成材料')


def test_material_manifest_count_and_combined_zip_per_project(db):
    classifier = backend().ArchiveClassification(db)
    manifest = [{'path': f'{number}.txt', 'size': 1, 'sha256': 'b' * 64} for number in range(4)]
    signature = hashlib.sha256(json.dumps([[v['path'], v['sha256']] for v in manifest], ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    with db.connect() as connection:
        connection.execute('''INSERT INTO project_delivery_archives(project_id,path,source_root,signature,manifest)
            VALUES (1,'Z:/anonymous/material','Z:/anonymous/source',?,?)''', (signature, json.dumps(manifest)))
        connection.execute('''INSERT INTO delivery_events(project_id,project_name,batch_name,kind,archive_path,archive_signature)
            VALUES (1,'匿名项目甲','匿名批次','manual-files','Z:/anonymous/material',?)''', (signature,))
    zipped = zip_event(db, 3, project_id=2)
    with db.connect() as connection:
        zip_id = connection.execute('SELECT zip_id FROM delivery_events WHERE id=?', (zipped,)).fetchone()[0]
        connection.execute("INSERT INTO zip_bundles VALUES (?,2,'[]','matched','')", (zip_id,))
        connection.execute('INSERT INTO zip_bundle_members VALUES (?,1)', (zip_id,))
        connection.execute("INSERT INTO zip_project_file_counts(zip_id,project_id,file_count,decided_status,rule) VALUES (?,1,40,'已交付','zip-file-count-v1')", (zip_id,))
    rows = classifier.preview()['rows']
    assert [(row['project_id'], row['count']) for row in rows] == [(1, 4), (2, 3)]


def test_preview_reads_one_snapshot_without_changing_any_business_or_audit_rows(db):
    zip_event(db, 4)
    classifier = backend().ArchiveClassification(db)
    def dump():
        with db.connect() as connection:
            return '\n'.join(connection.iterdump())
    before = dump()
    assert classifier.preview()['token'] == classifier.preview()['token']
    assert dump() == before


@pytest.mark.parametrize('state', ['archived', 'deleted', 'inactive-target'])
def test_visibility_and_target_catalog_protection(db, state):
    zip_event(db, 4)
    classifier = backend().ArchiveClassification(db)
    if state == 'inactive-target':
        db.set_project_type_active(type_id(db, '可运行'), False)
    else:
        with db.connect() as connection:
            if state == 'archived':
                connection.execute("INSERT INTO project_details(project_id,archived_at) VALUES (1,'anonymous')")
            else:
                connection.execute('INSERT INTO project_deletions(project_id) VALUES (1)')
    row = classifier.preview()['rows'][0]
    assert row['eligible'] is False and row['skip_reason']
    assert classifier.apply(classifier.preview()['token'], confirmed=True, enable_future=False)['changed_count'] == 0
    assert classifier.policy()['enabled'] is False


def test_undo_does_not_overwrite_different_current_type_or_restore_inactive_type(db):
    zip_event(db, 4)
    classifier = backend().ArchiveClassification(db)
    result = classifier.apply(classifier.preview()['token'], confirmed=True)
    db.set_project_type_active(type_id(db, '生成材料'), False)
    assert classifier.undo(result['operation_id'], confirmed=True)['changed_count'] == 0
    db.set_project_type_active(type_id(db, '生成材料'), True)
    manual(db, 1, None)
    result = classifier.apply(classifier.preview()['token'], confirmed=True)
    # 绕过人工入口也不能覆盖已不同的类型；原NULL类型也支持精确恢复。
    with db.connect() as connection:
        connection.execute('DELETE FROM project_type_links WHERE project_id=1')
    assert classifier.undo(result['operation_id'], confirmed=True)['changed_count'] == 0
    result = classifier.apply(classifier.preview()['token'], confirmed=True)
    assert classifier.undo(result['operation_id'], confirmed=True)['changed_count'] == 1
    assert current(db) is None


def test_automatic_restored_event_is_skipped_and_audits_are_append_only(db):
    classifier = backend().ArchiveClassification(db)
    classifier.set_policy(True)
    event_id = zip_event(db, 4)
    db.void_delivery_events([event_id], confirmed=True)
    db.restore_delivery_events([event_id], confirmed=True)
    with db.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        result = backend().classify_new_delivery(connection, event_id)
        assert result['changed_count'] == 0
        assert result['skipped']
    result = classifier.apply(classifier.preview()['token'], confirmed=True)
    with db.connect() as connection:
        with pytest.raises(Exception, match='仅允许追加'):
            connection.execute('DELETE FROM archive_classification_actions WHERE operation_id=?', (result['operation_id'],))


def test_canonical_project_uses_latest_effective_family_event(db):
    first = zip_event(db, 4)
    second = zip_event(db, 3, project_id=2)
    with db.connect() as connection:
        connection.execute("INSERT INTO project_merges(source_id,target_id,operation_id,snapshot_json) VALUES (1,2,'anonymous-merge','{}')")
    classifier = backend().ArchiveClassification(db)
    rows = classifier.preview()['rows']
    assert len(rows) == 1 and rows[0]['project_id'] == 2
    assert rows[0]['event_id'] == second and rows[0]['count'] == 3
    db.void_delivery_events([second], confirmed=True)
    row = classifier.preview()['rows'][0]
    assert row['event_id'] == first and row['count'] == 4


def test_hooks_before_module_installation_do_not_create_schema(db):
    with db.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        assert backend().classify_new_delivery(connection, 1)['changed_count'] == 0
        assert backend().record_manual_type_selection(connection, [1], {1: None}, None) is None
        assert connection.execute("SELECT 1 FROM sqlite_master WHERE name='archive_classification_policy'").fetchone() is None


def test_undo_does_not_overwrite_later_automatic_aba_result(db):
    zip_event(db, 4)
    classifier = backend().ArchiveClassification(db)
    initial = classifier.apply(classifier.preview()['token'], confirmed=True)
    zip_event(db, 3, hook=True, path='Z:/anonymous/later-low.zip')
    zip_event(db, 4, hook=True, path='Z:/anonymous/later-high.zip')
    undone = classifier.undo(initial['operation_id'], confirmed=True)
    assert undone['changed_count'] == 0
    assert current(db) == type_id(db, '可运行')
    assert undone['skipped']
