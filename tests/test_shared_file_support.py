import ctypes
import hashlib
import os
from pathlib import Path
from zipfile import ZipFile

import pytest

import native_files
from workspace_manager import _path, copy_verified


pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows 共享文件句柄')


class ApiCall:
    def __init__(self, implementation):
        self.implementation = implementation

    def __call__(self, *args):
        return self.implementation(*args)


def shared_fixture(tmp_path, monkeypatch):
    """只将匿名共享地址映射到隔离夹具，真实句柄、哈希和流校验照常运行。"""
    local = tmp_path / 'share'
    local.mkdir()
    share = Path(r'\\fixture-host\documents')
    real_stat = Path.stat
    real_api = native_files._api()
    calls = []

    def mapped(path):
        value = str(path)
        if value.startswith('\\\\?\\UNC\\fixture-host\\documents'):
            value = '\\\\fixture-host\\documents' + value[len('\\\\?\\UNC\\fixture-host\\documents'):]
        candidate = Path(value)
        if candidate == share or candidate.is_relative_to(share):
            return local / candidate.relative_to(share)
        return candidate

    def shared_stat(path, *args, **kwargs):
        return real_stat(mapped(path), *args, **kwargs)

    class Kernel:
        def __getattr__(self, name):
            return getattr(real_api, name)

    api = Kernel()
    for name in ('CreateFileW', 'FindFirstStreamW'):
        original = getattr(real_api, name)
        # 先声明真实API签名，包装调用仍使用真实Windows句柄。
        if name == 'FindFirstStreamW':
            from ctypes import wintypes as w
            original.argtypes = [w.LPCWSTR, ctypes.c_int, ctypes.c_void_p, w.DWORD]
            original.restype = w.HANDLE

        def invoke(path, *args, original=original, name=name):
            calls.append((name, path, args))
            if str(path).startswith('\\\\?\\UNC\\fixture-host\\documents'):
                path = '\\\\?\\' + str(mapped(path))
            return original(path, *args)

        setattr(api, name, ApiCall(invoke))
    monkeypatch.setattr(Path, 'stat', shared_stat)
    monkeypatch.setattr(native_files, '_api', lambda: api)
    return local, share, calls


def test_unc_source_copy_retains_original_hash_zone_and_read_only_lease(tmp_path, monkeypatch):
    local, share, calls = shared_fixture(tmp_path, monkeypatch)
    source = local / '材料.docx'
    content = b'isolated shared document\r\n' * 100
    source.write_bytes(content)
    zone = b'[ZoneTransfer]\r\nZoneId=3\r\n'
    Path(str(source) + ':Zone.Identifier').write_bytes(zone)
    before = source.stat()
    target = tmp_path / 'working' / source.name

    result = copy_verified(share / source.name, target)

    assert source.read_bytes() == target.read_bytes() == content
    assert native_files.identity(source.stat()) == native_files.identity(before)
    assert result['source_path'] == str(share / source.name)
    assert result['sha256'] == hashlib.sha256(content).hexdigest()
    assert Path(str(target) + ':Zone.Identifier').read_bytes() == zone
    source_opens = [args for name, path, args in calls
                    if name == 'CreateFileW' and path == '\\\\?\\UNC\\fixture-host\\documents\\材料.docx']
    assert source_opens and source_opens[0][0] == 0x80000000
    assert source_opens[0][1] == 1  # 不允许其他写入或删除句柄。


def test_unc_extra_stream_still_rejected_without_publishing_copy(tmp_path, monkeypatch):
    local, share, _ = shared_fixture(tmp_path, monkeypatch)
    source = local / '材料.docx'
    source.write_bytes(b'preserve original')
    extra = Path(str(source) + ':PrivateMetadata')
    extra.write_bytes(b'keep metadata')
    target = tmp_path / 'working' / source.name

    with pytest.raises(ValueError, match='额外命名数据流'):
        copy_verified(share / source.name, target)

    assert source.read_bytes() == b'preserve original'
    assert extra.read_bytes() == b'keep metadata'
    assert not target.exists() and list(target.parent.iterdir()) == []


@pytest.mark.parametrize('value', [r'\\.\PhysicalDrive0', r'\\?\GLOBALROOT\Device\Harddisk0',
                                  r'\\server\share\file.txt:private', r'C:\file.txt:private'])
def test_device_and_stream_sources_remain_rejected(value):
    with pytest.raises(ValueError):
        _path(value, allow_network=True)


def test_shared_destinations_and_destructive_leases_remain_rejected():
    source = r'\\fixture-host\documents\材料.docx'
    with pytest.raises(ValueError):
        _path(source)
    with pytest.raises(ValueError):
        native_files.ZipLease(source, movable=True)


def test_mapped_share_allowed_only_as_source(tmp_path, monkeypatch):
    source = tmp_path / 'mapped-source.txt'
    source.write_bytes(b'mapped share fixture')
    real_dll = ctypes.WinDLL

    def dll(name, *args, **kwargs):
        api = real_dll(name, *args, **kwargs)
        api.GetDriveTypeW = ApiCall(lambda root: 4)
        return api

    monkeypatch.setattr(ctypes, 'WinDLL', dll)
    assert _path(source, allow_network=True) == source
    with pytest.raises(ValueError, match='映射'):
        _path(source)


