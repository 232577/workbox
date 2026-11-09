"""Bounded, read-only external name lists. No project creation or date guessing."""
from __future__ import annotations

import csv
import io
import re
import unicodedata
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from native_files import cancelled

MAX_BYTES = 10 * 1024 * 1024
MAX_EXPANDED = 80 * 1024 * 1024
MAX_ROWS, MAX_COLUMNS, MAX_ENTRIES = 5000, 100, 2000
NAME_HEADERS = frozenset(('软件名称', '软件名', '系统名称', '项目名称', '名称'))


def normalized_name(value: str) -> str:
    return ' '.join(unicodedata.normalize('NFKC', value).split()).casefold()


def explicit_date(value) -> str:
    """Return ISO only when the source actually supplies a valid year."""
    if isinstance(value, (datetime, date)):
        return value.strftime('%Y-%m-%d')
    match = re.fullmatch(r'(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?', str(value or '').strip())
    if match is None:
        return ''
    try:
        return date(*(int(v) for v in match.groups())).isoformat()
    except ValueError:
        return ''


def _bounded_rows(rows, cancel_event):
    matrix = []
    for row in rows:
        cancelled(cancel_event)
        if len(matrix) >= MAX_ROWS or len(row) > MAX_COLUMNS:
            raise ValueError('清单最多 5000 行、100 列，请缩小范围后重试。')
        if any(len(str(v)) > 32768 for v in row):
            raise ValueError('清单单元格内容过长，请仅保留名称与日期。')
        matrix.append(list(row))
    return matrix


def read_tables(path, *, cancel_event=None, progress=None) -> list[dict]:
    """Take one bounded byte snapshot. Never extract, execute or rewrite input."""
    cancelled(cancel_event)
    path = Path(path)
    suffix = path.suffix.casefold()
    if suffix not in ('.xlsx', '.csv', '.tsv'):
        raise ValueError('请选择 XLSX、CSV 或 TSV；旧 XLS 请先另存为 XLSX。')
    with path.open('rb') as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError('清单文件超过 10 MB，请仅保留需要核对的内容。')
    cancelled(cancel_event)
    if suffix in ('.csv', '.tsv'):
        try:
            text = data.decode('utf-8-sig')
        except UnicodeDecodeError:
            try:
                text = data.decode('gb18030')
            except UnicodeDecodeError as error:
                raise ValueError('清单编码无法读取，请另存为 UTF-8 CSV。') from error
        rows = _bounded_rows(csv.reader(io.StringIO(text), delimiter='\t' if suffix == '.tsv' else ','), cancel_event)
        tables = [dict(name=path.stem, rows=rows)]
    else:
        try:
            with ZipFile(io.BytesIO(data)) as archive:
                if len(archive.infolist()) > 10000 or sum(i.file_size for i in archive.infolist()) > MAX_EXPANDED:
                    raise ValueError('表格展开内容过大，请缩小清单后重试。')
            from openpyxl import load_workbook
            book = load_workbook(io.BytesIO(data), read_only=True, data_only=False, keep_links=False)
            try:
                if len(book.worksheets) > 30:
                    raise ValueError('清单工作表不能超过 30 个。')
                tables = []
                for sheet in book.worksheets:
                    cancelled(cancel_event)
                    if (sheet.max_row or 0) > MAX_ROWS or (sheet.max_column or 0) > MAX_COLUMNS:
                        raise ValueError('清单最多 5000 行、100 列，请缩小范围后重试。')
                    def cells():
                        for row in sheet.iter_rows():
                            if any(cell.data_type == 'f' for cell in row):
                                raise ValueError('表格含公式，请粘贴为值后再导入。')
                            yield [cell.value if cell.value is not None else '' for cell in row]
                    tables.append(dict(name=sheet.title, rows=_bounded_rows(cells(), cancel_event)))
            finally:
                book.close()
        except (BadZipFile, KeyError, EOFError, SyntaxError) as error:
            raise ValueError('表格无法读取，请重新另存为 XLSX。') from error
    if not tables or not any(str(v).strip() for t in tables for row in t['rows'] for v in row):
        raise ValueError('清单为空。')
    cancelled(cancel_event)
    return tables


def suggest_columns(table: dict) -> tuple[int, int]:
    rows = table['rows'][:100]
    width = max((len(row) for row in rows), default=1)
    width = max(1, width)
    scores, dates = [0] * width, [0] * width
    for row in rows:
        for i, value in enumerate(row):
            text = str(value).strip()
            scores[i] += 100 if text in NAME_HEADERS else int(bool(re.search('系统|软件|平台|APP', text, re.I)))
            dates[i] += 100 if text in ('日期', '清单日期', '收件日期') else int(bool(explicit_date(value)))
    name_col = max(range(width), key=scores.__getitem__)
    day_col = max(range(width), key=dates.__getitem__)
    return name_col, day_col if dates[day_col] and day_col != name_col else -1


def extract_entries(table: dict, name_column: int, date_column: int = -1, *, cancel_event=None) -> list[dict]:
    rows = table['rows']
    width = max((len(row) for row in rows), default=0)
    if (type(name_column) is not int or not 0 <= name_column < width
            or type(date_column) is not int or not -1 <= date_column < width or date_column == name_column):
        raise ValueError('请选择有效且不同的名称列与日期列。')
    result = []
    for number, row in enumerate(rows, 1):
        cancelled(cancel_event)
        if name_column >= len(row):
            continue
        raw_date = row[date_column] if 0 <= date_column < len(row) else ''
        text_date = explicit_date(raw_date)
        for part in str(row[name_column] if row[name_column] is not None else '').splitlines():
            name = re.sub(r'^\s*\d{1,3}\s*[、.．)）]\s*', '', part).strip()
            if not name or name in NAME_HEADERS or name in ('合计', '总计'):
                continue
            if len(name) > 200 or any(ord(c) < 32 for c in name):
                raise ValueError(f'第 {number} 行的名称无效或超过 200 字符。')
            result.append(dict(name=name, date=text_date, source_date=str(raw_date or ''),
                               row=number, sheet=table['name'], duplicate=False))
            if len(result) > MAX_ENTRIES:
                raise ValueError('一次最多核对 2000 个名称，请缩小清单。')
    if not result:
        raise ValueError('所选列没有可核对的名称。')
    counts = Counter(normalized_name(row['name']) for row in result)
    for row in result:
        row['duplicate'] = counts[normalized_name(row['name'])] > 1
    return result


def filter_entries(entries, first=None, last=None):
    if first is None and last is None:
        return list(entries)
    if not first or not last or explicit_date(first) != first or explicit_date(last) != last or first > last:
        raise ValueError('请选择有效的开始和结束日期，开始日期不能晚于结束日期。')
    return [row for row in entries if row['date'] and first <= row['date'] <= last]
