"""只读 ZIP 中央目录，识别明确声明项目总数的合包。"""

from __future__ import annotations

import re
import stat
import unicodedata
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from native_files import ZipLease


MAX_MEMBERS = 10000
MAX_WRAPPERS = 4


def _name(value):
    return ' '.join(unicodedata.normalize('NFKC', str(value)).split()).casefold()


def _filename(value):
    return unicodedata.normalize('NFKC', str(value)).strip()


def bundle_hint(filename) -> dict | None:
    """只接受完整的 ``首项目名-N[-G].zip``；N 是总项目数。"""
    value = _filename(filename)
    if '/' in value or '\\' in value or '\x00' in value:
        return None
    match = re.fullmatch(r'(.+)-([1-9][0-9]{0,2})(?:-G)?\.zip', value, re.IGNORECASE)
    if not match or not match[1].strip() or int(match[2]) > 500:
        return None
    return {'head_name': match[1].strip(), 'declared_count': int(match[2])}


def _result(hint=None, *, state='needs-review', reason='', names=None, matches=None, provision_names=None):
    matches = matches or []
    return {
        'is_bundle': state != 'not-bundle',
        'declared_count': hint['declared_count'] if hint else 0,
        'member_names': names or [],
        'project_ids': [row['project_id'] for row in matches] if state == 'matched' else [],
        'matches': matches if state == 'matched' else [],
        'state': state,
        'reason': reason,
        'provision_names': provision_names or [],
    }


def _parts(info):
    # ZipInfo.filename 会在 NUL 处截断；安全检查必须读取中央目录的原始名称。
    value = unicodedata.normalize('NFKC', info.orig_filename).replace('\\', '/')
    if '\x00' in value or value.startswith('/') or ':' in value:
        raise ValueError('ZIP 包含绝对路径、文件流或 NUL 名称。')
    parts = value.rstrip('/').split('/')
    if not parts or any(part in ('', '.', '..') for part in parts):
        raise ValueError('ZIP 包含空路径或路径遍历成员。')
    if stat.S_ISLNK(info.external_attr >> 16):
        raise ValueError('ZIP 包含符号链接，需人工检查项目目录。')
    kind = stat.S_IFMT(info.external_attr >> 16)
    if kind not in (0, stat.S_IFREG, stat.S_IFDIR):
        raise ValueError('ZIP 包含非常规文件，需人工检查。')
    return parts


def _directories(infos):
    root = {'dirs': {}, 'files': 0}
    members = set()
    directory_paths = {}
    file_paths = set()
    for info in infos:
        parts = _parts(info)
        key = tuple(_name(part) for part in parts)
        if key in members:
            raise ValueError('ZIP 包含重复或正规化后重名的成员，需人工检查。')
        members.add(key)
        is_directory = (info.is_dir() or info.orig_filename.endswith('\\')
                        or stat.S_ISDIR(info.external_attr >> 16) or bool(info.external_attr & 0x10))
        directories = parts if is_directory else parts[:-1]
        original_parts = info.orig_filename.replace('\\', '/').rstrip('/').split('/')
        for depth in range(1, len(directories) + 1):
            directory_key = key[:depth]
            raw_path = tuple(original_parts[:depth])
            if directory_key in file_paths or (
                directory_key in directory_paths and directory_paths[directory_key] != raw_path
            ):
                raise ValueError('ZIP 路径存在文件目录冲突或目录名称歧义。')
            directory_paths[directory_key] = raw_path
        if not is_directory:
            if key in directory_paths:
                raise ValueError('ZIP 路径同时声明为文件和目录。')
            file_paths.add(key)
        if any(_name(part) in ('__macosx', '.ds_store') for part in parts):
            continue
        node = root
        for part in directories:
            node = node['dirs'].setdefault(part, {'dirs': {}, 'files': 0})
        if len(directories) != len(parts):
            node['files'] += 1
    return root


def _read_tree(path):
    """只读受锁定句柄的中央目录，所有计数使用相同的安全和成员上限。"""
    try:
        with ZipLease(path, movable=False) as lease:
            with ZipFile(lease.stream) as archive:
                infos = archive.infolist()
            if len(infos) > MAX_MEMBERS:
                raise ValueError(f'ZIP 成员超过 {MAX_MEMBERS} 个，需人工检查。')
            return _directories(infos)
    except (BadZipFile, EOFError) as error:
        raise ValueError('ZIP 中央目录损坏或不是有效的 ZIP 文件。') from error


