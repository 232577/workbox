"""Link original-site task identities to personal projects without changing local progress."""

from collections import Counter

from workbox.projects.database import Database
from workbox.projects.project_names import numbered_project_key, numberless_project_key, project_name_key, versionless_project_key
from workbox.projects.text_intake import create_intake_message, numbered_receipt_names, project_name_block, read_project_name_index


def _read_tasks(tasks):
    titles, invalid = {}, {}
    for task_id, title in tasks:
        if (not isinstance(task_id, str) or not task_id or len(task_id) > 128
                or task_id != task_id.strip() or not task_id.isprintable()):
            raise ValueError('「这是家」待办编号无效，请刷新任务后重试。')
        title = title.strip() if isinstance(title, str) else ''
        previous = titles.setdefault(task_id, title)
        if not title or len(title) > 200 or any(character in title for character in '\0\n\r'):
            invalid[task_id] = '项目名称为空、过长或包含换行。'
        elif previous != title:
            invalid[task_id] = '同一原站任务编号对应了多个名称，请刷新后核对。'
    return titles, invalid


def source_project_links(connection):
    return {row['task_id']: row['canonical_id'] for row in connection.execute(
        '''SELECT s.task_id,c.canonical_id FROM site_task_project_links s
           JOIN project_canonical c ON c.project_id=s.project_id''')}


def create_site_task_project(connection, task_id, title):
    # Keep each upstream task in an independent batch and working directory.
    batch_id, _, message_id = create_intake_message(connection, f'这是家待办 #{task_id}', title)
    return connection.execute('INSERT INTO projects(message_id,batch_id,name) VALUES (?,?,?)',
                              (message_id, batch_id, title)).lastrowid


def _numbered_names(names):
    result = {}
    for title, rows in names.items():
        if key := numbered_project_key(title):
            result.setdefault(key, {}).update(rows)
            result.setdefault((None, key[1]), {}).update(rows)
    return result


def _matches_with_changed_number(names, numbered_names, title):
    key = numbered_project_key(title)
    if key:
        return numbered_names.get(key, {}) | names.get(key[1], {})
    return numbered_names.get((None, title), {})


def _has_conflicting_receipt_number(connection, project_id, key):
    texts = {row['raw_text'] for row in connection.execute('''SELECT m.raw_text
        FROM projects p JOIN messages m ON m.id=p.message_id WHERE p.id=?''', (project_id,))}
    # A later numbered receipt can also have explicitly reused this existing project.
    if connection.execute("SELECT 1 FROM sqlite_master WHERE name='intake_receipts' AND type='table'").fetchone():
        texts.update(row['raw_text'] for row in connection.execute('''
            SELECT r.raw_text FROM intake_receipts r
            WHERE r.state='committed' AND EXISTS (
                SELECT 1 FROM json_each(r.project_ids) WHERE type='integer' AND value=?
            )''', (project_id,)))
    written_numbers = {number for text in texts for number, name in numbered_receipt_names(text, parsed=True)
                       if name == key[1]}
    return bool(written_numbers and written_numbers != {key[0]})


def import_site_tasks(connection, tasks, *, strict_identity=False, other_titles=()):
    """Write new projects and source identities in the caller's write transaction."""
    tasks, invalid = _read_tasks(tasks)
    ids, created, linked, unchanged, conflicts = [], [], [], [], []
    result = dict(project_ids=ids, created_project_ids=created, linked_project_ids=linked,
                  unchanged_project_ids=unchanged, conflicts=conflicts)
    if not tasks:
        return result

    by_id, current_names, aliases = read_project_name_index(connection)
    bindings = source_project_links(connection)
    project_tasks = {}
    for task_id, project_id in bindings.items():
        project_tasks.setdefault(project_id, set()).add(task_id)
    for row in connection.execute('''
        SELECT s.task_id,c.canonical_id FROM original_site_bindings s
        JOIN project_canonical c ON c.project_id=s.project_id
    '''):
        project_tasks.setdefault(row['canonical_id'], set()).add(row['task_id'])
    titles = [project_name_key(title) for task_id, title in tasks.items() if task_id not in invalid]
    titles.extend(project_name_key(title) for title in other_titles)
    if strict_identity:
        title_counts = Counter(numbered_project_key(title) or title for title in titles)
        numbered_title_counts = Counter(numberless_project_key(title) or title for title in titles)
        numbered_current, numbered_aliases = _numbered_names(current_names), _numbered_names(aliases)
    else:
        title_counts = Counter(titles)
    for task_id, title in tasks.items():
        if task_id in invalid:
            conflicts.append(dict(task_id=task_id, title=title[:200], reason=invalid[task_id]))
            continue

        pid = bindings.get(task_id)
        if pid is not None:
            reason = project_name_block(by_id[pid])
            if not reason and strict_identity and project_tasks[pid] != {task_id}:
                reason = '包含其他原站待办的关联，需要先核对'
            if reason:
                conflicts.append(dict(task_id=task_id, title=title, reason=f'关联项目 #{pid} {reason}。'))
            else:
                ids.append(pid)
                unchanged.append(pid)
            continue

        key = project_name_key(title)
        numbered_key = numbered_project_key(key) if strict_identity else None
        matches = current_names.get(key, {})
        historical = aliases.get(key, {})
        if numbered_key:
            matches = matches or numbered_current.get(numbered_key, {})
            historical = historical or numbered_aliases.get(numbered_key, {})
        number_changed = strict_identity and not matches and not historical
        if number_changed:
            matches = _matches_with_changed_number(current_names, numbered_current, key)
        if title_counts[numbered_key or key] > 1:
            reason = '存在多个同名的原站待办，请先核对任务名称。'
        elif number_changed and matches and numbered_title_counts[numberless_project_key(key) or key] > 1:
            reason = '去掉列表编号后有多个同名的原站待办，请先核对任务编号。'
        elif len(matches) > 1:
            reason = '存在多个同名的本机项目，请先核对项目名称。'
        elif not matches:
            if historical or (number_changed and _matches_with_changed_number(aliases, numbered_aliases, key)):
                reason = '同名历史项目需要人工核对。'
            else:
                reason = ''
                base = versionless_project_key(title) if strict_identity else None
                if base and versionless_project_key(base) is None and any(
                    project_id not in project_tasks
                    for names, numbered_names in ((current_names, numbered_current), (aliases, numbered_aliases))
                    for project_id in names.get(base, {}).keys() | _matches_with_changed_number(names, numbered_names, base).keys()
                ):
                    reason = '本机有未标版本的同名项目或历史名称，请核对。'
        else:
            pid, row = next(iter(matches.items()))
            blocked = project_name_block(row)
            reason = f'同名项目 #{pid} {blocked}。' if blocked else ''
            if pid in project_tasks and not reason:
                reason = '同名项目已关联另一个原站待办。'
            if not reason and number_changed and numbered_key and _has_conflicting_receipt_number(connection, pid, numbered_key):
                reason = '本机收录原文与原站的列表编号不同，请先核对任务。'
        if reason:
            conflicts.append(dict(task_id=task_id, title=title, reason=reason))
            continue
        if pid is None:
            pid = create_site_task_project(connection, task_id, title)
            created.append(pid)
        else:
            linked.append(pid)
        connection.execute('INSERT INTO site_task_project_links(task_id,project_id) VALUES (?,?)',
                           (task_id, pid))
        project_tasks.setdefault(pid, set()).add(task_id)
        ids.append(pid)
    if created:
        Database._assign_default_project_type(connection, created)
    result['project_ids'] = list(dict.fromkeys(ids))
    return result
