"""程序文件事务只使用匿名夹具，失败和中断都保留业务哨兵。"""
import hashlib
import importlib
import json
import os
from pathlib import Path
import threading
import zipfile

import pytest

from delta_format import REQUIRED_FILES, FORMAT, delta_filename


OLD, NEW = 'v1.0.0.20261113.2', 'v1.0.0.20261113.3'


def records(files):
    return {name: {'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)}
            for name, data in files.items()}


@pytest.fixture
def candidate(tmp_path):
    base = {name: ('anonymous old ' + name).encode() for name in REQUIRED_FILES}
    base['WorkBoxUpdater.exe'] = b'MZanonymous helper; never execute'
    base['_internal/obsolete.dll'] = b'obsolete'
    target = dict(base)
    target['WorkBox.exe'] = b'anonymous new executable; never executed'
    target['_internal/new.dll'] = b'new library'
    del target['_internal/obsolete.dll']
    app = tmp_path / 'app'
    for name, data in base.items():
        path = app / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    data_dir = app / 'data'
    data_dir.mkdir()
    (data_dir / 'workbox.db').write_bytes(b'anonymous business sentinel, not SQLite')
    (app / 'personal.txt').write_bytes(b'unknown owner file')
    (app / 'unins000.exe').write_bytes(b'uninstaller sentinel')
    manifest = dict(format=FORMAT, from_tag=OLD, to_tag=NEW, base=records(base), target=records(target))
    package = tmp_path / delta_filename(OLD, NEW)
    with zipfile.ZipFile(package, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('delta.json', json.dumps(manifest))
        for name in sorted(target):
            if target[name] != base.get(name):
                archive.writestr('payload/' + name, target[name])
    args = dict(expected_sha256=hashlib.sha256(package.read_bytes()).hexdigest(),
                expected_size=package.stat().st_size, from_tag=OLD, to_tag=NEW, data_dir=data_dir)
    return app, package, manifest, args


def module():
    return importlib.import_module('incremental_apply')


def preserved(app):
    assert (app / 'data/workbox.db').read_bytes() == b'anonymous business sentinel, not SQLite'
    assert (app / 'personal.txt').read_bytes() == b'unknown owner file'
    assert (app / 'unins000.exe').read_bytes() == b'uninstaller sentinel'


def test_changed_added_removed_files_apply_and_probe_before_commit(candidate):
    app, package, manifest, args = candidate
    probes = []
    def probe(root, data_dir, tag):
        module().verify_tree(root, manifest['target'])
        probes.append((root, data_dir, tag))
    result = module().apply_update(package, app, **args, probe=probe)
    assert result['status'] == 'committed'
    assert probes == [(app, args['data_dir'], NEW)]
    assert not module().has_pending_update(app)
    module().verify_tree(app, manifest['target'])
    preserved(app)


@pytest.mark.parametrize('changed', ['WorkBox.exe', '_internal/extra.dll'])
def test_mismatched_or_extra_managed_files_refuse_before_mutation(candidate, changed):
    app, package, manifest, args = candidate
    (app / changed).write_bytes(b'local changed bytes')
    with pytest.raises((ValueError, OSError)):
        module().apply_update(package, app, **args, probe=lambda *a: None)
    assert not list(app.glob('.workbox-update-*'))
    assert (app / changed).read_bytes() == b'local changed bytes'
    preserved(app)


@pytest.mark.parametrize('point', ['after-backup', 'after-replace', 'after-remove', 'before-probe'])
def test_any_commit_failure_restores_base(candidate, point):
    app, package, manifest, args = candidate
    def fail(phase, name=None):
        if phase == point:
            raise OSError('anonymous injected failure')
    with pytest.raises(module().UpdateApplyError) as caught:
        module().apply_update(package, app, **args, probe=lambda *a: None, _fault=fail)
    assert caught.value.rollback_complete
    module().verify_tree(app, manifest['base'])
    assert not module().has_pending_update(app)
    preserved(app)


def test_wrong_build_probe_rolls_back(candidate):
    app, package, manifest, args = candidate
    with pytest.raises(module().UpdateApplyError) as caught:
        module().apply_update(package, app, **args, probe=lambda *a: False)
    assert caught.value.rollback_complete
    module().verify_tree(app, manifest['base'])
    preserved(app)


@pytest.mark.skipif(os.name != 'nt', reason='Windows Internet-origin metadata')
def test_new_payload_keeps_internet_origin_and_rollback_preserves_old_marker(candidate):
    app, package, manifest, args = candidate
    marker = Path(str(app / 'WorkBox.exe') + ':Zone.Identifier')
    original = b'[ZoneTransfer]\r\nZoneId=3\r\nHostUrl=https://github.com/old-origin\r\n'
    marker.write_bytes(original)
    with pytest.raises(module().UpdateApplyError):
        module().apply_update(package, app, **args, probe=lambda *a: False)
    assert marker.read_bytes() == original
    module().apply_update(package, app, **args, probe=lambda *a: None)
    text = marker.read_text(encoding='utf-8')
    assert 'ZoneId=3' in text and NEW in text and package.name in text


@pytest.mark.skipif(os.name != 'nt', reason='Windows named streams')
def test_recovery_does_not_discard_new_unknown_named_stream(candidate):
    app, package, manifest, args = candidate
    class Crash(BaseException):
        pass
    def interrupt(phase, name=None):
        if phase == 'before-probe':
            raise Crash()
    with pytest.raises(Crash):
        module().apply_update(package, app, **args, probe=lambda *a: None, _fault=interrupt)
    stream = Path(str(app / 'WorkBox.exe') + ':AnonymousReview')
    stream.write_bytes(b'unknown external metadata')
    transaction, = app.glob('.workbox-update-*')
    with pytest.raises((ValueError, RuntimeError)):
        module().recover_update(transaction, app)
    assert stream.read_bytes() == b'unknown external metadata'
    assert module().has_pending_update(app)


@pytest.mark.skipif(os.name != 'nt', reason='Windows named streams')
def test_recovery_rejects_changed_backup_origin_metadata(candidate):
    app, package, manifest, args = candidate
    class Crash(BaseException):
        pass
    def interrupt(phase, name=None):
        if phase == 'before-probe':
            raise Crash()
    with pytest.raises(Crash):
        module().apply_update(package, app, **args, probe=lambda *a: None, _fault=interrupt)
    transaction, = app.glob('.workbox-update-*')
    marker = Path(str(transaction / 'backup/WorkBox.exe') + ':Zone.Identifier')
    changed = b'[ZoneTransfer]\nZoneId=4\nHostUrl=https://external.invalid/change\n'
    marker.write_bytes(changed)
    with pytest.raises((ValueError, RuntimeError)):
        module().recover_update(transaction, app)
    assert marker.read_bytes() == changed
    assert module().has_pending_update(app)


@pytest.mark.skipif(os.name != 'nt', reason='Windows handle-bound publication')
def test_last_moment_destination_occupant_is_preserved(candidate, monkeypatch):
    import update_file_ops
    app, package, manifest, args = candidate
    original = update_file_ops._native_rename
    def occupy(lease, parent, leaf):
        if parent.path == app and leaf == 'WorkBox.exe' and 'staged' in lease.path.parts:
            (app / 'WorkBox.exe').write_bytes(b'unknown last-moment occupant')
        return original(lease, parent, leaf)
    monkeypatch.setattr(update_file_ops, '_native_rename', occupy)
    with pytest.raises(module().UpdateApplyError) as caught:
        module().apply_update(package, app, **args, probe=lambda *a: None)
    assert not caught.value.rollback_complete
    assert (app / 'WorkBox.exe').read_bytes() == b'unknown last-moment occupant'
    assert module().has_pending_update(app)


def test_interrupted_commit_is_detected_and_recovered(candidate):
    app, package, manifest, args = candidate
    class Crash(BaseException):
        pass
    def interrupt(phase, name=None):
        if phase == 'after-replace':
            raise Crash()
    with pytest.raises(Crash):
        module().apply_update(package, app, **args, probe=lambda *a: None, _fault=interrupt)
    assert module().has_pending_update(app)
    transaction, = app.glob('.workbox-update-*')
    result = module().recover_update(transaction, app)
    assert result['status'] == 'rolled-back'
    module().verify_tree(app, manifest['base'])
    assert not module().has_pending_update(app)
    preserved(app)


def test_recovery_conflict_keeps_external_bytes_and_backup(candidate):
    app, package, manifest, args = candidate
    class Crash(BaseException):
        pass
    def interrupt(phase, name=None):
        if phase == 'before-probe':
            raise Crash()
    with pytest.raises(Crash):
        module().apply_update(package, app, **args, probe=lambda *a: None, _fault=interrupt)
    (app / 'WorkBox.exe').write_bytes(b'unknown external edit')
    transaction, = app.glob('.workbox-update-*')
    with pytest.raises((ValueError, RuntimeError)):
        module().recover_update(transaction, app)
    assert (app / 'WorkBox.exe').read_bytes() == b'unknown external edit'
    assert (transaction / 'backup/WorkBox.exe').is_file()
    assert module().has_pending_update(app)
    preserved(app)


@pytest.mark.parametrize('changed', ['WorkBox.exe', '_internal/new.dll', 'backup'])
def test_recovery_rechecks_conflicts_after_preflight(candidate, monkeypatch, changed):
    app, package, manifest, args = candidate
    class Crash(BaseException):
        pass
    def interrupt(phase, name=None):
        if phase == 'before-probe':
            raise Crash()
    with pytest.raises(Crash):
        module().apply_update(package, app, **args, probe=lambda *a: None, _fault=interrupt)
    transaction, = app.glob('.workbox-update-*')
    original = module()._write_json
    victim = transaction / 'backup/WorkBox.exe' if changed == 'backup' else app / changed
    def change_after_preflight(path, value):
        original(path, value)
        if value['phase'] == 'rolling-back':
            victim.write_bytes(b'external edit after recovery preflight')
    monkeypatch.setattr(module(), '_write_json', change_after_preflight)
    with pytest.raises((ValueError, RuntimeError)):
        module().recover_update(transaction, app)
    assert victim.read_bytes() == b'external edit after recovery preflight'
    assert module().has_pending_update(app)
    preserved(app)


@pytest.mark.skipif(os.name != 'nt', reason='Windows short-lived image/file locks')
def test_short_file_lock_retries_and_rechecks_before_overwrite(candidate, monkeypatch):
    app, package, manifest, args = candidate
    original = module().swap_checked
    attempts = []
    def temporarily_busy(source, destination, *values, **options):
        if Path(destination) == app / 'WorkBox.exe' and 'staged' in Path(source).parts:
            attempts.append(1)
            if len(attempts) <= 2:
                raise OSError(32, 'anonymous short file lock')
        return original(source, destination, *values, **options)
    monkeypatch.setattr(module(), 'swap_checked', temporarily_busy)
    assert module().apply_update(package, app, **args, probe=lambda *a: None)['status'] == 'committed'
    assert len(attempts) == 3
    preserved(app)


@pytest.mark.skipif(os.name != 'nt', reason='Windows short-lived image/file locks')
def test_recovery_retry_never_overwrites_a_new_external_edit(candidate, monkeypatch):
    app, package, manifest, args = candidate
    original = module().swap_checked
    def occupied_then_changed(source, destination, *values, **options):
        if Path(destination) == app / 'WorkBox.exe' and 'backup' in Path(source).parts:
            (app / 'WorkBox.exe').write_bytes(b'external bytes during lock retry')
            raise OSError(32, 'anonymous short file lock')
        return original(source, destination, *values, **options)
    monkeypatch.setattr(module(), 'swap_checked', occupied_then_changed)
    with pytest.raises(module().UpdateApplyError) as caught:
        module().apply_update(package, app, **args, probe=lambda *a: False)
    assert not caught.value.rollback_complete
    assert (app / 'WorkBox.exe').read_bytes() == b'external bytes during lock retry'
    assert module().has_pending_update(app)


def test_cancelled_baseline_read_and_active_runtime_do_not_mutate(candidate):
    from concurrent.futures import CancelledError
    app, package, manifest, args = candidate
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(CancelledError):
        module().verify_tree(app, manifest['base'], cancel_event=cancel)
    with module().runtime_guard(app):
        with pytest.raises((OSError, RuntimeError)):
            module().apply_update(package, app, **args, probe=lambda *a: None)
    module().verify_tree(app, manifest['base'])
    preserved(app)


def test_malformed_pending_journal_blocks_without_touching_files(candidate):
    app, package, manifest, args = candidate
    folder = app / ('.workbox-update-' + 'f' * 32)
    folder.mkdir()
    (folder / 'journal.json').write_text('{}')
    assert module().has_pending_update(app)
    with pytest.raises((RuntimeError, ValueError)):
        module().apply_update(package, app, **args, probe=lambda *a: None)
    module().verify_tree(app, manifest['base'])
    preserved(app)


def test_crash_before_first_journal_never_registers_an_unrecoverable_transaction(candidate, monkeypatch):
    app, package, manifest, args = candidate
    class Crash(BaseException):
        pass
    def crash(path, value):
        raise Crash()
    monkeypatch.setattr(module(), '_write_json', crash)
    with pytest.raises(Crash):
        module().apply_update(package, app, **args, probe=lambda *a: None)
    assert not module().has_pending_update(app)
    assert not list(app.glob('.workbox-update-*'))
    module().verify_tree(app, manifest['base'])
    preserved(app)
