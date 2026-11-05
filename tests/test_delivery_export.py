"""交付表只读快照与安全保存；全部数据均为临时匿名夹具。"""
import importlib
import threading
from concurrent.futures import CancelledError

import pytest
from openpyxl import load_workbook

from database import Database


@pytest.fixture
def db(tmp_path):
    result = Database(tmp_path / 'isolated.db')
    result.create_capture('隔离消息', '隔离批次', [('甲项目', False), ('乙项目', False)])
    return result


def event(db, pid, when, name='甲项目', path='未知归档', signature=None):
    with db.connect() as connection:
        return connection.execute('''INSERT INTO delivery_events(
            project_id,project_name,batch_name,kind,archive_path,occurred_at,archive_signature)
            VALUES (?,?,?,'manual-files',?,?,?)''',
            (pid, name, '隔离批次', path, when, signature)).lastrowid


def backend():
    # 缺少实现时给出明确的行为断言失败，而非收集阶段导入错误。
    assert importlib.util.find_spec('delivery_export'), '缺少交付表导出实现'
    return importlib.import_module('delivery_export')


def test_collect_uses_latest_valid_delivery_and_keeps_workload_exclusion(db):
    first = event(db, 1, '2026-10-01T00:00:00Z')
    latest = event(db, 1, '2026-10-02T00:00:00Z')
    revoked = event(db, 1, '2026-10-03T00:00:00Z')
    db.void_delivery_events([revoked], confirmed=True)
    db.set_project_delivery_counting([1], False, confirmed=True)
    report = backend().collect_delivery_report(db)
    assert report['project_count'] == 1
    row, = report['projects']
    assert row['project_id'] == 1 and row['latest_event_id'] == latest
    assert row['valid_delivery_count'] == 2 and not row['included_in_workload']
    assert row['archive_file_count'] is None
    assert row['first_at'] == '2026-10-01T00:00:00Z'
    assert report['workload_project_count'] == 0
    assert first != revoked


def test_all_revoked_is_absent_but_archived_and_deleted_are_kept(db):
    eid = event(db, 1, '2026-10-01T00:00:00Z')
    event(db, 2, '2026-10-02T00:00:00Z', '乙项目')
    db.void_delivery_events([eid], confirmed=True)
    db.delete_project(2, confirmed=True)
    row, = backend().collect_delivery_report(db)['projects']
    assert row['project_id'] == 2 and row['project_deleted'] and row['project_archived']


def test_prior_marker_retains_unknown_first_date_and_eligibility(db):
    from datetime import date
    event(db, 1, '2026-10-02T00:00:00Z')
    db.set_prior_delivery_projects([1], effective_day=date(2026, 10, 1), confirmed=True)
    report = backend().collect_delivery_report(db)
    row, = report['projects']
    assert row['prior_delivery'] and row['first_at'] is None
    assert row['included_in_workload'] and not row['counted']
    assert report['project_count'] == 1 and report['workload_project_count'] == 0


def test_same_name_different_project_ids_remain_separate_and_type_summary_counts_projects(db):
    with db.connect() as connection:
        connection.execute("UPDATE projects SET name='同名项目'")
    event(db, 1, '2026-10-01T00:00:00Z', '=同名项目')
    event(db, 1, '2026-10-02T00:00:00Z', '=同名项目')
    event(db, 2, '2026-10-02T00:00:00Z', '=同名项目')
    report = backend().collect_delivery_report(db)
    assert len(report['projects']) == 2
    assert sum(row['project_count'] for row in report['type_counts']) == 2


def test_xlsx_is_readable_has_summary_and_literal_user_text(db, tmp_path):
    event(db, 1, '2026-10-01T00:00:00Z', '=HYPERLINK("x")')
    with db.connect() as connection:
        connection.execute('UPDATE projects SET name=? WHERE id=1', ('=HYPERLINK("x")',))
    target = tmp_path / 'report.xlsx'
    result = backend().export_delivered_projects(db, target)
    book = load_workbook(target)
    assert book.sheetnames == ['交付项目', '类型汇总', '任务组汇总', '任务明细']
    sheet = book['交付项目']
    assert sheet.freeze_panes == 'A2' and sheet.auto_filter.ref
    headers = {cell.value: cell.column for cell in sheet[1]}
    cell = sheet.cell(2, headers['项目名称'])
    assert cell.value == '=HYPERLINK("x")' and cell.data_type == 's'
    assert sheet.cell(2, headers['最近交付归档文件数']).value == '待核对'
    assert result['project_count'] == 1 and result['path'] == str(target)


def test_existing_file_requires_explicit_unchanged_identity(db, tmp_path):
    export = backend()
    target = tmp_path / 'report.xlsx'
    target.write_bytes(b'original')
    with pytest.raises(FileExistsError):
        export.export_delivered_projects(db, target)
    with pytest.raises(ValueError, match='标识'):
        export.export_delivered_projects(db, target, replace_existing=True)
    old = export.target_identity(target)
    target.write_bytes(b'changed')
    with pytest.raises((ValueError, FileExistsError), match='变化|标识'):
        export.export_delivered_projects(db, target, replace_existing=True, expected_target_identity=old)
    assert target.read_bytes() == b'changed'


