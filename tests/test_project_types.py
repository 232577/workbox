"""自定义业务分类只影响标签和筛选，不改变项目事实。"""
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from app import WorkBox
from database import Database


@pytest.fixture
def db(tmp_path):
    result = Database(tmp_path / 'types.db')
    result.set_default_project_type(None)
    result.create_capture('隔离', '隔离批', [('甲系统', False), ('乙系统', False)])
    return result


def test_create_assign_filter_rename_disable_restore_and_clear(db):
    assert [row['name'] for row in db.list_project_types()] == ['生成材料', '可运行']
    assert db.get_project(1)['project_type_id'] is None
    assert db.get_project(1)['project_type_name'] == ''
    before = db.get_project(1)['name'], db.get_project(1)['status']
    kind = db.create_project_type('  软件业务  ')
    assert kind == dict(id=3, name='软件业务', active=True, project_count=0)
    assert db.set_project_types([1], kind['id'])['changed_count'] == 1
    assert db.set_project_types([1], kind['id'])['changed_count'] == 0
    assert [p['id'] for p in db.list_projects(type_id=kind['id'])] == [1]
    assert [p['id'] for p in db.list_projects(type_id=0)] == [2]
    assert db.list_project_types()[-1]['project_count'] == 1
    db.rename_project_type(kind['id'], '系统业务')
    assert db.get_project(1)['project_type_name'] == '系统业务'
    db.set_project_type_active(kind['id'], False)
    assert [row['name'] for row in db.list_project_types()] == ['生成材料', '可运行']
    assert db.list_project_types(include_inactive=True)[-1]['active'] is False
    assert db.get_project(1)['project_type_name'] == '系统业务'
    assert len(db.list_projects(type_id=kind['id'])) == 1
    with pytest.raises(ValueError):
        db.set_project_types([2], kind['id'])
    db.set_project_type_active(kind['id'], True)
    assert db.set_project_types([1, 2], None)['changed_count'] == 1
    assert (db.get_project(1)['name'], db.get_project(1)['status']) == before
    assert Database(db.path).get_project(1)['project_type_id'] is None


@pytest.mark.parametrize('name', ['', ' '*5, 'x'*41, '未分类', ' 未分类 ', None, 123])
def test_invalid_type_names_are_rejected(db, name):
    with pytest.raises(ValueError):
        db.create_project_type(name)
    assert [row['name'] for row in db.list_project_types(include_inactive=True)] == ['生成材料', '可运行']


def test_unicode_casefold_duplicate_and_inactive_names_remain_reserved(db):
    first = db.create_project_type('Straße')
    with pytest.raises(ValueError):
        db.create_project_type('STRASSE')
    second = db.create_project_type('二类')
    with pytest.raises(ValueError):
        db.rename_project_type(second['id'], 'strasse')
    db.set_project_type_active(first['id'], False)
    with pytest.raises(ValueError):
        db.create_project_type('strasse')
    with ThreadPoolExecutor(max_workers=2) as pool:
        def create(_):
            try:
                return db.create_project_type('并发')
            except ValueError:
                return None
        assert sum(value is not None for value in pool.map(create, range(2))) == 1


@pytest.mark.parametrize('boundary', ['archived', 'deleted', 'missing'])
def test_batch_assignment_rejects_whole_selection_for_project_boundaries(db, boundary):
    kind = db.create_project_type('业务')
    if boundary == 'archived':
        db.bulk_update_projects([2], archived=True)
    elif boundary == 'deleted':
        db.delete_project(2, confirmed=True)
    with pytest.raises((ValueError, KeyError)):
        db.set_project_types([1, 999 if boundary == 'missing' else 2], kind['id'])
    assert db.get_project(1)['project_type_id'] is None


def test_assignment_failure_rolls_back_and_links_protect_type(db):
    kind = db.create_project_type('业务')
    with db.connect() as con:
        con.execute("CREATE TRIGGER fail_type BEFORE INSERT ON project_type_links WHEN NEW.project_id=2 BEGIN SELECT RAISE(ABORT,'isolated'); END")
    with pytest.raises(sqlite3.DatabaseError):
        db.set_project_types([1, 2], kind['id'])
    assert db.get_project(1)['project_type_id'] is None
    with db.connect() as con:
        con.execute('DROP TRIGGER fail_type')
    db.set_project_types([1], kind['id'])
    with pytest.raises(sqlite3.IntegrityError), db.connect() as con:
        con.execute('DELETE FROM project_types WHERE id=?', (kind['id'],))


