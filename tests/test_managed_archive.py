import hashlib
import importlib
import importlib.util
from concurrent.futures import CancelledError
from pathlib import Path
from threading import Event
from zipfile import ZipFile

import pytest

from app import WorkBox


def archive(tmp_path):
    assert importlib.util.find_spec('managed_archive'), 'Managed ZIP movement is not implemented'
    module = importlib.import_module('managed_archive')
    service = WorkBox(tmp_path / 'data' / 'workbox.db')
    service.capture_message('1. 客户档案系统\n2. 合同工具', '今日收件')
    return module.ManagedArchive(service, tmp_path / 'data' / 'packages'), service


def make_zip(directory, name='客户档案系统.zip', content='isolated package'):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    with ZipFile(path, 'w') as stream:
        stream.writestr('readme.txt', content)
    return path


def test_move_removes_source_and_records_verified_managed_destination(tmp_path):
    manager, service = archive(tmp_path)
    source = make_zip(tmp_path / 'incoming')
    before, inode = source.read_bytes(), source.stat().st_ino
    result = manager.import_zip(source)
    dest = Path(result['zip']['path'])
    assert not source.exists()
    assert dest.is_relative_to(manager.root)
    assert dest.read_bytes() == before
    assert dest.stat().st_ino == inode, 'same volume should rename, not copy'
    assert result['moved'] is True
    assert result['zip']['sha256'] == hashlib.sha256(before).hexdigest()
    assert service.get_project(1)['status'] == '待交付'
    assert '今日收件' in str(dest) and '客户档案系统' in str(dest)
    assert manager.recover() == []


def test_invalid_archive_and_precancel_leave_source_untouched(tmp_path):
    manager, service = archive(tmp_path)
    source = tmp_path / 'bad.zip'
    source.write_bytes(b'not a zip')
    with pytest.raises(ValueError):
        manager.import_zip(source)
    assert source.read_bytes() == b'not a zip'
    valid = make_zip(tmp_path / 'incoming')
    stop = Event()
    stop.set()
    with pytest.raises(CancelledError):
        manager.import_zip(valid, cancel_event=stop)
    assert valid.exists() and service.db.counts()['zips'] == 0


def test_owned_drop_is_idempotent_and_never_deletes_managed_file(tmp_path):
    manager, service = archive(tmp_path)
    first = manager.import_zip(make_zip(tmp_path / 'incoming'))
    dest = Path(first['zip']['path'])
    second = manager.import_zip(dest)
    assert second['zip']['id'] == first['zip']['id']
    assert second['duplicate'] is True and dest.exists()
    assert service.db.counts()['zips'] == 1


def test_same_content_resubmitted_moves_away_extra_input_without_extra_package(tmp_path):
    manager, service = archive(tmp_path)
    source = make_zip(tmp_path / 'incoming')
    content = source.read_bytes()
    first = manager.import_zip(source)
    other = tmp_path / 'incoming' / '客户档案系统_v1.0.zip'
    other.write_bytes(content)
    second = manager.import_zip(other)
    assert not other.exists()
    assert second['zip']['id'] == first['zip']['id']
    assert len(list(manager.root.rglob('*.zip'))) == 1
    assert service.db.counts()['zips'] == 1


def test_different_content_with_same_filename_never_overwrites(tmp_path):
    manager, service = archive(tmp_path)
    first = manager.import_zip(make_zip(tmp_path / 'incoming', content='version one'))
    original = Path(first['zip']['path']).read_bytes()
    second = manager.import_zip(make_zip(tmp_path / 'incoming', content='version two'))
    assert first['zip']['path'] != second['zip']['path']
    assert Path(first['zip']['path']).read_bytes() == original
    assert service.db.counts()['zips'] == 2


def test_unmatched_package_is_managed_then_physically_relocated_when_linked(tmp_path):
    manager, service = archive(tmp_path)
    source = make_zip(tmp_path / 'incoming', 'unknown.zip')
    result = manager.import_zip(source)
    old = Path(result['zip']['path'])
    assert '待关联' in str(old) and not source.exists()
    assert result['zip']['project_id'] is None
    manager.link_zip(result['zip']['id'], 2)
    record = service.db.get_zip(result['zip']['id'])
    assert record['project_id'] == 2 and not old.exists()
    assert '合同工具' in record['path'] and Path(record['path']).exists()
    assert service.db.counts()['projects'] == 2