def test_confirmed_replace_and_empty_report(db, tmp_path):
    export = backend()
    target = tmp_path / 'report.xlsx'
    target.write_bytes(b'original')
    export.export_delivered_projects(db, target, replace_existing=True,
                                     expected_target_identity=export.target_identity(target))
    book = load_workbook(target)
    assert book['交付项目'].max_row == 1
    assert not list(tmp_path.glob('.workbox-export-*'))


def test_managed_paths_and_database_are_protected(db, tmp_path):
    export = backend()
    managed = tmp_path / 'managed'
    managed.mkdir()
    with db.connect() as connection:
        connection.execute('CREATE TABLE workflow_archive_roots(path TEXT PRIMARY KEY)')
        connection.execute('INSERT INTO workflow_archive_roots VALUES (?)', (str(managed),))
    for path in (managed / 'report.xlsx', db.path):
        with pytest.raises(ValueError):
            export.export_delivered_projects(db, path)
    assert not (managed / 'report.xlsx').exists()


def test_cancel_and_writer_failure_do_not_leave_files(db, tmp_path, monkeypatch):
    export = backend()
    stop = threading.Event()
    stop.set()
    target = tmp_path / 'report.xlsx'
    with pytest.raises(CancelledError):
        export.export_delivered_projects(db, target, cancel_event=stop)
    def fail(*_args, **_kwargs):
        raise OSError('隔离写入失败')
    monkeypatch.setattr(export.Workbook, 'save', fail)
    with pytest.raises(OSError, match='隔离写入失败'):
        export.export_delivered_projects(db, target)
    assert not target.exists() and not list(tmp_path.glob('.workbox-export-*'))


