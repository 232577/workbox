"""项目新增日期取自已有建项事实，匿名隔离数据库不补写历史。"""
from contextlib import contextmanager

import pytest

from desktop_service import DesktopService


def service_at(tmp_path, moments):
    service = DesktopService(tmp_path / 'anonymous' / 'workbox.db')
    with service.db.connect() as connection:
        bid = connection.execute("INSERT INTO batches(name) VALUES ('匿名日期批次')").lastrowid
        mid = connection.execute("INSERT INTO messages(batch_id,raw_text) VALUES (?,'匿名原文')", (bid,)).lastrowid
        connection.executemany('INSERT INTO projects(message_id,batch_id,name,created_at) VALUES (?,?,?,?)',
                               [(mid, bid, f'匿名项目{i}系统', moment) for i, moment in enumerate(moments, 1)])
    return service


def test_shanghai_midnight_naive_utc_and_explicit_offsets_filter_without_reordering(tmp_path):
    service = service_at(tmp_path, ['2026-11-08T15:59:59Z', '2026-11-08T16:00:00Z',
                                    '2026-11-09 15:59:59', '2026-11-09T00:30:00+09:00'])
    snapshot = service.project_workbench_snapshot()
    assert {p['id']: p['added_on'] for p in snapshot['projects']} == {
        1: '2026-11-08', 2: '2026-11-09', 3: '2026-11-09', 4: '2026-11-08'}
    assert [p['id'] for p in service.project_workbench_snapshot(added_on='2026-11-09')['projects']] == [3, 2]
    assert snapshot['added_date_choices'] == [
        dict(added_on='2026-11-09', label='2026-11-09', total_count=2),
        dict(added_on='2026-11-08', label='2026-11-08', total_count=2)]


@pytest.mark.parametrize('value', ['', 'not-a-date', '2026-11-09', '2026-02-30T01:00:00Z'])
def test_missing_or_invalid_timestamp_is_unknown_and_is_never_today(tmp_path, value):
    service = service_at(tmp_path, [value, '2026-11-08T16:00:00Z'])
    snapshot = service.project_workbench_snapshot(added_on='unknown')
    assert [p['id'] for p in snapshot['projects']] == [1]
    assert snapshot['projects'][0]['added_on'] is None
    assert snapshot['projects'][0]['added_on_label'] == '日期未知'
    assert snapshot['added_date_choices'][-1] == dict(added_on='unknown', label='日期未知', total_count=1)


@pytest.mark.parametrize('value', [False, 20261109, '', '20261109', '2026-11-9', '2026-02-30', '2026-11-09T00:00:00'])
def test_added_date_filter_requires_exact_existing_date_or_unknown(tmp_path, value):
    service = service_at(tmp_path, ['2026-11-09T00:00:00Z'])
    with pytest.raises(ValueError, match='日期'):
        service.project_workbench_snapshot(added_on=value)


def test_options_ignore_other_filters_and_batch_progress_keeps_whole_batch(tmp_path):
    service = service_at(tmp_path, ['2026-11-08T00:00:00Z', '2026-11-09T00:00:00Z',
                                    '2026-11-09T01:00:00Z'])
    service.set_status(2, '待交付')
    service.set_urgent(2, True)
    with service.db.connect() as connection:
        connection.execute("UPDATE projects SET status='已交付' WHERE id=3")
    service.update_project_details(2, '匿名筛选备注', '2026-11-11')
    snapshot = service.project_workbench_snapshot(query='匿名筛选备注', status='待交付',
        urgent_only=True, added_on='2026-11-09', batch_key='batch:1', type_id=0)
    assert [p['id'] for p in snapshot['projects']] == [2]
    assert [(c['added_on'], c['total_count']) for c in snapshot['added_date_choices']] == [
        ('2026-11-09', 2), ('2026-11-08', 1)]
    assert snapshot['batch_options'][0]['total_count'] == 3
    assert snapshot['batch_options'][0]['delivered_count'] == 1
    assert snapshot['batch_options'][0]['remaining_count'] == 2


def test_date_options_follow_visibility_and_refresh_after_mutations(tmp_path):
    service = service_at(tmp_path, ['2026-11-07T00:00:00Z', '2026-11-08T00:00:00Z',
                                    '2026-11-09T00:00:00Z'])
    service.db.bulk_update_projects([2], archived=True)
    service.db.delete_project(3, confirmed=True)
    assert [c['added_on'] for c in service.project_workbench_snapshot()['added_date_choices']] == ['2026-11-07']
    assert [c['added_on'] for c in service.project_workbench_snapshot(archived=True)['added_date_choices']] == ['2026-11-08']
    assert [c['added_on'] for c in service.project_workbench_snapshot(deleted=True)['added_date_choices']] == ['2026-11-09']
    service.db.restore_deleted_project(3)
    assert [c['added_on'] for c in service.project_workbench_snapshot()['added_date_choices']] == ['2026-11-09', '2026-11-07']


