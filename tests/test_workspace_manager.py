import hashlib
import os
import threading
from concurrent.futures import CancelledError
from pathlib import Path
from uuid import UUID
from zipfile import ZipFile

import pytest

from workbox.app import WorkBox
from workbox.storage.native_files import ZipLease
from workbox.storage.workspace_manager import WorkspaceManager, copy_verified


@pytest.fixture
def context(tmp_path):
    service = WorkBox(tmp_path / 'data' / 'workbox.db')
    return service, WorkspaceManager(service)


def projects(service, names, batch='本批'):
    return service.db.create_capture('\n'.join(names), batch, [(name, False) for name in names])['project_ids']


def test_single_and_batch_hierarchy_and_repeat(context):
    service, manager = context
    ids = projects(service, ['甲系统', '乙系统'])
    root = Path(manager.settings()['work_root'])
    result = manager.ensure_projects(ids)
    assert [Path(row['path']) for row in result] == [root / '甲系统' / '甲系统', root / '甲系统' / '乙系统']
    assert manager.ensure_projects(ids) == result
    single = projects(service, ['丙系统'], '单项')
    assert Path(manager.ensure_projects(single)[0]['path']) == root / '丙系统'
    assert manager.get_workspace(ids[0])['name'] == '甲系统'


def test_existing_unknown_directories_and_windows_names(context):
    service, manager = context
    root = Path(manager.settings()['work_root'])
    (root / '甲系统').mkdir(parents=True)
    (root / '甲系统' / '原件.txt').write_text('保留')
    ids = projects(service, ['甲系统'])
    assert Path(manager.ensure_projects(ids)[0]['path']).name == '甲系统 (2)'
    ids = projects(service, ['CON', '甲/乙:*?系统'], '名称')
    rows = manager.ensure_projects(ids)
    assert Path(rows[0]['path']).name == '_CON'
    assert Path(rows[1]['path']).name == '甲_乙___系统'
    assert (root / '甲系统' / '原件.txt').read_text() == '保留'


def test_settings_restart_and_old_paths_stay(context, tmp_path):
    service, manager = context
    pid = projects(service, ['旧系统'])[0]
    old = manager.ensure_projects([pid])[0]
    manager.configure(tmp_path / 'new-work', tmp_path / 'new-zip', stable_seconds=15)
    restarted = WorkspaceManager(WorkBox(service.db.path))
    assert restarted.settings() == dict(work_root=str(tmp_path / 'new-work'), archive_root=str(tmp_path / 'new-zip'), stable_seconds=15,
                                        auto_create_projects=True, auto_deliver_zip=True, zip_delivery_min_files=4)
    assert restarted.get_workspace(pid)['path'] == old['path']


@pytest.mark.parametrize('kind', ['same', 'child', 'parent', 'network', 'stream', 'seconds'])
def test_invalid_configuration_rejected(context, tmp_path, kind):
    _, manager = context
    work, archive, seconds = tmp_path / 'work', tmp_path / 'zip', 10
    if kind == 'same': archive = work
    elif kind == 'child': archive = work / 'zip'
    elif kind == 'parent': work = archive / 'work'
    elif kind == 'network': work = '\\\\server\\share'
    elif kind == 'stream': work = str(work) + ':stream'
    else: seconds = 4
    with pytest.raises(ValueError): manager.configure(work, archive, seconds)


def test_import_copy_dedup_versions_source_inside_and_archived(context, tmp_path):
    service, manager = context
    pid = projects(service, ['文件系统'])[0]
    source = tmp_path / '外来.txt'
    source.write_bytes(b'first')
    first = manager.import_file(source, pid)
    assert first['source_preserved'] and not first['duplicate']
    assert source.read_bytes() == b'first'
    assert Path(first['file']['path']).read_bytes() == b'first'
    assert manager.import_file(source, pid)['duplicate']
    source.write_bytes(b'second')
    second = manager.import_file(source, pid)
    assert second['file']['path'] == first['file']['path']
    assert Path(second['file']['path']).name == source.name
    with service.db.connect() as connection:
        old = connection.execute('SELECT * FROM work_files WHERE id=?', (first['file']['id'],)).fetchone()
    assert hashlib.sha256(b'first').hexdigest()[:8] in Path(old['path']).name
    assert Path(old['path']).read_bytes() == b'first'
    inside = Path(manager.get_workspace(pid)['path']) / '内部.txt'
    inside.write_bytes(b'inside')
    registered = manager.import_file(inside, pid)
    assert registered['file']['path'] == str(inside)
    service.bulk_update_projects([pid], archived=True)
    assert manager.list_workspaces() == []
    assert len(manager.list_workspaces(include_archived=True)) == 1
    with pytest.raises(ValueError): manager.import_file(source, pid)
    with pytest.raises((ValueError, KeyError)): manager.import_file(source, None)


