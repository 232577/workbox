import hashlib
import threading
from concurrent.futures import CancelledError
from pathlib import Path
from zipfile import ZipFile

import pytest

from app import WorkBox
from workspace_manager import WorkspaceManager, copy_verified


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
    assert second['file']['path'] != first['file']['path']
    assert hashlib.sha256(b'second').hexdigest()[:8] in Path(second['file']['path']).name
    inside = Path(manager.get_workspace(pid)['path']) / '内部.txt'
    inside.write_bytes(b'inside')
    registered = manager.import_file(inside, pid)
    assert registered['file']['path'] == str(inside)
    service.bulk_update_projects([pid], archived=True)
    assert manager.list_workspaces() == []
    assert len(manager.list_workspaces(include_archived=True)) == 1
    with pytest.raises(ValueError): manager.import_file(source, pid)
    with pytest.raises((ValueError, KeyError)): manager.import_file(source, None)


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
    import workspace_manager
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
