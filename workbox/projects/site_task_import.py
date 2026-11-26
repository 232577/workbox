"""Link original-site task identities to personal projects without changing local progress."""

from collections import Counter

from workbox.projects.database import Database
from workbox.projects.project_names import project_name_key
from workbox.projects.text_intake import create_intake_message, project_name_block, read_project_name_index


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


def import_site_tasks(connection, tasks):
    """Write new projects and source identities in the caller's write transaction."""
    tasks, invalid = _read_tasks(tasks)
    ids, created, linked, unchanged, conflicts = [], [], [], [], []
    result = dict(project_ids=ids, created_project_ids=created, linked_project_ids=linked,
                  unchanged_project_ids=unchanged, conflicts=conflicts)
    if not tasks:
        return result

    by_id, current_names, aliases = read_project_name_index(connection)
    bindings = {row['task_id']: row['canonical_id'] for row in connection.execute(
        '''SELECT s.task_id,c.canonical_id FROM site_task_project_links s
           JOIN project_canonical c ON c.project_id=s.project_id''')}
    linked_projects = set(bindings.values())
    title_counts = Counter(project_name_key(title) for task_id, title in tasks.items() if task_id not in invalid)
    for task_id, title in tasks.items():
        if task_id in invalid:
            conflicts.append(dict(task_id=task_id, title=title[:200], reason=invalid[task_id]))
            continue

        pid = bindings.get(task_id)
        if pid is not None:
            reason = project_name_block(by_id[pid])
            if reason:
                conflicts.append(dict(task_id=task_id, title=title, reason=f'关联项目 #{pid} {reason}。'))
            else:
                ids.append(pid)
                unchanged.append(pid)
            continue

        key = project_name_key(title)
        matches = current_names.get(key, {})
        if title_counts[key] > 1:
            reason = '存在多个同名的原站待办，请先核对任务名称。'
        elif len(matches) > 1:
            reason = '存在多个同名的本机项目，请先核对项目名称。'
        elif not matches:
            reason = '同名历史项目需要人工核对。' if key in aliases else ''
        else:
            pid, row = next(iter(matches.items()))
            blocked = project_name_block(row)
            reason = f'同名项目 #{pid} {blocked}。' if blocked else ''
            if pid in linked_projects and not reason:
                reason = '同名项目已关联另一个原站待办。'
        if reason:
            conflicts.append(dict(task_id=task_id, title=title, reason=reason))
            continue
        if pid is None:
            # An upstream task list is a personal queue, not one delivery batch.
            batch_id, _, message_id = create_intake_message(connection, f'这是家待办 #{task_id}', title)
            pid = connection.execute('INSERT INTO projects(message_id,batch_id,name) VALUES (?,?,?)',
                                     (message_id, batch_id, title)).lastrowid
            created.append(pid)
        else:
            linked.append(pid)
        connection.execute('INSERT INTO site_task_project_links(task_id,project_id) VALUES (?,?)',
                           (task_id, pid))
        linked_projects.add(pid)
        ids.append(pid)
    if created:
        Database._assign_default_project_type(connection, created)
    result['project_ids'] = list(dict.fromkeys(ids))
    return result
