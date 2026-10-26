import hashlib
import threading
import zipfile
from concurrent.futures import CancelledError, ThreadPoolExecutor

import pytest

from app import WorkBox


@pytest.fixture
def service(tmp_path):
    box = WorkBox(tmp_path / "workbox.db")
    box.capture_message("客户档案系统", "周二收件")
    return box


def make_zip(path, payload=b"fixture content"):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("readme.txt", payload)
    return path


def import_zip(service, path, **kwargs):
    assert hasattr(service, "import_zip"), "Task 3: ZIP import not implemented"
    return service.import_zip(path, **kwargs)


def test_import_auto_associates_but_never_marks_delivered(service, tmp_path):
    path = make_zip(tmp_path / "客户档案系统_v1.0.zip")
    original = path.read_bytes()
    result = import_zip(service, path)
    record = result["zip"]
    assert record["project_id"] == 1
    assert record["size"] == len(original)
    assert record["sha256"] == hashlib.sha256(original).hexdigest()
    assert service.get_project(1)["status"] == "待交付"
    assert service.get_project(1)["delivered_at"] is None
    assert path.read_bytes() == original


def test_unmatched_zip_does_not_invent_projects(service, tmp_path):
    result = import_zip(service, make_zip(tmp_path / "unrelated.zip"))
    assert result["zip"]["project_id"] is None
    assert service.db.counts() == {"projects": 1, "batches": 1, "zips": 1}
    assert len(service.list_zips(unmatched_only=True)) == 1


def test_ambiguous_match_requires_manual_project_choice(service, tmp_path):
    service.capture_message("客户档案系统", "周三收件")
    result = import_zip(service, make_zip(tmp_path / "客户档案系统.zip"))
    assert result["zip"]["project_id"] is None
    assert len(result["candidates"]) == 2
    service.link_zip(result["zip"]["id"], 2)
    assert service.get_project(1)["status"] == "待开发"
    assert service.get_project(2)["status"] == "待交付"
    assert service.list_zips(unmatched_only=True) == []


def test_duplicate_path_and_hash_keeps_one_record_and_association(service, tmp_path):
    path = make_zip(tmp_path / "客户档案系统.zip")
    first = import_zip(service, path)
    second = import_zip(service, path)
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert first["zip"]["id"] == second["zip"]["id"]
    assert service.db.counts()["zips"] == 1


def test_replaced_contents_at_same_path_become_a_new_metadata_record(service, tmp_path):
    path = make_zip(tmp_path / "客户档案系统.zip", b"version one")
    first = import_zip(service, path)
    make_zip(path, b"version two different")
    second = import_zip(service, path)
    assert first["zip"]["id"] != second["zip"]["id"]
    assert service.db.counts()["zips"] == 2
    with pytest.raises(ValueError, match="变化"):
        service.confirm_delivery(1, first["zip"]["id"], confirmed=True)
    assert service.get_project(1)["status"] == "待交付"


def test_delivery_requires_positive_human_confirmation(service, tmp_path):
    record = import_zip(service, make_zip(tmp_path / "客户档案系统.zip"))["zip"]
    with pytest.raises(ValueError, match="确认"):
        service.confirm_delivery(1, record["id"])
    assert service.get_project(1)["status"] == "待交付"


def test_confirmed_delivery_records_exact_zip_and_preserves_urgent_flag(service, tmp_path):
    service.set_urgent(1, True)
    path = make_zip(tmp_path / "客户档案系统.zip")
    original = path.read_bytes()
    record = import_zip(service, path)["zip"]
    service.confirm_delivery(1, record["id"], confirmed=True)
    project = WorkBox(service.db.path).get_project(1)
    assert project["status"] == "已交付"
    assert project["delivered_zip_id"] == record["id"]
    assert project["delivered_at"]
    assert project["urgent"] == 1
    assert path.read_bytes() == original


def test_moved_file_cannot_be_confirmed_and_no_replacement_is_created(service, tmp_path):
    path = make_zip(tmp_path / "客户档案系统.zip")
    record = import_zip(service, path)["zip"]
    moved = path.rename(tmp_path / "externally-moved.zip")
    with pytest.raises(FileNotFoundError):
        service.confirm_delivery(1, record["id"], confirmed=True)
    assert not path.exists()
    assert moved.exists()
    assert service.get_project(1)["status"] == "待交付"


def test_wrong_project_or_wrong_state_cannot_be_confirmed(service, tmp_path):
    record = import_zip(service, make_zip(tmp_path / "客户档案系统.zip"))["zip"]
    service.capture_message("其他项目", "周二收件")
    service.set_status(2, "待交付")
    with pytest.raises(ValueError):
        service.confirm_delivery(2, record["id"], confirmed=True)
    service.set_status(1, "开发中")
    with pytest.raises(ValueError):
        service.confirm_delivery(1, record["id"], confirmed=True)


def test_new_zip_does_not_rewrite_an_existing_delivery_confirmation(service, tmp_path):
    first = import_zip(service, make_zip(tmp_path / "客户档案系统_v1.0.zip"))["zip"]
    service.confirm_delivery(1, first["id"], confirmed=True)
    import_zip(service, make_zip(tmp_path / "客户档案系统_v2.0.zip", b"new content"))
    project = service.get_project(1)
    assert project["status"] == "已交付"
    assert project["delivered_zip_id"] == first["id"]
    assert len(project["zips"]) == 2


def test_confirmed_zip_cannot_be_reassigned_to_another_project(service, tmp_path):
    record = import_zip(service, make_zip(tmp_path / "客户档案系统.zip"))["zip"]
    service.confirm_delivery(1, record["id"], confirmed=True)
    service.capture_message("其他项目", "周二收件")
    with pytest.raises(ValueError):
        service.link_zip(record["id"], 2)
    assert service.list_zips()[0]["project_id"] == 1


def test_cancellation_cannot_register_half_a_zip(service, tmp_path):
    event = threading.Event()
    event.set()
    path = make_zip(tmp_path / "客户档案系统.zip")
    with pytest.raises(CancelledError):
        import_zip(service, path, cancel_event=event)
    assert service.db.counts()["zips"] == 0
    assert service.get_project(1)["status"] == "待开发"


def test_parallel_duplicate_imports_are_atomic(service, tmp_path):
    path = make_zip(tmp_path / "客户档案系统.zip")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: import_zip(service, path), range(2)))
    assert sum(not result["duplicate"] for result in results) == 1
    assert service.db.counts()["zips"] == 1


def test_status_is_rechecked_after_background_delivery_hashing(service, tmp_path):
    path = make_zip(tmp_path / "客户档案系统.zip")
    record = import_zip(service, path)["zip"]

    def change_state(done, total):
        service.set_status(1, "开发中")

    with pytest.raises(ValueError):
        service.confirm_delivery(1, record["id"], confirmed=True, progress=change_state)
    assert service.get_project(1)["status"] == "开发中"
