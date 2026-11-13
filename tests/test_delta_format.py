"""受限程序增量格式；全部字节来自匿名隔离 fixture。"""
import hashlib
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
from types import SimpleNamespace
import zipfile
import zlib

import pytest

from delta_format import (build_key, delta_filename, validate_path, validate_manifest,
                          changed_paths, removed_paths, read_manifest, stage_package)


REQUIRED = ('WorkBox.exe', 'WorkBoxUpdater.exe', 'PACKAGE_CONTENTS.sha256',
            '_internal/python311.dll', '_internal/PySide6/Qt6Core.dll',
            '_internal/PySide6/plugins/platforms/qwindows.dll')
FROM = 'v1.0.0.20261113.2'
TO = 'v1.0.0.20261113.3'


def fixture():
    before = {p: ('anonymous ' + p).encode() for p in REQUIRED}
    before['_internal/remove.dll'] = b'removed'
    after = dict(before)
    del after['_internal/remove.dll']
    after['WorkBox.exe'] = b'new anonymous program'
    after['_internal/new.dll'] = b'new dependency'
    def mapping(values):
        return {p: {'sha256': hashlib.sha256(b).hexdigest(), 'size': len(b)} for p, b in values.items()}
    manifest = dict(format='workbox-file-delta-v1', from_tag=FROM, to_tag=TO,
                    base=mapping(before), target=mapping(after))
    return manifest, after


def package(tmp_path, *, transform=None, manifest_text=None, extra=None, compression=zipfile.ZIP_DEFLATED):
    manifest, after = fixture()
    if transform:
        transform(manifest)
    path = tmp_path / 'anonymous.wbdelta'
    with zipfile.ZipFile(path, 'w', compression=compression) as archive:
        archive.writestr('delta.json', manifest_text if manifest_text is not None else json.dumps(manifest).encode())
        for name in changed_paths(manifest):
            archive.writestr('payload/' + name, after[name])
        if extra:
            archive.writestr(*extra)
    return path, manifest


def kwargs(path):
    return dict(expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), expected_size=path.stat().st_size,
                from_tag=FROM, to_tag=TO)


def test_build_keys_and_filename():
    assert build_key('v1.0.0.20261113') == (20261113, 0)
    assert build_key(FROM) == (20261113, 2)
    assert delta_filename(FROM, TO) == 'WorkBox-1.0.0-Delta-x64-20261113-r2-to-20261113-r3.wbdelta'
    for tag in ['v1.0.0.20260230', 'v1.0.0.20261113.0', 'v1.0.0.20261113.0001', None, 'v1.0.0.２０２６１１１３']:
        with pytest.raises(ValueError):
            build_key(tag)
    with pytest.raises(ValueError):
        delta_filename(TO, FROM)


@pytest.mark.parametrize('value', ['../WorkBox.exe', '/WorkBox.exe', 'C:/WorkBox.exe',
    '_internal\\x.dll', '_internal//x.dll', '_internal/./x.dll', '_internal/x.dll:ads',
    '_internal/NUL.dll', '_internal/COM1.dll', '_internal/com¹.txt', '_internal/x. /a.dll',
    '_internal/e\u0301.dll', '_internal/a\x00.dll', '_internal/工作项目/x.dll',
    '_internal/data/x.dll', '_internal/LOGS/x.dll', '_internal/sensitive.pdf',
    'not-allowed.exe', 'licenses/license.json', 'data.db', '_internal/.. /x.dll'])
def test_unsafe_paths(value):
    with pytest.raises(ValueError):
        validate_path(value)


def test_manifest_complete_independent_and_differences():
    original, _ = fixture()
    result = validate_manifest(original)
    assert result == original and result is not original and result['base'] is not original['base']
    assert changed_paths(result) == ['WorkBox.exe', '_internal/new.dll']
    assert removed_paths(result) == ['_internal/remove.dll']
    result['target']['WorkBox.exe']['size'] += 1
    assert original['target']['WorkBox.exe']['size'] != result['target']['WorkBox.exe']['size']
    original['target']['WorkBoxUpdater.exe']['size'] += 1
    assert 'WorkBoxUpdater.exe' in changed_paths(original)


