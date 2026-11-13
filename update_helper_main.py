"""独立标准库更新助手；不打开数据库，不从命令行接收可执行命令。"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import uuid

from delta_format import MAX_PACKAGE_SIZE, delta_filename
from incremental_apply import (
    TRANSACTION_PREFIX, TERMINAL, UpdateApplyError, _journal,
    apply_update, recover_update, runtime_guard,
)
from native_files import plain_path

PLAN_FORMAT = 'workbox-incremental-plan-v1'
PLAN_FIELDS = {'format', 'app_dir', 'data_dir', 'parent_pid', 'package',
               'expected_sha256', 'expected_size', 'from_tag', 'to_tag'}
MAX_PLAN = 16 * 1024
USAGE = ('WorkBox 程序更新助手：\n'
         '--plan <incremental-plan.json> 执行已确认的增量更新\n'
         '--recover <事务目录> --app-dir <程序目录> 显式恢复程序文件\n'
         '--self-check 检查助手入口；--noninteractive 仅关闭提示对话框。')


def _local_path(value):
    if isinstance(value, Path):
        value = str(value)
    if (not isinstance(value, str) or not value or not Path(value).is_absolute()
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
            or any(part in {'.', '..'} or part.endswith((' ', '.'))
                   for part in re.split(r'[/\\]', value) if part)):
        raise ValueError('计划路径必须是明确的本机绝对路径，不能含相对段或控制字符。')
    return plain_path(value)


def _regular(path, maximum=None):
    path = plain_path(path)
    info = path.stat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or (maximum is not None and info.st_size > maximum)):
        raise ValueError('更新记录或更新包必须是限额内的单链接普通文件。')
    return info


def _identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_nlink


def _read_bytes(path, maximum):
    before = _regular(path, maximum)
    with path.open('rb') as stream:
        if _identity(os.fstat(stream.fileno())) != _identity(before):
            raise ValueError('读取前更新记录已被替换。')
        content = stream.read(maximum + 1)
        after = os.fstat(stream.fileno())
    if (len(content) > maximum or _identity(before) != _identity(after)
            or _identity(before) != _identity(_regular(path, maximum))):
        raise ValueError('读取期间更新记录发生变化。')
    return content


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('更新计划含重复字段。')
        result[key] = value
    return result


def _read_plan(path, *, recovery_location=False):
    """恢复定位只放宽缺失程序/包及旧 PID 重用，其余计划约束相同。"""
    path = _local_path(path)
    if path.name != 'incremental-plan.json':
        raise ValueError('更新计划文件名必须为 incremental-plan.json。')
    try:
        value = json.loads(_read_bytes(path, MAX_PLAN).decode('utf-8'), object_pairs_hook=_unique_pairs)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError('无法读取 UTF-8 更新计划。') from error
    if type(value) is not dict or set(value) != PLAN_FIELDS or value['format'] != PLAN_FORMAT:
        raise ValueError('更新计划字段或格式不受支持。')
    pid, size, digest = value['parent_pid'], value['expected_size'], value['expected_sha256']
    if (type(pid) is not int or not 0 < pid < 2**32
            or (not recovery_location and pid == os.getpid())):
        raise ValueError('等待的 WorkBox 进程编号无效。')
    if type(size) is not int or not 0 < size <= MAX_PACKAGE_SIZE:
        raise ValueError('增量包长度无效或超过限额。')
    if not isinstance(digest, str) or re.fullmatch(r'[a-f0-9]{64}', digest) is None:
        raise ValueError('增量包 SHA-256 无效。')
    filename = delta_filename(value['from_tag'], value['to_tag'])
    app, data, package = (_local_path(value[key]) for key in ('app_dir', 'data_dir', 'package'))
    if not app.is_dir() or not data.is_dir():
        raise ValueError('原程序目录或数据目录不存在。')
    program = app / 'WorkBox.exe'
    if not recovery_location or program.exists() or program.is_symlink():
        _regular(program)
    if package.parent != path.parent or package.name != filename:
        raise ValueError('增量包必须与计划位于同一目录，并与来源和目标版本的固定文件名一致。')
    if not recovery_location or package.exists() or package.is_symlink():
        if _regular(package, MAX_PACKAGE_SIZE).st_size != size:
            raise ValueError('增量包长度与计划不符。')
    return dict(value, app_dir=app, data_dir=data, package=package)


def load_plan(path):
    """正常更新必须完整校验，不能使用恢复定位的缺失文件例外。"""
    return _read_plan(path)


def wait_for_parent(pid, *, timeout_ms=120000):
    """只等待真实 Win32 进程句柄；权限错误与超时不终止任何进程。"""
    if (os.name != 'nt' or type(pid) is not int or not 0 < pid < 2**32 or pid == os.getpid()
            or type(timeout_ms) is not int or not 0 <= timeout_ms <= 120000):
        raise ValueError('等待进程参数无效，或当前不是 Windows。')
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
    if not handle:
        error = ctypes.get_last_error()
        if error == 87:  # ERROR_INVALID_PARAMETER：该编号已无进程
            return
        raise ctypes.WinError(error)
    try:
        result = kernel.WaitForSingleObject(handle, timeout_ms)
        if result == 0:
            return
        if result == 258:
            raise TimeoutError('WorkBox 在两分钟内尚未退出，未替换程序文件；没有终止原进程。')
        if result == 0xFFFFFFFF:
            raise ctypes.WinError(ctypes.get_last_error())
        raise OSError('无法确认 WorkBox 已安全退出。')
    finally:
        kernel.CloseHandle(handle)


class _Receipt:
    def __init__(self, plan_path, plan):
        self.path = plain_path(plan_path.parent / 'install-result.json')
        parent = self.path.parent.stat()
        self.parent_identity = parent.st_dev, parent.st_ino
        self.value = dict(kind='delta', release_tag=plan['to_tag'],
                          delta_sha256=plan['expected_sha256'], status='helper-started')
        self.content = self._encode(self.value)
        with self.path.open('xb') as stream:
            stream.write(self.content)
            stream.flush()
            os.fsync(stream.fileno())
            created = os.fstat(stream.fileno())
        info = _regular(self.path, MAX_PLAN)
        if (info.st_dev, info.st_ino) != (created.st_dev, created.st_ino):
            raise ValueError('刚创建的安装收据已被替换，未接管未知文件。')
        self.file_identity = _identity(info)
        self.check()

    @staticmethod
    def _encode(value):
        return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n').encode('utf-8')

    def check(self):
        parent = plain_path(self.path.parent).stat()
        if ((parent.st_dev, parent.st_ino) != self.parent_identity
                or _identity(_regular(self.path, MAX_PLAN)) != self.file_identity
                or _read_bytes(self.path, MAX_PLAN) != self.content):
            raise ValueError('安装收据或其目录出现未知变化，未覆盖。')

    def write(self, status, **details):
        self.check()
        value = dict(self.value, status=status, **details)
        content = self._encode(value)
        if len(content) > MAX_PLAN:
            raise ValueError('安装收据超出限额。')
        temporary = plain_path(self.path.parent / ('.install-result-' + uuid.uuid4().hex + '.tmp'))
        owned = None
        try:
            with temporary.open('xb') as stream:
                owned = os.fstat(stream.fileno())
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            self.check()
            current = _regular(temporary, MAX_PLAN)
            if ((current.st_dev, current.st_ino) != (owned.st_dev, owned.st_ino)
                    or _read_bytes(temporary, MAX_PLAN) != content):
                raise ValueError('安装收据临时文件已变化，未替换原记录。')
            os.replace(temporary, self.path)
            info = _regular(self.path, MAX_PLAN)
            if (info.st_dev, info.st_ino) != (owned.st_dev, owned.st_ino):
                raise ValueError('安装收据原子写入后被替换，未接管未知文件。')
            self.content, self.value = content, value
            self.file_identity = _identity(info)
            self.check()
        finally:
            if owned is not None and temporary.exists():
                current = plain_path(temporary).stat()
                if (current.st_dev, current.st_ino) == (owned.st_dev, owned.st_ino):
                    temporary.unlink()


def relaunch(app_dir, data_dir):
    app, data = _local_path(app_dir), _local_path(data_dir)
    if not app.is_dir() or not data.is_dir():
        raise ValueError('程序或原数据目录已变化，未自动启动。')
    program = app / 'WorkBox.exe'
    _regular(program)
    environment = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT='1')
    process = subprocess.Popen([str(program), '--panel', '--data-dir=' + str(data)],
        cwd=app, env=environment, shell=False, close_fds=True,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    if type(process.pid) is not int or process.pid <= 0:
        raise RuntimeError('未取得重新启动程序的进程编号。')
    return process.pid


def _output(value, *, error=False):
    stream = sys.stderr if error else sys.stdout
    if stream is not None:
        try:
            stream.write((json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else str(value)) + '\n')
            stream.flush()
        except (OSError, UnicodeError):
            pass


def _notify(message, *, error=True):
    if os.name != 'nt':
        _output(message, error=error)
        return
    try:
        ctypes.windll.user32.MessageBoxW(None, str(message), 'WorkBox 程序更新', 0x10 if error else 0x40)
    except Exception:
        _output(message, error=error)


def _ask_recovery(transaction):
    text = ('发现一项未完成的程序文件更新。是否尝试恢复旧程序文件？\n'
            '请先关闭 WorkBox 窗口，再确认恢复。\n'
            '恢复前会重新核验记录和备份；不会回滚数据库，也不会自动打开业务。\n'
            '如不确定，请选“否”并保留现场。\n\n事务目录：' + str(transaction))
    return ctypes.windll.user32.MessageBoxW(None, text, 'WorkBox 程序文件恢复', 0x4 | 0x20 | 0x100) == 6


def _failure(message, noninteractive):
    _output(message, error=True)
    if not noninteractive:
        _notify(message)


def _run_plan(path, *, noninteractive):
    result = None
    committed = False
    try:
        plan_path = _local_path(path)
        plan = load_plan(plan_path)
        result = _Receipt(plan_path, plan)
        wait_for_parent(plan['parent_pid'])
        try:
            applied = apply_update(plan['package'], plan['app_dir'],
                expected_sha256=plan['expected_sha256'], expected_size=plan['expected_size'],
                from_tag=plan['from_tag'], to_tag=plan['to_tag'], data_dir=plan['data_dir'])
        except UpdateApplyError as error:
            details = dict(error=str(error)[:2000], transaction_dir=str(error.transaction_dir))
            if not error.rollback_complete:
                result.write('recovery-required', **details)
                _failure('程序更新未完成，文件恢复需要人工处理。请保留事务目录、备份和安装收据；不要重建或回滚数据库。\n' + str(error.transaction_dir), noninteractive)
                return 3
            try:
                result.check()
                details['pid'] = relaunch(plan['app_dir'], plan['data_dir'])
            except (OSError, ValueError, RuntimeError) as restart_error:
                details['error'] += '；旧程序尚未重新打开：' + str(restart_error)[:1000]
            result.write('rolled-back', **details)
            _failure('增量更新未完成，旧程序文件已恢复。数据库未回滚。\n请保留安装收据和恢复记录；若窗口未打开，可从原目录启动 WorkBox。', noninteractive)
            return 2
        if not isinstance(applied, dict) or applied.get('status') != 'committed':
            raise RuntimeError('更新器未确认程序文件已提交，未请求重新启动。')
        committed = True
        result.check()
        pid = relaunch(plan['app_dir'], plan['data_dir'])
        result.write('verified-relaunch-requested', pid=pid)
        _output(result.value)
        return 0
    except Exception as error:
        status = 'installed-restart-needed' if committed else 'failed'
        if result is not None:
            try:
                result.write(status, error=str(error)[:2000])
            except (OSError, ValueError) as receipt_error:
                _output('安装收据未覆盖：' + str(receipt_error), error=True)
        if committed:
            message = ('程序文件已更新，但未能确认重新启动。未回退程序或数据库；请从原目录启动 WorkBox。\n'
                       '请保留安装收据与更新事务。\n' + str(error))
        else:
            message = '程序更新未执行或未完成。请保留更新包、安装收据与恢复记录；现有数据未重置。\n' + str(error)
        _failure(message, noninteractive)
        return 4 if committed else 1


def _scan_pending(app_dir):
    root = _local_path(app_dir)
    with runtime_guard(root):
        candidates = [child for child in root.iterdir() if child.name.startswith(TRANSACTION_PREFIX)]
        if len(candidates) > 1000:
            raise ValueError('程序更新事务过多，请保留现场检查。')
        pending = []
        for child in candidates:
            if _journal(child, root)['phase'] not in TERMINAL:
                pending.append(child)
    return pending


def _verify_recovery_helper(executable, record):
    """缓存副本必须逐字节符合事务中记录的旧版助手，不能按名字信任。"""
    before = _regular(executable, record['size'])
    if before.st_size != record['size']:
        raise ValueError('缓存更新助手与恢复事务的原助手长度不符。')
    digest, count = hashlib.sha256(), 0
    with executable.open('rb') as stream:
        if _identity(os.fstat(stream.fileno())) != _identity(before):
            raise ValueError('缓存更新助手在读取前被替换。')
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            count += len(block)
            if count > record['size']:
                raise ValueError('缓存更新助手在校验期间增长。')
            digest.update(block)
        after = os.fstat(stream.fileno())
    if (_identity(before) != _identity(after)
            or _identity(before) != _identity(_regular(executable, record['size']))
            or count != record['size'] or digest.hexdigest() != record['sha256']):
        raise ValueError('缓存更新助手与恢复事务的原助手摘要不符，未请求恢复。')


def _recovery_location(executable):
    executable = _local_path(executable)
    root = executable.parent
    pending = _scan_pending(root)
    if not pending:
        if re.fullmatch(r'updater-[a-f0-9]{32}\.exe', executable.name) is None:
            raise ValueError('没有唯一可恢复的未完成程序事务。请保留现场，不要猜测恢复位置。')
        plan_path = root / 'incremental-plan.json'
        plan = _read_plan(plan_path, recovery_location=True)
        root = plan['app_dir']
        pending = _scan_pending(root)
        if len(pending) != 1:
            raise ValueError('缓存计划没有对应的唯一未完成事务。请保留现场，不要猜测恢复位置。')
        with runtime_guard(root):
            journal = _journal(pending[0], root)
            manifest = journal['manifest']
            if (journal['phase'] in TERMINAL or manifest['from_tag'] != plan['from_tag']
                    or manifest['to_tag'] != plan['to_tag']):
                raise ValueError('恢复事务的来源或目标版本与缓存计划不符。')
            _verify_recovery_helper(executable, manifest['base']['WorkBoxUpdater.exe'])
            if _journal(pending[0], root) != journal:
                raise ValueError('校验缓存助手期间恢复事务发生变化，未请求恢复。')
    if len(pending) != 1:
        raise ValueError('没有唯一可恢复的未完成程序事务。请保留现场，不要猜测恢复记录。')
    return root, pending[0]


def _recover(transaction, app_dir, *, noninteractive):
    transaction, app_dir = _local_path(transaction), _local_path(app_dir)
    if (transaction.parent != app_dir or not transaction.is_dir()
            or re.fullmatch(r'\.workbox-update-[a-f0-9]{32}', transaction.name) is None):
        raise ValueError('恢复事务不属于指定程序目录。')
    result = recover_update(transaction, app_dir)
    if not isinstance(result, dict) or result.get('status') not in TERMINAL:
        raise RuntimeError('更新器未确认程序文件恢复结果。')
    _output(result)
    if not noninteractive:
        _notify('程序文件已核验。恢复结果：' + result['status'] + '。\n未自动启动业务，未回滚数据库。', error=False)
    return 0


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError('助手参数无效：' + message)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    noninteractive = '--noninteractive' in argv
    try:
        parser = _Parser(add_help=False)
        group = parser.add_mutually_exclusive_group()
        group.add_argument('--plan')
        group.add_argument('--recover')
        group.add_argument('--self-check', action='store_true')
        group.add_argument('--help', '-h', action='store_true')
        parser.add_argument('--app-dir')
        parser.add_argument('--noninteractive', action='store_true')
        args = parser.parse_args(argv)
        if args.app_dir is not None and args.recover is None:
            raise ValueError('--app-dir 只用于显式恢复。')
        if args.self_check:
            return 0
        if args.help:
            _output(USAGE)
            return 0
        if args.plan is not None:
            return _run_plan(args.plan, noninteractive=noninteractive)
        if args.recover is not None:
            if args.app_dir is None:
                raise ValueError('显式恢复必须提供 --app-dir。')
            return _recover(args.recover, args.app_dir, noninteractive=noninteractive)
        if not getattr(sys, 'frozen', False):
            _output(USAGE)
            return 0
        executable = _local_path(Path(sys.executable))
        root, transaction = _recovery_location(executable)
        if noninteractive:
            raise ValueError('自动扫描恢复需要明确选择“是”；非交互模式请使用 --recover。')
        if not _ask_recovery(transaction):
            return 0
        if _recovery_location(executable) != (root, transaction):
            raise ValueError('确认后恢复定位发生变化，请保留现场重新检查。')
        return _recover(transaction, root, noninteractive=False)
    except Exception as error:
        _failure('更新助手未完成操作。如需恢复，请先关闭 WorkBox 窗口，并保留计划、缓存助手及恢复记录，不要删除现场。\n'
                 + str(error), noninteractive)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