def test_ambiguous_name_does_not_move_into_an_arbitrary_project(tmp_path):
    manager, service = archive(tmp_path)
    service.capture_message('客户档案系统', '另一批')
    result = manager.import_zip(make_zip(tmp_path / 'incoming'))
    assert result['zip']['project_id'] is None
    assert len(result['candidates']) == 2


def test_register_failure_recovers_original_or_verified_destination_not_data_loss(tmp_path, monkeypatch):
    manager, service = archive(tmp_path)
    source = make_zip(tmp_path / 'incoming')
    before = source.read_bytes()
    def fail(*args, **kwargs):
        raise OSError('simulated SQLite commit failure')
    with monkeypatch.context() as patch:
        patch.setattr(manager, '_commit_record', fail)
        with pytest.raises(OSError):
            manager.import_zip(source)
    copies = ([source] if source.exists() else []) + list(manager.root.rglob('*.zip'))
    assert copies and all(path.read_bytes() == before for path in copies)
    manager.recover()
    assert service.db.counts()['zips'] == 1
    assert Path(service.list_zips()[0]['path']).read_bytes() == before


def test_existing_references_are_not_moved_by_initialization_or_recovery(tmp_path):
    manager, service = archive(tmp_path)
    source = make_zip(tmp_path / 'legacy')
    service.import_zip(source)
    manager.recover()
    assert source.exists()
    assert service.list_zips()[0]['path'] == str(source.resolve())


def test_source_is_locked_against_replacement_during_hashing(tmp_path):
    manager, service = archive(tmp_path)
    source = make_zip(tmp_path / 'incoming')
    denied = []
    def progress(done, total):
        try:
            source.write_bytes(b'replacement')
        except OSError:
            denied.append(True)
    result = manager.import_zip(source, progress=progress)
    assert denied and Path(result['zip']['path']).read_bytes().startswith(b'PK')


def test_cross_volume_copy_branch_checks_hash_before_source_removal(tmp_path, monkeypatch):
    manager, service = archive(tmp_path)
    native = importlib.import_module('native_files')
    original = native.ZipLease.rename_to
    source = make_zip(tmp_path / 'incoming')
    before = source.read_bytes()
    def cross_volume(lease, target):
        if lease.path == source.resolve():
            error = OSError('simulated different volume')
            error.winerror = 17
            raise error
        return original(lease, target)
    monkeypatch.setattr(native.ZipLease, 'rename_to', cross_volume)
    result = manager.import_zip(source)
    assert not source.exists() and Path(result['zip']['path']).read_bytes() == before
    assert result['method'] == 'verified-copy-delete'
    assert not list(manager.root.rglob('*.partial'))


def test_directory_and_symlink_are_not_moved(tmp_path):
    manager, service = archive(tmp_path)
    directory = tmp_path / 'directory.zip'
    directory.mkdir()
    with pytest.raises((ValueError, OSError)):
        manager.import_zip(directory)
    assert directory.is_dir()


def force_cross_volume(monkeypatch, source):
    native = importlib.import_module('native_files')
    original = native.ZipLease.rename_to
    def cross(lease, target):
        if lease.path == source.resolve():
            error = OSError('different volume')
            error.winerror = 17
            raise error
        return original(lease, target)
    monkeypatch.setattr(native.ZipLease, 'rename_to', cross)


def test_copy_never_deletes_a_preexisting_unowned_temporary_path(tmp_path, monkeypatch):
    from types import SimpleNamespace
    manager, service = archive(tmp_path)
    source = make_zip(tmp_path / 'incoming')
    native = importlib.import_module('native_files')
    with native.ZipLease(source) as lease:
        target = manager._target(lease.inspect(), 1)
    sentinel = target.parent / ('.' + 'a' * 32 + '.partial')
    sentinel.write_bytes(b'not owned by this transfer')
    monkeypatch.setattr(importlib.import_module('managed_archive'), 'uuid4', lambda: SimpleNamespace(hex='a' * 32))
    force_cross_volume(monkeypatch, source)
    with pytest.raises(FileExistsError):
        manager.import_zip(source)
    assert sentinel.read_bytes() == b'not owned by this transfer'
    assert source.exists()


