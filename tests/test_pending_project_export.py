"""待开发项目名单的只读范围、单列格式与安全保存。"""
import os
import re
import threading
from concurrent.futures import CancelledError
from xml.etree import ElementTree
from zipfile import ZipFile

import pytest
from openpyxl import load_workbook

import delivery_export
from database import Database


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / 'isolated.db')


def collect_names(db):
    collect = getattr(delivery_export, 'collect_pending_project_names', None)
    assert collect is not None, '缺少待开发项目名单读取功能'
    return collect(db)


def export_names(db, target, **options):
    export = getattr(delivery_export, 'export_pending_projects', None)
    assert export is not None, '缺少待开发项目名单导出功能'
    return export(db, target, **options)


def test_pending_names_are_canonical_and_keep_global_project_order(db):
    db.create_capture('匿名消息一', '匿名批次一', [
        ('同名项目', False), ('按期项目', False), ('加急项目', True),
        ('合并来源', False), ('归档项目', False), ('删除项目', False),
        ('开发中项目', False), ('待交付项目', False), ('已交付项目', False),
    ])
    db.create_capture('匿名消息二', '匿名批次二', [('同名项目', False), ('新收项目', False)])
    db.update_project_details(2, '', '2026-11-20')
    db.bulk_update_projects([5], archived=True)
    db.delete_project(6, confirmed=True)
    db.bulk_update_projects([7], status='开发中')
    db.bulk_update_projects([8], status='待交付')
    with db.connect() as connection:
        connection.execute("UPDATE projects SET status='已交付' WHERE id=9")
        connection.execute("INSERT INTO project_merges(source_id,target_id,operation_id,snapshot_json) "
                           "VALUES (4,1,'isolated-merge','{}')")
    report = collect_names(db)
    assert report['project_names'] == ['加急项目', '按期项目', '新收项目', '同名项目', '同名项目']
    assert report['project_count'] == 5


def test_pending_names_and_protected_paths_share_one_snapshot(db, tmp_path, monkeypatch):
    db.create_capture('匿名消息', '匿名批次', [('快照内名称', False)])
    with db.connect() as connection:
        connection.execute('CREATE TABLE workflow_archive_roots(path TEXT PRIMARY KEY)')
    list_projects = db.list_projects
    later_root = str(tmp_path / 'later-root')

    def update_after_project_query(*args, **kwargs):
        rows = list_projects(*args, **kwargs)
        with db.connect() as writer:
            writer.execute("UPDATE projects SET name='快照后名称' WHERE id=1")
            writer.execute('INSERT INTO workflow_archive_roots VALUES (?)', (later_root,))
        return rows

    with monkeypatch.context() as patch:
        patch.setattr(db, 'list_projects', update_after_project_query)
        report = collect_names(db)
    assert report['project_names'] == ['快照内名称']
    assert later_root not in report['protected_roots']
    current = collect_names(db)
    assert current['project_names'] == ['快照后名称']
    assert later_root in current['protected_roots']


@pytest.mark.parametrize('names', [[], ['=1+1', '#N/A', '+0002', '  完整 名称  ', '长' * 32767]],
                         ids=['empty', 'literal-text'])
def test_pending_xlsx_has_only_names_and_does_not_change_database(db, tmp_path, names):
    for index, name in enumerate(reversed(names), 1):
        db.create_capture(f'匿名消息{index}', '匿名批次', [('占位项目', False)])
        with db.connect() as connection:
            connection.execute('UPDATE projects SET name=? WHERE id=?', (name, index))
    with db.connect() as connection:
        before = list(connection.iterdump())
    target = tmp_path / '名单.xlsx'
    result = export_names(db, target)
    assert result['path'] == str(target) and result['project_count'] == len(names)
    with db.connect() as connection:
        assert list(connection.iterdump()) == before
    book = load_workbook(target)
    try:
        assert book.sheetnames == ['待开发项目']
        sheet = book.active
        assert sheet.max_column == 1
        assert [cell.value for cell in sheet['A']] == ['项目名称', *names]
        assert all(cell.data_type == 's' for cell in sheet['A'])
    finally:
        book.close()


