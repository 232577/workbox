"""默认类型只用于新项目，偏好和项目关系均在各自事务内保存。"""
import json
import sqlite3
from zipfile import ZipFile

import pytest

from app import WorkBox
from database import Database
from desktop_service import DesktopService
from parser import ParsedProject
from workspace_manager import WorkspaceManager


def test_fresh_default_and_factory_origin_do_not_skip_setup(tmp_path):
    service = WorkBox(tmp_path / 'fresh.db')
    kind = next(t for t in service.list_project_types() if t['name'] == '生成材料')
    assert service.get_default_project_type() == kind['id']
    assert WorkspaceManager(service).setup_required()
    assert service.capture_message('新项目', '新批次')['project_ids'] == [1]
    assert service.get_project(1)['project_type_id'] == kind['id']


@pytest.mark.parametrize('entry', ['capture', 'text', 'review', 'file', 'zip', 'bundle', 'folder'])
@pytest.mark.parametrize('preference', ['factory', 'custom', 'none'])
def test_every_creation_entry_uses_saved_default(tmp_path, entry, preference):
    service = DesktopService(tmp_path / 'data' / 'workbox.db')
    if preference != 'factory':
        service.set_default_project_type(service.create_project_type('个人业务')['id']
                                         if preference == 'custom' else None)
    expected = service.get_default_project_type()
    if entry == 'capture':
        service.capture_message('新项目', '新批次')
    elif entry == 'text':
        service.receive_text('1. 新项目\n2. 另一个项目')
    elif entry == 'review':
        receipt = service.receive_text('这里是描述性内容。')
        service.resolve_text(receipt['receipt_id'], [ParsedProject('新项目')])
    else:
        source = tmp_path / ('新项目-2.zip' if entry == 'bundle' else '新项目.zip' if entry == 'zip'
                             else '新项目' if entry == 'folder' else '新项目.docx')
        if entry in ('zip', 'bundle'):
            with ZipFile(source, 'w') as package:
                for name in (['新项目/a.txt', '另一个项目/b.txt'] if entry == 'bundle' else ['a.txt']):
                    package.writestr(name, 'isolated')
        elif entry == 'folder':
            source.mkdir()
            (source / '操作手册.docx').write_bytes(b'isolated')
        else:
            source.write_bytes(b'isolated')
        service.receive_path(source)
    rows = service.list_projects()
    assert len(rows) == (2 if entry in ('text', 'bundle') else 1)
    assert all(row['project_type_id'] == expected for row in rows)
    assert DesktopService(service.db.path).get_default_project_type() == expected


def test_default_rename_disable_restore_and_clear_are_persistent(tmp_path):
    db = Database(tmp_path / 'settings.db')
    first = db.get_default_project_type()
    db.rename_project_type(first, '我的材料')
    assert Database(db.path).get_default_project_type() == first
    db.create_capture('原项目', '批次', [('原项目', False)])
    db.set_project_type_active(first, False)
    assert Database(db.path).get_default_project_type() is None
    assert db.get_project(1)['project_type_id'] == first
    db.set_project_type_active(first, True)
    assert db.get_default_project_type() is None
    other = db.create_project_type('自定义')['id']
    assert db.set_default_project_type(other) == other
    assert Database(db.path).get_default_project_type() == other
    assert db.set_default_project_type(None) is None
    assert Database(db.path).get_default_project_type() is None
    assert [t['name'] for t in db.list_project_types()] == ['我的材料', '可运行', '自定义']


@pytest.mark.parametrize('invalid', [True, False, 0, -1, '1', 1.5])
def test_default_rejects_invalid_ids_without_changes(tmp_path, invalid):
    db = Database(tmp_path / 'invalid.db')
    before = db.get_default_project_type()
    with pytest.raises(ValueError):
        db.set_default_project_type(invalid)
    assert db.get_default_project_type() == before
    with pytest.raises(KeyError):
        db.set_default_project_type(999)
    db.set_project_type_active(before, False)
    with pytest.raises(ValueError):
        db.set_default_project_type(before)
    assert db.get_default_project_type() is None


@pytest.mark.parametrize('entry', ['capture', 'text', 'review', 'file'])
def test_default_link_failure_rolls_back_project_and_receipt(tmp_path, entry):
    service = DesktopService(tmp_path / 'data' / 'atomic.db')
    receipt = service.receive_text('这里是描述性内容。') if entry == 'review' else None
    with service.db.connect() as con:
        con.execute("CREATE TRIGGER reject_default BEFORE INSERT ON project_type_links BEGIN SELECT RAISE(ABORT,'isolated default failure'); END")
    with pytest.raises(sqlite3.DatabaseError, match='isolated default failure'):
        if entry == 'capture':
            service.capture_message('新项目', '新批次')
        elif entry == 'text':
            service.receive_text('1. 新项目')
        elif entry == 'review':
            service.resolve_text(receipt['receipt_id'], [ParsedProject('新项目')])
        else:
            source = tmp_path / '新项目.docx'
            source.write_bytes(b'isolated')
            service.receive_path(source)
    assert service.db.counts() == {'projects': 0, 'batches': 0, 'zips': 0}
    with service.db.connect() as con:
        assert con.execute('SELECT COUNT(*) FROM messages').fetchone()[0] == 0
        assert con.execute('SELECT COUNT(*) FROM intake_receipts').fetchone()[0] == int(entry == 'review')


