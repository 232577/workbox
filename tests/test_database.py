import importlib
import importlib.util
import sqlite3


def make_database(path):
    assert importlib.util.find_spec("database") is not None, "Task 0: database.py 尚未实现"
    module = importlib.import_module("database")
    assert hasattr(module, "Database"), "Task 0: Database 尚未实现"
    return module.Database(path)


def test_first_start_has_zero_projects_batches_and_zips(tmp_path):
    db = make_database(tmp_path / "data" / "workbox.db")
    assert db.counts() == {"projects": 0, "batches": 0, "zips": 0}


def test_repeated_initialization_does_not_seed_any_records(tmp_path):
    path = tmp_path / "workbox.db"
    make_database(path)
    db = make_database(path)
    assert db.counts() == {"projects": 0, "batches": 0, "zips": 0}
    with sqlite3.connect(path) as connection:
        for table in ("messages", "batches", "projects", "zips"):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_database_is_real_sqlite_with_schema_version_one(tmp_path):
    path = tmp_path / "workbox.db"
    make_database(path)
    assert path.read_bytes().startswith(b"SQLite format 3\x00")
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
