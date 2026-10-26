"""只读查询已登记项目的本地文件与归档，不读内容、不创建目录或表。

工作文件以已登记工作目录为边界，排除其他项目子目录、依赖缓存和归档库存储；
未登记的同名目录不属于本项目。ZIP 只按扩展名标记工作副本，不验证
格式或解压。材料归档返回历史目录行，不混入当前工作文件。
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
from datetime import datetime, timezone
from pathlib import Path

from native_files import cancelled
from snapshot_manager import DISCOVERY_EXCLUDED_DIRECTORIES
from workspace_manager import _path


MAX_DOCUMENTS = 10000
MAX_ENTRIES = 50000
MAX_DEPTH = 64
_KIND_ORDER = {'工作文件': 0, 'ZIP工作副本': 1, 'ZIP归档': 2, '材料归档': 3}


def _absolute(value):
    path = Path(value)
    if not path.is_absolute():
        raise ValueError('登记路径不是绝对路径。')
    return Path(os.path.abspath(path))


def _key(path):
    return os.path.normcase(str(path))


def _inside(path, root):
    return path == root or path.is_relative_to(root)


def list_project_documents(service, project_id, *, cancel_event=None):
    """返回稳定快照；errors 为可显示的字符串列表，部分失败保留可读结果。

    documents 行包含 id/name/relative_path/path/kind/size/mtime_ns/modified_at/
    exists/is_directory/origin/source。id 只取项目编号及规范绝对路径，修改
    内容或补登记不会改变 id；missing 行实时元数据为 None。目录归档的
    size 为 None。这里的 exists 仅代表查询时的状态，不代表内容已验证。
    """
    result = dict(project_id=project_id, workspace_path=None, documents=[], errors=[])
    errors, documents = result['errors'], {}
    if type(project_id) is not int or project_id < 1:
        result['errors'].append('项目编号无效。')
        return result

    def problem(message):
        # 一次目录不可读可能影响多行，避免轮询产生重复提示。
        if message not in errors:
            errors.append(message)

    def lexical(value):
        try:
            return _absolute(value)
        except (OSError, TypeError, ValueError) as error:
            problem(f'跳过无效登记路径：{value}（{error}）')
            return None

    def safe(value):
        cancelled(cancel_event)
        try:
            return _path(_absolute(value))
        except (OSError, TypeError, ValueError) as error:
            problem(f'无法读取安全本地路径：{value}（{error}）')
            return None

    def add(path, kind, origin, relative=None, *, directory=False, **extra):
        cancelled(cancel_event)
        path = safe(path)
        if path is None:
            return
        key = _key(path)
        if key not in documents and len(documents) >= MAX_DOCUMENTS:
            problem(f'文档数量达到上限（{MAX_DOCUMENTS}），仅显示部分结果。')
            return
        try:
            info = path.lstat()
            if directory and not stat.S_ISDIR(info.st_mode):
                problem(f'已登记归档不再是目录：{path}')
                return
            if not directory and not stat.S_ISREG(info.st_mode):
                return
            exists, mtime = True, info.st_mtime_ns
            size = None if directory else info.st_size
        except FileNotFoundError:
            exists, mtime, size = False, None, None
        except OSError as error:
            problem(f'无法读取文件信息：{path}（{error}）')
            return
        documents[key] = dict(
            id='document:' + hashlib.sha256(f'{project_id}:{key}'.encode('utf-8')).hexdigest(),
            name=path.name, relative_path=relative or path.name, path=str(path), kind=kind,
            size=size, mtime_ns=mtime,
            modified_at=None if mtime is None else datetime.fromtimestamp(
                mtime / 1_000_000_000, timezone.utc).isoformat(timespec='microseconds'),
            exists=exists, is_directory=directory, origin=origin, source=origin, **extra)

    cancelled(cancel_event)
    try:
        with service.db.connect() as connection:
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
            if 'projects' not in tables or connection.execute(
                    'SELECT 1 FROM projects WHERE id=?', (project_id,)).fetchone() is None:
                problem('项目不存在。')
                return result
            workspaces = [dict(row) for row in connection.execute(
                'SELECT project_id,path FROM project_workspaces')] if 'project_workspaces' in tables else []
            registered = [dict(row) for row in connection.execute(
                'SELECT * FROM work_files WHERE project_id=? ORDER BY id DESC',
                (project_id,))] if 'work_files' in tables else []
            zips = [dict(row) for row in connection.execute(
                'SELECT z.* FROM zips z JOIN zip_project_links l ON l.zip_id=z.id '
                'WHERE l.project_id=? ORDER BY z.id DESC', (project_id,))] if {
                    'zips', 'zip_project_links'} <= tables else []
            zip_paths = [row[0] for row in connection.execute('SELECT path FROM zips')] if 'zips' in tables else []
            all_archives = [dict(row) for row in connection.execute(
                'SELECT * FROM project_delivery_archives ORDER BY id DESC')] if 'project_delivery_archives' in tables else []
            roots = [row[0] for row in connection.execute(
                'SELECT path FROM workflow_archive_roots')] if 'workflow_archive_roots' in tables else []
            roots.append(str(service.db.path.parent / 'ZIP归档'))
            if 'workflow_settings' in tables:
                row = connection.execute("SELECT value FROM workflow_settings WHERE key='archive_root'").fetchone()
                if row:
                    try:
                        roots.append(json.loads(row[0]))
                    except (TypeError, ValueError):
                        problem('保存的 ZIP 归档目录设置无法读取。')
    except sqlite3.Error as error:
        problem(f'文档登记读取失败：{error}')
        return result

    # 先记录明确关联的归档；同一路径只显示一次，不按名称推断关联。
    for row in zips:
        add(row['path'], 'ZIP归档', 'zip_project_links', zip_id=row['id'])
    for row in all_archives:
        if row['project_id'] == project_id:
            add(row['path'], '材料归档', 'project_delivery_archives', directory=True,
                archive_id=row['id'], archived_at=row.get('created_at'))

    own = next((row for row in workspaces if row['project_id'] == project_id), None)
    if own:
        result['workspace_path'] = own['path']
        root = safe(own['path'])
    else:
        root = None
    if root is not None:
        boundaries = [path for value in roots + [row['path'] for row in all_archives]
                      if (path := lexical(value)) is not None]
        other_roots = [path for row in workspaces if row['project_id'] != project_id
                       and (path := lexical(row['path'])) is not None and _inside(path, root)]
        boundaries.extend(other_roots)
        known_zips = {_key(path) for value in zip_paths if (path := lexical(value)) is not None}

        def allowed(path):
            return (_inside(path, root) and not any(_inside(path, boundary) for boundary in boundaries)
                    and not any(part in DISCOVERY_EXCLUDED_DIRECTORIES
                                for part in path.relative_to(root).parts[:-1])
                    and _key(path) not in known_zips and not path.name.startswith('~$'))

        if any(_inside(root, boundary) for boundary in boundaries):
            problem('工作目录与其他项目或归档存储重叠，已跳过工作文件扫描。')
        else:
            seen_registered = set()
            for row in registered:
                cancelled(cancel_event)
                path = lexical(row['path'])
                if path is None or not allowed(path) or _key(path) in seen_registered:
                    continue
                seen_registered.add(_key(path))
                kind = 'ZIP工作副本' if path.suffix.casefold() == '.zip' else '工作文件'
                add(path, kind, 'work_files', path.relative_to(root).as_posix())

            visited = 0

            def walk(directory, depth=0):
                nonlocal visited
                cancelled(cancel_event)
                if visited >= MAX_ENTRIES or len(documents) >= MAX_DOCUMENTS:
                    problem(f'文档扫描达到上限（{MAX_DOCUMENTS} 个文档 / {MAX_ENTRIES} 个目录项），仅显示部分结果。')
                    return
                if depth > MAX_DEPTH:
                    problem(f'目录层级达到上限（{MAX_DEPTH}），部分子目录未显示。')
                    return
                if safe(directory) is None:
                    return
                try:
                    children = []
                    with os.scandir(directory) as entries:
                        for entry in entries:
                            cancelled(cancel_event)
                            visited += 1
                            if visited > MAX_ENTRIES:
                                problem(f'目录项数量达到上限（{MAX_ENTRIES}），仅显示部分结果。')
                                break
                            children.append(Path(entry.path))
                    for path in sorted(children, key=lambda item: (item.name.casefold(), item.name)):
                        cancelled(cancel_event)
                        if not allowed(path) or safe(path) is None:
                            continue
                        info = path.lstat()
                        if stat.S_ISDIR(info.st_mode):
                            if path.name not in DISCOVERY_EXCLUDED_DIRECTORIES:
                                walk(path, depth + 1)
                        elif stat.S_ISREG(info.st_mode):
                            kind = 'ZIP工作副本' if path.suffix.casefold() == '.zip' else '工作文件'
                            existing = documents.get(_key(path))
                            origin = existing['origin'] if existing else 'workspace'
                            add(path, kind, origin, path.relative_to(root).as_posix())
                except OSError as error:
                    problem(f'工作目录读取不完整：{directory}（{error}）')

            walk(root)

    result['documents'] = sorted(documents.values(), key=lambda row: (
        _KIND_ORDER[row['kind']], row['relative_path'].casefold(), row['relative_path'], row['id']))
    return result
