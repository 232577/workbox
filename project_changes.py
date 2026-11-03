"""把只读业务预览、目录事务和项目合并接在同一操作边界上。"""
from __future__ import annotations

import hashlib
import json
import os
import unicodedata
from pathlib import Path

from native_files import cancelled


def normalized_name(value):
    return ' '.join(unicodedata.normalize('NFKC', value).split()).casefold()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


class ProjectChanges:
    def __init__(self, service, directories):
        self.service, self.db, self.directories = service, service.db, directories
        self.lock = service.archive._lock

    @staticmethod
    def _reason(row):
        if row.get('merged_into_id') is not None:
            return '项目已合并，请刷新后选择保留项目。'
        if row.get('block_reason'):
            return '工作目录尚待恢复：' + row['block_reason']
        if row.get('deleted_at') is not None:
            return '项目已删除，请先恢复，再核对是否合并。'
        if row.get('archived_at') is not None:
            return '项目已归档，请先恢复，再核对是否合并。'
        return ''

    def _business(self, connection, source_id, new_name, target_id=None):
        if type(source_id) is not int or source_id <= 0:
            raise ValueError('请选择有效项目。')
        if not isinstance(new_name, str) or not 1 <= len(new_name.strip()) <= 200:
            raise ValueError('项目名称需要 1 至 200 个字符。')
        if target_id is not None and (type(target_id) is not int or target_id <= 0 or target_id == source_id):
            raise ValueError('请选择另一个保留项目。')
        name = new_name.strip()
        entries = [dict(row) for row in connection.execute('''SELECT p.*,b.name AS batch_name,
            COALESCE(d.notes,'') AS notes,d.due_date,d.archived_at,x.deleted_at,
            m.target_id AS merged_into_id,cb.reason AS block_reason,
            w.path AS workspace_path,w.group_path,pt.name AS type_name,pt.id AS type_id,pt.active AS type_active,
            COALESCE((SELECT a.included FROM project_delivery_count_actions a
                WHERE a.project_id=p.id ORDER BY a.id DESC LIMIT 1),1) AS included
            FROM projects p JOIN batches b ON b.id=p.batch_id
            LEFT JOIN project_details d ON d.project_id=p.id
            LEFT JOIN project_deletions x ON x.project_id=p.id
            LEFT JOIN project_merges m ON m.source_id=p.id
            LEFT JOIN project_change_blocks cb ON cb.project_id=p.id
            LEFT JOIN project_workspaces w ON w.project_id=p.id
            LEFT JOIN project_type_links tl ON tl.project_id=p.id
            LEFT JOIN project_types pt ON pt.id=tl.type_id ORDER BY p.id''')]
        source = next((row for row in entries if row['id'] == source_id), None)
        if source is None:
            raise KeyError('项目不存在，请刷新列表。')
        candidates = [dict(row, blocked_reason=self._reason(row)) for row in entries
                      if row['id'] != source_id and row['merged_into_id'] is None
                      and normalized_name(row['name']) == normalized_name(name)]
        target = None
        warning = self._reason(source)
        kind = 'blocked' if warning else 'rename'
        if not warning:
            if target_id is not None:
                target = next((row for row in candidates if row['id'] == target_id), None)
                if target is None:
                    raise ValueError('同名候选已变化，请重新检查并选择保留项目。')
            elif len(candidates) == 1:
                target = candidates[0]
            elif candidates:
                kind = 'choose_target'
            if target:
                warning = self._reason(target)
                kind = 'blocked' if warning else 'merge'
        ids = [source_id] + ([target['id']] if target else [])
        marks = ','.join('?' for _ in ids)
        family = [row[0] for row in connection.execute(
            f'SELECT project_id FROM project_canonical WHERE canonical_id IN ({marks}) ORDER BY project_id', ids)]
        family = sorted(set(family) | set(ids))
        fmarks = ','.join('?' for _ in family)
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        snapshot = {'source': source, 'target': target, 'candidates': candidates, 'family_ids': family}
        # 旧版可能只改过显示名。只恢复已成功收件/交付事实中保存的项目名，不猜文件夹或文件前缀。
        historical = [dict(row) for row in connection.execute(f'''SELECT project_id,project_name FROM delivery_events
            WHERE project_id IN ({fmarks}) AND project_name!=''
            UNION SELECT e.project_id,e.project_name FROM file_intake_events e
            JOIN work_files f ON f.id=e.work_file_id
            WHERE e.project_id IN ({fmarks}) AND e.status IN ('copied','duplicate') AND e.project_name!=''
            ORDER BY project_id,project_name''', [*family, *family])]
        snapshot['historical_names'] = [row for row in historical
                                        if isinstance(row['project_name'], str) and 1 <= len(row['project_name'].strip()) <= 200]
        for table in ('project_details', 'project_type_links', 'project_workspaces', 'work_files',
                      'project_delivery_archives', 'delivery_events', 'project_delivery_count_actions',
                      'project_delivery_history_markers', 'project_name_aliases'):
            if table in tables:
                snapshot[table] = [dict(row) for row in connection.execute(
                    f'SELECT * FROM {table} WHERE project_id IN ({fmarks}) ORDER BY 1', family)]
        zip_ids = [row[0] for row in connection.execute(
            f'SELECT DISTINCT zip_id FROM zip_project_links WHERE project_id IN ({marks}) ORDER BY zip_id', ids)]
        for table in ('zips', 'zip_bundle_members', 'zip_project_file_counts', 'zip_file_count_policies', 'zip_project_approvals'):
            column = 'id' if table == 'zips' else 'zip_id'
            zmarks = ','.join('?' for _ in zip_ids)
            snapshot[table] = ([dict(row) for row in connection.execute(
                f'SELECT * FROM {table} WHERE {column} IN ({zmarks}) ORDER BY 1', zip_ids)] if zip_ids else [])
        snapshot['zip_sources'] = [dict(row) for row in connection.execute(
            f'SELECT id,source_key,source_path,project_id,enabled FROM zip_sources WHERE project_id IN ({marks}) ORDER BY id', ids)]
        result_status = None
        if kind == 'merge':
            statuses = {source['status'], target['status']}
            result_status = next((status for status in ('待交付', '开发中', '已交付') if status in statuses), '待开发')
            try:
                self.db._assert_project_merge_ready(connection, source_id, target['id'])
            except (ValueError, KeyError) as error:
                kind, warning = 'blocked', str(error)
            notes = target['notes']
            if source['notes']:
                notes += ('\n\n' if notes else '') + f'【合并自 {source["name"]}（项目 #{source_id}）】\n' + source['notes']
            if len(notes) > 10000:
                kind, warning = 'blocked', '合并后备注超过 10000 个字符，请先整理备注。'
        return dict(kind=kind, source=source, target=target, candidates=candidates, new_name=name,
                    warning=warning, result_status=result_status, snapshot=snapshot,
                    business_token=fingerprint(snapshot))

    def _prepare(self, source_id, name, target_id=None, *, cancel_event=None):
        cancelled(cancel_event)
        with self.db.connect() as connection:
            connection.execute('BEGIN')
            plan = self._business(connection, source_id, name, target_id)
        plan['folder_from'] = plan['source']['workspace_path']
        plan['folder_to'] = None
        plan['copy_path'] = None
        plan['token'] = None
        if plan['kind'] not in ('rename', 'merge'):
            return plan
        try:
            directory = self.directories.preview(source_id, plan['target']['name'] if plan['target'] else plan['new_name'],
                                                 plan['target']['id'] if plan['target'] else None,
                                                 cancel_event=cancel_event)
            plan['directory_plan'] = directory
            plan['folder_to'] = (plan['target']['workspace_path'] if plan['target'] else directory['target_path'])
            plan['copy_path'] = directory['target_path'] if plan['kind'] == 'merge' else None
            # 活动监视键也不能与旧登记碰撞，不能等文件搬完才发现。
            with self.db.connect() as connection:
                sources = [dict(row) for row in connection.execute('SELECT id,source_path,source_key FROM zip_sources')]
            by_key = {row['source_key']: row['id'] for row in sources}
            for row in sources:
                path = self._map_path(row['source_path'], directory['path_mapping'])
                if path != row['source_path'] and by_key.get(self.service.snapshots._key(Path(path)), row['id']) != row['id']:
                    raise ValueError('变更后路径已有 ZIP 监视登记，请先核对该目录。')
        except (OSError, ValueError, RuntimeError) as error:
            plan.update(kind='blocked', warning=str(error))
            return plan
        cancelled(cancel_event)
        plan['token'] = fingerprint({'business': plan['business_token'], 'directory': directory['fingerprint'],
                                     'name': plan['new_name'], 'kind': plan['kind']})
        return plan

    def preview(self, source_id, name, target_id=None, *, cancel_event=None, progress=None):
        with self.lock:
            plan = self._prepare(source_id, name, target_id, cancel_event=cancel_event)
            public = {key: value for key, value in plan.items() if key not in ('snapshot', 'business_token', 'directory_plan')}
            target = plan['target']
            public['result_type_name'] = (target['type_name'] or '未分类') if target else None
            public['result_included'] = bool(target['included'] and plan['source']['included']) if target else None
            if plan['kind'] == 'merge':
                public['warning'] = ('文件直接并入保留项目工作目录；同名文件会编号保留。原工作目录保留作恢复备份。'
                                     '原交付明细保留，按同一项目统计首次交付，不新增交付记录。')
            return public

    @staticmethod
    def _map_path(value, mapping):
        if not value:
            return value
        path = Path(value)
        for old, new in mapping.items():
            if path.is_relative_to(Path(old)):
                return str(Path(new) / path.relative_to(Path(old)))
        return value

    def commit(self, plan, operation_id, mapping, fs_result, *, cancel_event=None):
        with self.db.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            current = self._business(connection, plan['source']['id'], plan['new_name'],
                                     plan['target']['id'] if plan['target'] else None)
            if current['business_token'] != plan['business_token'] or current['kind'] != plan['kind']:
                raise ValueError('项目信息已变化，请重新检查后确认。')
            if plan['kind'] == 'rename':
                for row in connection.execute('SELECT project_id,path,group_path FROM project_workspaces').fetchall():
                    path, group = self._map_path(row['path'], mapping), self._map_path(row['group_path'], mapping)
                    if (path, group) != (row['path'], row['group_path']):
                        connection.execute('UPDATE project_workspaces SET path=?,group_path=? WHERE project_id=?',
                                           (path, group, row['project_id']))
            for row in connection.execute('SELECT id,path FROM work_files').fetchall():
                path = self._map_path(row['path'], mapping)
                if path != row['path']:
                    connection.execute('UPDATE work_files SET path=? WHERE id=?', (path, row['id']))
            for row in connection.execute('SELECT id,source_path,processed_stat FROM zip_sources').fetchall():
                path = self._map_path(row['source_path'], mapping)
                processed_stat = row['processed_stat']
                if plan['kind'] == 'merge' and processed_stat:
                    directory = plan['directory_plan']
                    for root, inventory in ((directory['source_path'], directory['inventory']),
                                            (directory['target_path'], directory['target_inventory'])):
                        original = Path(row['source_path'])
                        if not original.is_relative_to(Path(root)):
                            continue
                        relative = original.relative_to(Path(root)).as_posix().casefold()
                        item = next((entry for entry in inventory if entry['kind']=='file'
                                     and entry['path'].casefold()==relative), None)
                        if item:
                            device, inode, modified = item['identity']
                            if json.loads(processed_stat) == [device, inode, item['size'], modified]:
                                from native_files import identity
                                processed_stat = json.dumps(identity(os.stat(path)))
                        break
                if path != row['source_path']:
                    connection.execute('UPDATE zip_sources SET source_path=?,source_key=?,processed_stat=?,last_stat=NULL,stable_since=NULL WHERE id=?',
                                       (path, self.service.snapshots._key(Path(path)), processed_stat, row['id']))
                elif processed_stat != row['processed_stat']:
                    connection.execute('UPDATE zip_sources SET processed_stat=?,last_stat=NULL,stable_since=NULL WHERE id=?',
                                       (processed_stat, row['id']))
            source_id = plan['source']['id']
            target_id = plan['target']['id'] if plan['target'] else None
            if plan['kind'] == 'merge':
                self.db._merge_project_records(connection, source_id, target_id, operation_id=operation_id,
                                               result_status=plan['result_status'], snapshot=plan['snapshot'])
            else:
                self.db._assert_projects_editable(connection, source_id)
                if plan['new_name'] != plan['source']['name']:
                    connection.execute("UPDATE projects SET name=?,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",
                                       (plan['new_name'], source_id))
                    connection.execute('INSERT INTO project_name_aliases(project_id,name,normalized_name,operation_id) VALUES (?,?,?,?)',
                                       (source_id, plan['source']['name'], normalized_name(plan['source']['name']), operation_id))
            for history in plan['snapshot']['historical_names']:
                alias = history['project_name'].strip()
                key = normalized_name(alias)
                if not connection.execute('SELECT 1 FROM project_name_aliases WHERE project_id=? AND normalized_name=?',
                                          (history['project_id'], key)).fetchone():
                    connection.execute('INSERT INTO project_name_aliases(project_id,name,normalized_name,operation_id) VALUES (?,?,?,?)',
                                       (history['project_id'], alias, key, operation_id))
            self.directories.verify_commit_boundary(plan['directory_plan'], fs_result, cancel_event=cancel_event)
            cancelled(cancel_event)
            connection.execute('INSERT INTO project_change_commits(operation_id,kind,source_id,target_id,new_name) VALUES (?,?,?,?,?)',
                               (operation_id, plan['kind'], source_id, target_id, plan['new_name']))

    def execute(self, source_id, name, *, expected_token=None, merge_target_id=None, confirmed_merge=False,
                cancel_event=None, progress=None):
        with self.lock:
            errors = self.service.recover_project_changes()
            if errors:
                raise RuntimeError('存在待恢复的项目目录变更，请先检查保留现场：' + errors[0]['error'])
            plan = self._prepare(source_id, name, merge_target_id, cancel_event=cancel_event)
            if plan['kind'] not in ('rename', 'merge'):
                raise ValueError(plan['warning'] or '存在多个同名项目，请检查并选择合并目标。')
            if plan['kind'] == 'merge' and (confirmed_merge is not True or merge_target_id != plan['target']['id']):
                raise ValueError('存在同名项目，请先检查并明确确认合并。')
            if expected_token is not None and expected_token != plan['token']:
                raise ValueError('项目、文件或目录已变化，请重新检查后确认。')
            if plan['kind'] == 'merge' and not expected_token:
                raise ValueError('合并需要有效的预览，请重新检查后确认。')
            cancelled(cancel_event)
            directory_result = self.directories.execute(plan['directory_plan'],
                lambda operation_id, mapping, result: self.service._commit_project_change(
                    plan, operation_id, mapping, result, cancel_event=cancel_event),
                cancel_event=cancel_event, progress=progress)
            self.service.snapshots._observed.clear()
            retained = plan['target'] or plan['source']
            message = ('项目已合并，文件位于保留项目目录；原文件和交付记录已保留。'
                       if plan['kind']=='merge' else '项目名称和工作目录已同步。')
            if directory_result.get('cleanup_error'):
                message += ' 旧目标备份清理未完成，请保留现场并检查目录变更记录。'
            return dict(operation=plan['kind'], project_id=retained['id'], project_ids=[retained['id']],
                        name=retained['name'] if plan['kind']=='merge' else plan['new_name'],
                        message=message)
