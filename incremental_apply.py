"""只替换受信任清单中的程序文件；事务备份与恢复不打开业务数据库。"""
from __future__ import annotations

from concurrent.futures import CancelledError
from contextlib import contextmanager
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import time
import uuid

from delta_format import ROOT_FILES, changed_paths, removed_paths, read_manifest, stage_package, validate_manifest, validate_path, delta_filename
from native_files import plain_path
from update_file_ops import move_checked, swap_checked

JOURNAL_FORMAT = 'workbox-update-journal-v1'
TRANSACTION_PREFIX = '.workbox-update-'
TERMINAL = {'committed', 'rolled-back'}
PHASES = TERMINAL | {'preparing', 'prepared', 'applying', 'files-installed', 'rolling-back', 'rollback-failed'}
MAX_JOURNAL = 8 * 1024 * 1024


class UpdateApplyError(RuntimeError):
    def __init__(self, message, *, transaction_dir, rollback_complete):
        super().__init__(message)
        self.transaction_dir = Path(transaction_dir)
        self.rollback_complete = rollback_complete


def _cancel(event):
    if event is not None and event.is_set():
        raise CancelledError('已取消程序文件校验。')


def _root(path):
    root = plain_path(path)
    if not root.is_dir():
        raise ValueError('程序目录不存在。')
    info = root.stat()
    if not info.st_ino:
        raise ValueError('无法核对程序目录标识。')
    return root


def _file(root, name):
    validate_path(name)
    root = plain_path(root)
    path = plain_path(root.joinpath(*name.split('/')))
    if not path.is_relative_to(root):
        raise ValueError('程序文件路径越界。')
    return path


def _record(path, *, cancel_event=None):
    path = plain_path(path)
    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise ValueError('程序文件必须为单链接普通文件。')
    digest = hashlib.sha256()
    def read_bytes(stream):
        opened = os.fstat(stream.fileno())
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError('校验期间程序文件发生替换。')
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            _cancel(cancel_event)
            digest.update(block)
        return os.fstat(stream.fileno())
    if os.name == 'nt':
        from native_files import ZipLease
        with ZipLease(path, movable=False) as lease:
            lease.zone_metadata()  # 未识别的流也属于原字节，不能在替换或恢复中静默丢弃。
            after = read_bytes(lease.stream)
    else:
        with path.open('rb') as stream:
            after = read_bytes(stream)
    end = plain_path(path).stat()
    fields = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_nlink)
    if fields(before) != fields(after) or fields(before) != fields(end):
        raise ValueError('校验期间程序文件发生变化。')
    return {'sha256': digest.hexdigest(), 'size': before.st_size}


def _inventory(root, *, cancel_event=None):
    result = set()
    for name in ROOT_FILES:
        path = root / name
        if path.exists() or path.is_symlink():
            path = plain_path(path)
            if not path.is_file():
                raise ValueError('程序文件位置被目录占用。')
            result.add(name)
    for dirname in ('_internal', 'licenses'):
        folder = root / dirname
        if not folder.exists() and not folder.is_symlink():
            continue
        pending = [plain_path(folder)]
        if not folder.is_dir():
            raise ValueError('程序库目录不是普通目录。')
        while pending:
            _cancel(cancel_event)
            directory = plain_path(pending.pop())
            for child in directory.iterdir():
                child = plain_path(child)
                if child.is_dir():
                    pending.append(child)
                elif child.is_file():
                    result.add(child.relative_to(root).as_posix())
                else:
                    raise ValueError('程序目录包含特殊文件。')
                if len(result) > 10000 or len(pending) > 10000:
                    raise ValueError('程序目录超过校验限额。')
    return result


def verify_tree(app_dir, records, *, cancel_event=None):
    """只枚举程序白名单；不读取 data、卸载登记和用户根目录文件。"""
    _cancel(cancel_event)
    root = _root(app_dir)
    if not isinstance(records, dict) or not records:
        raise ValueError('缺少程序文件清单。')
    for name in records:
        validate_path(name)
    if _inventory(root, cancel_event=cancel_event) != set(records):
        raise ValueError('程序文件清单与当前基线不一致。')
    for name, expected in records.items():
        _cancel(cancel_event)
        if _record(_file(root, name), cancel_event=cancel_event) != expected:
            raise ValueError('程序文件校验与预期版本不一致：' + name)


