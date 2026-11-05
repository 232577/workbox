"""任务组报表与项目首交统计分开，匿名夹具验证同一读取事务。"""
from pathlib import Path
import pytest
from openpyxl import load_workbook

from database import Database
from desktop_service import DesktopService
from delivery_export import collect_delivery_report, export_delivered_projects
from task_groups import observe_delivery_event


def service(tmp_path):
    return DesktopService(tmp_path / 'anonymous' / 'workbox.db')


def deliver(s, pid=1, path='匿名归档/确切版本'):
    with s.db.connect() as connection:
        p = connection.execute('SELECT name FROM projects WHERE id=?', (pid,)).fetchone()
        eid = connection.execute("INSERT INTO delivery_events(project_id,project_name,batch_name,kind,archive_path) VALUES (?,?,'匿名批次','manual-files',?)", (pid, p['name'], path)).lastrowid
        observe_delivery_event(connection, eid)
    return eid


def rows(book, name):
    values = list(book[name].values)
    return [dict(zip(values[0], row)) for row in values[1:]]


def test_old_database_and_empty_export_get_four_sheets_without_new_tables(tmp_path):
    db = Database(tmp_path / 'old.db')
    with db.connect() as connection:
        before = list(connection.iterdump())
    report = collect_delivery_report(db)
    assert report['task_groups_snapshot'] == []
    target = tmp_path / 'empty.xlsx'
    export_delivered_projects(db, target)
    book = load_workbook(target)
    assert book.sheetnames == ['交付项目', '类型汇总', '任务组汇总', '任务明细']
    assert book['任务组汇总'].max_row == book['任务明细'].max_row == 1
    with db.connect() as connection:
        assert list(connection.iterdump()) == before


def test_multiple_task_rounds_export_without_inflating_unique_project_count(tmp_path):
    s = service(tmp_path)
    one = s.task_groups.receive('1. 匿名客户系统')
    eid = deliver(s)
    s.task_groups.handoff(one['task_ids'], confirmed=True)
    two = s.task_groups.receive('1. 匿名客户系统', new_round=True)
    s.task_groups.receive('项目名称：匿名客户系统', group_id=two['group_id'])
    type_id = s.db.create_project_type('匿名类型')['id']
    s.db.set_project_types([1], type_id)
    report = collect_delivery_report(s.db)
    assert report['project_count'] == 1
    assert len(report['task_rows']) == 2
    assert sum(g['counts']['handed'] for g in report['task_groups_snapshot']) == 1
    target = tmp_path / 'tasks.xlsx'
    result = export_delivered_projects(s.db, target)
    book = load_workbook(target)
    detail = rows(book, '任务明细')
    assert len(detail) == 2 and {r['项目名称'] for r in detail} == {'匿名客户系统'}
    assert all(r['当前项目类型'] == '匿名类型' for r in detail)
    assert {r['手工交接状态'] for r in detail} == {'已交接', '未交接'}
    assert next(r for r in detail if r['任务号'] == one['group_code'] + '-01')['关联归档位置'] == '匿名归档/确切版本'
    assert '2' in next(r for r in detail if r['任务号'] == two['group_code'] + '-01')['来源消息']
    assert result['task_count'] == 2 and result['handed_task_count'] == 1


def test_void_restore_and_rework_never_export_prior_handoff_as_current(tmp_path):
    s = service(tmp_path)
    one = s.task_groups.receive('1. 匿名客户系统')
    eid = deliver(s)
    s.task_groups.handoff(one['task_ids'], confirmed=True)
    s.db.void_delivery_events([eid], confirmed=True)
    s.db.restore_delivery_events([eid], confirmed=True)
    row, = collect_delivery_report(s.db)['task_rows']
    assert row['material_status'] == '需核对' and row['handoff_status'] == '曾交接，需补交'
    s.task_groups.bind_delivery(one['task_ids'], eid, confirmed=True)
    row, = collect_delivery_report(s.db)['task_rows']
    assert row['material_status'] == '材料就绪' and row['handoff_status'] == '曾交接，需补交'
    s.task_groups.handoff(one['task_ids'], confirmed=True)
    s.task_groups.reopen(one['task_ids'], confirmed=True)
    row, = collect_delivery_report(s.db)['task_rows']
    assert row['material_status'] == '需核对' and row['handoff_status'] == '曾交接，需补交'


