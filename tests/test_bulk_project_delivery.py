"""Batch delivery records verified work files for the selected projects atomically."""

import errno
import os
import threading
from concurrent.futures import CancelledError
from pathlib import Path

import pytest

from workbox.desktop.desktop_service import DesktopService


@pytest.fixture
def projects(tmp_path):
    service = DesktopService(tmp_path / 'data' / 'anonymous.db')
    names = ['匿名甲项目', '匿名乙项目', '匿名丙项目']
    ids = service.db.create_capture('\n'.join(names), '匿名批次', [(name, False) for name in names])['project_ids']
    service.ensure_workspaces(ids[0])
    service.bulk_update_projects(ids, status='待交付')
    sources = {}
    for pid in ids:
        workspace = Path(service.workspaces.get_workspace(pid)['path'])
        source = workspace / '材料.txt'
        source.write_bytes(f'anonymous material {pid}'.encode())
        sources[pid] = source
    return service, ids, sources


def archive_rows(service, table):
    with service.db.connect() as connection:
        return [dict(row) for row in connection.execute(f'SELECT * FROM {table} ORDER BY project_id')]


def test_selected_projects_deliver_verified_original_names_and_count_once(projects):
    service, (first, unselected, last), sources = projects
    prior = service.confirm_delivery(first, confirmed=True)
    service.set_status(first, '待交付')

    result = service.confirm_project_deliveries([first, last], confirmed=True)

    assert result['kind'] == 'bulk_delivery_result'
    assert result['project_ids'] == [first, last]
    history = service.db.delivery_history()
    assert history['project_count'] == 2
    assert history['event_count'] == 3
    assert history['repeat_count'] == 1
    events = {event['id']: event for event in history['events']}
    assert events[prior['event_id']]['counted']
    for pid, event_id in zip(result['project_ids'], result['event_ids'], strict=True):
        event = events[event_id]
        assert event['project_id'] == pid and event['kind'] == 'manual-files'
        source = sources[pid]
        assert (Path(event['archive_path']) / source.name).read_bytes() == source.read_bytes()
        assert service.get_project(pid)['status'] == '已交付'
    assert service.get_project(unselected)['status'] == '待交付'


def test_later_registration_failure_rolls_back_whole_batch_and_retries_copies(projects, monkeypatch):
    service, (first, last, _), sources = projects
    original = service.db._complete_file_delivery
    written = []

    def write_then_fail(connection, pid, verified):
        event_id = original(connection, pid, verified)
        written.append((pid, event_id))
        if pid == last:
            raise RuntimeError('isolated registration error')
        return event_id

    with monkeypatch.context() as patch:
        patch.setattr(service.db, '_complete_file_delivery', write_then_fail)
        with pytest.raises(RuntimeError, match='匿名乙项目.*isolated registration error'):
            service.confirm_project_deliveries([first, last], confirmed=True)

    assert [pid for pid, _ in written] == [first, last]
    assert service.db.delivery_history()['event_count'] == 0
    assert service.db.delivery_history()['project_count'] == 0
    assert archive_rows(service, 'project_delivery_archives') == []
    staged = archive_rows(service, 'project_delivery_archive_attempts')
    assert [row['state'] for row in staged] == ['prepared', 'prepared']
    for pid in (first, last):
        assert service.get_project(pid)['status'] == '待交付'
        assert sources[pid].is_file()

    service.confirm_project_deliveries([first, last], confirmed=True)
    assert [(row['path'], row['state']) for row in archive_rows(service, 'project_delivery_archive_attempts')] == [
        (row['path'], 'registered') for row in staged
    ]


