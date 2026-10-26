from concurrent.futures import CancelledError
from pathlib import Path
import threading
from zipfile import ZipFile

import pytest

from desktop_service import DesktopService


class Workspaces:
    def __init__(self, root):
        self.root = root
        self.items = []

    def settings(self):
        return {'work_root': str(self.root / 'work'), 'archive_root': str(self.root / 'data' / 'ZIP归档'), 'stable_seconds': 10}

    def list_workspaces(self, include_archived=False):
        return list(self.items)


def setup_manager(tmp_path):
    from snapshot_manager import SnapshotManager
    service = DesktopService(tmp_path / 'data' / 'workbox.db')
    service.receive_text('1. 客户档案系统\n2. 合同归档工具')
    workspaces = Workspaces(tmp_path)
    return service, workspaces, SnapshotManager(service, workspaces)


def write_zip(path, content='first'):
    path.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(path, 'w') as archive:
        archive.writestr('result.txt', content)
    return path


def stable(manager, start=0):
    assert manager.scan(start)['archived'] == []
    return manager.scan(start + 10)['archived']


def test_watch_waits_ten_seconds_and_copies_preserving_source(tmp_path):
    service, ws, manager = setup_manager(tmp_path)
    source = write_zip(tmp_path / 'drop' / 'result.zip')
    assert manager.watch_zip(source, 1)['watching']
    assert not manager.scan(0)['archived']
    assert not manager.scan(9.9)['archived']
    result = manager.scan(10)['archived'][0]
    assert source.exists() and Path(result['path']).read_bytes() == source.read_bytes()
    assert result['filename'] == 'result.zip'
    assert Path(result['path']).is_relative_to(Path(ws.settings()['archive_root']))
    assert service.get_project(1)['status'] == '待交付'
    assert not manager.list_versions(1)[0]['approved']


def test_versions_repeat_revert_and_approval_are_independent(tmp_path):
    service, ws, manager = setup_manager(tmp_path)
    source = write_zip(tmp_path / 'input.zip', 'A')
    manager.watch_zip(source, 1)
    first = stable(manager)[0]
    manager.approve_version(first['id'])
    write_zip(source, 'B longer')
    second = stable(manager, 20)[0]
    assert first['id'] != second['id']
    assert len(manager.list_versions(1)) == 2
    assert next(row for row in manager.list_versions(1) if row['id'] == first['id'])['approved']
    assert manager.scan(40)['archived'] == []
    # 使用原字节复原，避免 ZIP 写入时间影响散列。
    source.write_bytes(Path(first['path']).read_bytes())
    reverted = stable(manager, 50)[0]
    assert reverted['id'] == first['id']
    assert len(manager.list_versions(1)) == 2
    assert source.exists()
    assert service.get_project(1)['delivered_zip_id'] is None


def test_bad_update_keeps_good_version_and_reports_once(tmp_path):
    service, ws, manager = setup_manager(tmp_path)
    source = write_zip(tmp_path / 'input.zip')
    manager.watch_zip(source, 1)
    first = stable(manager)[0]
    manager.approve_version(first['id'])
    source.write_bytes(b'PK\x03\x04' + b'broken central directory' * 5)
    manager.scan(20)
    failed = manager.scan(30)
    assert failed['errors'] and not failed['archived']
    assert manager.scan(31)['errors'] == []
    assert manager.list_sources()[0]['last_error']
    assert manager.list_versions(1)[0]['approved']
    assert service.db.counts()['zips'] == 1


def test_cancel_and_restart_require_new_stability(tmp_path):
    service, ws, manager = setup_manager(tmp_path)
    source = write_zip(tmp_path / 'input.zip')
    manager.watch_zip(source)
    manager.scan(0)
    event = threading.Event()
    event.set()
    with pytest.raises(CancelledError):
        manager.scan(10, cancel_event=event)
    from snapshot_manager import SnapshotManager
    reopened = SnapshotManager(service, ws)
    assert reopened.scan(100)['archived'] == []
    assert reopened.scan(109)['archived'] == []
    assert reopened.scan(110)['archived']
    assert source.exists()


def test_unmatched_link_preserves_file_and_archived_projects_are_rejected(tmp_path):
    service, ws, manager = setup_manager(tmp_path)
    source = write_zip(tmp_path / 'input.zip')
    manager.watch_zip(source)
    record = stable(manager)[0]
    manager.link_zip(record['id'], 1)
    assert manager.is_snapshot(record['id'])
    assert service.db.get_zip(record['id'])['path'] == record['path']
    assert manager.list_sources()[0]['project_id'] == 1
    assert manager.list_versions(1)[0]['id'] == record['id']
    service.db.bulk_update_projects([2], archived=True)
    with pytest.raises(ValueError):
        manager.link_zip(record['id'], 2)
    with pytest.raises(ValueError):
        manager.watch_zip(source, 2)


