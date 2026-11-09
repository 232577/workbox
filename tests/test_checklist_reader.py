"""Anonymous external-list cases: no inferred identity, dates or writes."""
from datetime import date
from importlib import import_module, util

import pytest


def reader():
    assert util.find_spec('checklist_reader') is not None, 'Read-only checklist reader not implemented'
    return import_module('checklist_reader')


def test_csv_read_preserves_source_rows_multiline_and_duplicates(tmp_path):
    path = tmp_path/'anonymous.csv'
    path.write_text('日期,软件名称\n2026-11-09,"示例甲系统\n示例乙系统"\n,示例甲系统\n', encoding='utf-8-sig')
    before = path.read_bytes()
    book = reader().read_tables(path)
    rows = reader().extract_entries(book[0], 1, 0)
    assert [r['name'] for r in rows] == ['示例甲系统', '示例乙系统', '示例甲系统']
    assert [r['row'] for r in rows] == [2, 2, 3]
    assert [r['date'] for r in rows] == ['2026-11-09', '2026-11-09', '']
    assert rows[0]['duplicate'] and rows[2]['duplicate']
    assert not rows[1]['duplicate']
    assert path.read_bytes() == before


def test_xlsx_sheet_choice_and_real_dates(tmp_path):
    from openpyxl import Workbook
    path = tmp_path/'anonymous.xlsx'
    book = Workbook(); book.active.title = '第一组'
    book.active.append(['软件名称', '日期']); book.active.append(['示例甲系统', date(2026, 11, 9)])
    other = book.create_sheet('第二组'); other.append(['项目名称']); other.append(['示例乙系统'])
    book.save(path); book.close()
    tables = reader().read_tables(path)
    assert [t['name'] for t in tables] == ['第一组', '第二组']
    assert reader().suggest_columns(tables[0]) == (0, 1)
    rows = reader().extract_entries(tables[0], 0, 1)
    assert rows[0]['date'] == '2026-11-09'
    assert reader().extract_entries(tables[1], 0)[0]['name'] == '示例乙系统'


@pytest.mark.parametrize('value', ['11/09', '2026-02-30', '明天', ''])
def test_partial_or_bad_dates_are_not_invented(value):
    item = reader().extract_entries({'name': 'sheet', 'rows': [[value, '示例系统']]}, 1, 0)[0]
    assert item['date'] == ''
    assert item['source_date'] == value
    assert reader().filter_entries([item], '2026-01-01', '2026-12-31') == []


def test_date_range_is_inclusive_and_invalid_range_is_rejected():
    r = reader()
    rows = r.extract_entries({'name': 's', 'rows': [['2026/11/08', '甲系统'], ['2026年11月09日', '乙系统'], ['2026-11-10', '丙系统']]}, 1, 0)
    assert [x['name'] for x in r.filter_entries(rows, '2026-11-08', '2026-11-09')] == ['甲系统', '乙系统']
    with pytest.raises(ValueError): r.filter_entries(rows, '2026-11-10', '2026-11-09')


def test_formulas_rejected_without_using_cached_results(tmp_path):
    from openpyxl import Workbook
    path = tmp_path/'formula.xlsx'; b = Workbook(); b.active.append(['软件名称']); b.active.append(['="示例系统"']); b.save(path); b.close()
    with pytest.raises(ValueError, match='公式'): reader().read_tables(path)


def test_csv_formula_is_literal_text_not_executed(tmp_path):
    path = tmp_path/'literal.csv'; path.write_text('软件名称\n=HYPERLINK("https://example.invalid")\n', encoding='utf-8')
    assert reader().extract_entries(reader().read_tables(path)[0], 0)[0]['name'].startswith('=HYPERLINK')


@pytest.mark.parametrize('extension', ['.xls', '.exe', '.zip'])
def test_unsupported_input_refused(tmp_path, extension):
    path = tmp_path/('data'+extension); path.write_bytes(b'data')
    with pytest.raises(ValueError): reader().read_tables(path)


def test_import_limits_are_checked_and_empty_rows_are_not_projects(tmp_path):
    r = reader(); path = tmp_path/'large.csv'; path.write_bytes(b'a'*(r.MAX_BYTES+1))
    with pytest.raises(ValueError): r.read_tables(path)
    with pytest.raises(ValueError): r.extract_entries({'name': 's', 'rows': [['名称'], [''], ['合计']]}, 0)
    with pytest.raises(ValueError): r.extract_entries({'name': 's', 'rows': [['示例系统']]}, -1)


def test_cancelled_import_never_returns_partial_success(tmp_path):
    from concurrent.futures import CancelledError
    from threading import Event
    path = tmp_path/'source.csv'; path.write_text('软件名称\n示例系统', encoding='utf-8')
    event = Event(); event.set()
    with pytest.raises(CancelledError): reader().read_tables(path, cancel_event=event)
