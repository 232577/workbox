from pathlib import Path
from threading import Event
from zipfile import ZipFile
import os
import subprocess

import pytest

from desktop_service import DesktopService


@pytest.fixture
def service(tmp_path):
    return DesktopService(tmp_path / 'data' / 'workbox.db')


def intake(service, path, **kwargs):
    from folder_intake import FolderIntake
    return FolderIntake(service).receive(path, **kwargs)


def file(root, name, content=b'isolated'):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def project(service, name):
    return service.capture_message(name, name)['project_ids'][0]


def test_existing_folder_preserves_tree_and_distinct_identical_files(service, tmp_path):
    pid = project(service, '甲系统')
    root = tmp_path / 'source' / '甲系统'
    file(root, '文档/readme.txt')
    file(root, '资源/readme.txt')
    result = intake(service, root)
    target = Path(service.workspaces.get_workspace(pid)['path'])
    assert result['kind'] == 'folder_result'
    assert result['copied'] == 2 and result['failed'] == 0
    assert (target / '文档/readme.txt').read_bytes() == b'isolated'
    assert (target / '资源/readme.txt').read_bytes() == b'isolated'
    assert service.get_project(pid)['status'] == '待交付'
    assert intake(service, root)['duplicate'] == 2
    assert len(service.intake_log.list_recent()) == 4


def test_unknown_folder_creates_one_project_for_generic_material_names(service, tmp_path):
    root = tmp_path / '新建档案系统'
    file(root, '操作手册.docx')
    file(root, 'images/a.png', b'picture')
    result = intake(service, root)
    assert result['copied'] == 2
    assert [p['name'] for p in service.list_projects()] == ['新建档案系统']
    assert (root / '操作手册.docx').exists()


def test_multiple_project_subfolders_route_separately(service, tmp_path):
    a, b = project(service, '甲系统'), project(service, '乙系统')
    root = tmp_path / '本次材料'
    file(root, '甲系统/文档/readme.txt', b'a')
    file(root, '乙系统/readme.txt', b'b')
    result = intake(service, root)
    assert result['copied'] == 2
    assert (Path(service.workspaces.get_workspace(a)['path']) / '文档/readme.txt').read_bytes() == b'a'
    assert (Path(service.workspaces.get_workspace(b)['path']) / 'readme.txt').read_bytes() == b'b'
    assert len(service.list_projects()) == 2


def test_first_project_named_group_routes_each_project_child(service, tmp_path):
    a, b = project(service, '甲系统'), project(service, '乙系统')
    root = tmp_path / 'source' / '甲系统'
    file(root, '甲系统/文档/readme.txt', b'a')
    file(root, '乙系统/readme.txt', b'b')
    result = intake(service, root)
    assert result['copied'] == 2 and result['needs_choice'] == 0
    assert (Path(service.workspaces.get_workspace(a)['path']) / '文档/readme.txt').read_bytes() == b'a'
    assert (Path(service.workspaces.get_workspace(b)['path']) / 'readme.txt').read_bytes() == b'b'
    assert intake(service, root)['duplicate'] == 2


def test_filename_routes_existing_project_and_unknown_material_waits(service, tmp_path):
    pid = project(service, '甲系统')
    root = tmp_path / '本次材料'
    file(root, '甲系统_操作手册.docx')
    file(root, '说明.txt', b'unknown')
    result = intake(service, root)
    assert result['copied'] == 1 and result['needs_choice'] == 1
    assert len(service.list_projects()) == 1
    assert service.get_project(pid)['status'] == '待交付'


@pytest.mark.parametrize('protected', ['archived', 'deleted', 'duplicate'])
def test_protected_or_ambiguous_folder_never_guesses(service, tmp_path, protected):
    pid = project(service, '甲系统')
    if protected == 'archived':
        service.bulk_update_projects([pid], archived=True)
    elif protected == 'deleted':
        service.delete_project(pid, confirmed=True)
    else:
        service.capture_message('甲系统', '另一个批次')
    root = tmp_path / 'source' / '甲系统'
    file(root, 'readme.txt')
    result = intake(service, root)
    assert result['needs_choice'] == 1 and result['copied'] == 0
    assert len(service.list_projects(archived=None, deleted=None)) == (2 if protected == 'duplicate' else 1)


