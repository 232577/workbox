"""最终文件变更绑定真实 Windows 句柄，不接触业务目录。"""
import ctypes
import hashlib
import importlib
import os
from pathlib import Path
import struct

import pytest


def record(value):
    return dict(size=len(value), sha256=hashlib.sha256(value).hexdigest())


def module():
    return importlib.import_module('workbox.updates.update_file_ops')


def _reparse(path, target=None):
    """只用于匿名空目录的真实 FSCTL，不通过 shell 创建或删除链接。"""
    from ctypes import wintypes as w
    from workbox.storage.native_files import _api, _windows_path
    api = _api()
    api.DeviceIoControl.argtypes = [w.HANDLE, w.DWORD, ctypes.c_void_p, w.DWORD,
                                   ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.c_void_p]
    api.DeviceIoControl.restype = w.BOOL
    handle = api.CreateFileW(_windows_path(path), 0x40000000, 7, None, 3, 0x02200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if target is None:
            data = struct.pack('<LHH', 0xA0000003, 0, 0)
            code = 0x900AC
        else:
            substitute = ('\\??\\' + str(target)).encode('utf-16-le')
            display = str(target).encode('utf-16-le')
            names = substitute + b'\0\0' + display + b'\0\0'
            data = struct.pack('<LHHHHHH', 0xA0000003, len(names) + 8, 0,
                               0, len(substitute), len(substitute) + 2, len(display)) + names
            code = 0x900A4
        returned = w.DWORD()
        if not api.DeviceIoControl(handle, code, ctypes.create_string_buffer(data), len(data),
                                   None, 0, ctypes.byref(returned), None):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        api.CloseHandle(handle)


@pytest.fixture
def files(tmp_path):
    for name in ('incoming', 'installed', 'retained'):
        (tmp_path / name).mkdir()
    source = tmp_path / 'incoming/new.dll'
    destination = tmp_path / 'installed/app.dll'
    retired = tmp_path / 'retained/old.dll'
    source.write_bytes(b'new bytes')
    destination.write_bytes(b'old bytes')
    return source, destination, retired


@pytest.mark.skipif(os.name != 'nt', reason='Windows handle operations')
def test_swap_retains_old_identity_and_zone(files):
    source, destination, retired = files
    info = destination.stat()
    identity = info.st_ino
    Path(str(destination) + ':Zone.Identifier').write_bytes(b'old zone')
    returned_identity = module().swap_checked(source, destination, record(b'new bytes'), record(b'old bytes'), retired,
                          source_zone=None, destination_zone=b'old zone')
    assert returned_identity == (info.st_dev, identity)
    assert destination.read_bytes() == b'new bytes'
    assert retired.read_bytes() == b'old bytes'
    assert retired.stat().st_ino == identity
    assert Path(str(retired) + ':Zone.Identifier').read_bytes() == b'old zone'
    assert not source.exists()


@pytest.mark.skipif(os.name != 'nt', reason='Windows handle operations')
@pytest.mark.parametrize('bad', ['source', 'destination', 'source-zone', 'destination-zone', 'source-ads', 'destination-ads'])
def test_verification_refuses_before_any_move(files, bad):
    source, destination, retired = files
    zones = {}
    if bad in ('source', 'destination'):
        (source if bad == 'source' else destination).write_bytes(b'unknown external bytes')
    elif bad.endswith('-zone'):
        target = source if bad.startswith('source') else destination
        Path(str(target) + ':Zone.Identifier').write_bytes(b'external zone')
        zones[bad.replace('-', '_')] = None
    else:
        target = source if bad.startswith('source') else destination
        Path(str(target) + ':UnknownReview').write_bytes(b'unknown stream')
    before = source.read_bytes(), destination.read_bytes()
    with pytest.raises((ValueError, RuntimeError, OSError)):
        module().swap_checked(source, destination, record(b'new bytes'), record(b'old bytes'), retired, **zones)
    assert (source.read_bytes(), destination.read_bytes()) == before
    assert not retired.exists()


@pytest.mark.skipif(os.name != 'nt', reason='Windows handle operations')
def test_move_never_overwrites_and_new_target_can_be_created(files):
    source, destination, retired = files
    with pytest.raises((ValueError, OSError)):
        module().move_checked(source, destination, record(b'new bytes'))
    assert source.read_bytes() == b'new bytes' and destination.read_bytes() == b'old bytes'
    info = source.stat()
    returned_identity = module().move_checked(source, retired, record(b'new bytes'))
    assert returned_identity == (info.st_dev, info.st_ino)
    assert retired.read_bytes() == b'new bytes' and not source.exists()


@pytest.mark.skipif(os.name != 'nt', reason='Windows handle operations')
def test_final_rename_holds_writer_and_parent_guards(files, monkeypatch):
    from workbox.storage.native_files import _api, _windows_path
    source, destination, retired = files
    original = module()._MoveLease.rename_to
    attempts = []

    def final(lease, target):
        if lease.path == source:
            for path in (source, retired):
                with pytest.raises(OSError):
                    path.write_bytes(b'late external edit')
                for stream in ('Zone.Identifier', 'UnknownReview'):
                    with pytest.raises(OSError):
                        Path(str(path) + ':' + stream).write_bytes(b'late metadata edit')
            for parent in (source.parent, destination.parent, retired.parent):
                with pytest.raises(OSError):
                    parent.rename(parent.with_name(parent.name + '-switched'))
                # 共享写是发布子文件的必要条件，不能把它误报成防 reparse。
                api = _api()
                handle = api.CreateFileW(_windows_path(parent), 0x40000000, 7, None, 3, 0x02200000, None)
                assert handle != ctypes.c_void_p(-1).value
                api.CloseHandle(handle)
                # 禁止目录写句柄仍允许内部新建，证明不会阻断实际安装。
                extra = parent / 'anonymous-extra.txt'
                extra.write_bytes(b'allowed child creation')
                attempts.append(parent)
        return original(lease, target)

    monkeypatch.setattr(module()._MoveLease, 'rename_to', final)
    module().swap_checked(source, destination, record(b'new bytes'), record(b'old bytes'), retired)
    assert len(attempts) == 3 and destination.read_bytes() == b'new bytes'


@pytest.mark.skipif(os.name != 'nt', reason='Windows handle operations')
@pytest.mark.parametrize('occupied', [False, True])
def test_failed_swap_restores_only_empty_target_and_preserves_retired(files, monkeypatch, occupied):
    source, destination, retired = files
    original = module()._MoveLease.rename_to
    def fail(lease, target):
        if lease.path == source:
            if occupied:
                destination.write_bytes(b'external target occupant')
                return original(lease, target)  # 真正的 no-replace 内核失败。
            raise OSError('anonymous final rename failure')
        return original(lease, target)
    monkeypatch.setattr(module()._MoveLease, 'rename_to', fail)
    with pytest.raises(OSError):
        module().swap_checked(source, destination, record(b'new bytes'), record(b'old bytes'), retired)
    assert source.read_bytes() == b'new bytes'
    if occupied:
        assert destination.read_bytes() == b'external target occupant'
        assert retired.read_bytes() == b'old bytes'
    else:
        assert destination.read_bytes() == b'old bytes'
        assert not retired.exists()


@pytest.mark.skipif(os.name != 'nt', reason='Windows handle operations')
def test_new_target_swap_and_existing_retired_refusal(files):
    source, destination, retired = files
    module().swap_checked(source, retired, record(b'new bytes'), None, retired.with_name('unused'))
    assert retired.read_bytes() == b'new bytes'
    source.write_bytes(b'new bytes')
    with pytest.raises((OSError, ValueError)):
        module().swap_checked(source, destination, record(b'new bytes'), record(b'old bytes'), retired)
    assert destination.read_bytes() == b'old bytes'


@pytest.mark.skipif(os.name != 'nt', reason='Windows real reparse and relative-handle rename')
def test_last_instant_target_reparse_cannot_redirect_publication(files, monkeypatch, tmp_path):
    source, destination, retired = files
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / destination.name).write_bytes(b'outside sentinel')
    outside_before = {path.name: path.read_bytes() for path in outside.iterdir()}
    original = module()._native_rename
    changed = []
    def last_instant(lease, parent, leaf):
        if lease.path == source:
            # 旧文件已保留且目的父目录为空，在所有路径检查之后置 junction。
            _reparse(destination.parent, outside)
            changed.append(True)
        return original(lease, parent, leaf)
    monkeypatch.setattr(module(), '_native_rename', last_instant)
    try:
        with pytest.raises((OSError, ValueError, RuntimeError)):
            module().swap_checked(source, destination, record(b'new bytes'), record(b'old bytes'), retired)
        assert changed
        assert {path.name: path.read_bytes() for path in outside.iterdir()} == outside_before
        assert retired.read_bytes() == b'old bytes'
    finally:
        if changed:
            _reparse(destination.parent)
    # 原生目标目录句柄允许发布在原对象中；拒绝报告成功，同时完整保留两版字节。
    recovered_new = source if source.exists() else destination
    assert recovered_new.read_bytes() == b'new bytes'


