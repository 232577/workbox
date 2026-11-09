"""批次进度使用匿名项目的当前状态及完整批次归属。"""
import json
from pathlib import Path

import pytest

from desktop_service import DesktopService


@pytest.fixture
def service(tmp_path):
    return DesktopService(tmp_path / 'anonymous' / 'workbox.db')


def receipt_key(receipt):
    return f"receipt:{receipt['receipt_id']}"


def deliver(service, project_id):
    root = Path(service.workspaces.get_workspace(project_id)['path'])
    (root / '匿名材料.txt').write_text('匿名交付材料', encoding='utf-8')
    service.set_status(project_id, '待交付')
    return service.confirm_delivery(project_id, confirmed=True)['event_id']


def assert_batch_counts(snapshot, key, expected):
    option, = [item for item in snapshot['batch_options'] if item['key'] == key]
    fields = ('total_count', 'delivered_count', 'remaining_count')
    assert tuple(option.get(field) for field in fields) == expected
    for row in snapshot['projects']:
        for marker in row['batch_markers']:
            if marker['key'] == key:
                assert marker == option


def test_batch_progress_tracks_none_partial_and_all_delivered(service):
    receipt = service.receive_text('1. 匿名甲系统\n2. 匿名乙系统')
    key = receipt_key(receipt)
    first, second = receipt['project_ids']
    assert_batch_counts(service.project_workbench_snapshot(), key, (2, 0, 2))

    deliver(service, first)
    assert_batch_counts(service.project_workbench_snapshot(), key, (2, 1, 1))

    deliver(service, second)
    assert_batch_counts(service.project_workbench_snapshot(), key, (2, 2, 0))


def test_pending_delivery_is_separate_from_remaining_work_without_writes(service):
    receipt = service.receive_text('1. 匿名待开发\n2. 匿名开发中\n3. 匿名待交付\n4. 匿名已交付')
    new, working, pending, delivered = receipt['project_ids']
    service.set_status(working, '开发中')
    service.set_status(pending, '待交付')
    deliver(service, delivered)
    with service.db.connect() as connection:
        before = list(connection.iterdump())
    for filters in ({}, {'status': '待交付'}, {'status': '待开发'},
                    {'query': '匿名开发中'}, {'query': '没有匹配'}):
        snapshot = service.project_workbench_snapshot(**filters)
        marker, = snapshot['batch_options']
        assert marker['total_count'] == 4
        assert marker['delivered_count'] == 1
        assert marker['pending_delivery_count'] == 1
        # Keep the original unreadied-or-undelivered count for existing readers.
        assert marker['remaining_count'] == 3
        assert marker['remaining_count'] - marker['pending_delivery_count'] == 2
    with service.db.connect() as connection:
        assert list(connection.iterdump()) == before


def test_pending_delivery_counts_follow_current_state_and_deleted_members(service):
    receipt = service.receive_text('1. 匿名甲\n2. 匿名乙')
    first, second = receipt['project_ids']
    service.set_status(first, '待交付')
    service.set_status(second, '待交付')
    service.bulk_update_projects([first], archived=True)

    def counts():
        marker, = service.project_workbench_snapshot()['batch_options']
        return (marker['total_count'], marker['delivered_count'],
                marker['pending_delivery_count'], marker['remaining_count'])

    assert counts() == (2, 0, 2, 2)
    service.set_status(second, '开发中')
    assert counts() == (2, 0, 1, 2)
    service.delete_project(second, confirmed=True)
    assert counts() == (1, 0, 1, 1)
    service.restore_deleted_project(second)
    assert counts() == (2, 0, 1, 2)


def test_batch_progress_ignores_workload_and_counts_current_state_after_void(service):
    receipt = service.receive_text('1. 匿名代交系统')
    key = receipt_key(receipt)
    project_id, = receipt['project_ids']
    event_id = deliver(service, project_id)
    service.set_project_delivery_counting([project_id], False, confirmed=True)
    assert service.db.delivery_history()['project_count'] == 0
    assert_batch_counts(service.project_workbench_snapshot(), key, (1, 1, 0))

    service.db.void_delivery_events([event_id], confirmed=True)
    assert service.get_project(project_id)['status'] == '待交付'
    assert_batch_counts(service.project_workbench_snapshot(), key, (1, 0, 1))

    service.db.restore_delivery_events([event_id], confirmed=True)
    assert service.db.delivery_history()['valid_event_count'] == 1
    assert_batch_counts(service.project_workbench_snapshot(), key, (1, 0, 1))


def test_batch_progress_uses_full_membership_before_filters_and_includes_archived(service):
    receipt = service.receive_text('1. 匿名甲系统\n2. 匿名乙系统\n3. 匿名丙系统\n4. 匿名丁系统')
    key = receipt_key(receipt)
    first, second, third, fourth = receipt['project_ids']
    deliver(service, first)
    service.bulk_update_projects([first], archived=True)
    service.set_status(second, '待交付')
    service.set_status(third, '开发中')
    kind = service.create_project_type('匿名筛选类型')['id']
    service.set_project_types([second], kind)

    for filters, visible_ids in (
        ({'status': '待交付', 'batch_key': key}, {second}),
        ({'type_id': kind}, {second}),
        ({'query': '匿名丙系统'}, {third}),
        ({'query': '无匹配名称'}, set()),
        ({'archived': True}, {first}),
        ({}, {second, third, fourth}),
    ):
        snapshot = service.project_workbench_snapshot(**filters)
        assert {row['id'] for row in snapshot['projects']} == visible_ids
        assert_batch_counts(snapshot, key, (4, 1, 3))


