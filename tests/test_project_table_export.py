"""Project-table export keeps the explicitly chosen scope and never writes business data."""
import threading
from concurrent.futures import CancelledError
from contextlib import contextmanager

import pytest
from openpyxl import load_workbook

import delivery_export as exports
from database import Database


@pytest.fixture
def db(tmp_path):
    db = Database(tmp_path / 'anonymous.db')
    db.create_capture('匿名原文', '匿名批次', [('甲项目', False), ('乙项目', True), ('范围外项目', False)])
    with db.connect() as connection:
        connection.execute("UPDATE projects SET created_at='2026-11-11T16:00:00Z' WHERE id=1")
        connection.execute("UPDATE projects SET created_at='2026-11-11T15:59:59Z',status='待交付' WHERE id=2")
    return db


def collect(db, ids, **options):
    fn = getattr(exports, 'collect_project_table', None)
    assert fn is not None, '项目表需要独立的只读范围快照'
    return fn(db, project_ids=ids, **options)


def export(db, target, ids, **options):
    fn = getattr(exports, 'export_project_table', None)
    assert fn is not None, '缺少按筛选或勾选范围导出项目表的功能'
    return fn(db, target, project_ids=ids, **options)


def test_selected_scope_keeps_order_current_type_and_shanghai_creation_date(db):
    custom = db.create_project_type('匿名定制类型')
    db.set_project_types([1], custom['id'])
    db.set_project_type_active(custom['id'], False)
    report = collect(db, (2, 1, 2))
    assert [row['project_id'] for row in report['projects']] == [2, 1]
    assert [row['added_on'] for row in report['projects']] == ['2026-11-11', '2026-11-12']
    assert report['projects'][0]['status'] == '待交付'
    assert report['projects'][1]['project_type_name'] == '匿名定制类型'
    assert all(row['intake_batch_codes'] for row in report['projects'])
    assert report['project_count'] == 2


@pytest.mark.parametrize('ids', [None, (), [], '1', (True,), (0,), (-1,), (1.0,), (1, 999)])
def test_invalid_or_stale_scope_never_falls_back_to_all_projects(db, ids, tmp_path):
    target = tmp_path / '项目表.xlsx'
    with pytest.raises(ValueError):
        export(db, target, ids)
    assert not target.exists()


def test_merged_selection_requires_refresh_instead_of_exporting_a_different_project(db):
    with db.connect() as connection:
        connection.execute("INSERT INTO project_merges(source_id,target_id,operation_id,snapshot_json) "
                           "VALUES (2,1,'anonymous-merge','{}')")
    with pytest.raises(ValueError, match='刷新'):
        collect(db, (2, 1))
    # 保留项目的首次新增日期不由合并进来的较早项目日期替换。
    assert collect(db, (1,))['projects'][0]['added_on'] == '2026-11-12'


def test_explicit_archived_deleted_rows_are_labelled_and_unknown_date_stays_unknown(db):
    db.bulk_update_projects([1], archived=True)
    db.delete_project(2, confirmed=True)
    with db.connect() as connection:
        connection.execute("UPDATE projects SET created_at='unknown' WHERE id=1")
    report = collect(db, (1, 2))
    assert [row['visibility'] for row in report['projects']] == ['已归档', '已删除']
    assert report['projects'][0]['added_on'] == '日期未知'


def test_rows_batch_markers_and_boundaries_use_one_read_only_snapshot(db, tmp_path, monkeypatch):
    with db.connect() as connection:
        connection.execute('CREATE TABLE workflow_archive_roots(path TEXT PRIMARY KEY)')
    original = exports.read_project_batch_index
    later = str(tmp_path / 'later-root')

    def concurrent_update(connection):
        assert connection.in_transaction
        assert connection.execute('PRAGMA query_only').fetchone()[0] == 1
        with db.connect() as writer:
            writer.execute("UPDATE projects SET name='后来改名',status='开发中' WHERE id=1")
            writer.execute('INSERT INTO workflow_archive_roots VALUES (?)', (later,))
        return original(connection)

    monkeypatch.setattr(exports, 'read_project_batch_index', concurrent_update)
    report = collect(db, (1,))
    assert report['projects'][0]['project_name'] == '甲项目'
    assert report['projects'][0]['status'] == '待开发'
    assert later not in report['protected_roots']