@pytest.mark.skipif(os.name != 'nt', reason='Windows atomic no-replace kernel rename')
def test_last_instant_kernel_target_occupant_is_never_overwritten(files, monkeypatch):
    source, destination, retired = files
    original = module()._native_rename
    def occupy(lease, parent, leaf):
        if lease.path == source:
            destination.write_bytes(b'last instant external occupant')
        return original(lease, parent, leaf)
    monkeypatch.setattr(module(), '_native_rename', occupy)
    with pytest.raises(OSError):
        module().swap_checked(source, destination, record(b'new bytes'), record(b'old bytes'), retired)
    assert destination.read_bytes() == b'last instant external occupant'
    assert retired.read_bytes() == b'old bytes' and source.read_bytes() == b'new bytes'


@pytest.mark.skipif(os.name != 'nt', reason='Windows source lease and reparse protection')
def test_source_open_parent_cannot_be_emptied_or_reparsed(files, monkeypatch, tmp_path):
    source, destination, retired = files
    outside = tmp_path / 'outside'
    outside.mkdir()
    original = module()._verified
    attempted = []
    def after_source_open(lease, expected, zone):
        original(lease, expected, zone)
        if lease.path == source:
            with pytest.raises(OSError):
                source.unlink()
            with pytest.raises(OSError):
                _reparse(source.parent, outside)
            with pytest.raises(OSError):
                source.parent.rename(source.parent.with_name('switched-source'))
            attempted.append(True)
    monkeypatch.setattr(module(), '_verified', after_source_open)
    module().swap_checked(source, destination, record(b'new bytes'), record(b'old bytes'), retired)
    assert attempted and destination.read_bytes() == b'new bytes' and not list(outside.iterdir())