@pytest.mark.parametrize(('change', 'reason'), [
    ('missing', '不存在'), ('developing', '待交付'), ('archived', '归档'),
    ('deleted', '删除'), ('merged', '合并'), ('blocked', '恢复'), ('empty', '没有可交付文件'),
])
def test_ineligible_selection_never_commits_another_project(projects, change, reason):
    service, (first, last, other), sources = projects
    if change == 'missing':
        last = 99
    elif change == 'developing':
        service.set_status(last, '开发中')
    elif change == 'archived':
        service.bulk_update_projects([last], archived=True)
    elif change == 'deleted':
        service.delete_project(last, confirmed=True)
    elif change in ('merged', 'blocked'):
        with service.db.connect() as connection:
            if change == 'merged':
                connection.execute('INSERT INTO project_merges(source_id,target_id,operation_id,snapshot_json) '
                                   "VALUES (?,?,'isolated-merge','{}')", (last, other))
            else:
                connection.execute('INSERT INTO project_change_blocks(project_id,operation_id,reason) '
                                   "VALUES (?,'isolated-recovery','未完成')", (last,))
    elif change == 'empty':
        sources[last].unlink()

    with pytest.raises((ValueError, RuntimeError), match=reason):
        service.confirm_project_deliveries([first, last], confirmed=True)

    assert service.get_project(first)['status'] == '待交付'
    assert service.db.delivery_history()['event_count'] == 0
    assert archive_rows(service, 'project_delivery_archives') == []


@pytest.mark.parametrize('ids', [[], [1, 1], [True], list(range(1, 502))])
def test_batch_requires_valid_unique_selection(projects, ids):
    service, _, _ = projects
    with pytest.raises(ValueError, match='1 至 500'):
        service.confirm_project_deliveries(ids, confirmed=True)
    assert service.db.delivery_history()['event_count'] == 0


def test_batch_requires_explicit_confirmation(projects):
    service, ids, _ = projects
    with pytest.raises(ValueError, match='明确确认'):
        service.confirm_project_deliveries(ids)
    assert service.db.delivery_history()['event_count'] == 0


def test_batch_rechecks_state_before_registering_any_delivery(projects, monkeypatch):
    service, (first, last, _), _ = projects
    from workbox.storage.delivery_archive import ProjectDeliveryArchive

    original = ProjectDeliveryArchive._copy

    def copy_then_change(archive, root, target, manifest, **kwargs):
        original(archive, root, target, manifest, **kwargs)
        service.set_status(last, '开发中')

    monkeypatch.setattr(ProjectDeliveryArchive, '_copy', copy_then_change)
    with pytest.raises(ValueError, match='匿名乙项目.*待交付'):
        service.confirm_project_deliveries([first, last], confirmed=True)
    assert service.get_project(first)['status'] == '待交付'
    assert service.get_project(last)['status'] == '开发中'
    assert service.db.delivery_history()['event_count'] == 0
    assert archive_rows(service, 'project_delivery_archives') == []


def test_cancellation_during_final_registration_rolls_back_every_delivery(projects, monkeypatch):
    service, (first, last, _), sources = projects
    cancellation = threading.Event()
    original = service.db._complete_file_delivery

    def write_then_cancel(connection, pid, verified):
        event_id = original(connection, pid, verified)
        if pid == last:
            cancellation.set()
        return event_id

    monkeypatch.setattr(service.db, '_complete_file_delivery', write_then_cancel)
    with pytest.raises(CancelledError):
        service.confirm_project_deliveries([first, last], confirmed=True, cancel_event=cancellation)
    assert service.db.delivery_history()['event_count'] == 0
    assert archive_rows(service, 'project_delivery_archives') == []
    assert [service.get_project(pid)['status'] for pid in (first, last)] == ['待交付', '待交付']
    with sources[first].open('r+b') as stream:
        assert stream.read() == b'anonymous material 1'