@pytest.mark.skipif(os.name != 'nt', reason='需要 Windows NTFS 命名数据流')
def test_local_docx_intake_copies_named_streams_and_verifies_duplicate(context, tmp_path):
    service, manager = context
    pid = projects(service, ['匿名材料'])[0]
    source = tmp_path / '匿名材料.docx'
    source.write_bytes(b'ordinary fixture')
    metadata = {
        'Zone.Identifier': b'[ZoneTransfer]\r\nZoneId=3\r\n',
        'DocumentMetadata': b'private stream contents',
    }
    for name, data in metadata.items():
        Path(f'{source}:{name}').write_bytes(data)

    result = manager.import_file(source, pid)
    target = Path(result['file']['path'])
    assert result['source_preserved'] and not result['duplicate']
    assert target.name == source.name
    assert target.read_bytes() == source.read_bytes() == b'ordinary fixture'
    for name, data in metadata.items():
        assert Path(f'{target}:{name}').read_bytes() == Path(f'{source}:{name}').read_bytes() == data
    assert service.db.get_project(pid)['status'] == '待交付'
    assert manager.import_file(source, pid)['duplicate']


@pytest.mark.skipif(os.name != 'nt', reason='需要 Windows NTFS 命名数据流')
def test_local_docx_same_body_with_different_streams_is_not_a_duplicate(context, tmp_path):
    service, manager = context
    pid = projects(service, ['匿名材料'])[0]
    source = tmp_path / '匿名材料.docx'
    source.write_bytes(b'ordinary fixture')
    initial = manager.import_file(source, pid)
    target = Path(initial['file']['path'])
    Path(f'{source}:DocumentMetadata').write_bytes(b'not in the working copy')

    with pytest.raises(ValueError, match='命名数据流'):
        manager.import_file(source, pid)

    assert target.read_bytes() == source.read_bytes() == b'ordinary fixture'
    assert Path(f'{source}:DocumentMetadata').read_bytes() == b'not in the working copy'
    assert not Path(f'{target}:DocumentMetadata').exists()
    assert [row['status'] for row in manager.intake_log.list_recent()] == ['copied']


@pytest.mark.skipif(os.name != 'nt', reason='需要 Windows NTFS 命名数据流')
def test_local_docx_version_rotation_preserves_both_copies_named_streams(context, tmp_path):
    service, manager = context
    pid = projects(service, ['匿名材料'])[0]
    source = tmp_path / '匿名材料.docx'
    source.write_bytes(b'previous document')
    initial = manager.import_file(source, pid)
    target = Path(initial['file']['path'])
    Path(f'{target}:DocumentMetadata').write_bytes(b'old metadata')
    source.write_bytes(b'new document')
    Path(f'{source}:DocumentMetadata').write_bytes(b'new metadata')

    result = manager.import_file(source, pid)

    assert result['file']['path'] == str(target)
    assert target.read_bytes() == source.read_bytes() == b'new document'
    assert Path(f'{target}:DocumentMetadata').read_bytes() == Path(f'{source}:DocumentMetadata').read_bytes() == b'new metadata'
    with service.db.connect() as connection:
        history = Path(connection.execute('SELECT path FROM work_files WHERE id=?',
                                          (initial['file']['id'],)).fetchone()['path'])
    assert history != target and history.read_bytes() == b'previous document'
    assert Path(f'{history}:DocumentMetadata').read_bytes() == b'old metadata'