@pytest.mark.skipif(os.name != 'nt', reason='Windows single-file identity')
@pytest.mark.parametrize('which', ['source', 'destination'])
def test_hardlink_is_refused_before_swap(files, which):
    source, destination, retired = files
    target = source if which == 'source' else destination
    os.link(target, target.with_name('other-link'))
    with pytest.raises(ValueError):
        module().swap_checked(source, destination, record(b'new bytes'), record(b'old bytes'), retired)
    assert source.read_bytes() == b'new bytes' and destination.read_bytes() == b'old bytes'
    assert not retired.exists()


def test_unsupported_platform_refuses_before_filesystem(monkeypatch):
    api = module()
    monkeypatch.setattr(api, '_WINDOWS', False)
    with pytest.raises(OSError):
        api.move_checked(Path('absent'), Path('absent-target'), record(b'a'))
    with pytest.raises(OSError):
        api.swap_checked(Path('a'), Path('b'), record(b'a'), None, Path('c'))
    with pytest.raises(OSError):
        with api.create_new_file(Path('absent'), 'new.dll'):
            pytest.fail('unsupported platform accepted')


@pytest.mark.skipif(os.name != 'nt', reason='Windows exclusive staged file handles')
def test_create_new_file_unicode_flush_and_parent_reuse(tmp_path):
    root = tmp_path / 'stage'
    root.mkdir()
    for relative in ('内部/文件.dll', '内部/second.dll'):
        with module().create_new_file(root, relative) as stream:
            assert stream.writable()
            stream.write(b'new staged bytes')
            stream.flush()
            assert os.fstat(stream.fileno()).st_size == len(b'new staged bytes')
        assert stream.closed
        assert (root / relative).read_bytes() == b'new staged bytes'