def test_scope_only_registered_workspaces_excludes_archive_and_old_records(tmp_path):
    service, ws, manager = setup_manager(tmp_path)
    registered = tmp_path / 'work' / 'registered'
    ws.items.append({'project_id': 1, 'path': str(registered), 'group_path': str(registered.parent), 'name': '客户档案系统'})
    inside = write_zip(registered / 'nested' / 'result.zip')
    outside = write_zip(tmp_path / 'work' / 'unregistered' / 'old.zip')
    archive = write_zip(Path(ws.settings()['archive_root']) / 'existing.zip')
    old = service.db.register_zip({'path': str(outside), 'filename': outside.name, 'size': outside.stat().st_size, 'sha256': 'a' * 64, 'mtime_ns': outside.stat().st_mtime_ns}, None)[0]
    records = stable(manager)
    assert len(records) == 1 and manager.list_sources()[0]['source_path'] == str(inside)
    assert inside.exists() and outside.exists() and archive.exists()
    assert not manager.is_snapshot(old['id'])
    with pytest.raises(ValueError):
        manager.watch_zip(archive)


def test_approval_checks_hash_and_does_not_override_new_zip_count_decision(tmp_path):
    service, ws, manager = setup_manager(tmp_path)
    source = write_zip(tmp_path / 'input.zip')
    manager.watch_zip(source, 1)
    first = stable(manager)[0]
    manager.approve_version(first['id'])
    service.set_status(1, '待交付')
    service.db.complete_delivery(1, first['id'], first)
    write_zip(source, 'another version')
    second = stable(manager, 20)[0]
    manager.approve_version(second['id'])
    after = service.get_project(1)
    # 新ZIP不足4个文件时按新收录规则退回待交付；可用版标记不会伪造已交付。
    assert (after['status'], after['delivered_zip_id'], after['delivered_at']) == ('待交付', None, None)
    assert service.db.approved_zip_for_project(1) == second['id']
    service.confirm_delivery(1, first['id'], confirmed=True)
    with pytest.raises(ValueError):
        manager.link_zip(first['id'], 2)
    Path(second['path']).write_bytes(b'changed')
    with pytest.raises(ValueError):
        manager.approve_version(second['id'])


def test_source_update_resets_stability_and_manual_archive_uses_same_safe_copy(tmp_path):
    service, ws, manager = setup_manager(tmp_path)
    source = write_zip(tmp_path / 'input.zip')
    manager.watch_zip(source, 1)
    manager.scan(0)
    write_zip(source, 'changed while waiting')
    assert manager.scan(9)['archived'] == []
    assert manager.scan(10)['archived'] == []
    first = manager.scan(19)['archived'][0]
    repeated = manager.archive_now(source, 1)
    assert repeated['zip']['id'] == first['id']
    assert repeated['duplicate'] and repeated['source_preserved']
    assert source.exists()


def test_copy_success_database_failure_is_recoverable_without_second_copy(tmp_path, monkeypatch):
    service, ws, manager = setup_manager(tmp_path)
    source = write_zip(tmp_path / 'input.zip')
    manager.watch_zip(source, 1)
    manager.scan(0)
    original_commit = manager._commit
    def fail_commit(*args):
        raise RuntimeError('isolated database commit failure')
    monkeypatch.setattr(manager, '_commit', fail_commit)
    assert manager.scan(10)['errors']
    copied = list(Path(ws.settings()['archive_root']).rglob('*.zip'))
    assert len(copied) == 1 and source.exists()
    assert service.db.counts()['zips'] == 0
    with service.db.connect() as connection:
        attempt = connection.execute('SELECT * FROM zip_snapshot_attempts').fetchone()
        assert attempt['target'] == str(copied[0]) and attempt['error']
    monkeypatch.setattr(manager, '_commit', original_commit)
    record = manager.scan(11)['archived'][0]
    assert record['path'] == str(copied[0])
    assert len(list(Path(ws.settings()['archive_root']).rglob('*.zip'))) == 1


def test_cancellation_during_file_copy_preserves_source_and_no_partial_record(tmp_path):
    service, ws, manager = setup_manager(tmp_path)
    source = write_zip(tmp_path / 'large.zip', 'large result' * 200000)
    manager.watch_zip(source, 1)
    manager.scan(0)
    event = threading.Event()
    inspected = False
    def progress(read, total):
        nonlocal inspected
        if inspected:
            event.set()
        if read == total:
            inspected = True
    with pytest.raises(CancelledError):
        manager.scan(10, cancel_event=event, progress=progress)
    assert source.exists() and service.db.counts()['zips'] == 0
    assert not list(Path(ws.settings()['archive_root']).rglob('*.zip'))
    with service.db.connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM zip_snapshot_attempts').fetchone()[0] == 1