def test_cancel_during_copy_keeps_original_and_discards_only_owned_partial(tmp_path, monkeypatch):
    manager, service = archive(tmp_path)
    source = make_zip(tmp_path / 'incoming', content='x' * 2_000_000)
    before = source.read_bytes()
    force_cross_volume(monkeypatch, source)
    stop, calls = Event(), []
    def progress(done, total):
        calls.append(done)
        if len(calls) == 3:
            stop.set()
    with pytest.raises(CancelledError):
        manager.import_zip(source, cancel_event=stop, progress=progress)
    assert source.read_bytes() == before
    assert not list(manager.root.rglob('*.partial'))
    assert service.db.counts()['zips'] == 0


def test_relink_to_project_with_identical_owned_package_leaves_no_dangling_record(tmp_path):
    manager, service = archive(tmp_path)
    source = make_zip(tmp_path / 'incoming')
    content = source.read_bytes()
    manager.import_zip(source)
    other = tmp_path / 'incoming' / 'unknown.zip'
    other.write_bytes(content)
    pending = manager.import_zip(other)
    manager.link_zip(pending['zip']['id'], 1)
    records = service.list_zips()
    assert all(Path(record['path']).is_file() for record in records)


def test_cross_volume_preserves_zone_identifier_security_metadata(tmp_path, monkeypatch):
    manager, service = archive(tmp_path)
    source = make_zip(tmp_path / 'incoming')
    zone = b'[ZoneTransfer]\r\nZoneId=3\r\n'
    Path(str(source) + ':Zone.Identifier').write_bytes(zone)
    force_cross_volume(monkeypatch, source)
    result = manager.import_zip(source)
    assert Path(result['zip']['path'] + ':Zone.Identifier').read_bytes() == zone
    assert not source.exists()


def test_recovery_never_reapplies_status_after_record_was_committed(tmp_path, monkeypatch):
    manager, service = archive(tmp_path)
    source = make_zip(tmp_path / 'incoming')
    force_cross_volume(monkeypatch, source)
    native = importlib.import_module('native_files')
    remove = native.ZipLease.remove_after_verified_commit
    def deny(lease):
        if lease.path == source.resolve():
            raise PermissionError('simulated source removal denied')
        return remove(lease)
    monkeypatch.setattr(native.ZipLease, 'remove_after_verified_commit', deny)
    with pytest.raises(PermissionError):
        manager.import_zip(source)
    assert source.exists() and service.db.counts()['zips'] == 1
    service.set_status(1, '开发中')
    assert manager.recover()
    assert service.get_project(1)['status'] == '开发中'


def test_unowned_partial_is_reported_not_silently_abandoned_as_complete(tmp_path):
    manager, service = archive(tmp_path)
    source = make_zip(tmp_path / 'incoming')
    native = importlib.import_module('native_files')
    with native.ZipLease(source, movable=False) as lease:
        info = lease.inspect()
    target = manager._target(info, 1)
    operation = manager._prepare(source, target, info, 1, None)
    partial = target.with_name('.' + operation + '.partial')
    partial.write_bytes(b'unknown temporary contents')
    unresolved = manager.recover()
    assert unresolved and partial.read_bytes() == b'unknown temporary contents'
    assert source.exists()


def test_relink_deduplicates_same_project_same_content_without_extra_file(tmp_path):
    manager, service = archive(tmp_path)
    source = make_zip(tmp_path / 'incoming')
    content = source.read_bytes()
    first = manager.import_zip(source)
    other = tmp_path / 'incoming' / 'unknown.zip'
    other.write_bytes(content)
    pending = manager.import_zip(other)
    result = manager.link_zip(pending['zip']['id'], 1)
    assert len(list(manager.root.rglob('*.zip'))) == 1
    assert service.db.counts()['zips'] == 1
    assert result['zip']['id'] == first['zip']['id']
    assert manager.recover() == []
