"""Publish an opted-in copy of an already registered delivery to a folder."""

from __future__ import annotations

import json
import os
from concurrent.futures import CancelledError
from contextlib import ExitStack
from pathlib import Path, PurePosixPath, PureWindowsPath
from uuid import uuid4

from workbox.projects.archive_facts import material_archive_signature
from workbox.storage.managed_archive import segment
from workbox.storage.native_files import ZipLease, cancelled, hash_named_streams, hash_stream, path_is_within
from workbox.storage.workspace_manager import _path, copy_verified


_TARGETS = ('server', 'wechat')
_STALE_DELIVERY = '交付状态已变更，已暂停复制这份材料。'
_SCHEMA = '''
CREATE TABLE IF NOT EXISTS delivery_linkage_settings (target TEXT PRIMARY KEY, root TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS delivery_linkage_plans (
    event_id INTEGER PRIMARY KEY REFERENCES delivery_events(id),
    archive_path TEXT NOT NULL, manifest TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS delivery_linkage_jobs (
    event_id INTEGER NOT NULL REFERENCES delivery_linkage_plans(event_id),
    target TEXT NOT NULL CHECK(target IN ('server','wechat')),
    root TEXT NOT NULL, folder TEXT NOT NULL, token TEXT NOT NULL,
    folder_identity TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','cancelled','failed','completed')),
    message TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(event_id,target)
);
'''


def _directory_identity(directory):
    info = directory.stat()
    if not directory.is_dir() or not info.st_ino:
        raise ValueError('无法核对交付联动目录的身份。')
    return f'{info.st_dev}:{info.st_ino}'


def _relative_path(value):
    parts = value.split('/')
    if any(not part or part in ('.', '..') or part.endswith(('.', ' ')) or PureWindowsPath(part).is_reserved()
           or any(character in part for character in '\\:\0') for part in parts):
        raise ValueError('交付归档包含无法安全复制的文件路径。')
    return PurePosixPath(value)


def _check_file(path, item, leases, cancel_event):
    lease = leases.enter_context(ZipLease(path, movable=False))
    digest, size = hash_stream(lease.stream, cancel_event=cancel_event)
    if (digest, size) != (item['sha256'], item['size']):
        raise ValueError('交付档案或目的地文件已变化，未发布联动副本。')
    expected_streams = item.get('streams', {})
    if expected_streams is not None and hash_named_streams(lease, leases, cancel_event=cancel_event) != expected_streams:
        raise ValueError('交付文件的命名数据流已变化，未发布联动副本。')


