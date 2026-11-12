"""Local desktop intake: explicit text receipts and tool-managed ZIP storage."""

from __future__ import annotations

import hashlib
import json
import re
from concurrent.futures import CancelledError
from datetime import date, datetime, timezone
from pathlib import Path

from app import WorkBox
from database import Database, DELIVERY_TIMEZONE
from managed_archive import ManagedArchive
from parser import MAX_MESSAGE_LENGTH, NUMBERED, PROJECT_LABEL, TITLE, TITLE_END, ParsedProject, parse_message
from workspace_manager import WorkspaceManager, _path, _zip_structure
from snapshot_manager import SnapshotManager
from native_files import ZipLease, cancelled, plain_path, path_is_within
from zip_manager import match_projects
from file_matcher import match_work_file_projects, new_file_project_name
from bundle_analyzer import analyze_bundle, bundle_hint
from workflow_policy import read_policy, creation_disabled_message
from text_intake import TextProjectConflict, resolve_text_projects
from project_names import project_name_key
from functools import wraps

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
    if re.search(r'(?<![A-Za-z0-9])G\d{8}-\d{3,}(?:-\d{2,})?(?!\d)', raw, re.IGNORECASE):
        return [], '消息含已有任务编号，请核对项目名称；不会将编号作为新项目名。'
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


def _serialized_storage(method):
    """收件与目录变更共用锁，避免重新创建刚改名的旧路径。"""
    @wraps(method)
    def guarded(self, *args, **kwargs):
        with self.archive._lock:
            if not self._project_changes_ready:
                self.recover_project_changes()
            if (any(error.get('global_block') for error in self.project_change_errors)
                    or self.workspaces.document_versions.blocked):
                raise RuntimeError('项目目录或文档版本记录需要恢复，暂不能收录或修改文件。请保留现场并检查恢复记录。')
            return method(self, *args, **kwargs)
    return guarded


def _project_added_on(value):
    """SQLite 旧无时区时间按 UTC；缺失/损坏事实不猜测新增日期。"""
    try:
        if not isinstance(value, str) or not re.match(r'^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}', value):
            return None
        moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(DELIVERY_TIMEZONE).date().isoformat()
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _read_project_added_dates(connection, *, archived, deleted):
    # 日期选择只随可见域变化，不受搜索、状态或当前日期选择缩小。
    clauses = ['NOT EXISTS(SELECT 1 FROM project_merges m WHERE m.source_id=p.id)']
    if deleted is not None:
        clauses.append('x.deleted_at IS NOT NULL' if deleted else 'x.deleted_at IS NULL')
    if archived is not None and deleted is not True:
        clauses.append('d.archived_at IS NOT NULL' if archived else 'd.archived_at IS NULL')
    rows = connection.execute('''SELECT p.id,p.created_at FROM projects p
        LEFT JOIN project_details d ON d.project_id=p.id
        LEFT JOIN project_deletions x ON x.project_id=p.id WHERE ''' + ' AND '.join(clauses))
    by_project, counts = {}, {}
    for row in rows:
        added_on = _project_added_on(row['created_at'])
        by_project[row['id']] = added_on
        key = added_on or 'unknown'
        counts[key] = counts.get(key, 0) + 1
    ordered = sorted((key for key in counts if key != 'unknown'), reverse=True)
    if 'unknown' in counts:
        ordered.append('unknown')
    return by_project, [dict(added_on=key, label='日期未知' if key == 'unknown' else key,
                             total_count=counts[key]) for key in ordered]


