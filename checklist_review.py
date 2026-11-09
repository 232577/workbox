"""Read-only checklist-to-project comparison, separate from delivery semantics."""
from __future__ import annotations

import sqlite3

from checklist_reader import MAX_ENTRIES, normalized_name
from material_check_store import load_rules
from material_matching import check_snapshot
from native_files import cancelled
from project_documents import list_project_documents

MAX_MATERIAL_PROJECTS = 200


def _project_index(service):
    with service.db.connect() as db:
        db.execute('BEGIN')
        records = [dict(row) for row in db.execute('''SELECT p.id,p.name,p.status,
            d.archived_at,x.deleted_at,cb.reason AS block_reason,w.path AS workspace_path
            FROM projects p LEFT JOIN project_details d ON d.project_id=p.id
            LEFT JOIN project_deletions x ON x.project_id=p.id
            LEFT JOIN project_change_blocks cb ON cb.project_id=p.id
            LEFT JOIN project_workspaces w ON w.project_id=p.id
            WHERE NOT EXISTS(SELECT 1 FROM project_merges m WHERE m.source_id=p.id)
            ORDER BY p.id''')]
        aliases = [dict(row) for row in db.execute('''SELECT a.name,c.canonical_id
            FROM project_name_aliases a JOIN project_canonical c ON c.project_id=a.project_id''')]
    by_id = {row['id']: row for row in records}
    current, historical = {}, {}
    for row in records:
        current.setdefault(normalized_name(row['name']), {})[row['id']] = row
    for row in aliases:
        pid = row['canonical_id']
        if pid in by_id:
            historical.setdefault(normalized_name(row['name']), {})[pid] = by_id[pid]
    return current, historical, by_id


def _material_result(service, project, cancel_event):
    rules = load_rules(service, project['id'])
    if not rules:
        return dict(material_state='未配置规则', matched=None, required=None,
                    missing='先在项目“材料检查”中保存要求；不默认套用五件材料。')
    snapshot = list_project_documents(service, project['id'], cancel_event=cancel_event)
    cancelled(cancel_event)
    report = check_snapshot(snapshot, rules, project['name'])
    # A rule edited during a scan must not be presented as a current success.
    if load_rules(service, project['id']) != rules:
        return dict(material_state='规则已变化', matched=None, required=None, missing='请重新检查。')
    names = [f'{row.label} × {row.missing}' for row in report.rows if row.missing]
    state = {'complete':'已收齐','missing':'材料不齐','ambiguous':'归属待确认',
             'incomplete':'读取不完整','unconfigured':'未配置规则'}[report.status]
    if report.warnings:
        names = list(report.warnings)
    elif report.ambiguous:
        names.append(f'{len(report.ambiguous)} 个文件需在材料检查中选择归属')
    return dict(material_state=state,
                matched=None if report.warnings else sum(row.matched for row in report.rows),
                required=sum(row.required for row in report.rows), missing='；'.join(names) or '—')


def review_entries(service, entries, *, check_materials=False, cancel_event=None, progress=None):
    cancelled(cancel_event)
    if not isinstance(entries, (list, tuple)) or not 1 <= len(entries) <= MAX_ENTRIES:
        raise ValueError('一次核对 1 至 2000 个名称。')
    for row in entries:
        if not isinstance(row, dict) or not isinstance(row.get('name'), str) or not 1 <= len(row['name'].strip()) <= 200:
            raise ValueError('名单包含无效名称。')
    current, aliases, before = _project_index(service)
    result = []
    for raw in entries:
        cancelled(cancel_event)
        key = normalized_name(raw['name'])
        candidates = list((current.get(key) or aliases.get(key) or {}).values())
        row = dict(raw, state='尚未收录', project_id=None, project_state='—',
                   material_state='—', matched=None, required=None, missing='—', candidates=candidates)
        if len(candidates) > 1:
            row.update(state='同名待确认', missing='；'.join(f"#{p['id']} · {p['status']}" for p in candidates))
        elif candidates:
            project = candidates[0]
            row['project_state'] = project['status']
            if project['deleted_at']:
                row.update(state='已删除', missing='先在已删除列表核对或恢复项目。')
            elif project['archived_at']:
                row.update(state='已归档', missing='先核对归档项目；不会新建同名项。')
            elif project['block_reason']:
                row.update(state='目录待恢复', missing=project['block_reason'])
            else:
                row.update(state='已关联', project_id=project['id'], material_state='未检查')
        result.append(row)
    ids = {r['project_id'] for r in result if r['project_id'] is not None}
    if check_materials and len(ids) > MAX_MATERIAL_PROJECTS:
        raise ValueError('一次最多检查 200 个项目的材料，请按日期缩小名单范围。')
    if check_materials:
        cache = {}
        for index, row in enumerate(result):
            cancelled(cancel_event)
            pid = row['project_id']
            if pid is not None:
                if pid not in cache:
                    try:
                        cache[pid] = _material_result(service, before[pid], cancel_event)
                    except (OSError, ValueError, KeyError, sqlite3.Error) as error:
                        cache[pid] = dict(material_state='读取不完整', matched=None, required=None, missing=str(error))
                row.update(cache[pid])
            if progress is not None:
                progress(index + 1, len(result))
        _, _, after = _project_index(service)
        for row in result:
            pid = row['project_id']
            if pid is not None and before[pid] != after.get(pid):
                row.update(state='项目已变化', project_id=None, material_state='请重新核对',
                           matched=None, required=None, missing='检查期间项目身份或状态变化，旧结果不作为当前结论。')
    cancelled(cancel_event)
    return result
