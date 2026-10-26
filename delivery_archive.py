"""明确人工确认后复制项目材料，逐文件验证，最后原子登记交付。

不移动或执行原件，不生成 ZIP；恢复日志独立于已完成交付档案。
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import threading
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from managed_archive import segment
from native_files import ZipLease, cancelled, identity, plain_path
from workspace_manager import _hash_stream, copy_verified


SCHEMA = '''
CREATE TABLE IF NOT EXISTS project_delivery_archives (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    path TEXT NOT NULL UNIQUE,
    source_root TEXT NOT NULL,
    signature TEXT NOT NULL CHECK(length(signature)=64),
    manifest TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_material_archive_project_signature
    ON project_delivery_archives(project_id,signature);
CREATE TABLE IF NOT EXISTS project_delivery_archive_attempts (
    id TEXT PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    source_root TEXT NOT NULL,
    signature TEXT NOT NULL,
    path TEXT NOT NULL UNIQUE,
    manifest TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'prepared',
    error TEXT NOT NULL DEFAULT '',
    archive_id INTEGER REFERENCES project_delivery_archives(id),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
'''


def _serialized(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _signature(manifest):
    material = [[row['path'], row['sha256']] for row in manifest]
    return hashlib.sha256(_serialized(material).encode('utf-8')).hexdigest()


class ProjectDeliveryArchive:
    def __init__(self, service, workspaces):
        self.service, self.db, self.workspaces = service, service.db, workspaces
        self._lock = getattr(self.db, '_operation_lock', None) or threading.RLock()
        with self._lock, self.db.connect() as connection:
            connection.executescript(SCHEMA)

    def _source(self, project_id):
        with self.db.connect() as connection:
            self.db._assert_projects_editable(connection, project_id)
        workspace = self.workspaces.get_workspace(project_id)
        if not workspace:
            raise ValueError('项目尚未登记工作目录，请先收录文件或创建工作目录。')
        root = plain_path(workspace['path'])
        if not root.is_dir():
            raise ValueError('项目工作目录不存在，未确认交付。')
        excluded = []
        for other in self.workspaces.list_workspaces(include_archived=True):
            if other['project_id'] == project_id:
                continue
            path = Path(os.path.abspath(Path(other['path']).expanduser()))
            if path == root:
                raise ValueError('多个项目登记了同一工作目录，无法确认材料归属。')
            if path.is_relative_to(root):
                excluded.append(path)
        archive_root = plain_path(self.workspaces.settings()['archive_root'])
        if archive_root == root or archive_root.is_relative_to(root) or root.is_relative_to(archive_root):
            raise ValueError('项目工作目录与归档库不能重叠，未确认交付。')
        return root, tuple(sorted(excluded, key=str)), archive_root

    @staticmethod
    def _files(root, excluded=(), *, cancel_event=None):
        root = plain_path(root)
        if not root.is_dir():
            raise ValueError('材料目录不存在，未确认交付。')
        files = []

        def walk_error(error):
            raise error

        for directory, dirs, names in os.walk(root, followlinks=False, onerror=walk_error):
            cancelled(cancel_event)
            plain_path(directory)
            allowed = []
            for name in dirs:
                child = Path(directory) / name
                if any(child == path or child.is_relative_to(path) for path in excluded):
                    continue
                plain_path(child)
                allowed.append(name)
            dirs[:] = sorted(allowed, key=lambda value: (value.casefold(), value))
            for name in names:
                cancelled(cancel_event)
                if name.startswith('~$'):
                    continue
                child = plain_path(Path(directory) / name)
                if not stat.S_ISREG(child.lstat().st_mode):
                    raise ValueError('项目材料中有非常规文件，未确认交付。')
                files.append(child)
        return sorted(files, key=lambda value: (value.relative_to(root).as_posix().casefold(),
                                               value.relative_to(root).as_posix()))

    @classmethod
    def _snapshot(cls, root, excluded=(), *, cancel_event=None):
        manifest, identities = [], {}
        for path in cls._files(root, excluded, cancel_event=cancel_event):
            cancelled(cancel_event)
            with ZipLease(path, movable=False) as lease:
                initial = identity(os.fstat(lease.stream.fileno()))
                sha256, size = _hash_stream(lease.stream, cancel_event=cancel_event)
                if identity(os.fstat(lease.stream.fileno())) != initial or identity(path.stat()) != initial:
                    raise RuntimeError('材料在读取期间发生变化，未确认交付。')
            relative = path.relative_to(root).as_posix()
            manifest.append(dict(path=relative, size=size, sha256=sha256))
            identities[relative] = initial
        return manifest, identities

    def _assert_unchanged(self, project_id, root, excluded, original, *, cancel_event=None):
        current_root, current_excluded, _ = self._source(project_id)
        if current_root != root or current_excluded != excluded:
            raise RuntimeError('项目登记目录或材料归属在归档期间变化，未确认交付。')
        if self._snapshot(root, excluded, cancel_event=cancel_event) != original:
            raise RuntimeError('项目材料在归档期间新增、删除或发生变化，未确认交付。')

    def _verify_archive(self, target, manifest, *, cancel_event=None):
        actual, _ = self._snapshot(target, cancel_event=cancel_event)
        if actual != manifest:
            raise ValueError('材料归档副本缺失、内容变化或存在额外文件；未确认交付，不覆盖旧副本。')

    def _hold_verified_archive(self, leases, target, manifest, *, cancel_event=None):
        """全部副本先加只读租约再核对，租约由调用方持有到数据库提交完成。"""
        expected = [row['path'] for row in manifest]

        def check_paths():
            actual = [path.relative_to(target).as_posix()
                      for path in self._files(target, cancel_event=cancel_event)]
            if actual != expected:
                raise ValueError('材料归档副本文件集合发生变化，未确认交付。')

        check_paths()
        opened = []
        for row in manifest:
            cancelled(cancel_event)
            path = plain_path(target / row['path'])
            lease = leases.enter_context(ZipLease(path, movable=False))
            opened.append((row, path, lease))
        for row, path, lease in opened:
            cancelled(cancel_event)
            actual = _hash_stream(lease.stream, cancel_event=cancel_event)
            if (actual != (row['sha256'], row['size'])
                    or identity(os.fstat(lease.stream.fileno())) != identity(lease.initial)
                    or identity(path.stat()) != identity(lease.initial)):
                raise ValueError('材料归档副本内容发生变化，未确认交付。')
        check_paths()

    def _target(self, archive_root, project_id, signature):
        name = self.db.get_project(project_id)['name']
        parent = plain_path(archive_root / f'WB-{project_id:06d}_{segment(name)}')
        parent.mkdir(parents=True, exist_ok=True)
        plain_path(parent)
        timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        target = plain_path(parent / f'材料交付_{timestamp}_{signature[:8]}_{uuid4().hex[:8]}')
        target.mkdir(exist_ok=False)
        return target

    def _attempt(self, project_id, root, archive_root, signature, manifest):
        with self.db.connect() as connection:
            pending = connection.execute('''SELECT * FROM project_delivery_archive_attempts
                WHERE project_id=? AND source_root=? AND signature=? AND state!='registered'
                ORDER BY created_at,id''', (project_id, str(root), signature)).fetchall()
        for attempt in pending:
            if json.loads(attempt['manifest']) != manifest:
                continue
            target = plain_path(attempt['path'])
            history = getattr(self.workspaces, 'archive_roots', lambda: [])()
            roots = [plain_path(path) for path in [str(archive_root), *history]]
            if not any(target.is_relative_to(path) for path in roots):
                raise ValueError('待恢复材料副本不在已登记归档库，未确认交付。')
            if target.exists():
                if not target.is_dir():
                    raise ValueError('待恢复材料归档位置不是普通目录。')
                return attempt['id'], target
        target = self._target(archive_root, project_id, signature)
        operation = uuid4().hex
        with self.db.connect() as connection:
            connection.execute('''INSERT INTO project_delivery_archive_attempts
                (id,project_id,source_root,signature,path,manifest) VALUES (?,?,?,?,?,?)''',
                (operation, project_id, str(root), signature, str(target), _serialized(manifest)))
        return operation, target

    def _copy(self, root, target, manifest, *, cancel_event=None, progress=None):
        expected = {row['path']: row for row in manifest}
        existing, _ = self._snapshot(target, cancel_event=cancel_event)
        for row in existing:
            if expected.get(row['path']) != row:
                raise ValueError('待恢复材料副本有未知或变化文件，不覆盖或混合归档。')
        present = {row['path'] for row in existing}
        total = sum(row['size'] for row in manifest)
        completed = 0
        for row in manifest:
            cancelled(cancel_event)
            source, destination = plain_path(root / row['path']), plain_path(target / row['path'])
            if row['path'] not in present:
                offset = completed

                def advance(done, _total):
                    if progress:
                        progress(offset + done, total)

                copied = copy_verified(source, destination, cancel_event=cancel_event, progress=advance)
                if (copied['size'], copied['sha256']) != (row['size'], row['sha256']):
                    raise RuntimeError('材料在复制期间变化，未确认交付。')
            completed += row['size']
            if progress:
                progress(completed, total)
        cancelled(cancel_event)

    def confirm(self, project_id, *, confirmed=False, cancel_event=None, progress=None):
        """返回 archive、event_id、duplicate 和当前项目；确认失败不会改变状态。

        archive.manifest 每项为 path（相对 POSIX 路径）、size 和 sha256。
        同内容复用已验证副本；已交付重复确认不新增事件，退待交付后的确认新增事件。
        复制中断或数据库失败保留独立日志；重试同内容时复核并补齐，绝不覆盖已存文件。
        """
        if confirmed is not True:
            raise ValueError('材料交付必须由你明确确认。')
        if type(project_id) is not int or project_id <= 0:
            raise ValueError('项目编号无效。')
        with self._lock:
            cancelled(cancel_event)
            root, excluded, archive_root = self._source(project_id)
            original = self._snapshot(root, excluded, cancel_event=cancel_event)
            manifest = original[0]
            if not manifest:
                raise ValueError('项目工作目录没有可交付文件，不能确认空项目已交付。')
            signature = _signature(manifest)
            with self.db.connect() as connection:
                existing = connection.execute('''SELECT * FROM project_delivery_archives
                    WHERE project_id=? AND signature=? ORDER BY id DESC LIMIT 1''',
                    (project_id, signature)).fetchone()
            operation = None
            if existing:
                target = plain_path(existing['path'])
                self._verify_archive(target, manifest, cancel_event=cancel_event)
            else:
                operation, target = self._attempt(project_id, root, archive_root, signature, manifest)
            try:
                if not existing:
                    self._copy(root, target, manifest, cancel_event=cancel_event, progress=progress)
                    self._verify_archive(target, manifest, cancel_event=cancel_event)
                cancelled(cancel_event)
                # 先完成源材料的最终复核，再锁定并验证全部副本。
                # 数据库上下文先退出提交，ExitStack 后退出，防止验证后副本被改写。
                with ExitStack() as archive_leases, self.db.connect() as connection:
                    connection.execute('BEGIN IMMEDIATE')
                    self.db._assert_projects_editable(connection, project_id)
                    workspace = connection.execute('SELECT path FROM project_workspaces WHERE project_id=?',
                                                   (project_id,)).fetchone()
                    if workspace is None or plain_path(workspace['path']) != root:
                        raise RuntimeError('项目工作目录已变化，未确认交付。')
                    self._assert_unchanged(project_id, root, excluded, original, cancel_event=cancel_event)
                    self._hold_verified_archive(archive_leases, target, manifest, cancel_event=cancel_event)
                    if existing:
                        archive_id = existing['id']
                    else:
                        archive_id = connection.execute('''INSERT INTO project_delivery_archives
                            (project_id,path,source_root,signature,manifest) VALUES (?,?,?,?,?)''',
                            (project_id, str(target), str(root), signature, _serialized(manifest))).lastrowid
                    verified = dict(archive_id=archive_id, path=str(target), source_root=str(root),
                                    signature=signature, manifest=manifest, file_count=len(manifest),
                                    reused=bool(existing))
                    event_id = self.db._complete_file_delivery(connection, project_id, verified)
                    cancelled(cancel_event)
                    if operation:
                        connection.execute('''UPDATE project_delivery_archive_attempts
                            SET state='registered',archive_id=?,error='' WHERE id=?''', (archive_id, operation))
                    record = dict(connection.execute('SELECT * FROM project_delivery_archives WHERE id=?',
                                                     (archive_id,)).fetchone())
                record['manifest'] = json.loads(record['manifest'])
                record['file_count'] = len(record['manifest'])
                return dict(archive=record, event_id=event_id, duplicate=bool(existing),
                            project=self.db.get_project(project_id))
            except BaseException as error:
                if operation:
                    with self.db.connect() as connection:
                        connection.execute('UPDATE project_delivery_archive_attempts SET error=? WHERE id=?',
                                           (str(error), operation))
                raise