@pytest.mark.skipif(os.name != 'nt', reason='需要 Windows NTFS 命名数据流')
@pytest.mark.parametrize('duplicate', [False, True], ids=['fresh-copy', 'duplicate'])
def test_local_docx_rolls_back_when_a_source_stream_appears_during_registration(
        context, tmp_path, monkeypatch, duplicate):
    service, manager = context
    pid = projects(service, ['匿名材料'])[0]
    source = tmp_path / '匿名材料.docx'
    source.write_bytes(b'ordinary fixture')
    initial = manager.import_file(source, pid) if duplicate else None
    workspace = Path(manager.ensure_projects([pid])[0]['path'])
    extra = Path(f'{source}:LateMetadata')
    mark_pending = service.db._mark_work_file_pending

    def mark_then_add_stream(*args, **kwargs):
        mark_pending(*args, **kwargs)
        extra.write_bytes(b'new source metadata')

    monkeypatch.setattr(service.db, '_mark_work_file_pending', mark_then_add_stream)
    with pytest.raises(RuntimeError, match='命名数据流.*发生变化'):
        manager.import_file(source, pid)

    assert extra.read_bytes() == b'new source metadata'
    assert source.read_bytes() == b'ordinary fixture'
    assert (workspace / source.name).exists() is duplicate
    assert [row['status'] for row in manager.intake_log.list_recent()] == (['copied'] if initial else [])


def _write_stream_while_file_can_move(path, name, content):
    import ctypes
    import msvcrt

    from workbox.storage.native_files import _api, named_data_stream_path

    api = _api()
    handle = api.CreateFileW(named_data_stream_path(path, name), 0x40000000, 7, None, 2, 0, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY)
    except BaseException:
        api.CloseHandle(handle)
        raise
    with os.fdopen(fd, 'wb') as stream:
        stream.write(content)


@pytest.mark.skipif(os.name != 'nt', reason='需要 Windows NTFS 命名数据流')
@pytest.mark.parametrize('rotation', [False, True], ids=['fresh-copy', 'rotation'])
def test_local_docx_preserves_unexpected_destination_stream_without_committing(
        context, tmp_path, monkeypatch, rotation):
    service, manager = context
    pid = projects(service, ['匿名材料'])[0]
    source = tmp_path / '匿名材料.docx'
    if rotation:
        source.write_bytes(b'original document')
        initial = manager.import_file(source, pid)
    source.write_bytes(b'incoming document')
    workspace = Path(manager.ensure_projects([pid])[0]['path'])
    target = workspace / source.name
    unexpected = Path(f'{target}:LateMetadata')
    mark_pending = service.db._mark_work_file_pending

    def mark_then_add_stream(*args, **kwargs):
        mark_pending(*args, **kwargs)
        _write_stream_while_file_can_move(target, 'LateMetadata', b'external destination metadata')

    monkeypatch.setattr(service.db, '_mark_work_file_pending', mark_then_add_stream)
    with pytest.raises(RuntimeError) as failure:
        manager.import_file(source, pid)

    # 轮换回滚可能首先发现添加命名流也改变了文件的修改时间。
    assert '命名数据流' in str(failure.value) or '命名数据流' in str(failure.value.__context__)
    assert source.read_bytes() == target.read_bytes() == b'incoming document'
    assert unexpected.read_bytes() == b'external destination metadata'
    assert [row['status'] for row in manager.intake_log.list_recent()] == (['copied'] if rotation else [])
    with service.db.connect() as connection:
        rows = connection.execute('SELECT id FROM work_files WHERE project_id=?', (pid,)).fetchall()
    assert [row['id'] for row in rows] == ([initial['file']['id']] if rotation else [])
    if rotation:
        prepared = list(manager.document_versions.journal_root.glob('*.json'))
        assert len(prepared) == 1 and pid in manager.document_versions.blocked