@contextmanager
def runtime_guard(app_dir, *, exclusive=False):
    """应用保留共享句柄；更新和恢复独占。仅支持本机程序目录。"""
    root = _root(app_dir)
    path = plain_path(root / '.workbox-update.lock')
    if path.exists() and (not path.is_file() or path.stat().st_size or path.stat().st_nlink != 1):
        raise RuntimeError('更新锁位置存在未知内容，未覆盖。')
    if os.name == 'nt':
        from native_files import _api
        api = _api()
        access, share = (0xC0000000, 0) if exclusive else (0x80000000, 1)
        handle = api.CreateFileW(str(path), access, share, None, 4, 0x00200080, None)
        if handle == ctypes.c_void_p(-1).value:
            raise OSError(ctypes.get_last_error(), 'WorkBox 或另一个更新进程仍在使用程序目录。')
        try:
            plain_path(path)
            if path.stat().st_size or path.stat().st_nlink != 1:
                raise RuntimeError('更新锁不是预期文件。')
            yield
        finally:
            api.CloseHandle(handle)
    else:
        import fcntl
        with path.open('a+b') as stream:
            mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
            fcntl.flock(stream, mode | fcntl.LOCK_NB)
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)


def _replace(source, target):
    source, target = plain_path(source), plain_path(target)
    if os.name == 'nt':
        from ctypes import wintypes
        api = ctypes.WinDLL('kernel32', use_last_error=True)
        api.MoveFileExW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
        api.MoveFileExW.restype = wintypes.BOOL
        if not api.MoveFileExW(str(source), str(target), 0x1 | 0x8):
            raise OSError(ctypes.get_last_error(), '程序文件替换失败。')
    else:
        os.replace(source, target)


def _publish_transaction(preparation, transaction):
    """初始日志已 fsync；登记活动目录也等待 Windows 完成移动，不覆盖既有目录。"""
    preparation, transaction = plain_path(preparation), plain_path(transaction)
    if os.name != 'nt':
        raise OSError('程序增量替换仅支持 Windows。')
    from ctypes import wintypes
    from native_files import _windows_path
    before = preparation.stat()
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.MoveFileExW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
    api.MoveFileExW.restype = wintypes.BOOL
    if not api.MoveFileExW(_windows_path(preparation), _windows_path(transaction), 0x8):
        raise ctypes.WinError(ctypes.get_last_error())
    after = plain_path(transaction).stat()
    if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
        raise ValueError('活动更新目录标识发生变化，未修改程序文件。')


def _retry_change(operation, check):
    """等待短暂的 Windows 映像/扫描句柄，每次重试都重新核验，绝不解除占用。"""
    deadline = time.monotonic() + 5
    while True:
        try:
            check()
            return operation()
        except OSError as error:
            code = getattr(error, 'winerror', None) or error.errno
            if os.name != 'nt' or code not in (5, 32, 33) or time.monotonic() >= deadline:
                raise
            time.sleep(.1)


def _write_json(path, value):
    path = plain_path(path)
    temporary = plain_path(path.parent / ('.journal-' + uuid.uuid4().hex + '.tmp'))
    with temporary.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        stream.flush()
        os.fsync(stream.fileno())
    _replace(temporary, path)


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('恢复记录包含重复字段。')
        result[key] = value
    return result


def _journal(transaction, root):
    transaction, root = plain_path(transaction), _root(root)
    if transaction.parent != root or re.fullmatch(r'\.workbox-update-[a-f0-9]{32}', transaction.name) is None:
        raise ValueError('恢复目录不属于当前程序。')
    path = plain_path(transaction / 'journal.json')
    if not path.is_file() or path.stat().st_size > MAX_JOURNAL or path.stat().st_nlink != 1:
        raise ValueError('恢复记录缺失或无效。')
    with path.open(encoding='utf-8') as stream:
        value = json.load(stream, object_pairs_hook=_unique_pairs)
    fields = {'format', 'app_dir', 'app_identity', 'data_dir', 'phase', 'manifest', 'error', 'base_zone_hashes'}
    if not isinstance(value, dict) or set(value) != fields or value['format'] != JOURNAL_FORMAT or value['phase'] not in PHASES:
        raise ValueError('恢复记录格式无效。')
    info = root.stat()
    if value['app_dir'] != str(root) or value['app_identity'] != [info.st_dev, info.st_ino]:
        raise ValueError('程序目录标识与恢复记录不一致。')
    plain_path(value['data_dir'])
    value['manifest'] = validate_manifest(value['manifest'])
    zones = value['base_zone_hashes']
    possible = (set(changed_paths(value['manifest'])) | set(removed_paths(value['manifest']))) & set(value['manifest']['base'])
    if (not isinstance(zones, dict) or not set(zones) <= possible
            or any(marker is not None and (not isinstance(marker, str) or re.fullmatch('[a-f0-9]{64}', marker) is None)
                   for marker in zones.values())):
        raise ValueError('恢复来源标记记录无效。')
    if not isinstance(value['error'], str) or len(value['error']) > 4000:
        raise ValueError('恢复错误记录无效。')
    return value