@pytest.mark.parametrize('value', [True, -1, '1', 1.5])
def test_invalid_type_filter_and_assignment_ids(db, value):
    with pytest.raises(ValueError):
        db.list_projects(type_id=value)
    with pytest.raises(ValueError):
        db.set_project_types([1], value)


def test_service_forwards_all_type_apis(tmp_path):
    service = WorkBox(tmp_path / 'service.db')
    service.capture_message('隔离项目', '隔离批')
    kind = service.create_project_type('业务')
    service.set_project_types([1], kind['id'])
    assert service.list_projects(type_id=kind['id'])[0]['project_type_name'] == '业务'
    service.rename_project_type(kind['id'], '业务二')
    service.set_project_type_active(kind['id'], False)
    assert service.list_project_types(include_inactive=True)[-1]['name'] == '业务二'


@pytest.mark.parametrize('ids', [[], [1, 1], [True], [0], list(range(1, 502))])
def test_invalid_project_selection_does_not_assign_any_type(db, ids):
    kind = db.create_project_type('业务')
    with pytest.raises(ValueError):
        db.set_project_types(ids, kind['id'])
    assert db.list_project_types()[-1]['project_count'] == 0


def test_type_validation_missing_ids_and_renaming_preserve_all_project_rows(db):
    kind = db.create_project_type('业务')
    with db.connect() as con:
        before = [tuple(row) for row in con.execute('SELECT * FROM projects ORDER BY id')]
    for method, args in ((db.rename_project_type, (True, '名称')),
                         (db.set_project_type_active, (kind['id'], 1)),
                         (db.list_project_types, ())):
        kwargs = {'include_inactive': 1} if method == db.list_project_types else {}
        with pytest.raises(ValueError):
            method(*args, **kwargs)
    for method, args in ((db.rename_project_type, (999, '名称')),
                         (db.set_project_type_active, (999, True)),
                         (db.set_project_types, ([1], 999))):
        with pytest.raises(KeyError):
            method(*args)
    db.set_project_types([1], kind['id'])
    db.rename_project_type(kind['id'], '新名称')
    with db.connect() as con:
        assert [tuple(row) for row in con.execute('SELECT * FROM projects ORDER BY id')] == before


def test_type_counts_keep_archived_assignments_and_exclude_deleted_projects(db):
    kind = db.create_project_type('业务')
    db.set_project_types([1, 2], kind['id'])
    db.bulk_update_projects([1], archived=True)
    assert db.list_project_types()[-1]['project_count'] == 2
    db.delete_project(2, confirmed=True)
    assert db.list_project_types()[-1]['project_count'] == 1
    assert db.get_project(2)['project_type_name'] == '业务'
    db.restore_deleted_project(2)
    assert db.list_project_types()[-1]['project_count'] == 2


def test_five_hundred_project_assignment_changes_and_clearing(db):
    db.create_capture('隔离批量', '隔离批量', [('项目'+str(i), False) for i in range(3, 501)])
    kind = db.create_project_type('批量')
    selected = list(range(1, 501))
    assert db.set_project_types(selected, kind['id'])['changed_count'] == 500
    assert db.list_project_types()[-1]['project_count'] == 500
    assert db.set_project_types(selected, None)['changed_count'] == 500
    assert len(db.list_projects(type_id=0)) == 500


def upgrade_fixture(db):
    """只在隔离数据库还原尚未运行预设迁移的类型字典。"""
    with db.connect() as con:
        con.execute('DELETE FROM project_type_defaults')
        con.execute('DELETE FROM project_types')
        con.execute('DROP TABLE IF EXISTS project_type_migrations')