def test_conflicting_filename_waits_and_same_name_never_overwrites(service, tmp_path):
    pid = project(service, '甲系统')
    project(service, '乙系统')
    root = tmp_path / 'source' / '甲系统'
    file(root, 'readme.txt', b'one')
    file(root, '乙系统_代码.docx')
    assert intake(service, root)['needs_choice'] == 1
    file(root, 'readme.txt', b'two')
    assert intake(service, root)['copied'] == 1
    target = Path(service.workspaces.get_workspace(pid)['path'])
    assert (target / 'readme.txt').read_bytes() == b'one'
    assert sorted(p.read_bytes() for p in target.glob('*.txt')) == [b'one', b'two']


def test_bad_zip_is_failure_valid_zip_keeps_stable_watch(service, tmp_path):
    pid = project(service, '甲系统')
    root = tmp_path / 'source' / '甲系统'
    file(root, 'broken.zip', b'not zip')
    with ZipFile(root / '甲系统.zip', 'w') as archive:
        for name in ['a', 'b', 'c', 'd']:
            archive.writestr(name, b'isolated')
    result = intake(service, root)
    assert result['watching'] == 1 and result['failed'] == 1
    assert service.get_project(pid)['status'] != '已交付'
    service.scan_archives(now=0)
    service.scan_archives(now=11)
    assert service.get_project(pid)['status'] == '已交付'


def test_new_folder_with_only_bad_zip_does_not_create_project(service, tmp_path):
    root = tmp_path / '新建系统'
    file(root, 'broken.zip', b'not zip')
    result = intake(service, root)
    assert result['failed'] == 1 and not service.list_projects()


def test_overlap_rejected_and_bounded_scan_does_not_import_partial_tree(service, tmp_path):
    from folder_intake import FolderIntake
    project(service, '甲系统')
    assert intake(service, service.workspaces.settings()['work_root'])['failed'] == 1
    root = tmp_path / '新建系统'
    file(root, 'a.txt')
    file(root, 'b.txt')
    result = FolderIntake(service, max_files=1).receive(root)
    assert result['failed'] == 1 and result['copied'] == 0
    assert len(service.list_projects()) == 1


@pytest.mark.parametrize('limits', [{'max_bytes': 1}, {'max_depth': 1}, {'max_entries': 1}])
def test_scan_limits_prevent_partial_writes(service, tmp_path, limits):
    from folder_intake import FolderIntake
    root = tmp_path / '新建系统'
    file(root, 'a/b/file.txt', b'oversize')
    result = FolderIntake(service, **limits).receive(root)
    assert result['failed'] == 1 and result['copied'] == 0
    assert not service.list_projects()


def test_manual_zip_retry_preserves_nested_filename_and_stable_watch(service, tmp_path):
    from folder_intake import FolderIntake
    pid = project(service, '甲系统')
    root = tmp_path / '本次材料'
    file(root, '甲系统_操作手册.docx')
    (root / '其他').mkdir()
    source = root / '其他' / '交付.zip'
    with ZipFile(source, 'w') as archive:
        archive.writestr('readme.txt', b'isolated')
    result = intake(service, root)
    unresolved = next(item for item in result['items'] if item['status'] == 'needs_choice')
    retried = FolderIntake(service).receive_item(unresolved, pid)
    assert retried['watching'] == 1
    target = Path(service.workspaces.get_workspace(pid)['path']) / '其他' / '交付.zip'
    assert target.read_bytes() == source.read_bytes()
    assert service.get_project(pid)['status'] == '待交付'


