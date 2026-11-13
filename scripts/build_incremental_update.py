"""从两个只读、干净的安装载荷生成受限文件级增量包，不执行程序。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import uuid
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import delta_format as fmt
from delta_format import read_manifest
from native_files import ZipLease, plain_path

CONTENTS = 'PACKAGE_CONTENTS.sha256'
CHUNK = 1024 * 1024


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _file_facts(path, *, limit=fmt.MAX_FILE_SIZE, capture=False, sink=None):
    path = plain_path(path)
    before = path.stat()
    _require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1, '程序文件必须是单链接普通文件。')
    _require(0 <= before.st_size <= limit, '程序文件长度超限。')
    holder = ZipLease(path, movable=False) if os.name == 'nt' else path.open('rb')
    with holder as opened:
        stream = opened.stream if os.name == 'nt' else opened
        if os.name == 'nt':
            # 包格式不承载命名流；只读检查后拒绝，不能丢弃来源或未知元数据。
            _require(opened.zone_metadata() is None, '干净程序载荷不得含命名数据流。')
        current = os.fstat(stream.fileno())
        _require(current.st_nlink == 1 and _identity(before) == _identity(current), '程序文件在打开时发生变化。')
        digest, count, parts = hashlib.sha256(), 0, []
        while block := stream.read(CHUNK):
            count += len(block)
            _require(count <= limit, '程序文件在读取时超过限额。')
            digest.update(block)
            if capture:
                parts.append(block)
            if sink is not None:
                sink(block)
        after = os.fstat(stream.fileno())
        _require(after.st_nlink == 1 and count == before.st_size
                 and _identity(before) == _identity(after)
                 and _identity(before) == _identity(plain_path(path).stat()), '程序文件在读取时发生变化。')
    return {'sha256': digest.hexdigest(), 'size': count}, b''.join(parts) if capture else None


def _inventory(root):
    pending, names, visited, total = [root], set(), 0, 0
    while pending:
        parent = plain_path(pending.pop())
        for entry in parent.iterdir():
            visited += 1
            _require(visited <= fmt.MAX_FILES * 8, '程序载荷目录数量超限。')
            entry = plain_path(entry)
            _require(entry.is_relative_to(root), '程序文件越出载荷目录。')
            name, info = entry.relative_to(root).as_posix(), entry.stat()
            if stat.S_ISDIR(info.st_mode):
                fmt.validate_path(name + '/placeholder.txt')
                pending.append(entry)
            else:
                fmt.validate_path(name)
                _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, '程序载荷含链接或特殊文件。')
                names.add(name)
                total += info.st_size
                _require(len(names) <= fmt.MAX_FILES and info.st_size <= fmt.MAX_FILE_SIZE
                         and total <= fmt.MAX_TOTAL_SIZE, '程序载荷数量或总长度超限。')
    return names


def _payload(root):
    root = plain_path(root)
    _require(root.is_dir(), '输入必须是本机普通程序载荷目录。')
    identity = (root.stat().st_dev, root.stat().st_ino)
    names = _inventory(root)
    facts, raw = _file_facts(root / CONTENTS, limit=fmt.MAX_MANIFEST_SIZE, capture=True)
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeError as error:
        raise ValueError('程序清单必须是 UTF-8。') from error
    expected, folded = {}, set()
    for line in text.splitlines():
        match = re.fullmatch(r'([0-9a-f]{64})  (.+)', line)
        _require(match is not None, '程序清单行格式无效。')
        digest, name = match.groups()
        fmt.validate_path(name)
        _require(name.casefold() != CONTENTS.casefold(), '旧程序清单不能包含自身。')
        _require(name.casefold() not in folded, '程序清单有重复或大小写碰撞。')
        folded.add(name.casefold()); expected[name] = digest
        _require(len(expected) < fmt.MAX_FILES, '程序清单条目超限。')
    _require(bool(expected) and names == {*expected, CONTENTS}, '程序载荷存在缺失或清单外文件。')
    records = {CONTENTS: facts}
    for name in sorted(expected):
        record, _ = _file_facts(root / name)
        _require(record['sha256'] == expected[name], '程序文件与载荷清单摘要不符。')
        records[name] = record
    _require(identity == (plain_path(root).stat().st_dev, root.stat().st_ino), '程序载荷目录标识已变化。')
    return identity, records


def _zip_info(name):
    info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    info._compresslevel = 9
    return info


def _publish_new(temporary, output, *, expected):
    plain_path(output.parent)
    plain_path(output)
    if output.exists():
        raise FileExistsError('增量包输出已存在，禁止覆盖。')
    if os.name == 'nt':
        # 句柄改名明确禁止覆盖；不使用存在检查后可能覆盖的 os.replace。
        with ZipLease(temporary) as lease:
            _require(lease.initial.st_nlink == 1 and lease.initial.st_size == expected['size'],
                     '增量包在发布前已被替换。')
            _require(lease.zone_metadata() is None, '增量临时包含额外命名流。')
            digest, count = hashlib.sha256(), 0
            for block in iter(lambda: lease.stream.read(CHUNK), b''):
                count += len(block)
                _require(count <= expected['size'], '增量包在发布前长度发生变化。')
                digest.update(block)
            _require(count == expected['size'] and digest.hexdigest() == expected['sha256'],
                     '增量包在发布前摘要发生变化。')
            lease.rename_to(output)
    else:
        actual, _ = _file_facts(temporary, limit=fmt.MAX_PACKAGE_SIZE)
        _require(actual == expected, '增量包在发布前发生变化。')
        os.link(temporary, output)
        temporary.unlink()


def build_delta(base_payload, target_payload, output, *, from_tag, to_tag):
    name = fmt.delta_filename(from_tag, to_tag)
    base, target, output = map(plain_path, (base_payload, target_payload, output))
    _require(output.name == name, '输出文件名必须符合指定来源和目标版本。')
    _require(not output.is_relative_to(base) and not output.is_relative_to(target), '增量输出不能位于输入载荷内。')
    if output.exists():
        raise FileExistsError('增量包输出已存在，禁止覆盖。')
    base_snapshot, target_snapshot = _payload(base), _payload(target)
    manifest = fmt.validate_manifest(dict(format=fmt.FORMAT, from_tag=from_tag, to_tag=to_tag,
                                         base=base_snapshot[1], target=target_snapshot[1]))
    changes, removed = fmt.changed_paths(manifest), fmt.removed_paths(manifest)
    raw = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    _require(len(raw) <= fmt.MAX_MANIFEST_SIZE, '增量清单长度超限。')
    output.parent.mkdir(parents=True, exist_ok=True)
    parent = plain_path(output.parent)
    parent_identity = (parent.stat().st_dev, parent.stat().st_ino)
    temporary = parent / ('.' + output.name + '.' + uuid.uuid4().hex + '.tmp')
    owned = None
    try:
        with temporary.open('xb') as stream:
            info = os.fstat(stream.fileno()); owned = (info.st_dev, info.st_ino)
            with ZipFile(stream, 'w', compression=ZIP_DEFLATED, compresslevel=9) as archive:
                archive.writestr(_zip_info('delta.json'), raw)
                for relative in changes:
                    with archive.open(_zip_info('payload/' + relative), 'w') as member:
                        def write(block):
                            member.write(block)
                            _require(stream.tell() <= fmt.MAX_PACKAGE_SIZE, '增量包长度超限。')
                        actual, _ = _file_facts(target / relative, sink=write)
                    _require(actual == manifest['target'][relative], '目标文件在生成增量期间发生变化。')
            stream.flush(); os.fsync(stream.fileno())
        package, _ = _file_facts(temporary, limit=fmt.MAX_PACKAGE_SIZE)
        read_manifest(temporary, expected_sha256=package['sha256'], expected_size=package['size'],
                      from_tag=from_tag, to_tag=to_tag)
        # 最终重读完整输入，包含未变化文件和清单，拒绝生成期间的变更或目录替换。
        _require(_payload(base) == base_snapshot and _payload(target) == target_snapshot,
                 '程序载荷在增量生成期间发生变化。')
        _require((plain_path(parent).stat().st_dev, parent.stat().st_ino) == parent_identity,
                 '增量输出目录标识已变化。')
        _require((plain_path(temporary).stat().st_dev, temporary.stat().st_ino) == owned,
                 '增量临时文件标识已变化。')
        _publish_new(temporary, output, expected=package)
        return dict(name=name, size=package['size'], sha256=package['sha256'], from_tag=from_tag,
                    to_tag=to_tag, changed_count=len(changes), removed_count=len(removed))
    finally:
        # 只清理本次创建且身份仍一致的临时文件，不删除输入或竞争者的输出。
        if owned is not None:
            try:
                path = plain_path(temporary)
                if ((plain_path(parent).stat().st_dev, parent.stat().st_ino) == parent_identity
                        and (path.stat().st_dev, path.stat().st_ino) == owned):
                    path.unlink()
            except (OSError, ValueError):
                pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--target', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--from-tag', required=True)
    parser.add_argument('--to-tag', required=True)
    args = parser.parse_args()
    try:
        result = build_delta(args.base, args.target, args.output, from_tag=args.from_tag, to_tag=args.to_tag)
    except (ValueError, OSError, RuntimeError) as error:
        # CLI 错误不输出系统异常中的私有绝对路径。
        print(json.dumps({'error': '增量包生成失败；输入未修改，既有输出未覆盖。',
                          'error_type': type(error).__name__}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
