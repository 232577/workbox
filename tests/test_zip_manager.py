import hashlib
import importlib
import importlib.util
import threading
import zipfile
from concurrent.futures import CancelledError
from pathlib import Path

import pytest


def manager():
    assert importlib.util.find_spec("zip_manager") is not None, "Task 3: ZIP manager missing"
    return importlib.import_module("zip_manager")


def make_zip(path, content=b"isolated fixture; never execute"):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("nested/fixture.txt", content)
    return path


def test_metadata_reads_original_file_without_extracting_or_modifying(tmp_path):
    path = make_zip(tmp_path / "客户档案系统.zip")
    before = path.read_bytes()
    stat = path.stat()
    info = manager().inspect_zip(path)
    assert Path(info.path) == path.resolve()
    assert info.filename == path.name
    assert info.size == len(before)
    assert info.sha256 == hashlib.sha256(before).hexdigest()
    assert info.mtime_ns == stat.st_mtime_ns
    assert path.read_bytes() == before
    assert path.stat().st_mtime_ns == stat.st_mtime_ns
    assert list(tmp_path.iterdir()) == [path]


def test_empty_zip_is_readable_without_extracting(tmp_path):
    path = tmp_path / "empty.ZIP"
    with zipfile.ZipFile(path, "w"):
        pass
    assert manager().inspect_zip(path).size == 22


@pytest.mark.parametrize("filename,payload", [("notzip.txt", b"PK\x03\x04" * 8), ("fake.zip", b"not a ZIP"), ("zero.zip", b"")])
def test_invalid_inputs_are_rejected(tmp_path, filename, payload):
    path = tmp_path / filename
    path.write_bytes(payload)
    with pytest.raises(ValueError):
        manager().inspect_zip(path)
    assert path.read_bytes() == payload


def test_missing_file_and_directory_are_rejected(tmp_path):
    module = manager()
    with pytest.raises(FileNotFoundError):
        module.inspect_zip(tmp_path / "missing.zip")
    folder = tmp_path / "folder.zip"
    folder.mkdir()
    with pytest.raises(ValueError):
        module.inspect_zip(folder)


def test_hashing_can_be_cancelled_without_touching_zip(tmp_path):
    path = make_zip(tmp_path / "cancel.zip")
    before = path.read_bytes()
    event = threading.Event()
    event.set()
    with pytest.raises(CancelledError):
        manager().inspect_zip(path, cancel_event=event)
    assert path.read_bytes() == before


def test_file_change_during_hashing_is_detected(tmp_path):
    path = make_zip(tmp_path / "changing.zip", b"x" * (3 * 1024 * 1024))
    changed = []

    def change_fixture(done, total):
        if not changed:
            with path.open("ab") as stream:
                stream.write(b"external fixture change")
            changed.append(True)

    with pytest.raises(RuntimeError, match="读取期间"):
        manager().inspect_zip(path, progress=change_fixture)


@pytest.mark.parametrize("filename", [
    "客户档案系统.zip", "客户 档案 系统_v1.0_交付_20260922.zip", "客户档案系统-1.0.0-final.ZIP"
])
def test_name_and_recognized_delivery_suffixes_match(filename):
    projects = [{"id": 1, "name": "客户档案系统", "batch_name": "周二"}, {"id": 2, "name": "合同工具", "batch_name": "周三"}]
    assert [p["id"] for p in manager().match_projects(filename, projects)] == [1]


def test_same_name_in_different_batches_is_ambiguous():
    projects = [{"id": 1, "name": "同名项目", "batch_name": "第一批"}, {"id": 2, "name": "同名项目", "batch_name": "第二批"}]
    assert {p["id"] for p in manager().match_projects("同名项目.zip", projects)} == {1, 2}
    assert [p["id"] for p in manager().match_projects("第二批_同名项目_v1.0.zip", projects)] == [2]


def test_explicit_project_code_is_precise_and_unknown_code_does_not_fallback():
    projects = [{"id": 1, "name": "项目甲", "batch_name": "批次"}, {"id": 2, "name": "项目乙", "batch_name": "批次"}]
    assert [p["id"] for p in manager().match_projects("[WB-000002]_交付.zip", projects)] == [2]
    assert manager().match_projects("WB-009999_项目甲.zip", projects) == []


def test_partial_names_and_added_project_numbers_do_not_auto_match():
    projects = [{"id": 1, "name": "客户档案系统", "batch_name": "周二"}]
    assert manager().match_projects("客户.zip", projects) == []
    assert manager().match_projects("客户档案系统2.zip", projects) == []
    assert manager().match_projects("客互档案系统.zip", projects) == []
