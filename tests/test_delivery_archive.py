"""用隔离工作目录验证普通材料交付归档，原件始终保留。"""

import importlib
import importlib.util
import errno
import json
import threading
from concurrent.futures import CancelledError
from pathlib import Path

import pytest

from app import WorkBox
from workspace_manager import WorkspaceManager


def archive_class():
    assert importlib.util.find_spec('delivery_archive') is not None, 'project material archive is missing'
    return importlib.import_module('delivery_archive').ProjectDeliveryArchive


@pytest.fixture
def context(tmp_path):
    service = WorkBox(tmp_path / 'data' / 'workbox.db')
    ids = service.db.create_capture('甲系统\n乙系统', '隔离批次', [('甲系统', False), ('乙系统', False)])['project_ids']
    workspaces = WorkspaceManager(service)
    workspaces.ensure_projects(ids)
    root = Path(workspaces.get_workspace(ids[0])['path'])
    service.set_status(ids[0], '待交付')
    return service, workspaces, ids, root


def test_word_only_project_is_copied_then_delivered_with_manifest(context):
    service, workspaces, ids, root = context
    source = root / '操作手册.docx'
    source.write_bytes(b'isolated Word fixture')
    result = archive_class()(service, workspaces).confirm(ids[0], confirmed=True)
    stored = result['archive']
    target = Path(stored['path']) / source.name
    assert source.read_bytes() == target.read_bytes() == b'isolated Word fixture'
    assert target.parent.is_relative_to(Path(workspaces.settings()['archive_root']))
    assert stored['source_root'] == str(root)
    assert stored['file_count'] == 1
    assert stored['manifest'][0]['path'] == source.name
    assert result['event_id'] > 0
    current = service.db.get_project(ids[0])
    assert current['status'] == '已交付' and current['delivered_zip_id'] is None
    assert current['delivered_at']
    with service.db.connect() as connection:
        record = connection.execute('SELECT * FROM project_delivery_archives').fetchone()
        assert json.loads(record['manifest']) == stored['manifest']
        assert record['signature'] == stored['signature']


def test_manual_confirmation_is_required_before_copying(context):
    service, workspaces, ids, root = context
    (root / '资料.docx').write_bytes(b'word')
    manager = archive_class()(service, workspaces)
    with pytest.raises(ValueError):
        manager.confirm(ids[0])
    assert service.db.get_project(ids[0])['status'] == '待交付'
    assert not list(Path(workspaces.settings()['archive_root']).rglob('资料.docx'))


def test_relative_paths_zero_byte_files_and_original_names_are_preserved(context):
    service, workspaces, ids, root = context
    (root / '文档').mkdir()
    (root / '文档' / '同名.docx').write_bytes(b'nested')
    (root / '同名.docx').write_bytes(b'root')
    (root / '零字节.txt').write_bytes(b'')
    (root / '~$同名.docx').write_bytes(b'office lock')
    result = archive_class()(service, workspaces).confirm(ids[0], confirmed=True)
    stored = result['archive']
    assert {row['path'] for row in stored['manifest']} == {'文档/同名.docx', '同名.docx', '零字节.txt'}
    target = Path(stored['path'])
    assert (target / '文档' / '同名.docx').read_bytes() == b'nested'
    assert (target / '同名.docx').read_bytes() == b'root'
    assert (target / '零字节.txt').stat().st_size == 0
    assert not (target / '~$同名.docx').exists()
    assert (root / '~$同名.docx').read_bytes() == b'office lock'


def test_registered_other_project_subtree_is_excluded(context):
    service, workspaces, ids, root = context
    child = root / '另一项目'
    child.mkdir()
    (child / '其他项目.docx').write_bytes(b'other')
    (root / '本项目.docx').write_bytes(b'own')
    with service.db.connect() as connection:
        connection.execute('UPDATE project_workspaces SET path=? WHERE project_id=?', (str(child), ids[1]))
    result = archive_class()(service, workspaces).confirm(ids[0], confirmed=True)
    assert [row['path'] for row in result['archive']['manifest']] == ['本项目.docx']
    assert (child / '其他项目.docx').read_bytes() == b'other'
    assert service.db.get_project(ids[1])['status'] == '待开发'


