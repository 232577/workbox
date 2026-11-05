"""从既有收件和任务来源读取项目批次；不创建、迁移或修改历史。"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone


_COLORS = ('#507080', '#78658A', '#5D8070', '#96734E', '#697AA0', '#887080')
_LOCAL_ZONE = timezone(timedelta(hours=8))


def _date_parts(value):
    try:
        moment = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(_LOCAL_ZONE).strftime('%Y%m%d'), moment.timestamp()
    except (ValueError, TypeError, OverflowError, OSError):
        return '未知', float('-inf')


def _marker(kind, source_id, received_at, *, code=None, color=None):
    prefix, source_kind = {'group': ('G', 'task_group'), 'receipt': ('R', 'receipt'),
                           'batch': ('B', 'creation_batch')}[kind]
    key = f'{kind}:{source_id}'
    day, _ = _date_parts(received_at)
    if code is None:
        code = f'{prefix}{day}-{source_id}'
    label = re.sub(r'^([GRB])\d{2}(\d{6})-', r'\1\2-', code)
    if not isinstance(color, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', color):
        color = _COLORS[int.from_bytes(hashlib.sha256(key.encode('utf-8')).digest()[:2], 'big') % len(_COLORS)]
    return dict(key=key, code=code, label=label, color=color, source_kind=source_kind,
                source_id=source_id, received_at=received_at or '')


def _sort_key(marker):
    return (_date_parts(marker['received_at'])[1], marker['source_id'], marker['key'])


def read_project_batch_index(connection):
    """调用方持有已开启的快照事务；查询数与项目/任务数量无关。"""
    if not isinstance(connection, sqlite3.Connection) or not connection.in_transaction:
        raise ValueError('项目批次共用连接需要已开始的读取事务。')
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
    warnings = []
    projects = {r['id']: dict(r) for r in connection.execute('SELECT id,batch_id FROM projects')}
    canonical = {r['project_id']: r['canonical_id'] for r in connection.execute('SELECT project_id,canonical_id FROM project_canonical')}
    batches = {r['id']: dict(r) for r in connection.execute('SELECT id,created_at FROM batches')}
    by_project = {pid: {} for pid in set(canonical.values()) if pid in projects}

    def attach(project_id, marker, source):
        target = canonical.get(project_id)
        if target not in by_project:
            warnings.append(f'{source}引用的项目 #{project_id} 不存在或合并身份缺失。')
            return False
        by_project[target][marker['key']] = marker
        return True

    covered_receipts = set()
    if 'task_group_messages' in tables:
        covered_receipts = {r[0] for r in connection.execute('SELECT DISTINCT receipt_id FROM task_group_messages WHERE receipt_id IS NOT NULL')}
        if {'messages', 'intake_receipts'} <= tables:
            # 历史补录记录的是原消息ID，同批次/原文对应的回执也已被覆盖。
            covered_receipts.update(r[0] for r in connection.execute('''
                SELECT DISTINCT r.id FROM task_group_messages gm
                JOIN messages m ON m.id=gm.history_message_id
                JOIN intake_receipts r ON r.batch_id=m.batch_id AND r.raw_text=m.raw_text
                WHERE r.state='committed' '''))
    if 'task_groups' in tables:
        groups = {r['id']: dict(r) for r in connection.execute('SELECT id,code,color,created_at FROM task_groups')}
        if 'task_items' not in tables:
            warnings.append('历史任务归属来源表缺失，只能显示其他已核实的批次依据。')
        if 'task_group_actions' in tables:
            for action in connection.execute("SELECT group_id,value FROM task_group_actions WHERE kind='color' ORDER BY id"):
                if action['group_id'] in groups:
                    groups[action['group_id']]['color'] = action['value']
                else:
                    warnings.append(f"批次颜色记录引用的任务组 #{action['group_id']} 不存在。")
        moves = {}
        if 'task_actions' in tables:
            for action in connection.execute("SELECT task_id,target_group_id FROM task_actions WHERE kind='move' ORDER BY id"):
                moves[action['task_id']] = action['target_group_id']
        elif 'task_items' in tables:
            warnings.append('任务移组动作来源表缺失，不能确认当前任务批次归属。')
        if 'task_items' in tables and 'task_actions' in tables:
            for item in connection.execute('SELECT id,project_id,original_group_id FROM task_items ORDER BY id'):
                group_id = moves.get(item['id'], item['original_group_id'])
                group = groups.get(group_id)
                if group is None:
                    warnings.append(f"任务 #{item['id']} 当前引用的任务组 #{group_id} 不存在。")
                    continue
                if not group['code'] or not isinstance(group['code'], str):
                    warnings.append(f'任务组 #{group_id} 缺少原批次编号。')
                    continue
                if not re.fullmatch(r'#[0-9a-fA-F]{6}', str(group['color'])):
                    warnings.append(f'任务组 #{group_id} 的颜色无效，使用稳定辅助色。')
                marker = _marker('group', group_id, group['created_at'], code=group['code'], color=group['color'])
                attach(item['project_id'], marker, f"任务 #{item['id']}")
    elif 'task_items' in tables:
        warnings.append('任务记录存在，但任务组来源表缺失。')

    if 'intake_receipts' in tables:
        for receipt in connection.execute("SELECT id,project_ids,created_at FROM intake_receipts WHERE state='committed' ORDER BY id"):
            if receipt['id'] in covered_receipts:
                continue
            try:
                ids = json.loads(receipt['project_ids'])
                if not isinstance(ids, list) or not ids or any(type(pid) is not int or pid <= 0 for pid in ids):
                    raise ValueError('invalid project ids')
            except (ValueError, TypeError):
                warnings.append(f"收件回执 #{receipt['id']} 的项目编号列表损坏或为空。")
                continue
            marker = _marker('receipt', receipt['id'], receipt['created_at'])
            for pid in dict.fromkeys(ids):
                attach(pid, marker, f"收件回执 #{receipt['id']}")

    # 只对没有收件/当前任务依据的项目回退真实原建项批次；不会改写 batch_id。
    for pid, refs in by_project.items():
        if refs:
            continue
        batch_id = projects[pid]['batch_id']
        batch = batches.get(batch_id)
        if batch is None:
            warnings.append(f'项目 #{pid} 的原建项批次 #{batch_id} 不存在。')
            continue
        marker = _marker('batch', batch_id, batch['created_at'])
        refs[marker['key']] = marker
    result = {pid: sorted(refs.values(), key=_sort_key, reverse=True) for pid, refs in by_project.items()}
    options = {marker['key']: marker for refs in result.values() for marker in refs}
    return dict(by_project=result, options=sorted(options.values(), key=_sort_key, reverse=True),
                warnings=list(dict.fromkeys(warnings)))