def _count_files(node, excluded_names=()):
    pending = [node]
    count = 0
    excluded = set(excluded_names)
    while pending:
        current = pending.pop()
        count += current['files']
        pending.extend(child for name, child in current['dirs'].items() if _name(name) not in excluded)
    return count


def _has_files(node):
    return _count_files(node) > 0


def _match_projects(names, rows):
    by_name = {}
    for row in rows:
        if row.get('archived_at') is None:
            by_name.setdefault(_name(row['name']), []).append(row)
    candidates = [by_name.get(_name(name), []) for name in names]
    if not all(candidates):
        return None, '包内有目录不能精确匹配已有的未归档项目。'
    # 整包必须来自一个可唯一确定的批次，不拼接各批次的独立匹配。
    batches = set(row.get('batch_id') for row in candidates[0])
    for choices in candidates[1:]:
        batches.intersection_update(row.get('batch_id') for row in choices)
    solutions = []
    for batch in batches:
        grouped = [[row for row in choices if row.get('batch_id') == batch] for choices in candidates]
        if all(len(choices) == 1 for choices in grouped):
            chosen = [choices[0] for choices in grouped]
            if len({row['id'] for row in chosen}) == len(names):
                solutions.append(chosen)
    if len(solutions) != 1:
        return None, '项目名称存在批次歧义，或没有唯一完整的同批次匹配。'
    return [dict(folder_name=name, project_id=row['id'], project_name=row['name'],
                 batch_id=row.get('batch_id')) for name, row in zip(names, solutions[0])], ''


def analyze_bundle(path, projects, *, filename=None) -> dict:
    """返回整包识别结果；只有 matched 才能使用完整的 project_ids。

    filename 为记录中的原始文件名，适用于物理文件已改名的归档副本。
    不解压、不读取正文、不校验正文 CRC，也不执行任何包内内容。
    """
    filename = Path(path).name if filename is None else filename
    hint = bundle_hint(filename)
    rows = [dict(row) for row in projects]
    stem = _filename(filename)
    stem = stem[:-4] if stem.casefold().endswith('.zip') else stem
    if not hint or any(_name(row['name']) == _name(stem) for row in rows):
        return _result(state='not-bundle', reason='文件名未声明合包，或完整文件名已精确对应一个项目。')
    try:
        root = _read_tree(path)
    except ValueError as error:
        if isinstance(error.__cause__, (BadZipFile, EOFError)):
            raise
        return _result(hint, reason=str(error))
    current = root
    wrapper = []
    for depth in range(MAX_WRAPPERS + 1):
        members = current['dirs']
        if len(members) == hint['declared_count']:
            break
        if len(members) != 1 or depth == MAX_WRAPPERS:
            return _result(hint, names=list(members), reason='实际项目目录数与文件名声明总数不符。')
        wrapper_name, current = next(iter(members.items()))
        wrapper.append(wrapper_name)
    names = list(members)
    if len({_name(name) for name in names}) != len(names):
        return _result(hint, names=names, reason='多个项目目录名称正规化后重复，需人工检查。')
    if _name(hint['head_name']) not in {_name(name) for name in names}:
        return _result(hint, names=names, reason='文件名中的首项目名称与包内目录不一致。')
    # 自动建项只接受每个目录都有实际文件、名称合法且尚未出现的完整一组。
    # 这个字段只描述结构可用于建项，不改变已有匹配和计数结果。
    provision_names = []
    known_names = {_name(row['name']) for row in rows}
    if (all(0 < len(name.strip()) <= 200 and _has_files(members[name]) for name in names)
            and not any(_name(name) in known_names for name in names)):
        head = next(name for name in names if _name(name) == _name(hint['head_name']))
        provision_names = [head, *(name for name in names if name != head)]
    matches, reason = _match_projects(names, rows)
    if matches is None:
        return _result(hint, names=names, reason=reason, provision_names=provision_names)
    for match in matches:
        match['folder_path'] = '/'.join([*wrapper, match['folder_name']])
        match['file_count'] = _count_files(
            members[match['folder_name']], known_names - {_name(match['project_name'])},
        )
    return _result(hint, state='matched', names=names, matches=matches,
                   reason='声明总数、实际目录及同批次项目完整匹配。')