@pytest.mark.skipif(os.name != 'nt', reason='需要 Windows NTFS 命名数据流')
@pytest.mark.parametrize('rotation', [False, True], ids=['fresh-copy', 'rotation'])
def test_local_docx_keeps_copied_stream_write_locked_during_registration(
        context, tmp_path, monkeypatch, rotation):
    service, manager = context
    pid = projects(service, ['匿名材料'])[0]
    source = tmp_path / '匿名材料.docx'
    if rotation:
        source.write_bytes(b'original document')
        manager.import_file(source, pid)
    source.write_bytes(b'incoming document')
    Path(f'{source}:DocumentMetadata').write_bytes(b'verified destination metadata')
    workspace = Path(manager.ensure_projects([pid])[0]['path'])
    target = workspace / source.name
    target_stream = Path(f'{target}:DocumentMetadata')
    mark_pending = service.db._mark_work_file_pending

    def mark_then_try_modifying_stream(*args, **kwargs):
        mark_pending(*args, **kwargs)
        with pytest.raises(OSError):
            _write_stream_while_file_can_move(target, 'DocumentMetadata', b'external destination metadata')

    monkeypatch.setattr(service.db, '_mark_work_file_pending', mark_then_try_modifying_stream)
    assert not manager.import_file(source, pid)['duplicate']
    assert target_stream.read_bytes() == b'verified destination metadata'


@pytest.mark.skipif(os.name != 'nt', reason='需要 Windows NTFS 命名数据流')
@pytest.mark.parametrize('after_restore', [False, True], ids=['primary-match', 'after-restore'])
def test_local_docx_preserves_ads_added_while_rolling_back_rotation(context, tmp_path, monkeypatch, after_restore):
    from workbox.storage.native_files import ZipLease

    service, manager = context
    pid = projects(service, ['匿名材料'])[0]
    source = tmp_path / '匿名材料.docx'
    source.write_bytes(b'original document')
    target = Path(manager.import_file(source, pid)['file']['path'])
    source.write_bytes(b'incoming document')
    new_digest = hashlib.sha256(b'incoming document').hexdigest()
    versions = manager.document_versions
    match = versions._matches
    rename = ZipLease.rename_to
    rolling_back = False
    stage = None
    injected_path = None

    def fail_registration(*args, **kwargs):
        nonlocal rolling_back
        rolling_back = True
        raise RuntimeError('simulated registration failure')

    def add_metadata(path):
        nonlocal injected_path
        _write_stream_while_file_can_move(path, 'LateMetadata', b'external rollback metadata')
        injected_path = Path(f'{path}:LateMetadata')

    def match_then_add_stream(lease, expected_identity, digest):
        match(lease, expected_identity, digest)
        if rolling_back and not after_restore and digest == new_digest:
            add_metadata(lease.path)

    def rename_then_add_stream(lease, destination):
        nonlocal stage
        rename(lease, destination)
        if rolling_back and after_restore:
            if lease.path == target:
                add_metadata(stage)
            else:
                stage = lease.path

    monkeypatch.setattr(service.db, '_mark_work_file_pending', fail_registration)
    monkeypatch.setattr(versions, '_matches', match_then_add_stream)
    monkeypatch.setattr(ZipLease, 'rename_to', rename_then_add_stream)
    with pytest.raises(RuntimeError, match='命名数据流'):
        manager.import_file(source, pid)

    assert injected_path.read_bytes() == b'external rollback metadata'
    assert target.read_bytes() == (b'original document' if after_restore else b'incoming document')
    assert [row['status'] for row in manager.intake_log.list_recent()] == ['copied']
    assert list(versions.journal_root.glob('*.json')) and pid in versions.blocked


def test_copy_cancel_invalid_and_no_overwrite(tmp_path):
    source = tmp_path / 'source.bin'
    source.write_bytes(b'abc' * 1024 * 1024)
    target = tmp_path / 'out' / 'copy.bin'
    cancelled = threading.Event()
    def progress(done, total): cancelled.set()
    with pytest.raises(CancelledError): copy_verified(source, target, cancel_event=cancelled, progress=progress)
    assert source.exists() and not target.exists()
    assert not list(target.parent.glob('*.partial'))
    copy_verified(source, target)
    with pytest.raises(FileExistsError): copy_verified(source, target)
    with pytest.raises(ValueError): copy_verified(tmp_path, tmp_path / 'bad')


@pytest.fixture
def copy_paths(tmp_path, monkeypatch):
    identifier = UUID(int=1)
    monkeypatch.setattr('workbox.storage.workspace_manager.uuid.uuid4', lambda: identifier)
    source = tmp_path / 'source.txt'
    source.write_bytes(b'original material')
    return source, tmp_path / 'target.txt', tmp_path / f'~$workbox-{identifier.hex}.partial'


