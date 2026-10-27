import ctypes
import hashlib
import os
from ctypes import wintypes as w
from pathlib import Path

import pytest

import native_files
from workspace_manager import copy_verified


pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows 文件流 API')


class ApiCall:
    """替代外部 WinAPI 调用，同时允许生产代码声明 ctypes 签名。"""

    def __init__(self, implementation):
        self.implementation = implementation

    def __call__(self, *args):
        return self.implementation(*args)


def simulate_stream_volume(monkeypatch, source, *, flags=0x106, stream_error=87,
                           query_failure=None, volume_root=None):
    """只替代夹具原件的流枚举与卷查询；句柄、复制和副本校验仍走真实 API。"""
    real_api = native_files._api()
    real_api.FindFirstStreamW.argtypes = [w.LPCWSTR, ctypes.c_int, ctypes.c_void_p, w.DWORD]
    real_api.FindFirstStreamW.restype = w.HANDLE
    source_name = '\\\\?\\' + str(source)
    root = volume_root or source.anchor

    class Kernel:
        def __getattr__(self, name):
            return getattr(real_api, name)

    api = Kernel()

    def first_stream(path, level, data, options):
        if path == source_name:
            ctypes.set_last_error(stream_error)
            return ctypes.c_void_p(-1).value
        return real_api.FindFirstStreamW(path, level, data, options)

    def volume_path(path, output, length):
        assert path in (str(source), source_name)
        if query_failure == 'path':
            ctypes.set_last_error(5)
            return 0
        assert length > len(root)
        output.value = root
        return 1

    def volume_information(path, name, name_length, serial, component_length,
                           filesystem_flags, filesystem_name, filesystem_length):
        # 目录挂载卷必须查询 API 返回的卷根，不能用原路径的盘符根代替。
        assert path == root
        if query_failure == 'information':
            ctypes.set_last_error(5)
            return 0
        ctypes.cast(filesystem_flags, ctypes.POINTER(w.DWORD)).contents.value = flags
        return 1

    api.FindFirstStreamW = ApiCall(first_stream)
    api.GetVolumePathNameW = ApiCall(volume_path)
    api.GetVolumeInformationW = ApiCall(volume_information)
    monkeypatch.setattr(native_files, '_api', lambda: api)


def incoming(tmp_path):
    source = tmp_path / 'incoming.docx'
    source.write_bytes(b'isolated ordinary document\r\n' * 50)
    return source, tmp_path / 'working' / source.name


@pytest.mark.parametrize('mounted', [False, True], ids=['drive-root', 'directory-mount'])
def test_confirmed_no_stream_volume_copies_after_error_87_and_preserves_source(
        tmp_path, monkeypatch, mounted):
    source, target = incoming(tmp_path)
    content, before = source.read_bytes(), source.stat()
    root = str(tmp_path / 'mounted-volume') + '\\' if mounted else None
    simulate_stream_volume(monkeypatch, source, volume_root=root)

    result = copy_verified(source, target)

    assert source.read_bytes() == target.read_bytes() == content
    assert (source.stat().st_ino, source.stat().st_size, source.stat().st_mtime_ns) == (
        before.st_ino, before.st_size, before.st_mtime_ns)
    assert result['sha256'] == hashlib.sha256(content).hexdigest()
    assert result['size'] == len(content)
    assert not list(target.parent.glob('*.partial'))


def test_stream_capable_volume_does_not_hide_error_87(tmp_path, monkeypatch):
    source, target = incoming(tmp_path)
    simulate_stream_volume(monkeypatch, source, flags=0x40106)

    with pytest.raises(OSError) as error:
        copy_verified(source, target)

    assert error.value.winerror == 87
    assert source.exists() and not target.exists()
    assert list(target.parent.iterdir()) == []


@pytest.mark.parametrize('query_failure', ['path', 'information'])
def test_failed_volume_query_preserves_original_stream_error(
        tmp_path, monkeypatch, query_failure):
    source, target = incoming(tmp_path)
    simulate_stream_volume(monkeypatch, source, query_failure=query_failure)

    with pytest.raises(OSError) as error:
        copy_verified(source, target)

    assert error.value.winerror == 87
    assert source.exists() and not target.exists()
    assert list(target.parent.iterdir()) == []


def test_permission_failure_is_not_treated_as_absent_streams(tmp_path, monkeypatch):
    source, target = incoming(tmp_path)
    simulate_stream_volume(monkeypatch, source, stream_error=5)

    with pytest.raises(OSError) as error:
        copy_verified(source, target)

    assert error.value.winerror == 5
    assert source.exists() and not target.exists()
    assert list(target.parent.iterdir()) == []


def test_stream_capable_copy_preserves_zone_identifier_and_source(tmp_path):
    source, target = incoming(tmp_path)
    content = source.read_bytes()
    zone = b'[ZoneTransfer]\r\nZoneId=3\r\nHostUrl=https://example.invalid/file\r\n'
    source_zone = Path(str(source) + ':Zone.Identifier')
    source_zone.write_bytes(zone)

    copy_verified(source, target)

    assert source.read_bytes() == target.read_bytes() == content
    assert source_zone.read_bytes() == zone
    assert Path(str(target) + ':Zone.Identifier').read_bytes() == zone


def test_unknown_stream_is_rejected_without_losing_original(tmp_path):
    source, target = incoming(tmp_path)
    content = source.read_bytes()
    extra = Path(str(source) + ':PrivateMetadata')
    extra.write_bytes(b'preserve this metadata')

    with pytest.raises(ValueError, match='额外命名数据流'):
        copy_verified(source, target)

    assert source.read_bytes() == content
    assert extra.read_bytes() == b'preserve this metadata'
    assert not target.exists() and list(target.parent.iterdir()) == []
