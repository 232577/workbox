"""Exercise export publication races with real filesystem operations in a second process."""

import os
import subprocess
import sys
import threading
from concurrent.futures import CancelledError
from contextlib import contextmanager
from zipfile import is_zipfile

import pytest

import delivery_export
from database import Database
from native_files import ZipLease


EXTERNAL_CONTENT = b'anonymous file from another process'
PREVIOUS_CONTENT = b'anonymous previously confirmed export'
WINDOWS_HANDLES = pytest.mark.skipif(os.name != 'nt', reason='Windows 文件句柄移动语义')


@pytest.fixture
def export_target(tmp_path):
    db = Database(tmp_path / 'isolated.db')
    db.create_capture('匿名消息', '匿名批次', [('匿名项目', False)])
    output = tmp_path / 'output'
    output.mkdir()
    return db, output / 'report.xlsx'


def _external_python(program, *paths):
    return subprocess.run(
        [sys.executable, '-I', '-c', program, *map(str, paths)],
        capture_output=True, encoding='utf-8', errors='replace', timeout=30,
    )


def _external_replace(source, destination):
    result = _external_python(
        'import os, sys\n'
        'try:\n'
        '    os.replace(sys.argv[1], sys.argv[2])\n'
        'except PermissionError:\n'
        '    sys.exit(73)\n',
        source, destination,
    )
    assert result.returncode in (0, 73), result.stderr
    return result.returncode == 0


def _compete_for_temporary(db, target, *, cancel_event=None):
    external = target.parent / 'external.xlsx'
    external.write_bytes(EXTERNAL_CONTENT)
    observed = {}

    @contextmanager
    def publication_lock():
        temporary, = target.parent.glob('.workbox-export-*.xlsx')
        observed['path'] = temporary
        observed['replaced'] = _external_replace(external, temporary)
        if cancel_event is not None:
            cancel_event.set()
        yield

    # This process-local lock marks the beginning of the publication checks.
    # It does not give the other process any special access to the staged file.
    db._operation_lock = publication_lock()
    return observed


def test_export_does_not_publish_an_external_replacement_of_the_temporary_file(export_target):
    db, target = export_target
    observed = _compete_for_temporary(db, target)

    try:
        delivery_export.export_delivered_projects(db, target)
    except (OSError, RuntimeError, ValueError):
        assert observed['replaced']
    if observed['replaced']:
        assert not target.exists(), '导出发布了其他进程放入临时路径的文件'
        assert observed['path'].read_bytes() == EXTERNAL_CONTENT
    else:
        assert is_zipfile(target)


def test_cancelled_export_keeps_external_file_at_the_temporary_path(export_target):
    db, target = export_target
    stop = threading.Event()
    observed = _compete_for_temporary(db, target, cancel_event=stop)

    with pytest.raises(CancelledError):
        delivery_export.export_delivered_projects(db, target, cancel_event=stop)

    assert not target.exists()
    if observed['replaced']:
        assert observed['path'].exists(), '取消导出删除了其他进程放入临时路径的文件'
        assert observed['path'].read_bytes() == EXTERNAL_CONTENT
    else:
        assert not observed['path'].exists()


def test_new_export_refuses_a_destination_created_by_another_process(export_target):
    db, target = export_target
    external = target.parent / 'external.xlsx'
    external.write_bytes(EXTERNAL_CONTENT)

    @contextmanager
    def publication_lock():
        assert _external_replace(external, target)
        yield

    db._operation_lock = publication_lock()
    with pytest.raises(FileExistsError):
        delivery_export.export_delivered_projects(db, target)

    assert target.read_bytes() == EXTERNAL_CONTENT


@WINDOWS_HANDLES
def test_confirmed_overwrite_preserves_a_replacement_after_the_final_identity_read(export_target, monkeypatch):
    db, target = export_target
    target.write_bytes(PREVIOUS_CONTENT)
    external = target.parent / 'external.xlsx'
    external.write_bytes(EXTERNAL_CONTENT)
    target_identity = delivery_export.target_identity
    confirmed = target_identity(target)
    read_count = 0
    replaced = False

    def read_target_identity(path):
        nonlocal read_count, replaced
        identity = target_identity(path)
        read_count += 1
        if read_count == 2:
            # The real identity read has ended. Another process can run before
            # this call returns and before the subsequent native replace.
            replaced = _external_replace(external, target)
        return identity

    monkeypatch.setattr(delivery_export, 'target_identity', read_target_identity)
    try:
        delivery_export.export_delivered_projects(
            db, target, replace_existing=True, expected_target_identity=confirmed,
        )
    except (OSError, RuntimeError, ValueError):
        assert replaced
    assert read_count >= 2
    if replaced:
        assert target.read_bytes() == EXTERNAL_CONTENT, '导出覆盖了未授权替换的目标文件'
    else:
        assert is_zipfile(target)


@WINDOWS_HANDLES
def test_verified_staging_handle_blocks_a_second_process_until_publication(export_target, monkeypatch):
    db, target = export_target
    external = target.parent / 'external.xlsx'
    external.write_bytes(EXTERNAL_CONTENT)
    attempts = []
    inspect = ZipLease.inspect

    def inspect_and_compete(lease, **options):
        result = inspect(lease, **options)
        attempts.append(_external_replace(external, lease.path))
        return result

    monkeypatch.setattr(ZipLease, 'inspect', inspect_and_compete)
    delivery_export.export_delivered_projects(db, target)

    assert attempts == [False]
    assert is_zipfile(target)
    assert external.read_bytes() == EXTERNAL_CONTENT


