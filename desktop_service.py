"""Local desktop intake: explicit text receipts and tool-managed ZIP storage."""

from __future__ import annotations

import hashlib
import json
import re
from concurrent.futures import CancelledError
from datetime import datetime
from pathlib import Path

from app import WorkBox
from managed_archive import ManagedArchive
from parser import MAX_MESSAGE_LENGTH, NUMBERED, PROJECT_LABEL, TITLE, TITLE_END, ParsedProject, parse_message
from workspace_manager import WorkspaceManager, _path, _zip_structure
from snapshot_manager import SnapshotManager
from native_files import ZipLease, cancelled, plain_path, path_is_within
from zip_manager import match_projects
from file_matcher import match_work_file_projects, new_file_project_name
from bundle_analyzer import analyze_bundle, bundle_hint
import unicodedata

EXTENSIONS = '''
CREATE TABLE IF NOT EXISTS intake_receipts (
    id INTEGER PRIMARY KEY, fingerprint TEXT NOT NULL UNIQUE,
    raw_text TEXT NOT NULL, candidates TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('needs-review','committed','dismissed')),
    reason TEXT NOT NULL DEFAULT '', batch_name TEXT NOT NULL,
    batch_id INTEGER, project_ids TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS desktop_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
'''


def classify_text(raw: str) -> tuple[list[ParsedProject], str]:
    """Only unambiguous title lists enter the project list without a review form."""
    if not isinstance(raw, str) or not raw.strip() or len(raw) > MAX_MESSAGE_LENGTH:
        raise ValueError('请拖入非空项目消息，单条最多 200,000 字符。')
    cleaned = re.sub(r'[,，]\s*加急\s*$', '（加急）', raw, flags=re.MULTILINE)
    try:
        candidates = parse_message(cleaned)
    except ValueError as error:
        return [], str(error)
    if re.search(r'https?://|(?:^|\s)[A-Za-z]:[\\/]|\.zip\b', raw, re.IGNORECASE):
        return candidates, '收到的是网址或文件名文字，不是实体 ZIP。请核对项目名，或拖入本地 ZIP 文件。'
    lines = [line.strip() for line in re.split(r'[\r\n；;]+', cleaned) if line.strip()]
    declared = []
    unclear = []
    titles = []
    for line in lines:
        count = re.fullmatch(r'(?:共|合计)?\s*(\d+)\s*(?:个项目|项|件)(?:可运行)?[。！!]?\s*', line)
        if count:
            declared.append(int(count.group(1)))
        elif NUMBERED.match(line) or PROJECT_LABEL.match(line) or TITLE.search(line):
            titles.append(line)
        elif TITLE_END.search(line):
            titles.append(line)
        elif re.match(r'^(说明|备注|需求|要求|功能|本批项目)\s*[:：]', line):
            continue
        else:
            unclear.append(line)
    parsed = parse_message('\n'.join(f'{index + 1}. {line}' if not (NUMBERED.match(line) or PROJECT_LABEL.match(line) or TITLE.search(line)) else line for index, line in enumerate(titles)))
    if declared and (len(set(declared)) != 1 or declared[0] != len(parsed)):
        return candidates, '消息声明的项目数量与识别结果不一致，请核对。'
    if unclear or not titles or not candidates or len(candidates) > 500:
        return candidates, '这条消息需要核对项目名称；原文已保留，不会自动生成猜测项目。'
    # Mixed plain and numbered lines must not lose unnumbered titles in the legacy parser.
    return parsed, ''