def has_pending_update(app_dir):
    root = _root(app_dir)
    candidates = [child for child in root.iterdir() if child.name.startswith(TRANSACTION_PREFIX)]
    if len(candidates) > 1000:
        raise RuntimeError('程序更新恢复记录过多，请保留现场检查。')
    for child in candidates:
        try:
            if _journal(child, root)['phase'] not in TERMINAL:
                return True
        except (OSError, ValueError, TypeError, KeyError):
            return True
    return False


def _copy_checked(source, destination, expected):
    source, destination = plain_path(source), plain_path(destination)
    if _record(source) != expected:
        raise ValueError('备份源在更新期间发生变化。')
    destination.parent.mkdir(parents=True, exist_ok=True)
    plain_path(destination.parent)
    def copy_bytes(incoming, zone=None):
        with destination.open('xb') as outgoing:
            shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        if zone is not None:
            _write_zone(destination, zone)
    zone = None
    if os.name == 'nt':
        # 复用现有普通文件句柄租约，保留安全来源标记且拒绝未知数据流。
        from native_files import ZipLease
        with ZipLease(source, movable=False) as lease:
            zone = lease.zone_metadata()
            copy_bytes(lease.stream, zone)
    else:
        with source.open('rb') as incoming:
            copy_bytes(incoming)
    if _record(destination) != expected or _record(source) != expected:
        raise ValueError('旧程序备份未通过校验。')
    return _zone_hash(zone)


def _write_zone(path, value):
    path = plain_path(path)
    with Path(str(path) + ':Zone.Identifier').open('xb') as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())


def _zone_hash(value):
    return hashlib.sha256(value).hexdigest() if value is not None else None


def _zone_bytes(path):
    if os.name != 'nt':
        return None
    from native_files import ZipLease
    with ZipLease(plain_path(path), movable=False) as lease:
        return lease.zone_metadata()


def _origin(manifest):
    if os.name != 'nt':
        return None
    return ('[ZoneTransfer]\nZoneId=3\nHostUrl=https://github.com/232577/workbox/releases/download/'
            + manifest['to_tag'] + '/' + delta_filename(manifest['from_tag'], manifest['to_tag']) + '\n').encode('utf-8')