@pytest.mark.skipif(os.name != 'nt', reason='工作材料的只读租约依赖 Windows 文件共享模式')
def test_earlier_source_stays_read_only_during_later_registration(projects, monkeypatch):
    service, (first, last, _), sources = projects
    original = service.db._complete_file_delivery
    denied = []

    def write_then_try_to_change(connection, pid, verified):
        event_id = original(connection, pid, verified)
        if pid == last:
            try:
                sources[first].write_bytes(b'edited during the later registration')
            except OSError as error:
                denied.append(error.errno == errno.EACCES or getattr(error, 'winerror', None) == 32)
            else:
                denied.append(False)
        return event_id

    monkeypatch.setattr(service.db, '_complete_file_delivery', write_then_try_to_change)
    service.confirm_project_deliveries([first, last], confirmed=True)

    assert denied == [True]
    assert sources[first].read_bytes() == b'anonymous material 1'
    assert service.db.delivery_history()['event_count'] == 2
    with sources[first].open('r+b') as stream:
        assert stream.read() == b'anonymous material 1'


def test_batch_rejects_source_change_after_first_copy(projects, monkeypatch):
    service, (first, last, _), sources = projects
    from workbox.storage.delivery_archive import ProjectDeliveryArchive

    original = ProjectDeliveryArchive._copy

    def copy_then_edit_earlier(archive, root, target, manifest, **kwargs):
        original(archive, root, target, manifest, **kwargs)
        if root == sources[last].parent:
            sources[first].write_bytes(b'edited after first archive preparation')

    monkeypatch.setattr(ProjectDeliveryArchive, '_copy', copy_then_edit_earlier)
    with pytest.raises(RuntimeError, match='匿名甲项目.*发生变化'):
        service.confirm_project_deliveries([first, last], confirmed=True)

    assert sources[first].read_bytes() == b'edited after first archive preparation'
    assert service.db.delivery_history()['event_count'] == 0
    assert archive_rows(service, 'project_delivery_archives') == []
    assert [service.get_project(pid)['status'] for pid in (first, last)] == ['待交付', '待交付']


def test_batch_detects_new_source_file_before_commit(projects, monkeypatch):
    service, (first, last, _), sources = projects
    added = sources[first].with_name('追加材料.txt')
    original = service.db._complete_file_delivery

    def write_then_add_to_earlier_project(connection, pid, verified):
        event_id = original(connection, pid, verified)
        if pid == last:
            added.write_bytes(b'added during later registration')
        return event_id

    monkeypatch.setattr(service.db, '_complete_file_delivery', write_then_add_to_earlier_project)
    with pytest.raises(RuntimeError, match='匿名甲项目.*发生变化'):
        service.confirm_project_deliveries([first, last], confirmed=True)

    assert added.read_bytes() == b'added during later registration'
    assert service.db.delivery_history()['event_count'] == 0
    assert archive_rows(service, 'project_delivery_archives') == []
    assert [service.get_project(pid)['status'] for pid in (first, last)] == ['待交付', '待交付']


@pytest.mark.parametrize('reused', [False, True], ids=['staged', 'reused'])
def test_batch_detects_an_extra_archive_file_before_commit(projects, monkeypatch, reused):
    service, (first, last, _), _ = projects
    if reused:
        service.confirm_delivery(first, confirmed=True)
        service.set_status(first, '待交付')
    original = service.db._complete_file_delivery
    added = None

    def write_then_add_to_earlier_archive(connection, pid, verified):
        nonlocal added
        event_id = original(connection, pid, verified)
        if pid == first:
            added = Path(verified['path']) / '~$额外文件.txt'
        if pid == last:
            added.write_bytes(b'added during later registration')
        return event_id

    monkeypatch.setattr(service.db, '_complete_file_delivery', write_then_add_to_earlier_archive)
    with pytest.raises(ValueError, match='匿名甲项目.*归档副本文件集合发生变化'):
        service.confirm_project_deliveries([first, last], confirmed=True)

    assert added.read_bytes() == b'added during later registration'
    assert service.db.delivery_history()['event_count'] == int(reused)
    assert len(archive_rows(service, 'project_delivery_archives')) == int(reused)
    assert [service.get_project(pid)['status'] for pid in (first, last)] == ['待交付', '待交付']
