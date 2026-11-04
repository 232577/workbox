"""普通文档收录保留当前标准名，并保护原件及历史事实。"""

import hashlib
import sqlite3
import threading
import subprocess
import sys
from concurrent.futures import CancelledError
from pathlib import Path

import pytest

from app import WorkBox
from workspace_manager import WorkspaceManager
from native_files import ZipLease


@pytest.fixture
def documents(tmp_path):
    service = WorkBox(tmp_path / 'data' / 'workbox.db')
    manager = WorkspaceManager(service)
    pid = service.db.create_capture('材料系统', '匿名批次', [('材料系统', False)])['project_ids'][0]
    incoming = tmp_path / 'incoming'
    incoming.mkdir()
    source = incoming / '材料系统操作说明书.docx'
    source.write_bytes(b'first document')
    first = manager.import_file(source, pid)['file']
    return service, manager, pid, source, first


def rows(service, table):
    with service.db.connect() as connection:
        return [dict(row) for row in connection.execute(f'SELECT * FROM {table} ORDER BY id')]


def test_new_document_keeps_standard_name_and_old_row_tracks_version(documents):
    service, manager, pid, source, first = documents
    history = rows(service, 'file_intake_events')
    with service.db.connect() as connection:
        connection.execute("INSERT INTO delivery_events(project_id,project_name,batch_name,kind,archive_path) VALUES (?,?,?,'manual-files',?)",
                           (pid, '材料系统', '匿名批次', first['path']))
    deliveries = rows(service, 'delivery_events')
    source.write_bytes(b'new document')
    latest = manager.import_file(source, pid)['file']
    assert Path(latest['path']).name == source.name
    assert Path(latest['path']).read_bytes() == b'new document'
    old = rows(service, 'work_files')[0]
    assert old['id'] == first['id']
    assert old['sha256'] == first['sha256']
    assert old['source_path'] == first['source_path']
    assert old['path'] != latest['path']
    assert old['filename'] == Path(old['path']).name
    assert first['sha256'][:8] in old['filename']
    assert Path(old['path']).read_bytes() == b'first document'
    assert {key: value for key, value in old.items() if key not in ('path', 'filename')} == {
        key: value for key, value in first.items() if key not in ('path', 'filename')}
    assert rows(service, 'file_intake_events')[:len(history)] == history
    assert rows(service, 'delivery_events') == deliveries
    assert source.read_bytes() == b'new document'


def test_repeated_versions_never_overwrite_older_content(documents):
    service, manager, pid, source, first = documents
    canonical = Path(first['path'])
    occupied = canonical.with_name(f'{canonical.stem} ({first["sha256"][:8]}){canonical.suffix}')
    occupied.write_bytes(b'unrelated file')
    for content in (b'second document', b'third document'):
        source.write_bytes(content)
        result = manager.import_file(source, pid)
        assert Path(result['file']['path']) == canonical
    assert occupied.read_bytes() == b'unrelated file'
    assert sorted(Path(row['path']).read_bytes() for row in rows(service, 'work_files')) == [
        b'first document', b'second document', b'third document']


def test_subdirectory_and_case_collision_keep_requested_document_name(documents):
    service, manager, pid, source, first = documents
    source.write_bytes(b'nested first')
    nested = manager.import_file(source, pid, relative_path=Path('资料') / source.name)['file']
    source = source.rename(source.with_name(source.stem + '.DOCX'))
    source.write_bytes(b'nested latest')
    latest = manager.import_file(source, pid, relative_path=Path('资料') / source.name)['file']
    assert Path(latest['path']).name == source.name
    assert Path(latest['path']).parent == Path(nested['path']).parent
    assert Path(first['path']).read_bytes() == b'first document'
    old = next(row for row in rows(service, 'work_files') if row['id'] == nested['id'])
    assert Path(old['path']).read_bytes() == b'nested first'


