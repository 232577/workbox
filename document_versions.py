"""普通文档版本轮换：固定文件身份，并跨进程退出恢复提交边界。"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import stat
import uuid
from contextlib import ExitStack, contextmanager
from pathlib import Path

from native_files import ZipLease, _api, _windows_path, cancelled, identity, plain_path


SCHEMA = '''
CREATE TABLE IF NOT EXISTS document_version_commits (
    operation_id TEXT PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id)
);
'''


@contextmanager
def _parents_locked(paths):
    """拒绝祖先目录被改名或换成重解析点；文件内容由 ZipLease 保护。"""
    api = _api()
    parents = set()
    for path in paths:
        for parent in plain_path(path).parents:
            if parent != Path(parent.anchor):
                parents.add(parent)
    handles = []
    try:
        for parent in sorted(parents, key=lambda value: (len(value.parts), str(value).casefold())):
            if not parent.exists():
                continue
            plain_path(parent)
            before = parent.stat()
            if not stat.S_ISDIR(before.st_mode):
                raise ValueError('文档父路径不是普通目录。')
            handle = api.CreateFileW(_windows_path(parent), 0x80, 3, None, 3, 0x02200000, None)
            if handle == ctypes.c_void_p(-1).value:
                raise ctypes.WinError(ctypes.get_last_error())
            handles.append(handle)
            # 句柄禁止目录删除；再次检查路径，拒绝打开期间发生的替换。
            import msvcrt
            duplicate = ctypes.c_void_p()
            api.GetCurrentProcess.restype = ctypes.c_void_p
            process = api.GetCurrentProcess()
            api.DuplicateHandle.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                           ctypes.POINTER(ctypes.c_void_p), ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
            if not api.DuplicateHandle(process, handle, process, ctypes.byref(duplicate), 0, False, 2):
                raise ctypes.WinError(ctypes.get_last_error())
            fd = msvcrt.open_osfhandle(duplicate.value, os.O_RDONLY | os.O_BINARY)
            try:
                opened = os.fstat(fd)
            finally:
                os.close(fd)
            plain_path(parent)
            if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                raise RuntimeError('文档父目录在打开期间被替换。')
        yield
    finally:
        for handle in reversed(handles):
            api.CloseHandle(handle)


class DocumentVersions:
    def __init__(self, db):
        self.db = db
        self.journal_root = db.path.parent / 'document-version-journal'
        self.blocked = {}
        with db.connect() as connection:
            connection.executescript(SCHEMA)

    @contextmanager
    def _session_owned(self):
        from PySide6.QtCore import QLockFile
        session = QLockFile(str(plain_path(str(self.db.path) + '.session.lock')))
        session.setStaleLockTime(0)
        owner, _, _ = session.getLockInfo()
        if owner == os.getpid():
            yield
            return
        if not session.tryLock(0):
            raise RuntimeError('另一个 WorkBox 进程正在使用数据，未执行文档轮换。')
        try:
            yield
        finally:
            session.unlock()

    def _committed(self, record):
        with self.db.connect() as connection:
            return connection.execute('SELECT 1 FROM document_version_commits WHERE operation_id=? AND project_id=?',
                                      (record['operation_id'], record['project_id'])).fetchone() is not None

    def _journal_path(self, record):
        return plain_path(self.journal_root / (record['operation_id'] + '.json'))

    def _validate(self, record, path):
        operation = record['operation_id']
        if not isinstance(operation, str) or not re.fullmatch('[0-9a-f]{32}', operation) or path.name != operation + '.json':
            raise ValueError('文档恢复记录编号无效。')
        if type(record['project_id']) is not int:
            raise ValueError('文档恢复项目编号无效。')
        with self.db.connect() as connection:
            row = connection.execute('SELECT path FROM project_workspaces WHERE project_id=?', (record['project_id'],)).fetchone()
        if not row:
            raise ValueError('文档恢复项目不存在。')
        root = plain_path(row['path'])
        old, target, version, stage = [plain_path(record[key]) for key in ('old_path', 'target_path', 'version_path', 'stage_path')]
        if (not old.is_relative_to(root) or old == root or target != old
                or not (old.parent == version.parent == stage.parent)
                or stage.name != '~$workbox-' + operation + '.stage'
                or version == old or version == stage):
            raise ValueError('文档恢复路径越界或冲突。')
        for name in ('old', 'new'):
            if (not isinstance(record[name + '_identity'], list) or len(record[name + '_identity']) != 4
                    or any(type(value) is not int for value in record[name + '_identity'])
                    or not re.fullmatch('[0-9a-f]{64}', record[name + '_sha256'])):
                raise ValueError('文档恢复文件身份无效。')
        return old, target, version, stage

    @staticmethod
    def _matches(lease, expected_identity, digest):
        if identity(lease.initial) != expected_identity:
            raise RuntimeError('文档身份发生变化，保留恢复现场，请先检查材料。')
        lease.stream.seek(0)
        value = hashlib.file_digest(lease.stream, 'sha256').hexdigest()
        if value != digest or identity(os.fstat(lease.stream.fileno())) != expected_identity:
            raise RuntimeError('文档内容发生变化，保留恢复现场，请先检查材料。')

    def _finish(self, record):
        """提交后只核验，不再将旧版恢复成当前版。"""
        old, target, version, stage = self._validate(record, self._journal_path(record))
        with _parents_locked((target, version, self._journal_path(record))), ExitStack() as stack:
            previous = stack.enter_context(ZipLease(version, movable=True))
            current = stack.enter_context(ZipLease(target, movable=True))
            self._matches(previous, record['old_identity'], record['old_sha256'])
            self._matches(current, record['new_identity'], record['new_sha256'])
            if stage.exists():
                raise RuntimeError('文档提交后仍有暂存文件，保留现场。')
            self._journal_path(record).unlink()

    def _rollback(self, record):
        old, target, version, stage = self._validate(record, self._journal_path(record))
        with _parents_locked((old, version, self._journal_path(record))), ExitStack() as stack:
            # 所有现存对象先固定身份并校验，再执行删除或恢复，避免局部破坏。
            leases = {}
            for path in dict.fromkeys((old, version, stage)):
                if path.exists():
                    leases[path] = stack.enter_context(ZipLease(path, movable=True))
            old_lease = leases.get(version) or leases.get(old)
            if old_lease is None:
                raise RuntimeError('待恢复旧文档缺失，保留现场。')
            self._matches(old_lease, record['old_identity'], record['old_sha256'])
            new_lease = leases.get(stage)
            if old in leases and old_lease is not leases[old]:
                if new_lease is not None:
                    raise RuntimeError('当前与暂存文件同时存在，保留现场。')
                new_lease = leases[old]
            if new_lease is not None:
                self._matches(new_lease, record['new_identity'], record['new_sha256'])
            if old_lease.path == version:
                if new_lease is not None and new_lease.path == target:
                    # 先退回唯一暂存名再恢复；Windows 删除延迟到关句柄，不能直接删除后覆盖。
                    new_lease.rename_to(stage)
                old_lease.rename_to(old)
            if new_lease is not None:
                new_lease.remove_after_verified_commit()
        self._journal_path(record).unlink(missing_ok=True)

    def recover(self):
        with self.db._operation_lock:
            if not self.journal_root.exists():
                self.blocked.clear()
                return []
            plain_path(self.journal_root)
            with self._session_owned():
                self.blocked.clear()
                for path in sorted(self.journal_root.glob('*.json')):
                    project_id = None
                    try:
                        record = json.loads(plain_path(path).read_text(encoding='utf-8'))
                        if not isinstance(record, dict):
                            raise ValueError('文档恢复记录格式无效，请保留现场。')
                        project_id = record.get('project_id')
                        self._validate(record, path)
                        if self._committed(record):
                            self._finish(record)
                        else:
                            self._rollback(record)
                    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
                        self.blocked[project_id if type(project_id) is int else None] = str(error)
                return [dict(project_id=project_id, error=message, message=message, global_block=True)
                        for project_id, message in self.blocked.items()]

    @contextmanager
    def operation(self, project_id):
        with self._session_owned():
            self.recover()
            if self.blocked:
                raise RuntimeError('该项目有未能安全恢复的文档轮换，保留现场，请先检查材料。')
            operation = _Rotation(self, project_id)
            try:
                yield operation
            finally:
                operation.leases.close()
                if operation.record is not None:
                    try:
                        if self._committed(operation.record):
                            self._finish(operation.record)
                        else:
                            self._rollback(operation.record)
                    except BaseException:
                        self.blocked[project_id] = '文档轮换恢复未完成。'
                        raise


class _Rotation:
    def __init__(self, owner, project_id):
        self.owner, self.project_id = owner, project_id
        self.record = None
        self.leases = ExitStack()

    def publish(self, connection, source, target, digest, size, copy_file, *, cancel_event=None, progress=None):
        owner = self.owner
        old_rows = [dict(row) for row in connection.execute('SELECT * FROM work_files WHERE path=? COLLATE NOCASE ORDER BY id', (str(target),))]
        if not old_rows or any(row['project_id'] != self.project_id for row in old_rows):
            raise ValueError('同名文档尚未受管或所属项目不一致，请先检查工作目录。')
        old = plain_path(old_rows[-1]['path'])
        self.leases.enter_context(_parents_locked((old,)))
        old_lease = self.leases.enter_context(ZipLease(old, movable=True))
        current = hashlib.file_digest(old_lease.stream, 'sha256').hexdigest()
        if not any((row['sha256'], row['size']) == (current, old_lease.initial.st_size) for row in old_rows):
            raise RuntimeError('同名旧文档已在外部修改，请先核对后再收录。')
        operation_id = uuid.uuid4().hex
        # 复用文档列表及材料交付已有的 Office 临时文件排除约定。
        # 写 prepared 前若进程退出，无日志副本保留但不混入工作材料。
        stage = target.parent / ('~$workbox-' + operation_id + '.stage')
        index, version = 2, old.with_name(f'{old.stem} ({current[:8]}){old.suffix}')
        while version.exists() or connection.execute('SELECT 1 FROM work_files WHERE path=? COLLATE NOCASE', (str(version),)).fetchone():
            version = old.with_name(f'{old.stem} ({current[:8]}) ({index}){old.suffix}')
            index += 1
        metadata = copy_file(source, stage, cancel_event=cancel_event, progress=progress)
        # 从这一刻起记录拥有已校验副本；即便后续写日志失败也可按身份清理。
        record = dict(operation_id=operation_id, project_id=self.project_id, old_path=str(old), target_path=str(target),
                      version_path=str(version), stage_path=str(stage), old_identity=identity(old_lease.initial),
                      old_sha256=current, new_identity=metadata['identity'], new_sha256=metadata['sha256'])
        self.record = record
        root = plain_path(owner.journal_root)
        root.mkdir(parents=True, exist_ok=True)
        self.leases.enter_context(_parents_locked((owner._journal_path(record),)))
        owner._validate(record, owner._journal_path(record))
        with owner._journal_path(record).open('x', encoding='utf-8', newline='\n') as stream:
            json.dump(record, stream, ensure_ascii=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        if (metadata['sha256'], metadata['size']) != (digest, size):
            raise RuntimeError('收录来源在校验与复制之间发生变化。')
        cancelled(cancel_event)
        new_lease = self.leases.enter_context(ZipLease(stage, movable=True))
        owner._matches(new_lease, record['new_identity'], record['new_sha256'])
        old_lease.rename_to(version)
        new_lease.rename_to(target)
        connection.execute('UPDATE work_files SET path=?,filename=? WHERE path=? COLLATE NOCASE',
                           (str(version), version.name, str(old)))
        connection.execute('INSERT INTO document_version_commits(operation_id,project_id) VALUES (?,?)', (operation_id, self.project_id))
        metadata.update(path=str(target), filename=source.name)
        return metadata
