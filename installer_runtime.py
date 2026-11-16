"""全量安装后的程序基线核验；只保留明确识别的旧运行库，不访问业务数据。"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import uuid

import delta_format as fmt
from incremental_apply import (_file, _inventory, _record, _root, _zone_bytes, _zone_hash,
                               _publish_transaction, has_pending_update, runtime_guard, verify_tree)
from native_files import plain_path
from update_file_ops import move_checked

CONTENTS = 'PACKAGE_CONTENTS.sha256'
HISTORY = '.workbox-runtime-history'
PLAN_FORMAT = 'workbox-full-install-runtime-plan-v1'
RECEIPT_FORMAT = 'workbox-full-install-runtime-receipt-v1'
MAX_HISTORY = 1000

# Public releases v1.0.0.20261113.1 / v1.0.0.20261113.2, PACKAGE_CONTENTS.sha256:
# 3ccb6ede0811949e467b6a58bdb6ac74af733bfb1a63c366b769cb2477f94b34
# ac8439bef005bff7f2bd2a5e7b91c81614fa851f01ae10ebf2e8721b4383cd31
# These exact path/digest pairs are absent from the current program payload.
# A familiar filename alone never authorizes moving a file.
# Keep this catalog append-only: older completed recovery plans must stay readable
# after future releases add their own explicitly verified obsolete path/hash pairs.
KNOWN_OBSOLETE = {
    '_internal/_asyncio.pyd': frozenset({'04ce6c65ce6cb77e671111aabc4c63c69f4b7ec538e2a704e734619b08d671ea'}),
    '_internal/_overlapped.pyd': frozenset({'ace56b8216339f1b7f1080b49dc0b233c56dead8a170fd2600a62e69d1d5974d'}),
    '_internal/libcrypto-3.dll': frozenset({'05fe080eab7fc535c51e10c1bd76a2f3e6217f9c91a25034774588881c3f99de'}),
    '_internal/libssl-3.dll': frozenset({'2e9fbcd8f7fdc13a5179533239811456554f2b3aa2fb10e1b17be0df81c79006'}),
    '_internal/setuptools/_vendor/importlib_metadata-8.0.0.dist-info/INSTALLER': frozenset({'ceebae7b8927a3227e5303cf5e0f1f7b34bb542ad7250ac03fbcde36ec2f1508'}),
    '_internal/setuptools/_vendor/importlib_metadata-8.0.0.dist-info/LICENSE': frozenset({'cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30'}),
    '_internal/setuptools/_vendor/importlib_metadata-8.0.0.dist-info/METADATA': frozenset({'6a7b90effee1e09d5b484cdf7232016a43e2d9cc9543bcbb8e494b1ec05e1f59'}),
    '_internal/setuptools/_vendor/importlib_metadata-8.0.0.dist-info/RECORD': frozenset({'0d8d3c6eeb9ebbe86cac7d60861552433c329da9ea51248b61d02be2e5e64030'}),
    '_internal/setuptools/_vendor/importlib_metadata-8.0.0.dist-info/REQUESTED': frozenset({'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'}),
    '_internal/setuptools/_vendor/importlib_metadata-8.0.0.dist-info/WHEEL': frozenset({'9a0b8c95618c5fe5479cca4a3a38d089d228d6cb1194216ee1ae26069cf5b363'}),
    '_internal/setuptools/_vendor/importlib_metadata-8.0.0.dist-info/top_level.txt': frozenset({'08eddf0fdcb29403625e4acca38a872d5fe6a972f6b02e4914a82dd725804fe0'}),
    '_internal/setuptools/_vendor/jaraco/text/Lorem ipsum.txt': frozenset({'37fedcffbf73c4eb9f058f47677cb33203a436ff9390e4d38a8e01c9dad28e0b'}),
    '_internal/setuptools/_vendor/wheel-0.45.1.dist-info/INSTALLER': frozenset({'ceebae7b8927a3227e5303cf5e0f1f7b34bb542ad7250ac03fbcde36ec2f1508'}),
    '_internal/setuptools/_vendor/wheel-0.45.1.dist-info/LICENSE.txt': frozenset({'30c23618679108f3e8ea1d2a658c7ca417bdfc891c98ef1a89fa4ff0c9828654'}),
    '_internal/setuptools/_vendor/wheel-0.45.1.dist-info/METADATA': frozenset({'98acfce07ee6ee3b31272cde21c4d53918936f434f315dfd2af3886211a09a30'}),
    '_internal/setuptools/_vendor/wheel-0.45.1.dist-info/RECORD': frozenset({'d639f1ac7c993c3715bd42f27c616189b6b86792fdfd1b17afd77170d6e16984'}),
    '_internal/setuptools/_vendor/wheel-0.45.1.dist-info/REQUESTED': frozenset({'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'}),
    '_internal/setuptools/_vendor/wheel-0.45.1.dist-info/WHEEL': frozenset({'0a950253178741b44de54191407611268acee407fe432fdf1cc72d710f034862'}),
    '_internal/setuptools/_vendor/wheel-0.45.1.dist-info/entry_points.txt': frozenset({'ad363505b90f1e1906326e10dc5d29233241cd6da4331a06d68ae27dfbc6740d'}),
}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _identity(path):
    info = plain_path(path).stat()
    _require(bool(info.st_ino), '无法核对程序目录或文件身份。')
    return [info.st_dev, info.st_ino]


def _absolute(app_dir):
    _require(Path(app_dir).is_absolute(), '程序目录必须是本机绝对路径。')
    return plain_path(app_dir)


def _workbox_process_images():
    """只查询同名进程映像；兼容尚未持有 runtime_guard 的旧发行版。"""
    if os.name != 'nt':
        raise OSError('全量安装的进程占用核验仅支持 Windows。')
    import ctypes
    from ctypes import wintypes as w

    # Official PROCESSENTRY32W layout; ULONG_PTR follows the calling process width.
    # https://learn.microsoft.com/windows/win32/api/tlhelp32/ns-tlhelp32-processentry32w
    class ProcessEntry(ctypes.Structure):
        _fields_ = [('dwSize', w.DWORD), ('cntUsage', w.DWORD), ('th32ProcessID', w.DWORD),
                    ('th32DefaultHeapID', ctypes.c_size_t), ('th32ModuleID', w.DWORD),
                    ('cntThreads', w.DWORD), ('th32ParentProcessID', w.DWORD),
                    ('pcPriClassBase', w.LONG), ('dwFlags', w.DWORD), ('szExeFile', w.WCHAR * 260)]

    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
    api.CreateToolhelp32Snapshot.restype = w.HANDLE
    for name in ('Process32FirstW', 'Process32NextW'):
        operation = getattr(api, name)
        operation.argtypes = [w.HANDLE, ctypes.POINTER(ProcessEntry)]
        operation.restype = w.BOOL
    api.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    api.OpenProcess.restype = w.HANDLE
    api.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)]
    api.QueryFullProcessImageNameW.restype = w.BOOL
    api.CloseHandle.argtypes = [w.HANDLE]
    api.CloseHandle.restype = w.BOOL
    snapshot = api.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS; no modules or process memory.
    if snapshot == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_last_error(), '无法核对运行中的 WorkBox，请退出后重试安装。')
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        images = []
        available = api.Process32FirstW(snapshot, ctypes.byref(entry))
        for _ in range(100000):
            if not available:
                error = ctypes.get_last_error()
                if error == 18:  # ERROR_NO_MORE_FILES is the documented end of the snapshot.
                    return images
                raise OSError(error, '无法完整核对进程列表，未开始安装。')
            if entry.szExeFile.casefold() == 'workbox.exe':
                # QueryFullProcessImageNameW accepts this limited, read-only access.
                # Never request write/terminate rights or alter process privileges.
                handle = api.OpenProcess(0x1000, False, entry.th32ProcessID)
                if not handle:
                    raise OSError(ctypes.get_last_error(), '无法核对 WorkBox 的运行位置，请退出后重试安装。')
                try:
                    buffer = ctypes.create_unicode_buffer(32768)
                    size = w.DWORD(len(buffer))
                    if not api.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                        raise OSError(ctypes.get_last_error(), '无法核对 WorkBox 的运行位置，请退出后重试安装。')
                    _require(0 < size.value < len(buffer), 'WorkBox 进程映像路径无效，未开始安装。')
                    images.append(buffer.value)
                finally:
                    api.CloseHandle(handle)
            available = api.Process32NextW(snapshot, ctypes.byref(entry))
        raise RuntimeError('进程列表超过核验限额，未开始安装。')
    finally:
        api.CloseHandle(snapshot)


def _assert_no_running_app(root):
    target = plain_path(root / 'WorkBox.exe')
    if not target.exists():
        return
    for image in _workbox_process_images():
        image_path = Path(image)
        _require(image_path.is_absolute(), '无法核对 WorkBox 进程映像路径，未开始安装。')
        # samefile also recognizes ordinary Windows short-name aliases, without
        # reading executable bytes or opening any application data.
        if image_path == target or os.path.samefile(image_path, target):
            raise RuntimeError('请先退出 WorkBox，再重新运行安装程序。')


def _bounded_record(path, limit=fmt.MAX_FILE_SIZE):
    _require(plain_path(path).stat().st_size <= limit, '程序文件或恢复记录超过校验限额。')
    record = _record(path)
    _require(record['size'] <= limit, '程序文件或恢复记录超过校验限额。')
    return record


def _read_bytes(path, limit):
    expected = _bounded_record(path, limit)
    with plain_path(path).open('rb') as stream:
        raw = stream.read(limit + 1)
    _require(len(raw) == expected['size'] and hashlib.sha256(raw).hexdigest() == expected['sha256']
             and _bounded_record(path, limit) == expected, '程序清单或恢复记录在读取时发生变化。')
    return raw


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, '恢复记录包含重复字段。')
        result[key] = value
    return result


def _read_json(path):
    return json.loads(_read_bytes(path, fmt.MAX_MANIFEST_SIZE).decode('utf-8'), object_pairs_hook=_pairs)


def _write_new_json(path, value):
    # Plans are written only inside unpublished preparation directories. Receipts
    # become authoritative only after a fully flushed temporary file is moved.
    path = plain_path(path)
    temporary = (plain_path(path.parent / ('.pending-receipt-' + uuid.uuid4().hex))
                 if path.name == 'receipt.json' else path)
    with temporary.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        stream.flush()
        os.fsync(stream.fileno())
    if temporary != path:
        move_checked(temporary, path, _bounded_record(temporary, fmt.MAX_MANIFEST_SIZE), expected_zone=None)


def _validate_pin(expected_sha256):
    _require(isinstance(expected_sha256, str) and fmt.SHA256.fullmatch(expected_sha256) is not None,
             '必须提供安装包固定的程序清单 SHA-256。')


def _records(root, expected_sha256):
    _validate_pin(expected_sha256)
    path = _file(root, CONTENTS)
    raw = _read_bytes(path, fmt.MAX_MANIFEST_SIZE)
    _require(hashlib.sha256(raw).hexdigest() == expected_sha256, '安装后的程序清单与安装包不一致。')
    expected, folded = {}, set()
    for line in raw.decode('utf-8-sig').splitlines():
        match = re.fullmatch(r'([0-9a-f]{64})  (.+)', line)
        _require(match is not None, '程序清单行格式无效。')
        digest, name = match.groups()
        fmt.validate_path(name)
        _require(name.casefold() != CONTENTS.casefold() and name.casefold() not in folded,
                 '程序清单包含自身、重复路径或大小写碰撞。')
        folded.add(name.casefold())
        expected[name] = digest
        _require(len(expected) < fmt.MAX_FILES, '程序清单条目超过限额。')
    _require(fmt.REQUIRED_FILES <= {*expected, CONTENTS}, '程序清单缺少应用、更新助手或必要运行库。')
    fmt._validate_path_set(expected)
    records = {CONTENTS: {'sha256': expected_sha256, 'size': len(raw)}}
    total = len(raw)
    for name, digest in expected.items():
        record = _bounded_record(_file(root, name))
        _require(record['sha256'] == digest, '安装后的程序文件与清单不一致：' + name)
        total += record['size']
        _require(total <= fmt.MAX_TOTAL_SIZE, '程序载荷总长度超过限额。')
        records[name] = record
    return fmt._mapping(records)


def _entry(path):
    before = plain_path(path).stat()
    record = _bounded_record(path)
    zone = _zone_hash(_zone_bytes(path))
    after = plain_path(path).stat()
    _require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
             == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns),
             '旧运行库在预检期间发生变化。')
    return {**record, 'identity': _identity(path), 'zone_sha256': zone}


def _valid_identity(value):
    return (type(value) is list and len(value) == 2
            and all(type(part) is int and part >= 0 for part in value) and value[1] > 0)


def _validate_plan(plan, root):
    _require(type(plan) is dict and set(plan) == {'format', 'app_dir', 'app_identity', 'manifest_sha256', 'files'}
             and plan['format'] == PLAN_FORMAT, '旧运行库恢复计划格式无效。')
    _require(plan['app_dir'] == str(root) and _valid_identity(plan['app_identity'])
             and plan['app_identity'] == _identity(root), '旧运行库恢复计划不属于当前程序目录。')
    _require(isinstance(plan['manifest_sha256'], str)
             and fmt.SHA256.fullmatch(plan['manifest_sha256']) is not None, '恢复计划的程序清单摘要无效。')
    files = plan['files']
    _require(type(files) is dict and 0 < len(files) <= fmt.MAX_FILES, '旧运行库恢复计划数量无效。')
    total = 0
    for name, entry in files.items():
        fmt.validate_path(name)
        _require(type(entry) is dict and set(entry) == {'sha256', 'size', 'identity', 'zone_sha256'},
                 '旧运行库恢复文件记录无效。')
        _require(name in KNOWN_OBSOLETE and isinstance(entry['sha256'], str)
                 and entry['sha256'] in KNOWN_OBSOLETE[name],
                 '恢复计划包含未明确识别的旧运行库。')
        _require(type(entry['size']) is int and 0 <= entry['size'] <= fmt.MAX_FILE_SIZE
                 and _valid_identity(entry['identity']), '恢复计划文件长度或身份无效。')
        _require(entry['zone_sha256'] is None or (isinstance(entry['zone_sha256'], str)
                 and fmt.SHA256.fullmatch(entry['zone_sha256']) is not None), '恢复计划来源标记无效。')
        total += entry['size']
    _require(total <= fmt.MAX_TOTAL_SIZE, '旧运行库恢复计划总长度超过限额。')
    fmt._validate_path_set(files)


def _payload_names(transaction):
    folder = plain_path(transaction / 'files')
    if not folder.exists():
        return set()
    _require(folder.is_dir(), '旧运行库恢复载荷不是普通目录。')
    pending, names, visited = [folder], set(), 0
    while pending:
        for child in plain_path(pending.pop()).iterdir():
            visited += 1
            _require(visited <= fmt.MAX_FILES * 8, '旧运行库恢复目录超过限额。')
            child = plain_path(child)
            if child.is_dir():
                pending.append(child)
            else:
                name = child.relative_to(folder).as_posix()
                fmt.validate_path(name)
                names.add(name)
    return names


def _locations(root, transaction, plan, *, completed=False):
    moved, remaining = set(), set()
    for name, expected in plan['files'].items():
        source, destination = _file(root, name), _file(transaction / 'files', name)
        present = source.exists()
        saved = destination.exists()
        _require(saved if completed else present != saved,
                 '旧运行库原位与恢复位置冲突或同时缺失：' + name)
        path = destination if saved else source
        _require(_entry(path) == expected, '旧运行库或恢复副本发生未知变更：' + name)
        (moved if saved else remaining).add(name)
    _require(_payload_names(transaction) == moved, '旧运行库恢复目录含有未登记文件。')
    return remaining


def _histories(root, expected_sha256=None):
    history = plain_path(root / HISTORY)
    if not history.exists():
        return []
    _require(history.is_dir() and history.stat().st_dev == root.stat().st_dev,
             '旧运行库历史必须是同卷普通目录。')
    pending = []
    for count, child in enumerate(history.iterdir(), 1):
        _require(count <= MAX_HISTORY, '旧运行库恢复记录超过限额。')
        child = plain_path(child)
        _require(child.is_dir(), '旧运行库历史包含未知内容。')
        if re.fullmatch(r'\.prepare-[a-f0-9]{32}', child.name) is not None:
            # No program file is moved until the preparation directory is registered.
            # Incomplete metadata remains untouched and never authorizes a move.
            continue
        _require(re.fullmatch(r'[0-9a-f]{32}', child.name) is not None, '旧运行库历史包含未知内容。')
        names = set()
        for entry_count, path in enumerate(child.iterdir(), 1):
            _require(entry_count <= MAX_HISTORY + 3, '旧运行库恢复元数据超过限额。')
            if path.name in {'plan.json', 'receipt.json', 'files'}:
                names.add(path.name)
            else:
                path = plain_path(path)
                _require(re.fullmatch(r'\.pending-receipt-[a-f0-9]{32}', path.name) is not None
                         and path.is_file() and path.stat().st_nlink == 1
                         and path.stat().st_size <= fmt.MAX_MANIFEST_SIZE,
                         '旧运行库恢复目录包含未知内容。')
        _require('plan.json' in names, '旧运行库恢复计划缺失。')
        plan = _read_json(child / 'plan.json')
        _validate_plan(plan, root)
        complete = 'receipt.json' in names
        if complete:
            expected_receipt = _receipt(child, plan)
            _require(_read_json(child / 'receipt.json') == expected_receipt, '旧运行库完成凭据无效。')
        else:
            _require(expected_sha256 is None or expected_sha256 == plan['manifest_sha256'],
                     '存在另一程序版本尚未完成的旧运行库恢复计划。')
            pending.append((child, plan))
        _locations(root, child, plan, completed=complete)
    _require(len(pending) <= 1, '存在多个未完成的旧运行库恢复计划，请保留现场检查。')
    return pending


def _receipt(transaction, plan):
    return {'format': RECEIPT_FORMAT, 'manifest_sha256': plan['manifest_sha256'],
            'plan_sha256': _bounded_record(transaction / 'plan.json', fmt.MAX_MANIFEST_SIZE)['sha256'],
            'retired': len(plan['files'])}


def _check_pending(root):
    _require(not has_pending_update(root), '存在未完成的增量更新，请先恢复后再安装。')


def preflight_full_install(app_dir, expected_manifest_sha256):
    """复制安装文件前检查占用和未完成更新；全新目标不创建目录。"""
    _validate_pin(expected_manifest_sha256)
    root = _absolute(app_dir)
    if not root.exists():
        return {'status': 'ready'}
    root = _root(root)
    _assert_no_running_app(root)
    with runtime_guard(root, exclusive=True):
        _check_pending(root)
        _histories(root, expected_manifest_sha256)
    return {'status': 'ready'}


def _check_state(root, records, transaction, plan):
    _require(_identity(root) == plan['app_identity'], '程序目录身份发生变化。')
    _require(sum(value['size'] for value in records.values())
             + sum(value['size'] for value in plan['files'].values()) <= fmt.MAX_TOTAL_SIZE,
             '程序目录总长度超过校验限额。')
    remaining = _locations(root, transaction, plan)
    _require(not (set(plan['files']) & set(records)), '恢复计划与新程序文件发生冲突。')
    _require(_inventory(root) == set(records) | remaining, '程序目录存在未登记文件或缺失文件。')
    for name, expected in records.items():
        _require(_bounded_record(_file(root, name)) == expected, '当前程序文件在预检后发生变化：' + name)
    return remaining


def finalize_full_install(app_dir, expected_manifest_sha256):
    """按安装包固定清单核验；只移动已知旧字节，保留可重入且不覆盖的恢复记录。"""
    _validate_pin(expected_manifest_sha256)
    root = _root(_absolute(app_dir))
    with runtime_guard(root, exclusive=True):
        _check_pending(root)
        pending = _histories(root, expected_manifest_sha256)
        records = _records(root, expected_manifest_sha256)
        extras = _inventory(root) - set(records)
        if pending:
            transaction, plan = pending[0]
        elif extras:
            entries = {}
            for name in sorted(extras):
                fmt.validate_path(name)
                _require(name in KNOWN_OBSOLETE, '程序目录含有未知文件，未移动：' + name)
                entry = _entry(_file(root, name))
                _require(entry['sha256'] in KNOWN_OBSOLETE[name], '旧运行库字节未明确识别，未移动：' + name)
                entries[name] = entry
            _require(sum(value['size'] for value in records.values())
                     + sum(value['size'] for value in entries.values()) <= fmt.MAX_TOTAL_SIZE,
                     '程序目录总长度超过校验限额。')
            plan = {'format': PLAN_FORMAT, 'app_dir': str(root), 'app_identity': _identity(root),
                    'manifest_sha256': expected_manifest_sha256, 'files': entries}
            _validate_plan(plan, root)
            history = plain_path(root / HISTORY)
            history.mkdir(exist_ok=True)
            _require(history.stat().st_dev == root.stat().st_dev, '旧运行库历史目录不在同卷。')
            identifier = uuid.uuid4().hex
            preparation = plain_path(history / ('.prepare-' + identifier))
            preparation.mkdir()
            _write_new_json(preparation / 'plan.json', plan)
            transaction = plain_path(history / identifier)
            _publish_transaction(preparation, transaction)
        else:
            verify_tree(root, records)
            return {'status': 'verified', 'retired': 0, 'recovery_dir': None}
        # Recheck every target and every planned original/recovery file before the first move,
        # including a retry where some files already reside in history.
        remaining = _check_state(root, records, transaction, plan)
        for name in sorted(remaining):
            _require(_identity(root) == plan['app_identity'], '移动期间程序目录身份发生变化。')
            source, destination = _file(root, name), _file(transaction / 'files', name)
            _require(_entry(source) == plan['files'][name], '旧运行库在移动前发生变化：' + name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            plain_path(destination.parent)
            expected = {key: plan['files'][name][key] for key in ('sha256', 'size')}
            zone = _zone_bytes(source)
            _require(_zone_hash(zone) == plan['files'][name]['zone_sha256'], '旧运行库来源标记在移动前发生变化。')
            move_checked(source, destination, expected, expected_zone=zone)
        _require(not _check_state(root, records, transaction, plan), '旧运行库尚未全部保留。')
        verify_tree(root, records)
        _write_new_json(transaction / 'receipt.json', _receipt(transaction, plan))
        return {'status': 'verified', 'retired': len(plan['files']),
                'recovery_dir': transaction.relative_to(root).as_posix()}