def _retired(transaction, group, name):
    path = _file(transaction / group / uuid.uuid4().hex, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    return plain_path(path)


def _current(path):
    path = plain_path(path)
    return _record(path) if path.exists() else None


def _rollback(transaction, root, journal):
    manifest = journal['manifest']
    base, target = manifest['base'], manifest['target']
    if journal['phase'] == 'preparing':
        verify_tree(root, base)
    else:
        touched = set(changed_paths(manifest)) | set(removed_paths(manifest))
        # 先核对全部恢复来源和目的，遇到外部改动不得覆盖任何未知字节。
        restore, remove = [], []
        for name in sorted(touched):
            path = _file(root, name)
            current = _current(path)
            old, new = base.get(name), target.get(name)
            if old is not None and current == old:
                continue
            if current is not None and current != new:
                raise ValueError('恢复目的存在未知变更：' + name)
            current_zone = _zone_bytes(path) if current is not None else None
            if current is not None and current_zone != _origin(manifest):
                raise ValueError('恢复目的安全来源标记发生变化：' + name)
            if old is not None:
                backup = _file(transaction / 'backup', name)
                if _record(backup) != old:
                    raise ValueError('旧程序备份缺失或损坏：' + name)
                backup_zone = _zone_bytes(backup)
                if name not in journal['base_zone_hashes'] or _zone_hash(backup_zone) != journal['base_zone_hashes'][name]:
                    raise ValueError('旧程序备份的安全来源标记发生变化：' + name)
                restore.append((backup, path, current, old, backup_zone, current_zone))
            elif current is not None:
                remove.append((path, current, current_zone))
        for name in set(base) - touched:
            if _record(_file(root, name)) != base[name]:
                raise ValueError('未改动的程序文件发生外部变化：' + name)
        journal['phase'] = 'rolling-back'
        _write_json(transaction / 'journal.json', journal)
        def check_root():
            info = plain_path(root).stat()
            if [info.st_dev, info.st_ino] != journal['app_identity']:
                raise ValueError('恢复期间程序目录标识发生变化。')
        for backup, path, current, old, backup_zone, current_zone in restore:
            path.parent.mkdir(parents=True, exist_ok=True)
            plain_path(path.parent)
            def check_restore():
                check_root()
                if _record(backup) != old or _current(path) != current:
                    raise ValueError('恢复预检后程序或备份发生变化，未覆盖。')
            retired = _retired(transaction, 'discarded', path.relative_to(root).as_posix())
            _retry_change(lambda: swap_checked(backup, path, old, current, retired,
                source_zone=backup_zone, destination_zone=current_zone), check_restore)
        for path, current, current_zone in remove:
            def check_remove():
                check_root()
                if _current(path) != current:
                    raise ValueError('恢复预检后新增文件发生变化，未移除。')
            retired = _retired(transaction, 'discarded', path.relative_to(root).as_posix())
            _retry_change(lambda: move_checked(path, retired, current, expected_zone=current_zone), check_remove)
        verify_tree(root, base)
    journal['phase'] = 'rolled-back'
    _write_json(transaction / 'journal.json', journal)
    return {'status': 'rolled-back', 'transaction_dir': str(transaction)}


def recover_update(transaction_dir, app_dir):
    root = _root(app_dir)
    with runtime_guard(root, exclusive=True):
        transaction = plain_path(transaction_dir)
        journal = _journal(transaction, root)
        if journal['phase'] in TERMINAL:
            expected = journal['manifest']['target' if journal['phase'] == 'committed' else 'base']
            verify_tree(root, expected)
            return {'status': journal['phase'], 'transaction_dir': str(transaction)}
        try:
            return _rollback(transaction, root, journal)
        except Exception as error:
            journal.update(phase='rollback-failed', error=str(error)[:4000])
            _write_json(transaction / 'journal.json', journal)
            raise


def _probe_build(app_dir, data_dir, tag):
    # 此入口只读取构建标识；不启动业务服务、不运行数据库迁移。
    output = app_dir / ('.workbox-build-probe-' + uuid.uuid4().hex + '.json')
    environment = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT='1')
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    result = subprocess.run([str(app_dir / 'WorkBox.exe'), '--build-info',
        '--build-info-output=' + str(output), '--data-dir=' + str(data_dir)],
        cwd=app_dir, env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, timeout=30, creationflags=flags)
    if result.returncode or not output.is_file() or output.stat().st_size > 4096:
        raise ValueError('更新后的程序构建探测未通过。')
    with plain_path(output).open(encoding='utf-8') as stream:
        value = json.load(stream)
    if (value.get('version') != '1.0.0' or value.get('release_tag') != tag
            or plain_path(value.get('data_dir', '')) != data_dir):
        raise ValueError('更新后的构建或数据路径与目标不符。')