def test_pending_xlsx_preserves_names_that_look_like_excel_character_escapes(db, tmp_path):
    names = [
        '匿名_x0041_项目', '_x0041_', '_x0041__x0042_', '_x0041_x0042_',
        '前_x005F_后', '前_x000D_后', '前_xaBcD_后', '_X0041_项目',
        '普通中文项目', '=HYPERLINK("_x0041_")',
    ]
    db.create_capture('匿名消息', '匿名批次', [(name, False) for name in reversed(names)])
    target = tmp_path / '名单.xlsx'
    export_names(db, target)

    book = load_workbook(target)
    try:
        assert book.sheetnames == ['待开发项目']
        assert book.active.max_column == 1
        assert [cell.value for cell in book.active['A']] == ['项目名称', *names]
        assert all(cell.data_type == 's' for cell in book.active['A'])
    finally:
        book.close()

    with ZipFile(target) as archive:
        worksheet = ElementTree.fromstring(archive.read('xl/worksheets/sheet1.xml'))
    for cell, expected in zip(worksheet.findall('.//{*}c'), ['项目名称', *names], strict=True):
        text_runs = [part.text or '' for part in cell.findall('./{*}is//{*}t')]
        assert ''.join(text_runs) == expected
        # Excel 逐个文本节点解释转义，转义样式的名称必须跨节点才能保留原样。
        assert not any(re.search(r'_x[0-9a-fA-F]{4}_', run) for run in text_runs)


@pytest.mark.parametrize('name,error', [
    ('长' * 32768, '上限'), ('名称\x0b末尾', '控制字符'),
    ('名称\r末尾', '无法原样保存'), ('名称\ufffe末尾', '无法原样保存'),
], ids=['too-long', 'control-character', 'carriage-return', 'invalid-xml-character'])
def test_invalid_pending_name_preserves_existing_output(db, tmp_path, name, error):
    db.create_capture('匿名消息', '匿名批次', [('匿名项目', False)])
    with db.connect() as connection:
        connection.execute('UPDATE projects SET name=? WHERE id=1', (name,))
    target = tmp_path / '名单.xlsx'
    target.write_bytes(b'original')
    with pytest.raises(ValueError, match=error):
        export_names(db, target, replace_existing=True,
                     expected_target_identity=delivery_export.target_identity(target))
    assert target.read_bytes() == b'original'
    assert not list(tmp_path.glob('.workbox-export-*'))


@pytest.mark.parametrize('change', ['create', 'replace', 'managed', 'cancel'])
def test_pending_export_respects_changes_during_write(db, tmp_path, monkeypatch, change):
    target = tmp_path / '名单.xlsx'
    stop = threading.Event()
    options = {'cancel_event': stop}
    if change in ('replace', 'cancel'):
        target.write_bytes(b'original')
        options.update(replace_existing=True, expected_target_identity=delivery_export.target_identity(target))
    with db.connect() as connection:
        connection.execute('CREATE TABLE workflow_archive_roots(path TEXT PRIMARY KEY)')
    save = delivery_export.Workbook.save

    def save_and_change(book, stream):
        save(book, stream)
        if change in ('create', 'replace'):
            target.write_bytes(b'external change')
        elif change == 'managed':
            with db.connect() as connection:
                connection.execute('INSERT INTO workflow_archive_roots VALUES (?)', (str(tmp_path),))
        else:
            stop.set()

    monkeypatch.setattr(delivery_export.Workbook, 'save', save_and_change)
    errors = {'create': FileExistsError, 'replace': ValueError, 'managed': ValueError, 'cancel': CancelledError}
    with pytest.raises(errors[change]):
        export_names(db, target, **options)
    if change == 'managed':
        assert not target.exists()
    else:
        assert target.read_bytes() == (b'original' if change == 'cancel' else b'external change')
    assert not list(tmp_path.glob('.workbox-export-*'))


@pytest.mark.skipif(os.name != 'nt', reason='Windows 文件共享占用语义')
def test_pending_export_cannot_replace_an_occupied_file(db, tmp_path):
    target = tmp_path / '名单.xlsx'
    target.write_bytes(b'original')
    with target.open('rb'), pytest.raises(PermissionError):
        export_names(db, target, replace_existing=True,
                     expected_target_identity=delivery_export.target_identity(target))
    assert target.read_bytes() == b'original'
    assert not list(tmp_path.glob('.workbox-export-*'))
