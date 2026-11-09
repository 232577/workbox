"""交付报表与待开发项目名单的只读快照和 XLSX 安全导出。"""
from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from contextlib import contextmanager, nullcontext
from datetime import datetime
from functools import partial
from hashlib import file_digest
from pathlib import Path
from uuid import uuid4

from openpyxl import Workbook
from openpyxl.cell.rich_text import CellRichText
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill

from archive_facts import delivery_archive_facts
from database import DELIVERY_TIMEZONE
from native_files import ZipLease, cancelled, identity, path_is_within, plain_path
from task_groups import read_task_snapshot
from project_batches import read_project_batch_index


PROJECT_COLUMNS = (
    ('project_name', '项目名称', 42), ('project_type_name', '类型', 18),
    ('archive_file_count', '最近交付归档文件数', 24), ('first_at', '首次有效交付时间', 24),
    ('latest_at', '最近交付时间', 24), ('valid_delivery_count', '有效交付次数', 18),
    ('included_in_workload', '计入工作量', 18), ('status', '当前状态', 16),
    ('batch_name', '批次', 28), ('archive_path', '归档位置', 58),
    ('visibility', '项目可见性', 18), ('project_id', '项目编号', 14),
    ('counted', '首交计数', 18), ('archive_reason', '归档数量说明', 48),
    ('intake_batch_codes', '收件批次编号', 36),
)
SUMMARY_COLUMNS = (
    ('type_name', '当前类型', 28), ('project_count', '交付项目数（去重）', 25),
    ('eligible_project_count', '具备工作量资格的项目数', 30),
    ('workload_project_count', '纳入首交统计的项目数', 32),
)
TASK_GROUP_COLUMNS = (
    ('group_code', '组号', 24), ('received_day', '最初收件日期', 18), ('title', '组名', 36),
    ('ready_count', '材料就绪数', 18), ('handed_count', '已交接数', 18),
    ('pending_count', '未交接（已确定）', 24), ('withdrawn_count', '撤回数', 16),
    ('needs_review_count', '待核对任务数', 20), ('total_count', '总任务数', 16),
    ('message_review_count', '待核对消息数', 20),
)
TASK_COLUMNS = (
    ('task_code', '任务号', 28), ('group_code', '当前组号', 24), ('project_name', '项目名称', 42),
    ('received_at', '任务收件时间', 24), ('source_messages', '来源消息', 60),
    ('material_status', '材料状态', 18), ('handoff_status', '手工交接状态', 28),
    ('archive_path', '关联归档位置', 58), ('project_type_name', '当前项目类型', 20),
    ('event_id', '关联交付记录编号', 24), ('event_occurred_at', '交付记录时间', 24),
    ('project_id', '当前项目编号', 20), ('original_project_name', '最初项目名称', 42),
    ('withdrawn', '已撤回', 16), ('needs_review', '需核对', 16),
)


