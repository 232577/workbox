"""Resolve explicit text names without silently duplicating project identities."""
import re
from collections import OrderedDict

from workbox.projects.parser import ParsedProject, parse_message
from workbox.projects.project_names import numbered_project_key, project_name_key as normalized_name


class TextProjectConflict(ValueError):
    """The entire receipt must remain pending, without partial project creation."""


def read_project_name_index(connection):
    """Read canonical rows and their complete current and historical names."""
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
    return by_id, current, aliases


def project_name_block(row):
    if row['deleted_at']:
        return '已删除'
    if row['archived_at']:
        return '已归档'
    if row['block_reason']:
        return '工作目录待恢复'
    return ''


def create_intake_message(connection, batch_name, raw_text):
    original_name, suffix = batch_name, 1
    while connection.execute('SELECT 1 FROM batches WHERE name=?', (batch_name,)).fetchone():
        suffix += 1
        batch_name = f'{original_name} ({suffix})'
    batch_id = connection.execute('INSERT INTO batches(name) VALUES (?)', (batch_name,)).lastrowid
    message_id = connection.execute('INSERT INTO messages(batch_id,raw_text) VALUES (?,?)',
                                    (batch_id, raw_text)).lastrowid
    return batch_id, batch_name, message_id


def numbered_receipt_names(raw_text, *, parsed=False):
    """Return the written numbers and their titles from the original clipboard text."""
    names = set()
    for line in re.split(r'[\r\n；;]+', raw_text):
        key = numbered_project_key(line)
        if not key:
            continue
        names.add(key)
        if parsed:
            try:
                projects = parse_message(line)
            except ValueError:
                continue
            names.update((key[0], normalized_name(project.name)) for project in projects)
    return names


def _original_projects_from_numbered_lines(connection, raw_text, by_id, names):
    """The receipt can retain an exact source number its parsed project omits."""
    lines = {key for key in numbered_receipt_names(raw_text) if key[1] in names}
    if not lines:
        return {}
    matched = {}
    for row in connection.execute('SELECT title,active_project_id FROM original_site_active_projects'):
        key = numbered_project_key(row['title'])
        project_id = row['active_project_id']
        if key in lines and project_id in by_id:
            matched.setdefault(key[1], {})[project_id] = by_id[project_id]
    return matched


def resolve_text_projects(connection, projects, *, raw_text=''):
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
    by_id, current, aliases = read_project_name_index(connection)
    numbered = _original_projects_from_numbered_lines(connection, raw_text, by_id, names)
    planned = []
    for key, project in names.items():
        matches = list(((current.get(key) or aliases.get(key) or {}) | numbered.get(key, {})).values())
        if len(matches) > 1:
            choices = '、'.join(f"#{r['id']}（{r['status']}）" for r in matches[:8])
            raise TextProjectConflict(f'“{project.name}”已有多个同名项目：{choices}。请先核对并合并重名项目，或修正本次名称；未新建任何项目。')
        row = matches[0] if matches else None
        state = project_name_block(row) if row else ''
        if state:
            raise TextProjectConflict(f'“{project.name}”的同名项目 #{row["id"]} {state}。请先恢复或核对，未新建任何项目。')
        planned.append((project, row['id'] if row else None))
    return planned