@pytest.mark.parametrize('mutation', ['missing', 'extra', 'bool-size', 'negative', 'hash', 'collision', 'prefix', 'cross-case', 'too-large'])
def test_invalid_manifest(mutation):
    value, _ = fixture()
    row = value['target']['WorkBox.exe']
    if mutation == 'missing': del value['base']['WorkBoxUpdater.exe']
    elif mutation == 'extra': row['unknown'] = 1
    elif mutation == 'bool-size': row['size'] = True
    elif mutation == 'negative': row['size'] = -1
    elif mutation == 'hash': row['sha256'] = 'A' * 64
    elif mutation == 'collision': value['target']['_internal/PYTHON311.dll'] = dict(row)
    elif mutation == 'prefix': value['target']['_internal/python311.dll/child.dll'] = dict(row)
    elif mutation == 'cross-case': value['target']['_internal/REMOVE.dll'] = dict(value['base']['_internal/remove.dll'])
    elif mutation == 'too-large': row['size'] = 512 * 1024 * 1024 + 1
    with pytest.raises(ValueError):
        validate_manifest(value)


@pytest.mark.parametrize('compression', [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED])
def test_verified_read_and_stage(tmp_path, compression):
    path, manifest = package(tmp_path, compression=compression)
    assert read_manifest(path, **kwargs(path)) == manifest
    destination = tmp_path / 'stage'
    assert stage_package(path, destination, **kwargs(path)) == manifest
    _, expected = fixture()
    assert {p.relative_to(destination).as_posix() for p in destination.rglob('*') if p.is_file()} == set(changed_paths(manifest))
    for name in changed_paths(manifest):
        assert (destination / name).read_bytes() == expected[name]
    with pytest.raises(FileExistsError):
        stage_package(path, destination, **kwargs(path))


@pytest.mark.parametrize('failure', ['outer-hash', 'outer-size', 'tag', 'duplicate-json', 'extra', 'duplicate-zip', 'missing', 'content', 'symlink', 'directory', 'compression'])
def test_package_rejection_before_staging(tmp_path, failure):
    path, manifest = package(tmp_path)
    options = kwargs(path)
    if failure == 'outer-hash': options['expected_sha256'] = '0' * 64
    elif failure == 'outer-size': options['expected_size'] += 1
    elif failure == 'tag': options['from_tag'] = 'v1.0.0.20261113.1'
    else:
        _, after = fixture()
        with zipfile.ZipFile(path, 'w') as archive:
            text = json.dumps(manifest)
            if failure == 'duplicate-json': text = text.replace('"format":', '"format":"ignored","format":', 1)
            archive.writestr('delta.json', text)
            for name in changed_paths(manifest):
                if failure == 'missing' and name == 'WorkBox.exe': continue
                entry = zipfile.ZipInfo('payload/' + name)
                if failure == 'symlink' and name == 'WorkBox.exe':
                    entry.create_system = 3; entry.external_attr = (stat.S_IFLNK | 0o777) << 16
                if failure == 'compression': entry.compress_type = zipfile.ZIP_BZIP2
                archive.writestr(entry, b'corrupt' if failure == 'content' else after[name])
            if failure == 'extra': archive.writestr('payload/_internal/extra.dll', b'x')
            if failure == 'directory': archive.writestr('payload/', b'')
            if failure == 'duplicate-zip': archive.writestr('delta.json', text)
        options = kwargs(path)
    destination = tmp_path / 'must-not-exist'
    with pytest.raises(ValueError):
        stage_package(path, destination, **options)
    assert not destination.exists()


def test_declared_budget_checked_before_read(monkeypatch, tmp_path):
    import delta_format
    path, _ = package(tmp_path)
    monkeypatch.setattr(delta_format, 'MAX_MANIFEST_SIZE', 8)
    with pytest.raises(ValueError):
        read_manifest(path, **kwargs(path))


@pytest.mark.parametrize('limit', ['MAX_FILES', 'MAX_FILE_SIZE', 'MAX_TOTAL_SIZE', 'MAX_PACKAGE_SIZE'])
def test_each_resource_budget(monkeypatch, tmp_path, limit):
    import delta_format
    path, _ = package(tmp_path)
    monkeypatch.setattr(delta_format, limit, 1)
    with pytest.raises(ValueError):
        read_manifest(path, **kwargs(path))


def test_encrypted_member_rejected(tmp_path):
    path, _ = package(tmp_path)
    data = bytearray(path.read_bytes())
    local = data.index(b'PK\x03\x04')
    central = data.index(b'PK\x01\x02')
    for offset in (local + 6, central + 8):
        flags, = struct.unpack_from('<H', data, offset)
        struct.pack_into('<H', data, offset, flags | 1)
    path.write_bytes(data)
    with pytest.raises(ValueError):
        read_manifest(path, **kwargs(path))


