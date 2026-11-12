"""合并按各原项目的真实来源汇总批次，不让另一成员的回执遮住原批次。"""
import os
from pathlib import Path

import pytest

from delivery_export import collect_project_table
from desktop_service import DesktopService
from project_batches import read_project_batch_index


def read_index(db):
    with db.connect() as connection:
        connection.execute('PRAGMA query_only=ON')
        connection.execute('BEGIN')
        before = list(connection.iterdump())
        result = read_project_batch_index(connection)
        assert list(connection.iterdump()) == before
        assert connection.total_changes == 0
    return result


def record_old_merge(db, source, target):
    with db.connect() as connection:
        connection.execute('''INSERT INTO project_merges(source_id,target_id,operation_id,snapshot_json)
            VALUES (?,?,?,'{}')''', (source, target, f'anonymous-{source}-{target}'))


def keys(index, pid):
    return {marker['key'] for marker in index['by_project'][pid]}


@pytest.mark.skipif(os.name != 'nt', reason='实际项目合并目录身份租约使用 Windows API')
def test_unmatched_file_renamed_into_old_project_preserves_original_batch_on_refresh_and_reopen(tmp_path):
    service = DesktopService(tmp_path / 'data/workbox.db')
    original = service.capture_message('1. 正确项目\n2. 同批项目', '匿名初始批次')
    target, companion = original['project_ids']
    original_key = f"batch:{original['batch_id']}"
    incoming = tmp_path / '错误名称_操作手册.txt'
    incoming.write_text('匿名文件原文', encoding='utf-8')
    source = service.receive_path(incoming)['file']['project_id']
    source_keys = keys(read_index(service.db), source)
    assert source != target and any(key.startswith('receipt:') for key in source_keys)
    assert keys(read_index(service.db), target) == {original_key}
    before = {pid: service.get_project(pid)['batch_id'] for pid in (source, target)}
    plan = service.preview_project_rename(source, '正确项目')
    assert plan['kind'] == 'merge' and plan['target']['id'] == target
    service.rename_project(source, '正确项目', expected_token=plan['token'],
                           merge_target_id=target, confirmed_merge=True)

    for current in (service, DesktopService(service.db.path)):
        index = read_index(current.db)
        assert keys(index, target) == {original_key} | source_keys
        assert source not in index['by_project']
        marker = next(item for item in index['options'] if item['key'] == original_key)
        assert (marker['total_count'], marker['pending_delivery_count']) == (2, 1)
        snapshot = current.project_workbench_snapshot(batch_key=original_key)
        assert {row['id'] for row in snapshot['projects']} == {target, companion}
        assert [row['id'] for row in current.project_workbench_snapshot(
            batch_key=original_key, status='待交付')['projects']] == [target]
        report = collect_project_table(current.db, project_ids=(target,))
        assert set(report['projects'][0]['intake_batch_codes'].split('；')) == {
            item['code'] for item in index['by_project'][target]}
        with current.db.connect() as connection:
            assert {row['id']: row['batch_id'] for row in connection.execute(
                'SELECT id,batch_id FROM projects WHERE id IN (?,?)', (source, target))} == before
            paths = [row[0] for row in connection.execute('SELECT path FROM work_files WHERE project_id=?', (target,))]
        assert paths and all(Path(path).read_text(encoding='utf-8') == '匿名文件原文' for path in paths)
    assert incoming.read_text(encoding='utf-8') == '匿名文件原文'


def test_existing_chain_of_legacy_merges_recovers_every_original_batch_without_writes(tmp_path):
    service = DesktopService(tmp_path / 'data/workbox.db')
    captures = [service.db.create_capture(f'匿名原文{i}', f'匿名批次{i}', [(f'匿名项目{i}', False)])
                for i in range(3)]
    first, second, target = [capture['project_ids'][0] for capture in captures]
    record_old_merge(service.db, first, second)
    record_old_merge(service.db, second, target)
    index = read_index(service.db)
    assert set(index['by_project']) == {target}
    assert keys(index, target) == {f"batch:{capture['batch_id']}" for capture in captures}
    assert all(marker['total_count'] == 1 for marker in index['options'])
    assert not index['warnings']


def test_shared_original_batch_is_counted_once_after_merge_and_follows_target_visibility(tmp_path):
    service = DesktopService(tmp_path / 'data/workbox.db')
    capture = service.db.create_capture('匿名同批原文', '匿名同批', [('匿名甲', False), ('匿名乙', False)])
    source, target = capture['project_ids']
    record_old_merge(service.db, source, target)
    service.set_status(target, '待交付')
    index = read_index(service.db)
    assert keys(index, target) == {f"batch:{capture['batch_id']}"}
    assert (index['options'][0]['total_count'], index['options'][0]['pending_delivery_count']) == (1, 1)
    service.db.delete_project(target, confirmed=True)
    index = read_index(service.db)
    assert (index['options'][0]['total_count'], index['options'][0]['pending_delivery_count']) == (0, 0)


def test_mixed_chain_keeps_original_batch_and_current_group_without_reviving_covered_receipt(tmp_path):
    service = DesktopService(tmp_path / 'data/workbox.db')
    original = service.capture_message('匿名原建系统', '匿名原批次')
    legacy = service.task_groups.receive('1. 匿名旧组系统')
    received = service.receive_text('1. 匿名收件平台')
    moved = service.task_groups.receive('1. 匿名其他工具', new_round=True)
    service.task_groups.move_task(legacy['task_ids'][0], moved['group_id'], confirmed=True)
    old, middle, target = original['project_ids'][0], legacy['project_ids'][0], received['project_ids'][0]
    record_old_merge(service.db, old, middle)
    record_old_merge(service.db, middle, target)
    index = read_index(service.db)
    assert keys(index, target) == {f"batch:{original['batch_id']}", f"group:{moved['group_id']}",
                                  f"receipt:{received['receipt_id']}"}
    assert not index['warnings']


def test_corrupt_source_receipt_does_not_hide_its_real_original_batch_after_merge(tmp_path):
    service = DesktopService(tmp_path / 'data/workbox.db')
    source_receipt = service.receive_text('1. 匿名错误收件')
    target_receipt = service.receive_text('1. 匿名保留收件')
    source, target = source_receipt['project_ids'][0], target_receipt['project_ids'][0]
    original_batch = service.get_project(source)['batch_id']
    with service.db.connect() as connection:
        connection.execute("UPDATE intake_receipts SET project_ids='broken' WHERE id=?", (source_receipt['receipt_id'],))
    record_old_merge(service.db, source, target)
    index = read_index(service.db)
    assert keys(index, target) == {f'batch:{original_batch}', f"receipt:{target_receipt['receipt_id']}"}
    assert any('损坏' in warning for warning in index['warnings'])
