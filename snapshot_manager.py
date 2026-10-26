"""持续观察本地 ZIP 并保留独立副本；不解压、不执行、不删除原件。"""

from __future__ import annotations

import json
import os
import stat
import time
from concurrent.futures import CancelledError
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from zipfile import BadZipFile, ZipFile

from managed_archive import segment
from native_files import ZipLease, cancelled, identity, plain_path
from bundle_analyzer import analyze_bundle, bundle_hint
from zip_content import count_project_files


SCHEMA = '''
CREATE TABLE IF NOT EXISTS zip_sources (
    id INTEGER PRIMARY KEY, source_key TEXT NOT NULL UNIQUE,
    source_path TEXT NOT NULL, project_id INTEGER REFERENCES projects(id),
    last_stat TEXT, stable_since REAL, last_digest TEXT NOT NULL DEFAULT '',
    last_error TEXT NOT NULL DEFAULT '', processed_stat TEXT, failed_stat TEXT,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1))
);
CREATE TABLE IF NOT EXISTS zip_snapshots (
    zip_id INTEGER PRIMARY KEY REFERENCES zips(id),
    source_id INTEGER NOT NULL REFERENCES zip_sources(id), source_path TEXT NOT NULL,
    approved INTEGER NOT NULL DEFAULT 0 CHECK(approved IN (0,1)),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE IF NOT EXISTS zip_snapshot_attempts (
    id TEXT PRIMARY KEY, source_id INTEGER NOT NULL REFERENCES zip_sources(id),
    project_id INTEGER REFERENCES projects(id), target TEXT NOT NULL,
    metadata TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'prepared',
    error TEXT NOT NULL DEFAULT '', zip_id INTEGER REFERENCES zips(id),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE IF NOT EXISTS zip_work_copies (
    source_id INTEGER PRIMARY KEY REFERENCES zip_sources(id),
    original_source_id INTEGER NOT NULL REFERENCES zip_sources(id),
    baseline_identity TEXT NOT NULL,
    baseline_sha256 TEXT NOT NULL
);
'''


class InvalidZipStructure(ValueError):
    """确定的结构错误须等来源变化；临时 I/O 错误仍可重试。"""


DISCOVERY_EXCLUDED_DIRECTORIES = {'.git', 'node_modules', '.venv', 'venv', '__pycache__', '.cache'}