class DesktopService(WorkBox):
    def __init__(self, database_path):
        super().__init__(database_path)
        with self.db.connect() as connection:
            connection.executescript(EXTENSIONS)
        self.archive = ManagedArchive(self, self.db.path.parent / 'packages')
        self.workspaces = WorkspaceManager(self)
        self.intake_log = self.workspaces.intake_log
        self.snapshots = SnapshotManager(self, self.workspaces)
        from project_directory_changes import ProjectDirectoryChanges
        from project_changes import ProjectChanges
        self.directory_changes = ProjectDirectoryChanges(self.db, self.workspaces, self.archive._lock)
        self.project_changes = ProjectChanges(self, self.directory_changes)
        # 应用还未取得单实例锁；此处不能恢复另一个运行实例的目录变更。
        self.project_change_errors = []
        self._project_changes_ready = False
        from task_groups import TaskGroups
        self.task_groups = TaskGroups(self)
        from archive_classification import ArchiveClassification
        self.archive_classification = ArchiveClassification(self.db)

    def recover_project_changes(self):
        with self.archive._lock:
            self.project_change_errors = self.directory_changes.recover()
            self.project_change_errors.extend(self.workspaces.document_versions.recover())
            self._project_changes_ready = True
            return self.project_change_errors

    def list_projects(self, *args, **kwargs):
        projects = super().list_projects(*args, **kwargs)
        aliases = {}
        for row in self.db.list_project_aliases():
            aliases.setdefault(row['project_id'], []).append(row['alias_name'])
        merged_ids = {}
        with self.db.connect() as connection:
            for row in connection.execute('SELECT project_id,canonical_id FROM project_canonical WHERE project_id!=canonical_id ORDER BY project_id'):
                merged_ids.setdefault(row['canonical_id'], []).append(row['project_id'])
        for project in projects:
            if project['id'] in aliases:
                project['name_aliases'] = aliases[project['id']]
            if project['id'] in merged_ids:
                project['merged_project_ids'] = merged_ids[project['id']]
        return projects

    def project_workbench_snapshot(self, query='', status=None, batch_key=None, urgent_only=False,
                                   *, archived=False, due_filter=None, today=None, deleted=False,
                                   type_id=None, added_on=None):
        """项目、批次和别名取自同一读取事务，沿用项目列表原排序与筛选。"""
        from project_batches import read_project_batch_index
        if not isinstance(query, str) or len(query) > 1000:
            raise ValueError('搜索文字过长。')
        if batch_key is not None and not isinstance(batch_key, str):
            raise ValueError('批次筛选编号无效。')
        if added_on not in (None, 'unknown'):
            try:
                if not isinstance(added_on, str) or date.fromisoformat(added_on).isoformat() != added_on:
                    raise ValueError
            except (ValueError, TypeError):
                raise ValueError('新增日期筛选必须为 YYYY-MM-DD、日期未知或全部。') from None
        filters = dict(status=status, urgent_only=urgent_only, archived=archived,
                       due_filter=due_filter, today=today, deleted=deleted, type_id=type_id)
        with self.db.connect() as connection:
            connection.execute('BEGIN')
            projects = self.db.list_projects(connection=connection, **filters)
            index = read_project_batch_index(connection)
            added_dates, date_choices = _read_project_added_dates(connection, archived=archived, deleted=deleted)
            aliases, merged_ids = {}, {}
            for row in connection.execute('''SELECT DISTINCT c.canonical_id AS project_id,a.name AS alias_name
                    FROM project_name_aliases a JOIN project_canonical c ON c.project_id=a.project_id
                    ORDER BY project_id,alias_name'''):
                aliases.setdefault(row['project_id'], []).append(row['alias_name'])
            for row in connection.execute('SELECT project_id,canonical_id FROM project_canonical WHERE project_id!=canonical_id ORDER BY project_id'):
                merged_ids.setdefault(row['canonical_id'], []).append(row['project_id'])
            # 继续交由原SQL搜索名称、原目录批次、备注以及所有ZIP文件名。
            query = query.strip()
            matching_ids = ({row['id'] for row in self.db.list_projects(query=query, connection=connection, **filters)}
                            if query else None)
            result = []
            for project in projects:
                pid = project['id']
                project['added_on'] = added_dates.get(pid)
                project['added_on_label'] = project['added_on'] or '日期未知'
                if added_on is not None and (project['added_on'] or 'unknown') != added_on:
                    continue
                markers = index['by_project'].get(pid, [])
                if batch_key is not None and not any(m['key'] == batch_key for m in markers):
                    continue
                if matching_ids is not None and pid not in matching_ids and not any(
                        query.casefold() in str(m[field]).casefold()
                        for m in markers for field in ('key', 'code', 'label')):
                    continue
                project['batch_markers'] = markers
                if pid in aliases:
                    project['name_aliases'] = aliases[pid]
                if pid in merged_ids:
                    project['merged_project_ids'] = merged_ids[pid]
                result.append(project)
        return dict(projects=result, batch_options=index['options'], added_date_choices=date_choices,
                    warnings=index['warnings'])

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
        # Resolve every name before creating any batch/project. The caller owns
        # the write transaction, including review confirmation and queued intake.
        planned = resolve_text_projects(connection, projects)
        batch_name = receipt['batch_name']
        suffix = 1
        while connection.execute('SELECT 1 FROM batches WHERE name=?', (batch_name,)).fetchone():
            suffix += 1
            batch_name = receipt['batch_name'] + f' ({suffix})'
        batch_id = connection.execute('INSERT INTO batches(name) VALUES (?)', (batch_name,)).lastrowid
        connection.execute('UPDATE intake_receipts SET batch_name=? WHERE id=?', (batch_name, receipt['id']))
        message_id = connection.execute('INSERT INTO messages(batch_id,raw_text) VALUES (?,?)', (batch_id, receipt['raw_text'])).lastrowid
        ids, created, reused = [], [], []
        for project, existing_id in planned:
            if existing_id is None:
                pid = connection.execute('INSERT INTO projects(message_id,batch_id,name,urgent) VALUES (?,?,?,?)',
                    (message_id, batch_id, project.name.strip(), int(project.urgent))).lastrowid
                created.append(pid)
            else:
                pid = existing_id
                if pid not in reused:
                    reused.append(pid)
            if pid not in ids:
                ids.append(pid)
        if created:
            Database._assign_default_project_type(connection, created)
        reason = f'沿用 {len(reused)} 个已有项目，原状态、批次和工作目录保持。' if reused else ''
        connection.execute("UPDATE intake_receipts SET state='committed',batch_id=?,project_ids=?,reason=? WHERE id=?",
                           (batch_id, json.dumps(ids), reason, receipt['id']))
        return dict(created_project_ids=created, reused_project_ids=reused)

    @_serialized_storage
    def receive_text(self, text):
        projects, reason = classify_text(text)
        fingerprint = hashlib.sha256(text.strip().encode('utf-8')).hexdigest()
        changes = {}
        with self.db.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute('SELECT * FROM intake_receipts WHERE fingerprint=?', (fingerprint,)).fetchone()
            if row and (row['state'] != 'needs-review' or reason):
                result = self._receipt(row, duplicate=True)
            else:
                candidates = json.dumps([{'name': p.name, 'urgent': p.urgent} for p in projects], ensure_ascii=False)
                if row:
                    # 仅在用户重投时重评旧待核对项，保留原收件身份及原文。
                    receipt_id = row['id']
                    connection.execute('UPDATE intake_receipts SET candidates=? WHERE id=?', (candidates, receipt_id))
                else:
                    receipt_id = connection.execute("INSERT INTO intake_receipts(fingerprint,raw_text,candidates,state,reason,batch_name) VALUES (?,?,?,'needs-review',?,'')", (fingerprint, text, candidates, reason)).lastrowid
                    moment = datetime.now()
                    batch_name = f'{moment:%m}月{moment:%d}日 {moment:%H:%M} 收件 #{receipt_id}'
                    connection.execute('UPDATE intake_receipts SET batch_name=? WHERE id=?', (batch_name, receipt_id))
                receipt = connection.execute('SELECT * FROM intake_receipts WHERE id=?', (receipt_id,)).fetchone()
                if not reason:
                    try:
                        changes = self._commit_text(connection, receipt, projects)
                    except TextProjectConflict as error:
                        connection.execute('UPDATE intake_receipts SET reason=? WHERE id=?', (str(error), receipt_id))
                result = self._receipt(connection.execute('SELECT * FROM intake_receipts WHERE id=?', (receipt_id,)).fetchone())
                if row and changes:
                    result.update(self.task_groups.receipt_resolved(connection, receipt_id, result['project_ids']))
        result.update(changes)
        return self._attach_workspaces(result)

    @_serialized_storage
    def receive_task_text(self, text, *, group_id=None, new_round=False, request_id=None):
        return self.task_groups.receive(text, group_id=group_id, new_round=new_round,
                                        request_id=request_id)

    def _attach_workspaces(self, result):
        # 消息先提交；目录失败不能回滚或丢失已收录的需求。
        if result.get('project_ids'):
            try:
                original = list(result['project_ids'])
                result['project_ids'] = list(dict.fromkeys(self.db.canonical_project_id(pid) for pid in original))
                if result['project_ids'] != original:
                    result['original_project_ids'] = original
                result['workspaces'] = self.workspaces.ensure_projects(result['project_ids'])
            except (OSError, ValueError, RuntimeError) as error:
                result['workspace_error'] = str(error)
        return result

    @_serialized_storage
    def capture_message(self, raw_text, batch_name, projects=None):
        return self._attach_workspaces(super().capture_message(raw_text, batch_name, projects))

    @_serialized_storage
    def ensure_workspaces(self, project_id):
        project = self.get_project(project_id)
        with self.db.connect() as connection:
            self.db._assert_projects_editable(connection, project_id)
            ids = [row[0] for row in connection.execute('''SELECT p.id FROM projects p
                LEFT JOIN project_details d ON d.project_id=p.id
                LEFT JOIN project_deletions x ON x.project_id=p.id
                WHERE p.batch_id=? AND d.archived_at IS NULL AND x.deleted_at IS NULL
                AND NOT EXISTS(SELECT 1 FROM project_merges m WHERE m.source_id=p.id) ORDER BY p.id''', (project['batch_id'],))]
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

    @_serialized_storage
    def resolve_text(self, receipt_id, projects):
        with self.db.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            receipt = connection.execute('SELECT * FROM intake_receipts WHERE id=?', (receipt_id,)).fetchone()
            if receipt is None or receipt['state'] != 'needs-review':
                raise ValueError('这条收件已处理，请刷新。')
            changes = self._commit_text(connection, receipt, projects)
            result = self._receipt(connection.execute('SELECT * FROM intake_receipts WHERE id=?', (receipt_id,)).fetchone())
            result.update(self.task_groups.receipt_resolved(connection, receipt_id, result['project_ids']))
        result.update(changes)
        return self._attach_workspaces(result)

    def dismiss_receipt(self, receipt_id):
        with self.db.connect() as connection:
            connection.execute("UPDATE intake_receipts SET state='dismissed' WHERE id=? AND state='needs-review'", (receipt_id,))

    def get_setting(self, key, default=None):
        with self.db.connect() as connection:
            row = connection.execute('SELECT value FROM desktop_settings WHERE key=?', (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def set_setting(self, key, value):
        if key == 'orb_size':
            from floating_settings import validate_floating_size
            value = validate_floating_size(value)
        elif key == 'window_shortcut':
            from global_shortcut import validate_shortcut
            value = validate_shortcut(value)
        elif key == 'manager_window_state':
            if (type(value) is not str or not 0 < len(value) <= 4096 or len(value) % 2
                    or any(character not in '0123456789abcdef' for character in value)):
                raise ValueError('无效的窗口恢复设置。')
        elif key == 'appearance_theme':
            if type(value) is not str or value not in ('light', 'dark', 'system'):
                raise ValueError('外观只支持浅色、深色或跟随系统。')
        elif key == 'delivery_panel_sizes':
            if not isinstance(value, list) or len(value) != 2 or not all(
                    type(size) is int and 0 < size <= 100000 for size in value):
                raise ValueError('无效的交付列表高度设置。')
        elif key not in {'orb_position', 'manager_geometry'} or not isinstance(value, list) or not all(type(v) is int for v in value) or len(value) not in (2, 4):
            raise ValueError('无效的窗口位置设置。')
        with self.db.connect() as connection:
            connection.execute('INSERT INTO desktop_settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, json.dumps(value)))

    @_serialized_storage
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
        with self.db.connect() as connection:
            retired = [dict(row) for row in connection.execute('''SELECT w.path,c.canonical_id
                FROM project_workspaces w JOIN project_merges m ON m.source_id=w.project_id
                JOIN project_canonical c ON c.project_id=w.project_id''')]
        retained_ids = {row['canonical_id'] for row in retired if path_is_within(source, row['path'])}
        if retained_ids:
            return dict(candidates=[row for pid, row in all_projects.items() if pid in retained_ids
                                    and not row.get('archived_at') and not row.get('deleted_at')],
                        basis='来源位于已合并项目的保留备份目录，请明确选择保留项目后再收录。', requires_choice=True)
        registered = self.workspaces.list_workspaces(include_archived=True)
        protected = [all_projects.get(row['project_id'], {}) for row in registered
                     if path_is_within(source, row['path'])]
        if any(row.get('deleted_at') for row in protected):
            return dict(candidates=[], basis='来源位于已删除项目工作目录，请恢复项目或人工选择', requires_choice=True)
        if any(row.get('archived_at') for row in protected):
            return dict(candidates=[], basis='来源位于已归档项目工作目录，请恢复项目或人工选择', requires_choice=True)
        retained = [row for row in all_projects.values() if not row.get('deleted_at')]
        active = self.list_projects()
        by_id = {row['id']: row for row in active}
        inside = [by_id[row['project_id']] for row in self.workspaces.list_workspaces()
                  if row['project_id'] in by_id and path_is_within(source, row['path'])]
        if source.suffix.casefold() == '.zip':
            named = match_projects(source.name, retained, include_annotations=False)
            if not named:
                named = match_projects(source.name, list(all_projects.values()))
            if any(row.get('archived_at') or row.get('deleted_at') for row in named):
                return dict(candidates=[row for row in named if not row.get('archived_at') and not row.get('deleted_at')],
                    basis='ZIP 名称涉及已归档或已删除项目，请恢复项目或人工选择', requires_choice=True)
            return dict(candidates=inside or named, basis='ZIP 专门匹配规则')
        named = match_work_file_projects(source.name, retained, include_annotations=False)
        if not named['candidates']:
            # 明确材料规则仍可纠正已删除的误建名称；开放附注则必须先比较所有长名。
            named = match_work_file_projects(source.name, list(all_projects.values()))
        if any(row.get('archived_at') or row.get('deleted_at') for row in named['candidates']):
            return dict(candidates=[row for row in named['candidates'] if not row.get('archived_at') and not row.get('deleted_at')],
                basis=named['basis'] + '，涉及已归档或已删除项目，请恢复项目或人工选择', requires_choice=True)
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
        stem = project_name_key(Path(filename).stem)
        return not any(project_name_key(name) == stem for p in self.list_projects(archived=None, deleted=None)
                       for name in [p['name'], *p.get('name_aliases', [])])

    def _capture_file_projects(self, source, names):
        """名称检查与收件提交共用事务，重复拖入不能产生第二组项目。"""
        wanted = {project_name_key(name) for name in names}
        if not names or len(names) > 500 or len(wanted) != len(names):
            raise ValueError('项目名称或项目数量无效。')
        with self.db.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            if not read_policy(connection)['auto_create_projects']:
                raise ValueError(creation_disabled_message(bool(connection.execute('SELECT 1 FROM projects LIMIT 1').fetchone())))
            existing = [dict(row) for row in connection.execute('''SELECT p.id,p.name,d.archived_at,x.deleted_at
                FROM projects p LEFT JOIN project_details d ON d.project_id=p.id
                LEFT JOIN project_deletions x ON x.project_id=p.id
                WHERE NOT EXISTS(SELECT 1 FROM project_merges m WHERE m.source_id=p.id)''')
                if project_name_key(row['name']) in wanted]
            if existing:
                if len(names) == 1 and len(existing) == 1 and not existing[0]['archived_at'] and not existing[0]['deleted_at']:
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
            moment = datetime.now()
            batch = f'{moment:%m}月{moment:%d}日 {moment:%H:%M} 文件收件 #{receipt_id}'
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
        with self.archive._lock, ZipLease(source, movable=False) as lease:
            if not directory.is_dir():
                raise ValueError('项目工作目录不存在，ZIP 尚未收录。')
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

    @_serialized_storage
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
            self.workspaces.assert_current_source(source)
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
                if not candidates and not self.workspaces.settings()['auto_create_projects']:
                    routing.update(requires_choice=True, basis=creation_disabled_message(bool(self.list_projects())))
                    self.record_file_decision(source, 'needs-choice', routing)
                    return {'intake_choice': str(source), 'routing': routing}
                project_id = candidates[0]['id'] if len(candidates) == 1 else None
                manual = False
        except Exception as error:
            self.record_file_decision(path, 'cancelled' if isinstance(error, CancelledError) else 'failed',
                routing, project_id=project_id, manual=manual, message=str(error))
            raise
        return self.receive_file(source, project_id, cancel_event=cancel_event, progress=progress, manual=manual)

    @_serialized_storage
    def receive_file(self, path, project_id=None, *, cancel_event=None, progress=None, manual=None):
        ordinary = Path(path).suffix.casefold() != '.zip'
        routing, failure_status, created = {}, 'failed', {}
        try:
            source = _path(path, allow_network=True)
            cancelled(cancel_event)
            self.workspaces.assert_current_source(source)
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
                if plan['state'] != 'matched' and not self.workspaces.settings()['auto_create_projects']:
                    routing.update(requires_choice=True, basis=creation_disabled_message(bool(self.list_projects())))
                    self.record_file_decision(source, 'needs-choice', routing)
                    return {'intake_choice': str(source), 'routing': routing}
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
                    if not self.workspaces.settings()['auto_create_projects']:
                        failure_status = 'needs-choice'
                        raise ValueError(creation_disabled_message(bool(self.list_projects())))
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

    @_serialized_storage
    def archive_work_file(self, project_id, work_file_id, *, confirmed=False, cancel_event=None, progress=None):
        return self.workspaces.archive_existing(project_id, work_file_id,
            confirmed=confirmed, cancel_event=cancel_event)

    @_serialized_storage
    def confirm_delivery(self, project_id, zip_id=None, *, confirmed=False, cancel_event=None, progress=None):
        if zip_id is not None:
            return super().confirm_delivery(project_id, zip_id, confirmed=confirmed,
                cancel_event=cancel_event, progress=progress)
        from delivery_archive import ProjectDeliveryArchive
        return ProjectDeliveryArchive(self, self.workspaces).confirm(project_id, confirmed=confirmed,
            cancel_event=cancel_event, progress=progress)

    def preview_project_rename(self, project_id, name, merge_target_id=None, *, cancel_event=None, progress=None):
        return self.project_changes.preview(project_id, name, merge_target_id,
                                            cancel_event=cancel_event, progress=progress)

    def _commit_project_change(self, plan, operation_id, mapping, result, *, cancel_event=None):
        return self.project_changes.commit(plan, operation_id, mapping, result, cancel_event=cancel_event)

    def rename_project(self, project_id, name, *, expected_token=None, merge_target_id=None,
                       confirmed_merge=False, cancel_event=None, progress=None):
        return self.project_changes.execute(project_id, name, expected_token=expected_token,
            merge_target_id=merge_target_id, confirmed_merge=confirmed_merge,
            cancel_event=cancel_event, progress=progress)