@pytest.mark.skipif(os.name != 'nt', reason='Windows exclusive staged file handles')
def test_create_new_file_requires_expected_root_identity_before_creation(tmp_path):
    root = tmp_path / 'stage'
    root.mkdir()
    info = root.stat()
    identity = (info.st_dev, info.st_ino)
    with pytest.raises(ValueError, match='身份'):
        with module().create_new_file(root, 'nested/created.dll',
                                      expected_root_identity=(info.st_dev, info.st_ino + 1)):
            pytest.fail('different directory identity accepted')
    assert not list(root.iterdir())
    with module().create_new_file(root, 'nested/created.dll', expected_root_identity=identity) as stream:
        stream.write(b'expected directory bytes')
    assert (root / 'nested/created.dll').read_bytes() == b'expected directory bytes'


@pytest.mark.skipif(os.name != 'nt', reason='Windows exclusive staged file handles')
@pytest.mark.parametrize('relative', ['../outside.dll', 'a/../../outside.dll', '/outside.dll',
                                    'C:/outside.dll', 'a\\outside.dll', 'a//b.dll',
                                    'a/./b.dll', 'a:stream', '', 'a/'])
def test_create_new_file_rejects_noncanonical_paths_before_creation(tmp_path, relative):
    root = tmp_path / 'stage'
    root.mkdir()
    with pytest.raises(ValueError):
        with module().create_new_file(root, relative):
            pytest.fail('unsafe relative path accepted')
    assert not list(root.iterdir())


@pytest.mark.skipif(os.name != 'nt', reason='Windows exclusive staged file handles')
@pytest.mark.parametrize('occupied', ['file', 'directory', 'case-file'])
def test_create_new_file_never_overwrites_exact_collisions(tmp_path, occupied):
    root = tmp_path / 'stage'
    root.mkdir()
    target = root / ('CREATED.DLL' if occupied == 'case-file' else 'created.dll')
    if occupied == 'directory':
        target.mkdir()
    else:
        target.write_bytes(b'original occupant')
    with pytest.raises((OSError, ValueError)):
        with module().create_new_file(root, 'created.dll'):
            pytest.fail('collision accepted')
    assert target.is_dir() if occupied == 'directory' else target.read_bytes() == b'original occupant'


@pytest.mark.skipif(os.name != 'nt', reason='Windows real junction operations')
def test_create_new_file_rejects_directory_junction(tmp_path):
    root, outside = tmp_path / 'stage', tmp_path / 'outside'
    root.mkdir()
    outside.mkdir()
    link = root / 'linked'
    link.mkdir()
    _reparse(link, outside)
    try:
        with pytest.raises((OSError, ValueError)):
            with module().create_new_file(root, 'linked/created.dll'):
                pytest.fail('junction accepted')
        assert not list(outside.iterdir())
    finally:
        _reparse(link)


@pytest.mark.skipif(os.name != 'nt', reason='Windows directory symlink operations')
def test_create_new_file_rejects_directory_symlink(tmp_path):
    root, outside = tmp_path / 'stage', tmp_path / 'outside'
    root.mkdir()
    outside.mkdir()
    link = root / 'linked'
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as error:
        if error.winerror == 1314:
            pytest.skip('Windows symlink privilege unavailable')
        raise
    with pytest.raises((OSError, ValueError)):
        with module().create_new_file(root, 'linked/created.dll'):
            pytest.fail('directory symlink accepted')
    assert not list(outside.iterdir())


@pytest.mark.skipif(os.name != 'nt', reason='Windows exclusive staged file handles')
def test_create_new_file_requires_existing_directory_and_preserves_parent_collision(tmp_path):
    root = tmp_path / 'absent-stage'
    with pytest.raises((OSError, ValueError)):
        with module().create_new_file(root, 'new.dll'):
            pytest.fail('missing root accepted')
    assert not root.exists()
    root.mkdir()
    (root / 'occupied').write_bytes(b'parent file')
    with pytest.raises((OSError, ValueError)):
        with module().create_new_file(root, 'occupied/new.dll'):
            pytest.fail('file used as parent')
    assert (root / 'occupied').read_bytes() == b'parent file'


