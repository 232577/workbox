from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import ANY

import pytest

from database import Database
from intake_log import FileIntakeLog


@pytest.fixture
def isolated_dir():
    root = Path(__file__).parents[1] / ".local" / "intake-log-20261023"
    root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=root) as directory:
        yield Path(directory)


@pytest.fixture
def log(isolated_dir):
    return FileIntakeLog(Database(isolated_dir / "workbox.db"))


def _create_project(db, name="甲项目"):
    return db.create_capture(name, f"{name}批次", [(name, False)])["project_ids"][0]


def _create_legacy_table(connection):
    connection.execute(
        """
        CREATE TABLE work_files (
            id INTEGER PRIMARY KEY,
            project_id INTEGER NOT NULL,
            source_path TEXT NOT NULL,
            path TEXT NOT NULL,
            filename TEXT NOT NULL,
            size INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )


@pytest.mark.parametrize(
    ("status", "manual"),
    [
        ("copied", False),
        ("duplicate", True),
        ("failed", None),
        ("cancelled", None),
        ("needs-choice", None),
    ],
)
def test_append_and_list_preserve_each_intake_outcome(log, status, manual):
    event_id = log.append(
        r"D:\收件\需求说明.docx",
        status,
        project_id=12,
        project_name="收录时项目名",
        target_path=r"D:\工作项目\需求说明.docx",
        basis="文件名精确匹配",
        candidate_ids=(12, 15),
        manual=manual,
        message="已保留源文件",
        sha256="a" * 64,
        work_file_id=37,
    )

    assert event_id > 0
    assert log.list_recent() == [
        {
            "id": f"event:{event_id}",
            "created_at": ANY,
            "status": status,
            "filename": "需求说明.docx",
            "source_path": r"D:\收件\需求说明.docx",
            "target_path": r"D:\工作项目\需求说明.docx",
            "project_id": 12,
            "project_name": "收录时项目名",
            "basis": "文件名精确匹配",
            "candidate_ids": [12, 15],
            "manual": manual,
            "message": "已保留源文件",
            "sha256": "a" * 64,
            "work_file_id": 37,
        }
    ]


def test_invalid_status_is_rejected_without_writing(log):
    with pytest.raises(ValueError):
        log.append(r"D:\收件\文件.txt", "success")
    assert log.list_recent() == []


def test_caller_connection_controls_commit_and_rollback(log):
    with log.db.connect() as connection:
        _create_legacy_table(connection)

    with log.db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        work_file_id = connection.execute(
            """INSERT INTO work_files(
                project_id,source_path,path,filename,size,sha256,created_at
            ) VALUES (?,?,?,?,?,?,?)""",
            (
                1,
                r"D:\收件\回滚.txt",
                r"D:\工作项目\回滚.txt",
                "回滚.txt",
                1,
                "c" * 64,
                "2026-10-23T00:00:00.000Z",
            ),
        ).lastrowid
        rolled_back_id = log.append(
            r"D:\收件\回滚.txt",
            "copied",
            work_file_id=work_file_id,
            connection=connection,
        )
        assert rolled_back_id > 0
        connection.rollback()
    assert log.list_recent() == []
    with log.db.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM work_files").fetchone()[0] == 0

    with log.db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        committed_id = log.append(
            r"D:\收件\提交.txt", "duplicate", connection=connection
        )
    assert [row["id"] for row in log.list_recent()] == [f"event:{committed_id}"]


def test_legacy_rows_are_combined_without_inventing_old_decisions(isolated_dir):
    db = Database(isolated_dir / "legacy.db")
    project_id = _create_project(db, "旧项目现名")
    with db.connect() as connection:
        _create_legacy_table(connection)
        legacy_id = connection.execute(
            """INSERT INTO work_files(
                project_id,source_path,path,filename,size,sha256,created_at
            ) VALUES (?,?,?,?,?,?,?)""",
            (
                project_id,
                r"D:\旧收件\旧稿.txt",
                r"D:\工作项目\旧稿.txt",
                "旧稿.txt",
                3,
                "b" * 64,
                "2026-10-22T08:00:00.000Z",
            ),
        ).lastrowid

    intake = FileIntakeLog(db)
    rows = intake.list_recent()
    assert rows == [
        {
            "id": f"legacy:{legacy_id}",
            "created_at": "2026-10-22T08:00:00.000Z",
            "status": "historical",
            "filename": "旧稿.txt",
            "source_path": r"D:\旧收件\旧稿.txt",
            "target_path": r"D:\工作项目\旧稿.txt",
            "project_id": project_id,
            "project_name": "旧项目现名",
            "basis": "旧记录未记录判断依据",
            "candidate_ids": [],
            "manual": None,
            "message": "",
            "sha256": "b" * 64,
            "work_file_id": legacy_id,
        }
    ]

    intake.append(
        r"D:\旧收件\旧稿.txt",
        "copied",
        project_id=project_id,
        project_name="收录时旧项目名",
        target_path=r"D:\工作项目\旧稿.txt",
        work_file_id=legacy_id,
    )
    combined = intake.list_recent()
    assert [row["status"] for row in combined] == ["copied"]
    assert combined[0]["project_name"] == "收录时旧项目名"


def test_literal_percent_search_limit_and_restart(isolated_dir):
    db = Database(isolated_dir / "restart.db")
    intake = FileIntakeLog(db)
    percent_id = intake.append(
        r"D:\收件\完成%稿.txt",
        "failed",
        project_name="百分比项目",
        basis="路径含百分号",
    )
    intake.append(r"D:\收件\普通.txt", "cancelled", project_name="普通项目")

    restarted = FileIntakeLog(Database(db.path))
    assert [row["id"] for row in restarted.list_recent("%", limit=1)] == [
        f"event:{percent_id}"
    ]
    with pytest.raises(ValueError):
        restarted.list_recent(limit=0)
    with pytest.raises(ValueError):
        restarted.list_recent(limit=501)


def test_list_recent_works_when_legacy_table_is_absent(log):
    event_id = log.append(r"D:\收件\仅新日志.txt", "needs-choice")
    assert [row["id"] for row in log.list_recent()] == [f"event:{event_id}"]

