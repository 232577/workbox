"""显式文件夹收录。调用方必须在后台任务中执行扫描与复制。"""

from __future__ import annotations

import os
import stat
import unicodedata
from concurrent.futures import CancelledError
from pathlib import Path

from native_files import ZipLease, cancelled, path_is_within
from workspace_manager import _folder_name, _path, _zip_structure
from bundle_analyzer import analyze_bundle


def _normalized(name):
    return ' '.join(unicodedata.normalize('NFKC', name).split()).casefold()


class FolderIntake:
    """有界扫描后逐项提交；保留原件、成功项和逐项收录事实。"""

    def __init__(self, service, *, max_files=5000, max_entries=10000,
                 max_bytes=20 * 1024 ** 3, max_depth=32):
        self.service = service
        self.workspaces = service.workspaces
        self.limits = (max_files, max_entries, max_bytes, max_depth)
        if any(type(value) is not int or value < 1 for value in self.limits):
            raise ValueError('文件夹收录限制必须是正整数。')

    def _scan(self, root, cancel_event):
        files, entries, size = [], 0, 0
        stack = [(root, 0)]
        while stack:
            directory, depth = stack.pop()
            cancelled(cancel_event)
            _path(directory, allow_network=True)
            if depth > self.limits[3]:
                raise ValueError('文件夹层级超过收录上限。')
            with os.scandir(directory) as iterator:
                children = []
                for entry in iterator:
                    cancelled(cancel_event)
                    entries += 1
                    if entries > self.limits[1]:
                        raise ValueError('文件夹条目超过收录上限。')
                    info = entry.stat(follow_symlinks=False)
                    if entry.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
                        raise ValueError('文件夹包含符号链接或重解析点，请改为明确选择普通文件。')
                    path = _path(entry.path, allow_network=True)
                    if stat.S_ISDIR(info.st_mode):
                        children.append((path, depth + 1))
                    elif stat.S_ISREG(info.st_mode):
                        files.append(path)
                        size += info.st_size
                        if len(files) > self.limits[0] or size > self.limits[2]:
                            raise ValueError('文件夹文件数量或总大小超过收录上限。')
                    else:
                        raise ValueError('文件夹包含非普通文件，未开始收录。')
                stack.extend(sorted(children, reverse=True))
        return sorted(files, key=lambda value: str(value).casefold())

    def _named(self, name, projects):
        return [row for row in projects if _normalized(row['name']) == _normalized(name)]

    def _directory_route(self, names, projects):
        matches = [self._named(name, projects) for name in names]
        candidates = {row['id']: row for group in matches for row in group}
        protected = any(row.get('archived_at') or row.get('deleted_at') for row in candidates.values())
        return dict(candidates=list(candidates.values()),
                    requires_choice=protected or len(candidates) > 1,
                    basis='目录名称完整匹配' if candidates else '未识别目录名称')

    def _plan(self, root, files, project_id, manual, *, grouped=None):
        projects = self.service.list_projects(archived=None, deleted=None)
        by_id = {row['id']: row for row in projects}
        root_route = self._directory_route([root.name], projects)
        if grouped is None:
            children = {source.relative_to(root).parts[0] for source in files
                        if len(source.relative_to(root).parts) > 1}
            child_projects = {row['id'] for name in children for row in self._named(name, projects)}
            grouped = len(child_projects) > 1 and len(children) > 1
        # 首项目名只是分组容器；同级明确项目子目录各自拥有其中的文件。
        if grouped and not root_route['requires_choice'] and project_id is None:
            root_route = dict(candidates=[], basis='首项目名称分组目录', requires_choice=False)
        if project_id is not None:
            selected = by_id.get(project_id)
            if not manual or not selected or selected.get('archived_at') or selected.get('deleted_at'):
                raise ValueError('请明确选择有效的未归档、未删除项目。')
            root_route = dict(candidates=[selected], basis='人工选择文件夹所属项目', requires_choice=False)
        plans = []
        for source in files:
            relative = source.relative_to(root)
            # 只用路径中的完整项目名；深层的另一个项目名称同样属于冲突证据。
            directory_route = self._directory_route(relative.parts[:-1], projects)
            named = self.service.file_routing(source)
            is_bundle = self.service.is_bundle_candidate(source.name)
            routes = [root_route, directory_route, named]
            candidates = {row['id']: row for route in routes for row in route['candidates']}
            needs_choice = any(route.get('requires_choice') for route in routes) or len(candidates) > 1
            if is_bundle:
                needs_choice = bool(root_route['candidates'] or directory_route['candidates']
                                    or any(route.get('requires_choice') for route in routes))
            route = dict(candidates=list(candidates.values()), requires_choice=needs_choice,
                         basis='；'.join(route['basis'] for route in routes if route['candidates'] or route.get('requires_choice')) or '文件夹名称自动建项')
            cut = 0
            if not root_route['candidates'] and len(candidates) == 1:
                pid = next(iter(candidates))
                for index, part in enumerate(relative.parts[:-1]):
                    if any(row['id'] == pid for row in self._named(part, projects)):
                        cut = index + 1
            plans.append(dict(source=source, relative_path=Path(*relative.parts[cut:]), routing=route,
                              grouped=grouped,
                              is_bundle=is_bundle,
                              project_id=next(iter(candidates)) if len(candidates) == 1 and not needs_choice else None))
        # 有已识别的项目时，剩余泛名材料必须等待明确选择，不能另建项目。
        any_named = any(item['routing']['candidates'] or item['routing']['requires_choice'] or item['is_bundle'] for item in plans)
        for item in plans:
            if not item['routing']['candidates'] and not item['routing']['requires_choice'] and not item['is_bundle']:
                item['provision_name'] = None if any_named else root.name
                if any_named:
                    item['routing'].update(requires_choice=True, basis='文件夹包含已识别项目，当前文件归属未确定')
        return plans

    def _log(self, item, status, message='', *, project_id=None, manual=None):
        return self.service.record_file_decision(item['source'], status, item['routing'],
            project_id=project_id, manual=manual, message=message)

    def receive(self, path, project_id=None, *, cancel_event=None, progress=None, manual=None):
        """后台调用；progress 为已处理条目数/扫描条目总数。

        needs_choice 条目的 retry_path 可交给文件收录或再次显式选择文件夹项目。
        结果中的成功项已经提交，取消或部分失败均不回滚这些成功项。
        """
        result = dict(kind='folder_result', source_path=str(path), copied=0, duplicate=0,
                      watching=0, failed=0, needs_choice=0, cancelled=False, items=[],
                      project_ids=[], created_project_ids=[])

        def append(item, status, message='', **extra):
            row = dict(source_path=str(item['source']), status=status,
                       basis=item['routing'].get('basis', ''), message=message, **extra)
            if 'relative_path' in item:
                row.update(relative_path=str(item['relative_path']), root_path=str(root),
                           source_relative_path=str(item['source'].relative_to(root)))
            if status == 'needs_choice':
                row['retry_path'] = str(item['source'])
                row['candidate_ids'] = [p['id'] for p in item['routing']['candidates']]
            result['items'].append(row)
            if status in ('copied', 'duplicate', 'watching', 'failed', 'needs_choice'):
                result[status] += 1

        root_item = dict(source=Path(path), routing=dict(candidates=[], basis='文件夹收录安全检查'))
        try:
            cancelled(cancel_event)
            root = _path(path, allow_network=True)
            root_item['source'] = root
            if not root.is_dir():
                raise ValueError('请选择存在的普通文件夹。')
            projects = {p['id']: p for p in self.service.list_projects(archived=None, deleted=None)}
            for workspace in self.workspaces.list_workspaces(include_archived=True):
                protected = projects.get(workspace['project_id'], {})
                if path_is_within(root, Path(workspace['path'])) and (protected.get('archived_at') or protected.get('deleted_at')):
                    root_item['routing'].update(requires_choice=True, basis='来源位于已归档或已删除项目目录')
                    self._log(root_item, 'needs-choice', '请先恢复项目或明确选择所属项目。')
                    append(root_item, 'needs_choice', '来源位于已归档或已删除项目目录，请明确选择。')
                    return result
            roots = [Path(self.workspaces.settings()['work_root']), *map(Path, self.workspaces.archive_roots())]
            roots.extend(Path(w['path']) for w in self.workspaces.list_workspaces(include_archived=True))
            if any(path_is_within(root, target) or path_is_within(target, root) for target in roots):
                raise ValueError('来源文件夹与工作目录或归档目录重叠，请改选独立来源，避免递归收录。')
            files = self._scan(root, cancel_event)
            if not files:
                raise ValueError('文件夹内没有可收录的普通文件。')
            plans = self._plan(root, files, project_id, manual)
            for index, item in enumerate(plans):
                try:
                    cancelled(cancel_event)
                    source = _path(item['source'], allow_network=True)
                    if item['routing']['requires_choice']:
                        self._log(item, 'needs-choice', '归属有歧义或涉及受保护项目，请明确选择。')
                        append(item, 'needs_choice', '归属有歧义或涉及受保护项目，请明确选择。')
                        continue
                    is_zip = source.suffix.casefold() == '.zip'
                    if is_zip:
                        with ZipLease(source, movable=False) as lease:
                            _zip_structure(lease.stream, cancel_event)
                    if item['is_bundle']:
                        plan = analyze_bundle(source, self.service.list_projects(archived=None, deleted=None))
                        if plan['state'] != 'matched' and not plan['provision_names']:
                            item['routing']['basis'] = plan['reason']
                            self._log(item, 'needs-choice', plan['reason'])
                            append(item, 'needs_choice', plan['reason'])
                            continue
                        watched = self.service.receive_file(source, cancel_event=cancel_event, manual=False)
                        plan = analyze_bundle(source, self.service.list_projects(archived=None, deleted=None))
                        created_ids = watched.get('created_project_ids', [])
                        result['created_project_ids'].extend(created_ids)
                        result['project_ids'].extend(plan['project_ids'])
                        self._log(item, 'copied', '组合 ZIP 沿用整包识别与稳定监控。', manual=False)
                        append(item, 'watching', project_ids=plan['project_ids'], result=watched)
                        continue
                    pid = item['project_id']
                    if pid is None:
                        # 复查当前目录名称边界；排队期间归档、删除或新建同名项目不能被绕过。
                        current = self._directory_route([item['provision_name']], self.service.list_projects(archived=None, deleted=None))
                        if current['requires_choice']:
                            item['routing'] = current
                            self._log(item, 'needs-choice', '项目边界发生变化，请重新选择。')
                            append(item, 'needs_choice', '项目边界发生变化，请重新选择。')
                            continue
                        proposed = _path(Path(self.workspaces.settings()['work_root']) / _folder_name(item['provision_name']))
                        if not current['candidates'] and proposed.exists():
                            item['routing'].update(requires_choice=True, basis='同名目标文件夹已存在但未登记')
                            self._log(item, 'needs-choice', '请明确选择项目，不能自动采用已有目录。')
                            append(item, 'needs_choice', '同名目标文件夹已存在，请明确选择项目。')
                            continue
                        created = self.service._capture_file_projects(root, [item['provision_name']])
                        if len(created['project_ids']) != 1:
                            raise ValueError('项目名称冲突，请重新选择。')
                        pid = created['project_ids'][0]
                        result['created_project_ids'].extend(created.get('created_project_ids', []))
                    else:
                        fresh = self._plan(root, [source], project_id, manual, grouped=item['grouped'])[0]
                        if fresh['routing']['requires_choice'] or fresh['project_id'] != pid:
                            item['routing'] = fresh['routing']
                            self._log(item, 'needs-choice', '项目归属发生变化，请重新选择。')
                            append(item, 'needs_choice', '项目归属发生变化，请重新选择。')
                            continue
                    with self.service.db.connect() as connection:
                        self.service.db._assert_projects_editable(connection, pid)
                    if is_zip:
                        workspace = self.workspaces.get_workspace(pid) or self.workspaces.ensure_projects([pid])[0]
                        directory = _path(Path(workspace['path']) / item['relative_path'].parent)
                        directory.mkdir(parents=True, exist_ok=True)
                        copied = self.service._working_zip_copy(source, directory, cancel_event=cancel_event)
                        watched = self.service.snapshots.watch_zip(source, pid)
                        if Path(copied['path']) != source:
                            self.service.snapshots.watch_work_copy(copied['path'], source, pid, baseline_sha256=copied['sha256'])
                        self._log(item, 'copied', 'ZIP 已核验工作副本，等待稳定监控归档。', project_id=pid, manual=bool(manual))
                        append(item, 'watching', project_id=pid, working_zip_path=copied['path'], result=watched)
                    else:
                        decision = dict(basis=item['routing']['basis'], candidate_ids=[p['id'] for p in item['routing']['candidates']], manual=bool(manual))
                        imported = self.workspaces.import_file(source, pid, relative_path=item['relative_path'], cancel_event=cancel_event, routing=decision)
                        append(item, 'duplicate' if imported['duplicate'] else 'copied', project_id=pid, file=imported['file'])
                    result['project_ids'].append(pid)
                except CancelledError as error:
                    self._log(item, 'cancelled', str(error))
                    append(item, 'cancelled', str(error))
                    result['cancelled'] = True
                    break
                except Exception as error:
                    self._log(item, 'failed', str(error))
                    append(item, 'failed', str(error))
                finally:
                    if progress:
                        progress(index + 1, len(plans))
        except CancelledError as error:
            result['cancelled'] = True
            self._log(root_item, 'cancelled', str(error))
            append(root_item, 'cancelled', str(error))
        except Exception as error:
            self._log(root_item, 'failed', str(error))
            append(root_item, 'failed', str(error))
        result['project_ids'] = list(dict.fromkeys(result['project_ids']))
        result['created_project_ids'] = list(dict.fromkeys(result['created_project_ids']))
        return result

    def receive_item(self, item, project_id, *, cancel_event=None, progress=None):
        """只重试用户明确选定的一项，并保持最初扫描的目标相对路径。"""
        source_value = item.get('source_path', '')
        routing = dict(candidates=[], basis='人工选择文件夹内单个文件的所属项目')
        decision = dict(source=source_value, routing=routing)
        result = dict(kind='folder_result', source_path=source_value, copied=0, duplicate=0,
                      watching=0, failed=0, needs_choice=0, cancelled=False, items=[],
                      project_ids=[], created_project_ids=[], manual_choice_attempted=True)
        try:
            cancelled(cancel_event)
            source = _path(source_value, allow_network=True)
            decision['source'] = source
            if source.is_dir():
                # 目录级受保护边界仍须在整目录规则中复查。
                result = self.receive(source, project_id, cancel_event=cancel_event, progress=progress, manual=True)
                result['manual_choice_attempted'] = True
                return result
            root = _path(item.get('root_path', ''), allow_network=True)
            relative = Path(item.get('relative_path', ''))
            original_relative = Path(item.get('source_relative_path', ''))
            if (not source.is_file() or not root.is_dir() or not source.is_relative_to(root)
                    or source != root / original_relative or relative.is_absolute()
                    or not relative.parts or '..' in relative.parts or relative.name != source.name
                    or tuple(original_relative.parts[-len(relative.parts):]) != relative.parts
                    or any(':' in part for part in relative.parts)):
                raise ValueError('文件来源或原相对路径已经变化，请重新收录文件夹。')
            if any(path_is_within(root, Path(archive)) or path_is_within(Path(archive), root)
                   for archive in self.workspaces.archive_roots()):
                raise ValueError('来源已与归档目录重叠，请重新选择普通来源文件夹。')
            with self.service.db.connect() as connection:
                self.service.db._assert_projects_editable(connection, project_id)
            row = dict(source_path=str(source), root_path=str(root), relative_path=str(relative),
                       source_relative_path=str(original_relative), project_id=project_id,
                       basis=routing['basis'], message='')
            if source.suffix.casefold() == '.zip':
                if self.service.is_bundle_candidate(source.name):
                    raise ValueError('组合 ZIP 需要按整包匹配项目，不能通过单项目选择覆盖。')
                with ZipLease(source, movable=False) as lease:
                    _zip_structure(lease.stream, cancel_event)
                workspace = self.workspaces.get_workspace(project_id) or self.workspaces.ensure_projects([project_id])[0]
                directory = _path(Path(workspace['path']) / relative.parent)
                directory.mkdir(parents=True, exist_ok=True)
                copied = self.service._working_zip_copy(source, directory, cancel_event=cancel_event)
                watched = self.service.snapshots.watch_zip(source, project_id)
                if Path(copied['path']) != source:
                    self.service.snapshots.watch_work_copy(copied['path'], source, project_id, baseline_sha256=copied['sha256'])
                self._log(decision, 'copied', 'ZIP 工作副本已核验，等待稳定监控归档。', project_id=project_id, manual=True)
                row.update(status='watching', working_zip_path=copied['path'], result=watched)
            else:
                imported = self.workspaces.import_file(source, project_id, relative_path=relative,
                    cancel_event=cancel_event, routing=dict(basis=routing['basis'], candidate_ids=[], manual=True))
                row.update(status='duplicate' if imported['duplicate'] else 'copied', file=imported['file'])
            result[row['status']] = 1
            result['items'].append(row)
            result['project_ids'] = [project_id]
            if progress:
                progress(1, 1)
        except Exception as error:
            is_cancelled = isinstance(error, CancelledError)
            result['cancelled'] = is_cancelled
            status = 'cancelled' if is_cancelled else 'failed'
            self._log(decision, status, str(error), project_id=project_id, manual=True)
            result['items'].append(dict(source_path=str(source_value), status=status,
                                        basis=routing['basis'], message=str(error)))
            if not is_cancelled:
                result['failed'] = 1
        return result

    def receive_items(self, items, project_id, *, cancel_event=None, progress=None):
        """同一选择只作用于待选条目；逐项提交并汇总，取消保留已成功项。"""
        if not isinstance(items, (list, tuple)) or not 1 <= len(items) <= self.limits[0]:
            raise ValueError('请选择有界的待收录文件列表。')
        result = dict(kind='folder_result', source_path=items[0].get('root_path', items[0].get('source_path', '')),
                      copied=0, duplicate=0, watching=0, failed=0, needs_choice=0,
                      cancelled=False, items=[], project_ids=[], created_project_ids=[],
                      manual_choice_attempted=True)
        for index, item in enumerate(items):
            received = self.receive_item(item, project_id, cancel_event=cancel_event)
            for key in ('copied', 'duplicate', 'watching', 'failed', 'needs_choice'):
                result[key] += received[key]
            for key in ('items', 'project_ids', 'created_project_ids'):
                result[key].extend(received[key])
            if progress:
                progress(index + 1, len(items))
            if received['cancelled']:
                result['cancelled'] = True
                break
        result['project_ids'] = list(dict.fromkeys(result['project_ids']))
        result['created_project_ids'] = list(dict.fromkeys(result['created_project_ids']))
        return result
