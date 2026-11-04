"""Resolve explicit text names without silently duplicating project identities."""
from collections import OrderedDict
import unicodedata

from parser import ParsedProject


class TextProjectConflict(ValueError):
    """The entire receipt must remain pending, without partial project creation."""


def normalized_name(value):
    return ' '.join(unicodedata.normalize('NFKC', value).split()).casefold()


def resolve_text_projects(connection, projects):
    """Read-only plan within the caller's BEGIN IMMEDIATE transaction.

    A complete current name takes precedence over confirmed historical aliases.
    Existing status, batch, urgency, files, type and delivery history stay intact.
    """
    if not projects or len(projects) > 500:
        raise ValueError('请保留 1 至 500 个项目。')
    names = OrderedDict()
    for project in projects:
        if (not isinstance(project, ParsedProject) or not project.name.strip()
                or len(project.name.strip()) > 200 or type(project.urgent) is not bool):
            raise ValueError('项目名称不能为空、不能超过 200 字符，加急必须为是或否。')
        key = normalized_name(project.name)
        prior = names.get(key)
        names[key] = ParsedProject(prior.name if prior else project.name.strip(),
                                   project.urgent or bool(prior and prior.urgent))
    entries = [dict(r) for r in connection.execute('''SELECT p.id,p.name,p.status,
        d.archived_at,x.deleted_at,cb.reason AS block_reason FROM projects p
        LEFT JOIN project_details d ON d.project_id=p.id
        LEFT JOIN project_deletions x ON x.project_id=p.id
        LEFT JOIN project_change_blocks cb ON cb.project_id=p.id
        WHERE NOT EXISTS(SELECT 1 FROM project_merges m WHERE m.source_id=p.id)
        ORDER BY p.id''')]
    by_id = {r['id']: r for r in entries}
    current, aliases = {}, {}
    for row in entries:
        current.setdefault(normalized_name(row['name']), {})[row['id']] = row
    for alias in connection.execute('''SELECT a.name,c.canonical_id FROM project_name_aliases a
            JOIN project_canonical c ON c.project_id=a.project_id'''):
        if alias['canonical_id'] in by_id:
            aliases.setdefault(normalized_name(alias['name']), {})[alias['canonical_id']] = by_id[alias['canonical_id']]
    planned = []
    for key, project in names.items():
        matches = list((current.get(key) or aliases.get(key) or {}).values())
        if len(matches) > 1:
            choices = '、'.join(f"#{r['id']}（{r['status']}）" for r in matches[:8])
            raise TextProjectConflict(f'“{project.name}”已有多个同名项目：{choices}。请先核对并合并重名项目，或修正本次名称；未新建任何项目。')
        row = matches[0] if matches else None
        if row and (row['archived_at'] or row['deleted_at'] or row['block_reason']):
            state = '已删除' if row['deleted_at'] else ('已归档' if row['archived_at'] else '工作目录待恢复')
            raise TextProjectConflict(f'“{project.name}”的同名项目 #{row["id"]} {state}。请先恢复或核对，未新建任何项目。')
        planned.append((project, row['id'] if row else None))
    return planned