def test_workbook_scope_literal_values_filters_and_database_unchanged(db, tmp_path):
    with db.connect() as connection:
        connection.execute("UPDATE projects SET name='=1+1' WHERE id=1")
        connection.execute("UPDATE projects SET name='_x0041_' WHERE id=2")
        before = list(connection.iterdump())
    target = tmp_path / '项目表.xlsx'
    result = export(db, target, (2, 1), scope_label='当前筛选结果 · 新增日期 2026-11-12 · 全部状态')
    assert result['project_count'] == 2
    with db.connect() as connection:
        assert list(connection.iterdump()) == before
    book = load_workbook(target)
    try:
        sheet = book['项目清单']
        assert list(sheet.values)[0][:4] == ('项目名称', '当前状态', '项目类型', '首次新增日期')
        assert [sheet.cell(i, 1).value for i in (2, 3)] == ['_x0041_', '=1+1']
        assert sheet.cell(3, 1).data_type == 's'
        assert sheet.freeze_panes == 'A2' and sheet.auto_filter.ref
        assert '当前筛选结果' in str(list(book['导出说明'].values))
        assert '重复收件不改变' in str(list(book['导出说明'].values))
    finally:
        book.close()


def test_project_identifier_is_preserved_as_text_beyond_excel_numeric_precision(db, tmp_path):
    pid = 9007199254740993
    with db.connect() as connection:
        seed = connection.execute('SELECT message_id,batch_id FROM projects LIMIT 1').fetchone()
        connection.execute('INSERT INTO projects(id,message_id,batch_id,name) VALUES (?,?,?,?)',
                           (pid, seed[0], seed[1], '匿名大编号项目'))
    target = tmp_path / '项目表.xlsx'
    export(db, target, (pid,))
    book = load_workbook(target)
    try:
        cell = book['项目清单'].cell(2, 10)
        assert cell.value == str(pid)
        assert cell.data_type == 's'
    finally:
        book.close()


@pytest.mark.parametrize('change', ['create', 'replace', 'managed', 'cancel'])
def test_table_export_preserves_atomic_publish_guards(db, tmp_path, monkeypatch, change):
    target = tmp_path / '项目表.xlsx'
    event = threading.Event()
    options = dict(cancel_event=event)
    if change in ('replace', 'cancel'):
        target.write_bytes(b'original')
        options.update(replace_existing=True, expected_target_identity=exports.target_identity(target))
    with db.connect() as connection:
        connection.execute('CREATE TABLE workflow_archive_roots(path TEXT PRIMARY KEY)')
    original_save = exports.Workbook.save

    def save_then_change(book, stream):
        original_save(book, stream)
        if change in ('create', 'replace'):
            target.write_bytes(b'external')
        elif change == 'managed':
            with db.connect() as connection:
                connection.execute('INSERT INTO workflow_archive_roots VALUES (?)', (str(tmp_path),))
        else:
            event.set()

    monkeypatch.setattr(exports.Workbook, 'save', save_then_change)
    with pytest.raises({'create': FileExistsError, 'replace': ValueError, 'managed': ValueError,
                        'cancel': CancelledError}[change]):
        export(db, target, (1,), **options)
    if change == 'managed':
        assert not target.exists()
    else:
        assert target.read_bytes() == (b'original' if change == 'cancel' else b'external')
    assert not list(tmp_path.glob('.workbox-export-*'))


def test_large_explicit_scope_has_no_500_item_limit_or_per_project_fetch(db, monkeypatch):
    with db.connect() as connection:
        seed = connection.execute('SELECT message_id,batch_id FROM projects LIMIT 1').fetchone()
        connection.executemany('INSERT INTO projects(message_id,batch_id,name) VALUES (?,?,?)',
                               [(seed[0], seed[1], f'匿名大清单{i}') for i in range(1100)])
    ids = tuple(range(1, 1104))
    statements = []
    original_connect = db.connect

    @contextmanager
    def traced_connect():
        with original_connect() as connection:
            connection.set_trace_callback(statements.append)
            yield connection

    monkeypatch.setattr(db, 'connect', traced_connect)
    report = collect(db, ids)
    assert report['project_count'] == len(ids)
    assert [row['project_id'] for row in report['projects']] == list(ids)
    assert sum('SELECT p.id AS project_id' in statement for statement in statements) == 2
    assert sum(statement.lstrip().upper().startswith('SELECT') for statement in statements) < 25