def test_date_rows_and_choices_use_one_read_snapshot_without_writes(tmp_path, monkeypatch):
    service = service_at(tmp_path, ['2026-11-08T16:00:00Z'] * 1100)
    original = service.db.connect
    connections, statements = [], []

    @contextmanager
    def traced():
        with original() as connection:
            connections.append(connection)
            connection.execute('PRAGMA query_only=ON')
            connection.set_trace_callback(statements.append)
            yield connection

    monkeypatch.setattr(service.db, 'connect', traced)
    snapshot = service.project_workbench_snapshot(added_on='2026-11-09')
    assert len(snapshot['projects']) == 1100
    assert snapshot['added_date_choices'] == [dict(added_on='2026-11-09', label='2026-11-09', total_count=1100)]
    assert len(connections) == 1 and 'BEGIN' in statements
    assert sum(s.lstrip().upper().startswith(('SELECT', 'WITH', 'PRAGMA')) for s in statements) <= 19


def test_repeat_receipt_status_edit_rename_and_reopen_keep_original_added_date(tmp_path):
    service = DesktopService(tmp_path / 'anonymous' / 'workbox.db')
    first = service.receive_text('1. 匿名原名系统')
    pid = first['project_ids'][0]
    original = '2026-11-07T16:00:00Z'
    with service.db.connect() as connection:
        connection.execute('UPDATE projects SET created_at=? WHERE id=?', (original, pid))
    service.set_status(pid, '开发中')
    service.update_project_details(pid, '匿名修改备注', '2026-12-01')
    repeated = service.receive_text('1. 匿名原名系统\n2. 匿名新增工具')
    assert repeated['reused_project_ids'] == [pid]
    plan = service.preview_project_rename(pid, '匿名正确名系统')
    service.rename_project(pid, '匿名正确名系统', expected_token=plan['token'])
    reopened = DesktopService(service.db.path)
    row = reopened.project_workbench_snapshot(added_on='2026-11-08')['projects'][0]
    assert row['id'] == pid and row['name'] == '匿名正确名系统'
    assert row['created_at'] == original and row['added_on'] == '2026-11-08'


def test_merge_retains_canonical_added_date_and_original_source_history(tmp_path):
    service = DesktopService(tmp_path / 'anonymous' / 'workbox.db')
    source = service.receive_text('1. 匿名来源系统')['project_ids'][0]
    target = service.receive_text('1. 匿名保留系统')['project_ids'][0]
    moments = [(source, '2026-11-06T16:00:00Z'), (target, '2026-11-08T16:00:00Z')]
    with service.db.connect() as connection:
        connection.executemany('UPDATE projects SET created_at=? WHERE id=?', [(stamp, pid) for pid, stamp in moments])
    plan = service.preview_project_rename(source, '匿名保留系统')
    service.rename_project(source, '匿名保留系统', expected_token=plan['token'],
                           merge_target_id=target, confirmed_merge=True)
    snapshot = service.project_workbench_snapshot()
    assert [(p['id'], p['added_on']) for p in snapshot['projects']] == [(target, '2026-11-09')]
    assert service.project_workbench_snapshot(added_on='2026-11-07')['projects'] == []
    assert snapshot['added_date_choices'] == [dict(added_on='2026-11-09', label='2026-11-09', total_count=1)]
    with service.db.connect() as connection:
        assert [(r['id'], r['created_at']) for r in connection.execute('SELECT id,created_at FROM projects ORDER BY id')] == moments


def test_concurrent_writer_cannot_change_date_options_mid_snapshot(tmp_path, monkeypatch):
    service = service_at(tmp_path, ['2026-11-08T16:00:00Z'])
    import project_batches
    original = project_batches.read_project_batch_index

    def write_between_reads(connection):
        with service.db.connect() as writer:
            writer.execute("UPDATE projects SET created_at='2026-11-06T16:00:00Z'")
        return original(connection)

    monkeypatch.setattr(project_batches, 'read_project_batch_index', write_between_reads)
    snapshot = service.project_workbench_snapshot(added_on='2026-11-09')
    assert snapshot['projects'][0]['added_on'] == '2026-11-09'
    assert snapshot['added_date_choices'] == [dict(added_on='2026-11-09', label='2026-11-09', total_count=1)]
    monkeypatch.setattr(project_batches, 'read_project_batch_index', original)
    assert service.project_workbench_snapshot()['projects'][0]['added_on'] == '2026-11-07'
