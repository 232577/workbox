"""只读 ZIP 中央目录，按已确定的项目关联递归统计常规文件。

不解压、不读取正文、不校验正文 CRC。目录、系统元数据不计数；零字节文件计数。
project_ids 必须由调用方先通过名称、目录或人工选择确定，不能用当前面板选择猜测。
超过 10000 成员、危险路径、特殊文件、重名成员及无法确定目录归属时抛 ValueError。
"""

from __future__ import annotations

from bundle_analyzer import _count_files, _name, _read_tree


def count_project_files(path, projects, project_ids, *, bundle_plan=None) -> dict[int, int]:
    """返回每个关联项目自身的文件数，绝不把整包总数复制给多个项目。

matched 合包计划使用含共同外层的 folder_path，但每次重新读取计数。
没有计划时，完整项目名目录必须唯一；发现其他已知项目目录时不会归入目标。
仅一个已确定项目且包中没有任何已知项目目录时，所有常规文件归入该项目，
包括 src/docs 等通用子目录。空包返回零。多项目缺少唯一目录时拒绝计数。
"""
    rows = [dict(row) for row in projects]
    ids = list(project_ids)
    by_id = {row['id']: row for row in rows}
    if not ids or len(set(ids)) != len(ids) or any(pid not in by_id for pid in ids):
        raise ValueError('ZIP 必须关联不重复且已存在的项目。')
    by_name = {}
    for row in rows:
        by_name.setdefault(_name(row['name']), []).append(row['id'])
    root = _read_tree(path)
    nodes = {}
    named_paths = {}
    pending = [((), root)]
    while pending:
        current_path, node = pending.pop()
        nodes[current_path] = node
        for name, child in node['dirs'].items():
            child_path = (*current_path, name)
            if _name(name) in by_name:
                named_paths.setdefault(_name(name), []).append(child_path)
            pending.append((child_path, child))

    selected_paths = {}
    if bundle_plan is not None:
        matches = bundle_plan.get('matches', [])
        if (bundle_plan.get('state') != 'matched'
                or set(bundle_plan.get('project_ids', [])) != set(ids)
                or len(matches) != len(ids)):
            raise ValueError('ZIP 合包计划未完整匹配所关联项目。')
        for match in matches:
            pid = match.get('project_id')
            folder = match.get('folder_path', '')
            parts = tuple(folder.split('/'))
            if (pid not in ids or pid in selected_paths or parts not in nodes or not folder
                    or _name(parts[-1]) != _name(by_id[pid]['name'])):
                raise ValueError('ZIP 合包项目目录不存在或与关联项目不符。')
            selected_paths[pid] = parts
        paths = list(selected_paths.values())
        if any(first != second and second[:len(first)] == first for first in paths for second in paths):
            raise ValueError('ZIP 合包项目目录相互包含，归属不明确。')
    else:
        if len(ids) == 1 and not named_paths:
            return {ids[0]: _count_files(root)}
        for pid in ids:
            key = _name(by_id[pid]['name'])
            candidates = named_paths.get(key, [])
            selected_name_count = sum(_name(by_id[target]['name']) == key for target in ids)
            if selected_name_count != 1 or len(candidates) != 1:
                raise ValueError('ZIP 项目目录缺失或存在同名歧义，无法确定各项目文件数。')
            selected_paths[pid] = candidates[0]
    return {pid: _count_files(nodes[folder], set(by_name) - {_name(by_id[pid]['name'])})
            for pid, folder in selected_paths.items()}
