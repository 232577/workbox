"""Archive facts retain their read semantics without one connection per version."""
from contextlib import contextmanager
import json
from math import ceil

from desktop_service import DesktopService


def versions_at(tmp_path, count):
    service = DesktopService(tmp_path / 'anonymous' / 'data' / 'workbox.db')
    with service.db.connect() as connection:
        bid = connection.execute("INSERT INTO batches(name) VALUES ('匿名基准批次')").lastrowid
        mid = connection.execute("INSERT INTO messages(batch_id,raw_text) VALUES (?,'匿名项目原文')", (bid,)).lastrowid
        connection.executemany('INSERT INTO projects(id,message_id,batch_id,name) VALUES (?,?,?,?)',
            [(pid, mid, bid, f'匿名项目{pid}') for pid in range(1, count + 1)])
        source = str(tmp_path / 'anonymous-source.zip')
        sid = connection.execute("INSERT INTO zip_sources(source_key,source_path) VALUES ('anonymous',?)", (source,)).lastrowid
        connection.executemany('INSERT INTO zips(id,project_id,path,filename,size,sha256,mtime_ns) VALUES (?,?,?,?,1,?,1)',
            [(pid, pid, str(tmp_path / f'anonymous-{pid}.zip'), f'匿名版本{pid}.zip', 'a' * 64) for pid in range(1, count + 1)])
        connection.executemany('INSERT INTO zip_snapshots(zip_id,source_id,source_path) VALUES (?,?,?)',
            [(pid, sid, source) for pid in range(1, count + 1)])
    return service


def fact(connection, zip_id, project_id, count):
    connection.execute("""INSERT INTO zip_project_file_counts(zip_id,project_id,file_count,decided_status,rule)
        VALUES (?,?,?,'待交付','zip-file-count-v1')""", (zip_id, project_id, count))


def test_full_archive_list_uses_one_snapshot_and_bounded_fact_queries(tmp_path, monkeypatch):
    count = 1005  # Cross the portable SQLite bind-parameter chunk boundary.
    service = versions_at(tmp_path, count)
    with service.db.connect() as connection:
        for pid in (1, 500, 501, count):
            fact(connection, pid, pid, pid)
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
    result = service.snapshots.list_versions()
    assert [row['zip_id'] for row in result] == list(range(count, 0, -1))
    assert len(connections) == 1
    assert sum(sql.lstrip().upper().startswith(('SELECT', 'WITH')) for sql in statements) <= 1 + ceil(count / 500)
    assert 'BEGIN' in statements
    by_id = {row['zip_id']: row for row in result}
    assert by_id[501]['file_counts'][0]['file_count'] == 501
    assert by_id[2]['file_counts'] == []  # Unknown is not a measured zero.


def test_counts_match_single_zip_reader_and_needs_review_stays_empty(tmp_path):
    service = versions_at(tmp_path, 3)
    with service.db.connect() as connection:
        fact(connection, 1, 1, 0)
        fact(connection, 1, 2, 99)  # Historical unlinked fact: omit in ordinary version list.
        connection.execute('INSERT INTO zip_bundle_members(zip_id,project_id) VALUES (1,1)')
        fact(connection, 3, 3, 8)
        connection.execute("INSERT INTO zip_file_count_policies(zip_id,project_id,auto_deliver_zip,zip_delivery_min_files) VALUES (1,1,0,9)")
        connection.execute("INSERT INTO zip_bundles(zip_id,declared_count,member_names,match_state,reason) VALUES (3,2,?,'needs-review','匿名歧义')",
                           (json.dumps(['匿名甲', '匿名乙']),))
    expected = service.db.get_zip_file_counts(1)
    result = {row['zip_id']: row for row in service.snapshots.list_versions()}
    assert result[1]['file_counts'] == expected
    assert result[1]['file_counts'][0]['file_count'] == 0
    assert result[1]['file_counts'][0]['auto_deliver_zip'] is False
    assert result[1]['file_counts'][0]['zip_delivery_min_files'] == 9
    assert len(service.db.get_zip_file_counts(1, include_unlinked=True)) == 2
    assert result[2]['file_counts'] == []
    assert result[3]['file_counts'] == []