class DesktopService(WorkBox):
    def __init__(self, database_path):
        super().__init__(database_path)
        with self.db.connect() as connection:
            connection.executescript(EXTENSIONS)
        self.archive = ManagedArchive(self, self.db.path.parent / 'packages')
        self.workspaces = WorkspaceManager(self)
        self.intake_log = self.workspaces.intake_log
        self.snapshots = SnapshotManager(self, self.workspaces)

    @staticmethod
    def _receipt(row, *, duplicate=False):
        value = dict(row)
        value['receipt_id'] = value['id']
        value['project_ids'] = json.loads(value['project_ids'])
        value['candidates'] = json.loads(value['candidates'])
        value['duplicate'] = duplicate
        return value

    @staticmethod
    def _commit_text(connection, receipt, projects):
        if not projects or len(projects) > 500:
            raise ValueError('请保留 1 至 500 个项目。')
        for project in projects:
            if not isinstance(project, ParsedProject) or not project.name.strip() or len(project.name.strip()) > 200 or type(project.urgent) is not bool:
                raise ValueError('项目名称不能为空、不能超过 200 字符，加急必须为是或否。')
        batch_name = receipt['batch_name']
        suffix = 1
        while connection.execute('SELECT 1 FROM batches WHERE name=?', (batch_name,)).fetchone():
            suffix += 1
            batch_name = receipt['batch_name'] + f' ({suffix})'
        batch_id = connection.execute('INSERT INTO batches(name) VALUES (?)', (batch_name,)).lastrowid
        connection.execute('UPDATE intake_receipts SET batch_name=? WHERE id=?', (batch_name, receipt['id']))
        message_id = connection.execute('INSERT INTO messages(batch_id,raw_text) VALUES (?,?)', (batch_id, receipt['raw_text'])).lastrowid
        ids = [connection.execute('INSERT INTO projects(message_id,batch_id,name,urgent) VALUES (?,?,?,?)',
               (message_id, batch_id, project.name.strip(), int(project.urgent))).lastrowid for project in projects]
        connection.execute("UPDATE intake_receipts SET state='committed',batch_id=?,project_ids=?,reason='' WHERE id=?", (batch_id, json.dumps(ids), receipt['id']))

    def receive_text(self, text):
        projects, reason = classify_text(text)
        fingerprint = hashlib.sha256(text.strip().encode('utf-8')).hexdigest()
        with self.db.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute('SELECT * FROM intake_receipts WHERE fingerprint=?', (fingerprint,)).fetchone()
            if row:
                result = self._receipt(row, duplicate=True)
            else:
                candidates = json.dumps([{'name': p.name, 'urgent': p.urgent} for p in projects], ensure_ascii=False)
                receipt_id = connection.execute("INSERT INTO intake_receipts(fingerprint,raw_text,candidates,state,reason,batch_name) VALUES (?,?,?,'needs-review',?,'')", (fingerprint, text, candidates, reason)).lastrowid
                batch_name = datetime.now().strftime('%m月%d日 %H:%M 收件') + f' #{receipt_id}'
                connection.execute('UPDATE intake_receipts SET batch_name=? WHERE id=?', (batch_name, receipt_id))
                receipt = connection.execute('SELECT * FROM intake_receipts WHERE id=?', (receipt_id,)).fetchone()
                if not reason:
                    self._commit_text(connection, receipt, projects)
                result = self._receipt(connection.execute('SELECT * FROM intake_receipts WHERE id=?', (receipt_id,)).fetchone())
        return self._attach_workspaces(result)

    def _attach_workspaces(self, result):
        # 消息先提交；目录失败不能回滚或丢失已收录的需求。
        if result.get('project_ids'):
            try:
                result['workspaces'] = self.workspaces.ensure_projects(result['project_ids'])
            except (OSError, ValueError, RuntimeError) as error:
                result['workspace_error'] = str(error)
        return result

    def capture_message(self, raw_text, batch_name, projects=None):
        return self._attach_workspaces(super().capture_message(raw_text, batch_name, projects))

    def ensure_workspaces(self, project_id):
        project = self.get_project(project_id)
        with self.db.connect() as connection:
            self.db._assert_projects_editable(connection, project_id)
            ids = [row[0] for row in connection.execute('SELECT id FROM projects WHERE batch_id=? ORDER BY id', (project['batch_id'],))]
        return self.workspaces.ensure_projects(ids)

    def get_project(self, project_id):
        result = super().get_project(project_id)
        if hasattr(self, 'workspaces'):
            result['workspace'] = self.workspaces.get_workspace(project_id)
        if hasattr(self, 'snapshots'):
            versions = {row['zip_id']: row for row in self.snapshots.list_versions(project_id)}
            for package in result['zips']:
                package['approved'] = bool(versions.get(package['id'], {}).get('approved'))
                package['file_counts'] = versions.get(package['id'], {}).get('file_counts', [])
                package['bundle_state'] = versions.get(package['id'], {}).get('bundle_state')
                if package['bundle_state'] is None:
                    bundle = self.db.get_zip_bundle(package['id'])
                    package['bundle_state'] = bundle['state'] if bundle else None
        return result

    def pending_receipts(self):
        with self.db.connect() as connection:
            return [self._receipt(row) for row in connection.execute("SELECT * FROM intake_receipts WHERE state='needs-review' ORDER BY id DESC")]

    def resolve_text(self, receipt_id, projects):
        with self.db.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            receipt = connection.execute('SELECT * FROM intake_receipts WHERE id=?', (receipt_id,)).fetchone()
            if receipt is None or receipt['state'] != 'needs-review':
                raise ValueError('这条收件已处理，请刷新。')
            self._commit_text(connection, receipt, projects)
            result = self._receipt(connection.execute('SELECT * FROM intake_receipts WHERE id=?', (receipt_id,)).fetchone())
        return self._attach_workspaces(result)

    def dismiss_receipt(self, receipt_id):
        with self.db.connect() as connection:
            connection.execute("UPDATE intake_receipts SET state='dismissed' WHERE id=? AND state='needs-review'", (receipt_id,))

    def get_setting(self, key, default=None):
        with self.db.connect() as connection:
            row = connection.execute('SELECT value FROM desktop_settings WHERE key=?', (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def set_setting(self, key, value):
        if key not in {'orb_position', 'manager_geometry'} or not isinstance(value, list) or not all(type(v) is int for v in value) or len(value) not in (2, 4):
            raise ValueError('无效的窗口位置设置。')
        with self.db.connect() as connection:
            connection.execute('INSERT INTO desktop_settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, json.dumps(value)))

    def import_zip(self, path, *, cancel_event=None, progress=None):
        routing = self.file_routing(path)
        if routing.get('requires_choice'):
            raise ValueError('文件归属未确定，请恢复项目或选择文件对应的项目。')
        candidates = routing['candidates']
        project_id = candidates[0]['id'] if len(candidates) == 1 else None
        return self.snapshots.archive_now(path, project_id, cancel_event=cancel_event, progress=progress)

    def file_candidates(self, path):
        return self.file_routing(path)['candidates']

    def file_routing(self, path):
        source = plain_path(path, allow_network=True)
        all_projects = {row['id']: row for row in self.list_projects(archived=None, deleted=None)}
        registered = self.workspaces.list_workspaces(include_archived=True)
        protected = [all_projects.get(row['project_id'], {}) for row in registered
                     if path_is_within(source, row['path'])]
        if any(row.get('deleted_at') for row in protected):
            return dict(candidates=[], basis='来源位于已删除项目工作目录，请恢复项目或人工选择', requires_choice=True)
        if any(row.get('archived_at') for row in protected):
            return dict(candidates=[], basis='来源位于已归档项目工作目录，请恢复项目或人工选择', requires_choice=True)
        retained = [row for row in all_projects.values() if not row.get('deleted_at')]
        deleted = [row for row in all_projects.values() if row.get('deleted_at')]
        active = self.list_projects()
        by_id = {row['id']: row for row in active}
        inside = [by_id[row['project_id']] for row in self.workspaces.list_workspaces()
                  if row['project_id'] in by_id and path_is_within(source, row['path'])]
        if source.suffix.casefold() == '.zip':
            named = match_projects(source.name, retained)
            if any(row.get('archived_at') for row in named):
                return dict(candidates=[row for row in named if not row.get('archived_at')],
                    basis='ZIP 名称涉及已归档项目，请恢复项目或人工选择', requires_choice=True)
            if not named and match_projects(source.name, deleted):
                return dict(candidates=inside, basis='ZIP 名称涉及已删除项目，请恢复项目或人工选择', requires_choice=True)
            return dict(candidates=inside or named, basis='ZIP 专门匹配规则')
        named = match_work_file_projects(source.name, retained)
        if any(row.get('archived_at') for row in named['candidates']):
            # 已归档的完整名称仍是名称边界，不能继续剥尾缀命中另一个活动项目。
            return dict(candidates=[row for row in named['candidates'] if not row.get('archived_at')],
                basis=named['basis'] + '，涉及已归档项目，请恢复项目或人工选择', requires_choice=True)
        if not named['candidates'] and match_work_file_projects(source.name, deleted)['candidates']:
            return dict(candidates=inside, basis='材料名称涉及已删除项目，请恢复项目或人工选择', requires_choice=True)
        if not inside:
            return named
        if not named['candidates']:
            return dict(candidates=inside, basis='已登记的项目工作目录')
        inside_ids = {row['id'] for row in inside}
        common = [row for row in named['candidates'] if row['id'] in inside_ids]
        if common:
            return dict(candidates=common, basis=named['basis'] + '，与工作目录一致')
        return dict(candidates=named['candidates'] + inside, basis='文件名与工作目录冲突，需人工选择')

    def record_file_decision(self, path, status, routing, *, project_id=None, manual=None, message=''):
        candidates = routing.get('candidates', [])
        project = next((row for row in candidates if row['id'] == project_id), None)
        if project is None and project_id is not None:
            with self.db.connect() as connection:
                project = connection.execute('SELECT name FROM projects WHERE id=?', (project_id,)).fetchone()
        return self.intake_log.append(path, status, project_id=project_id,
            project_name=project['name'] if project else '', basis=routing.get('basis', ''),
            candidate_ids=[row['id'] for row in candidates], manual=manual, message=message)

    def is_bundle_candidate(self, filename):
        if not bundle_hint(filename):
            return False
        normalized = lambda name: ' '.join(unicodedata.normalize('NFKC', name).split()).casefold()
        stem = normalized(Path(filename).stem)
        return not any(normalized(p['name']) == stem for p in self.list_projects(archived=None, deleted=None))

    def _capture_file_projects(self, source, names):
        """名称检查与收件提交共用事务，重复拖入不能产生第二组项目。"""
        normalized = lambda value: ' '.join(unicodedata.normalize('NFKC', value).split()).casefold()
        wanted = {normalized(name) for name in names}
        if not names or len(names) > 500 or len(wanted) != len(names):
            raise ValueError('项目名称或项目数量无效。')
        with self.db.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            existing = [dict(row) for row in connection.execute('''SELECT p.id,p.name,d.archived_at
                FROM projects p LEFT JOIN project_details d ON d.project_id=p.id''')
                if normalized(row['name']) in wanted]
            if existing:
                if len(names) == 1 and len(existing) == 1 and not existing[0]['archived_at']:
                    return dict(project_ids=[existing[0]['id']], created_project_ids=[])
                # 合包中已经存在的项目不能另建、拼接或迁移到新批次。
                return dict(project_ids=[], created_project_ids=[])
            raw = '文件自动收件：' + str(source)
            candidates = json.dumps([dict(name=name, urgent=False) for name in names], ensure_ascii=False)
            seed = str(source) + candidates + datetime.now().isoformat(timespec='microseconds')
            fingerprint = hashlib.sha256(seed.encode('utf-8')).hexdigest()
            receipt_id = connection.execute('''INSERT INTO intake_receipts
                (fingerprint,raw_text,candidates,state,reason,batch_name) VALUES (?,?,?,'needs-review','','')''',
                (fingerprint, raw, candidates)).lastrowid
            batch = datetime.now().strftime('%m月%d日 %H:%M 文件收件') + f' #{receipt_id}'
            connection.execute('UPDATE intake_receipts SET batch_name=? WHERE id=?', (batch, receipt_id))
            receipt = connection.execute('SELECT * FROM intake_receipts WHERE id=?', (receipt_id,)).fetchone()
            self._commit_text(connection, receipt, [ParsedProject(name) for name in names])
            result = self._receipt(connection.execute('SELECT * FROM intake_receipts WHERE id=?', (receipt_id,)).fetchone())
        result['created_project_ids'] = result['project_ids']
        result = self._attach_workspaces(result)
        if result.get('workspace_error'):
            raise RuntimeError('项目已建立，工作目录创建失败：' + result['workspace_error'])
        return result

    def _working_zip_copy(self, source, directory, *, cancel_event=None, progress=None):
        """复制明确归属的 ZIP；同内容复用，同名新内容另放收件版本目录。"""
        from workspace_manager import _hash_stream, copy_verified
        directory = _path(directory)
        if not directory.is_dir():
            raise ValueError('项目工作目录不存在，ZIP 尚未收录。')
        with self.archive._lock, ZipLease(source, movable=False) as lease:
            _zip_structure(lease.stream, cancel_event)
            digest, size = _hash_stream(lease.stream, cancel_event=cancel_event)

            def same_content(candidate):
                candidate = _path(candidate)
                if not candidate.is_file():
                    return False
                with ZipLease(candidate, movable=False) as saved:
                    return _hash_stream(saved.stream, cancel_event=cancel_event) == (digest, size)

            target = _path(directory / source.name)
            if target == source or same_content(target):
                return dict(path=str(target), sha256=digest)
            if target.exists():
                versions = _path(directory / 'ZIP收件版本')
                versions.mkdir(exist_ok=True)
                index = 1
                while True:
                    folder = _path(versions / (digest[:16] + (f' ({index})' if index > 1 else '')))
                    target = _path(folder / source.name)
                    if not folder.exists():
                        folder.mkdir()
                        break
                    if same_content(target):
                        return dict(path=str(target), sha256=digest)
                    index += 1
            metadata = copy_verified(source, target, cancel_event=cancel_event, progress=progress, require_zip=True)
            if (metadata['sha256'], metadata['size']) != (digest, size):
                raise RuntimeError('ZIP 来源在收录期间变化，工作副本未通过核对。')
            return metadata

    def receive_path(self, path, project_id=None, *, cancel_event=None, progress=None, manual=None, folder_items=None):
        """在后台判断文件/目录与归属；需要人工选择时返回事实，界面线程再提问。"""
        routing = {}
        try:
            if folder_items is not None:
                if manual is not True or project_id is None:
                    raise ValueError('请明确选择文件夹材料对应的项目。')
                from folder_intake import FolderIntake
                return FolderIntake(self).receive_items(folder_items, project_id,
                    cancel_event=cancel_event, progress=progress)
            source = _path(path, allow_network=True)
            cancelled(cancel_event)
            if source.is_dir():
                from folder_intake import FolderIntake
                result = FolderIntake(self).receive(source, project_id, cancel_event=cancel_event,
                    progress=progress, manual=manual)
                result['manual_choice_attempted'] = manual is True
                return result
            if project_id is None and not self.is_bundle_candidate(source.name):
                routing = self.file_routing(source)
                candidates = routing['candidates']
                if routing.get('requires_choice') or len(candidates) > 1:
                    self.record_file_decision(source, 'needs-choice', routing)
                    return {'intake_choice': str(source), 'routing': routing}
                project_id = candidates[0]['id'] if len(candidates) == 1 else None
                manual = False
        except Exception as error:
            self.record_file_decision(path, 'cancelled' if isinstance(error, CancelledError) else 'failed',
                routing, project_id=project_id, manual=manual, message=str(error))
            raise
        return self.receive_file(source, project_id, cancel_event=cancel_event, progress=progress, manual=manual)

    def receive_file(self, path, project_id=None, *, cancel_event=None, progress=None, manual=None):
        ordinary = Path(path).suffix.casefold() != '.zip'
        routing, failure_status, created = {}, 'failed', {}
        try:
            source = _path(path, allow_network=True)
            cancelled(cancel_event)
            if not source.is_file():
                raise ValueError('请选择本机普通文件，不能收录目录。')
            # ZIP 登记前先验证结构；坏包不能留下根据文件名生成的空项目。
            if not ordinary:
                source = self.snapshots._source(source)
            with ZipLease(source, movable=False) as lease:
                if not ordinary:
                    _zip_structure(lease.stream, cancel_event)
            routing = self.file_routing(source)
            if routing.get('requires_choice') and (project_id is None or manual is False):
                failure_status = 'needs-choice'
                raise ValueError('文件归属未确定，请恢复已归档项目或选择文件对应的项目。')
            if self.is_bundle_candidate(source.name):
                plan = analyze_bundle(source, self.list_projects(archived=None, deleted=None))
                if plan['provision_names'] and not routing.get('requires_choice'):
                    created = self._capture_file_projects(source, plan['provision_names'])
                    plan = analyze_bundle(source, self.list_projects(archived=None, deleted=None))
                working = None
                if plan['state'] == 'matched':
                    self.ensure_workspaces(plan['project_ids'][0])
                    workspace = self.workspaces.get_workspace(plan['project_ids'][0])
                    copied = self._working_zip_copy(source, workspace['group_path'],
                        cancel_event=cancel_event, progress=progress)
                    working = copied['path']
                # 合包稳定后按包内全部项目统一关联。
                result = self.snapshots.watch_zip(source, None)
                if working and Path(working) != source:
                    self.snapshots.watch_work_copy(working, source, None, baseline_sha256=copied['sha256'])
                result['created_project_ids'] = created.get('created_project_ids', [])
                result['working_zip_path'] = working
                return result
            choices = routing['candidates']
            if project_id is None:
                manual = False
                if len(choices) == 1 and not routing.get('requires_choice'):
                    project_id = choices[0]['id']
                elif len(choices) > 1 or routing.get('requires_choice'):
                    failure_status = 'needs-choice'
                    raise ValueError('文件归属未确定，请选择文件对应的项目。')
                else:
                    name = new_file_project_name(source.name) if ordinary else source.stem.strip()
                    if not name or len(name) > 200:
                        raise ValueError('项目名称需要 1 至 200 个字符，请调整文件名。')
                    created = self._capture_file_projects(source, [name])
                    routing = self.file_routing(source)
                    choices = routing['candidates']
                    if len(choices) != 1 or routing.get('requires_choice') or not created['project_ids']:
                        failure_status = 'needs-choice'
                        raise ValueError('文件归属发生变化，请重新选择对应项目。')
                    project_id = choices[0]['id']
                    if created.get('created_project_ids'):
                        routing['basis'] += '，根据文件名自动新建项目'
            elif manual is False and (routing.get('requires_choice') or len(choices) != 1 or choices[0]['id'] != project_id):
                # 文件可能在队列等待期间改名、移动或出现同名项目，不能使用过期判断。
                raise ValueError('文件归属发生变化，请重新选择对应项目。')
            self.ensure_workspaces(project_id)
            if not ordinary:
                workspace = self.workspaces.get_workspace(project_id)
                copied = self._working_zip_copy(source, workspace['path'],
                    cancel_event=cancel_event, progress=progress)
                working = copied['path']
                result = self.snapshots.watch_zip(source, project_id)
                if Path(working) != source:
                    self.snapshots.watch_work_copy(working, source, project_id, baseline_sha256=copied['sha256'])
                result['working_zip_path'] = working
            else:
                result = self.workspaces.import_file(source, project_id, cancel_event=cancel_event, progress=progress,
                    routing=dict(basis=routing['basis'], candidate_ids=[row['id'] for row in choices], manual=manual))
            result['created_project_ids'] = created.get('created_project_ids', [])
            return result
        except Exception as error:
            try:
                self.record_file_decision(path, 'cancelled' if isinstance(error, CancelledError) else failure_status,
                    routing, project_id=project_id, manual=manual, message=str(error))
            except Exception as log_error:
                error.add_note('收录记录写入失败：' + str(log_error))
            raise

    def scan_archives(self, now=None, *, cancel_event=None, progress=None):
        return self.snapshots.scan(now, cancel_event=cancel_event, progress=progress)

    def link_zip(self, zip_id, project_id):
        if self.snapshots.is_snapshot(zip_id):
            return self.snapshots.link_zip(zip_id, project_id)
        return self.archive.link_zip(zip_id, project_id)

    def confirm_delivery(self, project_id, zip_id=None, *, confirmed=False, cancel_event=None, progress=None):
        if zip_id is not None:
            return super().confirm_delivery(project_id, zip_id, confirmed=confirmed,
                cancel_event=cancel_event, progress=progress)
        from delivery_archive import ProjectDeliveryArchive
        return ProjectDeliveryArchive(self, self.workspaces).confirm(project_id, confirmed=confirmed,
            cancel_event=cancel_event, progress=progress)

    def rename_project(self, project_id, name):
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 200:
            raise ValueError('项目名称需要 1 至 200 个字符。')
        with self.db.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            self.db._assert_projects_editable(connection, project_id)
            changed = connection.execute("UPDATE projects SET name=?,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?", (name.strip(), project_id))
            if changed.rowcount != 1:
                raise KeyError('项目不存在。')