def legacy_fixture(tmp_path, *, existing_generated=False, disabled=False):
    db = Database(tmp_path / 'upgrade.db')
    with db.connect() as con:
        con.execute('DELETE FROM project_type_defaults')
        con.execute('DELETE FROM project_type_migrations')
        con.execute('DELETE FROM project_types')
        con.execute("INSERT INTO project_types(id,name,normalized_name) VALUES (41,'材料','材料')")
        if existing_generated:
            con.execute("INSERT INTO project_types(id,name,normalized_name,active) VALUES (42,'生成材料','生成材料',?)", (int(not disabled),))
        con.execute('INSERT INTO project_type_migrations VALUES (?,?)',
                    ('editable-project-type-presets-v1', json.dumps({'41': '材料'})))
        con.execute("INSERT INTO batches(id,name) VALUES (1,'旧批次')")
        con.execute("INSERT INTO messages(id,batch_id,raw_text) VALUES (1,1,'旧消息')")
        con.execute("INSERT INTO projects(id,message_id,batch_id,name) VALUES (1,1,1,'旧未分类'),(2,1,1,'旧分类')")
        con.execute('INSERT INTO project_type_links(project_id,type_id) VALUES (2,41)')
    return db


@pytest.mark.parametrize('existing,disabled', [(False, False), (True, False), (True, True)])
def test_upgrade_preserves_old_types_projects_and_precise_origin(tmp_path, existing, disabled):
    db = legacy_fixture(tmp_path, existing_generated=existing, disabled=disabled)
    reopened = Database(db.path)
    assert reopened.get_project(1)['project_type_id'] is None
    assert reopened.get_project(2)['project_type_id'] == 41
    assert reopened.list_project_types(include_inactive=True)[0]['name'] == '材料'
    generated = next(t for t in reopened.list_project_types(include_inactive=True) if t['name'] == '生成材料')
    assert reopened.get_default_project_type() == (None if disabled else generated['id'])
    with reopened.connect() as con:
        origins = json.loads(con.execute('SELECT seeded_types FROM project_type_migrations WHERE name=?',
                             ('default-generated-project-type-v1',)).fetchone()[0])
        assert origins == ({} if existing else {str(generated['id']): '生成材料'})
    assert WorkspaceManager(WorkBox(db.path)).setup_required() is False
    before = reopened.list_project_types(include_inactive=True)
    assert Database(db.path).list_project_types(include_inactive=True) == before


def test_customized_default_before_workspace_counts_as_history(tmp_path):
    service = WorkBox(tmp_path / 'customized.db')
    service.set_default_project_type(None)
    assert WorkspaceManager(service).setup_required() is False


def test_disabling_default_rolls_back_both_changes_on_settings_failure(tmp_path):
    db = Database(tmp_path / 'atomic-disable.db')
    kind = db.get_default_project_type()
    with db.connect() as con:
        con.execute("CREATE TRIGGER reject_default_clear BEFORE UPDATE ON project_type_defaults BEGIN SELECT RAISE(ABORT,'isolated clear failure'); END")
    with pytest.raises(sqlite3.DatabaseError, match='isolated clear failure'):
        db.set_project_type_active(kind, False)
    assert db.get_default_project_type() == kind
    assert db.list_project_types()[0]['active'] is True


def test_new_default_does_not_change_existing_matching_project(tmp_path):
    service = DesktopService(tmp_path / 'data' / 'existing.db')
    service.capture_message('既有项目', '既有批次')
    original = service.get_project(1)['project_type_id']
    service.set_default_project_type(service.create_project_type('后续业务')['id'])
    source = tmp_path / '既有项目.docx'
    source.write_bytes(b'isolated')
    service.receive_path(source)
    assert len(service.list_projects()) == 1
    assert service.get_project(1)['project_type_id'] == original


def test_five_hundred_new_projects_get_default_and_over_limit_is_atomic(tmp_path):
    db = Database(tmp_path / 'limit.db')
    projects = [('项目' + str(index), False) for index in range(500)]
    result = db.create_capture('隔离五百项目', '隔离批次', projects)
    assert len(result['project_ids']) == 500
    assert all(row['project_type_id'] == db.get_default_project_type() for row in db.list_projects())
    with pytest.raises(ValueError):
        db.create_capture('超限项目', '超限批次', projects + [('额外项目', False)])
    assert db.counts() == {'projects': 500, 'batches': 1, 'zips': 0}


@pytest.mark.parametrize('failure', ['setting', 'migration'])
def test_upgrade_default_migration_failure_rolls_back_type_settings_and_origin(tmp_path, failure):
    db = legacy_fixture(tmp_path)
    with db.connect() as con:
        if failure == 'setting':
            con.execute("CREATE TRIGGER reject_default_migration BEFORE INSERT ON project_type_defaults BEGIN SELECT RAISE(ABORT,'isolated migration failure'); END")
        else:
            con.execute("CREATE TRIGGER reject_default_migration BEFORE INSERT ON project_type_migrations WHEN NEW.name='default-generated-project-type-v1' BEGIN SELECT RAISE(ABORT,'isolated migration failure'); END")
    with pytest.raises(sqlite3.DatabaseError, match='isolated migration failure'):
        Database(db.path)
    with db.connect() as con:
        assert [row['name'] for row in con.execute('SELECT name FROM project_types')] == ['材料']
        assert con.execute('SELECT COUNT(*) FROM project_type_defaults').fetchone()[0] == 0
        assert con.execute('SELECT COUNT(*) FROM project_type_migrations').fetchone()[0] == 1
        assert [(row['project_id'], row['type_id']) for row in con.execute('SELECT * FROM project_type_links')] == [(2, 41)]
