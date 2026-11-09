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
CREATE TABLE IF NOT EXISTS document_history_roots (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL COLLATE NOCASE UNIQUE,
    volume_device INTEGER NOT NULL,
    directory_inode TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE IF NOT EXISTS work_file_supersessions (
    old_file_id INTEGER PRIMARY KEY REFERENCES work_files(id),
    new_file_id INTEGER REFERENCES work_files(id),
    operation_id TEXT NOT NULL REFERENCES document_version_commits(operation_id),
    history_root_id INTEGER NOT NULL REFERENCES document_history_roots(id),
    relative_path TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_work_file_supersessions_new ON work_file_supersessions(new_file_id);
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

    @staticmethod
    def _overlap(left, right):
        return left.is_relative_to(right) or right.is_relative_to(left)

    def _history_boundaries(self, connection):
        values = [row[key] for row in connection.execute('SELECT path,group_path FROM project_workspaces')
                  for key in ('path', 'group_path')]
        values.extend(row['path'] for row in connection.execute('SELECT path FROM workflow_archive_roots'))
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ('zips', 'project_delivery_archives', 'project_delivery_archive_attempts'):
            if table in tables:
                values.extend(row['path'] for row in connection.execute(f'SELECT path FROM {table}'))
        return [Path(os.path.abspath(value)) for value in values if value]

    def _history_root(self, connection, root_id):
        row = connection.execute('SELECT * FROM document_history_roots WHERE id=?', (root_id,)).fetchone()
        if row is None:
            raise ValueError('文稿历史区未登记，保留现场。')
        root = plain_path(row['path'])
        info = root.stat()
        if (not stat.S_ISDIR(info.st_mode) or not info.st_ino
                or (info.st_dev, str(info.st_ino)) != (row['volume_device'], row['directory_inode'])):
            raise RuntimeError('文稿历史区身份已变化，保留现场。')
        if any(self._overlap(root, boundary) for boundary in self._history_boundaries(connection)):
            raise ValueError('文稿历史区与项目、分组或归档目录重叠，保留现场。')
        return dict(row)

    def prepare_history(self, project_id, target, incoming_digest, incoming_size):
        """轮换事务之前独立持久化历史根，回滚后仍可验证和恢复文件。"""
        target = plain_path(target)
        with self.db.connect() as connection:
            self.db._assert_projects_editable(connection, project_id)
            workspace = connection.execute('SELECT path FROM project_workspaces WHERE project_id=?', (project_id,)).fetchone()
            old_rows = connection.execute('SELECT * FROM work_files WHERE path=? COLLATE NOCASE', (str(target),)).fetchall()
        if (not workspace or not target.is_relative_to(plain_path(workspace['path']))
                or not old_rows or any(row['project_id'] != project_id for row in old_rows)):
            raise ValueError('同名文档尚未受管或所属项目不一致，请先检查工作目录。')
        with ZipLease(target, movable=False) as old:
            digest = hashlib.file_digest(old.stream, 'sha256').hexdigest()
            if not any((row['sha256'], row['size']) == (digest, old.initial.st_size) for row in old_rows):
                raise RuntimeError('同名旧文档已在外部修改，请先核对后再收录。')
            if (digest, old.initial.st_size) == (incoming_digest, incoming_size):
                return None
            device = old.initial.st_dev
        with self.db.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            for row in connection.execute('SELECT * FROM document_history_roots ORDER BY id'):
                checked = self._history_root(connection, row['id'])
                if checked['volume_device'] == device:
                    return checked
            boundaries = self._history_boundaries(connection)
            parent = target.parent
            while True:
                candidate = parent / 'WorkBox历史文稿'
                if not any(self._overlap(candidate, boundary) for boundary in boundaries):
                    break
                if parent == parent.parent:
                    raise ValueError('找不到与工作目录同卷且独立的文稿历史位置。')
                parent = parent.parent
            if plain_path(parent).stat().st_dev != device:
                raise ValueError('文稿历史区必须与当前文档位于同一磁盘卷。')
            with _parents_locked((candidate,)):
                index = 1
                while True:
                    candidate = plain_path(parent / ('WorkBox历史文稿' + (f' ({index})' if index > 1 else '')))
                    if any(self._overlap(candidate, boundary) for boundary in boundaries):
                        raise ValueError('文稿历史位置与已登记目录冲突。')
                    try:
                        candidate.mkdir()
                        break
                    except FileExistsError:
                        index += 1
                with _parents_locked((candidate / '.registration',)):
                    info = candidate.stat()
                    if not info.st_ino or info.st_dev != device:
                        raise ValueError('无法确认文稿历史区的同卷身份。')
                    root_id = connection.execute('''INSERT INTO document_history_roots
                        (path,volume_device,directory_inode) VALUES (?,?,?)''',
                        (str(candidate), info.st_dev, str(info.st_ino))).lastrowid
                    return self._history_root(connection, root_id)

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
        journal_version = record.get('version', 1)
        kind = record.get('kind', 'rotate')
        if (type(journal_version) is not int or journal_version not in (1, 2)
                or kind not in ('rotate', 'history-only') or (kind != 'rotate' and journal_version != 2)
                or not old.is_relative_to(root) or old == root or target != old
                or old.parent != stage.parent
                or stage.name != '~$workbox-' + operation + '.stage'
                or version == old or version == stage):
            raise ValueError('文档恢复路径越界或冲突。')
        if journal_version == 1 and old.parent != version.parent:
            raise ValueError('旧版文档恢复路径越界。')
        for name in (('old',) if kind == 'history-only' else ('old', 'new')):
            if (not isinstance(record[name + '_identity'], list) or len(record[name + '_identity']) != 4
                    or any(type(value) is not int for value in record[name + '_identity'])
                    or not re.fullmatch('[0-9a-f]{64}', record[name + '_sha256'])):
                raise ValueError('文档恢复文件身份无效。')
        if journal_version == 2:
            relative = Path(record['relative_path'])
            old_ids = record['old_file_ids']
            if (relative.is_absolute() or '..' in relative.parts or not relative.parts
                    or relative.as_posix() != record['relative_path'] or root / relative != old
                    or not isinstance(old_ids, list) or not old_ids
                    or any(type(value) is not int or value < 1 for value in old_ids)
                    or len(set(old_ids)) != len(old_ids) or type(record['history_root_id']) is not int):
                raise ValueError('文档恢复版本关联无效。')
            with self.db.connect() as connection:
                history = self._history_root(connection, record['history_root_id'])
                history_root = plain_path(history['path'])
                expected_parent = history_root / f'WB-{record["project_id"]:06d}' / relative.parent
                pattern = re.escape(old.stem + ' (' + record['old_sha256'][:8] + ')') + r'(?: \([0-9]+\))?' + re.escape(old.suffix)
                if (version.parent != expected_parent or not re.fullmatch(pattern, version.name)
                        or record['history_root_identity'] != [history['volume_device'], int(history['directory_inode'])]
                        or record['old_identity'][0] != history['volume_device']
                        or old.parent.stat().st_dev != history['volume_device']):
                    raise ValueError('文档历史恢复位置或磁盘卷身份无效。')
                committed = connection.execute('SELECT 1 FROM document_version_commits WHERE operation_id=? AND project_id=?',
                                               (operation, record['project_id'])).fetchone() is not None
                for old_id in old_ids:
                    saved = connection.execute('SELECT * FROM work_files WHERE id=?', (old_id,)).fetchone()
                    link = connection.execute('SELECT * FROM work_file_supersessions WHERE old_file_id=?', (old_id,)).fetchone()
                    if (saved is None or saved['project_id'] != record['project_id']
                            or Path(saved['path']) != (version if committed else old)):
                        raise ValueError('文档历史文件登记与恢复记录不一致。')
                    if committed:
                        if (link is None or link['operation_id'] != operation
                                or link['history_root_id'] != history['id']
                                or link['relative_path'] != record['relative_path'] or link['new_file_id'] in old_ids):
                            raise ValueError('文档历史新旧版本关联不一致。')
                        if kind == 'history-only':
                            if link['new_file_id'] is not None:
                                raise ValueError('手动移入历史的文档不能指向新版本。')
                        else:
                            successor = connection.execute('SELECT * FROM work_files WHERE id=?', (link['new_file_id'],)).fetchone()
                            if (successor is None or Path(successor['path']) != target
                                    or successor['project_id'] != record['project_id']
                                    or (successor['sha256'], successor['size']) != (record['new_sha256'], record['new_identity'][2])):
                                raise ValueError('文档当前版本登记与恢复记录不一致。')
                    elif link is not None:
                        raise ValueError('未提交的文档轮换存在历史关联，保留现场。')
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
            self._matches(previous, record['old_identity'], record['old_sha256'])
            if record.get('kind', 'rotate') == 'history-only':
                if target.exists():
                    raise RuntimeError('文档移入历史后原位置又有文件，保留现场。')
            else:
                current = stack.enter_context(ZipLease(target, movable=True))
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
            history_only = record.get('kind', 'rotate') == 'history-only'
            if history_only and (stage in leases or (old in leases and version in leases)):
                raise RuntimeError('手动历史恢复位置有其他文件，保留现场。')
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
        self.history = None
        self.leases = ExitStack()

    def prepare_history(self, target, digest, size):
        self.history = self.owner.prepare_history(self.project_id, target, digest, size)

    def register(self, connection, file_id):
        if self.record is None:
            return
        record = self.record
        for old_id in record['old_file_ids']:
            connection.execute('''INSERT INTO work_file_supersessions
                (old_file_id,new_file_id,operation_id,history_root_id,relative_path) VALUES (?,?,?,?,?)''',
                (old_id, file_id, record['operation_id'], record['history_root_id'], record['relative_path']))

    def _existing_rows(self, connection, file_id):
        selected = connection.execute('SELECT * FROM work_files WHERE id=?', (file_id,)).fetchone()
        workspace = connection.execute('SELECT path FROM project_workspaces WHERE project_id=?', (self.project_id,)).fetchone()
        if selected is None or workspace is None or selected['project_id'] != self.project_id:
            raise ValueError('请选择当前项目已登记的普通工作文件。')
        path = plain_path(selected['path'])
        root = plain_path(workspace['path'])
        if not path.is_relative_to(root) or path == root or path.suffix.casefold() == '.zip':
            raise ValueError('只能将当前工作目录内的普通文稿移入历史，不能移动 ZIP 或归档文件。')
        old_rows = [dict(row) for row in connection.execute('SELECT * FROM work_files WHERE path=? COLLATE NOCASE ORDER BY id', (str(path),))]
        for row in old_rows:
            if (row['project_id'] != self.project_id
                    or connection.execute('SELECT 1 FROM work_file_supersessions WHERE old_file_id=?', (row['id'],)).fetchone()):
                raise ValueError('同路径文件归属或当前版本登记不一致，未移动文稿。')
        return path, root, old_rows

    @staticmethod
    def _verify_existing(lease, old_rows):
        digest = hashlib.file_digest(lease.stream, 'sha256').hexdigest()
        if (identity(os.fstat(lease.stream.fileno())) != identity(lease.initial)
                or any((row['sha256'], row['size']) != (digest, lease.initial.st_size) for row in old_rows)):
            raise RuntimeError('工作文件与登记内容不一致，请先核对后再移入历史。')
        return digest

    def prepare_existing(self, file_id):
        with self.owner.db.connect() as connection:
            self.owner.db._assert_projects_editable(connection, self.project_id)
            path, _, old_rows = self._existing_rows(connection, file_id)
        with ZipLease(path, movable=False) as lease:
            self._verify_existing(lease, old_rows)
        self.prepare_history(path, '', -1)

    def _location(self, connection, old, digest, device):
        if self.history is None:
            raise RuntimeError('同名文档或历史位置在收录前变化，请重新收录。')
        history = self.owner._history_root(connection, self.history['id'])
        if device != history['volume_device']:
            raise ValueError('文稿历史区与旧文档不在同一磁盘卷。')
        workspace = connection.execute('SELECT path FROM project_workspaces WHERE project_id=?', (self.project_id,)).fetchone()
        relative = old.relative_to(plain_path(workspace['path']))
        parent = plain_path(Path(history['path']) / f'WB-{self.project_id:06d}' / relative.parent)
        self.leases.enter_context(_parents_locked((parent / '.history-parent',)))
        parent.mkdir(parents=True, exist_ok=True)
        self.leases.enter_context(_parents_locked((parent / '.history-parent',)))
        index, version = 2, parent / f'{old.stem} ({digest[:8]}){old.suffix}'
        while version.exists() or connection.execute('SELECT 1 FROM work_files WHERE path=? COLLATE NOCASE', (str(version),)).fetchone():
            version = parent / f'{old.stem} ({digest[:8]}) ({index}){old.suffix}'
            index += 1
        return history, relative, version

    def _write_record(self, record):
        owner = self.owner
        self.record = record
        root = plain_path(owner.journal_root)
        root.mkdir(parents=True, exist_ok=True)
        self.leases.enter_context(_parents_locked((owner._journal_path(record),)))
        owner._validate(record, owner._journal_path(record))
        with owner._journal_path(record).open('x', encoding='utf-8', newline='\n') as stream:
            json.dump(record, stream, ensure_ascii=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())

    def archive_existing(self, connection, file_id, *, cancel_event=None):
        cancelled(cancel_event)
        old, _, old_rows = self._existing_rows(connection, file_id)
        self.leases.enter_context(_parents_locked((old,)))
        lease = self.leases.enter_context(ZipLease(old, movable=True))
        digest = self._verify_existing(lease, old_rows)
        history, relative, version = self._location(connection, old, digest, lease.initial.st_dev)
        operation = uuid.uuid4().hex
        record = dict(version=2, kind='history-only', operation_id=operation, project_id=self.project_id,
                      history_root_id=history['id'], history_root_identity=[history['volume_device'], int(history['directory_inode'])],
                      relative_path=relative.as_posix(), old_file_ids=[row['id'] for row in old_rows],
                      old_path=str(old), target_path=str(old), version_path=str(version),
                      stage_path=str(old.parent / ('~$workbox-' + operation + '.stage')),
                      old_identity=identity(lease.initial), old_sha256=digest)
        self._write_record(record)
        cancelled(cancel_event)
        lease.rename_to(version)
        connection.execute('UPDATE work_files SET path=?,filename=? WHERE path=? COLLATE NOCASE',
                           (str(version), version.name, str(old)))
        connection.execute('INSERT INTO document_version_commits(operation_id,project_id) VALUES (?,?)',
                           (operation, self.project_id))
        self.register(connection, None)
        cancelled(cancel_event)
        return dict(project_id=self.project_id, archived_file_ids=record['old_file_ids'], history_path=str(version))

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
        history, relative, version = self._location(connection, old, current, old_lease.initial.st_dev)
        operation_id = uuid.uuid4().hex
        # 复用文档列表及材料交付已有的 Office 临时文件排除约定。
        # 写 prepared 前若进程退出，无日志副本保留但不混入工作材料。
        stage = target.parent / ('~$workbox-' + operation_id + '.stage')
        metadata = copy_file(source, stage, cancel_event=cancel_event, progress=progress)
        # 从这一刻起记录拥有已校验副本；即便后续写日志失败也可按身份清理。
        record = dict(version=2, kind='rotate', operation_id=operation_id, project_id=self.project_id,
                      history_root_id=history['id'], history_root_identity=[history['volume_device'], int(history['directory_inode'])],
                      relative_path=relative.as_posix(), old_file_ids=[row['id'] for row in old_rows],
                      old_path=str(old), target_path=str(target),
                      version_path=str(version), stage_path=str(stage), old_identity=identity(old_lease.initial),
                      old_sha256=current, new_identity=metadata['identity'], new_sha256=metadata['sha256'])
        self._write_record(record)
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
