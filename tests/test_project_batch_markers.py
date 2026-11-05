"""项目列表只读批次索引，全部使用匿名隔离数据库。"""
import importlib.util
import json
import sqlite3
from contextlib import contextmanager

import pytest

from database import Database
from desktop_service import DesktopService
from parser import ParsedProject


def reader(connection):
    assert importlib.util.find_spec('project_batches'), '只读批次索引尚未实现'
    from project_batches import read_project_batch_index
    return read_project_batch_index(connection)


def service_at(tmp_path):
    return DesktopService(tmp_path / 'anonymous' / 'workbox.db')


def test_file_first_then_receipt_reuses_original_project_and_shares_marker(tmp_path):
    service = service_at(tmp_path)
    service.capture_message('匿名原项目系统', '原建项', [ParsedProject('匿名原项目系统')])
    service.set_status(1, '开发中')
    received = service.receive_text('1. 匿名原项目系统\n2. 匿名后收项目工具')
    assert received['reused_project_ids'] == [1]
    snapshot = service.project_workbench_snapshot()
    rows = {row['id']: row for row in snapshot['projects']}
    assert rows[1]['batch_id'] != rows[2]['batch_id']
    assert rows[1]['status'] == '开发中'
    assert rows[1]['batch_markers'] == rows[2]['batch_markers']
    assert rows[1]['batch_markers'][0]['key'] == f"receipt:{received['receipt_id']}"
    with service.db.connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM task_groups').fetchone()[0] == 0


def test_multiple_receipts_sort_recent_first_and_search_filter_preserve_original_order(tmp_path):
    service = service_at(tmp_path)
    one = service.receive_text('1. 匿名甲系统\n2. 匿名乙系统')
    two = service.receive_text('1. 匿名甲系统\n2. 匿名丙系统')
    service.set_urgent(1, True)
    service.set_status(2, '待交付')
    service.update_project_details(3, '匿名备注检索', None)
    snapshot = service.project_workbench_snapshot()
    assert [r['id'] for r in snapshot['projects']] == [r['id'] for r in service.list_projects()]
    markers = snapshot['projects'][0]['batch_markers']
    assert [m['key'] for m in markers] == [f"receipt:{two['receipt_id']}", f"receipt:{one['receipt_id']}"]
    for query in (markers[0]['code'], markers[0]['key'], markers[0]['label']):
        assert [r['id'] for r in service.project_workbench_snapshot(query=query)['projects']] == [1, 3]
    assert [r['id'] for r in service.project_workbench_snapshot(batch_key=markers[1]['key'], status='待交付')['projects']] == [2]
    assert [r['id'] for r in service.project_workbench_snapshot(query='匿名备注检索')['projects']] == [3]
    assert service.project_workbench_snapshot(batch_key='receipt:missing')['projects'] == []


def test_legacy_current_group_move_and_latest_color_override_covered_receipt(tmp_path):
    service = service_at(tmp_path)
    first = service.task_groups.receive('1. 匿名甲系统\n2. 匿名乙系统')
    target = service.task_groups.receive('1. 匿名丙系统', new_round=True)
    service.task_groups.move_task(first['task_ids'][0], target['group_id'], confirmed=True)
    service.task_groups.set_color(target['group_id'], '#123456')
    service.task_groups.set_color(target['group_id'], '#ABCDEF')
    with service.db.connect() as connection:
        connection.execute('BEGIN')
        index = reader(connection)
    assert [m['key'] for m in index['by_project'][1]] == [f"group:{target['group_id']}"]
    assert index['by_project'][1][0]['color'] == '#ABCDEF'
    assert index['by_project'][2][0]['key'] == f"group:{first['group_id']}"
    assert not any(m['source_kind'] == 'receipt' for m in index['options'])


def test_canonical_merge_unions_memberships_without_duplicate_rows(tmp_path):
    service = service_at(tmp_path)
    one = service.task_groups.receive('1. 匿名甲系统')
    two = service.task_groups.receive('1. 匿名乙系统')
    with service.db.connect() as connection:
        connection.execute("INSERT INTO project_merges(source_id,target_id,operation_id,snapshot_json) VALUES (1,2,'anonymous-merge','{}')")
    snapshot = service.project_workbench_snapshot()
    assert [r['id'] for r in snapshot['projects']] == [2]
    assert {m['key'] for m in snapshot['projects'][0]['batch_markers']} == {f"group:{one['group_id']}", f"group:{two['group_id']}"}