class SnapshotManager:
    def __init__(self, service, workspaces):
        self.service, self.db, self.workspaces = service, service.db, workspaces
        self._lock = service.archive._lock
        self._observed = set()
        with self._lock, self.db.connect() as connection:
            connection.executescript(SCHEMA)
            self.db.migrate_zip_approvals(connection)
            # 单调观察不能跨进程继承：旧时间戳不证明重启期间持续稳定。
            connection.execute('UPDATE zip_sources SET last_stat=NULL,stable_since=NULL')

    def _root(self):
        return plain_path(self.workspaces.settings()['archive_root'])

    def _archive_roots(self):
        history = getattr(self.workspaces, 'archive_roots', None)
        roots = history() if history else []
        return [Path(os.path.abspath(Path(root).expanduser()))
                for root in [*roots, self.workspaces.settings()['archive_root']]]

    @staticmethod
    def _key(path):
        return os.path.normcase(str(path)).casefold()

    def _source(self, value):
        path = plain_path(value)
        info = path.stat()
        if path.suffix.casefold() != '.zip' or not stat.S_ISREG(info.st_mode):
            raise ValueError('请选择本机普通 .zip 文件。')
        if any(path.is_relative_to(root) for root in self._archive_roots()):
            raise ValueError('归档库中的 ZIP 不能再次作为监视来源。')
        with self.db.connect() as connection:
            known_paths = connection.execute('SELECT z.path FROM zips z JOIN zip_snapshots s ON s.zip_id=z.id').fetchall()
        if any(self._key(path) == self._key(Path(os.path.abspath(row['path']))) for row in known_paths):
            raise ValueError('已登记的归档副本不能再次作为监视来源。')
        return path

    def _assert_project(self, connection, *project_ids):
        self.db._assert_projects_editable(connection, *project_ids)

    def watch_zip(self, source, project_id=None):
        with self._lock:
            path = self._source(source)
            with self.db.connect() as connection:
                self._assert_project(connection, project_id)
                row = connection.execute('SELECT * FROM zip_sources WHERE source_key=?', (self._key(path),)).fetchone()
                if row:
                    # 显式收件可以让保留副本成为独立来源；后台发现不调用已有来源。
                    connection.execute('DELETE FROM zip_work_copies WHERE source_id=?', (row['id'],))
                    # 重复拖入不清空连续稳定时间；显式项目选择允许为待关联来源补关联。
                    self._assert_project(connection, row['project_id'])
                    if project_id is not None and row['project_id'] != project_id:
                        connection.execute('UPDATE zip_sources SET project_id=?,processed_stat=NULL,failed_stat=NULL,last_error=? WHERE id=?', (project_id, '', row['id']))
                    project_id = project_id if project_id is not None else row['project_id']
                    connection.execute('UPDATE zip_sources SET enabled=1,source_path=? WHERE id=?', (str(path), row['id']))
                    # 显式再次拖入按新规则重新判断；后台发现不会重复调用已有来源。
                    connection.execute('UPDATE zip_sources SET processed_stat=NULL,failed_stat=NULL WHERE id=?', (row['id'],))
                else:
                    connection.execute('INSERT INTO zip_sources(source_key,source_path,project_id) VALUES (?,?,?)', (self._key(path), str(path), project_id))
            return {'watching': True, 'source_path': str(path), 'project_id': project_id}

    def watch_work_copy(self, source, original_source, project_id=None, *, baseline_sha256):
        """程序生成的未修改副本只保留，不重复参与自动交付判断。"""
        with self._lock:
            path, original = self._source(source), self._source(original_source)
            if path == original:
                return self.watch_zip(path, project_id)
            with self.db.connect() as connection:
                self._assert_project(connection, project_id)
                existing = connection.execute('SELECT * FROM zip_sources WHERE source_key=?', (self._key(path),)).fetchone()
                parent = connection.execute('SELECT id FROM zip_sources WHERE source_key=?', (self._key(original),)).fetchone()
            if parent is None:
                raise ValueError('ZIP 原始来源尚未登记，工作副本未关联。')
            if existing:
                # 已经显式收件或被用户修改的来源不能重新降为保留副本。
                return dict(watching=True, source_path=str(path), project_id=existing['project_id'])
            metadata = self._inspect(path)
            result = self.watch_zip(path, project_id)
            if metadata['sha256'] == baseline_sha256:
                with self.db.connect() as connection:
                    row = connection.execute('SELECT id FROM zip_sources WHERE source_key=?', (self._key(path),)).fetchone()
                    connection.execute('''INSERT INTO zip_work_copies
                        (source_id,original_source_id,baseline_identity,baseline_sha256) VALUES (?,?,?,?)''',
                        (row['id'], parent['id'], json.dumps(metadata['identity']), metadata['sha256']))
            return result

    def list_sources(self):
        with self.db.connect() as connection:
            sources = [dict(row) for row in connection.execute('''SELECT s.*,d.archived_at,
                c.baseline_identity AS copy_baseline_identity,c.baseline_sha256 AS copy_baseline_sha256,
                c.original_source_id FROM zip_sources s
                LEFT JOIN project_details d ON d.project_id=s.project_id
                LEFT JOIN zip_work_copies c ON c.source_id=s.id ORDER BY s.id''')]
        for source in sources:
            if not source['enabled'] or source['archived_at'] is not None:
                source['state'] = 'paused'
            elif source['last_error']:
                source['state'] = 'error'
            elif source['copy_baseline_identity'] is not None:
                source['state'] = 'copy-retained'
            elif source['last_stat'] is not None and source['last_stat'] == source['processed_stat']:
                source['state'] = 'archived'
            else:
                source['state'] = 'pending'
        return sources

    def list_versions(self, project_id=None):
        sql = '''SELECT z.*,s.source_path,s.source_id,
            CASE WHEN b.match_state='needs-review' THEN 0 ELSE EXISTS(
                SELECT 1 FROM zip_project_approvals a WHERE a.zip_id=z.id
                AND (:pid IS NULL OR a.project_id=:pid)) END AS approved,
            (SELECT COUNT(*) FROM zip_project_approvals a WHERE a.zip_id=z.id) AS approved_count,
            (SELECT COUNT(*) FROM zip_project_links l WHERE l.zip_id=z.id) AS project_count,
            b.declared_count AS bundle_count,b.match_state AS bundle_state,b.reason AS bundle_reason,
            s.created_at AS snapshot_created_at,p.name AS project_name,
            CASE WHEN z.id=(SELECT max(z2.id) FROM zips z2 JOIN zip_snapshots s2 ON s2.zip_id=z2.id
                JOIN zip_project_links l2 ON l2.zip_id=z2.id
                WHERE l2.project_id=COALESCE(:pid,z.project_id)) THEN 1 ELSE 0 END AS latest
            FROM zips z JOIN zip_snapshots s ON s.zip_id=z.id
            LEFT JOIN projects p ON p.id=z.project_id
            LEFT JOIN zip_bundles b ON b.zip_id=z.id'''
        with self.db.connect() as connection:
            rows = connection.execute(sql + ''' WHERE :pid IS NULL OR EXISTS(
                SELECT 1 FROM zip_project_links l WHERE l.zip_id=z.id AND l.project_id=:pid)
                ORDER BY z.id DESC''', {'pid': project_id})
            result = [dict(row, zip_id=row['id'], approved=bool(row['approved'])) for row in rows]
        for row in result:
            row['file_counts'] = self.db.get_zip_file_counts(row['id']) if row['bundle_state'] != 'needs-review' else []
        return result

    def is_snapshot(self, zip_id):
        with self.db.connect() as connection:
            return connection.execute('SELECT 1 FROM zip_snapshots WHERE zip_id=?', (zip_id,)).fetchone() is not None

    def _discover(self, cancel_event):
        archive_roots = self._archive_roots()
        for workspace in self.workspaces.list_workspaces(include_archived=False):
            cancelled(cancel_event)
            try:
                root = plain_path(workspace['path'])
                if any(root.is_relative_to(archive_root) for archive_root in archive_roots):
                    continue
                with self.db.connect() as connection:
                    self._assert_project(connection, workspace['project_id'])
            except (OSError, ValueError, KeyError):
                continue
            for directory, dirs, files in os.walk(root, followlinks=False):
                cancelled(cancel_event)
                allowed = []
                for name in dirs:
                    if name.casefold() in DISCOVERY_EXCLUDED_DIRECTORIES:
                        continue
                    try:
                        child = plain_path(Path(directory) / name)
                        if not any(child.is_relative_to(archive_root) for archive_root in archive_roots):
                            allowed.append(name)
                    except (OSError, ValueError):
                        pass
                dirs[:] = allowed
                for name in files:
                    if Path(name).suffix.casefold() == '.zip':
                        try:
                            path = plain_path(Path(directory) / name)
                            with self.db.connect() as connection:
                                known = connection.execute('SELECT 1 FROM zip_sources WHERE source_key=?', (self._key(path),)).fetchone()
                            # 所属目录只给新来源默认归属，不覆盖用户已经选定的项目。
                            if not known:
                                self.watch_zip(path, workspace['project_id'])
                        except (OSError, ValueError, KeyError):
                            pass

    def _recover_pending(self, result, *, cancel_event=None):
        """已发布副本独立恢复；原路径是否仍存在不影响已存内容的登记。"""
        with self.db.connect() as connection:
            attempts = connection.execute("SELECT * FROM zip_snapshot_attempts WHERE state!='registered' ORDER BY created_at,id").fetchall()
        for attempt in attempts:
            cancelled(cancel_event)
            if not Path(attempt['target']).is_file():
                continue
            try:
                with self.db.connect() as connection:
                    source = dict(connection.execute('SELECT * FROM zip_sources WHERE id=?', (attempt['source_id'],)).fetchone())
                    self._assert_project(connection, attempt['project_id'])
                source['project_id'] = attempt['project_id']
                metadata = json.loads(attempt['metadata'])
                with ZipLease(attempt['target'], movable=False):
                    checked = self._inspect(attempt['target'], cancel_event=cancel_event)
                    if (checked['sha256'], checked['size']) != (metadata['sha256'], metadata['size']):
                        raise ValueError('待恢复的归档副本校验不一致；保留文件供检查。')
                    metadata['path'] = attempt['target']
                    record = self._commit(source, attempt['id'], metadata)
                if not any(row['id'] == record['id'] for row in result['archived']):
                    result['archived'].append(record)
                with self.db.connect() as connection:
                    connection.execute('UPDATE zip_sources SET last_error=?,failed_stat=NULL WHERE id=?', ('', source['id']))
            except CancelledError:
                raise
            except Exception as error:
                message = str(error)
                with self.db.connect() as connection:
                    source = connection.execute('SELECT source_path FROM zip_sources WHERE id=?', (attempt['source_id'],)).fetchone()
                    connection.execute('UPDATE zip_snapshot_attempts SET error=? WHERE id=?', (message, attempt['id']))
                    connection.execute('UPDATE zip_sources SET last_error=?,failed_stat=NULL WHERE id=?', (message, attempt['source_id']))
                if message != attempt['error']:
                    result['errors'].append({'source_path': source['source_path'], 'error': message})

    def scan(self, now=None, *, cancel_event=None, progress=None):
        timestamp = time.monotonic() if now is None else float(now)
        result = {'archived': [], 'pending': 0, 'errors': []}
        with self._lock:
            cancelled(cancel_event)
            self._recover_pending(result, cancel_event=cancel_event)
            self._discover(cancel_event)
            for source in self.list_sources():
                cancelled(cancel_event)
                if not source['enabled']:
                    continue
                try:
                    with self.db.connect() as connection:
                        self._assert_project(connection, source['project_id'])
                    path = self._source(source['source_path'])
                    observed_stat = json.dumps(identity(path.stat()))
                    if source['copy_baseline_identity'] is not None:
                        if observed_stat == source['copy_baseline_identity']:
                            if source['last_error']:
                                with self.db.connect() as connection:
                                    connection.execute('UPDATE zip_sources SET last_error=?,failed_stat=NULL WHERE id=?',
                                                       ('', source['id']))
                            continue
                        # 时间戳或文件属性变化不等于内容更新；实际字节变化才独立监视。
                        from workspace_manager import _hash_stream
                        with ZipLease(path, movable=False) as lease:
                            digest, _ = _hash_stream(lease.stream, cancel_event=cancel_event)
                            observed_stat = json.dumps(identity(os.fstat(lease.stream.fileno())))
                        with self.db.connect() as connection:
                            if digest == source['copy_baseline_sha256']:
                                connection.execute('UPDATE zip_work_copies SET baseline_identity=? WHERE source_id=?',
                                                   (observed_stat, source['id']))
                                connection.execute('UPDATE zip_sources SET last_error=? WHERE id=?', ('', source['id']))
                                continue
                            connection.execute('DELETE FROM zip_work_copies WHERE source_id=?', (source['id'],))
                    if source['id'] not in self._observed or observed_stat != source['last_stat']:
                        self._observed.add(source['id'])
                        with self.db.connect() as connection:
                            connection.execute('UPDATE zip_sources SET last_stat=?,stable_since=?,last_error=?,failed_stat=NULL WHERE id=?', (observed_stat, timestamp, '', source['id']))
                        result['pending'] += 1
                        continue
                    if timestamp - source['stable_since'] < float(self.workspaces.settings().get('stable_seconds', 10)):
                        result['pending'] += 1
                        continue
                    if observed_stat in (source['processed_stat'], source['failed_stat']):
                        if observed_stat == source['processed_stat'] and source['last_error']:
                            with self.db.connect() as connection:
                                connection.execute('UPDATE zip_sources SET last_error=? WHERE id=?', ('', source['id']))
                        continue
                    archived = self._archive_source(source, expected_stat=observed_stat, cancel_event=cancel_event, progress=progress)
                    with self.db.connect() as connection:
                        connection.execute('UPDATE zip_sources SET processed_stat=?,last_digest=?,last_error=?,failed_stat=NULL WHERE id=?', (observed_stat, archived['zip']['sha256'], '', source['id']))
                    if not any(row['id'] == archived['zip']['id'] for row in result['archived']):
                        result['archived'].append(archived['zip'])
                except CancelledError:
                    raise
                except Exception as error:
                    message = str(error)
                    if message != source['last_error']:
                        result['errors'].append({'source_path': source['source_path'], 'error': message})
                    with self.db.connect() as connection:
                        failed_stat = source['last_stat'] if isinstance(error, InvalidZipStructure) else None
                        connection.execute('UPDATE zip_sources SET last_error=?,failed_stat=? WHERE id=?', (message, failed_stat, source['id']))
            return result

    @staticmethod
    def _inspect(path, *, cancel_event=None, progress=None):
        with ZipLease(path, movable=False) as lease:
            try:
                metadata = lease.inspect(cancel_event=cancel_event, progress=progress)
                # 只检查目录结构；不解压或宣称通过 CRC/成品验收。
                lease.stream.seek(0)
                with ZipFile(lease.stream) as archive:
                    archive.infolist()
                return metadata
            except (BadZipFile, ValueError, EOFError) as error:
                raise InvalidZipStructure(str(error)) from error

    def _target(self, metadata, project_id):
        if project_id is None:
            folder = '待关联'
        else:
            project = self.service.get_project(project_id)
            folder = f"WB-{project_id:06d}_{segment(project['name'])}"
        directory = plain_path(self._root() / folder)
        directory.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        version = plain_path(directory / f"{timestamp}_{metadata['sha256'][:8]}_{uuid4().hex[:8]}")
        version.mkdir(exist_ok=False)
        filename = metadata['filename']
        if Path(filename).name != filename:
            raise ValueError('压缩包名称不是普通文件名，未归档。')
        return plain_path(version / filename)

    def _commit(self, source, operation, metadata):
        with ZipLease(metadata['path'], movable=False):
            checked = self._inspect(metadata['path'])
            if (checked['sha256'], checked['size']) != (metadata['sha256'], metadata['size']):
                raise ValueError('归档副本在登记前已变化，未更新项目状态。')
            return self._commit_leased(source, operation, metadata)

    def _counts(self, path, primary, plan):
        if plan['is_bundle']:
            return (count_project_files(path, self.service.list_projects(archived=None), plan['project_ids'], bundle_plan=plan)
                    if plan['state'] == 'matched' else {})
        return count_project_files(path, self.service.list_projects(archived=None), [primary]) if primary is not None else {}

    def _commit_leased(self, source, operation, metadata):
        # 恢复时也按当前项目核对已发布副本，不能盲用旧的匹配计划。
        plan = analyze_bundle(metadata['path'], self.service.list_projects(archived=None), filename=metadata['filename'])
        primary = source['project_id']
        if plan['is_bundle']:
            primary = primary if primary in plan['project_ids'] else (plan['project_ids'][0] if plan['project_ids'] else None)
        counts = self._counts(metadata['path'], primary, plan)
        with self.db.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            self._assert_project(connection, primary)
            zip_id = connection.execute('INSERT INTO zips(project_id,path,filename,size,sha256,mtime_ns) VALUES (?,?,?,?,?,?)', (primary, metadata['path'], metadata['filename'], metadata['size'], metadata['sha256'], metadata['mtime_ns'])).lastrowid
            connection.execute('INSERT INTO zip_snapshots(zip_id,source_id,source_path) VALUES (?,?,?)', (zip_id, source['id'], source['source_path']))
            connection.execute("UPDATE zip_snapshot_attempts SET state='registered',zip_id=?,error='' WHERE id=?", (zip_id, operation))
            if plan['is_bundle']:
                self.db._apply_zip_bundle(connection, zip_id, plan)
                connection.execute('UPDATE zip_sources SET project_id=? WHERE id=?', (primary, source['id']))
            if counts:
                self.db._apply_zip_file_counts(connection, zip_id, counts)
            return dict(connection.execute('SELECT * FROM zips WHERE id=?', (zip_id,)).fetchone())

    def _archive_source(self, source, *, expected_stat=None, cancel_event=None, progress=None):
        # 同一只读租约贯穿检查、复制、登记，封住两次读取之间的替换窗口。
        path = self._source(source['source_path'])
        with ZipLease(path, movable=False):
            return self._archive_leased_source(source, expected_stat=expected_stat,
                cancel_event=cancel_event, progress=progress)

    def _archive_leased_source(self, source, *, expected_stat=None, cancel_event=None, progress=None):
        from workspace_manager import copy_verified
        cancelled(cancel_event)
        with self.db.connect() as connection:
            self._assert_project(connection, source['project_id'])
        info = self._inspect(self._source(source['source_path']), cancel_event=cancel_event, progress=progress)
        if expected_stat is not None and json.dumps(info['identity']) != expected_stat:
            raise ValueError('来源在稳定观察后发生变化，请等待重新稳定。')
        plan = analyze_bundle(source['source_path'], self.service.list_projects(archived=None), filename=info['filename'])
        info['bundle'] = plan
        if plan['is_bundle']:
            source = dict(source)
            source['project_id'] = source['project_id'] if source['project_id'] in plan['project_ids'] else (plan['project_ids'][0] if plan['project_ids'] else None)
        with self.db.connect() as connection:
            existing = connection.execute('''SELECT z.* FROM zips z JOIN zip_snapshots s ON s.zip_id=z.id
                WHERE z.sha256=? AND z.filename=? AND (z.project_id IS ? OR (? AND s.source_id=?)) ORDER BY z.id''',
                (info['sha256'], info['filename'], source['project_id'], plan['is_bundle'], source['id'])).fetchall()
            attempts = connection.execute("SELECT * FROM zip_snapshot_attempts WHERE source_id=? AND project_id IS ? AND state!='registered' ORDER BY created_at", (source['id'], source['project_id'])).fetchall()
        for row in existing:
            # 历史已改名副本保留原位；新的显式收录另存原名版本，不改旧引用。
            if Path(row['path']).name != info['filename']:
                continue
            with ZipLease(row['path'], movable=False):
                checked = self._inspect(row['path'], cancel_event=cancel_event)
                if checked['sha256'] != row['sha256']:
                    raise ValueError('已有归档副本已改变，请先检查；原件和记录均保留。')
                counts = self._counts(row['path'], source['project_id'], plan)
                old_plan = self.db.get_zip_bundle(row['id'])
                with self.db.connect() as connection:
                    connection.execute('BEGIN IMMEDIATE')
                    if plan['is_bundle'] and (not old_plan or old_plan['state'] != plan['state']
                            or set(old_plan['project_ids']) != set(plan['project_ids'])):
                        updated = self.db._apply_zip_bundle(connection, row['id'], plan)
                        connection.execute('UPDATE zip_sources SET project_id=? WHERE id=?', (updated['project_id'], source['id']))
                    if counts:
                        self.db._apply_zip_file_counts(connection, row['id'], counts)
            row = self.db.get_zip(row['id'])
            return {'zip': dict(row), 'source_preserved': True, 'duplicate': True}
        # 复制后数据库提交失败时，下一次只复核同目标，不生成第二个副本。
        for attempt in attempts:
            expected = json.loads(attempt['metadata'])
            if expected['sha256'] == info['sha256'] and Path(attempt['target']).is_file():
                checked = self._inspect(attempt['target'], cancel_event=cancel_event)
                if checked['sha256'] != expected['sha256']:
                    raise ValueError('待恢复的归档副本校验不一致；保留文件供检查。')
                expected['path'] = attempt['target']
                record = self._commit(source, attempt['id'], expected)
                return {'zip': record, 'source_preserved': True, 'duplicate': False}
        target = self._target(info, source['project_id'])
        operation = uuid4().hex
        with self.db.connect() as connection:
            connection.execute('INSERT INTO zip_snapshot_attempts(id,source_id,project_id,target,metadata) VALUES (?,?,?,?,?)', (operation, source['id'], source['project_id'], str(target), json.dumps(info)))
        try:
            metadata = copy_verified(source['source_path'], target, cancel_event=cancel_event, progress=progress, require_zip=True)
            if metadata['sha256'] != info['sha256']:
                raise ValueError('来源在归档准备期间变化，请等待重新稳定。')
            metadata['filename'] = info['filename']
            metadata['bundle'] = plan
            with self.db.connect() as connection:
                connection.execute("UPDATE zip_snapshot_attempts SET state='copied',metadata=? WHERE id=?", (json.dumps(metadata), operation))
            record = self._commit(source, operation, metadata)
            return {'zip': record, 'source_preserved': True, 'duplicate': False}
        except BaseException as error:
            with self.db.connect() as connection:
                connection.execute('UPDATE zip_snapshot_attempts SET error=? WHERE id=?', (str(error), operation))
            raise

    def archive_now(self, source, project_id=None, *, cancel_event=None, progress=None):
        with self._lock:
            watched = self.watch_zip(source, project_id)
            with self.db.connect() as connection:
                row = dict(connection.execute('SELECT * FROM zip_sources WHERE source_key=?', (self._key(Path(watched['source_path'])),)).fetchone())
            with ZipLease(row['source_path'], movable=False) as lease:
                result = self._archive_source(row, cancel_event=cancel_event, progress=progress)
                observed_stat = json.dumps(identity(os.fstat(lease.stream.fileno())))
                with self.db.connect() as connection:
                    connection.execute('UPDATE zip_sources SET processed_stat=?,last_digest=?,last_error=? WHERE id=?', (observed_stat, result['zip']['sha256'], '', row['id']))
                return result

    def approve_version(self, zip_id):
        with self._lock:
            if not self.is_snapshot(zip_id):
                raise ValueError('请选择新的 ZIP 归档版本。')
            record = self.db.get_zip(zip_id)
            if record['project_id'] is None:
                raise ValueError('请先关联项目，再标记可用版本。')
            with ZipLease(record['path'], movable=False):
                info = self._inspect(record['path'])
                if info['sha256'] != record['sha256'] or info['size'] != record['size']:
                    raise ValueError('归档副本已改变，不能标记为可用。')
                with self.db.connect() as connection:
                    connection.execute('BEGIN IMMEDIATE')
                    self.db._approve_zip_for_projects(connection, zip_id)
                    connection.execute('UPDATE zip_snapshots SET approved=1 WHERE zip_id=?', (zip_id,))

    def recognize_bundle(self, zip_id, *, batch_id=None, cancel_event=None, progress=None):
        """用原始文件名和归档内容重新识别；不复制、改名或搬动文件。"""
        with self._lock:
            record = self.db.get_zip(zip_id)
            with ZipLease(record['path'], movable=False):
                checked = self._inspect(record['path'], cancel_event=cancel_event, progress=progress)
                if checked['sha256'] != record['sha256'] or checked['size'] != record['size']:
                    raise ValueError('归档副本内容已变化，未修改任何项目状态。')
                plan = analyze_bundle(record['path'], self.service.list_projects(batch_id=batch_id, archived=None), filename=record['filename'])
                if not plan['is_bundle']:
                    raise ValueError('此文件不是“首项目名-数量[-G].zip”格式的多项目包。')
                counts = self._counts(record['path'], record['project_id'], plan)
                cancelled(cancel_event)
                with self.db.connect() as connection:
                    connection.execute('BEGIN IMMEDIATE')
                    updated = self.db._apply_zip_bundle(connection, zip_id, plan)
                    if plan['state'] == 'matched':
                        self.db._apply_zip_file_counts(connection, zip_id, counts)
                        connection.execute('''UPDATE zip_sources SET project_id=? WHERE id IN
                            (SELECT source_id FROM zip_snapshots WHERE zip_id=?)''', (updated['project_id'], zip_id))
                return {'zip': updated, 'bundle': self.db.get_zip_bundle(zip_id), 'source_preserved': True}

    def link_zip(self, zip_id, project_id):
        with self._lock:
            record = self.db.get_zip(zip_id)
            with ZipLease(record['path'], movable=False):
                checked = self._inspect(record['path'])
                if (checked['sha256'], checked['size']) != (record['sha256'], record['size']):
                    raise ValueError('归档副本已变化，未关联或更新项目状态。')
                counts = count_project_files(record['path'], self.service.list_projects(archived=None), [project_id])
                return self._link_counted_zip(zip_id, project_id, counts)

    def _link_counted_zip(self, zip_id, project_id, counts):
        with self._lock, self.db.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute('SELECT z.*,s.source_id FROM zips z JOIN zip_snapshots s ON s.zip_id=z.id WHERE z.id=?', (zip_id,)).fetchone()
            if row is None:
                raise ValueError('此记录不是新的 ZIP 归档版本。')
            bundle = connection.execute('SELECT declared_count FROM zip_bundles WHERE zip_id=?', (zip_id,)).fetchone()
            if bundle and bundle['declared_count'] > 1:
                raise ValueError('这是多项目包，请使用“识别包内项目”，不能只改成一个项目。')
            source = connection.execute('SELECT * FROM zip_sources WHERE id=?', (row['source_id'],)).fetchone()
            self._assert_project(connection, row['project_id'], project_id, source['project_id'])
            if connection.execute('SELECT 1 FROM projects WHERE delivered_zip_id=?', (zip_id,)).fetchone() and row['project_id'] != project_id:
                raise ValueError('已确认交付的 ZIP 不可重新关联。')
            if row['project_id'] == project_id:
                self.db._apply_zip_file_counts(connection, zip_id, counts)
                return
            connection.execute('UPDATE zips SET project_id=? WHERE id=?', (project_id, zip_id))
            connection.execute('DELETE FROM zip_bundle_members WHERE zip_id=?', (zip_id,))
            connection.execute('DELETE FROM zip_bundles WHERE zip_id=?', (zip_id,))
            connection.execute('DELETE FROM zip_project_approvals WHERE zip_id=?', (zip_id,))
            connection.execute('UPDATE zip_snapshots SET approved=0 WHERE zip_id=?', (zip_id,))
            connection.execute('UPDATE zip_sources SET project_id=?,processed_stat=NULL WHERE id=?', (project_id, row['source_id']))
            self.db._apply_zip_file_counts(connection, zip_id, counts)