@pytest.mark.parametrize('lock_only', [False, True])
def test_empty_or_office_lock_only_project_cannot_be_delivered(context, lock_only):
    service, workspaces, ids, root = context
    if lock_only:
        (root / '~$资料.docx').write_bytes(b'lock')
    with pytest.raises(ValueError):
        archive_class()(service, workspaces).confirm(ids[0], confirmed=True)
    assert service.db.get_project(ids[0])['status'] == '待交付'


def test_same_content_confirmation_is_idempotent_but_pending_reconfirmation_is_a_new_event(context):
    service, workspaces, ids, root = context
    (root / '资料.docx').write_bytes(b'word')
    manager = archive_class()(service, workspaces)
    first = manager.confirm(ids[0], confirmed=True)
    repeated = manager.confirm(ids[0], confirmed=True)
    assert first['archive']['id'] == repeated['archive']['id']
    assert first['event_id'] == repeated['event_id']
    service.set_status(ids[0], '待交付')
    reconfirmed = manager.confirm(ids[0], confirmed=True)
    assert reconfirmed['archive']['id'] == first['archive']['id']
    assert reconfirmed['event_id'] != first['event_id']
    with service.db.connect() as connection:
        assert connection.execute('SELECT count(*) FROM project_delivery_archives').fetchone()[0] == 1


def test_changed_content_creates_independent_version_and_does_not_overwrite(context):
    service, workspaces, ids, root = context
    source = root / '资料.docx'
    source.write_bytes(b'first')
    manager = archive_class()(service, workspaces)
    first = manager.confirm(ids[0], confirmed=True)
    source.write_bytes(b'second')
    service.set_status(ids[0], '待交付')
    second = manager.confirm(ids[0], confirmed=True)
    assert first['archive']['path'] != second['archive']['path']
    assert first['archive']['signature'] != second['archive']['signature']
    assert (Path(first['archive']['path']) / source.name).read_bytes() == b'first'
    assert (Path(second['archive']['path']) / source.name).read_bytes() == source.read_bytes() == b'second'


def test_cancellation_preserves_source_and_pending_status(context):
    service, workspaces, ids, root = context
    source = root / '资料.docx'
    source.write_bytes(b'word')
    event = threading.Event()
    event.set()
    with pytest.raises(CancelledError):
        archive_class()(service, workspaces).confirm(ids[0], confirmed=True, cancel_event=event)
    assert source.read_bytes() == b'word'
    assert service.db.get_project(ids[0])['status'] == '待交付'


def test_cancel_during_copy_can_retry_without_overwriting_verified_files(context, monkeypatch):
    service, workspaces, ids, root = context
    (root / 'a.docx').write_bytes(b'a')
    (root / 'b.docx').write_bytes(b'b')
    manager = archive_class()(service, workspaces)
    module = importlib.import_module('delivery_archive')
    original = module.copy_verified
    event = threading.Event()
    copies = []

    def copy_then_cancel(source, target, **kwargs):
        value = original(source, target, **kwargs)
        copies.append(str(target))
        event.set()
        return value

    monkeypatch.setattr(module, 'copy_verified', copy_then_cancel)
    with pytest.raises(CancelledError):
        manager.confirm(ids[0], confirmed=True, cancel_event=event)
    assert service.db.get_project(ids[0])['status'] == '待交付'
    monkeypatch.setattr(module, 'copy_verified', original)
    resumed = manager.confirm(ids[0], confirmed=True)
    assert str(Path(resumed['archive']['path']) / 'a.docx') == copies[0]
    assert (root / 'a.docx').read_bytes() == b'a'
    assert len(list(Path(workspaces.settings()['archive_root']).rglob('a.docx'))) == 1