class DeliveryLinkage:
    def __init__(self, service):
        self.service = service
        self.db = service.db
        self._lock = self.db._operation_lock
        with self._lock, self.db.connect() as connection:
            connection.executescript(_SCHEMA)

    @staticmethod
    def _settings(connection):
        values = {row['target']: row['root'] for row in connection.execute('SELECT * FROM delivery_linkage_settings')}
        return {target + '_root': values.get(target, '') for target in _TARGETS}

    def settings(self):
        with self.db.connect() as connection:
            return self._settings(connection)

    def _check_root(self, target, value):
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise ValueError('请选择完整的联动目录路径。')
        root = _path(value, allow_network=target == 'server')
        _directory_identity(root)
        workspaces = self.service.workspaces
        protected = {self.db.path.parent, Path(workspaces.settings()['work_root'])}
        protected.update(map(Path, workspaces.archive_roots() + workspaces.history_roots()))
        with self.db.connect() as connection:
            for row in connection.execute('SELECT path,group_path FROM project_workspaces'):
                protected.update(Path(value) for value in row)
        if any(path_is_within(root, path) or path_is_within(path, root) for path in protected):
            raise ValueError('联动目录须与工作箱的数据、项目和归档目录分开。')
        return root

    def configure(self, server_root: str, wechat_root: str):
        with self._lock:
            values = {}
            for target, value in zip(_TARGETS, (server_root, wechat_root), strict=True):
                if not isinstance(value, str):
                    raise ValueError('请选择有效的联动目录。')
                values[target] = str(self._check_root(target, value.strip())) if value.strip() else ''
            server, wechat = values.values()
            if server and wechat and (path_is_within(server, wechat) or path_is_within(wechat, server)):
                raise ValueError('服务器目录和微信待发送目录须分开。')
            with self.db.connect() as connection:
                connection.executemany('INSERT OR REPLACE INTO delivery_linkage_settings VALUES (?,?)', values.items())
                return self._settings(connection)

    @staticmethod
    def _event(connection, project_id, result):
        event_id = result.get('event_id')
        if event_id is not None:
            if type(event_id) is not int or event_id <= 0:
                raise ValueError('交付联动需要有效的本机交付记录。')
            rows = connection.execute('SELECT * FROM delivery_events WHERE id=? AND project_id=?',
                                      (event_id, project_id)).fetchall()
        else:
            rows = connection.execute('''SELECT * FROM delivery_events WHERE project_id=? AND kind='manual-zip'
                AND zip_id=? AND occurred_at=?''', (project_id, result.get('delivered_zip_id'),
                                                   result.get('delivered_at'))).fetchall()
        if len(rows) != 1 or rows[0]['kind'] not in ('manual-files', 'manual-zip'):
            raise ValueError('无法唯一核对本次人工交付的归档记录。')
        return rows[0]

    @staticmethod
    def _artifact(connection, event):
        if event['kind'] == 'manual-files':
            archive = connection.execute('''SELECT manifest FROM project_delivery_archives
                WHERE path=? AND project_id=? AND signature=?''',
                (event['archive_path'], event['project_id'], event['archive_signature'])).fetchone()
            if archive is not None:
                manifest = json.loads(archive['manifest'])
                if manifest and material_archive_signature(manifest) == event['archive_signature']:
                    return manifest
        else:
            archive = connection.execute('SELECT size,sha256 FROM zips WHERE id=?', (event['zip_id'],)).fetchone()
            if archive is not None:
                # ZIP delivery records pin the ZIP bytes. Preserve its Zone.Identifier through copy_verified.
                return [dict(path=Path(event['archive_path']).name, size=archive['size'], sha256=archive['sha256'],
                             streams=None)]
        raise ValueError('本次交付的已验证归档或清单缺失。')

    def _plan(self, project_id, result):
        with self.db.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            event = self._event(connection, project_id, result)
            event_id = event['id']
            needs_plan = connection.execute('SELECT 1 FROM delivery_linkage_plans WHERE event_id=?',
                                            (event_id,)).fetchone() is None
            if needs_plan:
                settings = self._settings(connection)
                manifest = self._artifact(connection, event) if any(settings.values()) else []
                connection.execute('INSERT INTO delivery_linkage_plans VALUES (?,?,?)',
                                   (event_id, event['archive_path'], json.dumps(manifest, ensure_ascii=False)))
                for target in _TARGETS:
                    root = settings[target + '_root']
                    if root:
                        token = uuid4().hex
                        name = segment(event['project_name'], 32)
                        folder = f'WB-{project_id:06d}_D{event_id}_{token[:12]}_{name}'
                        connection.execute('''INSERT INTO delivery_linkage_jobs(event_id,target,root,folder,token)
                            VALUES (?,?,?,?,?)''', (event_id, target, root, folder, token))
            jobs = connection.execute('SELECT * FROM delivery_linkage_jobs WHERE event_id=? ORDER BY target',
                                      (event_id,)).fetchall()
            return event_id, jobs, needs_plan

    @staticmethod
    def _report(row):
        return dict(event_id=row['event_id'], target=row['target'], status=row['status'],
                    path=str(Path(row['root']) / row['folder']), message=row['message'])

    def pending(self):
        with self.db.connect() as connection:
            return [self._report(row) for row in connection.execute('''SELECT * FROM delivery_linkage_jobs
                WHERE status!='completed' ORDER BY event_id,target''')]

    def plan_delivery(self, project_id, result):
        """Remember destinations before the desktop queues a cancellable copy."""
        with self._lock:
            if not any(self.settings().values()):
                return []
            _, jobs, created = self._plan(project_id, result)
            return [self._report(job) for job in jobs] if created else []

    def process_delivery(self, project_id, result, *, cancel_event=None, progress=None):
        if type(project_id) is not int or project_id <= 0 or not isinstance(result, dict):
            raise ValueError('交付联动需要已提交的项目交付结果。')
        with self._lock:
            event_id, jobs, needs_plan = self._plan(project_id, result)
            if not needs_plan:
                return [self._report(job) for job in jobs]
            return [self.retry(event_id, job['target'], cancel_event=cancel_event, progress=progress) for job in jobs]

    @staticmethod
    def _is_current_delivery(connection, event_id):
        return connection.execute('''SELECT 1 FROM delivery_events e JOIN projects p ON p.id=e.project_id
            WHERE e.id=? AND p.status='已交付' AND p.delivered_at=e.occurred_at
                AND p.delivered_zip_id IS e.zip_id
                AND NOT EXISTS (SELECT 1 FROM project_merges m WHERE m.source_id=p.id)
                AND NOT EXISTS (SELECT 1 FROM delivery_events newer
                    WHERE newer.project_id=e.project_id AND newer.occurred_at=e.occurred_at AND newer.id>e.id)
                AND NOT COALESCE((SELECT voided FROM delivery_event_actions WHERE event_id=e.id
                    ORDER BY id DESC LIMIT 1),0)''', (event_id,)).fetchone() is not None

    def retry(self, event_id, target, *, cancel_event=None, progress=None):
        if type(event_id) is not int or event_id <= 0 or target not in _TARGETS:
            raise ValueError('联动记录编号或目的地无效。')
        with self._lock:
            with self.db.connect() as connection:
                row = connection.execute('''SELECT j.*,p.archive_path,p.manifest,e.kind
                    FROM delivery_linkage_jobs j JOIN delivery_linkage_plans p USING(event_id)
                    JOIN delivery_events e ON e.id=j.event_id WHERE j.event_id=? AND j.target=?''',
                    (event_id, target)).fetchone()
                if row is None:
                    raise ValueError('没有对应的已登记联动任务。')
                if row['status'] == 'completed':
                    return self._report(row)
                current_delivery = self._is_current_delivery(connection, event_id)
            try:
                cancelled(cancel_event)
                if not current_delivery:
                    raise ValueError(_STALE_DELIVERY)
                self.service.assert_storage_ready()
                self._publish(row, cancel_event=cancel_event, progress=progress)
                status, message = 'completed', ''
            except CancelledError:
                status, message = 'cancelled', '已取消此次联动，可从交付联动设置重试。'
            except (OSError, RuntimeError, ValueError) as error:
                status, message = 'failed', str(error)
            with self.db.connect() as connection:
                connection.execute('''UPDATE delivery_linkage_jobs SET status=?,message=?
                    WHERE event_id=? AND target=? AND status!='completed' ''', (status, message, event_id, target))
                return self._report(connection.execute('SELECT * FROM delivery_linkage_jobs WHERE event_id=? AND target=?',
                                                       (event_id, target)).fetchone())

    def _publish(self, row, *, cancel_event=None, progress=None):
        root = self._check_root(row['target'], row['root'])
        remote = row['target'] == 'server'
        stage = _path(root / ('.workbox-pending-' + row['token']), allow_network=remote)
        final = _path(root / row['folder'], allow_network=remote)
        manifest = json.loads(row['manifest'])
        files = {str(_relative_path(item['path'])): item for item in manifest}
        if not files or len(manifest) != len({name.casefold() for name in files}):
            raise ValueError('交付归档清单为空或包含重复路径。')
        if final.exists():
            if not row['folder_identity'] or _directory_identity(final) != row['folder_identity']:
                raise FileExistsError('联动目的地已有其他文件夹，未覆盖。')
            self._verify_directory(final, files, remote, cancel_event)
            return
        owned = row['folder_identity']
        if not owned or not stage.exists():
            stage.mkdir()
            owned = _directory_identity(stage)
            with self.db.connect() as connection:
                connection.execute('UPDATE delivery_linkage_jobs SET folder_identity=? WHERE event_id=? AND target=?',
                                   (owned, row['event_id'], row['target']))
        if _directory_identity(stage) != owned:
            raise ValueError('联动准备目录已变化，未覆盖已有文件。')
        archive = _path(row['archive_path'])
        total, done = sum(item['size'] for item in files.values()), 0
        for relative, item in files.items():
            cancelled(cancel_event)
            destination = _path(stage / relative, allow_network=remote)
            if not destination.exists():
                source = _path(archive / relative if row['kind'] == 'manual-files' else archive)
                with ExitStack() as leases:
                    _check_file(source, item, leases, cancel_event)

                    def advance(amount, _size):
                        if progress:
                            progress(done + amount, total)

                    copy_verified(source, destination, cancel_event=cancel_event, progress=advance,
                                  expected_named_streams=item.get('streams', {}), allow_network_destination=remote)
            done += item['size']
        self._verify_directory(stage, files, remote, cancel_event)
        cancelled(cancel_event)
        self._check_root(row['target'], str(root))
        # Serialize publication with delivery corrections without keeping a SQLite
        # write lock while copying or verifying the materials.
        with self.db.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            if not self._is_current_delivery(connection, row['event_id']):
                raise ValueError(_STALE_DELIVERY)
            if _directory_identity(stage) != owned or final.exists():
                raise FileExistsError('联动目录已变化，未覆盖已有文件。')
            cancelled(cancel_event)
            os.rename(stage, final)  # On Windows, same-volume rename never replaces an existing folder.

    @staticmethod
    def _verify_directory(directory, files, remote, cancel_event):
        present = set()

        def read_error(error):
            raise error

        for root, folders, names in os.walk(directory, onerror=read_error):
            cancelled(cancel_event)
            for folder in folders:
                _path(Path(root) / folder, allow_network=remote)
            for name in names:
                path = _path(Path(root) / name, allow_network=remote)
                relative = path.relative_to(directory).as_posix()
                if relative not in files:
                    raise ValueError('联动目录存在清单之外的文件，未覆盖。')
                with ExitStack() as leases:
                    _check_file(path, files[relative], leases, cancel_event)
                present.add(relative)
        if present != files.keys():
            raise ValueError('联动目录的文件不齐全，未发布。')
