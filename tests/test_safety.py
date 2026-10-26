import ast
from pathlib import Path
import sqlite3

import pytest

from database import Database


ROOT = Path(__file__).resolve().parents[1]


def test_production_has_no_archive_extraction_execution_or_file_destruction():
    forbidden_calls = {"extract", "extractall", "unpack_archive", "rmtree", "unlink", "remove", "rename", "startfile", "system", "Popen", "move"}
    for name in ("zip_manager.py", "app.py", "database.py", "ui.py"):
        tree = ast.parse((ROOT / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in forbidden_calls, f"Unexpected forbidden call in {name}: {node.func.attr}"


def test_future_database_version_is_rejected_without_resetting_existing_data(tmp_path):
    path = tmp_path / "future.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE customer_data(value TEXT)")
        connection.execute("INSERT INTO customer_data VALUES ('keep me')")
        connection.execute("PRAGMA user_version=8")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="版本"):
        Database(path)
    assert path.read_bytes() == before


def test_corrupt_database_is_not_silently_replaced(tmp_path):
    path = tmp_path / "damaged.db"
    content = b"This is not SQLite. It must never be overwritten."
    path.write_bytes(content)
    with pytest.raises(sqlite3.DatabaseError):
        Database(path)
    assert path.read_bytes() == content
