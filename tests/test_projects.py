import sqlite3

import pytest

from app import WorkBox
from database import STATUSES


@pytest.fixture
def service(tmp_path):
    box = WorkBox(tmp_path / "workbox.db")
    box.capture_message("1. WorkBox 工具\n2. 客户档案系统", "周二批次")
    box.capture_message("1. 合同归档工具\n2. 完成率100%工具", "周三批次")
    return box


def test_four_states_are_exact_and_new_projects_are_pending(service):
    assert STATUSES == ("待开发", "开发中", "待交付", "已交付")
    assert {p["status"] for p in service.list_projects()} == {"待开发"}


@pytest.mark.parametrize("status", ["待开发", "开发中", "待交付"])
def test_editable_development_states_persist(service, status):
    service.set_status(1, status)
    assert WorkBox(service.db.path).get_project(1)["status"] == status


@pytest.mark.parametrize("status", ["已交付", "已完成", "待审批", ""])
def test_delivery_cannot_be_set_by_generic_status_update(service, status):
    with pytest.raises(ValueError):
        service.set_status(1, status)
    assert service.get_project(1)["status"] == "待开发"


def test_urgent_flag_never_replaces_or_changes_development_state(service):
    service.set_status(1, "开发中")
    service.set_urgent(1, True)
    assert service.get_project(1)["status"] == "开发中"
    assert service.get_project(1)["urgent"] == 1
    service.set_urgent(1, False)
    assert service.get_project(1)["status"] == "开发中"
    assert service.get_project(1)["urgent"] == 0


def test_search_matches_project_name_case_insensitively_and_batch(service):
    assert [p["id"] for p in service.list_projects(query="workbox")] == [1]
    assert {p["id"] for p in service.list_projects(query="周三批次")} == {3, 4}


def test_zip_name_is_searchable_without_duplicate_project_rows(service):
    with service.db.connect() as connection:
        for index in range(2):
            connection.execute(
                "INSERT INTO zips(project_id,path,filename,size,sha256,mtime_ns) VALUES (?,?,?,?,?,?)",
                (1, f"D:/fixture/special_{index}.zip", f"special_delivery_{index}.zip", 22, str(index) * 64, 1),
            )
    assert [p["id"] for p in service.list_projects(query="SPECIAL_DELIVERY")] == [1]
    assert service.list_projects(query="SPECIAL_DELIVERY")[0]["zip_count"] == 2


def test_search_treats_percent_and_sql_as_literal_text(service):
    assert [p["id"] for p in service.list_projects(query="100%")] == [4]
    assert service.list_projects(query="' OR 1=1 --") == []


def test_status_batch_and_urgent_filters_combine(service):
    service.set_urgent(1, True)
    service.set_urgent(3, True)
    service.set_status(1, "开发中")
    assert [p["id"] for p in service.list_projects(status="开发中", batch_id=1, urgent_only=True)] == [1]
    assert service.list_projects(status="开发中", batch_id=2, urgent_only=True) == []


def test_batch_counts_and_original_message_details(service):
    assert {batch["name"]: batch["project_count"] for batch in service.list_batches()} == {
        "周二批次": 2, "周三批次": 2
    }
    project = service.get_project(1)
    assert project["raw_text"] == "1. WorkBox 工具\n2. 客户档案系统"
    assert project["batch_name"] == "周二批次"
    assert project["zips"] == []


def test_unknown_project_cannot_be_updated_silently(service):
    with pytest.raises(KeyError):
        service.set_status(999, "开发中")
    with pytest.raises(KeyError):
        service.set_urgent(999, True)
    with pytest.raises(KeyError):
        service.get_project(999)


def test_database_rejects_a_fifth_state(service):
    with pytest.raises(sqlite3.IntegrityError):
        with service.db.connect() as connection:
            connection.execute("UPDATE projects SET status='第五状态' WHERE id=1")
