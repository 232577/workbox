from datetime import date
import sqlite3
import zipfile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from app import WorkBox
from desktop_service import DesktopService


@pytest.fixture
def box(tmp_path):
    value = WorkBox(tmp_path / 'workbox.db')
    value.capture_message('1. 甲系统\n2. 乙系统\n3. 丙系统\n4. 丁系统', '管理批次')
    return value


def test_details_preserve_original_text_and_restart(box):
    box.update_project_details(1, '  原样备注\n100% _  ', '2026-10-22')
    row = WorkBox(box.db.path).get_project(1)
    assert row['notes'] == '  原样备注\n100% _  '
    assert row['due_date'] == '2026-10-22'
    assert row['archived_at'] is None
    assert row['raw_text'] == '1. 甲系统\n2. 乙系统\n3. 丙系统\n4. 丁系统'
    assert [p['id'] for p in box.list_projects(query='100% _')] == [1]
    assert box.list_projects(query="' OR 1=1 --") == []
    box.update_project_details(1, '', '')
    assert box.get_project(1)['due_date'] is None


@pytest.mark.parametrize('value', ['2026-2-01', '2026-02-29', '2026-13-01', ' 2026-10-22', 20261022, True])
def test_invalid_dates_are_atomic(box, value):
    with pytest.raises(ValueError):
        box.update_project_details(1, '不得保存', value)
    assert box.get_project(1)['notes'] == ''


def test_notes_validation(box):
    for notes in (None, 123, '字' * 10001):
        with pytest.raises(ValueError):
            box.update_project_details(1, notes, None)
    box.update_project_details(1, '字' * 10000, '2024-02-29')
    with pytest.raises(KeyError):
        box.update_project_details(999, '', None)


def test_due_filters_and_sorting(box):
    for pid, due in ((1, '2026-10-21'), (2, '2026-10-22'), (3, '2026-10-28')):
        box.update_project_details(pid, '', due)
    today = date(2026, 10, 22)
    assert [p['id'] for p in box.list_projects()] == [1, 2, 3, 4]
    box.set_urgent(4, True)
    assert [p['id'] for p in box.list_projects()] == [4, 1, 2, 3]
    expected = {'overdue': [1], 'today': [2], 'week': [2, 3], 'unscheduled': [4]}
    for key, ids in expected.items():
        assert [p['id'] for p in box.list_projects(due_filter=key, today=today)] == ids
    with box.db.connect() as connection:
        connection.execute("UPDATE projects SET status='已交付' WHERE id=2")
    assert box.list_projects(due_filter='today', today=today) == []
    with pytest.raises(ValueError):
        box.list_projects(due_filter='month')


def test_combined_filters_ties_and_delivered_exclusion(box):
    for pid in (1, 2, 3):
        box.update_project_details(pid, '共同备注', '2026-10-22')
    box.bulk_update_projects([1, 2], status='开发中', urgent=True)
    assert [p['id'] for p in box.list_projects(query='共同备注', status='开发中', batch_id=1,
                                             urgent_only=True, due_filter='today', today=date(2026, 10, 22))] == [2, 1]
    box.bulk_update_projects([2], archived=True)
    assert [p['id'] for p in box.list_projects(archived=True, due_filter='today', today=date(2026, 10, 22))] == [2]
    with box.db.connect() as connection:
        connection.execute("UPDATE projects SET status='已交付' WHERE id IN (1,3,4)")
    for due_filter in ('overdue', 'today', 'week', 'unscheduled'):
        assert box.list_projects(due_filter=due_filter, today=date(2026, 10, 22)) == []
    with pytest.raises(ValueError):
        box.list_projects(archived=1)
    with pytest.raises(ValueError):
        box.list_projects(today='2026-10-22')


def test_bulk_updates_and_archive_restore_are_atomic(box):
    box.bulk_update_projects([1, 2], status='开发中', urgent=True)
    with pytest.raises(KeyError):
        box.bulk_update_projects([1, 999], status='待交付', urgent=False)
    assert box.get_project(1)['status'] == '开发中'
    assert box.get_project(1)['urgent'] == 1
    box.bulk_update_projects([1, 2], archived=True)
    original_archive_time = box.get_project(1)['archived_at']
    with pytest.raises(KeyError):
        box.bulk_update_projects([1, 999], archived=False)
    assert box.get_project(1)['archived_at'] == original_archive_time
    assert {p['id'] for p in box.list_projects()} == {3, 4}
    assert {p['id'] for p in box.list_projects(archived=True)} == {1, 2}
    assert len(box.list_projects(archived=None)) == 4
    assert WorkBox(box.db.path).get_project(1)['archived_at']
    for action in (lambda: box.set_status(1, '待开发'), lambda: box.set_urgent(1, False),
                   lambda: box.update_project_details(1, '修改', None),
                   lambda: box.bulk_update_projects([1, 3], urgent=True)):
        with pytest.raises(ValueError):
            action()
    assert box.get_project(3)['urgent'] == 0
    box.bulk_update_projects([1, 2], archived=False)
    assert box.get_project(1)['archived_at'] is None
    assert box.get_project(1)['status'] == '开发中'


@pytest.mark.parametrize('ids,kwargs', [([], {'urgent': True}), ([1, 1], {'urgent': True}),
    ([True], {'urgent': True}), ([0], {'urgent': True}), ([1.0], {'urgent': True}),
    (list(range(1, 502)), {'urgent': True}), ([1], {'status': '已交付'}),
    ([1], {'urgent': 1}), ([1], {'archived': 1}), ([1], {'archived': True, 'status': '开发中'}),
    ([1], {})])