def test_queued_project_archival_rechecks_before_each_copy(service, tmp_path):
    pid = project(service, '甲系统')
    root = tmp_path / 'source' / '甲系统'
    file(root, 'a.txt', b'a')
    file(root, 'b.txt', b'b')
    def progress(done, total):
        if done == 1:
            service.bulk_update_projects([pid], archived=True)
    result = intake(service, root, progress=progress)
    assert result['copied'] == 1 and result['needs_choice'] == 1
    target = Path(service.workspaces.get_workspace(pid)['path'])
    assert (target / 'a.txt').exists() and not (target / 'b.txt').exists()


def test_cancellation_keeps_successful_items_and_sources(service, tmp_path):
    root = tmp_path / '甲系统'
    file(root, 'a.txt', b'a')
    file(root, 'b.txt', b'b')
    event = Event()
    def progress(done, total):
        if done == 1:
            event.set()
    result = intake(service, root, cancel_event=event, progress=progress)
    assert result['cancelled'] is True and result['copied'] == 1
    assert (root / 'a.txt').exists() and (root / 'b.txt').exists()


def test_import_file_rejects_relative_traversal(service, tmp_path):
    pid = project(service, '甲系统')
    source = file(tmp_path, 'input.txt')
    with pytest.raises(ValueError):
        service.workspaces.import_file(source, pid, relative_path='../outside.txt')


def test_same_relative_file_from_different_source_is_duplicate(service, tmp_path):
    project(service, '甲系统')
    first = tmp_path / 'one' / '甲系统'
    second = tmp_path / 'two' / '甲系统'
    file(first, '文档/readme.txt')
    file(second, '文档/readme.txt')
    assert intake(service, first)['copied'] == 1
    assert intake(service, second)['duplicate'] == 1


def test_manual_retry_preserves_subdirectories_without_reimporting_success(service, tmp_path):
    from folder_intake import FolderIntake
    a, b = project(service, '甲系统'), project(service, '乙系统')
    root = tmp_path / '本次材料'
    file(root, '甲系统_操作手册.docx')
    source = file(root, '文档/说明.txt', b'manual')
    first = intake(service, root)
    unresolved = next(item for item in first['items'] if item['status'] == 'needs_choice')
    result = FolderIntake(service).receive_item(unresolved, b)
    assert result['copied'] == 1 and result['manual_choice_attempted'] is True
    assert (Path(service.workspaces.get_workspace(b)['path']) / '文档/说明.txt').read_bytes() == b'manual'
    assert result['items'][0]['source_path'] == str(source)
    assert [event['project_id'] for event in service.intake_log.list_recent() if event['status'] == 'copied'].count(a) == 1
    assert service.intake_log.list_recent()[0]['manual'] is True


def test_manual_group_retry_preserves_two_files_and_keeps_previous_success(service, tmp_path):
    from folder_intake import FolderIntake
    pid = project(service, '甲系统')
    root = tmp_path / '本次材料'
    file(root, '甲系统_操作手册.docx')
    file(root, '文档/a.txt', b'a')
    file(root, '文档/b.txt', b'b')
    first = intake(service, root)
    pending = [item for item in first['items'] if item['status'] == 'needs_choice']
    result = FolderIntake(service).receive_items(pending, pid)
    assert result['copied'] == 2 and result['manual_choice_attempted'] is True
    target = Path(service.workspaces.get_workspace(pid)['path'])
    assert (target / '文档/a.txt').read_bytes() == b'a'
    assert (target / '文档/b.txt').read_bytes() == b'b'
    with service.db.connect() as connection:
        assert connection.execute('SELECT count(*) FROM work_files').fetchone()[0] == 3


