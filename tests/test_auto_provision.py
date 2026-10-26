"""自动建项使用隔离来源，不能把失败收件当成建项或归档成功。"""

from pathlib import Path
from zipfile import ZipFile

import pytest

from desktop_service import DesktopService


@pytest.fixture
def service(tmp_path):
    return DesktopService(tmp_path / 'data' / 'workbox.db')


def ordinary(tmp_path, filename='客户档案系统_代码（C#）.docx'):
    source = tmp_path / filename
    source.write_bytes(b'isolated ordinary content')
    return source


def package(tmp_path, filename='客户档案系统.zip', members=('readme.txt',)):
    source = tmp_path / filename
    with ZipFile(source, 'w') as archive:
        for member in members:
            archive.writestr(member, b'isolated archive content')
    return source


def test_unknown_ordinary_creates_project_and_pending_file(service, tmp_path):
    source = ordinary(tmp_path)
    result = service.receive_file(source)
    project = service.get_project(result['file']['project_id'])
    assert project['name'] == '客户档案系统'
    assert project['status'] == '待交付'
    assert Path(project['workspace']['path']).name == project['name']
    assert Path(result['file']['path']).read_bytes() == source.read_bytes()
    assert service.intake_log.list_recent()[0]['manual'] is False


def test_unknown_plain_stem_and_meaningful_punctuation_are_preserved(service, tmp_path):
    source = ordinary(tmp_path, 'A+B（内部）_用户手册.docx')
    result = service.receive_file(source)
    assert service.get_project(result['file']['project_id'])['name'] == 'A+B（内部）'


def test_repeated_file_and_another_material_reuse_created_project(service, tmp_path):
    source = ordinary(tmp_path)
    service.receive_file(source)
    assert service.receive_file(source)['duplicate'] is True
    service.receive_file(ordinary(tmp_path, '客户档案系统_操作手册.docx'))
    assert service.db.counts()['projects'] == 1


def test_unknown_zip_creates_full_stem_then_watches_and_archives(service, tmp_path):
    source = package(tmp_path, '客户档案系统_交付包.zip', ('a.txt', 'b.txt', 'c.txt', 'd.txt'))
    result = service.receive_file(source)
    assert result['watching'] is True
    assert service.get_project(result['project_id'])['name'] == '客户档案系统_交付包'
    working = Path(result['working_zip_path'])
    assert working.parent == Path(service.workspaces.get_workspace(result['project_id'])['path'])
    assert working.name == source.name and working.read_bytes() == source.read_bytes()
    service.scan_archives(now=0)
    service.scan_archives(now=11)
    assert service.get_project(result['project_id'])['status'] == '已交付'
    assert service.db.counts()['zips'] == 1
    assert service.db.today_delivery_summary()['event_count'] == 1
    assert {row['source_path'] for row in service.snapshots.list_sources()} == {str(source), str(working)}
    service.receive_file(source)
    assert service.db.counts()['projects'] == 1 and source.is_file()


@pytest.mark.parametrize('kind', ['missing', 'directory', 'invalid-zip', 'long-name'])
def test_invalid_source_does_not_create_project(service, tmp_path, kind):
    source = tmp_path / '不存在.txt'
    if kind == 'directory':
        source.mkdir()
    elif kind == 'invalid-zip':
        source = tmp_path / '无效.zip'
        source.write_bytes(b'PK\x03\x04' + b'not a zip' * 10)
    elif kind == 'long-name':
        source = ordinary(tmp_path, '长' * 201 + '.txt')
    with pytest.raises((ValueError, OSError)):
        service.receive_file(source)
    assert service.db.counts()['projects'] == 0
    assert not service.snapshots.list_sources()


def test_broken_local_zip_header_does_not_create_project(service, tmp_path):
    source = package(tmp_path)
    with source.open('r+b') as stream:
        stream.write(b'FAIL')
    with pytest.raises(ValueError, match='ZIP'):
        service.receive_file(source)
    assert service.db.counts()['projects'] == 0


@pytest.mark.parametrize('extension', ['.txt', '.zip'])
def test_duplicate_names_still_require_manual_choice(service, tmp_path, extension):
    service.capture_message('客户档案系统', '甲批')
    service.capture_message('客户档案系统', '乙批')
    source = package(tmp_path) if extension == '.zip' else ordinary(tmp_path, '客户档案系统.txt')
    with pytest.raises(ValueError, match='选择'):
        service.receive_file(source)
    assert service.db.counts()['projects'] == 2