@pytest.mark.parametrize('change', ['modify', 'add', 'delete'])
def test_source_changes_during_copy_do_not_confirm_delivery(context, monkeypatch, change):
    service, workspaces, ids, root = context
    (root / 'a.docx').write_bytes(b'a')
    (root / 'b.docx').write_bytes(b'b')
    module = importlib.import_module('delivery_archive')
    manager = archive_class()(service, workspaces)
    original = module.copy_verified
    changed = False

    def copy_then_change(source, target, **kwargs):
        nonlocal changed
        result = original(source, target, **kwargs)
        if not changed:
            changed = True
            if change == 'modify':
                (root / 'a.docx').write_bytes(b'changed after copying')
            elif change == 'add':
                (root / 'new.docx').write_bytes(b'new file')
            else:
                (root / 'a.docx').unlink()
        return result

    monkeypatch.setattr(module, 'copy_verified', copy_then_change)
    with pytest.raises((ValueError, RuntimeError, OSError)):
        manager.confirm(ids[0], confirmed=True)
    assert service.db.get_project(ids[0])['status'] == '待交付'
    with service.db.connect() as connection:
        assert connection.execute('SELECT count(*) FROM project_delivery_archives').fetchone()[0] == 0


def test_database_failure_preserves_copied_version_and_recovers_without_second_copy(context, monkeypatch):
    service, workspaces, ids, root = context
    (root / '资料.docx').write_bytes(b'word')
    manager = archive_class()(service, workspaces)
    original = service.db._complete_file_delivery

    def fail(*args):
        original(*args)
        raise RuntimeError('isolated database failure')

    monkeypatch.setattr(service.db, '_complete_file_delivery', fail)
    with pytest.raises(RuntimeError):
        manager.confirm(ids[0], confirmed=True)
    copied = list(Path(workspaces.settings()['archive_root']).rglob('资料.docx'))
    assert len(copied) == 1
    assert service.db.get_project(ids[0])['status'] == '待交付'
    with service.db.connect() as connection:
        assert connection.execute('SELECT count(*) FROM project_delivery_archives').fetchone()[0] == 0
    monkeypatch.setattr(service.db, '_complete_file_delivery', original)
    recovered = manager.confirm(ids[0], confirmed=True)
    assert Path(recovered['archive']['path']) / '资料.docx' == copied[0]
    assert len(list(Path(workspaces.settings()['archive_root']).rglob('资料.docx'))) == 1


def test_symlink_source_is_refused(context, tmp_path):
    service, workspaces, ids, root = context
    outside = tmp_path / 'outside.docx'
    outside.write_bytes(b'outside')
    try:
        (root / 'linked.docx').symlink_to(outside)
    except OSError:
        pytest.skip('current Windows user cannot create isolated symlink')
    with pytest.raises(ValueError):
        archive_class()(service, workspaces).confirm(ids[0], confirmed=True)
    assert outside.read_bytes() == b'outside'
    assert service.db.get_project(ids[0])['status'] == '待交付'


def test_reparse_marked_source_is_refused_without_reading_target(context, monkeypatch):
    from types import SimpleNamespace
    service, workspaces, ids, root = context
    source = root / 'reparse.docx'
    source.write_bytes(b'source remains')
    real_lstat = Path.lstat

    def marked_lstat(path, *args, **kwargs):
        info = real_lstat(path, *args, **kwargs)
        if path == source:
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
        return info

    monkeypatch.setattr(Path, 'lstat', marked_lstat)
    with pytest.raises(ValueError):
        archive_class()(service, workspaces).confirm(ids[0], confirmed=True)
    assert source.read_bytes() == b'source remains'
    assert service.db.get_project(ids[0])['status'] == '待交付'


