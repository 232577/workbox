"""Explicit managed moves with a durable journal and no overwrite of prior ZIPs."""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from uuid import uuid4

from native_files import ZipLease, cancelled, plain_path
from zip_manager import match_projects


JOURNAL = '''
CREATE TABLE IF NOT EXISTS managed_transfers (
    id TEXT PRIMARY KEY, source TEXT NOT NULL, destination TEXT NOT NULL,
    metadata TEXT NOT NULL, project_id INTEGER, zip_id INTEGER,
    method TEXT NOT NULL DEFAULT '', state TEXT NOT NULL DEFAULT 'prepared',
    error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS managed_zips (
    zip_id INTEGER PRIMARY KEY REFERENCES zips(id), source_path TEXT NOT NULL,
    transfer_id TEXT NOT NULL REFERENCES managed_transfers(id)
);
'''


def segment(name, maximum=48):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(name)).strip(' .')[:maximum].rstrip(' .')
    if not value or value.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(10)), *(f'LPT{i}' for i in range(10))}:
        value = '_' + value
    return value


class ManagedArchive:
    def __init__(self, service, root):
        self.service = service
        self.db = service.db
        self.root = plain_path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.db._operation_lock = self._lock
        with self.db.connect() as connection:
            connection.executescript(JOURNAL)

    def _target(self, info, project_id):
        if project_id is None:
            directory = self.root / '待关联'
        else:
            project = self.service.get_project(project_id)
            directory = self.root / f"B{project['batch_id']:04d}_{segment(project['batch_name'])}"
            directory /= f"WB-{project_id:06d}_{segment(project['name'])}"
        plain_path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        name = segment(Path(info['filename']).stem, 64)
        target = directory / f"{name}__{info['sha256'][:12]}.zip"
        return plain_path(target)

    def _update(self, operation, **values):
        allowed = {'state', 'error', 'method', 'zip_id'}
        if not set(values) <= allowed:
            raise ValueError('Invalid journal fields')
        with self.db.connect() as connection:
            connection.execute('UPDATE managed_transfers SET ' + ','.join(f'{key}=?' for key in values) + ' WHERE id=?', [*values.values(), operation])

    def _prepare(self, source, target, info, project_id, zip_id):
        operation = uuid4().hex
        with self.db.connect() as connection:
            connection.execute('INSERT INTO managed_transfers(id,source,destination,metadata,project_id,zip_id) VALUES (?,?,?,?,?,?)',
                               (operation, str(source), str(target), json.dumps(info), project_id, zip_id))
        return operation

    def _commit_record(self, operation, info, project_id, zip_id=None):
        with self.db.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            self.db._assert_projects_editable(connection, project_id)
            op = connection.execute('SELECT * FROM managed_transfers WHERE id=?', (operation,)).fetchone()
            if zip_id is None:
                row = connection.execute('SELECT id FROM zips WHERE path=? COLLATE NOCASE AND sha256=?', (info['path'], info['sha256'])).fetchone()
                zip_id = row[0] if row else None
            if zip_id is None:
                zip_id = connection.execute('INSERT INTO zips(project_id,path,filename,size,sha256,mtime_ns) VALUES (?,?,?,?,?,?)',
                    (project_id, info['path'], info['filename'], info['size'], info['sha256'], info['mtime_ns'])).lastrowid
            else:
                row = connection.execute('SELECT * FROM zips WHERE id=?', (zip_id,)).fetchone()
                if not row or row['sha256'] != info['sha256']:
                    raise ValueError('原 ZIP 记录已改变。')
                bundle = connection.execute('SELECT declared_count FROM zip_bundles WHERE zip_id=?', (zip_id,)).fetchone()
                if bundle and bundle['declared_count'] > 1 and (row['project_id'] != project_id or row['path'] != info['path']):
                    raise ValueError('多项目归档包不能通过旧移动流程更改路径或项目。')
                self.db._assert_projects_editable(connection, row['project_id'])
                if row['project_id'] != project_id and connection.execute('SELECT 1 FROM projects WHERE delivered_zip_id=?', (zip_id,)).fetchone():
                    raise ValueError('已确认交付的文件不能改关联。')
                connection.execute('UPDATE zips SET path=?,mtime_ns=?,project_id=? WHERE id=?', (info['path'], info['mtime_ns'], project_id, zip_id))
            connection.execute('INSERT INTO managed_zips(zip_id,source_path,transfer_id) VALUES (?,?,?) ON CONFLICT(zip_id) DO UPDATE SET transfer_id=excluded.transfer_id', (zip_id, op['source'], operation))
            merged = json.loads(op['metadata']).get('_merge_zip_id')
            if merged is not None and merged != zip_id:
                original = connection.execute('SELECT * FROM zips WHERE id=?', (merged,)).fetchone()
                if original is not None:
                    self.db._assert_projects_editable(connection, original['project_id'])
                    if original['sha256'] != info['sha256'] or original['path'].casefold() != op['source'].casefold():
                        raise ValueError('待合并的原 ZIP 记录已改变。')
                    if connection.execute('SELECT 1 FROM projects WHERE delivered_zip_id=?', (merged,)).fetchone():
                        raise ValueError('原记录用于已确认交付，不合并或删除该记录。')
                    connection.execute('DELETE FROM managed_zips WHERE zip_id=?', (merged,))
                    connection.execute('DELETE FROM zips WHERE id=?', (merged,))
            if project_id is not None:
                connection.execute("UPDATE projects SET status='待交付',updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=? AND status!='已交付'", (project_id,))
            connection.execute("UPDATE managed_transfers SET state='registered',zip_id=? WHERE id=?", (zip_id, operation))
            return dict(connection.execute('SELECT * FROM zips WHERE id=?', (zip_id,)).fetchone())

    def _existing(self, info, project_id):
        with self.db.connect() as connection:
            rows = connection.execute('SELECT z.* FROM zips z JOIN managed_zips m ON m.zip_id=z.id WHERE z.sha256=? AND z.project_id IS ? ORDER BY z.id', (info['sha256'], project_id)).fetchall()
            return next((dict(row) for row in rows if Path(row['path']).is_file() and Path(row['path']).is_relative_to(self.root)), None)

    def import_zip(self, path, *, project_id=None, cancel_event=None, progress=None, _zip_id=None):
        with self._lock:
            cancelled(cancel_event)
            source = plain_path(path)
            if source.suffix.casefold() != '.zip':
                raise ValueError('只接收本地 ZIP 文件。')
            with self.db.connect() as connection:
                current = connection.execute('SELECT z.* FROM zips z JOIN managed_zips m ON m.zip_id=z.id WHERE z.path=? COLLATE NOCASE', (str(source),)).fetchone()
                bundle = connection.execute('''SELECT 1 FROM zips z JOIN zip_bundles b ON b.zip_id=z.id
                    WHERE (z.path=? COLLATE NOCASE OR z.id=?) AND b.declared_count>1''', (str(source), _zip_id)).fetchone()
                if bundle:
                    raise ValueError('多项目归档包不能进入旧移动收件流程，请按整包重新识别。')
                original = connection.execute('SELECT project_id FROM zips WHERE id=?', (_zip_id,)).fetchone() if _zip_id is not None else None
                self.db._assert_projects_editable(connection, project_id,
                    current['project_id'] if current else None, original['project_id'] if original else None)
            if current and _zip_id is None:
                with ZipLease(source, movable=False) as lease:
                    checked = lease.inspect(cancel_event=cancel_event)
                if checked['sha256'] != current['sha256']:
                    raise ValueError('托管文件已被外部修改；未删除或覆盖任何文件。')
                return {'zip': dict(current), 'candidates': [], 'duplicate': True, 'moved': False, 'method': 'already-managed'}
            with ZipLease(source) as lease:
                info = lease.inspect(cancel_event=cancel_event, progress=progress)
                candidates = match_projects(source.name, self.service.list_projects())
                if project_id is None and len(candidates) == 1:
                    project_id = candidates[0]['id']
                if project_id is not None:
                    self.service.get_project(project_id)
                    with self.db.connect() as connection:
                        self.db._assert_projects_editable(connection, project_id)
                existing = self._existing(info, project_id)
                if existing and existing['id'] == _zip_id:
                    existing = None
                if existing:
                    with ZipLease(existing['path'], movable=False) as destination:
                        if lease.zone_metadata() != destination.zone_metadata():
                            existing = None
                target = Path(existing['path']) if existing else self._target(info, project_id)
                if source == target:
                    raise ValueError('文件已在目标位置，请通过现有记录管理。')
                if not existing and target.exists():
                    # Do not reuse untracked files or trust a short digest collision.
                    target = target.with_name(target.stem + '_' + uuid4().hex[:8] + '.zip')
                zip_id = existing['id'] if existing else _zip_id
                if existing:
                    with self.db.connect() as connection:
                        original = connection.execute('SELECT id FROM zips WHERE path=? COLLATE NOCASE AND sha256=?', (str(source), info['sha256'])).fetchone()
                    if original and original[0] != zip_id:
                        info['_merge_zip_id'] = original[0]
                if zip_id is None:
                    with self.db.connect() as connection:
                        old = connection.execute('SELECT id FROM zips WHERE path=? COLLATE NOCASE AND sha256=?', (str(source), info['sha256'])).fetchone()
                        zip_id = old[0] if old else None
                # 历史引用也可能由本次明确投递接管；移动前核对原所属项目。
                with self.db.connect() as connection:
                    for previous_id in (zip_id, info.get('_merge_zip_id')):
                        previous = connection.execute('SELECT project_id FROM zips WHERE id=?', (previous_id,)).fetchone()
                        if previous:
                            self.db._assert_projects_editable(connection, previous['project_id'])
                operation = self._prepare(source, target, info, project_id, zip_id)
                method = 'rename'
                try:
                    cancelled(cancel_event)
                    if existing:
                        with ZipLease(target, movable=False) as destination:
                            verified = destination.inspect(cancel_event=cancel_event)
                            self._assert_content(info, verified)
                            self._update(operation, method='deduplicated', state='placed')
                            record = self._commit_record(operation, {**verified, 'filename': info['filename']}, project_id, zip_id)
                            lease.remove_after_verified_commit()
                            method = 'deduplicated'
                    else:
                        try:
                            lease.rename_to(target)
                        except OSError as error:
                            if getattr(error, 'winerror', None) != 17:
                                raise
                            record = self._copy_commit(lease, target, info, operation, project_id, zip_id, cancel_event, progress)
                            method = 'verified-copy-delete'
                        else:
                            self._update(operation, method='rename', state='placed')
                            verified = lease.inspect()  # move is the commit boundary; finish journal, don't abandon mid-move
                            self._assert_content(info, verified)
                            record = self._commit_record(operation, {**verified, 'filename': info['filename']}, project_id, zip_id)
                    self._update(operation, state='complete', error='')
                except BaseException as error:
                    self._update(operation, state='needs-attention', error=str(error))
                    raise
            return {'zip': record, 'candidates': candidates, 'duplicate': existing is not None,
                    'moved': True, 'method': method}

    @staticmethod
    def _assert_content(expected, actual):
        if any(expected[key] != actual[key] for key in ('sha256', 'size')):
            raise ValueError('目标内容校验不一致；保留原件。')

    def _copy_commit(self, source, target, info, operation, project_id, zip_id, cancel_event, progress):
        temporary = target.with_name('.' + operation + '.partial')
        created_identity = None
        try:
            zone = source.zone_metadata()
            source.stream.seek(0)
            with temporary.open('xb') as output:
                opened = os.fstat(output.fileno())
                created_identity = (opened.st_dev, opened.st_ino)
                count = 0
                for block in iter(lambda: source.stream.read(1024 * 1024), b''):
                    cancelled(cancel_event)
                    output.write(block)
                    count += len(block)
                    if progress:
                        progress(count, info['size'])
                output.flush()
                os.fsync(output.fileno())
            if zone is not None:
                with open(str(temporary) + ':Zone.Identifier', 'xb') as output:
                    output.write(zone)
                    output.flush()
                    os.fsync(output.fileno())
            with ZipLease(temporary) as destination:
                verified = destination.inspect(cancel_event=cancel_event)
                self._assert_content(info, verified)
                if destination.zone_metadata() != zone:
                    raise ValueError('安全来源标记校验不一致；保留原件。')
                cancelled(cancel_event)
                destination.rename_to(target)
                verified['path'] = str(target)
                verified['filename'] = info['filename']
                self._update(operation, method='verified-copy-delete', state='placed')
                record = self._commit_record(operation, verified, project_id, zip_id)
                source.remove_after_verified_commit()
                return record
        finally:
            # Only this operation's unpublished temporary file can be removed here.
            if created_identity is not None and temporary.exists():
                with ZipLease(temporary) as owned:
                    if (owned.initial.st_dev, owned.initial.st_ino) == created_identity:
                        owned.remove_after_verified_commit()

    def link_zip(self, zip_id, project_id):
        with self._lock:
            return self._link_zip_locked(zip_id, project_id)

    def _link_zip_locked(self, zip_id, project_id):
        record = self.db.get_zip(zip_id)
        bundle = self.db.get_zip_bundle(zip_id)
        if bundle and bundle['declared_count'] > 1:
            raise ValueError('这是多项目包，不能移动或改成单个项目。')
        project = self.service.get_project(project_id)
        with self.db.connect() as connection:
            self.db._assert_projects_editable(connection, record['project_id'], project_id)
            owned = connection.execute('SELECT 1 FROM managed_zips WHERE zip_id=?', (zip_id,)).fetchone()
            delivered = connection.execute('SELECT 1 FROM projects WHERE delivered_zip_id=?', (zip_id,)).fetchone()
        if record['project_id'] == project['id']:
            return {'zip': record, 'moved': False}
        if delivered:
            raise ValueError('已确认交付的文件不能改关联。')
        if not owned:
            # Historical reference association does not authorize silently moving its file.
            self.db.link_zip(zip_id, project_id)
            return {'zip': self.db.get_zip(zip_id), 'moved': False}
        return self.import_zip(record['path'], project_id=project_id, _zip_id=zip_id)

    def recover(self):
        """Reconcile a known destination, never delete a source merely on startup."""
        with self._lock:
            return self._recover_locked()

    def _recover_locked(self):
        unresolved = []
        with self._lock, self.db.connect() as connection:
            pending = [dict(row) for row in connection.execute("SELECT * FROM managed_transfers WHERE state!='complete'")]
        for op in pending:
            target, source = Path(op['destination']), Path(op['source'])
            try:
                partial = target.with_name('.' + op['id'] + '.partial')
                if partial.exists():
                    op['error'] = '发现未完成的暂存文件，归属不能重新确认，已保留：' + str(partial)
                    self._update(op['id'], state='needs-attention', error=op['error'])
                    unresolved.append(op)
                    continue
                if not target.is_relative_to(self.root) or not target.is_file():
                    if source.is_file():
                        self._update(op['id'], state='complete', error='原件保留；未完成移动，请重新拖入。')
                    else:
                        unresolved.append(op)
                    continue
                with ZipLease(target, movable=False) as lease:
                    info = lease.inspect()
                    expected = json.loads(op['metadata'])
                    self._assert_content(expected, info)
                    with self.db.connect() as connection:
                        committed = connection.execute('SELECT path,sha256 FROM zips WHERE id=?', (op['zip_id'],)).fetchone()
                    if not committed or committed['path'].casefold() != str(target).casefold() or committed['sha256'] != info['sha256']:
                        self._commit_record(op['id'], {**info, 'filename': expected['filename']}, op['project_id'], op['zip_id'])
                if source.exists():
                    op['error'] = '目标已校验，原位置仍有文件；请重新拖入原件以完成移动。'
                    self._update(op['id'], state='needs-attention', error=op['error'])
                    unresolved.append(op)
                else:
                    self._update(op['id'], state='complete', error='')
            except (OSError, ValueError, KeyError) as error:
                self._update(op['id'], state='needs-attention', error=str(error))
                unresolved.append(op)
        return unresolved
