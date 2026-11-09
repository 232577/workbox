"""History storage must never re-enter intake, exports or delivery materials."""
from pathlib import Path
from zipfile import ZipFile

import pytest

from desktop_service import DesktopService
from delivery_export import export_pending_projects
from folder_intake import FolderIntake


@pytest.fixture
def service(tmp_path):
    return DesktopService(tmp_path / 'data' / 'workbox.db')


def project(service):
    pid = service.capture_message('匿名项目', '匿名批次')['project_ids'][0]
    return pid, Path(service.workspaces.get_workspace(pid)['path'])


def register_history(service, root, monkeypatch):
    root.mkdir(parents=True, exist_ok=True)
    info = root.stat()
    with service.db.connect() as connection:
        connection.execute('''CREATE TABLE IF NOT EXISTS document_history_roots (
            id INTEGER PRIMARY KEY, path TEXT NOT NULL COLLATE NOCASE UNIQUE,
            volume_device INTEGER NOT NULL, directory_inode TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
        connection.execute('INSERT INTO document_history_roots(path,volume_device,directory_inode) VALUES (?,?,?)',
                           (str(root), info.st_dev, str(info.st_ino)))
    if not hasattr(service.workspaces, 'history_roots'):
        monkeypatch.setattr(service.workspaces, 'history_roots', lambda: [str(root)], raising=False)
    return root


def test_history_folder_cannot_be_imported_as_current_materials(service, tmp_path, monkeypatch):
    pid, workspace = project(service)
    history = register_history(service, tmp_path / 'history', monkeypatch)
    old = history / 'old.txt'
    old.write_bytes(b'old material')
    result = FolderIntake(service).receive(history, project_id=pid, manual=True)
    assert result['failed'] == 1 and result['copied'] == 0
    assert old.read_bytes() == b'old material' and not list(workspace.iterdir())


def test_history_zip_cannot_be_added_to_automatic_watch(service, tmp_path, monkeypatch):
    pid, _ = project(service)
    history = register_history(service, tmp_path / 'history', monkeypatch)
    source = history / 'old.zip'
    with ZipFile(source, 'w') as archive:
        archive.writestr('old.txt', b'old material')
    with pytest.raises(ValueError):
        service.snapshots.watch_zip(source, pid)
    with service.db.connect() as connection:
        assert connection.execute('SELECT count(*) FROM zip_sources').fetchone()[0] == 0


def test_export_cannot_write_into_history_storage(service, tmp_path, monkeypatch):
    project(service)
    history = register_history(service, tmp_path / 'history', monkeypatch)
    target = history / 'report.xlsx'
    with pytest.raises(ValueError):
        export_pending_projects(service.db, target)
    assert not target.exists()


def test_rename_cannot_move_a_registered_history_root(service, monkeypatch):
    pid, workspace = project(service)
    history = register_history(service, workspace / 'protected', monkeypatch)
    (history / 'old.txt').write_bytes(b'preserve')
    with pytest.raises(ValueError):
        service.directory_changes.preview(pid, '新名称')
    assert (history / 'old.txt').read_bytes() == b'preserve'


def test_delivery_refuses_overlapping_history_storage(service, monkeypatch):
    from delivery_archive import ProjectDeliveryArchive
    pid, workspace = project(service)
    history = register_history(service, workspace / 'protected', monkeypatch)
    (workspace / 'current.txt').write_bytes(b'current')
    (history / 'old.txt').write_bytes(b'preserve')
    with pytest.raises(ValueError):
        ProjectDeliveryArchive(service, service.workspaces).confirm(pid, confirmed=True)
    with service.db.connect() as connection:
        assert connection.execute('SELECT count(*) FROM project_delivery_archives').fetchone()[0] == 0


def test_history_boundary_change_invalidates_rename_preview(service, tmp_path, monkeypatch):
    pid, workspace = project(service)
    plan = service.preview_project_rename(pid, '新名称')
    register_history(service, tmp_path / 'history', monkeypatch)
    with pytest.raises(ValueError):
        service.rename_project(pid, '新名称', expected_token=plan['token'])
    assert workspace.is_dir()