def test_old_database_without_task_tables_uses_real_creation_batch(tmp_path):
    db = Database(tmp_path / 'old.db')
    db.create_capture('匿名旧项目系统', '匿名旧批次', [('匿名旧项目系统', False)])
    with db.connect() as connection:
        connection.execute('BEGIN')
        before = connection.total_changes
        index = reader(connection)
        assert connection.total_changes == before
        assert connection.in_transaction
        assert not connection.execute("SELECT 1 FROM sqlite_master WHERE name='task_groups'").fetchone()
    marker = index['by_project'][1][0]
    assert marker['source_kind'] == 'creation_batch'
    assert marker['key'] == 'batch:1'
    assert marker['code'].startswith('B')
    assert index['warnings'] == []


def test_invalid_receipt_json_and_orphan_ids_are_explicit_warnings(tmp_path):
    service = service_at(tmp_path)
    service.receive_text('1. 匿名甲系统')
    with service.db.connect() as connection:
        connection.execute("UPDATE intake_receipts SET project_ids='broken' WHERE id=1")
        connection.execute("INSERT INTO intake_receipts(fingerprint,raw_text,candidates,state,batch_name,project_ids) VALUES ('orphan','匿名','[]','committed','匿名','[999]')")
        connection.execute('BEGIN') if not connection.in_transaction else None
        index = reader(connection)
    assert len(index['warnings']) >= 2
    assert not any(m['source_kind'] == 'receipt' for m in index['options'])
    assert index['by_project'][1][0]['source_kind'] == 'creation_batch'


def test_reader_requires_transaction_and_list_projects_validates_shared_database(tmp_path):
    db = Database(tmp_path / 'one.db')
    other = Database(tmp_path / 'other.db')
    with db.connect() as connection:
        with pytest.raises(ValueError):
            reader(connection)
        with pytest.raises(ValueError):
            db.list_projects(connection=connection)
        connection.execute('BEGIN')
        assert db.list_projects(connection=connection) == []
        assert connection.in_transaction
    with other.connect() as connection:
        connection.execute('BEGIN')
        with pytest.raises(ValueError):
            db.list_projects(connection=connection)


@pytest.mark.parametrize('with_legacy_tasks', [False, True])
def test_snapshot_is_one_read_transaction_constant_queries_and_no_writes(tmp_path, monkeypatch, with_legacy_tasks):
    service = service_at(tmp_path)
    with service.db.connect() as connection:
        bid = connection.execute("INSERT INTO batches(name) VALUES ('匿名千级批次')").lastrowid
        mid = connection.execute("INSERT INTO messages(batch_id,raw_text) VALUES (?,'匿名千级原文')", (bid,)).lastrowid
        connection.executemany('INSERT INTO projects(message_id,batch_id,name) VALUES (?,?,?)', [(mid, bid, f'匿名项目{i}系统') for i in range(1100)])
        connection.execute("INSERT INTO intake_receipts(fingerprint,raw_text,candidates,state,batch_name,project_ids) VALUES ('bulk','匿名','[]','committed','匿名',?)", (json.dumps(list(range(1, 1101))),))
        if with_legacy_tasks:
            connection.execute("INSERT INTO task_groups(code,received_day,day_sequence,color,created_at) VALUES ('G20261105-001','2026-11-05',1,'#507080','2026-11-05T00:00:00Z')")
            connection.executemany("INSERT INTO task_items(code,original_group_id,sequence,project_id,project_name,created_at,delivery_floor) VALUES (?,1,?,?,?,'2026-11-05T00:00:00Z',0)",
                                   [(f'G20261105-001-{pid:04}', pid, pid, f'匿名项目{pid}系统') for pid in range(1, 1101)])
            connection.execute("INSERT INTO task_group_messages(group_id,receipt_id,raw_text,fingerprint,received_at,sequence) VALUES (1,1,'匿名','anonymous','2026-11-05T00:00:00Z',1)")
    statements, connections = [], []
    original = service.db.connect

    @contextmanager
    def traced():
        with original() as connection:
            connections.append(connection)
            connection.set_trace_callback(statements.append)
            connection.execute('PRAGMA query_only=ON')
            yield connection

    monkeypatch.setattr(service.db, 'connect', traced)
    snapshot = service.project_workbench_snapshot()
    assert len(snapshot['projects']) == 1100
    assert len(connections) == 1
    assert sum(s.lstrip().upper().startswith(('SELECT', 'WITH', 'PRAGMA')) for s in statements) <= 18
    assert any(s == 'BEGIN' for s in statements)
    assert all(row['batch_markers'][0]['key'] == ('group:1' if with_legacy_tasks else 'receipt:1') for row in snapshot['projects'])