def test_combined_zip_project_filter_order_and_merged_approval_are_preserved(tmp_path):
    service = versions_at(tmp_path, 3)
    with service.db.connect() as connection:
        connection.execute("UPDATE zips SET project_id=NULL WHERE id=3")
        connection.executemany('INSERT INTO zip_bundle_members(zip_id,project_id) VALUES (3,?)', [(1,), (2,)])
        connection.execute("INSERT INTO zip_bundles(zip_id,declared_count,member_names,match_state,reason) VALUES (3,2,?,'matched','')",
                           (json.dumps(['匿名项目1', '匿名项目2']),))
        fact(connection, 3, 2, 9)
        fact(connection, 3, 1, 4)
        connection.executemany('INSERT INTO zip_project_approvals(project_id,zip_id) VALUES (?,3)', [(1,), (2,)])
        connection.execute("INSERT INTO project_merges(source_id,target_id,operation_id,snapshot_json) VALUES (1,2,'anonymous-merge','{}')")
    old_counts = service.db.get_zip_file_counts(3)
    rows = service.snapshots.list_versions(2)
    assert [row['zip_id'] for row in rows] == [3, 2]
    combined = rows[0]
    assert combined['file_counts'] == old_counts
    assert [row['project_id'] for row in combined['file_counts']] == [1, 2]
    assert combined['approved'] is True and combined['approved_count'] == 1
    assert combined['project_count'] == 2
    assert combined['latest'] == 1 and rows[1]['latest'] == 0


def test_version_rows_and_file_facts_share_the_same_read_snapshot(tmp_path, monkeypatch):
    service = versions_at(tmp_path, 1)
    original_reader = getattr(service.db, '_zip_file_counts_many', None)
    assert original_reader is not None, 'Archive facts need a caller-snapshot bulk reader'
    original_connect = service.db.connect

    def write_after_version_rows(connection, zip_ids):
        assert connection.in_transaction
        with original_connect() as writer:
            fact(writer, 1, 1, 7)
        return original_reader(connection, zip_ids)

    monkeypatch.setattr(service.db, '_zip_file_counts_many', write_after_version_rows)
    assert service.snapshots.list_versions()[0]['file_counts'] == []
    assert service.db.get_zip_file_counts(1)[0]['file_count'] == 7


def test_empty_archive_list_keeps_one_connection_and_no_unknown_facts(tmp_path, monkeypatch):
    service = versions_at(tmp_path, 1)
    original = service.db.connect
    connections = []

    @contextmanager
    def traced():
        with original() as connection:
            connections.append(connection)
            yield connection

    monkeypatch.setattr(service.db, 'connect', traced)
    assert service.snapshots.list_versions(99999) == []
    assert len(connections) == 1


def test_dense_fact_read_does_not_rescan_all_zip_links_for_every_chunk(tmp_path):
    costs = []
    for count in (200, 2000):
        service = versions_at(tmp_path / str(count), count)
        with service.db.connect() as connection:
            for pid in range(1, count + 1):
                fact(connection, pid, pid, pid % 17)
        steps = [0]
        with service.db.connect() as connection:
            connection.execute('PRAGMA query_only=ON')
            connection.execute('BEGIN')
            def progress():
                steps[0] += 100
                return 0
            connection.set_progress_handler(progress, 100)
            result = service.db._zip_file_counts_many(connection, range(1, count + 1))
            assert len(result) == count
        costs.append(steps[0])
    # Ten times the measured facts allows linear growth plus fixed query overhead,
    # without using wall-clock thresholds or allowing each chunk to rebuild all links.
    assert costs[1] <= costs[0] * 12