def test_archived_project_is_not_discovered_and_existing_watch_is_paused(tmp_path):
    service, ws, manager = setup_manager(tmp_path)
    root = tmp_path / 'work' / 'project'
    source = write_zip(root / 'input.zip')
    ws.items.append({'project_id': 1, 'path': str(root), 'group_path': str(root.parent), 'name': '客户档案系统'})
    manager.scan(0)
    service.db.bulk_update_projects([1], archived=True)
    assert manager.scan(10)['archived'] == []
    assert manager.list_versions(1) == [] and source.exists()
    new_source = write_zip(root / 'new.zip')
    manager.scan(20)
    assert all(row['source_path'] != str(new_source) for row in manager.list_sources())


def test_only_one_approved_version_and_ui_keys_are_explicit(tmp_path):
    service, ws, manager = setup_manager(tmp_path)
    source = write_zip(tmp_path / 'input.zip', 'A')
    first = manager.archive_now(source, 1)['zip']
    manager.approve_version(first['id'])
    write_zip(source, 'B version')
    second = manager.archive_now(source, 1)['zip']
    manager.approve_version(second['id'])
    rows = manager.list_versions(1)
    assert sum(row['approved'] for row in rows) == 1
    assert rows[0]['zip_id'] == second['id'] and rows[0]['approved'] is True
    assert rows[1]['approved'] is False


