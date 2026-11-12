"""Anonymous recovery and registered-project boundaries for ZIP writes."""
from pathlib import Path
from zipfile import ZipFile

import pytest

from desktop_service import DesktopService
from parser import ParsedProject


def write_zip(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(path, 'w') as output:
        output.writestr('result.txt', 'anonymous')
    return path


def block_document_recovery(service, root):
    journal = service.workspaces.document_versions.journal_root
    journal.mkdir(parents=True, exist_ok=True)
    record = journal / ('a' * 32 + '.json')
    record.write_text('{broken', encoding='utf-8')
    ordinary = root / 'anonymous.txt'
    ordinary.write_text('anonymous', encoding='utf-8')
    with pytest.raises(RuntimeError):
        service.workspaces.import_file(ordinary, 1)
    assert service.workspaces.document_versions.blocked
    return record


def nested_projects(tmp_path):
    service = DesktopService(tmp_path / 'data' / 'workbox.db')
    first = service.capture_message('匿名甲系统', '匿名同一批次', [ParsedProject('匿名甲系统')])['project_ids'][0]
    second = service.capture_message('匿名乙系统', '匿名同一批次', [ParsedProject('匿名乙系统')])['project_ids'][0]
    first_path = Path(service.workspaces.get_workspace(first)['path'])
    second_path = Path(service.workspaces.get_workspace(second)['path'])
    assert second_path.is_relative_to(first_path)
    return service, first, second, first_path, second_path


def test_nested_registered_workspace_zip_belongs_to_child(tmp_path):
    service, first, second, first_path, second_path = nested_projects(tmp_path)
    source = write_zip(second_path / 'result.zip')
    own_source = write_zip(first_path / 'output' / 'parent.zip')
    service.scan_archives(0)
    watched = {row['source_path']: row['project_id'] for row in service.snapshots.list_sources()}
    assert watched[str(source)] == second
    assert watched[str(own_source)] == first
    archived = service.scan_archives(10)['archived']
    assert {row['project_id'] for row in archived} == {first, second}


@pytest.mark.parametrize('retired', ['archived', 'deleted'])
def test_parent_never_discovers_inactive_child_materials(tmp_path, retired):
    service, first, second, first_path, second_path = nested_projects(tmp_path)
    source = write_zip(second_path / 'result.zip')
    if retired == 'archived':
        service.bulk_update_projects([second], archived=True)
    else:
        service.delete_project(second, confirmed=True)
    service.scan_archives(0)
    assert not any(row['source_path'] == str(source) for row in service.snapshots.list_sources())


def test_discovery_preserves_existing_explicit_source_binding(tmp_path):
    service, first, second, first_path, second_path = nested_projects(tmp_path)
    source = write_zip(second_path / 'result.zip')
    service.snapshots.watch_zip(source, first)
    service.scan_archives(0)
    assert next(row for row in service.snapshots.list_sources() if row['source_path'] == str(source))['project_id'] == first


def test_nested_boundary_beyond_unregistered_intermediate_directory(tmp_path):
    service, first, second, first_path, second_path = nested_projects(tmp_path)
    deeper = first_path / 'ordinary' / 'nested' / 'child'
    source = write_zip(deeper / 'result.zip')
    own_source = write_zip(first_path / 'ordinary' / 'parent.zip')
    with service.db.connect() as connection:
        connection.execute('UPDATE project_workspaces SET path=? WHERE project_id=?', (str(deeper), second))
    service.scan_archives(0)
    watched = {row['source_path']: row['project_id'] for row in service.snapshots.list_sources()}
    assert watched[str(source)] == second
    assert watched[str(own_source)] == first


def test_shared_registered_root_is_not_discovered_without_unambiguous_owner(tmp_path, monkeypatch):
    service, first, second, first_path, second_path = nested_projects(tmp_path)
    source = write_zip(first_path / 'result.zip')
    shared = [dict(row, path=str(first_path)) for row in service.workspaces.list_workspaces(include_archived=True)]
    monkeypatch.setattr(service.workspaces, 'list_workspaces', lambda **_: list(shared))
    service.scan_archives(0)
    assert not any(row['source_path'] == str(source) for row in service.snapshots.list_sources())


@pytest.mark.parametrize('direct', [False, True])
def test_scan_refuses_document_recovery_block_without_writing(tmp_path, direct):
    service = DesktopService(tmp_path / 'data' / 'workbox.db')
    pid = service.receive_text('1. 匿名恢复系统')['project_ids'][0]
    source = write_zip(Path(service.workspaces.get_workspace(pid)['path']) / 'result.zip')
    service.snapshots.watch_zip(source, pid)
    service.scan_archives(0)
    record = block_document_recovery(service, tmp_path)
    scan = service.snapshots.scan if direct else service.scan_archives
    with pytest.raises(RuntimeError, match='恢复'):
        scan(10)
    assert service.db.counts()['zips'] == 0
    assert not list(Path(service.workspaces.settings()['archive_root']).rglob('*.zip'))
    assert source.is_file() and record.read_text(encoding='utf-8') == '{broken'


def test_pending_zip_registration_waits_for_explicit_recovery(tmp_path, monkeypatch):
    service = DesktopService(tmp_path / 'data' / 'workbox.db')
    pid = service.receive_text('1. 匿名恢复系统')['project_ids'][0]
    source = write_zip(tmp_path / 'drop' / 'result.zip')
    with monkeypatch.context() as patch:
        def fail_commit(*args, **kwargs):
            raise OSError('anonymous commit failure')
        patch.setattr(service.snapshots, '_commit', fail_commit)
        with pytest.raises(OSError, match='anonymous commit failure'):
            service.snapshots.archive_now(source, pid)
    with service.db.connect() as connection:
        pending = dict(connection.execute('SELECT * FROM zip_snapshot_attempts').fetchone())
    assert pending['state'] == 'copied' and Path(pending['target']).is_file()
    record = block_document_recovery(service, tmp_path)
    with pytest.raises(RuntimeError, match='恢复'):
        service.snapshots.scan(10)
    with service.db.connect() as connection:
        assert connection.execute('SELECT state FROM zip_snapshot_attempts').fetchone()[0] == 'copied'
    assert service.db.counts()['zips'] == 0
    record.unlink()
    assert service.recover_project_changes() == []
    assert service.snapshots.scan(20)['archived'][0]['path'] == pending['target']
    assert service.db.counts()['zips'] == 1 and source.is_file()


def test_storage_blocked_property_tracks_current_recovery_state(tmp_path):
    service = DesktopService(tmp_path / 'data' / 'workbox.db')
    service.receive_text('1. 匿名恢复系统')
    assert service.storage_blocked is False
    service.project_change_errors = [dict(global_block=False)]
    assert service.storage_blocked is False
    service.project_change_errors = [dict(global_block=True)]
    assert service.storage_blocked is True
    service.project_change_errors = []
    record = block_document_recovery(service, tmp_path)
    assert service.storage_blocked is True
    record.unlink()
    assert service.recover_project_changes() == []
    assert service.storage_blocked is False


@pytest.mark.parametrize('operation', ['watch', 'work-copy', 'archive', 'approve', 'recognize', 'link'])
def test_snapshot_write_entries_refuse_recovery_block(tmp_path, operation):
    service = DesktopService(tmp_path / 'data' / 'workbox.db')
    ids = service.receive_text('1. 匿名恢复系统\n2. 匿名保留系统')['project_ids']
    source = write_zip(tmp_path / 'drop' / 'result.zip')
    manager = service.snapshots
    package = manager.archive_now(source, ids[0])['zip']
    extra = write_zip(tmp_path / 'drop' / 'extra.zip')
    block_document_recovery(service, tmp_path)
    operations = {
        'watch': lambda: manager.watch_zip(extra, ids[0]),
        'work-copy': lambda: manager.watch_work_copy(extra, source, ids[0], baseline_sha256=package['sha256']),
        'archive': lambda: manager.archive_now(extra, ids[0]),
        'approve': lambda: manager.approve_version(package['id']),
        'recognize': lambda: manager.recognize_bundle(package['id']),
        'link': lambda: manager.link_zip(package['id'], ids[1]),
    }
    with service.db.connect() as connection:
        before = list(connection.iterdump())
    with pytest.raises(RuntimeError, match='恢复'):
        operations[operation]()
    with service.db.connect() as connection:
        assert list(connection.iterdump()) == before
    assert manager.list_versions() and manager.list_sources()


def test_snapshot_manager_keeps_base_service_compatibility(tmp_path):
    from app import WorkBox
    from managed_archive import ManagedArchive
    from snapshot_manager import SnapshotManager
    from workspace_manager import WorkspaceManager

    service = WorkBox(tmp_path / 'data' / 'workbox.db')
    service.archive = ManagedArchive(service, tmp_path / 'packages')
    workspaces = WorkspaceManager(service)
    pid = service.capture_message('匿名基础系统', '匿名批次', [ParsedProject('匿名基础系统')])['project_ids'][0]
    manager = SnapshotManager(service, workspaces)
    source = write_zip(tmp_path / 'drop' / 'result.zip')
    manager.watch_zip(source, pid)
    manager.scan(0)
    assert manager.scan(10)['archived'][0]['project_id'] == pid
    workspaces.document_versions.blocked = {None: 'anonymous recovery block'}
    with pytest.raises(RuntimeError, match='恢复'):
        manager.scan(20)