def test_batch_progress_deduplicates_canonical_projects_in_each_receipt(service):
    one = service.receive_text('1. 匿名甲系统\n2. 匿名乙系统')
    source, target = one['project_ids']
    two = service.receive_text('1. 匿名甲系统\n2. 匿名丙系统')
    _, other = two['project_ids']
    deliver(service, target)
    with service.db.connect() as connection:
        connection.execute('UPDATE intake_receipts SET project_ids=? WHERE id=?',
                           (json.dumps([source, target, source]), one['receipt_id']))
        connection.execute('''INSERT INTO project_merges(source_id,target_id,operation_id,snapshot_json)
                              VALUES (?,?,'anonymous-merge','{}')''', (source, target))

    snapshot = service.project_workbench_snapshot()
    assert {row['id'] for row in snapshot['projects']} == {target, other}
    assert_batch_counts(snapshot, receipt_key(one), (1, 1, 0))
    assert_batch_counts(snapshot, receipt_key(two), (2, 1, 1))


def test_batch_progress_excludes_deleted_members_until_restore(service):
    receipt = service.receive_text('1. 匿名甲系统\n2. 匿名乙系统')
    key = receipt_key(receipt)
    first, second = receipt['project_ids']
    deliver(service, first)
    service.delete_project(second, confirmed=True)
    assert_batch_counts(service.project_workbench_snapshot(), key, (1, 1, 0))
    assert_batch_counts(service.project_workbench_snapshot(deleted=True), key, (1, 1, 0))

    service.delete_project(first, confirmed=True)
    snapshot = service.project_workbench_snapshot(deleted=True)
    assert {row['id'] for row in snapshot['projects']} == {first, second}
    assert_batch_counts(snapshot, key, (0, 0, 0))

    service.restore_deleted_project(second)
    assert_batch_counts(service.project_workbench_snapshot(), key, (1, 0, 1))


def test_batch_progress_uses_verified_fallback_for_damaged_receipt(service):
    receipt = service.receive_text('1. 匿名历史项目')
    with service.db.connect() as connection:
        connection.execute("UPDATE intake_receipts SET project_ids='invalid' WHERE id=?", (receipt['receipt_id'],))
        before = list(connection.iterdump())

    snapshot = service.project_workbench_snapshot()
    project, = snapshot['projects']
    option, = snapshot['batch_options']
    assert option['source_kind'] == 'creation_batch'
    assert option['source_id'] == project['batch_id']
    assert any('损坏' in warning for warning in snapshot['warnings'])
    assert_batch_counts(snapshot, option['key'], (1, 0, 1))
    with service.db.connect() as connection:
        assert list(connection.iterdump()) == before


def test_project_status_and_batch_progress_use_same_snapshot(service, monkeypatch):
    import project_batches

    receipt = service.receive_text('1. 匿名并发系统')
    project_id, = receipt['project_ids']
    event_id = deliver(service, project_id)
    read_index = project_batches.read_project_batch_index

    def void_after_rows(connection):
        service.db.void_delivery_events([event_id], confirmed=True)
        return read_index(connection)

    with monkeypatch.context() as patch:
        patch.setattr(project_batches, 'read_project_batch_index', void_after_rows)
        before = service.project_workbench_snapshot()
    assert before['projects'][0]['status'] == '已交付'
    assert_batch_counts(before, receipt_key(receipt), (1, 1, 0))

    after = service.project_workbench_snapshot()
    assert after['projects'][0]['status'] == '待交付'
    assert_batch_counts(after, receipt_key(receipt), (1, 0, 1))


def test_batch_progress_query_count_does_not_grow_with_members(service):
    from project_batches import read_project_batch_index

    def read_index():
        statements = []
        with service.db.connect() as connection:
            connection.execute('PRAGMA query_only=ON')
            connection.execute('BEGIN')
            connection.set_trace_callback(statements.append)
            index = read_project_batch_index(connection)
            assert connection.in_transaction
            assert connection.total_changes == 0
        selects = sum(sql.lstrip().upper().startswith(('SELECT', 'WITH')) for sql in statements)
        return index, selects

    first = service.db.create_capture('匿名原文', '匿名首批', [('匿名单个项目', False)])
    small_index, small_selects = read_index()
    assert small_index['by_project'][first['project_ids'][0]][0]['total_count'] == 1

    extra = service.db.create_capture('匿名多项原文', '匿名多项批次',
                                     [(f'匿名项目{number}', False) for number in range(60)])
    large_index, large_selects = read_index()
    assert large_index['by_project'][extra['project_ids'][0]][0]['total_count'] == 60
    assert large_selects == small_selects
