import importlib
import importlib.util
import sqlite3

import pytest


@pytest.fixture
def service(tmp_path):
    assert importlib.util.find_spec("app") is not None, "Task 1: app.py missing"
    module = importlib.import_module("app")
    assert hasattr(module, "WorkBox"), "Task 1: WorkBox service missing"
    return module.WorkBox(tmp_path / "workbox.db")


def rows(service, table):
    with service.db.connect() as connection:
        return [dict(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY id")]


def test_capture_preserves_verbatim_text_batch_and_split_projects(service):
    original = "  1. 客户工具\r\n2. 交付管理工具（加急）  "
    result = service.capture_message(original, "周二收件")
    assert len(result["project_ids"]) == 2
    assert service.db.counts() == {"projects": 2, "batches": 1, "zips": 0}
    assert rows(service, "messages")[0]["raw_text"] == original
    assert rows(service, "batches")[0]["name"] == "周二收件"
    assert [(p["name"], p["urgent"], p["status"]) for p in rows(service, "projects")] == [
        ("客户工具", 0, "待开发"), ("交付管理工具", 1, "待开发")
    ]


def test_same_batch_can_receive_more_messages_without_extra_batches(service):
    service.capture_message("项目甲", "批次A")
    service.capture_message("项目乙", "批次A")
    assert service.db.counts() == {"projects": 2, "batches": 1, "zips": 0}
    assert len(rows(service, "messages")) == 2


def test_duplicate_click_is_rejected_but_new_batch_is_independent(service):
    service.capture_message("项目甲", "批次A")
    with pytest.raises(ValueError, match="已收录"):
        service.capture_message("项目甲", "批次A")
    service.capture_message("项目甲", "批次B")
    assert service.db.counts() == {"projects": 2, "batches": 2, "zips": 0}


def test_user_edited_preview_is_what_gets_saved(service):
    ParsedProject = importlib.import_module("parser").ParsedProject
    service.capture_message("一段未规整的文字", "手工收件", [ParsedProject("修正后的项目名", True)])
    project = rows(service, "projects")[0]
    assert project["name"] == "修正后的项目名"
    assert project["urgent"] == 1
    assert rows(service, "messages")[0]["raw_text"] == "一段未规整的文字"


@pytest.mark.parametrize("message,batch", [("", "批次"), (" \n", "批次"), ("项目", " ")])
def test_invalid_capture_cannot_leave_partial_records(service, message, batch):
    with pytest.raises(ValueError):
        service.capture_message(message, batch)
    assert service.db.counts() == {"projects": 0, "batches": 0, "zips": 0}
    assert rows(service, "messages") == []


def test_empty_preview_is_not_saved(service):
    with pytest.raises(ValueError):
        service.capture_message("原始项目消息", "批次", [])
    assert service.db.counts()["batches"] == 0


def test_sql_failure_rolls_back_entire_capture(service):
    with service.db.connect() as connection:
        connection.executescript("""
            CREATE TRIGGER test_reject BEFORE INSERT ON projects
            WHEN NEW.name='拒绝' BEGIN SELECT RAISE(ABORT, 'fixture rejection'); END;
        """)
    with pytest.raises(sqlite3.IntegrityError):
        service.capture_message("1. 正常\n2. 拒绝", "批次")
    assert service.db.counts() == {"projects": 0, "batches": 0, "zips": 0}
    assert rows(service, "messages") == []


def test_captured_data_survives_restart(service):
    service.capture_message("1. 项目甲\n2. 项目乙", "批次")
    reopened = importlib.import_module("app").WorkBox(service.db.path)
    assert reopened.db.counts() == {"projects": 2, "batches": 1, "zips": 0}
    assert rows(reopened, "messages")[0]["raw_text"] == "1. 项目甲\n2. 项目乙"
