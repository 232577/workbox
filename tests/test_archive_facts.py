"""归档登记事实只按精确事件归属读取，不把未知数量当作零。"""
import json
import sqlite3

import pytest


@pytest.fixture
def connection():
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.executescript('''
        CREATE TABLE project_canonical(project_id INTEGER, canonical_id INTEGER);
        INSERT INTO project_canonical VALUES (1,1),(2,2),(3,1);
        CREATE TABLE zips(id INTEGER PRIMARY KEY,path TEXT);
        INSERT INTO zips VALUES (11,'Z:/anonymous/bundle.zip');
        CREATE TABLE zip_project_links(zip_id INTEGER,project_id INTEGER);
        INSERT INTO zip_project_links VALUES (11,1),(11,2);
        CREATE TABLE zip_project_file_counts(zip_id INTEGER,project_id INTEGER,file_count INTEGER);
        INSERT INTO zip_project_file_counts VALUES (11,1,2),(11,2,7);
        CREATE TABLE zip_bundles(zip_id INTEGER,match_state TEXT);
        INSERT INTO zip_bundles VALUES (11,'matched');
        CREATE TABLE project_delivery_archives(
            id INTEGER PRIMARY KEY,project_id INTEGER,path TEXT,signature TEXT,manifest TEXT);
    ''')
    yield db
    db.close()


def event(event_id=51, project_id=1, **values):
    return dict(id=event_id, project_id=project_id, original_project_id=project_id,
                kind='auto-zip', zip_id=11, archive_path='Z:/anonymous/bundle.zip',
                archive_signature=None, **values)


def facts(connection, events):
    from archive_facts import delivery_archive_facts
    return delivery_archive_facts(connection, events)


def file_item(path='docs/a.txt'):
    return dict(path=path, size=0, sha256='a' * 64)


def material(connection, manifest, *, archive_id=31, project_id=1, path='Z:/anonymous/files', signature=None):
    from delivery_archive import _signature

    if signature is None:
        try:
            files = [item for item in manifest if not item['path'].endswith('/')]
            signature = _signature(files)
        except (TypeError, KeyError, AttributeError):
            signature = 'b' * 64
    connection.execute('INSERT INTO project_delivery_archives VALUES (?,?,?,?,?)',
                       (archive_id, project_id, path, signature, json.dumps(manifest)))
    return dict(id=61, project_id=1, original_project_id=3, kind='manual-files', zip_id=None,
                archive_path=path, archive_signature=signature)


def assert_unknown(fact):
    assert fact['known'] is False
    assert fact['count'] is None
    assert fact['reason']


def test_bundle_uses_each_projects_own_count_and_does_not_write(connection):
    connection.commit()
    connection.execute('PRAGMA query_only=ON')
    connection.execute('BEGIN')
    result = facts(connection, [event(), event(52, 2)])
    assert [result[key]['count'] for key in (51, 52)] == [2, 7]
    assert result[51] == dict(count=2, known=True, source_kind='zip', source_id=11,
                             path='Z:/anonymous/bundle.zip', reason='')
    assert connection.in_transaction


def test_merge_family_resolves_original_event_to_current_count(connection):
    old = event(project_id=3)
    assert facts(connection, [old])[51]['count'] == 2


@pytest.mark.parametrize('change', [
    'DELETE FROM zip_project_file_counts WHERE project_id=1',
    'DELETE FROM zip_project_links WHERE project_id=1',
    "UPDATE zip_bundles SET match_state='needs-review'",
    'DELETE FROM zips',
    'INSERT INTO zip_project_file_counts VALUES (11,3,9)',
    'UPDATE zip_project_file_counts SET file_count=-1 WHERE project_id=1',
])
def test_zip_missing_unlinked_review_or_conflicting_counts_remain_unknown(connection, change):
    connection.execute(change)
    assert_unknown(facts(connection, [event()])[51])


def test_verified_empty_zip_is_known_zero(connection):
    connection.execute('UPDATE zip_project_file_counts SET file_count=0 WHERE project_id=1')
    fact = facts(connection, [event()])[51]
    assert fact['known'] is True
    assert fact['count'] == 0


