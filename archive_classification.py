"""按已登记的单项目归档事实分类；不读取文件、不改变交付及工作量事实。

构造服务只初始化本模块的表，默认关闭未来规则。批量操作需要当前预览和确认。
两个独立 hook 共用调用方事务，既不提交也不关闭连接；初始化前调用不产生写入。
"""
from __future__ import annotations

from contextlib import nullcontext
import hashlib
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from archive_facts import delivery_archive_facts


_COMPARISON_COLUMN = "comparison TEXT NOT NULL DEFAULT 'at_least' CHECK(comparison IN ('at_least','exact'))"
_TABLES = (
    f'''CREATE TABLE IF NOT EXISTS archive_classification_policy (
        id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
        minimum_files INTEGER NOT NULL CHECK(minimum_files BETWEEN 1 AND 10000),
        high_type_id INTEGER REFERENCES project_types(id), low_type_id INTEGER REFERENCES project_types(id),
        initial_event_id INTEGER NOT NULL, revision INTEGER NOT NULL DEFAULT 0, {_COMPARISON_COLUMN})''',
    '''CREATE TABLE IF NOT EXISTS archive_classification_operations (
        operation_id TEXT PRIMARY KEY, kind TEXT NOT NULL, token TEXT NOT NULL DEFAULT '',
        undo_of TEXT UNIQUE REFERENCES archive_classification_operations(operation_id),
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')))''',
    '''CREATE TABLE IF NOT EXISTS archive_classification_actions (
        id INTEGER PRIMARY KEY, operation_id TEXT NOT NULL REFERENCES archive_classification_operations(operation_id),
        project_id INTEGER NOT NULL REFERENCES projects(id), kind TEXT NOT NULL,
        old_type_id INTEGER REFERENCES project_types(id), new_type_id INTEGER REFERENCES project_types(id),
        event_id INTEGER REFERENCES delivery_events(id), outcome TEXT NOT NULL,
        reason TEXT NOT NULL DEFAULT '', facts_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')))''',
    '''CREATE INDEX IF NOT EXISTS idx_archive_classification_project
        ON archive_classification_actions(project_id,id)''',
    '''CREATE TABLE IF NOT EXISTS archive_classification_observed_events (
        event_id INTEGER PRIMARY KEY REFERENCES delivery_events(id), outcome TEXT NOT NULL,
        reason TEXT NOT NULL DEFAULT '', operation_id TEXT REFERENCES archive_classification_operations(operation_id),
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')))''',
)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _installed(connection):
    return connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='archive_classification_policy'").fetchone() is not None


def _suggest(connection, name):
    row = connection.execute('SELECT id FROM project_types WHERE normalized_name=? AND active=1', (name.casefold(),)).fetchone()
    return row[0] if row else None


def _policy(connection):
    row = dict(connection.execute('SELECT * FROM archive_classification_policy WHERE id=1').fetchone())
    row['enabled'] = bool(row['enabled'])
    row.setdefault('comparison', 'at_least')
    return row


def _operation(connection, kind, *, token='', undo_of=None):
    operation_id = uuid4().hex
    connection.execute('INSERT INTO archive_classification_operations(operation_id,kind,token,undo_of) VALUES (?,?,?,?)',
                       (operation_id, kind, token, undo_of))
    return operation_id


def _audit(connection, operation_id, kind, row, *, outcome='changed', reason='', policy=None):
    facts = row.get('fact', {})
    if policy is not None:
        facts = dict(facts, policy=policy)
    connection.execute('''INSERT INTO archive_classification_actions
        (operation_id,project_id,kind,old_type_id,new_type_id,event_id,outcome,reason,facts_json)
        VALUES (?,?,?,?,?,?,?,?,?)''', (operation_id, row['project_id'], kind, row.get('old_type_id'),
        row.get('new_type_id'), row.get('event_id'), outcome, reason, _json(facts)))


def _write_type(connection, project_id, type_id):
    if type_id is None:
        connection.execute('DELETE FROM project_type_links WHERE project_id=?', (project_id,))
    else:
        connection.execute('''INSERT INTO project_type_links(project_id,type_id) VALUES (?,?)
            ON CONFLICT(project_id) DO UPDATE SET type_id=excluded.type_id,
            updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')''', (project_id, type_id))


