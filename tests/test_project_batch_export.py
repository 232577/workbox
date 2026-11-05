"""全部交付导出沿用同一只读批次索引，并保留旧表契约。"""
from openpyxl import load_workbook

from delivery_export import collect_delivery_report, export_delivered_projects
from desktop_service import DesktopService
from project_batches import read_project_batch_index


def test_delivery_project_rows_include_same_read_only_batch_codes(tmp_path):
    service = DesktopService(tmp_path / 'data/workbox.db')
    first = service.receive_text('1. 匿名设备管理项目')['project_ids'][0]
    receipt = service.receive_text('1. 匿名设备管理项目\n2. 匿名文档管理项目')
    with service.db.connect() as connection:
        for project_id in receipt['project_ids']:
            connection.execute('''INSERT INTO delivery_events(project_id,project_name,batch_name,kind,archive_path,occurred_at)
                VALUES (?,?,'匿名批次','manual-files','匿名归档','2026-11-05T00:00:00Z')''', (project_id, f'匿名项目{project_id}'))
        before = list(connection.iterdump())
    report = collect_delivery_report(service.db)
    with service.db.connect() as connection:
        connection.execute('BEGIN')
        index = read_project_batch_index(connection)
        assert list(connection.iterdump()) == before
    assert report['project_count'] == 2
    for row in report['projects']:
        assert row['intake_batch_codes'] == '；'.join(m['code'] for m in index['by_project'][row['project_id']])
    assert len(index['by_project'][first]) == 2
    target = tmp_path / '交付汇总.xlsx'
    export_delivered_projects(service.db, target)
    book = load_workbook(target, read_only=True)
    try:
        assert book.sheetnames == ['交付项目', '类型汇总', '任务组汇总', '任务明细']
        rows = list(book['交付项目'].values)
        assert '收件批次编号' in rows[0]
        code_column = rows[0].index('收件批次编号')
        assert all(row[code_column] for row in rows[1:])
    finally:
        book.close()
    with service.db.connect() as connection:
        assert list(connection.iterdump()) == before


def test_batch_codes_share_delivery_snapshot_under_concurrent_receipt(tmp_path, monkeypatch):
    service = DesktopService(tmp_path / 'data/workbox.db')
    receipt = service.receive_text('1. 匿名快照项目')
    pid = receipt['project_ids'][0]
    with service.db.connect() as connection:
        connection.execute('''INSERT INTO delivery_events(project_id,project_name,batch_name,kind,archive_path,occurred_at)
            VALUES (?,'匿名快照项目','匿名批次','manual-files','匿名归档','2026-11-05T00:00:00Z')''', (pid,))
    before = service.project_workbench_snapshot()['projects'][0]['batch_markers']
    original = service.db.delivery_history
    def interleave(*args, **kwargs):
        result = original(*args, **kwargs)
        service.receive_text('1. 匿名快照项目\n2. 匿名新项目')
        return result
    monkeypatch.setattr(service.db, 'delivery_history', interleave)
    row, = collect_delivery_report(service.db)['projects']
    assert row['intake_batch_codes'] == '；'.join(m['code'] for m in before)
    assert len(service.project_workbench_snapshot()['projects'][-1]['batch_markers']) == 2


def test_export_marks_incomplete_batch_sources_without_changing_history(tmp_path):
    service = DesktopService(tmp_path / 'data/workbox.db')
    service.receive_text('1. 匿名批次核对项目')
    with service.db.connect() as connection:
        connection.execute("UPDATE intake_receipts SET project_ids='bad-json'")
        connection.execute('''INSERT INTO delivery_events(project_id,project_name,batch_name,kind,archive_path,occurred_at)
            VALUES (1,'匿名批次核对项目','匿名批次','manual-files','匿名归档','2026-11-05T00:00:00Z')''')
        before = list(connection.iterdump())
    target = tmp_path / '需核对批次.xlsx'
    export_delivered_projects(service.db, target)
    book = load_workbook(target)
    try:
        header = next(c for c in book['交付项目'][1] if c.value == '收件批次编号')
        assert header.comment is not None and '损坏' in header.comment.text
    finally:
        book.close()
    with service.db.connect() as connection:
        assert list(connection.iterdump()) == before