def test_task_snapshot_reader_is_read_only_and_requires_no_service(tmp_path):
    import task_groups
    s = service(tmp_path)
    s.task_groups.receive('1. 匿名客户系统')
    assert hasattr(task_groups, 'read_task_snapshot'), '需要独立只读任务快照读取器'
    with s.db.connect() as connection:
        connection.execute('PRAGMA query_only=ON')
        connection.execute('BEGIN')
        before = connection.total_changes
        groups = task_groups.read_task_snapshot(connection, open_only=False)
        assert groups and connection.total_changes == before
    with s.db.connect() as connection:
        before = list(connection.iterdump())
    collect_delivery_report(s.db)
    with s.db.connect() as connection:
        assert list(connection.iterdump()) == before


def test_task_group_and_detail_use_literals_and_count_withdrawn_separately(tmp_path):
    s = service(tmp_path)
    one = s.task_groups.receive('1. 匿名客户系统\n2. 匿名合同工具')
    s.task_groups.rename_group(one['group_id'], '=HYPERLINK("example")')
    with s.db.connect() as connection:
        connection.execute("UPDATE projects SET name='=SUM(1,2)' WHERE id=1")
    type_id = s.db.create_project_type('=SUM(3,4)')['id']
    s.db.set_project_types([1], type_id)
    s.task_groups.withdraw([one['task_ids'][1]], confirmed=True)
    target = tmp_path / 'literal.xlsx'
    export_delivered_projects(s.db, target)
    book = load_workbook(target)
    group, = rows(book, '任务组汇总')
    assert group['总任务数'] == 2 and group['未交接（已确定）'] == group['撤回数'] == 1
    assert group['组名'] == '=HYPERLINK("example")'
    assert book['任务组汇总'].cell(2, 3).data_type == 's'
    assert book['任务明细'].cell(2, 3).value == '=SUM(1,2)'
    assert book['任务明细'].cell(2, 3).data_type == 's'
    assert book['任务明细'].cell(2, 9).value == '=SUM(3,4)'
    assert book['任务明细'].cell(2, 9).data_type == 's'
    assert {r['手工交接状态'] for r in rows(book, '任务明细')} == {'未交接', '已撤回'}


def test_project_and_task_statuses_are_from_the_same_read_transaction(tmp_path, monkeypatch):
    import delivery_export
    s = service(tmp_path)
    one = s.task_groups.receive('1. 匿名客户系统')
    deliver(s)
    before_type = s.db.create_project_type('匿名原类型')['id']
    after_type = s.db.create_project_type('匿名新类型')['id']
    s.db.set_project_types([1], before_type)
    with s.db.connect() as connection:
        connection.execute('PRAGMA journal_mode=WAL')
    facts = delivery_export.delivery_archive_facts
    def concurrent_handoff(connection, events):
        result = facts(connection, events)
        s.task_groups.handoff(one['task_ids'], confirmed=True)
        s.db.set_project_types([1], after_type)
        return result
    monkeypatch.setattr(delivery_export, 'delivery_archive_facts', concurrent_handoff)
    report = collect_delivery_report(s.db)
    assert report['task_rows'][0]['handoff_status'] == '未交接'
    assert report['projects'][0]['project_type_name'] == report['task_rows'][0]['project_type_name'] == '匿名原类型'
    monkeypatch.setattr(delivery_export, 'delivery_archive_facts', facts)
    assert collect_delivery_report(s.db)['task_rows'][0]['handoff_status'] == '已交接'


def test_moved_task_keeps_original_source_and_review_counts_are_exclusive(tmp_path):
    s = service(tmp_path)
    first = s.task_groups.receive('1. 匿名客户系统')
    second = s.task_groups.receive('1. 匿名客户系统', new_round=True)
    deliver(s)
    # 双轮任务使同一事件存在歧义：待核对与已确定未交接不能重复计数。
    report = collect_delivery_report(s.db)
    assert all(r['needs_review_count'] == 1 and r['pending_count'] == 0 for r in report['task_group_rows'])
    s.task_groups.move_task(first['task_ids'][0], second['group_id'], confirmed=True)
    moved = next(r for r in collect_delivery_report(s.db)['task_rows'] if r['task_id'] == first['task_ids'][0])
    assert moved['group_code'] == second['group_code']
    assert moved['task_code'].startswith(first['group_code'])
    assert first['group_code'] in moved['source_messages']


def test_canonical_project_merge_keeps_two_task_rows_and_one_project_row(tmp_path):
    s = service(tmp_path)
    s.task_groups.receive('1. 匿名旧系统')
    s.task_groups.receive('1. 匿名新系统')
    with s.db.connect() as connection:
        connection.execute("INSERT INTO project_merges(source_id,target_id,operation_id,snapshot_json) VALUES (1,2,'anonymous-merge','{}')")
    deliver(s, 2)
    report = collect_delivery_report(s.db)
    assert report['project_count'] == 1 and report['task_count'] == 2
    assert {r['project_name'] for r in report['task_rows']} == {'匿名新系统'}
    assert {r['original_project_name'] for r in report['task_rows']} == {'匿名新系统', '匿名旧系统'}