@pytest.mark.parametrize('failure', ['cancel', 'copy', 'log', 'sql', 'commit', 'pending'])
def test_failure_restores_canonical_old_row_and_logs(documents, monkeypatch, failure):
    import workspace_manager
    service, manager, pid, source, first = documents
    original_rows = rows(service, 'work_files')
    original_logs = rows(service, 'file_intake_events')
    source.write_bytes(b'new document')
    event = threading.Event()
    if failure == 'cancel':
        original = workspace_manager.copy_verified
        def copy_and_cancel(*args, **kwargs):
            result = original(*args, **kwargs)
            event.set()
            return result
        monkeypatch.setattr(workspace_manager, 'copy_verified', copy_and_cancel)
    elif failure == 'copy':
        def progress(done, total):
            raise OSError('injected copy failure')
    elif failure == 'log':
        def fail(*args, **kwargs):
            raise OSError('injected log failure')
        monkeypatch.setattr(manager.intake_log, 'append', fail)
    elif failure == 'pending':
        def fail(*args, **kwargs):
            raise OSError('injected status failure')
        monkeypatch.setattr(service.db, '_mark_work_file_pending', fail)
    else:
        with service.db.connect() as connection:
            if failure == 'sql':
                connection.execute("CREATE TRIGGER reject_document BEFORE INSERT ON work_files BEGIN SELECT RAISE(ABORT,'injected SQL failure'); END")
            else:
                connection.execute('CREATE TABLE deferred_failure(id INTEGER REFERENCES projects(id) DEFERRABLE INITIALLY DEFERRED)')
                connection.execute('CREATE TRIGGER reject_commit AFTER INSERT ON work_files BEGIN INSERT INTO deferred_failure VALUES (-1); END')
    with pytest.raises((CancelledError, OSError, sqlite3.IntegrityError)):
        manager.import_file(source, pid, cancel_event=event,
                            progress=progress if failure == 'copy' else None)
    assert rows(service, 'work_files') == original_rows
    assert rows(service, 'file_intake_events') == original_logs
    assert Path(first['path']).read_bytes() == b'first document'
    assert list(Path(first['path']).parent.iterdir()) == [Path(first['path'])]
    assert source.read_bytes() == b'new document'


def test_same_content_duplicate_does_not_rotate(documents):
    service, manager, pid, source, first = documents
    before = rows(service, 'work_files')
    assert manager.import_file(source, pid)['duplicate']
    assert rows(service, 'work_files') == before
    assert list(Path(first['path']).parent.iterdir()) == [Path(first['path'])]


def test_zip_collision_preserves_old_naming(documents):
    service, manager, pid, source, first = documents
    source = source.with_name('材料系统.zip')
    source.write_bytes(b'first zip')
    old = manager.import_file(source, pid)['file']
    source.write_bytes(b'new zip')
    new = manager.import_file(source, pid)['file']
    assert Path(old['path']).read_bytes() == b'first zip'
    assert hashlib.sha256(b'new zip').hexdigest()[:8] in Path(new['path']).name


def test_unmanaged_document_collision_is_rejected(documents):
    service, manager, pid, source, first = documents
    source = source.with_name('未受管.docx')
    source.write_bytes(b'incoming')
    unmanaged = Path(first['path']).parent / source.name
    unmanaged.write_bytes(b'owner content')
    with pytest.raises((ValueError, RuntimeError)):
        manager.import_file(source, pid)
    assert unmanaged.read_bytes() == b'owner content'


def test_source_inside_workspace_is_registered_without_renaming(documents):
    service, manager, pid, source, first = documents
    inside = Path(first['path']).parent / 'other' / source.name
    inside.parent.mkdir()
    inside.write_bytes(b'local source')
    registered = manager.import_file(inside, pid)['file']
    assert registered['path'] == str(inside)
    assert inside.read_bytes() == b'local source'
    assert Path(first['path']).read_bytes() == b'first document'


