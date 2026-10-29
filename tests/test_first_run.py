"""首次设置只记录本机引导进度，不改写旧配置或业务事实。"""

import json
import sqlite3

import pytest

from app import WorkBox
from intake_log import FileIntakeLog
from workspace_manager import SCHEMA, WorkspaceManager


def test_fresh_core_database_stays_pending_after_reopen(tmp_path):
    service = WorkBox(tmp_path / 'data' / 'workbox.db')
    manager = WorkspaceManager(service)
    assert manager.setup_required() is True
    assert manager.settings()['auto_deliver_zip'] is True
    with service.db.connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM workflow_settings').fetchone()[0] == 0
    assert WorkspaceManager(WorkBox(service.db.path)).setup_required() is True
    assert service.db.counts() == {'projects': 0, 'batches': 0, 'zips': 0}


def test_legacy_empty_workspace_schema_skips_guide_without_saving_defaults(tmp_path):
    service = WorkBox(tmp_path / 'legacy.db')
    with service.db.connect() as connection:
        connection.executescript(SCHEMA)
    manager = WorkspaceManager(service)
    assert manager.setup_required() is False
    assert manager.settings()['auto_deliver_zip'] is True
    with service.db.connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM workflow_settings').fetchone()[0] == 0


@pytest.mark.parametrize('automatic', [True, False])
def test_legacy_custom_settings_and_project_history_survive_upgrade(tmp_path, automatic):
    service = WorkBox(tmp_path / 'legacy.db')
    capture = service.capture_message('测试项目', '测试批次')
    original = service.get_project(capture['project_ids'][0])
    settings = dict(work_root=str(tmp_path / 'old-work'), archive_root=str(tmp_path / 'old-zip'),
                    stable_seconds=37, auto_create_projects=False, auto_deliver_zip=automatic,
                    zip_delivery_min_files=19)
    with service.db.connect() as connection:
        connection.executescript(SCHEMA)
        connection.executemany('INSERT INTO workflow_settings VALUES (?,?)',
                               [(key, json.dumps(value)) for key, value in settings.items()])
    manager = WorkspaceManager(service)
    assert manager.setup_required() is False
    assert manager.settings() == settings
    assert service.get_project(original['id']) == original
    assert WorkspaceManager(WorkBox(service.db.path)).settings() == settings


@pytest.mark.parametrize('history', ['project', 'batch', 'intake'])
def test_legacy_business_history_without_workspace_schema_skips_guide(tmp_path, history):
    service = WorkBox(tmp_path / 'legacy.db')
    with service.db.connect() as connection:
        if history == 'project':
            service.capture_message('测试项目', '测试批次')
        elif history == 'batch':
            connection.execute("INSERT INTO batches(name) VALUES ('测试批次')")
        else:
            FileIntakeLog(service.db).append(tmp_path / 'cancelled.txt', 'cancelled')
    manager = WorkspaceManager(service)
    assert manager.setup_required() is False
    with service.db.connect() as connection:
        table = {'project': 'projects', 'batch': 'batches', 'intake': 'file_intake_events'}[history]
        assert connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 1


def test_saving_settings_without_finishing_keeps_setup_pending(tmp_path):
    manager = WorkspaceManager(WorkBox(tmp_path / 'data' / 'workbox.db'))
    manager.configure(tmp_path / 'work', tmp_path / 'zip', auto_deliver_zip=False)
    assert manager.setup_required() is True
    assert manager.settings()['auto_deliver_zip'] is False
    assert WorkspaceManager(WorkBox(manager.db.path)).setup_required() is True


def test_explicit_finish_is_persistent_and_idempotent(tmp_path):
    manager = WorkspaceManager(WorkBox(tmp_path / 'data' / 'workbox.db'))
    settings = manager.configure(tmp_path / 'work', tmp_path / 'zip', 25,
                                 auto_deliver_zip=False, complete_setup=True)
    assert manager.setup_required() is False
    assert settings['auto_deliver_zip'] is False
    manager.configure(tmp_path / 'work', tmp_path / 'zip', 25,
                      auto_deliver_zip=False, complete_setup=True)
    restarted = WorkspaceManager(WorkBox(manager.db.path))
    assert restarted.setup_required() is False
    assert restarted.settings() == settings


@pytest.mark.parametrize('invalid', [None, 0, 1, 'true', [], {}])
def test_complete_setup_requires_a_real_boolean(tmp_path, invalid):
    manager = WorkspaceManager(WorkBox(tmp_path / 'data' / 'workbox.db'))
    before = manager.settings()
    with pytest.raises(ValueError):
        manager.configure(tmp_path / 'work', tmp_path / 'zip', complete_setup=invalid)
    assert manager.settings() == before
    assert manager.setup_required() is True


