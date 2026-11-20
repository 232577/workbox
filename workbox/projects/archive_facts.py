"""只读交付归档的历史登记事实。

数量来自已经登记的 ZIP 分项目计数或材料归档清单，不表示文件当前仍存在，
也不表示本次已重新校验文件内容。本模块不访问文件系统、不推断最新版本，
不创建表、不写入数据，也不提交调用方的读取事务。
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3


def _fact(kind='unknown', source_id=None, path='', *, count=None, reason='归档登记事实不足。'):
    return dict(count=count, known=count is not None, source_kind=kind,
                source_id=source_id, path=path if isinstance(path, str) else '', reason=reason)


def _family(connection, event):
    project_id = event.get('project_id')
    original_id = event.get('original_project_id', project_id)
    if any(type(value) is not int or value <= 0 for value in (project_id, original_id)):
        return None
    rows = connection.execute('SELECT project_id,canonical_id FROM project_canonical '
                              'WHERE project_id IN (?,?)', (project_id, original_id)).fetchall()
    identities = {row[0]: row[1] for row in rows}
    canonical = identities.get(project_id)
    if canonical is None or identities.get(original_id) != canonical:
        return None
    return [row[0] for row in connection.execute(
        'SELECT project_id FROM project_canonical WHERE canonical_id=?', (canonical,))]


def _zip_fact(connection, event, family):
    zip_id = event.get('zip_id')
    result = _fact('zip', zip_id, event.get('archive_path', ''))
    if type(zip_id) is not int or zip_id <= 0:
        return dict(result, source_id=None, reason='ZIP 编号缺失或无效。')
    row = connection.execute('SELECT path FROM zips WHERE id=?', (zip_id,)).fetchone()
    if row is None:
        return dict(result, reason='ZIP 登记记录缺失。')
    result['path'] = row[0] if isinstance(row[0], str) else ''
    plans = connection.execute('SELECT match_state FROM zip_bundles WHERE zip_id=?', (zip_id,)).fetchall()
    if len(plans) > 1 or (plans and plans[0][0] != 'matched'):
        return dict(result, reason='组合包归属尚待核对。')
    marks = ','.join('?' for _ in family)
    linked = connection.execute(f'SELECT DISTINCT project_id FROM zip_project_links '
                                f'WHERE zip_id=? AND project_id IN ({marks})', [zip_id, *family]).fetchall()
    counts = connection.execute(f'SELECT project_id,file_count FROM zip_project_file_counts '
                                f'WHERE zip_id=? AND project_id IN ({marks})', [zip_id, *family]).fetchall()
    if len(linked) != 1 or len(counts) != 1 or linked[0][0] != counts[0][0]:
        return dict(result, reason='ZIP 项目关联或分项目文件数缺失、冲突。')
    count = counts[0][1]
    if type(count) is not int or count < 0:
        return dict(result, reason='ZIP 分项目文件数无效。')
    return dict(result, count=count, known=True, reason='')


def _manifest_count(serialized):
    try:
        manifest = json.loads(serialized)
    except (TypeError, ValueError):
        return None
    if not isinstance(manifest, list) or not manifest:
        return None
    files, paths = set(), set()
    for item in manifest:
        if not isinstance(item, dict):
            return None
        path = item.get('path')
        if (not isinstance(path, str) or not path or '\\' in path or ':' in path
                or '\x00' in path or path.startswith('/')):
            return None
        directory = path.endswith('/') or item.get('is_dir') is True or item.get('type') == 'directory'
        parts = path[:-1].split('/') if path.endswith('/') else path.split('/')
        if any(part in ('', '.', '..') for part in parts):
            return None
        key = '/'.join(parts).casefold()
        if key in paths:
            return None
        paths.add(key)
        if directory:
            continue
        size, digest = item.get('size'), item.get('sha256')
        if type(size) is not int or size < 0 or not isinstance(digest, str) or not re.fullmatch('[0-9a-fA-F]{64}', digest):
            return None
        files.add(key)
    # 一个普通文件不能同时充当另一个条目的父目录。
    for key in paths:
        parts = key.split('/')
        if any('/'.join(parts[:depth]) in files for depth in range(1, len(parts))):
            return None
    return len(files) if files else None


def _material_fact(connection, event, family):
    path, signature = event.get('archive_path'), event.get('archive_signature')
    result = _fact('files', path=path)
    if (not isinstance(path, str) or not path or not isinstance(signature, str)
            or not re.fullmatch('[0-9a-fA-F]{64}', signature)):
        return dict(result, reason='材料交付事件缺少精确归档路径或签名。')
    marks = ','.join('?' for _ in family)
    rows = connection.execute(f'SELECT id,manifest FROM project_delivery_archives '
                              f'WHERE project_id IN ({marks}) AND path=? AND signature=?',
                              [*family, path, signature]).fetchall()
    if len(rows) != 1:
        return dict(result, reason='材料归档清单缺失或存在多个精确匹配。')
    result['source_id'] = rows[0][0]
    count = _manifest_count(rows[0][1])
    if count is None:
        return dict(result, reason='材料归档清单无效或不含已登记的常规文件。')
    # 与 delivery_archive._signature 相同：按清单原顺序签署文件路径与散列。
    # 历史归档清单只含文件；兼容的目录项不计数，也不参与文件清单签名。
    manifest = json.loads(rows[0][1])
    material = [[item['path'], item['sha256']] for item in manifest
                if not (item['path'].endswith('/') or item.get('is_dir') is True
                        or item.get('type') == 'directory')]
    serialized = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    if hashlib.sha256(serialized.encode('utf-8')).hexdigest() != signature.casefold():
        return dict(result, reason='材料归档清单与登记签名不一致。')
    return dict(result, count=count, known=True, reason='')


def delivery_archive_facts(connection, events) -> dict:
    """按事件 ID 返回数量事实；调用方负责同一读取快照与有效事件选择。"""
    result = {}
    for value in events:
        event = dict(value)
        kind = 'files' if event.get('kind') == 'manual-files' else 'zip' if event.get('zip_id') is not None else 'unknown'
        fact = _fact(kind, event.get('zip_id') if kind == 'zip' else None, event.get('archive_path', ''))
        try:
            family = _family(connection, event)
            if not family:
                fact['reason'] = '事件项目及合并后的项目归属无法确定。'
            elif kind == 'files' and event.get('zip_id') is None:
                fact = _material_fact(connection, event, family)
            elif kind == 'zip':
                fact = _zip_fact(connection, event, family)
            else:
                fact['reason'] = '事件没有可精确关联的已登记归档。'
        except sqlite3.Error:
            fact['reason'] = '归档登记表缺失或无法读取。'
        result[event['id']] = fact
    return result