def test_copy_does_not_remove_an_existing_temporary_file(copy_paths):
    source, target, temporary = copy_paths
    temporary.write_bytes(b'another operation')

    with pytest.raises(FileExistsError):
        copy_verified(source, target)

    assert temporary.read_bytes() == b'another operation'
    assert not target.exists()


def test_copy_publishes_before_releasing_the_verified_temporary_file(copy_paths, monkeypatch):
    source, target, temporary = copy_paths
    close_lease = ZipLease.__exit__

    def change_unlocked_temporary(lease, *args):
        close_lease(lease, *args)
        if lease.path == temporary and temporary.exists():
            temporary.write_bytes(b'changed after verification')

    monkeypatch.setattr(ZipLease, '__exit__', change_unlocked_temporary)
    result = copy_verified(source, target)

    assert target.read_bytes() == source.read_bytes() == b'original material'
    assert result['sha256'] == hashlib.sha256(target.read_bytes()).hexdigest()


def test_copy_leaves_a_replacement_temporary_file_in_place(copy_paths, monkeypatch):
    source, target, temporary = copy_paths
    displaced = temporary.with_suffix('.displaced')
    open_lease = ZipLease.__enter__

    def substitute_temporary(lease):
        if lease.path == temporary and not displaced.exists():
            temporary.rename(displaced)
            temporary.write_bytes(b'original material')
        return open_lease(lease)

    monkeypatch.setattr(ZipLease, '__enter__', substitute_temporary)
    with pytest.raises(RuntimeError):
        copy_verified(source, target)

    assert not target.exists()
    assert temporary.read_bytes() == displaced.read_bytes() == b'original material'


def test_copy_does_not_overwrite_a_target_created_during_verification(copy_paths, monkeypatch):
    from workbox.storage.workspace_manager import _hash_stream

    source, target, temporary = copy_paths

    def create_competing_target(stream, **kwargs):
        result = _hash_stream(stream, **kwargs)
        target.write_bytes(b'another operation')
        return result

    monkeypatch.setattr('workbox.storage.workspace_manager._hash_stream', create_competing_target)
    with pytest.raises(FileExistsError):
        copy_verified(source, target)

    assert target.read_bytes() == b'another operation'
    assert not temporary.exists()