@pytest.mark.parametrize('extension', ['.txt', '.zip'])
def test_archived_name_requires_choice_instead_of_recreation(service, tmp_path, extension):
    service.capture_message('客户档案系统', '已归档批次')
    service.bulk_update_projects([1], archived=True)
    source = package(tmp_path) if extension == '.zip' else ordinary(tmp_path, '客户档案系统.txt')
    assert service.file_routing(source)['requires_choice']
    with pytest.raises(ValueError, match='选择'):
        service.receive_file(source)
    assert service.db.counts()['projects'] == 1


def test_unknown_safe_bundle_creates_one_batch_with_first_name_outer_folder(service, tmp_path):
    source = package(tmp_path, '客户档案系统-2-G.zip',
                     ('合同管理系统/readme.txt', '客户档案系统/a.txt', '客户档案系统/b.txt',
                      '客户档案系统/c.txt', '客户档案系统/d.txt'))
    result = service.receive_file(source)
    projects = sorted(service.list_projects(), key=lambda row: row['id'])
    assert [row['name'] for row in projects] == ['客户档案系统', '合同管理系统']
    assert len({row['batch_id'] for row in projects}) == 1
    for project in projects:
        workspace = service.workspaces.get_workspace(project['id'])
        assert Path(workspace['path']).parent.name == '客户档案系统'
    assert result['watching'] and result['project_id'] is None
    working = Path(result['working_zip_path'])
    assert working.parent == Path(service.workspaces.get_workspace(1)['group_path'])
    assert working.name == source.name and working.read_bytes() == source.read_bytes()
    service.scan_archives(now=0)
    service.scan_archives(now=11)
    assert service.get_project(1)['status'] == '已交付'
    assert service.get_project(2)['status'] == '待交付'
    assert service.db.counts()['zips'] == 1
    assert service.db.today_delivery_summary()['event_count'] == 1
    service.receive_file(source)
    assert service.db.counts()['projects'] == 2


@pytest.mark.parametrize('members', [('客户档案系统/a.txt',),
    ('客户档案系统/a.txt', '合同管理系统/'),
    ('客户档案系统/a.txt', '../合同管理系统/b.txt'),
    ('别的系统/a.txt', '其他名称/b.txt', 'README.txt')])
def test_incomplete_or_unsafe_bundle_does_not_create_pseudo_project(service, tmp_path, members):
    source = package(tmp_path, '客户档案系统-2-G.zip', members)
    service.receive_file(source)
    assert service.db.counts()['projects'] == 0


def test_partial_existing_bundle_does_not_clone_or_move_projects(service, tmp_path):
    service.capture_message('客户档案系统', '原批')
    existing = service.workspaces.get_workspace(1)
    source = package(tmp_path, '客户档案系统-2-G.zip',
                     ('客户档案系统/a.txt', '合同管理系统/b.txt'))
    service.receive_file(source)
    assert service.db.counts()['projects'] == 1
    assert service.workspaces.get_workspace(1) == existing


def test_copy_failure_keeps_explicit_project_and_failed_receipt_for_retry(service, tmp_path, monkeypatch):
    source = ordinary(tmp_path)
    original = service.workspaces.import_file
    def fail(*args, **kwargs):
        raise OSError('isolated copy failure')
    monkeypatch.setattr(service.workspaces, 'import_file', fail)
    with pytest.raises(OSError, match='copy failure'):
        service.receive_file(source)
    assert service.db.counts()['projects'] == 1
    assert service.get_project(1)['status'] == '待开发'
    assert service.intake_log.list_recent()[0]['status'] == 'failed'
    monkeypatch.setattr(service.workspaces, 'import_file', original)
    service.receive_file(source)
    assert service.db.counts()['projects'] == 1


def test_word_only_delivery_calls_project_archive(service, monkeypatch):
    import delivery_archive
    calls = []
    class Archive:
        def __init__(self, owner, workspaces):
            assert owner is service and workspaces is service.workspaces
        def confirm(self, project_id, **kwargs):
            calls.append((project_id, kwargs))
            return {'delivery_id': 99}
    monkeypatch.setattr(delivery_archive, 'ProjectDeliveryArchive', Archive)
    assert service.confirm_delivery(1, confirmed=True) == {'delivery_id': 99}
    assert calls == [(1, dict(confirmed=True, cancel_event=None, progress=None))]


