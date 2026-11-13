"""受限的 WorkBox 程序增量包；不依赖 Qt，也不接触业务数据。

外层包和所有成员均按实际字节核验。Windows 读取期间仅共享读，禁止其他
写入或删除句柄；staging 只写入新目录，不调用 ZIP 的通用解压接口。
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import unicodedata
import zipfile
import zlib

from native_files import plain_path

MAX_FILES = 10000
MAX_FILE_SIZE = 512 * 1024 * 1024
MAX_TOTAL_SIZE = 512 * 1024 * 1024
MAX_PACKAGE_SIZE = 256 * 1024 * 1024
MAX_MANIFEST_SIZE = 4 * 1024 * 1024
CHUNK_SIZE = 1024 * 1024
FORMAT = 'workbox-file-delta-v1'
REQUIRED_FILES = frozenset({
    'WorkBox.exe', 'WorkBoxUpdater.exe', 'PACKAGE_CONTENTS.sha256',
    '_internal/python311.dll', '_internal/PySide6/Qt6Core.dll',
    '_internal/PySide6/plugins/platforms/qwindows.dll',
})
ROOT_FILES = frozenset({'WorkBox.exe', 'WorkBoxUpdater.exe', 'PACKAGE_CONTENTS.sha256',
                        '安装说明.txt', 'THIRD_PARTY_NOTICES.md'})
PRIVATE_PARTS = frozenset({'data', 'backups', 'logs', '.local', '.git', '.codex',
                           '工作项目', 'zip归档', '升级恢复点'})
PRIVATE_SUFFIXES = frozenset({'.db', '.db-wal', '.db-shm', '.sqlite', '.sqlite3', '.csv',
                              '.doc', '.docx', '.xls', '.xlsx', '.pdf', '.zip', '.7z', '.log'})
SHA256 = re.compile(r'[0-9a-f]{64}')


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def build_key(tag: str) -> tuple[int, int]:
    if not isinstance(tag, str):
        raise ValueError('发行版本格式无效。')
    match = re.fullmatch(r'v1\.0\.0\.(20[0-9]{6})(?:\.([1-9][0-9]{0,3}))?', tag)
    if match is None:
        raise ValueError('发行版本格式无效。')
    try:
        datetime.strptime(match[1], '%Y%m%d')
    except ValueError as error:
        raise ValueError('发行版本日期无效。') from error
    return int(match[1]), int(match[2] or 0)


def delta_filename(from_tag: str, to_tag: str) -> str:
    old, new = build_key(from_tag), build_key(to_tag)
    _require(new > old, '增量目标必须是更新的发行版本。')
    def suffix(key):
        return str(key[0]) + (f'-r{key[1]}' if key[1] else '')
    return f'WorkBox-1.0.0-Delta-x64-{suffix(old)}-to-{suffix(new)}.wbdelta'


def validate_path(value: str) -> str:
    _require(isinstance(value, str) and bool(value), '程序路径必须是非空文字。')
    _require(value == unicodedata.normalize('NFC', value), '程序路径必须使用 NFC 正规化。')
    _require(not any(unicodedata.category(ch) in {'Cc', 'Cf', 'Cs'} for ch in value), '程序路径含控制字符。')
    _require(not any(ch in value for ch in '\\:<>"|?*'), '程序路径含非法 Windows 字符。')
    parts = value.split('/')
    _require(all(part and part not in {'.', '..'} and not part.endswith((' ', '.')) for part in parts),
             '程序路径不是规范的相对路径。')
    for part in parts:
        _require(part.casefold() not in PRIVATE_PARTS, '增量包不得包含业务或私有目录。')
        base = part.split('.', 1)[0].rstrip(' ')
        _require(re.fullmatch(r'(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])', base, re.I) is None,
                 '程序路径不得使用设备名。')
    path = PurePosixPath(value)
    _require(not path.is_absolute() and path.as_posix() == value, '程序路径不是规范相对路径。')
    _require(path.suffix.casefold() not in PRIVATE_SUFFIXES, '增量包不得包含业务数据文件。')
    if len(parts) == 1:
        _require(value in ROOT_FILES, '增量包根文件不在允许清单内。')
    else:
        _require(parts[0] in {'_internal', 'licenses'}, '程序路径不在允许的载荷目录内。')
        if parts[0] == 'licenses':
            _require(path.suffix.casefold() == '.txt', '许可证必须是文本文件。')
    return value


def _validate_path_set(paths):
    files = set()
    spelling = {}
    for name in paths:
        validate_path(name)
        parts = name.split('/')
        for count in range(1, len(parts) + 1):
            prefix = '/'.join(parts[:count])
            key = prefix.casefold()
            _require(key not in spelling or spelling[key] == prefix, '程序路径存在大小写碰撞。')
            spelling[key] = prefix
        files.add(name.casefold())
    for name in files:
        parts = name.split('/')
        _require(not any('/'.join(parts[:n]) in files for n in range(1, len(parts))),
                 '程序载荷存在文件与目录前缀冲突。')


def _mapping(value):
    _require(type(value) is dict and 0 < len(value) <= MAX_FILES, '程序文件映射数量无效。')
    result, total = {}, 0
    for name, record in value.items():
        validate_path(name)
        _require(type(record) is dict and set(record) == {'sha256', 'size'}, '程序文件记录字段无效。')
        digest, size = record['sha256'], record['size']
        _require(isinstance(digest, str) and SHA256.fullmatch(digest) is not None, '程序文件摘要无效。')
        _require(type(size) is int and 0 <= size <= MAX_FILE_SIZE, '程序文件长度超限或无效。')
        total += size
        _require(total <= MAX_TOTAL_SIZE, '程序载荷总长度超限。')
        result[name] = {'sha256': digest, 'size': size}
    _require(REQUIRED_FILES <= result.keys(), '程序载荷缺少必要运行时、更新助手或清单。')
    _validate_path_set(result)
    return result


def validate_manifest(value: object) -> dict:
    _require(type(value) is dict and set(value) == {'format', 'from_tag', 'to_tag', 'base', 'target'},
             '增量清单字段无效。')
    _require(value['format'] == FORMAT, '不支持此增量格式。')
    delta_filename(value['from_tag'], value['to_tag'])
    base, target = _mapping(value['base']), _mapping(value['target'])
    # 跨版本的大小写改名及文件/目录转换也不能交给 Windows 猜测处理。
    _validate_path_set(set(base) | set(target))
    return {'format': FORMAT, 'from_tag': value['from_tag'], 'to_tag': value['to_tag'],
            'base': base, 'target': target}


def changed_paths(manifest: dict) -> list[str]:
    value = validate_manifest(manifest)
    return sorted(name for name, record in value['target'].items() if value['base'].get(name) != record)


def removed_paths(manifest: dict) -> list[str]:
    value = validate_manifest(manifest)
    return sorted(value['base'].keys() - value['target'].keys())


@contextmanager
def _open_package(path):
    path = plain_path(path)
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        import msvcrt
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                      wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.CreateFileW(str(path), 0x80000000, 0x1, None, 3, 0x08200000, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            fd = msvcrt.open_osfhandle(int(handle), os.O_RDONLY | os.O_BINARY)
        except BaseException:
            kernel.CloseHandle(handle)
            raise
        stream = os.fdopen(fd, 'rb')
    else:
        stream = path.open('rb')
    with stream:
        info = os.fstat(stream.fileno())
        _require(stat.S_ISREG(info.st_mode) and not (getattr(info, 'st_file_attributes', 0) & 0x400),
                 '增量包必须是普通本地文件。')
        yield stream


def _verify_outer(stream, expected_sha256, expected_size):
    _require(isinstance(expected_sha256, str) and SHA256.fullmatch(expected_sha256) is not None,
             '必须提供有效的增量包摘要。')
    _require(type(expected_size) is int and 0 < expected_size <= MAX_PACKAGE_SIZE, '增量包长度无效或超限。')
    _require(os.fstat(stream.fileno()).st_size == expected_size, '增量包实际长度不符。')
    stream.seek(0)
    digest, count = hashlib.sha256(), 0
    while True:
        chunk = stream.read(min(CHUNK_SIZE, expected_size - count + 1))
        if not chunk:
            break
        count += len(chunk)
        _require(count <= expected_size, '增量包实际长度超限。')
        digest.update(chunk)
    _require(count == expected_size and digest.hexdigest() == expected_sha256, '增量包摘要或长度不符。')
    stream.seek(0)


def _json_pairs(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, '增量清单包含重复 JSON 键。')
        result[key] = value
    return result


def _preflight_directory(stream, package_size):
    """在 ZipFile 分配成员对象之前，检查 EOCD 声明及实际目录条目数。"""
    length = min(package_size, 22 + 65535)
    stream.seek(package_size - length)
    tail = stream.read(length)
    index = tail.rfind(b'PK\x05\x06')
    _require(index >= 0 and len(tail) - index >= 22, 'ZIP 缺少完整目录结束记录。')
    end = struct.unpack('<4s4H2IH', tail[index:index + 22])
    _require(index + 22 + end[7] == len(tail), 'ZIP 目录结束记录长度不一致。')
    end_offset = package_size - length + index
    disk, directory_disk, local_count, count, directory_size, directory_offset = end[1:7]
    _require(disk == directory_disk == 0 and local_count == count, '增量包不得分卷。')
    directory_end = end_offset
    if count == 0xffff or directory_size == 0xffffffff or directory_offset == 0xffffffff:
        _require(end_offset >= 20, 'ZIP64 目录定位记录缺失。')
        stream.seek(end_offset - 20)
        locator = stream.read(20)
        _require(len(locator) == 20, 'ZIP64 目录定位记录不完整。')
        marker, disk, offset64, disks = struct.unpack('<4sIQI', locator)
        _require(marker == b'PK\x06\x07' and disk == 0 and disks == 1
                 and offset64 + 56 <= end_offset - 20, 'ZIP64 定位记录无效或分卷。')
        stream.seek(offset64)
        record = stream.read(56)
        _require(len(record) == 56, 'ZIP64 目录结束记录不完整。')
        end64 = struct.unpack('<4sQ2H2I4Q', record)
        _require(end64[0] == b'PK\x06\x06' and end64[1] >= 44
                 and offset64 + 12 + end64[1] == end_offset - 20
                 and end64[4] == end64[5] == 0 and end64[6] == end64[7], 'ZIP64 目录结束记录无效。')
        count, directory_size, directory_offset = end64[7:10]
        directory_end = offset64
    _require(0 < count <= MAX_FILES + 1, '增量包目录成员数量超限。')
    _require(0 <= directory_offset < directory_end and directory_size >= 46
             and directory_offset + directory_size == directory_end, 'ZIP 中央目录边界无效。')
    stream.seek(directory_offset)
    remaining, actual_count = directory_size, 0
    while remaining:
        _require(remaining >= 46, 'ZIP 中央目录记录不完整。')
        header = stream.read(46)
        _require(len(header) == 46 and header[:4] == b'PK\x01\x02', 'ZIP 中央目录记录无效。')
        name_length, extra_length, comment_length = struct.unpack('<3H', header[28:34])
        size = 46 + name_length + extra_length + comment_length
        actual_count += 1
        _require(actual_count <= count and size <= remaining, 'ZIP 实际目录数量或长度超限。')
        stream.seek(size - 46, 1)
        remaining -= size
    _require(actual_count == count, 'ZIP 实际目录数量不符。')
    stream.seek(0)


def _read_member(archive, info, *, size, expected_sha256=None, destination=None):
    digest, count, chunks, crc = hashlib.sha256(), 0, [], 0

    def consume(chunk):
        nonlocal count, crc
        count += len(chunk)
        _require(count <= size and count <= MAX_FILE_SIZE, '增量成员实际解压长度超限。')
        digest.update(chunk)
        crc = zlib.crc32(chunk, crc)
        if destination is not None:
            destination.write(chunk)
        elif expected_sha256 is None:
            chunks.append(chunk)

    # 先让标准 ZIP reader 检查本地文件名、重叠条目等容器约束，再自行
    # 有界解码；ZipExtFile 会按声明 file_size 截断，不能证明实际解压长度。
    with archive.open(info, 'r'):
        source = archive.fp
        source.seek(info.header_offset)
        header = source.read(30)
        _require(len(header) == 30, 'ZIP 本地头不完整。')
        fields = struct.unpack('<4s5H3I2H', header)
        _require(fields[0] == b'PK\x03\x04' and fields[2] == info.flag_bits
                 and fields[3] == info.compress_type, 'ZIP 本地头与中央目录不一致。')
        source.seek(info.header_offset + 30 + fields[-2] + fields[-1])
        remaining = info.compress_size
        decoder = zlib.decompressobj(-15) if info.compress_type == zipfile.ZIP_DEFLATED else None
        while remaining:
            data = source.read(min(CHUNK_SIZE, remaining))
            _require(bool(data), 'ZIP 压缩内容不完整。')
            remaining -= len(data)
            if decoder is None:
                consume(data)
            else:
                while data:
                    consume(decoder.decompress(data, min(CHUNK_SIZE, size - count + 1)))
                    _require(not decoder.unused_data, 'Deflate 内容包含声明外字节。')
                    data = decoder.unconsumed_tail
        if decoder is not None:
            _require(decoder.eof and not decoder.unconsumed_tail and not decoder.unused_data,
                     'Deflate 内容未完整结束。')
    _require(count == size, '增量成员实际解压长度不符。')
    _require(crc == info.CRC, '增量成员 CRC 不符。')
    if expected_sha256 is not None:
        _require(digest.hexdigest() == expected_sha256, '增量成员实际摘要不符。')
    return b''.join(chunks)


@contextmanager
def _validated_package(package, *, expected_sha256, expected_size, from_tag, to_tag):
    delta_filename(from_tag, to_tag)
    with _open_package(package) as stream:
        _verify_outer(stream, expected_sha256, expected_size)
        try:
            _preflight_directory(stream, expected_size)
            with zipfile.ZipFile(stream, 'r') as archive:
                entries = archive.infolist()
                _require(0 < len(entries) <= MAX_FILES + 1, '增量包成员数量超限。')
                infos, declared_total = {}, 0
                for info in entries:
                    name = info.filename
                    _require(info.orig_filename == name and name not in infos, '增量包成员名重复或含控制字符。')
                    _require(not info.is_dir() and not (info.external_attr & (0x10 | 0x400)),
                             '增量包不得包含目录或重解析成员。')
                    mode = info.external_attr >> 16
                    _require(stat.S_IFMT(mode) in (0, stat.S_IFREG), '增量包不得包含链接或特殊成员。')
                    _require(not (info.flag_bits & (1 | 0x40 | 0x2000)), '增量包不得加密。')
                    _require(info.compress_type in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}, '不支持此压缩算法。')
                    limit = MAX_MANIFEST_SIZE if name == 'delta.json' else MAX_FILE_SIZE
                    _require(0 <= info.file_size <= limit and 0 <= info.compress_size <= MAX_PACKAGE_SIZE,
                             '增量成员声明长度超限。')
                    if name != 'delta.json':
                        _require(name.startswith('payload/'), '增量包含未知成员。')
                        validate_path(name[len('payload/'):])
                        declared_total += info.file_size
                    _require(declared_total <= MAX_TOTAL_SIZE, '增量成员总原始长度超限。')
                    infos[name] = info
                _require('delta.json' in infos, '增量包缺少 delta.json。')
                manifest_info = infos['delta.json']
                raw = _read_member(archive, manifest_info, size=manifest_info.file_size)
                try:
                    value = json.loads(raw.decode('utf-8'), object_pairs_hook=_json_pairs,
                                       parse_constant=lambda value: (_ for _ in ()).throw(ValueError('非法 JSON 常量。')))
                    manifest = validate_manifest(value)
                except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
                    raise ValueError('增量 JSON 清单无效。') from error
                _require(manifest['from_tag'] == from_tag and manifest['to_tag'] == to_tag,
                         '增量包来源或目标版本不符。')
                changes = changed_paths(manifest)
                _require(set(infos) == {'delta.json', *('payload/' + name for name in changes)},
                         '增量包有缺失、未变化或声明外文件。')
                for name in changes:
                    info, record = infos['payload/' + name], manifest['target'][name]
                    _require(info.file_size == record['size'], 'ZIP 与目标清单声明长度不符。')
                    _read_member(archive, info, size=record['size'], expected_sha256=record['sha256'])
                _verify_outer(stream, expected_sha256, expected_size)
                yield archive, infos, manifest
                _verify_outer(stream, expected_sha256, expected_size)
        except (zipfile.BadZipFile, NotImplementedError, RuntimeError, EOFError, zlib.error, struct.error) as error:
            raise ValueError('增量 ZIP 容器无效或无法安全读取。') from error


def read_manifest(package: Path, *, expected_sha256: str, expected_size: int,
                  from_tag: str, to_tag: str) -> dict:
    with _validated_package(package, expected_sha256=expected_sha256, expected_size=expected_size,
                            from_tag=from_tag, to_tag=to_tag) as (_, _, manifest):
        return manifest


def stage_package(package: Path, destination: Path, *, expected_sha256: str, expected_size: int,
                  from_tag: str, to_tag: str) -> dict:
    destination = plain_path(destination)
    if destination.exists():
        raise FileExistsError('增量 staging 目录必须是新目录，禁止覆盖。')
    _require(destination.parent.is_dir(), 'staging 的普通本地父目录必须已经存在。')
    with _validated_package(package, expected_sha256=expected_sha256, expected_size=expected_size,
                            from_tag=from_tag, to_tag=to_tag) as (archive, infos, manifest):
        # 完整验证后才创建目录；失败留下独立 staging，不递归删除任何路径。
        destination = plain_path(destination)
        destination.mkdir(exist_ok=False)
        identity = destination.stat()
        changes = changed_paths(manifest)
        change_names = set(changes)
        for name in changes:
            plain_path(destination)
            current = destination.stat()
            _require((current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino), 'staging 目录标识改变。')
            target = plain_path(destination / name)
            _require(target.is_relative_to(destination), '增量输出路径越界。')
            target.parent.mkdir(parents=True, exist_ok=True)
            target = plain_path(target)
            record = manifest['target'][name]
            with target.open('xb') as output:
                _read_member(archive, infos['payload/' + name], size=record['size'],
                             expected_sha256=record['sha256'], destination=output)
                output.flush()
                os.fsync(output.fileno())
        current = plain_path(destination).stat()
        _require((current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino), 'staging 目录标识改变。')
        actual = set()
        for path in destination.rglob('*'):
            path = plain_path(path)
            if path.is_file():
                name = path.relative_to(destination).as_posix()
                _require(name in change_names, 'staging 出现未声明文件。')
                actual.add(name)
                record = manifest['target'][name]
                _require(path.stat().st_size == record['size'], 'staging 文件长度不符。')
                digest, count = hashlib.sha256(), 0
                with path.open('rb') as staged:
                    while chunk := staged.read(CHUNK_SIZE):
                        count += len(chunk)
                        _require(count <= record['size'], 'staging 文件实际长度超限。')
                        digest.update(chunk)
                _require(count == record['size'] and digest.hexdigest() == record['sha256'],
                         'staging 文件实际摘要不符。')
            else:
                _require(path.is_dir(), 'staging 出现特殊文件。')
        _require(actual == change_names, 'staging 缺少声明文件。')
        return manifest