def test_directory_count_budget_is_checked_before_zip_parser(tmp_path, monkeypatch):
    path, _ = package(tmp_path)
    data = bytearray(path.read_bytes())
    end = data.rindex(b'PK\x05\x06')
    struct.pack_into('<HH', data, end + 8, 10002, 10002)
    path.write_bytes(data)
    import delta_format
    def forbidden(*args, **options):
        raise AssertionError('超限目录不得交给 ZIP parser 先分配内存')
    monkeypatch.setattr(delta_format.zipfile, 'ZipFile', forbidden)
    with pytest.raises(ValueError):
        read_manifest(path, **kwargs(path))


def test_real_deflate_length_cannot_be_hidden_by_false_central_size(tmp_path):
    # 标准 ZipExtFile 会按 file_size 截断输出；前缀 CRC/SHA 也正确时仍须拒绝尾部。
    manifest, after = fixture()
    name = 'WorkBox.exe'
    prefix = after[name]
    with zipfile.ZipFile(tmp_path / 'attack.wbdelta', 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('delta.json', json.dumps(manifest))
        for changed in changed_paths(manifest):
            archive.writestr('payload/' + changed, after[changed] + (b'extra' * 100 if changed == name else b''))
    path = tmp_path / 'attack.wbdelta'
    with zipfile.ZipFile(path) as archive:
        info = archive.getinfo('payload/' + name)
    data = bytearray(path.read_bytes())
    central = data.index(b'payload/' + name.encode(), data.index(b'PK\x01\x02')) - 46
    struct.pack_into('<I', data, info.header_offset + 14, zlib.crc32(prefix))
    struct.pack_into('<I', data, info.header_offset + 22, len(prefix))
    struct.pack_into('<I', data, central + 16, zlib.crc32(prefix))
    struct.pack_into('<I', data, central + 24, len(prefix))
    path.write_bytes(data)
    with zipfile.ZipFile(path) as archive:
        assert archive.read('payload/' + name) == prefix
    with pytest.raises(ValueError):
        read_manifest(path, **kwargs(path))


def test_windows_package_handle_denies_concurrent_writers(tmp_path):
    if os.name != 'nt':
        pytest.skip('Windows 文件共享租约仅在 Windows 验证')
    import delta_format
    path, _ = package(tmp_path)
    with delta_format._open_package(path):
        with pytest.raises(OSError):
            with path.open('ab'):
                pass


def test_staged_disk_bytes_are_rechecked_before_success(monkeypatch, tmp_path):
    import delta_format
    path, _ = package(tmp_path)
    original = delta_format._read_member
    def corrupt(*args, **options):
        result = original(*args, **options)
        if options.get('destination') is not None:
            options['destination'].seek(0)
            options['destination'].write(b'X')
        return result
    monkeypatch.setattr(delta_format, '_read_member', corrupt)
    with pytest.raises(ValueError):
        stage_package(path, tmp_path / 'stage', **kwargs(path))


def test_stage_flush_failure_cannot_report_files_ready(monkeypatch, tmp_path):
    import delta_format
    path, _ = package(tmp_path)
    def fail_flush(fd):
        raise OSError('anonymous disk flush failure')
    monkeypatch.setattr(delta_format.os, 'fsync', fail_flush)
    with pytest.raises(OSError, match='flush'):
        stage_package(path, tmp_path / 'stage', **kwargs(path))


def test_stage_rejects_link_parent(tmp_path):
    path, _ = package(tmp_path)
    actual = tmp_path / 'actual'; actual.mkdir()
    link = tmp_path / 'link'
    try:
        link.symlink_to(actual, target_is_directory=True)
    except OSError:
        if os.name != 'nt':
            pytest.skip('当前隔离环境不允许创建测试符号链接')
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(actual)], capture_output=True)
        if result.returncode:
            pytest.skip('当前隔离环境不允许创建测试重解析目录')
    try:
        with pytest.raises(ValueError):
            stage_package(path, link / 'stage', **kwargs(path))
        assert not (actual / 'stage').exists()
    finally:
        if link.is_symlink():
            link.unlink()
        elif link.exists():
            link.rmdir()


def test_stage_rejects_reparse_metadata_before_creating_destination(tmp_path, monkeypatch):
    path, _ = package(tmp_path)
    parent = tmp_path / 'ordinary-parent'; parent.mkdir()
    original = Path.lstat
    def marked(candidate, *args, **options):
        info = original(candidate, *args, **options)
        if candidate == parent:
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
        return info
    monkeypatch.setattr(Path, 'lstat', marked)
    with pytest.raises(ValueError):
        stage_package(path, parent / 'stage', **kwargs(path))
    assert not (parent / 'stage').exists()