def apply_update(package, app_dir, *, expected_sha256, expected_size, from_tag, to_tag,
                 data_dir, probe=None, _fault=None):
    root, data_dir = _root(app_dir), plain_path(data_dir)
    with runtime_guard(root, exclusive=True):
        if has_pending_update(root):
            raise RuntimeError('存在尚未解决的程序更新事务，未覆盖现场。')
        manifest = read_manifest(package, expected_sha256=expected_sha256,
            expected_size=expected_size, from_tag=from_tag, to_tag=to_tag)
        verify_tree(root, manifest['base'])
        changed, removed = changed_paths(manifest), removed_paths(manifest)
        backup_names = sorted((set(changed) | set(removed)) & set(manifest['base']))
        required = sum(manifest['target'][p]['size'] for p in changed)
        required += sum(manifest['base'][p]['size'] for p in backup_names) + 64 * 1024 * 1024
        if shutil.disk_usage(root).free < required:
            raise OSError('程序所在磁盘空间不足，未开始增量更新。')
        attempt = uuid.uuid4().hex
        transaction = root / (TRANSACTION_PREFIX + attempt)
        preparation = root / ('.workbox-prepare-' + attempt)
        preparation.mkdir()
        info = root.stat()
        journal = dict(format=JOURNAL_FORMAT, app_dir=str(root), app_identity=[info.st_dev, info.st_ino],
                       data_dir=str(data_dir), phase='preparing', manifest=manifest, error='', base_zone_hashes={})
        # 日志完成前只存在非活动目录；此时没有程序文件变更，崩溃不会误封启动。
        _write_json(preparation / 'journal.json', journal)
        if transaction.exists():
            raise FileExistsError('本次程序更新事务目录已存在，未覆盖。')
        _publish_transaction(preparation, transaction)
        fault = _fault or (lambda phase, name=None: None)
        try:
            staged = transaction / 'staged'
            stage_package(package, staged, expected_sha256=expected_sha256,
                expected_size=expected_size, from_tag=from_tag, to_tag=to_tag)
            if os.name == 'nt':
                origin = _origin(manifest)
                for name in changed:
                    _write_zone(_file(staged, name), origin)
            for name in backup_names:
                journal['base_zone_hashes'][name] = _copy_checked(
                    _file(root, name), _file(transaction / 'backup', name), manifest['base'][name])
            fault('after-backup')
            verify_tree(root, manifest['base'])
            journal['phase'] = 'prepared'
            _write_json(transaction / 'journal.json', journal)
            journal['phase'] = 'applying'
            _write_json(transaction / 'journal.json', journal)
            for name in sorted(changed, key=lambda p: (p in ROOT_FILES, p)):
                destination = _file(root, name)
                if _current(destination) != manifest['base'].get(name):
                    raise ValueError('替换前程序文件发生变化：' + name)
                source = _file(staged, name)
                if _record(source) != manifest['target'][name]:
                    raise ValueError('已准备的增量文件发生变化：' + name)
                destination.parent.mkdir(parents=True, exist_ok=True)
                plain_path(destination.parent)
                old_zone = _zone_bytes(_file(transaction / 'backup', name)) if name in manifest['base'] else None
                if name in manifest['base'] and _zone_hash(old_zone) != journal['base_zone_hashes'][name]:
                    raise ValueError('旧程序备份的安全来源标记发生变化：' + name)
                def check_replace():
                    if (_record(source) != manifest['target'][name]
                            or _current(destination) != manifest['base'].get(name)):
                        raise ValueError('最终替换前程序文件发生变化：' + name)
                retired = _retired(transaction, 'retired', name)
                _retry_change(lambda: swap_checked(source, destination, manifest['target'][name],
                    manifest['base'].get(name), retired, source_zone=_origin(manifest),
                    destination_zone=old_zone), check_replace)
                fault('after-replace', name)
            for name in removed:
                path = _file(root, name)
                def check_remove():
                    if _record(path) != manifest['base'][name]:
                        raise ValueError('移除前程序文件发生变化：' + name)
                old_zone = _zone_bytes(_file(transaction / 'backup', name))
                if _zone_hash(old_zone) != journal['base_zone_hashes'][name]:
                    raise ValueError('旧程序备份的安全来源标记发生变化：' + name)
                retired = _retired(transaction, 'retired', name)
                _retry_change(lambda: move_checked(path, retired, manifest['base'][name],
                    expected_zone=old_zone), check_remove)
                fault('after-remove', name)
            verify_tree(root, manifest['target'])
            journal['phase'] = 'files-installed'
            _write_json(transaction / 'journal.json', journal)
            fault('before-probe')
            if (probe or _probe_build)(root, data_dir, to_tag) is False:
                raise ValueError('更新后的程序构建探测未通过。')
            verify_tree(root, manifest['target'])
            journal['phase'] = 'committed'
            _write_json(transaction / 'journal.json', journal)
            return {'status': 'committed', 'transaction_dir': str(transaction), 'from_tag': from_tag, 'to_tag': to_tag}
        except Exception as error:
            journal['error'] = str(error)[:4000]
            try:
                _rollback(transaction, root, journal)
            except Exception as recovery_error:
                journal.update(phase='rollback-failed', error=str(recovery_error)[:4000])
                _write_json(transaction / 'journal.json', journal)
                raise UpdateApplyError('增量更新失败，恢复遇到冲突；请保留更新记录和备份。',
                    transaction_dir=transaction, rollback_complete=False) from recovery_error
            raise UpdateApplyError('增量更新未完成，旧程序文件已恢复。',
                transaction_dir=transaction, rollback_complete=True) from error
