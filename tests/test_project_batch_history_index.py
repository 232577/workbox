"""历史补录来源已覆盖时，不把旧回执再次当作当前批次。"""
from desktop_service import DesktopService
import sqlite3


def test_history_import_then_move_has_no_duplicate_receipt_membership(tmp_path):
    service = DesktopService(tmp_path / 'data/workbox.db')
    received = service.receive_text('1. 匿名甲系统\n2. 匿名乙系统')
    first, second = received['project_ids']
    original = service.task_groups.import_history(service.task_groups.history_candidates()['token'], confirmed=True)['group_ids'][0]
    target = service.task_groups.receive('1. 匿名丙系统')['group_id']
    task = next(t for g in service.task_groups.snapshot(open_only=False) for t in g['tasks'] if t['project_id'] == first)
    service.task_groups.move_task(task['id'], target, confirmed=True)
    before = None
    with service.db.connect() as connection:
        before = list(connection.iterdump())
    rows = service.project_workbench_snapshot()['projects']
    by_id = {p['id']: {m['key'] for m in p['batch_markers']} for p in rows}
    assert by_id[first] == {f'group:{target}'}
    assert by_id[second] == {f'group:{original}'}
    assert f"receipt:{received['receipt_id']}" not in set().union(*by_id.values())
    with service.db.connect() as connection:
        assert list(connection.iterdump()) == before


def test_partial_task_history_missing_items_is_reported_not_silent(tmp_path):
    service = DesktopService(tmp_path / 'data/workbox.db')
    service.task_groups.receive('1. 匿名缺失来源项目')
    with sqlite3.connect(service.db.path) as connection:
        connection.execute('DROP TABLE task_items')
    snapshot = service.project_workbench_snapshot()
    assert any('任务' in warning and '来源' in warning for warning in snapshot['warnings'])
    assert snapshot['projects'][0]['batch_markers'][0]['source_kind'] == 'creation_batch'