def _protected(connection, project_id):
    row = connection.execute('''SELECT p.id,c.canonical_id,d.archived_at,x.project_id AS deleted,b.reason
        FROM projects p JOIN project_canonical c ON c.project_id=p.id
        LEFT JOIN project_details d ON d.project_id=p.id LEFT JOIN project_deletions x ON x.project_id=p.id
        LEFT JOIN project_change_blocks b ON b.project_id=p.id WHERE p.id=?''', (project_id,)).fetchone()
    if row is None:
        return '项目不存在。'
    if row['canonical_id'] != project_id:
        return '项目已合并，不能修改旧项目。'
    if row['reason'] is not None:
        return '项目目录待恢复：' + row['reason']
    if row['deleted'] is not None:
        return '项目已删除。'
    if row['archived_at'] is not None:
        return '项目已归档。'
    return ''


def _snapshot(connection, db):
    policy = _policy(connection)
    threshold = policy['minimum_files']
    catalog = [dict(row) for row in connection.execute('SELECT * FROM project_types ORDER BY id')]
    types = {row['id']: row for row in catalog}
    events = db.delivery_history(connection=connection)['events']
    latest = {}
    for event in events:
        if not event['voided']:
            latest.setdefault(event['project_id'], event)
    facts = delivery_archive_facts(connection, latest.values())
    rows = []
    for project in connection.execute('''SELECT p.id,p.name,l.type_id FROM projects p
        JOIN project_canonical c ON c.project_id=p.id AND c.canonical_id=p.id
        LEFT JOIN project_type_links l ON l.project_id=p.id ORDER BY p.id'''):
        project_id = project['id']
        event = latest.get(project_id)
        fact = facts[event['id']] if event else dict(known=False, count=None, reason='项目没有有效交付。')
        count = fact['count']
        target_id = None
        if fact['known']:
            matches = count == threshold if policy['comparison'] == 'exact' else count >= threshold
            target_id = policy['high_type_id'] if matches else policy['low_type_id']
        old = types.get(project['type_id'], {})
        target = types.get(target_id, {})
        reason = _protected(connection, project_id)
        if not reason and not fact['known']:
            reason = fact['reason'] or '归档文件数未知。'
        if not reason and (not target or not target['active']):
            reason = '目标类型缺失或已停用。'
        row = dict(project_id=project_id, project_name=project['name'],
            event_id=event['id'] if event else None, last_action_id=event['last_action_id'] if event else None,
            old_type_id=project['type_id'], old_type_name=old.get('name', '未分类'),
            new_type_id=target_id, new_type_name=target.get('name', ''), count=count, fact=fact,
            eligible=not bool(reason), changed=not bool(reason) and project['type_id'] != target_id,
            skip_reason=reason)
        rows.append(row)
    # 覆盖原始来源、类型链接及动作版本；同值人工选择也会使令牌过期。
    sources = {}
    for table in ('zip_project_file_counts', 'zip_bundles', 'zip_bundle_members', 'zips',
                  'project_delivery_archives', 'project_canonical', 'project_type_links',
                  'project_change_blocks', 'project_details', 'project_deletions',
                  'archive_classification_actions', 'archive_classification_observed_events'):
        if connection.execute('SELECT 1 FROM sqlite_master WHERE name=?', (table,)).fetchone():
            sources[table] = sorted((_json(dict(row)) for row in connection.execute('SELECT * FROM ' + table)))
    token = hashlib.sha256(_json(dict(policy=policy, catalog=catalog, events=events, rows=rows, sources=sources)).encode('utf-8')).hexdigest()
    return dict(token=token, rows=rows, changed_count=sum(row['changed'] for row in rows),
                skipped=[row for row in rows if not row['eligible']], policy=policy)


def _result(operation_id=None, changed=None, skipped=None):
    changed = changed or []
    return dict(operation_id=operation_id, changed_count=len(changed), changed_project_ids=changed, skipped=skipped or [])