def test_published_copy_before_metadata_update_can_recover(tmp_path, monkeypatch):
    import workspace_manager
    service, ws, manager = setup_manager(tmp_path)
    source = write_zip(tmp_path / 'input.zip')
    manager.watch_zip(source, 1)
    manager.scan(0)
    original = workspace_manager.copy_verified
    def published_then_interrupted(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError('interrupted after atomic copy publish')
    monkeypatch.setattr(workspace_manager, 'copy_verified', published_then_interrupted)
    assert manager.scan(10)['errors']
    assert service.db.counts()['zips'] == 0
    monkeypatch.setattr(workspace_manager, 'copy_verified', original)
    assert manager.scan(11)['archived']
    assert len(list(Path(ws.settings()['archive_root']).rglob('*.zip'))) == 1


def test_transient_copy_error_retries_unchanged_source_without_repeating_notice(tmp_path, monkeypatch):
    import workspace_manager
    service, ws, manager = setup_manager(tmp_path)
    source = write_zip(tmp_path / 'input.zip')
    manager.watch_zip(source, 1)
    manager.scan(0)
    original = workspace_manager.copy_verified
    calls = []
    def temporary_failure(*args, **kwargs):
        calls.append(1)
        raise OSError('temporarily insufficient space')
    monkeypatch.setattr(workspace_manager, 'copy_verified', temporary_failure)
    assert manager.scan(10)['errors']
    assert manager.scan(11)['errors'] == []
    assert len(calls) == 2
    monkeypatch.setattr(workspace_manager, 'copy_verified', original)
    assert manager.scan(12)['archived']


def test_restored_project_retries_unchanged_observed_zip(tmp_path):
    service, ws, manager = setup_manager(tmp_path)
    source = write_zip(tmp_path / 'input.zip')
    manager.watch_zip(source, 1)
    manager.scan(0)
    service.db.bulk_update_projects([1], archived=True)
    manager.scan(10)
    assert manager.list_sources()[0]['state'] == 'paused'
    service.db.bulk_update_projects([1], archived=False)
    assert manager.scan(20)['archived']


@pytest.mark.parametrize('delete_source', [True, False])
def test_pending_published_snapshot_recovers_independently_of_current_source(tmp_path, monkeypatch, delete_source):
    service, ws, manager = setup_manager(tmp_path)
    source = write_zip(tmp_path / 'input.zip', 'A result')
    manager.watch_zip(source, 1)
    manager.scan(0)
    original_commit = manager._commit
    def failed_commit(*args):
        raise RuntimeError('database temporarily unavailable')
    monkeypatch.setattr(manager, '_commit', failed_commit)
    assert manager.scan(10)['errors']
    a_target = next(Path(ws.settings()['archive_root']).rglob('*.zip'))
    if delete_source:
        source.unlink()
    else:
        write_zip(source, 'B changed result')
    monkeypatch.setattr(manager, '_commit', original_commit)
    result = manager.scan(20)
    assert any(row['path'] == str(a_target) for row in result['archived'])
    assert service.db.counts()['zips'] == 1
    if not delete_source:
        assert manager.scan(30)['archived']
        assert len(manager.list_versions(1)) == 2 and source.exists()


def test_new_source_content_state_is_pending_even_with_historical_digest(tmp_path):
    service, ws, manager = setup_manager(tmp_path)
    source = write_zip(tmp_path / 'input.zip', 'A')
    manager.watch_zip(source, 1)
    stable(manager)
    assert manager.list_sources()[0]['state'] == 'archived'
    write_zip(source, 'B waiting for stability')
    manager.scan(20)
    row = manager.list_sources()[0]
    assert row['last_digest'] and row['state'] == 'pending'


def test_workspace_discovery_never_replaces_manual_project_association(tmp_path):
    service, ws, manager = setup_manager(tmp_path)
    root = tmp_path / 'work' / 'projectA'
    source = write_zip(root / 'input.zip', 'A')
    ws.items.append({'project_id': 1, 'path': str(root), 'group_path': str(root.parent), 'name': '客户档案系统'})
    first = stable(manager)[0]
    manager.link_zip(first['id'], 2)
    write_zip(source, 'B version with manual association')
    manager.scan(20)
    assert manager.list_sources()[0]['project_id'] == 2
    record = manager.scan(30)['archived'][0]
    assert record['project_id'] == 2


def test_historical_archive_roots_are_not_reingested_after_reconfiguration(tmp_path):
    service, ws, manager = setup_manager(tmp_path)
    old_root = Path(ws.settings()['archive_root'])
    old_zip = write_zip(old_root / 'historic.zip')
    new_root = tmp_path / 'new-archive'
    ws.settings = lambda: {'work_root': str(tmp_path / 'work'), 'archive_root': str(new_root), 'stable_seconds': 10}
    ws.archive_roots = lambda: [str(old_root), str(new_root)]
    ws.items.append({'project_id': 1, 'path': str(old_root.parent), 'group_path': str(old_root.parent), 'name': '历史布局'})
    assert stable(manager) == []
    assert manager.list_sources() == []
    with pytest.raises(ValueError):
        manager.watch_zip(old_zip)


def test_registered_snapshot_path_is_excluded_even_outside_known_archive_roots(tmp_path):
    service, ws, manager = setup_manager(tmp_path)
    source = write_zip(tmp_path / 'input.zip')
    snapshot = manager.archive_now(source, 1)['zip']
    detached = write_zip(tmp_path / 'work' / 'old-layout' / 'snapshot.zip')
    detached.write_bytes(Path(snapshot['path']).read_bytes())
    with service.db.connect() as connection:
        connection.execute('UPDATE zips SET path=? WHERE id=?', (str(detached), snapshot['id']))
    ws.items.append({'project_id': 1, 'path': str(detached.parent), 'group_path': str(detached.parent), 'name': '历史布局'})
    manager.scan(0)
    manager.scan(10)
    assert all(row['source_path'] != str(detached) for row in manager.list_sources())
    with pytest.raises(ValueError):
        manager.watch_zip(detached)


def test_pending_recovery_waits_while_project_archived_then_restores_without_source(tmp_path, monkeypatch):
    service, ws, manager = setup_manager(tmp_path)
    source = write_zip(tmp_path / 'input.zip')
    manager.watch_zip(source, 1)
    manager.scan(0)
    original_commit = manager._commit
    def failure(*args):
        raise OSError('temporary database write failure')
    monkeypatch.setattr(manager, '_commit', failure)
    assert manager.scan(10)['errors']
    source.unlink()
    monkeypatch.setattr(manager, '_commit', original_commit)
    service.db.bulk_update_projects([1], archived=True)
    before = service.get_project(1)
    assert manager.scan(11)['archived'] == []
    assert service.db.counts()['zips'] == 0
    assert manager.list_sources()[0]['state'] == 'paused'
    assert manager.scan(12)['errors'] == []
    assert service.get_project(1)['status'] == before['status']
    service.db.bulk_update_projects([1], archived=False)
    assert manager.scan(13)['archived']
    assert service.db.counts()['zips'] == 1


def test_workspace_discovery_skips_dependencies_but_keeps_output_directories(tmp_path):
    service, ws, manager = setup_manager(tmp_path)
    root = tmp_path / 'work' / 'project'
    ws.items.append({'project_id': 1, 'path': str(root), 'group_path': str(root.parent), 'name': '客户档案系统'})
    expected = [write_zip(root / 'root.zip', 'root')]
    for directory in ['release', 'dist', 'build']:
        expected.append(write_zip(root / directory / 'final.zip', directory))
    for directory in ['.git', 'node_modules', '.venv', 'venv', '__pycache__', '.cache', 'NODE_MODULES']:
        write_zip(root / directory / 'dependency.zip', directory)
    manager.scan(0)
    assert {row['source_path'] for row in manager.list_sources()} == {str(path) for path in expected}
    assert len(manager.scan(10)['archived']) == 4