def _tables(connection):
    return {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def collect_delivery_report(db, *, cancel_event=None, progress=None, group_id=None):
    """一个读取事务内归一项目、撤销、类型、数量事实；不创建或修复表。"""
    cancelled(cancel_event)
    if group_id is not None and (type(group_id) is not int or group_id <= 0):
        raise ValueError('任务组编号必须为正整数。')
    with db.connect() as connection:
        connection.execute('BEGIN')
        history = db.delivery_history(connection=connection)
        facts = delivery_archive_facts(connection, history['events'])
        current = {row['id']: dict(row) for row in connection.execute('SELECT id,name,status FROM projects')}
        batch_index = read_project_batch_index(connection)
        task_groups_snapshot = read_task_snapshot(connection, open_only=False)
        if group_id is not None:
            task_groups_snapshot = [g for g in task_groups_snapshot if g['id'] == group_id]
            if not task_groups_snapshot:
                raise ValueError('任务组不存在，请刷新后重新导出。')
        allowed_projects = ({t['project_id'] for g in task_groups_snapshot for t in g['tasks']}
                            if group_id is not None else None)
        task_group_rows, task_rows = _task_report(connection, task_groups_snapshot, cancel_event=cancel_event)
        protected_roots, protected_files = _protected_paths(connection, db)
    grouped = {}
    for index, event in enumerate(history['events']):
        cancelled(cancel_event)
        if event['voided']:
            continue
        pid = event['project_id']
        if allowed_projects is not None and pid not in allowed_projects:
            continue
        if pid not in grouped:
            fact = facts.get(event['id'], {})
            grouped[pid] = dict(
                project_id=pid, project_name=current[pid]['name'],
                project_type_id=event.get('project_type_id'),
                project_type_name=event.get('project_type_name') or '未分类',
                project_type_active=event.get('project_type_active'),
                archive_file_count=fact.get('count') if fact.get('known') else None,
                archive_reason=fact.get('reason') or ('' if fact.get('known') else '缺少可核对的归档数量事实'),
                first_at=event['first_at'], latest_at=event['occurred_at'], latest_event_id=event['id'],
                valid_delivery_count=0, included_in_workload=event['included_in_workload'], counted=False,
                prior_delivery=event['prior_delivery'], status=current[pid]['status'],
                batch_name=event['batch_name'], archive_path=event['archive_path'],
                intake_batch_codes='；'.join(marker['code'] for marker in batch_index['by_project'].get(pid, [])),
                project_archived=event['project_archived'], project_deleted=event['project_deleted'],
                visibility='已删除' if event['project_deleted'] else '已归档' if event['project_archived'] else '日常项目',
            )
        grouped[pid]['valid_delivery_count'] += 1
        grouped[pid]['counted'] |= event['counted']
        if progress:
            progress(index + 1, max(1, len(history['events'])))
    projects = list(grouped.values())
    types = {}
    for project in projects:
        group = types.setdefault(project['project_type_id'], dict(
            type_id=project['project_type_id'], type_name=project['project_type_name'],
            active=project['project_type_active'], project_count=0,
            eligible_project_count=0, workload_project_count=0))
        group['project_count'] += 1
        group['eligible_project_count'] += project['included_in_workload']
        group['workload_project_count'] += project['counted']
    if sum(row['project_count'] for row in types.values()) != len(projects):
        raise ValueError('类型汇总与交付项目总数不一致，未导出。')
    return dict(projects=projects, type_counts=list(types.values()), project_count=len(projects),
                eligible_project_count=sum(row['included_in_workload'] for row in projects),
                workload_project_count=sum(row['counted'] for row in projects),
                valid_event_count=sum(row['valid_delivery_count'] for row in projects),
                task_groups_snapshot=task_groups_snapshot, task_group_rows=task_group_rows, task_rows=task_rows,
                task_count=len(task_rows), handed_task_count=sum(row['counts']['handed'] for row in task_groups_snapshot),
                group_id=group_id, group_code=task_groups_snapshot[0]['code'] if group_id is not None else None,
                batch_warnings=batch_index['warnings'],
                protected_roots=protected_roots, protected_files=protected_files)


def collect_pending_project_names(db, *, cancel_event=None, progress=None):
    """读取日常待开发项目及同一快照的受管路径，沿用项目列表的顺序。"""
    cancelled(cancel_event)
    with db.connect() as connection:
        connection.execute('PRAGMA query_only=ON')
        connection.execute('BEGIN')
        projects = db.list_projects(status='待开发', connection=connection)
        cancelled(cancel_event)
        protected_roots, protected_files = _protected_paths(connection, db)
    names = []
    for index, project in enumerate(projects, 1):
        cancelled(cancel_event)
        names.append(project['name'])
        if progress:
            progress(index, len(projects))
    cancelled(cancel_event)
    return dict(project_names=names, project_count=len(names),
                protected_roots=protected_roots, protected_files=protected_files)


def _task_report(connection, groups, *, cancel_event=None):
    """只使用同一快照中的来源、当前类型和确切绑定事件，不用项目状态补任务。"""
    if not groups:
        return [], []
    types = {row['id']: row['type_name'] or '未分类' for row in connection.execute('''
        SELECT p.id,pt.name AS type_name FROM projects p
        LEFT JOIN project_type_links l ON l.project_id=p.id LEFT JOIN project_types pt ON pt.id=l.type_id''')}
    sources = {}
    for row in connection.execute('''SELECT l.task_id,m.sequence,m.received_at,g.code AS group_code
        FROM task_message_links l JOIN task_group_messages m ON m.id=l.message_id
        JOIN task_groups g ON g.id=m.group_id ORDER BY m.id'''):
        sources.setdefault(row['task_id'], []).append(
            f'{row["group_code"]} 消息{row["sequence"]}（{_time(row["received_at"])}）')
    events = {row['id']: row['occurred_at'] for row in connection.execute('SELECT id,occurred_at FROM delivery_events')}
    group_rows, task_rows = [], []
    for group in groups:
        cancelled(cancel_event)
        counts = group['counts']
        if sum(counts[key] for key in ('handed', 'pending', 'withdrawn', 'needs_review')) != counts['total']:
            raise ValueError('任务组状态汇总与任务总数不一致，未导出。')
        group_rows.append(dict(group_code=group['code'], received_day=group['received_day'], title=group['title'],
            ready_count=counts['ready'], handed_count=counts['handed'], pending_count=counts['pending'],
            withdrawn_count=counts['withdrawn'], needs_review_count=counts['needs_review'],
            total_count=counts['total'], message_review_count=group['message_review_count']))
        for task in group['tasks']:
            cancelled(cancel_event)
            material = ('已撤回' if task['withdrawn'] else '需核对' if task['needs_review']
                        else '材料就绪' if task['material_ready'] else '未就绪')
            handoff = ('已撤回' if task['withdrawn'] else '已交接' if task['handed_over']
                       else '曾交接，需补交' if task['ever_handed_over'] else '未交接')
            task_rows.append(dict(task_id=task['id'], task_code=task['code'], group_code=group['code'],
                project_id=task['project_id'], project_name=task['project_name'],
                original_project_name=task['original_project_name'], received_at=task['created_at'],
                source_messages='；'.join(sources.get(task['id'], [])), material_status=material,
                handoff_status=handoff, archive_path=task['archive_path'], event_id=task['event_id'],
                event_occurred_at=events.get(task['event_id']), project_type_name=types[task['project_id']],
                withdrawn=task['withdrawn'], needs_review=task['needs_review']))
    return group_rows, task_rows


def _protected_paths(connection, db):
    """只读取已经登记的边界；旧安装缺少扩展表也不会补建。"""
    tables = _tables(connection)
    roots = {str(db.path.parent / name) for name in ('工作项目', 'ZIP归档', 'packages')}
    files = {str(db.path), str(db.path) + '-wal', str(db.path) + '-shm'}
    for table, columns, target in (
        ('workflow_archive_roots', ('path',), roots),
        ('project_workspaces', ('path', 'group_path'), roots),
        ('project_delivery_archives', ('path', 'source_root'), roots),
        ('zips', ('path',), files), ('work_files', ('path',), files),
    ):
        if table in tables:
            for row in connection.execute(f'SELECT {",".join(columns)} FROM {table}'):
                target.update(str(value) for value in row if value)
    if 'workflow_settings' in tables:
        for row in connection.execute("SELECT value FROM workflow_settings WHERE key IN ('work_root','archive_root')"):
            value = json.loads(row['value'])
            if not isinstance(value, str) or not value:
                raise ValueError('受管目录设置无效，未导出。')
            roots.add(value)
    return sorted(roots), sorted(files)


def _destination(path):
    raw = str(path)
    if not raw.strip():
        raise ValueError('请选择导出文件位置。')
    # Win32 绝对化会先吞掉尾随点，必须在规范化之前拒绝歧义路径。
    if any(part.endswith((' ', '.')) for part in raw.replace('\\', '/').split('/') if part not in ('', '.', '..')):
        raise ValueError('不接受设备名称或不明确的文件路径。')
    result = plain_path(path)
    for part in result.parts[1:]:
        if part.endswith((' ', '.')) or re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', part, re.I):
            raise ValueError('不接受设备名称或不明确的文件路径。')
    if not result.parent.is_dir():
        raise ValueError('导出目录不存在，请选择现有目录。')
    return result


def target_identity(path):
    """保存对话框确认覆盖时记录；不得以后台任务开始时的新标识替代。"""
    path = _destination(path)
    try:
        info = path.stat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('导出目标必须是普通文件。')
    return identity(info)


def _check_protected(path, report):
    if any(path_is_within(path, root) for root in report['protected_roots']):
        raise ValueError('导出位置属于受管项目或归档目录，请另选位置。')
    for protected in report['protected_files']:
        item = Path(protected)
        if os.path.normcase(str(path)) == os.path.normcase(os.path.abspath(item)):
            raise ValueError('不能覆盖数据库或受管材料。')
        if path.exists() and item.exists() and os.path.samefile(path, item):
            raise ValueError('不能覆盖数据库或受管材料的文件别名。')


def _text(value):
    text = str(value)
    if len(text) > 32767:
        raise ValueError('内容超过 Excel 单元格文字上限，未截断或导出。')
    if re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', text):
        raise ValueError('内容包含 Excel 不支持的控制字符，未导出。')
    return text


def _time(value):
    if value is None:
        return ''
    try:
        moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if moment.tzinfo is None:
            return str(value)
        return moment.astimezone(DELIVERY_TIMEZONE).strftime('%Y-%m-%d %H:%M:%S')
    except (ValueError, TypeError, AttributeError):
        return str(value)


def _sheet(book, name, columns, rows, *, cancel_event=None):
    if len(rows) + 1 > 1048576:
        raise ValueError('交付项目超过 Excel 工作表行数上限，未截断或导出。')
    sheet = book.create_sheet(name)
    sheet.freeze_panes = 'A2'
    for column, (_, title, width) in enumerate(columns, 1):
        cell = sheet.cell(1, column, title)
        cell.data_type = 's'
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = PatternFill('solid', fgColor='245A81')
        sheet.column_dimensions[cell.column_letter].width = width
    for index, row in enumerate(rows, 2):
        cancelled(cancel_event)
        for column, (key, _, _) in enumerate(columns, 1):
            value = row.get(key)
            if key in ('first_at', 'latest_at', 'received_at', 'event_occurred_at'):
                value = ('历史已交付，日期未记录' if key == 'first_at' and row.get('prior_delivery')
                         else _time(value))
            elif key == 'archive_file_count' and value is None:
                value = '待核对'
            elif isinstance(value, bool):
                value = '是' if value else '否'
            elif value is None:
                value = ''
            cell = sheet.cell(index, column)
            if isinstance(value, str):
                cell.value = _text(value)
                cell.data_type = 's'
            else:
                cell.value = value
            cell.alignment = Alignment(vertical='top', wrap_text=True)
    sheet.auto_filter.ref = sheet.dimensions
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_setup.orientation = 'landscape'
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.print_title_rows = '1:1'
    return sheet


def _discard_export_staging(path, created_identity):
    try:
        if os.name == 'nt':
            with ZipLease(path) as owned:
                if identity(owned.initial)[:2] == created_identity:
                    owned.remove_after_verified_commit()
        elif identity(path.lstat())[:2] == created_identity:
            path.unlink()
    except (OSError, RuntimeError, ValueError):
        # 名称已被替换或占用时，保留现场及原异常。
        pass


def _publish_windows(staged, path, expected_target_identity, cancel_event):
    if expected_target_identity is None:
        cancelled(cancel_event)
        staged.rename_to(path)
        return None

    with ZipLease(path) as previous:
        if identity(previous.initial) != expected_target_identity:
            raise ValueError('导出期间目标文件已经变化，未覆盖。')
        cancelled(cancel_event)
        backup = path.with_name(f'.workbox-export-recovery-{uuid4().hex}-{path.stem[:64]}.xlsx')
        # 只有用户确认的句柄能移出原路径，随后使用不覆盖的发布与恢复。
        previous.rename_to(backup)
        try:
            cancelled(cancel_event)
            staged.rename_to(path)
        except BaseException:
            try:
                previous.rename_to(path)
            except (OSError, ValueError) as error:
                raise OSError(f'导出未完成，原文件已保留在 {backup}。无法恢复原位置：{error}') from error
            raise
        try:
            previous.remove_after_verified_commit()
        except OSError:
            return backup
    return None


def _publish_workbook(db, book, path, expected_target_identity, *, cancel_event=None):
    """落盘后复核导出路径；Windows 发布和清理均使用已核对的文件句柄。"""
    cancelled(cancel_event)
    temporary = None
    created_identity = None
    try:
        with tempfile.NamedTemporaryFile(prefix='.workbox-export-', suffix='.xlsx',
                                         dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            opened = os.fstat(stream.fileno())
            if not opened.st_ino:
                raise OSError('无法核对导出临时文件标识，未导出。')
            created_identity = identity(opened)[:2]
            book.save(stream)
            stream.flush()
            os.fsync(stream.fileno())
            cancelled(cancel_event)
            saved_identity = identity(os.fstat(stream.fileno()))
            if os.name == 'nt':
                stream.seek(0)
                saved_digest = file_digest(stream, 'sha256').hexdigest()
        cancelled(cancel_event)
        # 报表保留开始时的业务快照；发布边界必须使用此刻的受管目录。
        with getattr(db, '_operation_lock', None) or nullcontext():
            cancelled(cancel_event)
            _destination(path)
            with db.connect() as connection:
                connection.execute('BEGIN')
                roots, files = _protected_paths(connection, db)
            _check_protected(path, dict(protected_roots=roots, protected_files=files))
            if expected_target_identity is not None and target_identity(path) != expected_target_identity:
                raise ValueError('导出期间目标文件已经变化，未覆盖。')
            if os.name == 'nt':
                with ZipLease(temporary) as staged:
                    if identity(staged.initial) != saved_identity or (
                            staged.inspect(cancel_event=cancel_event)['sha256'] != saved_digest):
                        raise ValueError('导出临时文件已经变化，未发布。')
                    recovery_path = _publish_windows(staged, path, expected_target_identity, cancel_event)
                created_identity = None
                return recovery_path
            if identity(temporary.lstat()) != saved_identity:
                raise ValueError('导出临时文件已经变化，未发布。')
            cancelled(cancel_event)
            if expected_target_identity is not None:
                os.replace(temporary, path)
                created_identity = None
            else:
                # 非 Windows 的 rename 会覆盖，硬链接提供同卷的独占发布。
                os.link(temporary, path)
    finally:
        if created_identity is not None:
            _discard_export_staging(temporary, created_identity)
    return None


@contextmanager
def _workbook_export(db, destination, collect_report, *, cancel_event=None, progress=None,
                     replace_existing=False, expected_target_identity=None):
    """使用只读报表填充工作簿，完成后统一安全发布。"""
    cancelled(cancel_event)
    if type(replace_existing) is not bool:
        raise ValueError('覆盖选项无效。')
    path = _destination(destination)
    report = collect_report(db, cancel_event=cancel_event, progress=progress)
    _check_protected(path, report)
    initial = target_identity(path)
    if replace_existing:
        if expected_target_identity is None or initial != list(expected_target_identity):
            raise ValueError('覆盖目标的文件标识缺失或已经变化，请重新选择。')
    elif initial is not None:
        raise FileExistsError('导出文件已经存在，请另选名称或明确确认覆盖。')
    if path.suffix.lower() != '.xlsx':
        raise ValueError('导出文件必须使用 .xlsx 扩展名。')
    book = Workbook()
    result = dict(path=str(path), project_count=report['project_count'])
    try:
        yield book, report, result
        recovery_path = _publish_workbook(db, book, path, initial, cancel_event=cancel_event)
        if recovery_path is not None:
            result['recovery_path'] = str(recovery_path)
        if progress:
            progress(1, 1)
    finally:
        book.close()


def export_pending_projects(db, destination, *, cancel_event=None, progress=None,
                            replace_existing=False, expected_target_identity=None):
    """将全部日常待开发项目的名称导出为单列工作簿。"""
    with _workbook_export(db, destination, collect_pending_project_names, cancel_event=cancel_event,
                          progress=progress, replace_existing=replace_existing,
                          expected_target_identity=expected_target_identity) as (book, report, result):
        names = report['project_names']
        if len(names) + 1 > 1048576:
            raise ValueError('待开发项目超过 Excel 工作表行数上限，未截断或导出。')
        sheet = book.active
        sheet.title = '待开发项目'
        sheet['A1'] = '项目名称'
        sheet.column_dimensions['A'].width = 42
        for index, name in enumerate(names, 2):
            cancelled(cancel_event)
            # XML 读取会归一化回车，且 U+FFFE/U+FFFF 会让工作簿无法读取。
            if re.search(r'[\r\ufffe\uffff]', name):
                raise ValueError('项目名称含有 XLSX 无法原样保存的字符，未导出。')
            value = _text(name)
            if re.search(r'_x[0-9A-Fa-f]{4}_', value):
                # 分开写入普通文本段，避免 Excel 将名称中的字面量解释为字符转义。
                value = CellRichText(re.split(r'(?<=_)(?=x[0-9A-Fa-f]{4}_)', value))
            sheet.cell(index, 1, value).data_type = 's'
    return result


def export_delivered_projects(db, destination, *, cancel_event=None, progress=None,
                              replace_existing=False, expected_target_identity=None, group_id=None):
    """导出交付项目、类型汇总、任务组汇总和任务明细。"""
    with _workbook_export(db, destination, partial(collect_delivery_report, group_id=group_id),
                          cancel_event=cancel_event, progress=progress, replace_existing=replace_existing,
                          expected_target_identity=expected_target_identity) as (book, report, result):
        book.remove(book.active)
        project_sheet = _sheet(book, '交付项目', PROJECT_COLUMNS, report['projects'], cancel_event=cancel_event)
        batch_note = ('与项目列表的收件批次编号一致，同一项目可有多个编号。'
                      '原“批次”列保留建项批次，编号不改变项目目录或状态。')
        if report['batch_warnings']:
            warnings = report['batch_warnings']
            batch_note += '\n部分批次来源需核对：\n' + '\n'.join(warnings[:20])
            if len(warnings) > 20:
                batch_note += f'\n另有 {len(warnings) - 20} 条来源提示，请在项目工作台查看。'
        project_sheet.cell(1, len(PROJECT_COLUMNS)).comment = Comment(batch_note, 'WorkBox')
        summary = _sheet(book, '类型汇总', SUMMARY_COLUMNS, report['type_counts'], cancel_event=cancel_event)
        # 汇总总计在筛选范围之外，避免被当成一个项目类型或随筛选隐藏。
        total_row = summary.max_row + 2
        for column, value in enumerate(('合计', report['project_count'], report['eligible_project_count'],
                                        report['workload_project_count']), 1):
            cell = summary.cell(total_row, column, value)
            if isinstance(value, str):
                cell.data_type = 's'
            cell.font = Font(bold=True)
            cell.fill = PatternFill('solid', fgColor='E5EFF7')
        book['类型汇总']['A1'].comment = Comment(
            '按当前类型统计，每个项目只列一次，含归档及已删除项目。'
            '工作量资格表示允许计入个人工作量；首交计数表示该项目首次有效交付纳入统计。'
            '已注明历史交付但首次日期未记录的项目保留工作量资格，补交不增加首交计数。'
            '已撤销记录不参与，代交项目仍列入交付项目数。合计不随类型筛选改变。', 'WorkBox')
        _sheet(book, '任务组汇总', TASK_GROUP_COLUMNS, report['task_group_rows'], cancel_event=cancel_event)
        _sheet(book, '任务明细', TASK_COLUMNS, report['task_rows'], cancel_event=cancel_event)
        book['任务组汇总']['A1'].comment = Comment(
            '任务与项目分开统计；同一项目多轮任务分别列出。已交接、未交接（已确定）、撤回、'
            '待核对任务数互斥并合计为总任务数。材料就绪是另一个维度，不参与相加。'
            '待核对消息可能尚未形成任务，单独列出，不混入任务数。', 'WorkBox')
        book['任务明细']['A1'].comment = Comment(
            '材料就绪来自本任务明确绑定且当前有效的交付版本；手工交接来自用户确认。'
            '撤销、恢复或重新打开后，旧交接不会自动恢复。关联归档保留原绑定的确切历史位置，'
            '不能仅因有归档位置就认定本次材料有效或已交接。', 'WorkBox')
        if group_id is not None:
            book.active = book.sheetnames.index('任务明细' if report['task_rows'] else '任务组汇总')
    result.update(workload_project_count=report['workload_project_count'], type_counts=report['type_counts'],
                  task_count=report['task_count'], handed_task_count=report['handed_task_count'],
                  group_id=report['group_id'], group_code=report['group_code'])
    return result