class ArchiveClassification:
    def __init__(self, db):
        self.db = db
        self._lock = getattr(db, '_operation_lock', None) or nullcontext()
        with self._lock, db.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            for statement in _TABLES:
                connection.execute(statement)
            columns = connection.execute('PRAGMA table_info(archive_classification_policy)')
            if not any(column['name'] == 'comparison' for column in columns.fetchall()):
                connection.execute(f'ALTER TABLE archive_classification_policy ADD COLUMN {_COMPARISON_COLUMN}')
            for table in ('archive_classification_operations', 'archive_classification_actions', 'archive_classification_observed_events'):
                for action in ('UPDATE', 'DELETE'):
                    connection.execute(f'''CREATE TRIGGER IF NOT EXISTS {table}_no_{action.lower()}
                        BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'归档分类审计仅允许追加。'); END''')
            connection.execute('''INSERT OR IGNORE INTO archive_classification_policy
                (id,enabled,minimum_files,high_type_id,low_type_id,initial_event_id,comparison)
                VALUES (1,0,16,?,?,(SELECT COALESCE(MAX(id),0) FROM delivery_events),'exact')''',
                (_suggest(connection, '可运行'), _suggest(connection, '生成材料')))

    def policy(self):
        with self.db.connect() as connection:
            return _policy(connection)

    def recent_operations(self, limit=30):
        """只读列出确有变更的批量分类；自动事件不会挤掉可撤销批次。"""
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError('分类历史条数需为 1—200。')
        with self.db.connect() as connection:
            connection.execute('BEGIN')
            rows = connection.execute('''SELECT o.operation_id,o.kind,o.created_at,
                (SELECT COUNT(*) FROM archive_classification_actions a
                 WHERE a.operation_id=o.operation_id AND a.outcome='changed') AS changed_count,
                u.operation_id AS undo_operation_id
                FROM archive_classification_operations o
                LEFT JOIN archive_classification_operations u ON u.undo_of=o.operation_id
                WHERE o.kind='batch' AND EXISTS (SELECT 1 FROM archive_classification_actions a
                    WHERE a.operation_id=o.operation_id AND a.outcome='changed')
                ORDER BY o.rowid DESC LIMIT ?''', (limit,)).fetchall()
            return [dict(dict(row), undone=row['undo_operation_id'] is not None) for row in rows]

    def set_policy(self, enabled, minimum_files=4, high_type_id=None, low_type_id=None, *, comparison='at_least'):
        if type(enabled) is not bool or type(minimum_files) is not int or not 1 <= minimum_files <= 10000:
            raise ValueError('分类开关或文件数阈值无效。')
        if type(comparison) is not str or comparison not in ('at_least', 'exact'):
            raise ValueError('归档文件数比较方式无效。')
        with self._lock, self.db.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            values = [high_type_id, low_type_id]
            for index, name in enumerate(('可运行', '生成材料')):
                if values[index] is None:
                    values[index] = _suggest(connection, name)
                elif type(values[index]) is not int or values[index] <= 0 or not connection.execute(
                        'SELECT 1 FROM project_types WHERE id=?', (values[index],)).fetchone():
                    raise ValueError('目标项目类型不存在。')
            connection.execute('''UPDATE archive_classification_policy SET enabled=?,minimum_files=?,
                high_type_id=?,low_type_id=?,comparison=?,revision=revision+1 WHERE id=1''',
                (int(enabled), minimum_files, *values, comparison))
            return _policy(connection)

    def preview(self):
        with self.db.connect() as connection:
            connection.execute('BEGIN')
            return _snapshot(connection, self.db)

    def apply(self, token, confirmed=False, enable_future=True):
        if confirmed is not True:
            raise ValueError('请明确确认批量归档分类。')
        if type(enable_future) is not bool:
            raise ValueError('未来分类开关无效。')
        with self._lock, self.db.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            preview = _snapshot(connection, self.db)
            if not isinstance(token, str) or token != preview['token']:
                raise ValueError('分类预览已过期，请重新预览后确认。')
            operation_id = _operation(connection, 'batch', token=token)
            changed = []
            for row in preview['rows']:
                if row['changed']:
                    _write_type(connection, row['project_id'], row['new_type_id'])
                    _audit(connection, operation_id, 'batch', row, policy=preview['policy'])
                    changed.append(row['project_id'])
            if enable_future and not preview['policy']['enabled']:
                connection.execute('UPDATE archive_classification_policy SET enabled=1,revision=revision+1 WHERE id=1')
            return _result(operation_id, changed, preview['skipped'])

    def undo(self, operation_id, confirmed=False):
        if confirmed is not True:
            raise ValueError('请明确确认撤销本次分类。')
        with self._lock, self.db.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            operation = connection.execute('SELECT * FROM archive_classification_operations WHERE operation_id=?', (operation_id,)).fetchone()
            if not operation or operation['kind'] not in ('batch', 'auto'):
                raise ValueError('分类操作不存在或不能撤销。')
            existing = connection.execute('SELECT operation_id FROM archive_classification_operations WHERE undo_of=?', (operation_id,)).fetchone()
            if existing:
                return _result(existing[0])
            undo_id = _operation(connection, 'undo', undo_of=operation_id)
            changed, skipped = [], []
            for action in connection.execute("SELECT * FROM archive_classification_actions WHERE operation_id=? AND outcome='changed' ORDER BY id", (operation_id,)).fetchall():
                project_id = action['project_id']
                reason = _protected(connection, project_id)
                link = connection.execute('SELECT type_id FROM project_type_links WHERE project_id=?', (project_id,)).fetchone()
                current = link[0] if link else None
                later_manual = connection.execute("SELECT 1 FROM archive_classification_actions WHERE project_id=? AND id>? AND kind='manual'", (project_id, action['id'])).fetchone()
                later_changed = connection.execute('''SELECT 1 FROM archive_classification_actions
                    WHERE project_id=? AND id>? AND operation_id!=? AND outcome='changed'
                    AND old_type_id IS NOT new_type_id''', (project_id, action['id'], operation_id)).fetchone()
                if not reason and later_manual:
                    reason = '此后已有人工类型选择。'
                if not reason and later_changed:
                    reason = '此后已有其他类型变更。'
                if not reason and current != action['new_type_id']:
                    reason = '当前类型已改变。'
                if not reason and action['old_type_id'] is not None:
                    old = connection.execute('SELECT active FROM project_types WHERE id=?', (action['old_type_id'],)).fetchone()
                    if not old or not old[0]:
                        reason = '原类型缺失或已停用。'
                row = dict(project_id=project_id, old_type_id=current, new_type_id=action['old_type_id'], event_id=action['event_id'])
                if reason:
                    _audit(connection, undo_id, 'undo', row, outcome='skipped', reason=reason)
                    skipped.append(dict(project_id=project_id, skip_reason=reason))
                else:
                    _write_type(connection, project_id, row['new_type_id'])
                    _audit(connection, undo_id, 'undo', row)
                    changed.append(project_id)
            return _result(undo_id, changed, skipped)


