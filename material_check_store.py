"""Explicit, project-scoped rule settings; no schema or delivery migration."""
import json

from material_matching import decode_rules, encode_rules


def setting_key(project_id: int) -> str:
    if type(project_id) is not int or project_id < 1:
        raise ValueError('项目编号无效。')
    return f'material_check_v1:{project_id}'


def check_project(service, project_id):
    setting_key(project_id)
    project = service.get_project(project_id)
    if project.get('merged_into_id') is not None or project.get('deleted_at'):
        raise ValueError('项目已合并或删除，请刷新并选择保留项目。')
    return project


def load_rules(service, project_id):
    check_project(service, project_id)
    with service.db.connect() as db:
        row = db.execute('SELECT value FROM desktop_settings WHERE key=?', (setting_key(project_id),)).fetchone()
    if row is None:
        return None
    if not isinstance(row[0], str) or len(row[0]) > 128 * 1024:
        raise ValueError('保存的材料规则无效或过大；原设置未改变。')
    return decode_rules(json.loads(row[0]))


def save_rules(service, project_id, rules):
    key = setting_key(project_id)
    payload = encode_rules(rules)
    check_project(service, project_id)
    with service.db.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        # Recheck the identity in the same transaction as the sole setting write.
        row = db.execute('''SELECT p.id FROM projects p WHERE p.id=?
            AND NOT EXISTS(SELECT 1 FROM project_merges m WHERE m.source_id=p.id)
            AND NOT EXISTS(SELECT 1 FROM project_deletions d WHERE d.project_id=p.id)''', (project_id,)).fetchone()
        if row is None:
            raise ValueError('项目已变化，规则未保存，请刷新。')
        db.execute('''INSERT INTO desktop_settings(key,value) VALUES (?,?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value''',
            (key, json.dumps(payload, ensure_ascii=False)))
