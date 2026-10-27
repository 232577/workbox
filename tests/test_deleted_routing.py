"""删除项目的名称与工作目录仍有明确的收件边界。"""

from pathlib import Path
from zipfile import ZipFile

import pytest

from desktop_service import DesktopService


@pytest.fixture
def service(tmp_path):
    return DesktopService(tmp_path / 'data' / 'workbox.db')


def add_project(service, name, batch='隔离批次'):
    return service.capture_message(name, batch)['project_ids'][0]


def source_file(tmp_path, filename='图像分析系统_申请表重新下载.txt'):
    path = tmp_path / filename
    path.write_bytes(b'isolated content')
    return path


def test_deleted_misnamed_project_does_not_shadow_correct_live_material_match(service, tmp_path):
    correct = add_project(service, '图像分析系统')
    mistaken = add_project(service, '图像分析系统_申请表重新下载', '误收件批次')
    service.delete_project(mistaken, confirmed=True)
    source = source_file(tmp_path)
    route = service.file_routing(source)
    assert [row['id'] for row in route['candidates']] == [correct]
    assert not route.get('requires_choice')
    result = service.receive_file(source)
    assert result['file']['project_id'] == correct
    assert not result['created_project_ids']
    assert len(service.list_projects(archived=None, deleted=None)) == 2


@pytest.mark.parametrize('suffix', ['_申请表重新下载.txt', '.zip'])
def test_deleted_only_name_requires_choice_and_never_recreates(service, tmp_path, suffix):
    removed = add_project(service, '图像分析系统')
    service.delete_project(removed, confirmed=True)
    source = tmp_path / ('图像分析系统' + suffix)
    if suffix.endswith('.zip'):
        with ZipFile(source, 'w') as archive:
            archive.writestr('readme.txt', b'isolated')
    else:
        source.write_bytes(b'isolated')
    route = service.file_routing(source)
    assert route.get('requires_choice') is True
    assert not route['candidates']
    with pytest.raises(ValueError, match='选择'):
        service.receive_file(source)
    assert len(service.list_projects(archived=None, deleted=None)) == 1
    assert service.intake_log.list_recent()[0]['status'] == 'needs-choice'
    assert source.is_file()


def test_deleted_workspace_boundary_precedes_live_name(service):
    live = add_project(service, '图像分析系统')
    removed = add_project(service, '旧文档系统', '旧批次')
    source = Path(service.workspaces.get_workspace(removed)['path']) / '图像分析系统_申请表重新下载.txt'
    source.write_bytes(b'isolated')
    service.delete_project(removed, confirmed=True)
    assert service.file_routing(source).get('requires_choice') is True
    with pytest.raises(ValueError, match='选择'):
        service.receive_file(source)
    result = service.receive_file(source, live, manual=True)
    assert result['file']['project_id'] == live


def test_archived_material_full_name_remains_boundary_before_short_live_name(service, tmp_path):
    add_project(service, '图像分析系统')
    archived = add_project(service, '图像分析系统_申请表重新下载', '旧批次')
    service.bulk_update_projects([archived], archived=True)
    route = service.file_routing(source_file(tmp_path))
    assert route.get('requires_choice') is True
    assert not route['candidates']


def test_deleted_bundle_member_cannot_be_provisioned(service, tmp_path):
    removed = add_project(service, '图像分析系统')
    service.delete_project(removed, confirmed=True)
    source = tmp_path / '图像分析系统-2.zip'
    with ZipFile(source, 'w') as archive:
        archive.writestr('图像分析系统/readme.txt', b'isolated')
        archive.writestr('客户档案系统/readme.txt', b'isolated')
    service.receive_file(source)
    assert len(service.list_projects(archived=None, deleted=None)) == 1


def test_unknown_directory_conflict_stays_unadopted_after_delete(service, tmp_path):
    removed = add_project(service, '旧文档系统')
    protected = Path(service.workspaces.get_workspace(removed)['path'])
    service.delete_project(removed, confirmed=True)
    with pytest.raises(ValueError):
        service.workspaces.configure(protected, tmp_path / 'other-archives')
    assert service.file_routing(protected / '未知材料.txt').get('requires_choice') is True


def test_direct_zip_import_respects_deleted_project_choice_boundary(service, tmp_path):
    removed = add_project(service, '图像分析系统')
    service.delete_project(removed, confirmed=True)
    source = tmp_path / '图像分析系统.zip'
    with ZipFile(source, 'w') as archive:
        archive.writestr('readme.txt', b'isolated')
    with pytest.raises(ValueError, match='选择'):
        service.import_zip(source)
    assert not service.snapshots.list_sources()


def test_restore_deleted_project_restores_automatic_material_routing(service, tmp_path):
    removed = add_project(service, '图像分析系统')
    deletion = service.delete_project(removed, confirmed=True)
    assert deletion['project_id'] == removed and deletion['deleted'] is True and deletion['changed'] is True
    restoration = service.restore_deleted_project(removed)
    assert restoration['project_id'] == removed and restoration['deleted'] is False and restoration['changed'] is True
    source = source_file(tmp_path)
    route = service.file_routing(source)
    assert [row['id'] for row in route['candidates']] == [removed]
    assert not route.get('requires_choice')
