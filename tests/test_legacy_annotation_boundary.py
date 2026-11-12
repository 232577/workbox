"""Legacy ZIP APIs must not inherit annotation matching without full routing."""

import os
from pathlib import Path
from zipfile import ZipFile

import pytest

from app import WorkBox
from managed_archive import ManagedArchive


@pytest.fixture
def legacy_material(tmp_path):
    service = WorkBox(tmp_path / 'data' / 'workbox.db')
    ids = service.capture_message(
        '1. 匿名资料系统\n2. 匿名资料系统_移动端', '匿名隔离批次',
    )['project_ids']
    service.delete_project(ids[1], confirmed=True)
    source = tmp_path / '匿名资料系统_移动端_任意客户附注.zip'
    with ZipFile(source, 'w') as archive:
        archive.writestr('anonymous.txt', b'anonymous-material')
    return service, ids[0], source


def test_legacy_reference_import_does_not_route_deleted_long_name_to_active_prefix(legacy_material):
    service, active_id, source = legacy_material
    original = source.read_bytes()

    result = service.import_zip(source)

    assert result['candidates'] == []
    assert result['zip']['project_id'] is None
    assert service.get_project(active_id)['status'] == '待开发'
    assert source.read_bytes() == original
    assert len(service.list_projects(archived=None, deleted=None)) == 2


@pytest.mark.skipif(os.name != 'nt', reason='Managed ZIP movement requires Windows leases')
def test_legacy_managed_import_does_not_route_deleted_long_name_to_active_prefix(legacy_material, tmp_path):
    service, active_id, source = legacy_material
    original = source.read_bytes()
    manager = ManagedArchive(service, tmp_path / 'data' / 'packages')

    result = manager.import_zip(source)

    assert result['candidates'] == []
    assert result['zip']['project_id'] is None
    assert service.get_project(active_id)['status'] == '待开发'
    destination = Path(result['zip']['path'])
    assert destination.is_relative_to(manager.root / '待关联')
    assert destination.read_bytes() == original
    assert not source.exists()
    assert len(service.list_projects(archived=None, deleted=None)) == 2