def test_zip_structure_validation_and_zone_preserved(tmp_path):
    source = tmp_path / 'a.zip'
    source.write_bytes(b'PK\x03\x04' + b'broken' * 20)
    with pytest.raises(ValueError): copy_verified(source, tmp_path / 'bad.zip', require_zip=True)
    with ZipFile(source, 'w') as archive: archive.writestr('资料.txt', '正文')
    Path(str(source) + ':Zone.Identifier').write_bytes(b'[ZoneTransfer]\r\nZoneId=3\r\n')
    target = tmp_path / 'good.zip'
    result = copy_verified(source, target, require_zip=True)
    assert result['sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert Path(str(target) + ':Zone.Identifier').read_bytes() == Path(str(source) + ':Zone.Identifier').read_bytes()
    assert source.exists()


def test_reparse_roots_and_source_are_rejected(context, tmp_path, monkeypatch):
    from types import SimpleNamespace
    service, manager = context
    real = tmp_path / 'real'
    real.mkdir()
    linked = tmp_path / 'linked'
    linked.mkdir()
    original = Path.lstat
    def marked_lstat(path, *args, **kwargs):
        info = original(path, *args, **kwargs)
        if path == linked:
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
        return info
    monkeypatch.setattr(Path, 'lstat', marked_lstat)
    with pytest.raises(ValueError): manager.configure(linked, tmp_path / 'archives')
    source = linked / 'a.txt'
    source.write_bytes(b'original')
    pid = projects(service, ['链接系统'])[0]
    with pytest.raises(ValueError): manager.import_file(source, pid)
    assert source.read_bytes() == b'original'


def test_corrupt_local_zip_member_name_is_rejected(tmp_path):
    source = tmp_path / 'broken-name.zip'
    with ZipFile(source, 'w') as archive: archive.writestr('a.txt', 'body')
    data = bytearray(source.read_bytes())
    data[30] = ord('b')
    source.write_bytes(data)
    with pytest.raises(ValueError): copy_verified(source, tmp_path / 'copy.zip', require_zip=True)


def test_cancellation_after_publish_removes_only_own_copy(context, tmp_path, monkeypatch):
    from workbox.storage import workspace_manager
    service, manager = context
    pid = projects(service, ['取消系统'])[0]
    directory = Path(manager.ensure_projects([pid])[0]['path'])
    source = tmp_path / 'source.txt'
    source.write_bytes(b'keep')
    event = threading.Event()
    original = workspace_manager.copy_verified
    def cancel_after_copy(*args, **kwargs):
        result = original(*args, **kwargs)
        event.set()
        return result
    monkeypatch.setattr(workspace_manager, 'copy_verified', cancel_after_copy)
    with pytest.raises(CancelledError): manager.import_file(source, pid, cancel_event=event)
    assert source.read_bytes() == b'keep'
    assert list(directory.iterdir()) == []


@pytest.mark.parametrize('location', ['inside_project', 'project', 'group', 'ancestor'])
def test_new_archive_cannot_intersect_registered_workspaces(context, tmp_path, location):
    service, manager = context
    rows = manager.ensure_projects(projects(service, ['第一系统', '第二系统']))
    project = Path(rows[0]['path'])
    group = Path(rows[0]['group_path'])
    candidate = {'inside_project': project / '归档', 'project': project,
                 'group': group, 'ancestor': group.parent}[location]
    original = manager.settings()
    with pytest.raises(ValueError): manager.configure(tmp_path / '新工作', candidate)
    assert manager.settings() == original
    assert not (tmp_path / '新工作').exists()
    assert all(Path(row['path']).is_dir() for row in rows)


def test_archive_roots_keep_default_and_all_configured_roots_after_restart(context, tmp_path):
    service, manager = context
    default = manager.settings()['archive_root']
    assert default in manager.archive_roots()
    old_work = manager.ensure_projects(projects(service, ['旧项目']))[0]['path']
    manager.configure(tmp_path / '工作二', tmp_path / '归档二')
    manager.configure(tmp_path / '工作三', tmp_path / '归档三')
    restarted = WorkspaceManager(WorkBox(service.db.path))
    assert set(restarted.archive_roots()) == {default, str(tmp_path / '归档二'), str(tmp_path / '归档三')}
    assert restarted.get_workspace(1)['path'] == old_work


@pytest.mark.parametrize('location', ['inside', 'same', 'ancestor'])
def test_new_work_root_cannot_reuse_historical_archive(context, tmp_path, location):
    _, manager = context
    historical = tmp_path / '历史归档'
    manager.configure(tmp_path / '工作二', historical)
    manager.configure(tmp_path / '工作三', tmp_path / '当前归档')
    candidate = {'inside': historical / '误用工作', 'same': historical, 'ancestor': tmp_path}[location]
    original = manager.settings()
    with pytest.raises(ValueError): manager.configure(candidate, tmp_path.parent / '独立归档')
    assert manager.settings() == original


@pytest.mark.parametrize('location', ['same', 'inside'])
def test_new_work_root_cannot_nest_inside_registered_project(context, tmp_path, location):
    service, manager = context
    workspace = Path(manager.ensure_projects(projects(service, ['外层项目']))[0]['path'])
    candidate = workspace if location == 'same' else workspace / '后续'
    original = manager.settings()
    with pytest.raises(ValueError): manager.configure(candidate, tmp_path / '独立归档')
    assert manager.settings() == original
    assert not (workspace / '后续').exists()


def test_work_root_can_be_common_ancestor_sibling_or_pure_group(context, tmp_path):
    service, manager = context
    rows = manager.ensure_projects(projects(service, ['第一项目', '第二项目']))
    group = Path(rows[0]['group_path'])
    ancestor = group.parent
    archive = tmp_path / '独立归档'
    for candidate in (ancestor, ancestor / '同级工作', group):
        assert manager.configure(candidate, archive)['work_root'] == str(candidate)
