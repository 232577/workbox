import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from app import WorkBox
from native_files import identity
from workspace_manager import WorkspaceManager, copy_verified
from test_document_versions import documents, rows


def test_rotation_moves_old_copy_outside_workspace_and_records_explicit_successor(documents):
    service, manager, pid, source, first = documents
    source.write_bytes(b'new document')
    latest = manager.import_file(source, pid)['file']
    old = rows(service, 'work_files')[0]
    workspace = Path(manager.get_workspace(pid)['path'])
    assert not Path(old['path']).is_relative_to(workspace)
    assert sorted(path.name for path in workspace.iterdir()) == [source.name]
    assert Path(old['path']).read_bytes() == b'first document'
    assert old['id'] == first['id']
    assert source.read_bytes() == b'new document'
    with service.db.connect() as connection:
        link = dict(connection.execute('SELECT * FROM work_file_supersessions').fetchone())
        root = dict(connection.execute('SELECT * FROM document_history_roots').fetchone())
    assert link['old_file_id'] == first['id']
    assert link['new_file_id'] == latest['id']
    assert link['relative_path'] == source.name
    assert link['history_root_id'] == root['id']
    assert Path(root['path']).name == 'WorkBox历史文稿'
    assert manager.history_roots() == [root['path']]
    assert Path(old['path']).parent == Path(root['path']) / f'WB-{pid:06d}'


def test_first_intake_and_duplicate_do_not_allocate_history(documents):
    service, manager, pid, source, first = documents
    assert manager.history_roots() == []
    assert manager.import_file(source, pid)['duplicate']
    assert manager.history_roots() == []
    assert rows(service, 'work_files') == [first]


def test_registered_local_revision_never_reuses_a_superseded_history_row(documents):
    service, manager, pid, source, first = documents
    source.write_bytes(b'second document')
    current = manager.import_file(source, pid)['file']
    canonical = Path(current['path'])
    canonical.write_bytes(b'first document')
    registered = manager.import_file(canonical, pid)
    assert not registered['duplicate']
    assert registered['file']['id'] != first['id']
    assert registered['file']['path'] == str(canonical)
    with service.db.connect() as connection:
        assert connection.execute('SELECT 1 FROM work_file_supersessions WHERE old_file_id=?',
                                  (registered['file']['id'],)).fetchone() is None


def test_history_root_avoids_shared_groups_and_unregistered_name_collision(tmp_path):
    service = WorkBox(tmp_path / 'data' / 'box.db')
    manager = WorkspaceManager(service)
    ids = service.db.create_capture('a\nb', 'isolated', [('甲系统', False), ('乙系统', False)])['project_ids']
    manager.ensure_projects(ids)
    workspaces = manager.list_workspaces(include_archived=True)
    existing = Path(manager.settings()['work_root']) / 'WorkBox历史文稿'
    existing.mkdir()
    foreign = existing / 'external.txt'
    foreign.write_bytes(b'keep foreign folder')
    source = tmp_path / 'external.docx'
    source.write_bytes(b'first')
    first = manager.import_file(source, ids[1])['file']
    source.write_bytes(b'latest')
    manager.import_file(source, ids[1])
    root = Path(manager.history_roots()[0])
    assert root.name == 'WorkBox历史文稿 (2)'
    assert root.stat().st_dev == Path(first['path']).stat().st_dev
    for workspace in workspaces:
        for key in ('path', 'group_path'):
            protected = Path(workspace[key])
            assert not root.is_relative_to(protected)
            assert not protected.is_relative_to(root)
    assert foreign.read_bytes() == b'keep foreign folder'


def test_history_registration_survives_failed_rotation_transaction(documents):
    service, manager, pid, source, first = documents
    with service.db.connect() as connection:
        connection.execute("CREATE TRIGGER reject_document BEFORE INSERT ON work_files BEGIN SELECT RAISE(ABORT,'injected failure'); END")
    source.write_bytes(b'new document')
    with pytest.raises(Exception, match='injected failure'):
        manager.import_file(source, pid)
    assert len(manager.history_roots()) == 1
    with service.db.connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM work_file_supersessions').fetchone()[0] == 0
    assert rows(service, 'work_files') == [first]
    assert Path(first['path']).read_bytes() == b'first document'
    assert not list(manager.document_versions.journal_root.glob('*.json'))