def test_presets_migrate_once_and_remain_ordinary_editable_types(db):
    presets = db.list_project_types()
    assert [row['name'] for row in presets] == ['生成材料', '可运行']
    db.rename_project_type(presets[0]['id'], '个人材料')
    db.set_project_type_active(presets[1]['id'], False)
    before = db.list_project_types(include_inactive=True)
    assert Database(db.path).list_project_types(include_inactive=True) == before
    assert len(before) == 2 and before[1]['active'] is False
    assert all(row['project_type_id'] is None for row in db.list_projects())
    with db.connect() as con:
        assert json.loads(con.execute('SELECT seeded_types FROM project_type_migrations WHERE name=?',
                          ('editable-project-type-presets-v1',)).fetchone()[0]) == {
            str(presets[0]['id']): '生成材料', str(presets[1]['id']): '可运行'}


def test_upgrade_reuses_normalized_name_without_changing_existing_types_or_facts(db):
    upgrade_fixture(db)
    with db.connect() as con:
        con.execute("INSERT INTO project_types(id,name,normalized_name,active) VALUES (41,'旧材料名','材料',0)")
        con.execute("INSERT INTO project_types(id,name,normalized_name) VALUES (42,'自定义','自定义')")
        con.execute('INSERT INTO project_type_links(project_id,type_id) VALUES (1,42)')
        tables = ('projects', 'project_type_links', 'project_delivery_count_actions', 'delivery_events')
        before = {table: [tuple(row) for row in con.execute('SELECT * FROM '+table)] for table in tables}
        existing = [tuple(row) for row in con.execute('SELECT * FROM project_types ORDER BY id')]
    reopened = Database(db.path)
    with reopened.connect() as con:
        assert [tuple(row) for row in con.execute('SELECT * FROM project_types WHERE id IN (41,42) ORDER BY id')] == existing
        assert {table: [tuple(row) for row in con.execute('SELECT * FROM '+table)] for table in tables} == before
        assert con.execute('SELECT COUNT(*) FROM project_type_migrations').fetchone()[0] == 2
        assert json.loads(con.execute('SELECT seeded_types FROM project_type_migrations WHERE name=?',
                          ('editable-project-type-presets-v1',)).fetchone()[0]) == {'43': '生成材料', '44': '可运行'}
    assert [row['name'] for row in reopened.list_project_types(include_inactive=True)] == ['旧材料名', '自定义', '生成材料', '可运行']


def test_concurrent_upgrade_creates_each_preset_and_marker_only_once(db):
    upgrade_fixture(db)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: Database(db.path).list_project_types(), range(8)))
    assert all([row['name'] for row in rows] == ['生成材料', '可运行'] for rows in results)
    with db.connect() as con:
        assert con.execute('SELECT COUNT(*) FROM project_type_migrations').fetchone()[0] == 2
        assert json.loads(con.execute('SELECT seeded_types FROM project_type_migrations WHERE name=?',
                          ('editable-project-type-presets-v1',)).fetchone()[0]) == {'1': '生成材料', '2': '可运行'}


@pytest.mark.parametrize('failure', ['second-type', 'marker'])
def test_preset_migration_failure_rolls_back_all_type_rows_and_marker(db, failure):
    upgrade_fixture(db)
    with db.connect() as con:
        if failure == 'second-type':
            con.execute("CREATE TRIGGER fail_preset BEFORE INSERT ON project_types WHEN NEW.normalized_name='可运行' BEGIN SELECT RAISE(ABORT,'isolated migration failure'); END")
        else:
            con.execute('CREATE TABLE project_type_migrations(name TEXT PRIMARY KEY,seeded_types TEXT NOT NULL)')
            con.execute("CREATE TRIGGER fail_preset BEFORE INSERT ON project_type_migrations BEGIN SELECT RAISE(ABORT,'isolated migration failure'); END")
    with pytest.raises(sqlite3.DatabaseError):
        Database(db.path)
    with db.connect() as con:
        assert con.execute('SELECT COUNT(*) FROM project_types').fetchone()[0] == 0
        assert con.execute('SELECT COUNT(*) FROM project_type_migrations').fetchone()[0] == 0
        con.execute('DROP TRIGGER fail_preset')
    assert [row['name'] for row in Database(db.path).list_project_types()] == ['生成材料', '可运行']
