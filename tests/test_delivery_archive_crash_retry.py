"""材料复制强退后保留旧现场，并从独立副本完成交付。"""

import hashlib
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QLockFile

from desktop_service import DesktopService


@pytest.fixture
def material(tmp_path):
    service = DesktopService(tmp_path / 'data' / 'anonymous.db')
    pid = service.db.create_capture('匿名系统', '匿名批次', [('匿名系统', False)])['project_ids'][0]
    service.ensure_workspaces(pid)
    service.set_status(pid, '待交付')
    root = Path(service.workspaces.get_workspace(pid)['path'])
    (root / 'a.txt').write_bytes(b'complete first document')
    nested = root / 'nested'
    nested.mkdir()
    (nested / 'b.bin').write_bytes(b'B' * (3 * 1024 * 1024))
    return service, pid, root


def attempts(service):
    with service.db.connect() as connection:
        return [dict(row) for row in connection.execute('SELECT * FROM project_delivery_archive_attempts ORDER BY created_at,id')]


def contents(root):
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob('*') if path.is_file()}


@pytest.mark.parametrize('temporary_format', ['current', 'legacy'])
def test_crashed_archive_copy_retries_without_changing_original_attempt(material, temporary_format):
    service, pid, root = material
    before = contents(root)
    script = '''
import os, sys
from desktop_service import DesktopService
service = DesktopService(sys.argv[1])
def interrupted(done, total):
    if done > len(b'complete first document'):
        os._exit(96)
service.confirm_delivery(int(sys.argv[2]), confirmed=True, progress=interrupted)
'''
    process = subprocess.run([sys.executable, '-c', script, str(service.db.path), str(pid)],
                             cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=30)
    assert process.returncode == 96, process.stderr.decode(errors='replace')
    old_attempt, = attempts(service)
    old_target = Path(old_attempt['path'])
    fragment, = old_target.rglob('*.partial')
    assert 0 < fragment.stat().st_size < (root / 'nested' / 'b.bin').stat().st_size
    if temporary_format == 'legacy':
        fragment = fragment.rename(fragment.with_name('.' + 'a' * 32 + '.partial'))
    old_contents = contents(old_target)
    assert (old_target / 'a.txt').read_bytes() == (root / 'a.txt').read_bytes()

    restarted = DesktopService(service.db.path)
    session = QLockFile(str(service.db.path) + '.session.lock')
    session.setStaleLockTime(0)
    assert session.tryLock(0)
    try:
        assert restarted.recover_project_changes() == []
        delivered = restarted.confirm_delivery(pid, confirmed=True)
        target = Path(delivered['archive']['path'])
        assert target != old_target
        assert contents(target) == before
        assert contents(root) == before
        assert contents(old_target) == old_contents
        records = attempts(restarted)
        assert len(records) == 2
        assert next(row for row in records if row['id'] == old_attempt['id']) == old_attempt
        assert next(row for row in records if row['path'] == str(target))['state'] == 'registered'
        assert restarted.get_project(pid)['status'] == '已交付'
        with restarted.db.connect() as connection:
            assert connection.execute('SELECT COUNT(*) FROM delivery_events').fetchone()[0] == 1
            assert connection.execute('SELECT COUNT(*) FROM project_delivery_archives').fetchone()[0] == 1
        again = restarted.confirm_delivery(pid, confirmed=True)
        assert again['archive']['id'] == delivered['archive']['id']
        assert len(attempts(restarted)) == 2
    finally:
        session.unlock()


def pending_copy(service, pid, monkeypatch):
    complete = service.db._complete_file_delivery

    def fail(*args, **kwargs):
        complete(*args, **kwargs)
        raise RuntimeError('injected database rollback')

    with monkeypatch.context() as patch:
        patch.setattr(service.db, '_complete_file_delivery', fail)
        with pytest.raises(RuntimeError, match='injected database rollback'):
            service.confirm_delivery(pid, confirmed=True)
    record, = attempts(service)
    return Path(record['path'])


@pytest.mark.parametrize('change', ['modified', 'unknown', 'office-name', 'locked', 'locked-fragment',
                                  'reparse', 'fragment-and-unknown', 'fragment-and-modified'])
def test_pending_attempt_protection_is_not_bypassed_by_starting_another_copy(material, monkeypatch, change):
    service, pid, root = material
    target = pending_copy(service, pid, monkeypatch)
    saved = target / 'a.txt'
    protected = saved
    if change.startswith('fragment-and-'):
        (target / ('~$workbox-' + 'd' * 32 + '.partial')).write_bytes(b'partial')
    if change in ('modified', 'fragment-and-modified'):
        saved.write_bytes(b'changed complete file')
    elif change in ('unknown', 'office-name', 'fragment-and-unknown'):
        protected = target / ('foreign.txt' if change == 'unknown' else '~$foreign.docx')
        protected.write_bytes(b'unknown material')
    elif change == 'locked-fragment':
        protected = target / ('.' + 'e' * 32 + '.partial')
        protected.write_bytes(b'locked partial')
    elif change == 'reparse':
        protected = target / ('~$workbox-' + 'b' * 32 + '.partial')
        protected.write_bytes(b'not an ordinary file')
        original_lstat = Path.lstat

        def marked(path, *args, **kwargs):
            info = original_lstat(path, *args, **kwargs)
            if path == protected:
                return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
            return info

        monkeypatch.setattr(Path, 'lstat', marked)
    before = protected.read_bytes()
    if change in ('locked', 'locked-fragment'):
        # 写句柄与只读内容租约冲突；不能把占用当成可绕过的碎片。
        with protected.open('r+b'), pytest.raises((OSError, RuntimeError, ValueError)):
            service.confirm_delivery(pid, confirmed=True)
    else:
        with pytest.raises((OSError, RuntimeError, ValueError)):
            service.confirm_delivery(pid, confirmed=True)
    assert protected.read_bytes() == before
    assert len(attempts(service)) == 1
    assert service.get_project(pid)['status'] == '待交付'
    with service.db.connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM delivery_events').fetchone()[0] == 0


def test_manifest_member_with_legacy_temporary_name_still_resumes_same_attempt(material, monkeypatch):
    service, pid, root = material
    name = '.' + 'c' * 32 + '.partial'
    (root / name).write_bytes(b'legitimate source material')
    target = pending_copy(service, pid, monkeypatch)
    before = contents(target)
    delivered = service.confirm_delivery(pid, confirmed=True)
    assert Path(delivered['archive']['path']) == target
    assert contents(target) == before == contents(root)
    assert len(attempts(service)) == 1
    assert name in [row['path'] for row in delivered['archive']['manifest']]


@pytest.mark.parametrize('name', ['~$workbox-' + 'f' * 32 + '.partial', '.' + 'f' * 32 + '.partial'])
def test_registered_archive_with_an_extra_fragment_is_not_replaced(material, name):
    service, pid, root = material
    delivered = service.confirm_delivery(pid, confirmed=True)
    target = Path(delivered['archive']['path'])
    fragment = target / name
    fragment.write_bytes(b'added after verified delivery')
    service.set_status(pid, '待交付')
    with pytest.raises((OSError, RuntimeError, ValueError)):
        service.confirm_delivery(pid, confirmed=True)
    assert fragment.read_bytes() == b'added after verified delivery'
    assert len(attempts(service)) == 1
    with service.db.connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM delivery_events').fetchone()[0] == 1
        assert connection.execute('SELECT COUNT(*) FROM project_delivery_archives').fetchone()[0] == 1