def test_failed_configuration_does_not_finish_guide_or_change_settings(tmp_path):
    manager = WorkspaceManager(WorkBox(tmp_path / 'data' / 'workbox.db'))
    before = manager.settings()
    # 在设置写入后让数据库拒绝完成操作，验证事务确实回滚两个部分。
    with manager.db.connect() as connection:
        connection.execute('''CREATE TRIGGER reject_setup BEFORE UPDATE ON workflow_first_run
            BEGIN SELECT RAISE(ABORT,'fixture failure'); END''')
    with pytest.raises(sqlite3.IntegrityError, match='fixture failure'):
        manager.configure(tmp_path / 'work', tmp_path / 'zip', auto_deliver_zip=False,
                          complete_setup=True)
    assert manager.settings() == before
    assert manager.setup_required() is True
    assert str(tmp_path / 'zip') not in manager.archive_roots()


def test_invalid_paths_do_not_finish_guide(tmp_path):
    manager = WorkspaceManager(WorkBox(tmp_path / 'data' / 'workbox.db'))
    before = manager.settings()
    with pytest.raises(ValueError):
        manager.configure(tmp_path / 'same', tmp_path / 'same', complete_setup=True)
    assert manager.settings() == before
    assert manager.setup_required() is True


@pytest.mark.parametrize('corruption', ['missing_row', 'wrong_value', 'wrong_schema'])
def test_corrupt_setup_metadata_is_not_silently_replaced(tmp_path, corruption):
    service = WorkBox(tmp_path / 'data' / 'workbox.db')
    manager = WorkspaceManager(service)
    with service.db.connect() as connection:
        if corruption == 'missing_row':
            connection.execute('DELETE FROM workflow_first_run')
        else:
            connection.execute('DROP TABLE workflow_first_run')
            if corruption == 'wrong_schema':
                connection.execute('CREATE TABLE workflow_first_run (other TEXT)')
            else:
                connection.execute('CREATE TABLE workflow_first_run (id INTEGER, completed INTEGER)')
                connection.execute('INSERT INTO workflow_first_run VALUES (1,9)')
    with pytest.raises(RuntimeError, match='首次设置'):
        manager.setup_required()
    with pytest.raises(RuntimeError, match='首次设置'):
        WorkspaceManager(service)


@pytest.mark.parametrize('automatic, expected', [(True, 'automatic'), (False, 'manual')])
def test_mode_follows_zip_delivery_preference(automatic, expected):
    from first_run import mode_key
    assert mode_key({'auto_deliver_zip': automatic, 'auto_create_projects': not automatic}) == expected


@pytest.mark.parametrize('invalid', [None, 0, 1, 'false'])
def test_mode_rejects_corrupt_zip_delivery_preference(invalid):
    from first_run import mode_key
    with pytest.raises(ValueError):
        mode_key({'auto_deliver_zip': invalid})


def test_only_factory_type_presets_keep_first_setup_pending(tmp_path):
    service = WorkBox(tmp_path / 'fresh-presets.db')
    assert {row['name'] for row in service.list_project_types()} == {'材料', '可运行'}
    assert WorkspaceManager(service).setup_required()


@pytest.mark.parametrize('name', ['材料', '可运行', '既有业务'])
def test_existing_same_named_types_are_not_factory_history(tmp_path, name):
    service = WorkBox(tmp_path / 'legacy-types.db')
    with service.db.connect() as connection:
        connection.execute('DELETE FROM project_type_migrations')
        connection.execute('DELETE FROM project_types')
        connection.execute('INSERT INTO project_types(id,name,normalized_name) VALUES (41,?,?)',
                           (name, name.casefold()))
    reopened = WorkBox(service.db.path)
    assert WorkspaceManager(reopened).setup_required() is False
    assert next(t for t in reopened.list_project_types() if t['id'] == 41)['name'] == name


@pytest.mark.parametrize('change', ['rename', 'disable', 'new'])
def test_user_type_customization_before_workspace_is_legacy(tmp_path, change):
    service = WorkBox(tmp_path / 'changed-presets.db')
    preset = next(row for row in service.list_project_types() if row['name'] == '材料')
    if change == 'rename':
        service.rename_project_type(preset['id'], '内部材料')
    elif change == 'disable':
        service.set_project_type_active(preset['id'], False)
    else:
        service.create_project_type('自定义业务')
    assert WorkspaceManager(service).setup_required() is False


def test_custom_type_does_not_complete_existing_pending_guide(tmp_path):
    service = WorkBox(tmp_path / 'pending.db')
    manager = WorkspaceManager(service)
    assert manager.setup_required()
    service.create_project_type('自定义业务')
    assert WorkspaceManager(WorkBox(service.db.path)).setup_required()


@pytest.mark.parametrize('payload', ['invalid json', '[]', '{"invalid":"材料"}', '{"1":1}'])
def test_corrupt_preset_origin_does_not_silently_decide_first_setup(tmp_path, payload):
    service = WorkBox(tmp_path / 'corrupt-presets.db')
    with service.db.connect() as connection:
        connection.execute('UPDATE project_type_migrations SET seeded_types=?', (payload,))
    with pytest.raises(RuntimeError, match='首次设置'):
        WorkspaceManager(service)
    with service.db.connect() as connection:
        assert not connection.execute("SELECT 1 FROM sqlite_master WHERE name='workflow_first_run'").fetchone()