def test_missing_count_table_is_unknown_not_zero(connection):
    connection.execute('DROP TABLE zip_project_file_counts')
    assert_unknown(facts(connection, [event()])[51])


def test_material_matches_family_path_and_signature_and_counts_files(connection):
    row = material(connection, [file_item(), file_item('src/main.py'), {'path': 'empty/'}])
    result = facts(connection, [row])[61]
    assert result == dict(count=2, known=True, source_kind='files', source_id=31,
                          path='Z:/anonymous/files', reason='')


@pytest.mark.parametrize('manifest', [
    [file_item('../a.txt')], [file_item('/a.txt')], [file_item('a\\b.txt')],
    [file_item('C:/a.txt')], [file_item('a//b.txt')],
    [file_item('A.txt'), file_item('a.txt')],
    [file_item('a'), file_item('a/b')],
    [dict(path='a', size=-1, sha256='a' * 64)],
    [dict(path='a', size=0, sha256='wrong')],
    {'path': 'a'}, [None],
])
def test_invalid_material_manifest_remains_unknown(connection, manifest):
    row = material(connection, manifest)
    assert_unknown(facts(connection, [row])[61])


@pytest.mark.parametrize('change', [
    "UPDATE project_delivery_archives SET path='Z:/other'",
    "UPDATE project_delivery_archives SET signature='c'",
    'UPDATE project_delivery_archives SET project_id=2',
    "UPDATE project_delivery_archives SET manifest='broken'",
    'INSERT INTO project_delivery_archives SELECT 32,project_id,path,signature,manifest FROM project_delivery_archives',
])
def test_material_missing_or_ambiguous_exact_evidence_is_unknown(connection, change):
    row = material(connection, [file_item()])
    connection.execute(change)
    assert_unknown(facts(connection, [row])[61])


def test_material_does_not_take_another_version_when_event_evidence_missing(connection):
    row = material(connection, [file_item()])
    row['archive_signature'] = None
    assert_unknown(facts(connection, [row])[61])


def test_material_changed_manifest_with_original_signature_is_unknown(connection):
    row = material(connection, [file_item()])
    connection.execute('UPDATE project_delivery_archives SET manifest=?',
                       (json.dumps([file_item(), file_item('extra.txt')]),))
    assert_unknown(facts(connection, [row])[61])


def test_unknown_legacy_event_has_no_invented_count(connection):
    row = dict(id=71, project_id=1, kind='legacy', zip_id=None, archive_path='')
    result = facts(connection, [row])[71]
    assert_unknown(result)
    assert result['source_kind'] == 'unknown'


def test_event_project_disagreement_is_unknown(connection):
    row = event()
    row['original_project_id'] = 2
    assert_unknown(facts(connection, [row])[51])


def test_reads_real_database_delivery_history_without_archive_files(tmp_path):
    from database import Database
    from delivery_archive import SCHEMA

    db = Database(tmp_path / 'anonymous.db')
    db.create_capture('匿名项目', '匿名批次', [('匿名项目', False)])
    with db.connect() as connection:
        connection.executescript(SCHEMA)
        connection.execute('''INSERT INTO zips(id,project_id,path,filename,size,sha256,mtime_ns)
            VALUES (11,1,'Z:/anonymous/absent.zip','absent.zip',22,?,0)''', ('a' * 64,))
        connection.execute('''INSERT INTO zip_project_file_counts
            (zip_id,project_id,file_count,decided_status,rule)
            VALUES (11,1,4,'已交付','zip-file-count-v1')''')
        connection.execute('''INSERT INTO delivery_events
            (id,project_id,project_name,batch_name,kind,zip_id,archive_path)
            VALUES (51,1,'匿名项目','匿名批次','manual-zip',11,'Z:/anonymous/absent.zip')''')
    history = db.delivery_history()
    with db.connect() as connection:
        connection.execute('PRAGMA query_only=ON')
        connection.execute('BEGIN')
        fact = facts(connection, history['events'])[51]
        assert fact['known'] is True
        assert fact['count'] == 4
        assert connection.in_transaction