@pytest.mark.parametrize('phase', ['old-renamed', 'new-published', 'committed'])
def test_restart_recovers_interrupted_rotation_without_rewriting_history(documents, phase):
    service, manager, pid, source, first = documents
    history = rows(service, 'file_intake_events')
    source.write_bytes(b'new document')
    script = '''
import os, sys
from pathlib import Path
from app import WorkBox
from workspace_manager import WorkspaceManager
from native_files import ZipLease
service = WorkBox(sys.argv[1])
manager = WorkspaceManager(service)
original = ZipLease.rename_to
phase = sys.argv[4]
def interrupted(self, target):
    original(self, target)
    if (phase == 'old-renamed' and '.stage' not in str(self.path) and Path(target).name != Path(sys.argv[2]).name
            or phase == 'new-published' and Path(target).name == Path(sys.argv[2]).name):
        os._exit(91)
ZipLease.rename_to = interrupted
if phase == 'committed':
    manager.document_versions._finish = lambda record: os._exit(91)
manager.import_file(sys.argv[2], int(sys.argv[3]))
'''
    result = subprocess.run([sys.executable, '-c', script, str(service.db.path), str(source), str(pid), phase],
                            cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=30)
    assert result.returncode == 91, result.stderr.decode(errors='replace')
    before_restart = {str(path): path.read_bytes() for path in Path(first['path']).parent.iterdir()}
    restarted = WorkspaceManager(WorkBox(service.db.path))
    # 仅创建服务不具备恢复授权；显式取得 session 锁的恢复入口才触碰文件。
    assert {str(path): path.read_bytes() for path in Path(first['path']).parent.iterdir()} == before_restart
    restarted.document_versions.recover()
    current = rows(service, 'work_files')
    if phase == 'committed':
        assert len(current) == 2
        assert Path(first['path']).read_bytes() == b'new document'
        assert Path(current[0]['path']).read_bytes() == b'first document'
    else:
        assert current == [first]
        assert Path(first['path']).read_bytes() == b'first document'
        assert rows(service, 'file_intake_events') == history
    assert source.read_bytes() == b'new document'
    assert not list(restarted.document_versions.journal_root.glob('*.json'))


def test_all_historical_rows_for_same_path_follow_old_file(documents):
    service, manager, pid, source, first = documents
    canonical = Path(first['path'])
    canonical.write_bytes(b'local edit')
    registered = manager.import_file(canonical, pid)['file']
    assert registered['path'] == str(canonical)
    before = rows(service, 'work_files')
    source.write_bytes(b'latest external document')
    current = manager.import_file(source, pid)['file']
    stored = rows(service, 'work_files')
    assert Path(current['path']) == canonical
    assert stored[0]['path'] == stored[1]['path'] != current['path']
    for old, moved in zip(before, stored):
        assert {key: value for key, value in moved.items() if key not in ('path', 'filename')} == {
            key: value for key, value in old.items() if key not in ('path', 'filename')}
        assert Path(moved['path']).read_bytes() == b'local edit'


def test_locked_old_document_rejects_rotation_without_changing_files(documents):
    service, manager, pid, source, first = documents
    source.write_bytes(b'new document')
    before = rows(service, 'work_files')
    with ZipLease(first['path'], movable=False), pytest.raises(OSError):
        manager.import_file(source, pid)
    assert rows(service, 'work_files') == before
    assert Path(first['path']).read_bytes() == b'first document'


def test_returning_historical_content_becomes_current_again(documents):
    service, manager, pid, source, first = documents
    source.write_bytes(b'second document')
    manager.import_file(source, pid)
    source.write_bytes(b'first document')
    returned = manager.import_file(source, pid)
    assert not returned['duplicate']
    assert returned['file']['path'] == first['path']
    assert Path(first['path']).read_bytes() == b'first document'
    assert sorted(Path(row['path']).read_bytes() for row in rows(service, 'work_files')) == [
        b'first document', b'first document', b'second document']


def test_old_layout_digest_copy_is_preserved_while_content_becomes_current(documents):
    service, manager, pid, source, first = documents
    canonical = Path(first['path'])
    source.write_bytes(b'old layout latest')
    digest = hashlib.sha256(b'old layout latest').hexdigest()
    historical = canonical.with_name(f'{canonical.stem} ({digest[:8]}){canonical.suffix}')
    historical.write_bytes(b'old layout latest')
    with service.db.connect() as connection:
        old_id = connection.execute('INSERT INTO work_files(project_id,source_path,path,filename,size,sha256,mtime_ns) VALUES (?,?,?,?,?,?,?)',
                                   (pid, str(source), str(historical), source.name, historical.stat().st_size,
                                    digest, historical.stat().st_mtime_ns)).lastrowid
    old_row = next(row for row in rows(service, 'work_files') if row['id'] == old_id)
    result = manager.import_file(source, pid)
    assert not result['duplicate']
    assert result['file']['path'] == str(canonical)
    assert canonical.read_bytes() == b'old layout latest'
    assert historical.read_bytes() == b'old layout latest'
    assert next(row for row in rows(service, 'work_files') if row['id'] == old_id) == old_row