@pytest.mark.skipif(os.name != 'nt', reason='Windows handle ownership and cleanup')
def test_create_new_file_writer_failure_closes_handles_without_deleting_bytes(tmp_path):
    root = tmp_path / 'stage'
    root.mkdir()
    with pytest.raises(RuntimeError, match='writer stopped'):
        with module().create_new_file(root, 'nested/partial.dll') as stream:
            for path in (root, root / 'nested'):
                with pytest.raises(OSError):
                    path.rename(path.with_name(path.name + '-switched'))
            stream.write(b'partial bytes retained')
            raise RuntimeError('writer stopped')
    assert stream.closed
    retained = tmp_path / 'retained-stage'
    root.rename(retained)
    target = retained / 'nested/partial.dll'
    assert target.read_bytes() == b'partial bytes retained'
    target.rename(target.with_name('retained.dll'))


@pytest.mark.skipif(os.name != 'nt', reason='Windows handle-bound duplicate cleanup')
def test_remove_duplicate_preserves_verified_keeper_and_origin(tmp_path):
    duplicate, keeper = tmp_path / 'retired.dll', tmp_path / 'backup.dll'
    expected = record(b'old anonymous program')
    zone = b'[ZoneTransfer]\nZoneId=3\n'
    for path in (duplicate, keeper):
        path.write_bytes(b'old anonymous program')
        Path(str(path) + ':Zone.Identifier').write_bytes(zone)
    copy_info, keeper_info = duplicate.stat(), keeper.stat()
    module().remove_duplicate_checked(duplicate, keeper, expected,
        duplicate_identity=(copy_info.st_dev, copy_info.st_ino),
        keeper_identity=(keeper_info.st_dev, keeper_info.st_ino), expected_zone=zone)
    assert not duplicate.exists()
    assert keeper.read_bytes() == b'old anonymous program'
    assert keeper.stat().st_ino == keeper_info.st_ino
    assert Path(str(keeper) + ':Zone.Identifier').read_bytes() == zone


@pytest.mark.skipif(os.name != 'nt', reason='Windows handle-bound duplicate cleanup')
@pytest.mark.parametrize('mismatch', ['missing-copy', 'missing-keeper', 'copy-bytes',
    'keeper-bytes', 'copy-identity', 'keeper-identity', 'copy-zone', 'keeper-zone'])
def test_remove_duplicate_refuses_unverified_copy_or_keeper(tmp_path, mismatch):
    duplicate, keeper = tmp_path / 'retired.dll', tmp_path / 'backup.dll'
    identities = {}
    for label, path in (('copy', duplicate), ('keeper', keeper)):
        if mismatch == 'missing-' + label:
            identities[label] = (0, 0)
            continue
        path.write_bytes(b'unknown bytes' if mismatch == label + '-bytes' else b'old program')
        info = path.stat()
        identities[label] = (info.st_dev, info.st_ino + (mismatch == label + '-identity'))
        if mismatch == label + '-zone':
            Path(str(path) + ':Zone.Identifier').write_bytes(b'[ZoneTransfer]\nZoneId=4\n')
    before = {path: path.read_bytes() for path in (duplicate, keeper) if path.exists()}
    with pytest.raises((OSError, ValueError)):
        module().remove_duplicate_checked(duplicate, keeper, record(b'old program'),
            duplicate_identity=identities['copy'], keeper_identity=identities['keeper'], expected_zone=None)
    assert {path: path.read_bytes() for path in (duplicate, keeper) if path.exists()} == before


@pytest.mark.skipif(os.name != 'nt', reason='Windows handle-bound duplicate cleanup')
def test_remove_duplicate_refuses_the_keeper_itself(tmp_path):
    keeper = tmp_path / 'backup.dll'
    keeper.write_bytes(b'old program')
    info = keeper.stat()
    with pytest.raises(ValueError):
        module().remove_duplicate_checked(keeper, keeper, record(b'old program'),
            duplicate_identity=(info.st_dev, info.st_ino),
            keeper_identity=(info.st_dev, info.st_ino), expected_zone=None)
    assert keeper.read_bytes() == b'old program'