def test_identity_boundary_detects_unc_aliases_and_nested_paths(tmp_path, monkeypatch):
    local, share, _ = shared_fixture(tmp_path, monkeypatch)
    protected = local / 'protected'
    protected.mkdir()
    nested = protected / 'documents'
    nested.mkdir()
    source = nested / 'source.txt'
    source.write_bytes(b'isolated protected content')
    assert native_files.path_is_within(share / 'protected', protected)
    assert native_files.path_is_within(share / 'protected' / 'documents' / source.name, protected)
    assert native_files.path_is_within(share / 'protected' / 'not-yet-created', protected)
    assert not native_files.path_is_within(share / 'other' / 'source.txt', protected)
    assert not native_files.path_is_within(share / 'protected', local / 'not-created')


def test_identity_boundary_does_not_hide_permission_failures(tmp_path, monkeypatch):
    protected = tmp_path / 'protected'
    protected.mkdir()
    real_stat = Path.stat
    forbidden = tmp_path / 'forbidden'

    def blocked_stat(path, *args, **kwargs):
        if path == forbidden:
            raise PermissionError('isolated boundary check denied')
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'stat', blocked_stat)
    with pytest.raises(PermissionError, match='boundary check denied'):
        native_files.path_is_within(forbidden / 'source.txt', protected)


def test_identity_boundary_refuses_unknown_file_identity(tmp_path, monkeypatch):
    from types import SimpleNamespace
    protected = tmp_path / 'protected'
    protected.mkdir()
    real_stat = Path.stat
    unknown = tmp_path / 'unknown'

    def unknown_stat(path, *args, **kwargs):
        if path == unknown:
            return SimpleNamespace(st_dev=123, st_ino=0)
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'stat', unknown_stat)
    with pytest.raises(OSError, match='标识'):
        native_files.path_is_within(unknown, protected)


@pytest.mark.parametrize('supports_streams', [False, True, None])
def test_share_stream_api_not_supported_requires_confirmed_volume_capability(
        tmp_path, monkeypatch, supports_streams):
    local, share, _ = shared_fixture(tmp_path, monkeypatch)
    source = local / 'source.txt'
    source.write_bytes(b'shared volume content')
    target = tmp_path / 'working' / source.name
    api = native_files._api()
    original = api.FindFirstStreamW

    def first_stream(path, *args):
        if str(path).startswith('\\\\?\\UNC\\fixture-host\\documents'):
            ctypes.set_last_error(50)  # SMB服务器明确返回“不支持此请求”。
            return ctypes.c_void_p(-1).value
        return original(path, *args)

    api.FindFirstStreamW = ApiCall(first_stream)
    monkeypatch.setattr(native_files, '_volume_supports_named_streams',
                        lambda path, kernel: supports_streams)

    if supports_streams is False:
        result = copy_verified(share / source.name, target)
        assert result['sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()
        assert source.read_bytes() == target.read_bytes()
    else:
        with pytest.raises(OSError) as error:
            copy_verified(share / source.name, target)
        assert error.value.winerror == 50
        assert not target.exists() and list(target.parent.iterdir()) == []


@pytest.mark.skipif(os.environ.get('WORKBOX_TEST_LOCAL_UNC') != '1',
                    reason='真实本机UNC验证需显式启用，并允许访问localhost管理共享')
@pytest.mark.parametrize('extension', ['.docx', '.zip'])
def test_real_unc_copy_uses_only_isolated_files_and_preserves_metadata(tmp_path, extension):
    # 该用例仅经本机D$共享访问本次临时夹具，不读写任何业务文件。
    if tmp_path.drive.casefold() != 'd:':
        pytest.skip('本机隔离UNC夹具要求D盘临时目录')
    source = tmp_path / ('isolated-shared-source' + extension)
    if extension == '.zip':
        with ZipFile(source, 'w') as archive:
            archive.writestr('isolated.txt', b'isolated archive content')
    else:
        source.write_bytes(b'isolated ordinary file\r\n' * 1000)
    content = source.read_bytes()
    zone = b'[ZoneTransfer]\r\nZoneId=3\r\n'
    Path(str(source) + ':Zone.Identifier').write_bytes(zone)
    before = native_files.identity(source.stat())
    shared_source = Path(r'\\localhost\D$') / source.relative_to('D:\\')
    target = tmp_path / 'working' / source.name

    assert native_files.path_is_within(shared_source, tmp_path)
    assert native_files.path_is_within(tmp_path, shared_source.parent)
    assert not native_files.path_is_within(shared_source, target.parent)

    result = copy_verified(shared_source, target, require_zip=extension == '.zip')

    assert result['source_path'] == str(shared_source)
    assert result['sha256'] == hashlib.sha256(content).hexdigest()
    assert source.read_bytes() == target.read_bytes() == content
    assert native_files.identity(source.stat()) == before
    assert Path(str(source) + ':Zone.Identifier').read_bytes() == zone
    assert Path(str(target) + ':Zone.Identifier').read_bytes() == zone
    assert not list(target.parent.glob('*.partial'))
    with native_files.ZipLease(shared_source, movable=False) as lease:
        for access in (0x40000000, 0x10000):  # WRITE、DELETE请求均不得与读取租约并存。
            handle = lease.api.CreateFileW(native_files._windows_path(shared_source),
                                          access, 7, None, 3, 0x08000000, None)
            if handle != ctypes.c_void_p(-1).value:
                lease.api.CloseHandle(handle)
                pytest.fail('共享原件读取期间未能阻止写入或删除句柄')
            assert ctypes.get_last_error() == 32