def test_history_root_is_stable_after_work_root_change_and_restart(documents, tmp_path):
    service, manager, pid, source, first = documents
    source.write_bytes(b'second')
    manager.import_file(source, pid)
    roots = manager.history_roots()
    manager.configure(tmp_path / 'future-work', tmp_path / 'future-archive')
    restarted = WorkspaceManager(WorkBox(service.db.path))
    source.write_bytes(b'third')
    restarted.import_file(source, pid)
    assert restarted.history_roots() == roots
    assert len(list(Path(roots[0]).rglob('*.docx'))) == 2


@pytest.mark.parametrize('kind', ['work', 'archive'])
def test_configuration_cannot_place_managed_roots_over_history(documents, tmp_path, kind):
    _, manager, pid, source, _ = documents
    source.write_bytes(b'second')
    manager.import_file(source, pid)
    root = Path(manager.history_roots()[0])
    with pytest.raises(ValueError):
        manager.configure(root if kind == 'work' else tmp_path / 'next-work',
                          root if kind == 'archive' else tmp_path / 'next-archive')


def test_legacy_v1_journal_recovers_without_history_registration(documents):
    service, manager, pid, source, first = documents
    operation = 'a' * 32
    canonical = Path(first['path'])
    version = canonical.with_name('legacy-version.docx')
    stage = canonical.parent / ('~$workbox-' + operation + '.stage')
    old_identity = identity(canonical.stat())
    source.write_bytes(b'new document')
    new = copy_verified(source, stage)
    canonical.rename(version)
    record = dict(operation_id=operation, project_id=pid, old_path=str(canonical), target_path=str(canonical),
                  version_path=str(version), stage_path=str(stage), old_identity=old_identity,
                  old_sha256=first['sha256'], new_identity=new['identity'], new_sha256=new['sha256'])
    manager.document_versions.journal_root.mkdir()
    journal = manager.document_versions.journal_root / (operation + '.json')
    journal.write_text(json.dumps(record), encoding='utf-8')
    assert manager.document_versions.recover() == []
    assert canonical.read_bytes() == b'first document'
    assert not stage.exists() and not version.exists() and not journal.exists()
    assert manager.history_roots() == []


@pytest.mark.parametrize('tamper', ['root', 'successor'])
def test_v2_recovery_blocks_replaced_history_root_or_inconsistent_link(documents, monkeypatch, tamper):
    service, manager, pid, source, first = documents
    original_finish = manager.document_versions._finish

    def interrupt(record):
        raise OSError('leave committed journal for recovery')

    monkeypatch.setattr(manager.document_versions, '_finish', interrupt)
    source.write_bytes(b'new document')
    with pytest.raises(OSError):
        manager.import_file(source, pid)
    journal = next(manager.document_versions.journal_root.glob('*.json'))
    before = journal.read_bytes()
    if tamper == 'root':
        root = Path(manager.history_roots()[0])
        retained = root.with_name('retained-original-root')
        root.rename(retained)
        root.mkdir()
        (root / 'external.txt').write_bytes(b'external replacement')
    else:
        with service.db.connect() as connection:
            connection.execute('UPDATE work_file_supersessions SET new_file_id=old_file_id')
    monkeypatch.setattr(manager.document_versions, '_finish', original_finish)
    errors = manager.document_versions.recover()
    assert errors and errors[0]['global_block']
    assert journal.read_bytes() == before
    assert Path(first['path']).read_bytes() == b'new document'
    if tamper == 'root':
        assert (root / 'external.txt').read_bytes() == b'external replacement'
        assert list(retained.rglob('*.docx'))


