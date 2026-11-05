"""本次任务、材料版本和人工交接的独立追加式关联层。"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path

from database import Database
from task_schema import initialize


INTERNAL_TASK_PREFIX = re.compile(r'^\s*G\d{8}-\d{3,}-\d{2,}', re.MULTILINE)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def _day(moment):
    return datetime.fromisoformat(moment.replace('Z', '+00:00')).astimezone(
        timezone(timedelta(hours=8))).date().isoformat()


def _ids(values):
    if isinstance(values, (str, bytes)):
        raise ValueError('请选择任务。')
    try:
        result = list(dict.fromkeys(values))
    except TypeError as error:
        raise ValueError('请选择任务。') from error
    if not result or len(result) > 500 or any(type(i) is not int or i <= 0 for i in result):
        raise ValueError('请选择有效任务，单次最多500项。')
    return result


def _event(connection, event_id):
    row = connection.execute('''SELECT e.*,c.canonical_id,
        COALESCE(a.id,0) AS action_version,COALESCE(a.voided,0) AS voided
        FROM delivery_events e JOIN project_canonical c ON c.project_id=e.project_id
        LEFT JOIN delivery_event_actions a ON a.id=(
            SELECT MAX(id) FROM delivery_event_actions WHERE event_id=e.id)
        WHERE e.id=?''', (event_id,)).fetchone()
    if row is None:
        raise ValueError('交付记录不存在，请刷新。')
    return dict(row)


def _has_archive(delivery):
    return isinstance(delivery['archive_path'], str) and bool(delivery['archive_path'].strip())


def _task_states(connection):
    tasks = []
    for row in connection.execute('''SELECT t.*,c.canonical_id,p.name AS current_name
            FROM task_items t JOIN project_canonical c ON c.project_id=t.project_id
            JOIN projects p ON p.id=c.canonical_id ORDER BY t.id'''):
        item = dict(row)
        actions = [dict(a) for a in connection.execute(
            'SELECT * FROM task_actions WHERE task_id=? ORDER BY id', (row['id'],))]
        move = next((a for a in reversed(actions) if a['kind'] == 'move'), None)
        state = next((a for a in reversed(actions) if a['kind'] in ('withdraw', 'reopen')), None)
        reopen = next((a for a in reversed(actions) if a['kind'] == 'reopen'), None)
        last_handoff = next((a for a in reversed(actions) if a['kind'] in (
            'handoff', 'undo-handoff', 'withdraw', 'reopen')), None)
        binding = connection.execute('SELECT * FROM task_delivery_bindings WHERE task_id=? ORDER BY id DESC LIMIT 1', (row['id'],)).fetchone()
        withdrawn = bool(state and state['kind'] == 'withdraw')
        ready, handed, event_id, path = False, False, None, ''
        if binding:
            version = _event(connection, binding['event_id'])
            event_id, path = version['id'], version['archive_path']
            ready = (not withdrawn and not version['voided'] and _has_archive(version)
                     and version['action_version'] == binding['event_action_id']
                     and (not state or state['id'] <= binding['task_action_id']))
            handed = bool(ready and last_handoff and last_handoff['kind'] == 'handoff'
                          and last_handoff['binding_id'] == binding['id'])
        # 人工选择可明确使用较旧版本；仅清除当时已知的歧义，不能让后来新事件失去提示。
        # 旧开发schema读取保持保守语义，列的补充只发生在显式服务初始化。
        observation_floor = (max(binding['event_id'], binding['observed_event_floor']
            if 'observed_event_floor' in binding.keys() else 0) if binding else 0)
        ambiguous = any(row['id'] in json.loads(obs['candidate_task_ids'])
            and (not binding or obs['event_id'] > observation_floor)
            for obs in connection.execute("SELECT event_id,candidate_task_ids FROM task_delivery_observations WHERE result='ambiguous'"))
        needs_review = bool(not withdrawn and not handed and (
            ambiguous or (row['history_review'] and not binding)
            or (binding and not ready)))
        item.update(id=row['id'], project_id=row['canonical_id'], original_project_id=row['project_id'],
            original_project_name=row['project_name'], project_name=row['current_name'],
            group_id=move['target_group_id'] if move else row['original_group_id'],
            material_ready=bool(ready), handed_over=bool(handed), withdrawn=withdrawn,
            ever_handed_over=any(a['kind'] == 'handoff' for a in actions),
            event_id=event_id, archive_path=path, binding_id=binding['id'] if binding else None,
            delivery_floor=max(row['delivery_floor'], reopen['delivery_floor'] if reopen else 0),
            needs_review=needs_review)
        tasks.append(item)
    return tasks


def _bind(connection, item, delivery, origin):
    version = connection.execute('SELECT COALESCE(MAX(id),0) FROM task_actions WHERE task_id=?', (item['id'],)).fetchone()[0]
    floor = (connection.execute('SELECT COALESCE(MAX(id),0) FROM delivery_events').fetchone()[0]
             if origin == 'explicit' else delivery['id'])
    return connection.execute('''INSERT INTO task_delivery_bindings(
        task_id,event_id,event_action_id,task_action_id,origin,observed_event_floor,created_at) VALUES (?,?,?,?,?,?,?)''',
        (item['id'], delivery['id'], delivery['action_version'], version, origin, floor, _now())).lastrowid


def observe_delivery_event(connection, event_id):
    """交付事务内调用一次；歧义结果永久保留，不因后来任务变化重新猜测。"""
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_items'").fetchone():
        return
    if connection.execute('SELECT 1 FROM task_delivery_observations WHERE event_id=?', (event_id,)).fetchone():
        return
    delivery = _event(connection, event_id)
    candidates = [item for item in _task_states(connection)
                  if item['project_id'] == delivery['canonical_id']
                  and item['delivery_floor'] < event_id
                  and not item['withdrawn'] and not item['handed_over']]
    result = ('ignored' if delivery['voided'] or not _has_archive(delivery) or not candidates
              else 'bound' if len(candidates) == 1 else 'ambiguous')
    connection.execute('INSERT INTO task_delivery_observations VALUES (?,?,?,?)',
        (event_id, json.dumps([t['id'] for t in candidates]), result, _now()))
    if result == 'bound':
        _bind(connection, candidates[0], delivery, 'automatic')


def _read_messages(connection, group_id):
    result = []
    for row in connection.execute('''SELECT m.*,COALESCE(r.state,'historical') AS state
        FROM task_group_messages m LEFT JOIN intake_receipts r ON r.id=m.receipt_id
        WHERE group_id=? ORDER BY sequence''', (group_id,)):
        item = dict(row)
        item['task_ids'] = [r[0] for r in connection.execute('SELECT task_id FROM task_message_links WHERE message_id=? ORDER BY task_id', (row['id'],))]
        result.append(item)
    return result


def read_task_snapshot(connection, *, query='', open_only=True, received_day=None):
    """共用调用方已开始的读取事务；不初始化服务、表、文件或任务绑定。"""
    if not isinstance(connection, sqlite3.Connection) or not connection.in_transaction:
        raise ValueError('任务组共用连接需要已开始的读取事务。')
    if not isinstance(query, str) or len(query) > 1000:
        raise ValueError('搜索文字过长。')
    if received_day is not None:
        received_day = str(received_day)
        try:
            datetime.strptime(received_day, '%Y-%m-%d')
        except ValueError as error:
            raise ValueError('收件日期无效。') from error
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_groups'").fetchone():
        return []
    states = _task_states(connection)
    result = []
    for row in connection.execute('SELECT * FROM task_groups ORDER BY received_day DESC,day_sequence DESC'):
        group = dict(row)
        for action in connection.execute('SELECT kind,value FROM task_group_actions WHERE group_id=? ORDER BY id', (row['id'],)):
            group['title' if action['kind'] == 'title' else 'color'] = action['value']
        tasks = [t for t in states if t['group_id'] == row['id']]
        messages = _read_messages(connection, row['id'])
        review_count = sum(m['state'] == 'needs-review' for m in messages)
        counts = dict(total=len(tasks), ready=sum(t['material_ready'] for t in tasks),
            handed=sum(t['handed_over'] for t in tasks), withdrawn=sum(t['withdrawn'] for t in tasks),
            needs_review=sum(t['needs_review'] for t in tasks))
        counts['pending'] = sum(not t['withdrawn'] and not t['handed_over'] and not t['needs_review'] for t in tasks)
        is_open = bool(review_count or any(not t['withdrawn'] and not t['handed_over'] for t in tasks))
        state = ('open' if is_open else 'withdrawn' if tasks and counts['withdrawn'] == len(tasks)
                 else 'closed' if tasks else 'empty')
        if open_only and not is_open:
            continue
        if received_day and not any(_day(m['received_at']) == received_day for m in messages):
            continue
        search = ' '.join([group['code'], group['title']] + [t['code'] + ' ' + t['project_name'] for t in tasks]
                          + [m['raw_text'] for m in messages])
        if query.strip().casefold() not in search.casefold():
            continue
        group.update(group_id=row['id'], group_code=row['code'], tasks=tasks, counts=counts,
            message_review_count=review_count, state=state,
            last_received_at=messages[-1]['received_at'] if messages else row['created_at'])
        result.append(group)
    return result


class TaskGroups:
    def __init__(self, service):
        self.service, self.db = service, service.db
        with self.db.connect() as connection:
            initialize(connection)

    @staticmethod
    def _group(connection, group_id):
        if type(group_id) is not int or group_id <= 0:
            raise ValueError('任务组编号无效。')
        row = connection.execute('SELECT * FROM task_groups WHERE id=?', (group_id,)).fetchone()
        if row is None:
            raise ValueError('任务组不存在，请刷新。')
        return dict(row)

    @staticmethod
    def _create_group(connection, moment, *, history_batch_id=None):
        day = _day(moment)
        sequence = connection.execute('SELECT COALESCE(MAX(day_sequence),0)+1 FROM task_groups WHERE received_day=?', (day,)).fetchone()[0]
        colors = ('#507080', '#78658A', '#5D8070', '#96734E', '#697AA0', '#887080')
        code = f'G{day.replace("-", "")}-{sequence:03d}'
        gid = connection.execute('''INSERT INTO task_groups(code,received_day,day_sequence,color,created_at,history_batch_id)
            VALUES (?,?,?,?,?,?)''', (code, day, sequence, colors[(sequence - 1) % len(colors)], moment, history_batch_id)).lastrowid
        return TaskGroups._group(connection, gid)

    def _attach_tasks(self, connection, message_id, project_ids, *, history=False):
        message = connection.execute('SELECT * FROM task_group_messages WHERE id=?', (message_id,)).fetchone()
        group = self._group(connection, message['group_id'])
        existing = _task_states(connection)
        floor = connection.execute('SELECT COALESCE(MAX(id),0) FROM delivery_events').fetchone()[0]
        task_ids = []
        for project_id in project_ids:
            canonical = Database._canonical_project_id(connection, project_id)
            found = next((t for t in existing if t['group_id'] == group['id'] and t['project_id'] == canonical), None)
            if found:
                tid = found['id']
            else:
                project = connection.execute('SELECT name FROM projects WHERE id=?', (project_id,)).fetchone()
                seq = connection.execute('SELECT COALESCE(MAX(sequence),0)+1 FROM task_items WHERE original_group_id=?', (group['id'],)).fetchone()[0]
                tid = connection.execute('''INSERT INTO task_items(code,original_group_id,sequence,
                    project_id,project_name,created_at,delivery_floor,history_review) VALUES (?,?,?,?,?,?,?,?)''',
                    (f'{group["code"]}-{seq:02d}', group['id'], seq, project_id, project['name'],
                     message['received_at'], floor, int(history))).lastrowid
                existing.append(dict(id=tid, group_id=group['id'], project_id=canonical))
            connection.execute('INSERT OR IGNORE INTO task_message_links(message_id,task_id) VALUES (?,?)', (message_id, tid))
            task_ids.append(tid)
        return list(dict.fromkeys(task_ids))

    def receipt_resolved(self, connection, receipt_id, project_ids):
        """沿用旧核对事务，原消息与组身份不发生迁移。"""
        result = {}
        for message in connection.execute('SELECT id,group_id FROM task_group_messages WHERE receipt_id=? ORDER BY id', (receipt_id,)):
            tids = self._attach_tasks(connection, message['id'], project_ids)
            group = self._group(connection, message['group_id'])
            result = dict(group_id=group['id'], group_code=group['code'], task_ids=tids)
        return result

    def _result(self, connection, message, *, duplicate=False):
        receipt = connection.execute('SELECT * FROM intake_receipts WHERE id=?', (message['receipt_id'],)).fetchone()
        group = self._group(connection, message['group_id'])
        task_rows = connection.execute('''SELECT t.id,t.project_id FROM task_message_links l
            JOIN task_items t ON t.id=l.task_id WHERE l.message_id=? ORDER BY t.id''', (message['id'],)).fetchall()
        if receipt:
            result = self.service._receipt(receipt, duplicate=duplicate)
        else:
            # 历史导入可能来自早期messages，无旧收件回执；只定位原组，不伪造回执。
            result = dict(id=None, receipt_id=None, raw_text=message['raw_text'], state='committed',
                reason='历史来源已保留，本次交付需核对。', batch_id=group['history_batch_id'],
                candidates=[], project_ids=list(dict.fromkeys(r['project_id'] for r in task_rows)), duplicate=duplicate)
        result.update(group_id=group['id'], group_code=group['code'], task_ids=[r['id'] for r in task_rows])
        return result

    @staticmethod
    def _coded_intake(connection, text, group_id):
        """内部清单只按确切任务号及名称核对，不能回落成普通项目名猜测。"""
        by_code = {t['code']: t for t in _task_states(connection)}
        selected = []
        for line in (line.strip() for line in text.splitlines() if line.strip()):
            match = re.fullmatch(r'(G\d{8}-\d{3,}-\d{2,})\s+(.+)', line)
            if not match or match[1] not in by_code:
                return [], '内部任务号未知或清单格式不完整，请核对原任务号和项目名称。'
            task = by_code[match[1]]
            if match[2].strip() not in (task['project_name'], task['original_project_name']):
                return [], '内部任务号与项目名称不一致，请核对后再收件。'
            if task['id'] not in {t['id'] for t in selected}:
                selected.append(task)
        if group_id is None and len({t['group_id'] for t in selected}) != 1:
            return [], '内部任务号涉及不同任务组，请明确选择续入的任务组后再收件。'
        return selected, ''

    def receive(self, text, *, group_id=None, new_round=False, request_id=None):
        from desktop_service import classify_text
        from text_intake import TextProjectConflict
        projects, reason = classify_text(text)
        coded = bool(INTERNAL_TASK_PREFIX.search(text))
        if type(new_round) is not bool or (new_round and group_id is not None):
            raise ValueError('新一轮任务请创建新组，续入消息请指定原组。')
        if request_id is not None and (not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 200):
            raise ValueError('收件重试标识无效。')
        fingerprint = hashlib.sha256(text.strip().encode('utf-8')).hexdigest()
        payload = json.dumps([fingerprint, group_id, new_round])
        changes = {}
        with self.service.archive._lock:
            if not self.service._project_changes_ready:
                self.service.recover_project_changes()
            if (any(e.get('global_block') for e in self.service.project_change_errors)
                    or self.service.workspaces.document_versions.blocked):
                raise RuntimeError('项目目录或文档版本记录需要恢复，暂不能收件。')
            with self.db.connect() as connection:
                connection.execute('BEGIN IMMEDIATE')
                prior = None
                command = None
                if request_id:
                    command = connection.execute('SELECT * FROM task_receive_requests WHERE request_id=?', (request_id,)).fetchone()
                    if command:
                        if command['payload'] != payload:
                            raise ValueError('收件重试标识已用于其他消息，请刷新。')
                        prior = connection.execute('SELECT * FROM task_group_messages WHERE id=?', (command['message_id'],)).fetchone()
                    else:
                        prior = connection.execute('SELECT * FROM task_group_messages WHERE request_id=?', (request_id,)).fetchone()
                    if prior and not command and prior['request_payload'] != payload:
                        raise ValueError('收件重试标识已用于其他消息，请刷新。')
                if not prior and not new_round:
                    sql = 'SELECT * FROM task_group_messages WHERE fingerprint=?'
                    params = [fingerprint]
                    if group_id is not None:
                        self._group(connection, group_id)
                        sql += ' AND group_id=?'
                        params.append(group_id)
                    prior = connection.execute(sql + ' ORDER BY id LIMIT 1', params).fetchone()
                if prior:
                    if request_id and not command:
                        connection.execute('INSERT INTO task_receive_requests VALUES (?,?,?,?)', (request_id, prior['id'], payload, _now()))
                    # 目录登记另有写事务，先释放本次收件事务再复用旧回执。
                    connection.commit()
                    result = self._result(connection, prior, duplicate=True)
                    return result if coded else self.service._attach_workspaces(result)
                coded_tasks = []
                if coded:
                    from parser import ParsedProject
                    coded_tasks, reason = self._coded_intake(connection, text, group_id)
                    projects = [ParsedProject(t['project_name']) for t in coded_tasks]
                    if not reason and group_id is None and not new_round:
                        group_id = coded_tasks[0]['group_id']
                moment = _now()
                group = self._group(connection, group_id) if group_id is not None else self._create_group(connection, moment)
                receipt = (None if coded else connection.execute('SELECT * FROM intake_receipts WHERE fingerprint=?', (fingerprint,)).fetchone())
                if receipt is None or new_round:
                    key = 'task:' + uuid.uuid4().hex if new_round or coded else fingerprint
                    rid = connection.execute('''INSERT INTO intake_receipts(fingerprint,raw_text,candidates,state,reason,batch_name)
                        VALUES (?,?,?,'needs-review',?,?)''', (key, text,
                        json.dumps([dict(name=p.name, urgent=p.urgent) for p in projects], ensure_ascii=False),
                        reason, '')).lastrowid
                    # 任务组只是逻辑关联；物理批次继续沿用原收件命名规则。
                    local_moment = datetime.now()
                    batch_name = f'{local_moment:%m}月{local_moment:%d}日 {local_moment:%H:%M} 收件 #{rid}'
                    connection.execute('UPDATE intake_receipts SET batch_name=? WHERE id=?', (batch_name, rid))
                    receipt = connection.execute('SELECT * FROM intake_receipts WHERE id=?', (rid,)).fetchone()
                    if not reason:
                        if coded:
                            # 编号已证明身份；只追加逻辑来源，不新建批次、项目或物理工作目录。
                            ids = list(dict.fromkeys(t['project_id'] for t in coded_tasks))
                            connection.execute("UPDATE intake_receipts SET state='committed',project_ids=?,reason=? WHERE id=?",
                                (json.dumps(ids), '沿用内部任务号对应的已有项目，项目与原任务状态保持。', rid))
                            changes = dict(created_project_ids=[], reused_project_ids=ids)
                        else:
                            try:
                                changes = self.service._commit_text(connection, receipt, projects)
                            except TextProjectConflict as error:
                                connection.execute('UPDATE intake_receipts SET reason=? WHERE id=?', (str(error), rid))
                    receipt = connection.execute('SELECT * FROM intake_receipts WHERE id=?', (rid,)).fetchone()
                sequence = connection.execute('SELECT COALESCE(MAX(sequence),0)+1 FROM task_group_messages WHERE group_id=?', (group['id'],)).fetchone()[0]
                mid = connection.execute('''INSERT INTO task_group_messages(group_id,receipt_id,raw_text,
                    fingerprint,received_at,sequence,request_id,request_payload) VALUES (?,?,?,?,?,?,?,?)''',
                    (group['id'], receipt['id'], text, fingerprint, moment, sequence, request_id, payload)).lastrowid
                if request_id:
                    connection.execute('INSERT INTO task_receive_requests VALUES (?,?,?,?)', (request_id, mid, payload, moment))
                if receipt['state'] == 'committed':
                    if coded and not new_round and all(t['group_id'] == group['id'] for t in coded_tasks):
                        for task in coded_tasks:
                            connection.execute('INSERT INTO task_message_links(message_id,task_id) VALUES (?,?)', (mid, task['id']))
                    else:
                        self._attach_tasks(connection, mid, json.loads(receipt['project_ids']))
                message = connection.execute('SELECT * FROM task_group_messages WHERE id=?', (mid,)).fetchone()
                result = self._result(connection, message)
            result.update(changes)
            return result if coded else self.service._attach_workspaces(result)

    def snapshot(self, *, query='', open_only=True, received_day=None, connection=None):
        supplied = connection is not None
        if supplied:
            if not isinstance(connection, sqlite3.Connection) or not connection.in_transaction:
                raise ValueError('任务组共用连接需要已开始的读取事务。')
            main_path = next((row[2] for row in connection.execute('PRAGMA database_list') if row[1] == 'main'), '')
            if not main_path or Path(main_path).resolve() != self.db.path:
                raise ValueError('任务组连接不是当前工作箱数据库。')
        with (nullcontext(connection) if supplied else self.db.connect()) as connection:
            if not supplied:
                connection.execute('BEGIN')
            return read_task_snapshot(connection, query=query, open_only=open_only, received_day=received_day)

    @staticmethod
    def _messages(connection, group_id):
        return _read_messages(connection, group_id)

    def messages(self, group_id):
        with self.db.connect() as connection:
            self._group(connection, group_id)
            return self._messages(connection, group_id)

    @staticmethod
    def _selected(connection, task_ids, *, editable=False):
        ids = _ids(task_ids)
        found = {t['id']: t for t in _task_states(connection)}
        if any(i not in found for i in ids):
            raise ValueError('任务不存在，请刷新。')
        selected = [found[i] for i in ids]
        if editable:
            for pid in {t['project_id'] for t in selected}:
                Database._assert_projects_editable(connection, pid)
        return selected

    def delivery_choices(self, task_ids):
        with self.db.connect() as connection:
            connection.execute('BEGIN')
            selected = self._selected(connection, task_ids)
            project_ids = {t['project_id'] for t in selected}
            if len(project_ids) != 1:
                return []
            return [e for e in self.db.delivery_history(project_id=next(iter(project_ids)), connection=connection)['events']
                    if not e['voided'] and _has_archive(e)]

    def bind_delivery(self, task_ids, event_id, confirmed=False):
        if not confirmed:
            raise ValueError('请确认使用所选交付记录。')
        with self.db.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            selected = self._selected(connection, task_ids, editable=True)
            delivery = _event(connection, event_id)
            if not _has_archive(delivery):
                raise ValueError('交付记录没有归档位置，请先收录材料后再关联。')
            if delivery['voided'] or any(t['withdrawn'] or t['project_id'] != delivery['canonical_id'] for t in selected):
                raise ValueError('交付记录失效、任务已撤回或项目不一致，请刷新。')
            changed = 0
            for item in selected:
                if item['material_ready'] and item['event_id'] == event_id and not item['needs_review']:
                    continue
                if item['handed_over']:
                    raise ValueError('任务已交接，请先重新打开后更换交付版本。')
                _bind(connection, item, delivery, 'explicit')
                changed += 1
            return dict(task_ids=[t['id'] for t in selected], changed_count=changed)

    def _action(self, task_ids, kind, *, confirmed=False, reason='', target_group_id=None,
                expected_bindings=None):
        if not confirmed:
            raise ValueError('请确认所选任务操作。')
        if not isinstance(reason, str) or len(reason) > 1000:
            raise ValueError('更正说明不能超过1000字。')
        with self.db.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            selected = self._selected(connection, task_ids, editable=True)
            if kind == 'handoff' and expected_bindings is not None:
                # 对话框显示的版本必须与本事务逐项一致；先校验整批，再追加任何交接动作。
                if (not isinstance(expected_bindings, dict)
                        or any(type(key) is not int for key in expected_bindings)
                        or set(expected_bindings) != {t['id'] for t in selected}
                        or any(type(value) is not int or value <= 0 for value in expected_bindings.values())):
                    raise ValueError('交接确认的任务版本不完整或无效，请刷新后重新确认。')
                if any(t['binding_id'] != expected_bindings[t['id']] for t in selected):
                    raise ValueError('材料版本已经变化，请刷新后重新确认交接。')
            if target_group_id is not None:
                self._group(connection, target_group_id)
            if kind == 'handoff' and any(not t['material_ready'] or t['withdrawn'] for t in selected):
                raise ValueError('任务材料尚未就绪或版本已失效，请重新关联。')
            changed = 0
            for item in selected:
                if ((kind == 'handoff' and item['handed_over'])
                    or (kind == 'undo-handoff' and not item['handed_over'])
                    or (kind == 'withdraw' and item['withdrawn'])
                    or (kind == 'move' and item['group_id'] == target_group_id)):
                    continue
                floor = connection.execute('SELECT COALESCE(MAX(id),0) FROM delivery_events').fetchone()[0] if kind == 'reopen' else 0
                connection.execute('''INSERT INTO task_actions(task_id,kind,binding_id,target_group_id,reason,created_at,delivery_floor)
                    VALUES (?,?,?,?,?,?,?)''', (item['id'], kind, item['binding_id'] if kind == 'handoff' else None,
                    target_group_id, reason.strip(), _now(), floor))
                changed += 1
            return dict(task_ids=[t['id'] for t in selected], changed_count=changed)

    def handoff(self, task_ids, confirmed=False, reason='', *, expected_bindings=None):
        return self._action(task_ids, 'handoff', confirmed=confirmed, reason=reason,
                            expected_bindings=expected_bindings)

    def undo_handoff(self, task_ids, confirmed=False, reason=''):
        return self._action(task_ids, 'undo-handoff', confirmed=confirmed, reason=reason)

    def withdraw(self, task_ids, confirmed=False, reason=''):
        return self._action(task_ids, 'withdraw', confirmed=confirmed, reason=reason)

    def reopen(self, task_ids, confirmed=False, reason=''):
        return self._action(task_ids, 'reopen', confirmed=confirmed, reason=reason)

    def move_task(self, task_id, group_id, confirmed=False, reason=''):
        return self._action([task_id], 'move', confirmed=confirmed, reason=reason, target_group_id=group_id)

    def actions(self, task_id):
        with self.db.connect() as connection:
            self._selected(connection, [task_id])
            return [dict(r) for r in connection.execute('SELECT * FROM task_actions WHERE task_id=? ORDER BY id', (task_id,))]

    def _group_action(self, group_id, kind, value):
        with self.db.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            group = self._group(connection, group_id)
            connection.execute('INSERT INTO task_group_actions(group_id,kind,value,created_at) VALUES (?,?,?,?)',
                (group_id, kind, value, _now()))
            return dict(group_id=group['id'])

    def rename_group(self, group_id, title):
        if not isinstance(title, str) or not title.strip() or len(title) > 200:
            raise ValueError('组名应为1—200字。')
        return self._group_action(group_id, 'title', title.strip())

    def set_color(self, group_id, color):
        if not isinstance(color, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', color):
            raise ValueError('请选择有效颜色。')
        return self._group_action(group_id, 'color', color.upper())

    def _history_candidates(self, connection):
        rows = []
        projects_by_id = {p['id']: dict(p) for p in connection.execute('SELECT id,name,message_id FROM projects ORDER BY id')}
        for batch in connection.execute('SELECT * FROM batches ORDER BY id'):
            # 一个物理批次可混有现代已分组来源和早期未分组来源；只能逐消息排除。
            messages = [dict(m) for m in connection.execute('''SELECT source.* FROM messages source
                WHERE source.batch_id=? AND NOT EXISTS(
                    SELECT 1 FROM task_group_messages known WHERE known.history_message_id=source.id)
                AND NOT EXISTS(SELECT 1 FROM task_group_messages known
                    JOIN intake_receipts receipt ON receipt.id=known.receipt_id
                    WHERE receipt.batch_id=source.batch_id AND receipt.raw_text=source.raw_text)
                ORDER BY source.id''', (batch['id'],))]
            project_ids = set()
            for message in messages:
                related = {p['id'] for p in projects_by_id.values() if p['message_id'] == message['id']}
                for receipt in connection.execute("SELECT project_ids FROM intake_receipts WHERE batch_id=? AND raw_text=? AND state='committed'",
                                                  (batch['id'], message['raw_text'])):
                    related.update(json.loads(receipt['project_ids']))
                message['project_ids'] = sorted(related)
                project_ids.update(related)
            projects = [projects_by_id[pid] for pid in sorted(project_ids) if pid in projects_by_id]
            if projects and messages:
                rows.append(dict(batch_id=batch['id'], batch_name=batch['name'], created_at=batch['created_at'],
                                 projects=projects, messages=messages, needs_review=True))
        token = hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        return dict(token=token, rows=rows)

    def history_candidates(self):
        with self.db.connect() as connection:
            connection.execute('BEGIN')
            return self._history_candidates(connection)

    def import_history(self, token, batch_ids=None, confirmed=False):
        if not confirmed:
            raise ValueError('请核对历史候选并明确确认导入；历史交付不会自动完成任务。')
        with self.db.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            preview = self._history_candidates(connection)
            if token != preview['token']:
                raise ValueError('历史候选已变化，请重新预览。')
            ids = set(_ids(batch_ids)) if batch_ids is not None else {r['batch_id'] for r in preview['rows']}
            if not ids.issubset({r['batch_id'] for r in preview['rows']}):
                raise ValueError('历史批次候选无效，请刷新。')
            imported = []
            for candidate in preview['rows']:
                if candidate['batch_id'] not in ids:
                    continue
                # 旧SQLite时间按UTC解释，保留原收件日，不制造今日任务。
                moment = candidate['created_at'].replace(' ', 'T')
                if not re.search(r'(Z|[+-]\d\d:\d\d)$', moment):
                    moment += 'Z'
                existing_group = connection.execute('SELECT id FROM task_groups WHERE history_batch_id=?', (candidate['batch_id'],)).fetchone()
                group = (self._group(connection, existing_group['id']) if existing_group
                         else self._create_group(connection, moment, history_batch_id=candidate['batch_id']))
                sequence = connection.execute('SELECT COALESCE(MAX(sequence),0) FROM task_group_messages WHERE group_id=?', (group['id'],)).fetchone()[0]
                for message in candidate['messages']:
                    sequence += 1
                    message_moment = message['created_at'].replace(' ', 'T')
                    if not re.search(r'(Z|[+-]\d\d:\d\d)$', message_moment):
                        message_moment += 'Z'
                    mid = connection.execute('''INSERT INTO task_group_messages(group_id,raw_text,fingerprint,
                        received_at,sequence,history_message_id) VALUES (?,?,?,?,?,?)''',
                        (group['id'], message['raw_text'], hashlib.sha256(message['raw_text'].strip().encode()).hexdigest(),
                         message_moment, sequence, message['id'])).lastrowid
                    self._attach_tasks(connection, mid, message['project_ids'], history=True)
                imported.append(group['id'])
            return dict(group_ids=imported)