def classify_new_delivery(connection, event_id):
    """只在新增交付事件的原事务末尾调用；不能用于刷新、启动或撤销/恢复。"""
    if not _installed(connection):
        return _result(skipped=[dict(event_id=event_id, skip_reason='归档分类规则尚未初始化。')])
    if not connection.in_transaction:
        raise ValueError('自动归档分类需要新增交付的原事务。')
    if connection.execute('SELECT 1 FROM archive_classification_observed_events WHERE event_id=?', (event_id,)).fetchone():
        return _result(skipped=[dict(event_id=event_id, skip_reason='该事件已处理。')])
    event = connection.execute('SELECT * FROM delivery_events WHERE id=?', (event_id,)).fetchone()
    if event is None:
        raise ValueError('交付事件不存在。')
    policy = _policy(connection)
    reason = ''
    if event_id <= policy['initial_event_id']:
        reason = '初始化前的历史事件不自动回填。'
    elif not policy['enabled']:
        reason = '未来自动分类尚未启用。'
    elif connection.execute('SELECT 1 FROM delivery_event_actions WHERE event_id=?', (event_id,)).fetchone():
        reason = '已有撤销或恢复管理动作，不属于新增有效交付。'
    if reason:
        connection.execute('INSERT INTO archive_classification_observed_events(event_id,outcome,reason) VALUES (?,\'skipped\',?)', (event_id, reason))
        return _result(skipped=[dict(event_id=event_id, skip_reason=reason)])
    from database import Database
    # 构造只读查询宿主，不能调用 Database.__init__ 执行迁移或提交调用方事务。
    db = Database.__new__(Database)
    db.path = Path(next(row[2] for row in connection.execute('PRAGMA database_list') if row[1] == 'main')).resolve()
    preview = _snapshot(connection, db)
    canonical = connection.execute('SELECT canonical_id FROM project_canonical WHERE project_id=?', (event['project_id'],)).fetchone()[0]
    row = next(row for row in preview['rows'] if row['project_id'] == canonical)
    reason = row['skip_reason']
    if row['event_id'] != event_id:
        reason = '事件不是该项目最近有效交付。'
    operation_id = _operation(connection, 'auto', token=preview['token'])
    changed = []
    if not reason and row['changed']:
        _write_type(connection, canonical, row['new_type_id'])
        _audit(connection, operation_id, 'auto', row, policy=policy)
        changed.append(canonical)
    else:
        _audit(connection, operation_id, 'auto', row, outcome='skipped' if reason else 'unchanged',
               reason=reason, policy=policy)
    connection.execute('INSERT INTO archive_classification_observed_events(event_id,outcome,reason,operation_id) VALUES (?,?,?,?)',
                       (event_id, 'changed' if changed else 'skipped' if reason else 'unchanged', reason, operation_id))
    return _result(operation_id, changed, [dict(project_id=canonical, event_id=event_id, skip_reason=reason)] if reason else [])


def record_manual_type_selection(connection, project_ids, before, type_id):
    """人工入口即使选择同值也记录意图；before 为 {project_id: 原type_id}。"""
    if not _installed(connection):
        return None
    operation_id = _operation(connection, 'manual')
    for project_id in sorted(set(project_ids)):
        _audit(connection, operation_id, 'manual', dict(project_id=project_id,
            old_type_id=before.get(project_id), new_type_id=type_id), outcome='selected')
    return operation_id