def test_manual_history_requires_confirmation_and_preserves_delivery_facts(documents):
    service, manager, pid, source, first = documents
    with pytest.raises(ValueError):
        manager.archive_existing(pid, first['id'])
    assert manager.history_roots() == []
    with service.db.connect() as connection:
        connection.execute("INSERT INTO delivery_events(project_id,project_name,batch_name,kind,archive_path) VALUES (?,?,?,'manual-files',?)",
                           (pid, '材料系统', '匿名批次', first['path']))
    before = rows(service, 'delivery_events')
    result = manager.archive_existing(pid, first['id'], confirmed=True)
    assert result['project_id'] == pid
    assert result['archived_file_ids'] == [first['id']]
    assert not Path(first['path']).exists()
    assert Path(result['history_path']).read_bytes() == b'first document'
    assert source.read_bytes() == b'first document'
    assert rows(service, 'delivery_events') == before
    with service.db.connect() as connection:
        link = dict(connection.execute('SELECT * FROM work_file_supersessions').fetchone())
    assert link['old_file_id'] == first['id'] and link['new_file_id'] is None
    assert link['relative_path'] == source.name
    source.write_bytes(b'new current document')
    latest = manager.import_file(source, pid)['file']
    assert latest['path'] == first['path']
    assert Path(result['history_path']).read_bytes() == b'first document'


@pytest.mark.parametrize('invalid', ['modified', 'stale-alias', 'zip', 'already-history'])
def test_manual_history_rejects_unverified_or_noncurrent_files(documents, invalid):
    service, manager, pid, source, first = documents
    if invalid == 'modified':
        Path(first['path']).write_bytes(b'external edit')
    elif invalid == 'stale-alias':
        canonical = Path(first['path'])
        canonical.write_bytes(b'local edit')
        first = manager.import_file(canonical, pid)['file']
    elif invalid == 'zip':
        source = source.with_suffix('.zip')
        source.write_bytes(b'zip untouched')
        first = manager.import_file(source, pid)['file']
    else:
        source.write_bytes(b'new current document')
        manager.import_file(source, pid)
    before = rows(service, 'work_files')
    with pytest.raises((ValueError, RuntimeError)):
        manager.archive_existing(pid, first['id'], confirmed=True)
    assert rows(service, 'work_files') == before


def test_manual_history_sql_failure_restores_original_file(documents):
    service, manager, pid, source, first = documents
    with service.db.connect() as connection:
        connection.execute("CREATE TRIGGER reject_history BEFORE INSERT ON work_file_supersessions BEGIN SELECT RAISE(ABORT,'injected history failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match='injected history failure'):
        manager.archive_existing(pid, first['id'], confirmed=True)
    assert rows(service, 'work_files') == [first]
    assert Path(first['path']).read_bytes() == b'first document'
    assert not list(manager.document_versions.journal_root.glob('*.json'))


@pytest.mark.parametrize('phase', ['old-renamed', 'committed'])
def test_manual_history_restart_recovers_rename_and_commit_boundaries(documents, phase):
    service, manager, pid, source, first = documents
    script = '''
import os, sys
from app import WorkBox
from workspace_manager import WorkspaceManager
from native_files import ZipLease
manager = WorkspaceManager(WorkBox(sys.argv[1]))
phase = sys.argv[4]
original = ZipLease.rename_to
def interrupted(self, target):
    original(self, target)
    if phase == 'old-renamed':
        os._exit(94)
ZipLease.rename_to = interrupted
if phase == 'committed':
    manager.document_versions._finish = lambda record: os._exit(94)
manager.archive_existing(int(sys.argv[2]), int(sys.argv[3]), confirmed=True)
'''
    result = subprocess.run([sys.executable, '-c', script, str(service.db.path), str(pid), str(first['id']), phase],
                            cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=30)
    assert result.returncode == 94, result.stderr.decode(errors='replace')
    restarted = WorkspaceManager(WorkBox(service.db.path))
    assert restarted.document_versions.recover() == []
    stored = rows(service, 'work_files')[0]
    assert Path(stored['path']).read_bytes() == b'first document'
    if phase == 'committed':
        assert not Path(first['path']).exists()
        assert Path(stored['path']).is_relative_to(Path(restarted.history_roots()[0]))
    else:
        assert stored == first
    assert source.read_bytes() == b'first document'
    assert not list(restarted.document_versions.journal_root.glob('*.json'))