def test_tampered_archived_copy_is_not_reused_or_overwritten(context):
    service, workspaces, ids, root = context
    source = root / '资料.docx'
    source.write_bytes(b'good')
    manager = archive_class()(service, workspaces)
    first = manager.confirm(ids[0], confirmed=True)
    archived = Path(first['archive']['path']) / source.name
    archived.write_bytes(b'tampered')
    service.set_status(ids[0], '待交付')
    with pytest.raises(ValueError):
        manager.confirm(ids[0], confirmed=True)
    assert service.db.get_project(ids[0])['status'] == '待交付'
    assert source.read_bytes() == b'good'
    assert archived.read_bytes() == b'tampered'


def test_archived_project_is_rejected_before_copy(context):
    service, workspaces, ids, root = context
    (root / '资料.docx').write_bytes(b'word')
    service.db.bulk_update_projects([ids[0]], archived=True)
    with pytest.raises(ValueError):
        archive_class()(service, workspaces).confirm(ids[0], confirmed=True)
    assert not list(Path(workspaces.settings()['archive_root']).rglob('资料.docx'))


@pytest.mark.parametrize('reuse', [False, True])
def test_archived_word_stays_locked_after_database_verification(context, monkeypatch, reuse):
    service, workspaces, ids, root = context
    source = root / '资料.docx'
    source.write_bytes(b'verified Word material')
    manager = archive_class()(service, workspaces)
    if reuse:
        manager.confirm(ids[0], confirmed=True)
        service.set_status(ids[0], '待交付')
    original_complete = service.db._complete_file_delivery
    attempts = []

    def complete_then_try_to_replace(connection, project_id, verified):
        event_id = original_complete(connection, project_id, verified)
        target = Path(verified['path']) / source.name
        try:
            target.write_bytes(b'changed after database verified the archive')
        except OSError as error:
            attempts.append(error.errno == errno.EACCES or error.winerror == 32)
        else:
            attempts.append('changed')
        return event_id

    monkeypatch.setattr(service.db, '_complete_file_delivery', complete_then_try_to_replace)
    result = manager.confirm(ids[0], confirmed=True)
    assert attempts == [True], 'another writer must be denied until the delivery transaction commits'
    assert (Path(result['archive']['path']) / source.name).read_bytes() == source.read_bytes()
    assert service.db.get_project(ids[0])['status'] == '已交付'


@pytest.mark.parametrize('reuse', [False, True])
def test_archived_word_stays_locked_until_database_transaction_commit(context, monkeypatch, reuse):
    from contextlib import contextmanager
    service, workspaces, ids, root = context
    source = root / '资料.docx'
    source.write_bytes(b'archive must stay verified through commit')
    manager = archive_class()(service, workspaces)
    if reuse:
        manager.confirm(ids[0], confirmed=True)
        service.set_status(ids[0], '待交付')
    original_connect = service.db.connect
    original_complete = service.db._complete_file_delivery
    target = None
    attempts = []

    def complete_and_remember(connection, project_id, verified):
        nonlocal target
        event_id = original_complete(connection, project_id, verified)
        target = Path(verified['path']) / source.name
        return event_id

    @contextmanager
    def connect_and_try_to_change_before_commit():
        with original_connect() as connection:
            yield connection
            # 原连接上下文退出时才真正提交；此处精确落在生产事务提交之前。
            if target is not None and connection.in_transaction and not attempts:
                try:
                    target.write_bytes(b'changed immediately before commit')
                except OSError as error:
                    attempts.append(error.errno == errno.EACCES or error.winerror == 32)
                else:
                    attempts.append('changed')

    monkeypatch.setattr(service.db, '_complete_file_delivery', complete_and_remember)
    monkeypatch.setattr(service.db, 'connect', connect_and_try_to_change_before_commit)
    result = manager.confirm(ids[0], confirmed=True)
    assert attempts == [True], 'archive leases must outlive the database connection commit boundary'
    assert (Path(result['archive']['path']) / source.name).read_bytes() == source.read_bytes()
    with original_connect() as connection:
        assert connection.execute('SELECT count(*) FROM delivery_events WHERE project_id=?',
                                  (ids[0],)).fetchone()[0] == (2 if reuse else 1)