def test_workspace_failure_is_reported_without_false_zip_watch(service, tmp_path, monkeypatch):
    source = package(tmp_path)
    def fail(project_ids):
        raise OSError('isolated workspace failure')
    monkeypatch.setattr(service.workspaces, 'ensure_projects', fail)
    with pytest.raises(RuntimeError, match='workspace failure'):
        service.receive_file(source)
    assert service.db.counts()['projects'] == 1
    assert not service.snapshots.list_sources()


@pytest.mark.parametrize('kind', ['ordinary', 'zip', 'bundle'])
def test_archived_workspace_never_becomes_an_automatic_new_project(service, kind):
    pid = service.capture_message('已归档系统', '旧批')['project_ids'][0]
    directory = Path(service.workspaces.get_workspace(pid)['path'])
    service.bulk_update_projects([pid], archived=True)
    if kind == 'ordinary':
        source = ordinary(directory, 'readme.docx')
    elif kind == 'zip':
        source = package(directory, '其他系统.zip')
    else:
        source = package(directory, '甲系统-2.zip', ('甲系统/a.txt', '乙系统/b.txt'))
    assert service.file_routing(source).get('requires_choice')
    with pytest.raises(ValueError, match='选择'):
        service.receive_file(source)
    assert service.db.counts()['projects'] == 1
    assert not service.snapshots.list_sources()


def test_archive_library_zip_is_rejected_before_automatic_project_creation(service):
    directory = Path(service.workspaces.settings()['archive_root'])
    directory.mkdir(parents=True)
    source = package(directory, '未创建系统.zip')
    with pytest.raises(ValueError, match='归档'):
        service.receive_file(source)
    assert service.db.counts()['projects'] == 0


@pytest.mark.parametrize('bundle', [False, True])
def test_zip_work_copy_failure_can_retry_without_duplicate_projects(service, tmp_path, monkeypatch, bundle):
    import workspace_manager
    source = package(tmp_path, '甲系统-2.zip', ('甲系统/a.txt', '乙系统/b.txt')) if bundle else package(tmp_path)
    original = workspace_manager.copy_verified
    def fail(*args, **kwargs):
        raise OSError('isolated ZIP copy failure')
    monkeypatch.setattr(workspace_manager, 'copy_verified', fail)
    with pytest.raises(OSError, match='ZIP copy failure'):
        service.receive_file(source)
    assert service.db.counts()['projects'] == (2 if bundle else 1)
    assert not service.snapshots.list_sources()
    failure = service.intake_log.list_recent()[0]
    assert failure['status'] == 'failed' and 'ZIP copy failure' in failure['message']
    monkeypatch.setattr(workspace_manager, 'copy_verified', original)
    retried = service.receive_file(source)
    assert Path(retried['working_zip_path']).read_bytes() == source.read_bytes()
    assert service.db.counts()['projects'] == (2 if bundle else 1)


def test_same_named_zip_versions_keep_names_and_reuse_verified_work_copies(service, tmp_path):
    source = package(tmp_path)
    first = service.receive_file(source)
    first_path = Path(first['working_zip_path'])
    original_bytes = first_path.read_bytes()
    package(tmp_path, members=('changed.txt',))
    second = service.receive_file(source)
    second_path = Path(second['working_zip_path'])
    assert second_path != first_path
    assert second_path.name == first_path.name == source.name
    assert first_path.read_bytes() == original_bytes
    assert second_path.read_bytes() == source.read_bytes()
    assert service.receive_file(source)['working_zip_path'] == str(second_path)
    assert service.db.counts()['projects'] == 1


def test_unchanged_work_copy_never_overwrites_a_newer_original_revision(service, tmp_path):
    source = package(tmp_path, members=('a.txt', 'b.txt', 'c.txt', 'd.txt'))
    received = service.receive_file(source)
    package(tmp_path, members=('new-revision.txt',))
    service.scan_archives(now=0)
    service.scan_archives(now=11)
    assert service.get_project(received['project_id'])['status'] == '待交付'
    assert service.db.counts()['zips'] == 1
    assert service.db.today_delivery_summary()['event_count'] == 0
    copied = next(row for row in service.snapshots.list_sources()
                  if row['source_path'] == received['working_zip_path'])
    assert copied['state'] == 'copy-retained'