@WINDOWS_HANDLES
def test_export_rejects_changed_staging_bytes_with_unchanged_identity(export_target):
    db, target = export_target

    @contextmanager
    def publication_lock():
        temporary, = target.parent.glob('.workbox-export-*.xlsx')
        before = delivery_export.target_identity(temporary)
        changed = _external_python(
            'import os, sys\n'
            'path = sys.argv[1]\n'
            'info = os.stat(path)\n'
            'with open(path, "r+b") as stream:\n'
            '    stream.seek(10)\n'
            '    old = stream.read(1)\n'
            '    stream.seek(10)\n'
            '    stream.write(bytes([old[0] ^ 1]))\n'
            'os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))\n',
            temporary,
        )
        assert changed.returncode == 0, changed.stderr
        assert delivery_export.target_identity(temporary) == before
        yield

    db._operation_lock = publication_lock()
    with pytest.raises(ValueError, match='临时文件已经变化'):
        delivery_export.export_delivered_projects(db, target)

    assert list(target.parent.iterdir()) == []


def _confirmed_overwrite(db, target, **options):
    return delivery_export.export_delivered_projects(
        db, target, replace_existing=True, expected_target_identity=delivery_export.target_identity(target),
        **options,
    )


def _after_retaining_original(monkeypatch, target, action):
    rename_to = ZipLease.rename_to

    def rename_and_run(lease, destination):
        moving_original = lease.path == target
        rename_to(lease, destination)
        if moving_original:
            action(destination)

    monkeypatch.setattr(ZipLease, 'rename_to', rename_and_run)


@WINDOWS_HANDLES
def test_competing_target_after_retaining_original_leaves_recovery_file(export_target, monkeypatch):
    db, target = export_target
    target.write_bytes(PREVIOUS_CONTENT)
    external = target.parent / 'external.xlsx'
    external.write_bytes(EXTERNAL_CONTENT)
    recovered = []

    def create_competing_target(backup):
        assert _external_replace(external, target)
        recovered.append(backup)

    _after_retaining_original(monkeypatch, target, create_competing_target)

    with pytest.raises(OSError, match='原文件.*保留') as error:
        _confirmed_overwrite(db, target)

    backup, = recovered
    assert str(backup) in str(error.value)
    assert backup.read_bytes() == PREVIOUS_CONTENT
    assert target.read_bytes() == EXTERNAL_CONTENT
    assert set(target.parent.iterdir()) == {backup, target}


@WINDOWS_HANDLES
def test_cancellation_after_retaining_original_restores_original(export_target, monkeypatch):
    db, target = export_target
    target.write_bytes(PREVIOUS_CONTENT)
    stop = threading.Event()
    _after_retaining_original(monkeypatch, target, lambda _: stop.set())

    with pytest.raises(CancelledError):
        _confirmed_overwrite(db, target, cancel_event=stop)

    assert target.read_bytes() == PREVIOUS_CONTENT
    assert list(target.parent.iterdir()) == [target]


@WINDOWS_HANDLES
@pytest.mark.parametrize('restore_fails', [False, True], ids=['restored', 'recovery-retained'])
def test_failed_publication_restores_original_or_reports_recovery_path(export_target, monkeypatch, restore_fails):
    db, target = export_target
    target.write_bytes(PREVIOUS_CONTENT)
    rename_to = ZipLease.rename_to
    retained = []

    def fail_publication(lease, destination):
        if lease.path == target:
            rename_to(lease, destination)
            retained.append(destination)
        elif destination == target and (lease.path not in retained or restore_fails):
            raise OSError('anonymous write restriction')
        else:
            rename_to(lease, destination)

    monkeypatch.setattr(ZipLease, 'rename_to', fail_publication)

    with pytest.raises(OSError) as error:
        _confirmed_overwrite(db, target)

    backup, = retained
    if restore_fails:
        assert str(backup) in str(error.value)
        assert backup.read_bytes() == PREVIOUS_CONTENT
        assert not target.exists()
        assert list(target.parent.iterdir()) == [backup]
    else:
        assert 'anonymous write restriction' in str(error.value)
        assert target.read_bytes() == PREVIOUS_CONTENT
        assert list(target.parent.iterdir()) == [target]


@WINDOWS_HANDLES
def test_successful_publish_reports_old_file_if_cleanup_is_denied(export_target, monkeypatch):
    db, target = export_target
    target.write_bytes(PREVIOUS_CONTENT)
    retained = []
    _after_retaining_original(monkeypatch, target, retained.append)
    remove = ZipLease.remove_after_verified_commit

    def refuse_cleanup(lease):
        if lease.path in retained:
            raise PermissionError('anonymous cleanup restriction')
        remove(lease)

    monkeypatch.setattr(ZipLease, 'remove_after_verified_commit', refuse_cleanup)
    result = _confirmed_overwrite(db, target)

    backup, = retained
    assert result['recovery_path'] == str(backup)
    assert backup.read_bytes() == PREVIOUS_CONTENT
    assert is_zipfile(target)
    assert set(target.parent.iterdir()) == {backup, target}