def test_export_one_group_filters_tasks_projects_types_and_workload_together(tmp_path):
    s = service(tmp_path)
    one = s.task_groups.receive('1. 匿名共有系统\n2. 匿名本组工具\n3. 匿名未交工具')
    two = s.task_groups.receive('1. 匿名共有系统\n2. 匿名他组工具')
    # 同组可保留同项目多轮任务，项目表仍只列一次。
    s.task_groups.move_task(two['task_ids'][0], one['group_id'], confirmed=True)
    for pid, type_name in ((1, '匿名甲类型'), (2, '匿名乙类型'), (4, '匿名他组类型')):
        type_id = s.db.create_project_type(type_name)['id']
        s.db.set_project_types([pid], type_id)
        deliver(s, pid)
    s.db.set_project_delivery_counting([2], False, confirmed=True)
    report = collect_delivery_report(s.db, group_id=one['group_id'])
    assert report['task_count'] == 4 and report['project_count'] == 2
    assert report['eligible_project_count'] == report['workload_project_count'] == 1
    assert {p['project_id'] for p in report['projects']} == {1, 2}
    assert {r['type_name'] for r in report['type_counts']} == {'匿名甲类型', '匿名乙类型'}
    assert len(report['task_groups_snapshot']) == len(report['task_group_rows']) == 1
    assert {r['group_code'] for r in report['task_rows']} == {one['group_code']}
    target = tmp_path / 'one-group.xlsx'
    result = export_delivered_projects(s.db, target, group_id=one['group_id'])
    book = load_workbook(target)
    assert book.sheetnames == ['交付项目', '类型汇总', '任务组汇总', '任务明细']
    assert book.active.title == '任务明细'
    assert len(rows(book, '任务明细')) == 4 and len(rows(book, '交付项目')) == 2
    assert '匿名他组工具' not in {r['项目名称'] for r in rows(book, '任务明细')}
    assert [cell.value for cell in book['类型汇总'][book['类型汇总'].max_row]] == ['合计', 2, 1, 1]
    assert result['task_count'] == 4 and result['project_count'] == 2


def test_group_export_rejects_invalid_groups_and_shows_empty_group_summary(tmp_path):
    s = service(tmp_path)
    pending = s.task_groups.receive('请核对这份匿名需求说明')
    for group_id in (0, -1, True, '1', 999):
        target = tmp_path / f'invalid-{group_id}.xlsx'
        with pytest.raises(ValueError, match='任务组'):
            export_delivered_projects(s.db, target, group_id=group_id)
        assert not target.exists()
    target = tmp_path / 'pending-group.xlsx'
    export_delivered_projects(s.db, target, group_id=pending['group_id'])
    book = load_workbook(target)
    assert book.active.title == '任务组汇总'
    group, = rows(book, '任务组汇总')
    assert group['待核对消息数'] == 1 and group['总任务数'] == 0
    assert book['任务明细'].max_row == book['交付项目'].max_row == 1
    assert not list(tmp_path.glob('.workbox-export-*'))


def test_group_export_still_protects_other_groups_managed_paths(tmp_path):
    s = service(tmp_path)
    one = s.task_groups.receive('1. 匿名本组系统')
    other = s.task_groups.receive('1. 匿名他组工具')
    other_workspace = s.workspaces.get_workspace(other['project_ids'][0])
    target = Path(other_workspace['path']) / 'report.xlsx'
    with pytest.raises(ValueError, match='受管'):
        export_delivered_projects(s.db, target, group_id=one['group_id'])
    assert not target.exists()


def test_group_export_retains_explicit_unchanged_file_replacement_contract(tmp_path):
    from delivery_export import target_identity
    s = service(tmp_path)
    group = s.task_groups.receive('1. 匿名本组系统')
    target = tmp_path / 'existing-group.xlsx'
    target.write_bytes(b'anonymous original')
    with pytest.raises(FileExistsError):
        export_delivered_projects(s.db, target, group_id=group['group_id'])
    assert target.read_bytes() == b'anonymous original'
    initial = target_identity(target)
    export_delivered_projects(s.db, target, group_id=group['group_id'], replace_existing=True,
                              expected_target_identity=initial)
    book = load_workbook(target)
    assert book.active.title == '任务明细' and len(rows(book, '任务明细')) == 1
    assert not list(tmp_path.glob('.workbox-export-*'))