def test_manual_group_cancellation_retains_completed_file(service, tmp_path):
    from folder_intake import FolderIntake
    pid = project(service, '甲系统')
    root = tmp_path / '本次材料'
    file(root, '甲系统_操作手册.docx')
    file(root, '文档/a.txt', b'a')
    file(root, '文档/b.txt', b'b')
    pending = [item for item in intake(service, root)['items'] if item['status'] == 'needs_choice']
    event = Event()
    def progress(done, total):
        if done == 1:
            event.set()
    result = FolderIntake(service).receive_items(pending, pid, cancel_event=event, progress=progress)
    assert result['copied'] == 1 and result['cancelled'] is True
    target = Path(service.workspaces.get_workspace(pid)['path'])
    assert (target / '文档/a.txt').exists() and not (target / '文档/b.txt').exists()


@pytest.mark.parametrize('tamper', ['outside', 'traversal', 'renamed_source'])
def test_manual_retry_revalidates_paths(service, tmp_path, tamper):
    from folder_intake import FolderIntake
    pid = project(service, '甲系统')
    root = tmp_path / '本次材料'
    file(root, '甲系统_操作手册.docx')
    source = file(root, '文档/说明.txt')
    first = intake(service, root)
    unresolved = next(item for item in first['items'] if item['status'] == 'needs_choice')
    if tamper == 'outside':
        unresolved['source_path'] = str(file(tmp_path, 'outside.txt'))
    elif tamper == 'traversal':
        unresolved['relative_path'] = '../outside.txt'
    else:
        source.rename(root / 'renamed.txt')
    result = FolderIntake(service).receive_item(unresolved, pid)
    assert result['failed'] == 1 and result['copied'] == 0
    assert result['manual_choice_attempted'] is True


def test_folder_bundle_creates_member_projects_without_outer_project(service, tmp_path):
    root = tmp_path / '本次交付'
    root.mkdir()
    with ZipFile(root / '甲系统-2.zip', 'w') as archive:
        archive.writestr('甲系统/a.txt', b'a')
        archive.writestr('乙系统/b.txt', b'b')
    result = intake(service, root)
    assert result['watching'] == 1 and result['failed'] == 0
    assert sorted(p['name'] for p in service.list_projects()) == ['乙系统', '甲系统']
    assert len(result['created_project_ids']) == 2 and len(result['project_ids']) == 2


def test_bundle_inside_single_project_folder_needs_choice(service, tmp_path):
    project(service, '丙系统')
    root = tmp_path / 'source' / '丙系统'
    root.mkdir(parents=True)
    with ZipFile(root / '甲系统-2.zip', 'w') as archive:
        archive.writestr('甲系统/a.txt', b'a')
        archive.writestr('乙系统/b.txt', b'b')
    result = intake(service, root)
    assert result['needs_choice'] == 1 and result['watching'] == 0
    assert len(service.list_projects()) == 1


def test_unknown_existing_destination_folder_requires_choice(service, tmp_path):
    root = tmp_path / 'source' / '新建系统'
    file(root, 'readme.txt')
    target = Path(service.workspaces.settings()['work_root']) / '新建系统'
    file(target, 'keep.txt', b'existing')
    result = intake(service, root)
    assert result['needs_choice'] == 1 and result['copied'] == 0
    assert not service.list_projects()
    assert (target / 'keep.txt').read_bytes() == b'existing'


def test_symbolic_directory_is_rejected_before_copy(service, tmp_path):
    root = tmp_path / '新建系统'
    file(root, 'first.txt')
    outside = tmp_path / 'outside'
    file(outside, 'secret.txt')
    try:
        (root / 'linked').symlink_to(outside, target_is_directory=True)
    except OSError:
        if os.name != 'nt':
            pytest.skip('当前账户没有创建符号链接权限')
        created = subprocess.run(['cmd', '/c', 'mklink', '/J', str(root / 'linked'), str(outside)], capture_output=True)
        if created.returncode:
            pytest.skip('当前账户不能创建符号链接或目录联接')
    try:
        result = intake(service, root)
        assert result['failed'] == 1 and result['copied'] == 0
        assert not service.list_projects()
    finally:
        # 删除链接本身，源目录及其文件保持不变。
        os.rmdir(root / 'linked')