def test_bulk_rejects_invalid_requests(box, ids, kwargs):
    with pytest.raises(ValueError):
        box.bulk_update_projects(ids, **kwargs)
    assert box.get_project(1)['status'] == '待开发'


def test_legacy_database_extension_keeps_delivery_and_zip(tmp_path):
    path = tmp_path / 'legacy.db'
    old = WorkBox(path)
    old.capture_message('甲系统', '原批次')
    package = tmp_path / '甲系统.zip'
    with zipfile.ZipFile(package, 'w') as archive:
        archive.writestr('正文.txt', '原始成品')
    content = package.read_bytes()
    record = old.import_zip(package)['zip']
    old.confirm_delivery(1, record['id'], confirmed=True)
    original = old.get_project(1)
    with sqlite3.connect(path) as connection:
        connection.execute('DROP TABLE IF EXISTS project_details')
    migrated = WorkBox(path)
    migrated.bulk_update_projects([1], archived=True)
    migrated.bulk_update_projects([1], archived=False)
    current = WorkBox(path).get_project(1)
    for key in ('raw_text', 'status', 'delivered_at', 'delivered_zip_id', 'zips'):
        assert current[key] == original[key]
    assert package.read_bytes() == content
    with sqlite3.connect(path) as connection:
        assert connection.execute('PRAGMA user_version').fetchone()[0] == 1
        assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'


@pytest.mark.parametrize('service_class', [WorkBox, DesktopService])
def test_archived_zip_relink_refused_before_move_and_restored_after_recovery(tmp_path, service_class):
    box = service_class(tmp_path / 'data' / 'box.db')
    box.capture_message('1. 甲系统\n2. 乙系统', '批次')
    package = tmp_path / '甲系统.zip'
    with zipfile.ZipFile(package, 'w') as archive:
        archive.writestr('结果.txt', '成品内容')
    record = box.import_zip(package)['zip']
    location = Path(record['path'])
    content = location.read_bytes()
    box.bulk_update_projects([1], archived=True)
    for link in (box.link_zip, box.db.link_zip):
        with pytest.raises(ValueError):
            link(record['id'], 2)
        assert box.db.get_zip(record['id'])['project_id'] == 1
        assert box.db.get_zip(record['id'])['path'] == str(location)
        assert location.read_bytes() == content
    with pytest.raises(ValueError):
        box.confirm_delivery(1, record['id'], confirmed=True)
    with pytest.raises(ValueError):
        box.db.complete_delivery(1, record['id'], record)
    box.bulk_update_projects([1], archived=False)
    box.bulk_update_projects([2], archived=True)
    with pytest.raises(ValueError):
        box.link_zip(record['id'], 2)
    assert location.read_bytes() == content
    box.bulk_update_projects([2], archived=False)
    box.link_zip(record['id'], 2)
    assert box.db.get_zip(record['id'])['project_id'] == 2


def test_archived_rename_and_explicit_archive_target_reject_without_moving(tmp_path):
    box = DesktopService(tmp_path / 'data' / 'box.db')
    box.capture_message('甲系统', '批次')
    box.bulk_update_projects([1], archived=True)
    with pytest.raises(ValueError):
        box.rename_project(1, '新名称')
    package = tmp_path / '交付.zip'
    with zipfile.ZipFile(package, 'w') as archive:
        archive.writestr('结果.txt', '成品内容')
    content = package.read_bytes()
    with pytest.raises(ValueError):
        box.archive.import_zip(package, project_id=1)
    assert package.read_bytes() == content
    assert box.list_zips() == []
    box.bulk_update_projects([1], archived=False)
    box.rename_project(1, '新名称')
    assert box.get_project(1)['name'] == '新名称'


def test_archived_legacy_reference_cannot_be_moved_by_legacy_managed_path(tmp_path):
    path = tmp_path / 'data' / 'box.db'
    legacy = WorkBox(path)
    legacy.capture_message('甲系统', '批次')
    package = tmp_path / '甲系统.zip'
    with zipfile.ZipFile(package, 'w') as archive:
        archive.writestr('结果.txt', '历史成品')
    record = legacy.import_zip(package)['zip']
    legacy.bulk_update_projects([1], archived=True)
    content = package.read_bytes()
    desktop = DesktopService(path)
    with pytest.raises(ValueError):
        desktop.archive.import_zip(package)
    assert package.read_bytes() == content
    assert desktop.db.get_zip(record['id'])['path'] == str(package)
    assert desktop.db.get_zip(record['id'])['project_id'] == 1


def test_archive_waits_for_in_progress_managed_move(tmp_path, monkeypatch):
    box = DesktopService(tmp_path / 'data' / 'box.db')
    box.capture_message('甲系统', '批次')
    package = tmp_path / '甲系统.zip'
    with zipfile.ZipFile(package, 'w') as archive:
        archive.writestr('结果.txt', '成品')
    entered, resume, archive_started = Event(), Event(), Event()
    original = box.archive._target
    def paused_target(info, project_id):
        entered.set()
        assert resume.wait(5)
        return original(info, project_id)
    def archive_project():
        archive_started.set()
        box.bulk_update_projects([1], archived=True)
    monkeypatch.setattr(box.archive, '_target', paused_target)
    with ThreadPoolExecutor(max_workers=2) as pool:
        moving = pool.submit(box.archive.import_zip, package)
        assert entered.wait(5)
        archiving = pool.submit(archive_project)
        try:
            assert archive_started.wait(5)
            assert not archiving.done()
            assert box.get_project(1)['archived_at'] is None
        finally:
            resume.set()
        result = moving.result(timeout=5)
        archiving.result(timeout=5)
    assert box.get_project(1)['archived_at']
    assert Path(result['zip']['path']).is_file()
    assert not package.exists()
