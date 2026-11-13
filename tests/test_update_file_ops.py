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
    return importlib.import_module('update_file_ops')


def _reparse(path, target=None):
    """只用于匿名空目录的真实 FSCTL，不通过 shell 创建或删除链接。"""
    from ctypes import wintypes as w
    from native_files import _api, _windows_path
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
    identity = destination.stat().st_ino
    Path(str(destination) + ':Zone.Identifier').write_bytes(b'old zone')
    module().swap_checked(source, destination, record(b'new bytes'), record(b'old bytes'), retired,
                          source_zone=None, destination_zone=b'old zone')
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
    module().move_checked(source, retired, record(b'new bytes'))
    assert retired.read_bytes() == b'new bytes' and not source.exists()


@pytest.mark.skipif(os.name != 'nt', reason='Windows handle operations')
def test_final_rename_holds_writer_and_parent_guards(files, monkeypatch):
    from native_files import _api, _windows_path
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