@pytest.mark.parametrize('text', ['G20261105-001-01 匿名客户系统', '1. G20261105-001-01 匿名客户系统', 'G20261105-001 匿名客户系统'])
def test_old_numbered_task_messages_never_create_code_prefixed_projects(tmp_path, text):
    service = service_at(tmp_path)
    result = service.receive_text(text)
    assert result['state'] == 'needs-review'
    assert service.db.counts()['projects'] == 0
    assert result['raw_text'] == text


def test_compact_labels_keep_two_digit_year_and_unknown_date_is_honest(tmp_path):
    service = service_at(tmp_path)
    service.receive_text('1. 匿名甲系统')
    with service.db.connect() as connection:
        connection.execute("UPDATE intake_receipts SET created_at='2026-11-05T10:00:00Z'")
        connection.execute('BEGIN') if not connection.in_transaction else None
        marker = reader(connection)['by_project'][1][0]
    assert marker['code'] == 'R20261105-1'
    assert marker['label'] == 'R261105-1'
    with service.db.connect() as connection:
        connection.execute("UPDATE intake_receipts SET created_at='not-a-date'")
        connection.execute('BEGIN') if not connection.in_transaction else None
        marker = reader(connection)['by_project'][1][0]
    assert marker['code'] == 'R未知-1'
    assert marker['label'] == 'R未知-1'


def test_snapshot_does_not_mix_writer_commit_between_rows_and_markers(tmp_path, monkeypatch):
    service = service_at(tmp_path)
    service.capture_message('匿名甲系统', '匿名建项', [ParsedProject('匿名甲系统')])
    import project_batches
    original = project_batches.read_project_batch_index

    def write_between_reads(connection):
        with service.db.connect() as writer:
            writer.execute("INSERT INTO intake_receipts(fingerprint,raw_text,candidates,state,batch_name,project_ids) VALUES ('concurrent','匿名','[]','committed','匿名','[1]')")
        return original(connection)

    monkeypatch.setattr(project_batches, 'read_project_batch_index', write_between_reads)
    snapshot = service.project_workbench_snapshot()
    assert snapshot['projects'][0]['batch_markers'][0]['source_kind'] == 'creation_batch'
    monkeypatch.setattr(project_batches, 'read_project_batch_index', original)
    assert service.project_workbench_snapshot()['projects'][0]['batch_markers'][0]['source_kind'] == 'receipt'


def test_original_zip_search_all_statuses_and_due_order_remain_available(tmp_path):
    service = service_at(tmp_path)
    service.receive_text('1. 匿名甲系统\n2. 匿名乙系统\n3. 匿名丙系统\n4. 匿名丁系统')
    for pid, status in enumerate(('待开发', '开发中', '待交付', '已交付'), 1):
        with service.db.connect() as connection:
            connection.execute('UPDATE projects SET status=? WHERE id=?', (status, pid))
    for pid, status in enumerate(('待开发', '开发中', '待交付', '已交付'), 1):
        assert [r['id'] for r in service.project_workbench_snapshot(status=status)['projects']] == [pid]
    service.update_project_details(1, '', '2026-11-08')
    service.update_project_details(2, '', '2026-11-06')
    with service.db.connect() as connection:
        for index, name in enumerate(('匿名旧附件.zip', '匿名最新附件.zip')):
            connection.execute("INSERT INTO zips(project_id,filename,path,size,sha256,mtime_ns) VALUES (1,?, ?,1,?,1)", (name, f'anonymous-{index}', 'a' * 64))
    assert [r['id'] for r in service.project_workbench_snapshot(query='匿名旧附件')['projects']] == [1]
    assert [r['id'] for r in service.project_workbench_snapshot()['projects']] == [2, 1, 4, 3]


def test_missing_task_action_source_does_not_claim_original_membership(tmp_path):
    service = service_at(tmp_path)
    service.task_groups.receive('1. 匿名甲系统')
    with sqlite3.connect(service.db.path) as connection:
        connection.execute('DROP TABLE task_actions')
    with service.db.connect() as connection:
        connection.execute('BEGIN')
        index = reader(connection)
    assert any('移组' in warning for warning in index['warnings'])
    assert index['by_project'][1][0]['source_kind'] == 'creation_batch'
