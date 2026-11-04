"""全部有效交付项目的只读快照与本机 XLSX 安全导出。"""
from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill

from archive_facts import delivery_archive_facts
from database import DELIVERY_TIMEZONE
from native_files import cancelled, identity, path_is_within, plain_path


PROJECT_COLUMNS = (
    ('project_name', '项目名称', 42), ('project_type_name', '类型', 18),
    ('archive_file_count', '最近交付归档文件数', 24), ('first_at', '首次有效交付时间', 24),
    ('latest_at', '最近交付时间', 24), ('valid_delivery_count', '有效交付次数', 18),
    ('included_in_workload', '计入工作量', 18), ('status', '当前状态', 16),
    ('batch_name', '批次', 28), ('archive_path', '归档位置', 58),
    ('visibility', '项目可见性', 18), ('project_id', '项目编号', 14),
    ('counted', '首交计数', 18), ('archive_reason', '归档数量说明', 48),
)
SUMMARY_COLUMNS = (
    ('type_name', '当前类型', 28), ('project_count', '交付项目数（去重）', 25),
    ('eligible_project_count', '具备工作量资格的项目数', 30),
    ('workload_project_count', '纳入首交统计的项目数', 32),
)


def _tables(connection):
    return {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def collect_delivery_report(db, *, cancel_event=None, progress=None):
    """一个读取事务内归一项目、撤销、类型、数量事实；不创建或修复表。"""
    cancelled(cancel_event)
    with db.connect() as connection:
        connection.execute('BEGIN')
        history = db.delivery_history(connection=connection)
        facts = delivery_archive_facts(connection, history['events'])
        current = {row['id']: dict(row) for row in connection.execute('SELECT id,name,status FROM projects')}
        protected_roots, protected_files = _protected_paths(connection, db)
    grouped = {}
    for index, event in enumerate(history['events']):
        cancelled(cancel_event)
        if event['voided']:
            continue
        pid = event['project_id']
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
                protected_roots=protected_roots, protected_files=protected_files)


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
            if key in ('first_at', 'latest_at'):
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


def export_delivered_projects(db, destination, *, cancel_event=None, progress=None,
                              replace_existing=False, expected_target_identity=None):
    """同目录独占临时文件完成并落盘后发布；失败仅清理本次临时文件。"""
    cancelled(cancel_event)
    if type(replace_existing) is not bool:
        raise ValueError('覆盖选项无效。')
    path = _destination(destination)
    report = collect_delivery_report(db, cancel_event=cancel_event, progress=progress)
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
    book.remove(book.active)
    temporary = None
    try:
        _sheet(book, '交付项目', PROJECT_COLUMNS, report['projects'], cancel_event=cancel_event)
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
        cancelled(cancel_event)
        with tempfile.NamedTemporaryFile(prefix='.workbox-export-', suffix='.xlsx',
                                         dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            book.save(stream)
            stream.flush()
            os.fsync(stream.fileno())
        cancelled(cancel_event)
        # 报表保留开始时的业务快照；发布边界必须使用此刻的受管目录。
        # 与工作设置共用操作锁，避免复核后本进程再把目标变成受管目录。
        with getattr(db, '_operation_lock', None) or nullcontext():
            cancelled(cancel_event)
            _destination(path)
            with db.connect() as connection:
                connection.execute('BEGIN')
                roots, files = _protected_paths(connection, db)
            _check_protected(path, dict(protected_roots=roots, protected_files=files))
            if replace_existing:
                if target_identity(path) != initial:
                    raise ValueError('导出期间目标文件已经变化，未覆盖。')
                os.replace(temporary, path)
            elif os.name == 'nt':
                # Windows rename 原子发布且拒绝覆盖已有目标。
                os.rename(temporary, path)
            else:
                # 其他平台 rename 会覆盖，硬链接创建提供同卷的独占发布。
                os.link(temporary, path)
                temporary.unlink()
        temporary = None
        if progress:
            progress(1, 1)
        return dict(path=str(path), project_count=report['project_count'],
                    workload_project_count=report['workload_project_count'], type_counts=report['type_counts'])
    finally:
        book.close()
        if temporary is not None:
            temporary.unlink(missing_ok=True)
