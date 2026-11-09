"""项目目录变更：预检、保留原件的合并和可恢复的数据库提交边界。"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import stat
import uuid
from contextlib import ExitStack, contextmanager
from pathlib import Path

from native_files import ZipLease, _api, _windows_path, cancelled, plain_path
from workspace_manager import _folder_name, _hash_stream, copy_verified


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _directory_identity(path):
    path = plain_path(path)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or not info.st_ino:
        raise ValueError('项目目录不存在或无法确认目录标识。')
    return [info.st_dev, info.st_ino]


def _overlap(left, right):
    return left == right or left.is_relative_to(right) or right.is_relative_to(left)


class _DirectoryLease:
    """用同一目录句柄核对身份和无覆盖改名，阻止别的进程替换目录。"""

    def __init__(self, path, *, movable=True):
        self.path = plain_path(path)
        self.movable = movable
        self.fd = None

    def __enter__(self):
        import msvcrt
        before = _directory_identity(self.path)
        self.api = _api()
        # READ_ATTRIBUTES | DELETE；允许内容读写，但拒绝其他句柄删除/替换目录。
        handle = self.api.CreateFileW(_windows_path(self.path), 0x10080 if self.movable else 0x80, 3, None, 3,
                                      0x02200000, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            self.fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except BaseException:
            self.api.CloseHandle(handle)
            raise
        info = os.fstat(self.fd)
        self.handle = handle
        self.identity = [info.st_dev, info.st_ino]
        if self.identity != before or not stat.S_ISDIR(info.st_mode):
            os.close(self.fd)
            self.fd = None
            raise RuntimeError('目录在打开时被替换，未执行项目变更。')
        return self

    def __exit__(self, *args):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def rename(self, target):
        if not self.movable:
            raise ValueError('父目录保护句柄不允许移动目录。')
        target = plain_path(target)
        if target.exists() and not (target == self.path and _directory_identity(target) == self.identity):
            raise FileExistsError('目标目录已存在，不覆盖已有内容。')
        from ctypes import wintypes as w
        class RenameInfo(ctypes.Structure):
            _fields_ = [('Flags', w.DWORD), ('RootDirectory', w.HANDLE),
                        ('FileNameLength', w.DWORD), ('FileName', w.WCHAR * 1)]
        encoded = str(target).encode('utf-16-le')
        buffer = ctypes.create_string_buffer(RenameInfo.FileName.offset + len(encoded) + 2)
        info = ctypes.cast(buffer, ctypes.POINTER(RenameInfo)).contents
        info.Flags = 0
        info.RootDirectory = None
        info.FileNameLength = len(encoded)
        ctypes.memmove(ctypes.addressof(buffer) + RenameInfo.FileName.offset, encoded, len(encoded))
        if not self.api.SetFileInformationByHandle(self.handle, 3, buffer, len(buffer)):
            raise ctypes.WinError(ctypes.get_last_error())
        self.path = target

    def remove_empty(self):
        if not self.movable:
            raise ValueError('只读目录保护句柄不能删除目录。')
        disposition = ctypes.c_ubyte(1)
        if not self.api.SetFileInformationByHandle(self.handle, 4, ctypes.byref(disposition), 1):
            raise ctypes.WinError(ctypes.get_last_error())


@contextmanager
def _parents_locked(paths, *, movable_paths=()):
    # 固定所有现存父目录；不能只检查一次路径后任由祖先变成 junction。
    parents = set()
    for raw in paths:
        if not raw:
            continue
        path = plain_path(raw)
        for parent in (path, *path.parents):
            if parent == Path(parent.anchor):
                continue
            if parent.exists():
                parents.add(parent)
    with ExitStack() as stack:
        held = {}
        for parent in sorted(parents, key=lambda p: (len(p.parts), str(p).casefold())):
            held[str(parent).casefold()] = stack.enter_context(_DirectoryLease(parent, movable=parent in movable_paths))
        yield held


class ProjectDirectoryChanges:
    def __init__(self, db, workspaces, lock):
        self.db, self.workspaces, self.lock = db, workspaces, lock
        self.journal_root = db.path.parent / 'project-change-journal'

    @contextmanager
    def _session_owned(self):
        from PySide6.QtCore import QLockFile
        path = plain_path(str(self.db.path) + '.session.lock')
        session = QLockFile(str(path))
        session.setStaleLockTime(0)
        owner_pid, _, _ = session.getLockInfo()
        if owner_pid > 0:
            if owner_pid != os.getpid():
                raise RuntimeError('另一个 WorkBox 进程正在使用数据，不能执行或恢复项目目录变更。')
            # Controller 或外层 execute 已持有本进程会话锁；只能借用，不能释放。
            yield
            return
        if not session.tryLock(0):
            owner_pid, _, _ = session.getLockInfo()
            if owner_pid == os.getpid():
                yield
                return
            raise RuntimeError('另一个进程正在运行或会话锁无法核验，未变更任何目录。')
        try:
            yield
        finally:
            session.unlock()

    def _inventory(self, root, *, cancel_event=None):
        result = []
        for directory, dirs, files in os.walk(root, followlinks=False,
                                               onerror=lambda error: (_ for _ in ()).throw(error)):
            cancelled(cancel_event)
            directory = plain_path(directory)
            for name in sorted(dirs, key=str.casefold):
                cancelled(cancel_event)
                path = plain_path(directory / name)
                result.append(dict(kind='directory', path=path.relative_to(root).as_posix(),
                                   identity=_directory_identity(path)))
            for name in sorted(files, key=str.casefold):
                cancelled(cancel_event)
                path = plain_path(directory / name)
                with ZipLease(path, movable=False) as lease:
                    digest, size = _hash_stream(lease.stream, cancel_event=cancel_event)
                    info = os.fstat(lease.stream.fileno())
                    zone = lease.zone_metadata()
                    result.append(dict(kind='file', path=path.relative_to(root).as_posix(),
                        identity=[info.st_dev, info.st_ino, info.st_mtime_ns], size=size,
                        sha256=digest, zone_sha256=None if zone is None else hashlib.sha256(zone).hexdigest()))
            dirs[:] = sorted(dirs, key=str.casefold)
        return sorted(result, key=lambda item: (item['path'].casefold(), item['path']))

    def _copy_tree(self, old, stage, inventory, *, cancel_event=None, progress=None):
        with ExitStack() as leases:
            # 复制阶段只申请属性读取权；DELETE 访问会妨碍 Windows 发布子文件。
            leases.enter_context(_DirectoryLease(stage, movable=False))
            for item in inventory:
                if item['kind'] == 'directory':
                    leases.enter_context(_DirectoryLease(old / item['path'], movable=False))
            for item in inventory:
                cancelled(cancel_event)
                source, target = old / item['path'], stage / item['path']
                if item['kind'] == 'directory':
                    plain_path(target).mkdir(parents=True, exist_ok=True)
                    leases.enter_context(_DirectoryLease(target, movable=False))
                else:
                    metadata = copy_verified(source, target, cancel_event=cancel_event, progress=progress)
                    if (metadata['size'], metadata['sha256']) != (item['size'], item['sha256']):
                        raise RuntimeError('材料在复制期间变化，合并未提交。')
            if self._inventory(old, cancel_event=cancel_event) != inventory:
                raise RuntimeError('来源材料在合并期间变化，合并未提交。')
            if self._content(self._inventory(stage, cancel_event=cancel_event)) != self._content(inventory):
                raise RuntimeError('合并副本内容核对不一致，合并未提交。')

    @staticmethod
    def _merge_layout(source, target, source_inventory, target_inventory):
        """沿用目标目录的大小写拼写；同名的并入文件编号保留。"""
        occupied = {item['path'].casefold(): item for item in target_inventory}
        assigned = {}
        mapping = {}
        for item in source_inventory:
            relative = item['path']
            key = relative.casefold()
            current = occupied.get(key)
            parent = occupied.get(Path(relative).parent.as_posix().casefold())
            destination = Path(parent['path'] if parent else '.') / Path(relative).name
            if item['kind'] == 'directory':
                if current is not None and current['kind'] != 'directory':
                    raise ValueError('合并目录与目标文件同名，请先整理目录。')
                occupied.setdefault(key, dict(item, path=destination.as_posix()))
                continue
            if current is not None and current['kind'] == 'directory':
                raise ValueError('合并文件与目标目录同名，请先整理目录。')
            if current is not None:
                index = 2
                while True:
                    candidate = destination.with_name(f'{destination.stem} ({index}){destination.suffix}').as_posix()
                    if candidate.casefold() not in occupied:
                        destination = Path(candidate)
                        break
                    index += 1
            final = destination.as_posix()
            occupied[final.casefold()] = dict(item, path=final)
            assigned[relative] = final
            if final != relative:
                mapping[str(source / Path(relative))] = str(target / destination)
        mapping[str(source)] = str(target)
        expected = sorted(({key: value for key, value in item.items() if key != 'identity'}
                           for item in occupied.values()), key=lambda item: (item['path'].casefold(), item['path']))
        return assigned, mapping, expected

    def _copy_merge_stage(self, source, target, stage, plan, *, cancel_event=None, progress=None):
        self._copy_tree(target, stage, plan['target_inventory'], cancel_event=cancel_event, progress=progress)
        for item in plan['inventory']:
            cancelled(cancel_event)
            relative = Path(item['path'])
            if item['kind'] == 'directory':
                (stage / relative).mkdir(exist_ok=True)
                continue
            destination = stage / Path(plan['merge_files'][item['path']])
            metadata = copy_verified(source / relative, destination, cancel_event=cancel_event, progress=progress)
            if (metadata['size'], metadata['sha256']) != (item['size'], item['sha256']):
                raise RuntimeError('并入材料在复制期间变化，合并未提交。')
        if self._content(self._inventory(stage, cancel_event=cancel_event)) != plan['merge_inventory']:
            raise RuntimeError('合并副本材料清单核对不一致，合并未提交。')

    @staticmethod
    def _stage_path(plan, operation_id):
        target = next((row for row in plan['workspaces'] if row['project_id'] == plan['target_id']), None)
        if target is None:
            raise ValueError('合并目标工作目录登记缺失。')
        stage = plain_path(Path(target['path']).parent / ('.workbox-merge-' + operation_id))
        if any(stage.is_relative_to(Path(row['path'])) for row in plan['workspaces']):
            raise ValueError('合并暂存目录不能位于任何已登记项目内部。')
        if any(_overlap(stage, Path(path)) for path in plan['protected_paths']):
            raise ValueError('合并暂存目录不能与历史归档相交。')
        return stage

    @staticmethod
    def _hold_tree(root, inventory, leases, *, include_root=False):
        if include_root:
            leases.enter_context(_DirectoryLease(root, movable=False))
        for item in inventory:
            if item['kind'] == 'directory':
                leases.enter_context(_DirectoryLease(root / item['path'], movable=False))
        for item in inventory:
            if item['kind'] == 'file':
                leases.enter_context(ZipLease(root / item['path'], movable=False))

    def _merge(self, current, record, old, new, commit_callback, result, *, cancel_event=None, progress=None):
        # 所有文件句柄覆盖复制、发布和数据库提交；异常离开本作用域后才能回滚目录。
        with ExitStack() as leases:
            self._hold_tree(old, current['inventory'], leases)
            if self._inventory(old, cancel_event=cancel_event) != current['inventory']:
                raise RuntimeError('来源材料在取得保护句柄前变化，合并未提交。')
            stage = self._stage_path(current, record['operation_id'])
            record['stage_path'] = str(stage)
            self._write(record)
            stage.mkdir()
            record['stage_identity'] = _directory_identity(stage)
            record['state'] = 'copying'
            self._write(record)
            self._copy_merge_stage(old, new, stage, current, cancel_event=cancel_event, progress=progress)
            if self._inventory(new, cancel_event=cancel_event) != current['target_inventory']:
                raise RuntimeError('目标材料在复制期间变化，合并未提交。')
            if self._inventory(old, cancel_event=cancel_event) != current['inventory']:
                raise RuntimeError('来源材料在复制期间变化，合并未提交。')
            backup = plain_path(new.parent / ('.workbox-merge-backup-' + record['operation_id']))
            if backup.exists():
                raise FileExistsError('合并恢复目录已存在，未覆盖。')
            record['backup_path'] = str(backup)
            record['backup_identity'] = current['target_identity']
            self._write(record)
            with _DirectoryLease(new) as target_lease, _DirectoryLease(stage) as stage_lease:
                if target_lease.identity != current['target_identity'] or stage_lease.identity != record['stage_identity']:
                    raise RuntimeError('目标或暂存目录已被替换，未发布合并副本。')
                cancelled(cancel_event)
                target_lease.rename(backup)
                stage_lease.rename(new)
            self._hold_tree(backup, current['target_inventory'], leases, include_root=True)
            self._hold_tree(new, self._inventory(new, cancel_event=cancel_event), leases, include_root=True)
            if self._inventory(backup, cancel_event=cancel_event) != current['target_inventory']:
                raise RuntimeError('原目标材料在发布期间变化，合并未提交。')
            if _directory_identity(new) != record['stage_identity']:
                raise RuntimeError('已发布副本目录被替换，合并未提交。')
            result['published_identity'] = list(record['stage_identity'])
            if self._content(self._inventory(new, cancel_event=cancel_event)) != current['merge_inventory']:
                raise RuntimeError('已发布合并副本内容核对不一致，合并未提交。')
            if self._inventory(old, cancel_event=cancel_event) != current['inventory']:
                raise RuntimeError('来源材料在发布期间变化，合并未提交。')
            record['state'] = 'published'
            self._write(record)
            cancelled(cancel_event)
            return commit_callback(record['operation_id'], current['path_mapping'], result)

    def verify_commit_boundary(self, fs_plan, fs_result, *, cancel_event=None):
        """在 SQL 提交标记之前做最后清单核对；不承诺文件系统目录快照。"""
        cancelled(cancel_event)
        if fs_plan['kind'] != 'merge':
            return
        if any(fs_result.get(key) != fs_plan.get(key) for key in
               ('kind', 'source_id', 'target_id', 'new_name', 'source_path', 'target_path')):
            raise RuntimeError('合并材料提交结果与确认计划不一致。')
        source, published = Path(fs_plan['source_path']), Path(fs_plan['target_path'])
        if _directory_identity(source) != fs_plan['source_identity']:
            raise RuntimeError('来源材料目录在提交前被替换，合并未提交。')
        expected = fs_result.get('published_identity')
        if not expected or _directory_identity(published) != expected:
            raise RuntimeError('已发布副本目录在提交前被替换，合并未提交。')
        if self._inventory(source, cancel_event=cancel_event) != fs_plan['inventory']:
            raise RuntimeError('来源材料清单在提交前变化，合并未提交；新增材料保留在来源目录。')
        if self._content(self._inventory(published, cancel_event=cancel_event)) != fs_plan['merge_inventory']:
            raise RuntimeError('已发布副本清单或内容在提交前变化，合并未提交。')
        backup = Path(published.parent / ('.workbox-merge-backup-' + fs_result['operation_id']))
        if (_directory_identity(backup) != fs_plan['target_identity'] or
                self._inventory(backup, cancel_event=cancel_event) != fs_plan['target_inventory']):
            raise RuntimeError('原目标材料在提交前变化，合并未提交。')
        cancelled(cancel_event)

    def _protected(self, connection):
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        paths = [row[0] for row in connection.execute('SELECT path FROM zips')]
        for table in ('project_delivery_archives', 'project_delivery_archive_attempts'):
            if table in tables:
                paths.extend(row[0] for row in connection.execute(f'SELECT path FROM {table}'))
        paths.extend(self.workspaces.archive_roots())
        return [Path(os.path.abspath(path)) for path in paths if path]

    def _project(self, connection, project_id):
        if type(project_id) is not int or project_id < 1:
            raise ValueError('请选择有效项目。')
        self.db._assert_projects_editable(connection, project_id)
        row = connection.execute('SELECT * FROM projects WHERE id=?', (project_id,)).fetchone()
        if row is None:
            raise KeyError('项目不存在。')
        return dict(row)

    def preview(self, source_id, new_name, target_id=None, *, cancel_event=None):
        cancelled(cancel_event)
        if not isinstance(new_name, str) or not new_name.strip() or len(new_name.strip()) > 200:
            raise ValueError('项目名称需要 1 至 200 个字符。')
        if target_id == source_id:
            raise ValueError('不能将项目合并到自身。')
        with self.lock, self.db.connect() as connection:
            cancelled(cancel_event)
            source = self._project(connection, source_id)
            target = self._project(connection, target_id) if target_id is not None else None
            if target is not None and target['name'] != new_name.strip():
                raise ValueError('目标项目名称已变化，请重新核对。')
            source_workspace = connection.execute('SELECT * FROM project_workspaces WHERE project_id=?', (source_id,)).fetchone()
            target_workspace = connection.execute('SELECT * FROM project_workspaces WHERE project_id=?', (target_id,)).fetchone() if target else None
            if target is not None and (source_workspace is None or target_workspace is None):
                raise ValueError('合并双方都必须有已登记且独立的工作目录。')
            all_workspaces = [dict(row) for row in connection.execute('SELECT * FROM project_workspaces ORDER BY project_id')]
            protected = self._protected(connection)
            plan = dict(version=1, kind='merge' if target else 'rename', source_id=source_id,
                        target_id=target_id, new_name=new_name.strip(), source_path='', target_path='',
                        source_group_path='', target_group_path='', path_mapping={}, inventory=[],
                        target_inventory=[], merge_inventory=[], merge_files={},
                        source_identity=None, parent_identity=None, target_identity=None,
                        source_project=source, target_project=target,
                        workspaces=all_workspaces, protected_paths=[str(path) for path in protected])
            if source_workspace is not None:
                cancelled(cancel_event)
                old = plain_path(source_workspace['path'])
                _directory_identity(old)
                other = plain_path(target_workspace['path']) if target_workspace else None
                if other is not None:
                    _directory_identity(other)
                    if _overlap(old, other):
                        raise ValueError('合并项目目录不能相同或互相包含。')
                    destination = other
                    source_inventory = self._inventory(old, cancel_event=cancel_event)
                    target_inventory = self._inventory(other, cancel_event=cancel_event)
                    merge_files, path_mapping, merge_inventory = self._merge_layout(
                        old, other, source_inventory, target_inventory)
                else:
                    destination = old.parent / _folder_name(new_name.strip())
                    if destination.exists() and destination != old:
                        raise FileExistsError('同名工作目录已存在，请先核对项目和合并方式。')
                    source_inventory = self._inventory(old, cancel_event=cancel_event)
                    target_inventory, merge_files, merge_inventory = [], {}, []
                    path_mapping = {} if str(old) == str(destination) else {str(old): str(destination)}
                plain_path(destination)
                for row in all_workspaces:
                    cancelled(cancel_event)
                    if row['project_id'] in (source_id, target_id):
                        continue
                    registered = Path(os.path.abspath(row['path']))
                    if _overlap(old, registered) or _overlap(destination, registered):
                        raise ValueError('目录与其他已登记项目重叠，未变更任何目录。')
                if any(_overlap(old, path) or _overlap(destination, path) for path in protected):
                    raise ValueError('目录包含历史归档或与归档库重叠，不能移动或合并。')
                parent = destination.parent
                while not parent.exists():
                    cancelled(cancel_event)
                    parent = parent.parent
                plan.update(source_path=str(old), target_path=str(destination),
                    source_group_path=source_workspace['group_path'],
                    target_group_path=target_workspace['group_path'] if target_workspace else
                        (str(destination) if Path(source_workspace['group_path']) == old else source_workspace['group_path']),
                    path_mapping=path_mapping,
                    source_identity=_directory_identity(old), parent_identity=dict(path=str(parent), identity=_directory_identity(parent)),
                    target_identity=None if other is None else _directory_identity(other),
                    inventory=source_inventory, target_inventory=target_inventory,
                    merge_files=merge_files, merge_inventory=merge_inventory)
            cancelled(cancel_event)
            plan['fingerprint'] = hashlib.sha256(_json(plan).encode('utf-8')).hexdigest()
            return plan

    def _read(self, path):
        path = plain_path(path)
        value = json.loads(path.read_text(encoding='utf-8'))
        if value.get('operation_id') != path.stem:
            raise ValueError('目录变更日志标识不一致。')
        return value

    @staticmethod
    def _validate_record(record):
        operation_id = record['operation_id']
        if uuid.UUID(hex=operation_id).hex != operation_id:
            raise ValueError('目录变更日志操作标识无效。')
        plan = dict(record['plan'])
        fingerprint = plan.pop('fingerprint', None)
        if fingerprint != hashlib.sha256(_json(plan).encode('utf-8')).hexdigest():
            raise ValueError('目录变更计划校验不一致，未操作任何目录。')
        if record.get('stage_path'):
            expected = ProjectDirectoryChanges._stage_path(plan, operation_id)
            if plan['kind'] != 'merge' or Path(record['stage_path']) != expected:
                raise ValueError('目录变更暂存路径与计划不一致。')
        if record.get('backup_path'):
            expected = Path(plan['target_path']).parent / ('.workbox-merge-backup-' + operation_id)
            if plan['kind'] != 'merge' or Path(record['backup_path']) != expected:
                raise ValueError('目录变更备份路径与计划不一致。')

    def _write(self, record):
        root = plain_path(self.journal_root)
        root.mkdir(parents=True, exist_ok=True)
        path = plain_path(root / (record['operation_id'] + '.json'))
        if path.exists():
            self._read(path)
        temporary = root / ('.' + uuid.uuid4().hex + '.partial')
        created = None
        try:
            with temporary.open('x', encoding='utf-8', newline='\n') as stream:
                created = os.fstat(stream.fileno())
                stream.write(_json(record))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        except BaseException:
            if created is not None and created.st_ino:
                try:
                    with ZipLease(temporary, movable=True) as owned:
                        # 内容长度会随写入变化；只清理本次创建且未被替换的文件身份。
                        if (owned.initial.st_dev, owned.initial.st_ino) == (created.st_dev, created.st_ino):
                            owned.remove_after_verified_commit()
                except BaseException:
                    # 清理失败保留现场，但不能掩盖原始写入或取消异常。
                    pass
            raise

    def _committed(self, operation_id):
        with self.db.connect() as connection:
            return connection.execute('SELECT 1 FROM project_change_commits WHERE operation_id=?', (operation_id,)).fetchone() is not None

    def _block(self, record, reason):
        with self.db.connect() as connection:
            for project_id in (record['plan']['source_id'], record['plan']['target_id']):
                if project_id is not None:
                    connection.execute('INSERT INTO project_change_blocks(project_id,operation_id,reason) VALUES (?,?,?) ON CONFLICT(project_id) DO UPDATE SET operation_id=excluded.operation_id,reason=excluded.reason',
                                       (project_id, record['operation_id'], reason))

    def _unblock(self, operation_id):
        with self.db.connect() as connection:
            connection.execute('DELETE FROM project_change_blocks WHERE operation_id=?', (operation_id,))

    def _verify_committed(self, record):
        plan = record['plan']
        if not plan['source_path'] or not plan['path_mapping']:
            return
        expected = plan['source_identity'] if plan['kind'] == 'rename' else record.get('stage_identity')
        if not expected or not self._same(Path(plan['target_path']), expected):
            raise RuntimeError('已提交项目的目标目录缺失或被替换，已保护项目且不回滚已提交变更。')

    def _discard_backup(self, record):
        if record['plan']['kind'] != 'merge' or not record.get('backup_path'):
            return
        backup = Path(record['backup_path'])
        if not backup.exists():
            return
        plan = record['plan']
        published = Path(plan['target_path'])
        with _parents_locked([str(backup.parent), str(published.parent)]), ExitStack() as leases:
            root_lease = leases.enter_context(_DirectoryLease(backup))
            if root_lease.identity != plan['target_identity']:
                raise RuntimeError('合并原目标备份目录已变化，未清理。')
            expected = {item['path']: item for item in plan['target_inventory']}
            inventory = self._inventory(backup)
            if any(item != expected.get(item['path']) for item in inventory):
                raise RuntimeError('合并原目标备份内容已变化，未清理。')
            directories = []
            for item in inventory:
                if item['kind'] == 'directory':
                    lease = leases.enter_context(_DirectoryLease(backup / item['path']))
                    if lease.identity != item['identity']:
                        raise RuntimeError('合并原目标备份子目录已变化，未清理。')
                    directories.append(lease)
            self._hold_tree(published, self._inventory(published), leases, include_root=True)
            if self._content(self._inventory(published)) != plan['merge_inventory']:
                raise RuntimeError('合并目标内容已变化，未清理备份。')
            with ExitStack() as file_handles:
                files = []
                for item in inventory:
                    if item['kind'] != 'file':
                        continue
                    lease = file_handles.enter_context(ZipLease(backup / item['path']))
                    info = os.fstat(lease.stream.fileno())
                    digest, size = _hash_stream(lease.stream)
                    zone = lease.zone_metadata()
                    if ([info.st_dev, info.st_ino, info.st_mtime_ns] != item['identity'] or
                            size != item['size'] or digest != item['sha256'] or
                            (None if zone is None else hashlib.sha256(zone).hexdigest()) != item['zone_sha256']):
                        raise RuntimeError('合并原目标备份文件已变化，未清理。')
                    files.append(lease)
                # 只删除已核对并持有同一身份句柄的文件；新增内容不在删除清单内。
                for lease in files:
                    lease.remove_after_verified_commit()
            for lease in sorted(directories, key=lambda entry: len(entry.path.parts), reverse=True):
                lease.remove_empty()
                lease.__exit__()
            root_lease.remove_empty()

    @staticmethod
    def _content(inventory):
        return [{key: value for key, value in item.items() if key != 'identity'} for item in inventory]

    @staticmethod
    def _same(path, expected):
        return path.exists() and _directory_identity(path) == expected

    def _rollback(self, record):
        plan = record['plan']
        if not plan['source_path'] or not plan['path_mapping']:
            return
        old, new = Path(plan['source_path']), Path(plan['target_path'])
        stage = Path(record['stage_path']) if record.get('stage_path') else None
        with _parents_locked([str(old.parent), str(new.parent), str(stage.parent) if stage else '']):
            parent = plan['parent_identity']
            if not self._same(Path(parent['path']), parent['identity']):
                raise RuntimeError('变更父目录标识变化，已保留现场。')
            if plan['kind'] == 'rename':
                if old == new and str(old) != str(new) and self._same(old, plan['source_identity']):
                    with _DirectoryLease(new) as lease:
                        lease.rename(old)
                    return
                if self._same(old, plan['source_identity']) and (str(new) == str(old) or not new.exists()):
                    return
                if old.exists() or not self._same(new, plan['source_identity']):
                    raise RuntimeError('原目录或目标目录标识变化，已保留现场。')
                with _DirectoryLease(new) as lease:
                    lease.rename(old)
            else:
                if not self._same(old, plan['source_identity']):
                    raise RuntimeError('合并来源目录标识变化，已保留现场。')
                expected = record.get('stage_identity')
                if 'backup_path' not in record:
                    # 升级前的日志发布在“合并资料/原项目名”，仍按旧布局恢复。
                    if new.exists():
                        if not expected or not self._same(new, expected) or stage is None or stage.exists():
                            raise RuntimeError('合并副本目录标识变化，已保留现场。')
                        with _DirectoryLease(new) as lease:
                            lease.rename(stage)
                    elif stage is not None and stage.exists():
                        if not expected or not self._same(stage, expected):
                            raise RuntimeError('暂存副本目录标识变化，已保留现场。')
                    elif expected:
                        raise RuntimeError('已登记的暂存副本缺失，已保留现场。')
                    return
                backup = Path(record['backup_path']) if record.get('backup_path') else None
                if new.exists() and expected and self._same(new, expected):
                    if stage is None or stage.exists():
                        raise RuntimeError('合并副本目录标识变化，已保留现场。')
                    with _DirectoryLease(new) as lease:
                        lease.rename(stage)
                elif new.exists() and not self._same(new, plan['target_identity']):
                    raise RuntimeError('合并目标目录标识变化，已保留现场。')
                if backup is not None and backup.exists():
                    if not self._same(backup, plan['target_identity']) or new.exists():
                        raise RuntimeError('原目标目录标识变化，已保留现场。')
                    with _DirectoryLease(backup) as lease:
                        lease.rename(new)
                elif not self._same(new, plan['target_identity']):
                    raise RuntimeError('原目标目录缺失，已保留现场。')
                if stage is not None and stage.exists() and not self._same(stage, expected):
                    raise RuntimeError('暂存副本目录标识变化，已保留现场。')

    def recover(self):
        errors = []
        with self.lock, self._session_owned():
            root = plain_path(self.journal_root)
            if not root.exists():
                return errors
            for path in sorted(root.glob('*.json')):
                record = None
                try:
                    record = self._read(path)
                    self._validate_record(record)
                    if record.get('state') == 'complete':
                        if record.get('error') and record.get('backup_path'):
                            try:
                                self._verify_committed(record)
                                self._discard_backup(record)
                                record['error'] = ''
                                self._write(record)
                            except (OSError, RuntimeError, ValueError):
                                pass
                        continue
                    if record.get('state') == 'rolled_back':
                        continue
                    if self._committed(record['operation_id']):
                        self._verify_committed(record)
                        try:
                            self._discard_backup(record)
                        except (OSError, RuntimeError, ValueError) as cleanup_error:
                            record['error'] = '合并已完成，旧目标备份清理失败：' + str(cleanup_error)
                        else:
                            record['error'] = ''
                        record['state'] = 'complete'
                    else:
                        self._rollback(record)
                        record['state'] = 'rolled_back'
                        record['error'] = ''
                    self._write(record)
                    self._unblock(record['operation_id'])
                except Exception as error:
                    message = str(error)
                    project_ids = []
                    if isinstance(record, dict) and isinstance(record.get('plan'), dict):
                        project_ids = list(dict.fromkeys(pid for pid in
                            (record['plan'].get('source_id'), record['plan'].get('target_id'))
                            if type(pid) is int and pid > 0))
                    global_block = not project_ids
                    if project_ids:
                        try:
                            self._block(record, message)
                        except Exception as block_error:
                            global_block = True
                            message += '；保护项目失败：' + str(block_error)
                    errors.append(dict(path=str(path), error=message,
                                       global_block=global_block, project_ids=project_ids))
        return errors

    def execute(self, fs_plan, commit_callback, *, cancel_event=None, progress=None):
        with self.lock, self._session_owned():
            cancelled(cancel_event)
            if self.recover():
                raise RuntimeError('存在未恢复的目录变更，请先处理保留现场。')
            current = self.preview(fs_plan['source_id'], fs_plan['new_name'], fs_plan.get('target_id'), cancel_event=cancel_event)
            if _json(current) != _json(fs_plan):
                raise ValueError('项目、文件或目录已变化，请重新核对变更。')
            operation_id = uuid.uuid4().hex
            result = dict(operation_id=operation_id, kind=current['kind'], source_id=current['source_id'],
                          target_id=current['target_id'], new_name=current['new_name'],
                          source_path=current['source_path'], target_path=current['target_path'])
            record = dict(operation_id=operation_id, plan=current, state='prepared',
                          stage_path='', stage_identity=None, backup_path='', backup_identity=None, error='')
            self._write(record)
            try:
                if current['source_path'] and current['path_mapping']:
                    old, new = Path(current['source_path']), Path(current['target_path'])
                    roots = [str(old), current['parent_identity']['path']]
                    with _parents_locked(roots, movable_paths=(old,)) as held:
                        # 句柄固定后再核对，防止预览与打开目录之间的替换。
                        if _directory_identity(old) != current['source_identity']:
                            raise RuntimeError('原目录已被替换，未执行项目变更。')
                        parent = current['parent_identity']
                        if _directory_identity(parent['path']) != parent['identity']:
                            raise RuntimeError('目标父目录已被替换，未执行项目变更。')
                        if self._inventory(old, cancel_event=cancel_event) != current['inventory']:
                            raise RuntimeError('材料在执行前发生变化，请重新核对。')
                        if current['kind'] == 'rename':
                            cancelled(cancel_event)
                            held[str(old).casefold()].rename(new)
                            record['state'] = 'published'
                            self._write(record)
                            cancelled(cancel_event)
                            callback_result = commit_callback(operation_id, current['path_mapping'], result)
                        else:
                            callback_result = self._merge(current, record, old, new, commit_callback, result,
                                                          cancel_event=cancel_event, progress=progress)
                else:
                    cancelled(cancel_event)
                    callback_result = commit_callback(operation_id, current['path_mapping'], result)
                if not self._committed(operation_id):
                    raise RuntimeError('数据库未登记变更提交标记，目录变更未确认。')
                try:
                    self._discard_backup(record)
                except (OSError, RuntimeError, ValueError) as cleanup_error:
                    record['error'] = '合并已完成，旧目标备份清理失败：' + str(cleanup_error)
                    result['cleanup_error'] = record['error']
                record['state'] = 'complete'
                self._write(record)
                self._unblock(operation_id)
                result['commit_result'] = callback_result
                return result
            except BaseException as error:
                if self._committed(operation_id):
                    # 提交后异常不得反向搬动目录；保留日志供下一次恢复确认。
                    raise
                try:
                    self._rollback(record)
                    record['state'] = 'rolled_back'
                    record['error'] = str(error)
                    self._write(record)
                    self._unblock(operation_id)
                except Exception as recovery_error:
                    reason = str(recovery_error)
                    self._block(record, reason)
                    raise RuntimeError('目录变更未提交且自动恢复受阻，已保护相关项目：' + reason) from error
                raise