def test_latest_valid_material_fact_is_not_sum_of_versions_and_query_is_read_only(db):
    import hashlib
    import json
    from delivery_archive import SCHEMA
    manifests = [[dict(path=f'{n}.txt', size=0, sha256='a'*64) for n in range(count)] for count in (7, 2)]
    signatures = [hashlib.sha256(json.dumps([[row['path'], row['sha256']] for row in manifest],
                  ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()
                  for manifest in manifests]
    event(db, 1, '2026-10-01T00:00:00Z', path='匿名归档1', signature=signatures[0])
    event(db, 1, '2026-10-02T00:00:00Z', path='匿名归档2', signature=signatures[1])
    with db.connect() as connection:
        connection.executescript(SCHEMA)
        for path, signature, manifest in zip(('匿名归档1', '匿名归档2'), signatures, manifests):
            connection.execute('''INSERT INTO project_delivery_archives
                (project_id,path,source_root,signature,manifest) VALUES (1,?,'匿名来源',?,?)''',
                (path, signature, json.dumps(manifest)))
    with db.connect() as connection:
        before = list(connection.iterdump())
    report = backend().collect_delivery_report(db)
    assert report['projects'][0]['archive_file_count'] == 2
    with db.connect() as connection:
        assert list(connection.iterdump()) == before


def test_merge_uses_one_canonical_project(db):
    from workspace_manager import SCHEMA
    event(db, 1, '2026-10-01T00:00:00Z')
    event(db, 2, '2026-10-02T00:00:00Z', '乙项目')
    with db.connect() as connection:
        connection.executescript(SCHEMA)
        connection.execute('BEGIN IMMEDIATE')
        db._merge_project_records(connection, 1, 2, operation_id='isolated-merge',
                                  result_status='已交付', snapshot={})
    row, = backend().collect_delivery_report(db)['projects']
    assert row['project_id'] == 2 and row['project_name'] == '乙项目'
    assert row['valid_delivery_count'] == 2


def test_packages_directory_is_protected_even_without_existing_registration(db, tmp_path):
    packages = tmp_path / 'packages'
    packages.mkdir()
    with pytest.raises(ValueError, match='受管'):
        backend().export_delivered_projects(db, packages / 'report.xlsx')


def test_replacement_refuses_file_changed_during_write(db, tmp_path, monkeypatch):
    export = backend()
    target = tmp_path / 'report.xlsx'
    target.write_bytes(b'original')
    initial = export.target_identity(target)
    save = export.Workbook.save
    def change_target(book, stream):
        save(book, stream)
        target.write_bytes(b'new external content')
    monkeypatch.setattr(export.Workbook, 'save', change_target)
    with pytest.raises(ValueError, match='变化'):
        export.export_delivered_projects(db, target, replace_existing=True, expected_target_identity=initial)
    assert target.read_bytes() == b'new external content'
    assert not list(tmp_path.glob('.workbox-export-*'))


@pytest.mark.parametrize('name', ['report.xlsx:stream', 'NUL.xlsx', 'report.xlsx.'])
def test_device_ads_and_ambiguous_paths_are_rejected(db, tmp_path, name):
    with pytest.raises(ValueError):
        backend().export_delivered_projects(db, tmp_path / name)


def test_long_cell_is_not_silently_truncated(db, tmp_path):
    event(db, 1, '2026-10-01T00:00:00Z')
    with db.connect() as connection:
        connection.execute('UPDATE projects SET name=? WHERE id=1', ('长'*32768,))
    target = tmp_path / 'report.xlsx'
    with pytest.raises(ValueError, match='上限'):
        backend().export_delivered_projects(db, target)
    assert not target.exists()


def test_no_replace_refuses_target_created_during_write(db, tmp_path, monkeypatch):
    export = backend()
    target = tmp_path / 'report.xlsx'
    save = export.Workbook.save
    def create_target(book, stream):
        save(book, stream)
        target.write_bytes(b'other task')
    monkeypatch.setattr(export.Workbook, 'save', create_target)
    with pytest.raises(FileExistsError):
        export.export_delivered_projects(db, target)
    assert target.read_bytes() == b'other task'
    assert not list(tmp_path.glob('.workbox-export-*'))


def test_cancellation_during_write_retains_original(db, tmp_path, monkeypatch):
    export = backend()
    target = tmp_path / 'report.xlsx'
    target.write_bytes(b'original')
    stop = threading.Event()
    save = export.Workbook.save
    def cancel_after_save(book, stream):
        save(book, stream)
        stop.set()
    monkeypatch.setattr(export.Workbook, 'save', cancel_after_save)
    with pytest.raises(CancelledError):
        export.export_delivered_projects(db, target, cancel_event=stop, replace_existing=True,
                                         expected_target_identity=export.target_identity(target))
    assert target.read_bytes() == b'original'
    assert not list(tmp_path.glob('.workbox-export-*'))


def test_windows_occupied_target_is_not_replaced(db, tmp_path):
    import os
    if os.name != 'nt':
        pytest.skip('Windows 文件共享占用语义')
    export = backend()
    target = tmp_path / 'report.xlsx'
    target.write_bytes(b'original')
    with target.open('rb'):
        with pytest.raises(PermissionError):
            export.export_delivered_projects(db, target, replace_existing=True,
                                             expected_target_identity=export.target_identity(target))
    assert target.read_bytes() == b'original'
    assert not list(tmp_path.glob('.workbox-export-*'))


def test_type_summary_total_distinguishes_eligibility_from_first_count_outside_filter(db, tmp_path):
    from datetime import date
    from openpyxl.utils.cell import range_boundaries
    event(db, 1, '2026-10-02T00:00:00Z')
    event(db, 2, '2026-10-02T00:00:00Z', '乙项目')
    db.set_project_delivery_counting([1], False, confirmed=True)
    db.set_prior_delivery_projects([2], effective_day=date(2026, 10, 1), confirmed=True)
    target = tmp_path / 'report.xlsx'
    backend().export_delivered_projects(db, target)
    book = load_workbook(target)
    sheet = book['类型汇总']
    totals = [row for row in sheet.iter_rows() if row[0].value == '合计']
    assert len(totals) == 1
    total, = totals
    assert [cell.value for cell in total] == ['合计', 2, 1, 0]
    last_filter_row = range_boundaries(sheet.auto_filter.ref)[3]
    assert total[0].row > last_filter_row
    assert sum(sheet.cell(row, 2).value or 0 for row in range(2, last_filter_row + 1)) == 2
    assert '纳入首交统计的项目数' in [cell.value for cell in sheet[1]]


def test_empty_report_summary_has_explicit_zero_total(db, tmp_path):
    target = tmp_path / 'report.xlsx'
    backend().export_delivered_projects(db, target)
    summary = load_workbook(target)['类型汇总']
    assert [cell.value for cell in summary[summary.max_row]] == ['合计', 0, 0, 0]


def test_new_managed_root_during_xlsx_write_blocks_publication(db, tmp_path, monkeypatch):
    import json
    import threading
    from workspace_manager import SCHEMA
    export = backend()
    db._operation_lock = threading.RLock()
    destination = tmp_path / 'new-managed-root'
    destination.mkdir()
    target = destination / 'report.xlsx'
    with db.connect() as connection:
        connection.executescript(SCHEMA)
    save = export.Workbook.save
    def change_managed_root(book, stream):
        save(book, stream)
        with db._operation_lock, db.connect() as connection:
            connection.execute('INSERT INTO workflow_settings(key,value) VALUES (?,?)',
                               ('work_root', json.dumps(str(destination))))
    monkeypatch.setattr(export.Workbook, 'save', change_managed_root)
    with pytest.raises(ValueError, match='受管'):
        export.export_delivered_projects(db, target)
    assert not target.exists()
    assert not list(destination.glob('.workbox-export-*'))