def test_external_edit_is_not_silently_versioned_or_overwritten(documents):
    service, manager, pid, source, first = documents
    source.write_bytes(b'new document')
    Path(first['path']).write_bytes(b'unregistered external edit')
    before = rows(service, 'work_files')
    with pytest.raises(RuntimeError):
        manager.import_file(source, pid)
    assert rows(service, 'work_files') == before
    assert Path(first['path']).read_bytes() == b'unregistered external edit'
    assert source.read_bytes() == b'new document'


def test_journal_write_failure_restores_file_and_removes_stage(documents, monkeypatch):
    import document_versions
    service, manager, pid, source, first = documents
    source.write_bytes(b'new document')
    before = rows(service, 'work_files')
    def fail(*args, **kwargs):
        raise OSError('injected journal write failure')
    monkeypatch.setattr(document_versions.json, 'dump', fail)
    with pytest.raises(OSError):
        manager.import_file(source, pid)
    assert rows(service, 'work_files') == before
    assert list(Path(first['path']).parent.iterdir()) == [Path(first['path'])]
    assert not list(manager.document_versions.journal_root.glob('*.json'))


def test_directory_collision_is_rejected_without_copying_new_document(documents):
    service, manager, pid, source, first = documents
    source = source.with_name('目录.docx')
    source.write_bytes(b'incoming')
    target = Path(first['path']).parent / source.name
    target.mkdir()
    with pytest.raises(ValueError):
        manager.import_file(source, pid)
    assert target.is_dir()
    assert list(target.iterdir()) == []


def test_marked_reparse_old_document_is_rejected(documents, monkeypatch):
    from types import SimpleNamespace
    service, manager, pid, source, first = documents
    source.write_bytes(b'new document')
    original = Path.lstat
    def marked(path, *args, **kwargs):
        info = original(path, *args, **kwargs)
        if path == Path(first['path']):
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
        return info
    monkeypatch.setattr(Path, 'lstat', marked)
    with pytest.raises(ValueError):
        manager.import_file(source, pid)
    assert Path(first['path']).read_bytes() == b'first document'


def test_interruption_before_journal_does_not_expose_stage_as_project_material(documents):
    from desktop_service import DesktopService
    from project_documents import list_project_documents
    from PySide6.QtCore import QLockFile
    service, manager, pid, source, first = documents
    source.write_bytes(b'new document')
    script = '''
import os, sys
import workspace_manager
from app import WorkBox
service = WorkBox(sys.argv[1])
manager = workspace_manager.WorkspaceManager(service)
original = workspace_manager.copy_verified
def interrupted(*args, **kwargs):
    original(*args, **kwargs)
    os._exit(92)
workspace_manager.copy_verified = interrupted
manager.import_file(sys.argv[2], int(sys.argv[3]))
'''
    result = subprocess.run([sys.executable, '-c', script, str(service.db.path), str(source), str(pid)],
                            cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=30)
    assert result.returncode == 92, result.stderr.decode(errors='replace')
    restarted = DesktopService(service.db.path)
    # 与正式 controller.start 一致：先取得单实例锁，再运行显式恢复。
    session = QLockFile(str(service.db.path) + '.session.lock')
    assert session.tryLock(0)
    try:
        assert restarted.recover_project_changes() == []
        documents = list_project_documents(restarted, pid)
        assert [row['path'] for row in documents['documents']] == [first['path']]
        delivered = restarted.confirm_delivery(pid, confirmed=True)
        with restarted.db.connect() as connection:
            manifest = connection.execute('SELECT manifest FROM project_delivery_archives WHERE id=?',
                                          (delivered['archive']['id'],)).fetchone()[0]
        import json
        assert [row['path'] for row in json.loads(manifest)] == [source.name]
        assert Path(first['path']).read_bytes() == b'first document'
        assert source.read_bytes() == b'new document'
        assert len(list(Path(first['path']).parent.glob('*workbox-*.stage'))) == 1
    finally:
        session.unlock()


