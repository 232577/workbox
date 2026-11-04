"""交付历史与导出附加数据必须读取同一个调用方快照。"""
import pytest

from database import Database


@pytest.fixture
def db(tmp_path):
    result = Database(tmp_path / 'snapshot.db')
    result.create_capture('匿名报表系统', '匿名批次', [('匿名报表系统', False)])
    return result


def append_event(connection):
    return connection.execute('''INSERT INTO delivery_events(
        project_id,project_name,batch_name,kind,archive_path,occurred_at)
        VALUES (1,'匿名报表系统','匿名批次','manual-files','匿名归档','2026-11-04T01:00:00Z')''').lastrowid


def test_history_uses_owned_snapshot_across_concurrent_type_change(db):
    with db.connect() as connection:
        append_event(connection)
    original = db.delivery_history()['events'][0]['project_type_name']
    changed = db.create_project_type('匿名新分类')['id']
    with db.connect() as connection:
        connection.execute('BEGIN')
        connection.execute('SELECT count(*) FROM projects').fetchone()
        db.set_project_types([1], changed)
        history = db.delivery_history(connection=connection)
        assert history['events'][0]['project_type_name'] == original
        assert connection.in_transaction
        assert connection.execute('SELECT count(*) FROM projects').fetchone()[0] == 1
    assert db.delivery_history()['events'][0]['project_type_name'] == '匿名新分类'


def test_history_does_not_commit_or_close_caller_transaction(db):
    with db.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        event_id = append_event(connection)
        assert db.delivery_history(connection=connection)['events'][0]['id'] == event_id
        assert connection.in_transaction
        connection.rollback()
    assert db.delivery_history()['events'] == []


def test_history_rejects_unstarted_or_different_database_snapshot(db, tmp_path):
    with db.connect() as connection:
        with pytest.raises(ValueError, match='事务'):
            db.delivery_history(connection=connection)
    other = Database(tmp_path / 'other.db')
    with other.connect() as connection:
        connection.execute('BEGIN')
        with pytest.raises(ValueError, match='数据库'):
            db.delivery_history(connection=connection)
