"""批次待完成数只含待开发、开发中；总数始终来自完整批次。"""
import json
from pathlib import Path

import pytest

from desktop_service import DesktopService
import fluent_views
from fluent_views import _batch_badge_parts, batch_progress_summary


@pytest.mark.parametrize('total', [1, 5])
def test_pending_delivery_reduces_visible_unfinished_count_and_keeps_total(tmp_path, total):
    service = DesktopService(tmp_path / 'anonymous' / 'workbox.db')
    receipt = service.receive_text('\n'.join(f'{i}. 匿名项目{i}' for i in range(1, total + 1)))
    project_id = receipt['project_ids'][0]
    service.set_status(project_id, '待交付')

    for filters in ({}, {'status': '待开发'}, {'status': '待交付'},
                    {'status': '已交付'}, {'query': '没有匹配'}):
        snapshot = service.project_workbench_snapshot(**filters)
        marker, = snapshot['batch_options']
        expected = f'待完成{total - 1}，共{total}'
        assert batch_progress_summary(marker) == expected
        assert _batch_badge_parts(marker) == (marker['label'], ' ' + expected)
        # Legacy backend readers still count projects awaiting actual delivery.
        assert marker['remaining_count'] == total
        for project in snapshot['projects']:
            assert project['batch_markers'] == [marker]


def test_return_to_development_and_delivery_keep_same_total(tmp_path):
    service = DesktopService(tmp_path / 'anonymous' / 'workbox.db')
    receipt = service.receive_text('1. 匿名单项目')
    project_id, = receipt['project_ids']
    for status, expected in [('待交付', '待完成0，共1'), ('开发中', '待完成1，共1'),
                             ('待开发', '待完成1，共1'), ('已交付', '待完成0，共1')]:
        if status == '已交付':
            workspace = Path(service.workspaces.get_workspace(project_id)['path'])
            (workspace / '匿名交付材料.txt').write_text('匿名材料', encoding='utf-8')
            service.set_status(project_id, '待交付')
            service.confirm_delivery(project_id, confirmed=True)
        else:
            service.set_status(project_id, status)
        marker, = service.project_workbench_snapshot()['batch_options']
        assert batch_progress_summary(marker) == expected
        assert _batch_badge_parts(marker)[1] == ' ' + expected


def test_multiple_batches_and_merged_members_count_canonical_projects_once(tmp_path):
    service = DesktopService(tmp_path / 'anonymous' / 'workbox.db')
    first = service.receive_text('1. 匿名甲项目\n2. 匿名乙项目')
    source, target = first['project_ids']
    second = service.receive_text('1. 匿名甲项目\n2. 匿名丙项目')
    service.set_status(target, '待交付')
    with service.db.connect() as connection:
        connection.execute('UPDATE intake_receipts SET project_ids=? WHERE id=?',
                           (json.dumps([source, target, source]), first['receipt_id']))
        connection.execute('''INSERT INTO project_merges(source_id,target_id,operation_id,snapshot_json)
                              VALUES (?,?,'anonymous-completion-merge','{}')''', (source, target))

    snapshot = service.project_workbench_snapshot(query='没有匹配')
    by_key = {marker['key']: marker for marker in snapshot['batch_options']}
    assert batch_progress_summary(by_key[f"receipt:{first['receipt_id']}"]) == '待完成0，共1'
    assert batch_progress_summary(by_key[f"receipt:{second['receipt_id']}"]) == '待完成1，共2'


def test_known_empty_batch_and_unknown_counts_are_distinct():
    marker = dict(label='R261109-1', total_count=0, delivered_count=0,
                  remaining_count=0, pending_delivery_count=0)
    assert batch_progress_summary(marker) == '待完成0，共0'
    assert _batch_badge_parts(marker) == ('R261109-1', ' 待完成0，共0')
    marker.pop('pending_delivery_count')
    assert batch_progress_summary(marker) == ''
    assert _batch_badge_parts(marker) == ('R261109-1', '')


def test_batch_details_preserve_pending_and_delivered_breakdown():
    marker = dict(total_count=5, delivered_count=1, remaining_count=4,
                  pending_delivery_count=2)
    assert fluent_views.batch_progress_details(marker) == '待完成2，共5；待交付2，已交付1'
    assert fluent_views.batch_progress_details({}) == ''