@pytest.mark.parametrize('value', ['[]', '{"project_id":1}', '{broken'])
def test_malformed_journal_blocks_service_writes_but_keeps_reads(documents, value):
    from desktop_service import DesktopService
    service, manager, pid, source, first = documents
    journal = manager.document_versions.journal_root
    journal.mkdir()
    path = journal / ('a' * 32 + '.json')
    path.write_text(value, encoding='utf-8')
    restarted = DesktopService(service.db.path)
    errors = restarted.recover_project_changes()
    assert errors and errors[0]['global_block']
    assert errors[0]['error']
    assert restarted.get_project(pid)['name'] == '材料系统'
    with pytest.raises(RuntimeError):
        restarted.confirm_delivery(pid, confirmed=True)
    assert rows(service, 'delivery_events') == []
    assert Path(first['path']).read_bytes() == b'first document'
    assert path.read_text(encoding='utf-8') == value


def test_same_process_failed_rollback_blocks_other_service_writes(documents, monkeypatch):
    from desktop_service import DesktopService
    service, manager, pid, source, first = documents
    desktop = DesktopService(service.db.path)
    desktop.recover_project_changes()
    source.write_bytes(b'new document')
    original = desktop.workspaces.document_versions._rollback
    def rollback_failure(record):
        raise OSError('injected rollback failure')
    def log_failure(*args, **kwargs):
        raise OSError('injected log failure')
    monkeypatch.setattr(desktop.workspaces.document_versions, '_rollback', rollback_failure)
    monkeypatch.setattr(desktop.intake_log, 'append', log_failure)
    with pytest.raises(OSError):
        desktop.workspaces.import_file(source, pid)
    assert desktop.workspaces.document_versions.blocked
    with pytest.raises(RuntimeError):
        desktop.confirm_delivery(pid, confirmed=True)
    assert rows(service, 'delivery_events') == []
    # 真实恢复方法可随后恢复未提交文件；保留现场不要求更改任何原件。
    monkeypatch.setattr(desktop.workspaces.document_versions, '_rollback', original)
    assert desktop.recover_project_changes() == []
    assert Path(first['path']).read_bytes() == b'first document'


def test_external_replacement_during_interruption_is_preserved_and_blocks_recovery(documents):
    import json
    from desktop_service import DesktopService
    from PySide6.QtCore import QLockFile
    service, manager, pid, source, first = documents
    source.write_bytes(b'new document')
    script = '''
import os, sys
from pathlib import Path
from app import WorkBox
from workspace_manager import WorkspaceManager
from native_files import ZipLease
manager = WorkspaceManager(WorkBox(sys.argv[1]))
original = ZipLease.rename_to
def interrupted(self, target):
    original(self, target)
    if Path(target).name == Path(sys.argv[2]).name:
        os._exit(93)
ZipLease.rename_to = interrupted
manager.import_file(sys.argv[2], int(sys.argv[3]))
'''
    result = subprocess.run([sys.executable, '-c', script, str(service.db.path), str(source), str(pid)],
                            cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=30)
    assert result.returncode == 93, result.stderr.decode(errors='replace')
    canonical = Path(first['path'])
    canonical.unlink()
    canonical.write_bytes(b'foreign replacement')
    journal_path = next(manager.document_versions.journal_root.glob('*.json'))
    journal_before = journal_path.read_bytes()
    record = json.loads(journal_before)
    restarted = DesktopService(service.db.path)
    session = QLockFile(str(service.db.path) + '.session.lock')
    assert session.tryLock(0)
    try:
        errors = restarted.recover_project_changes()
    finally:
        session.unlock()
    assert errors and errors[0]['global_block']
    assert canonical.read_bytes() == b'foreign replacement'
    assert Path(record['version_path']).read_bytes() == b'first document'
    assert journal_path.read_bytes() == journal_before
    assert rows(service, 'work_files') == [first]
    with pytest.raises(RuntimeError):
        restarted.confirm_delivery(pid, confirmed=True)