@pytest.mark.parametrize('bundle', [False, True])
def test_editing_work_copy_starts_independent_stable_monitoring(service, tmp_path, bundle):
    source = package(tmp_path, '甲系统-2.zip', ('甲系统/a.txt', '乙系统/b.txt')) if bundle else package(tmp_path)
    received = service.receive_file(source)
    service.scan_archives(now=0)
    service.scan_archives(now=11)
    working = Path(received['working_zip_path'])
    members = ('甲系统/a.txt', '甲系统/b.txt', '甲系统/c.txt', '甲系统/d.txt', '乙系统/b.txt') if bundle else ('a.txt', 'b.txt', 'c.txt', 'd.txt')
    package(working.parent, working.name, members)
    service.scan_archives(now=20)
    service.scan_archives(now=31)
    assert service.get_project(1)['status'] == '已交付'
    assert service.db.counts()['zips'] == 2
    assert service.db.today_delivery_summary()['event_count'] == 1


def test_explicitly_dropping_work_copy_is_never_suppressed(service, tmp_path):
    source = package(tmp_path, members=('a.txt', 'b.txt', 'c.txt', 'd.txt'))
    received = service.receive_file(source)
    source.unlink()
    service.receive_file(received['working_zip_path'])
    service.scan_archives(now=0)
    service.scan_archives(now=11)
    assert service.get_project(1)['status'] == '已交付'
    assert service.db.counts()['zips'] == 1
    assert service.db.today_delivery_summary()['event_count'] == 1
    with service.db.connect() as connection:
        assert connection.execute('SELECT count(*) FROM zip_work_copies').fetchone()[0] == 0


def test_restart_and_metadata_only_change_preserve_work_copy_binding(service, tmp_path):
    import os
    source = package(tmp_path, members=('a.txt', 'b.txt', 'c.txt', 'd.txt'))
    received = service.receive_file(source)
    package(tmp_path, members=('new-revision.txt',))
    working = Path(received['working_zip_path'])
    info = working.stat()
    os.utime(working, ns=(info.st_atime_ns, info.st_mtime_ns + 1000000000))
    reopened = DesktopService(service.db.path)
    reopened.scan_archives(now=0)
    reopened.scan_archives(now=11)
    assert reopened.get_project(1)['status'] == '待交付'
    assert reopened.db.counts()['zips'] == 1
    assert reopened.db.today_delivery_summary()['event_count'] == 0
    copied = next(row for row in reopened.snapshots.list_sources()
                  if row['source_path'] == str(working))
    assert copied['state'] == 'copy-retained'


def test_new_same_named_copy_does_not_suppress_edits_to_older_work_copy(service, tmp_path):
    source = package(tmp_path)
    first = service.receive_file(source)
    service.scan_archives(now=0)
    service.scan_archives(now=11)
    older_working = Path(first['working_zip_path'])
    package(older_working.parent, older_working.name, ('a.txt', 'b.txt', 'c.txt', 'd.txt'))
    package(tmp_path, members=('new-a.txt', 'new-b.txt'))
    second = service.receive_file(source)
    assert second['working_zip_path'] != first['working_zip_path']
    service.scan_archives(now=20)
    service.scan_archives(now=31)
    sources = {row['source_path']: row for row in service.snapshots.list_sources()}
    assert sources[first['working_zip_path']]['state'] == 'archived'
    assert sources[second['working_zip_path']]['state'] == 'copy-retained'
    assert service.db.counts()['zips'] == 3
    assert service.db.today_delivery_summary()['event_count'] == 1


def test_restoring_project_clears_old_pause_error_on_unchanged_work_copy(service, tmp_path):
    source = package(tmp_path)
    received = service.receive_file(source)
    service.bulk_update_projects([1], archived=True)
    service.scan_archives(now=0)
    service.bulk_update_projects([1], archived=False)
    service.scan_archives(now=11)
    copied = next(row for row in service.snapshots.list_sources()
                  if row['source_path'] == received['working_zip_path'])
    assert copied['state'] == 'copy-retained' and not copied['last_error']
