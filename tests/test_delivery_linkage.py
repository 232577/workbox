from pathlib import Path
from threading import Event
from zipfile import ZipFile

import pytest

from workbox.desktop.desktop_service import DesktopService
from workbox.storage.delivery_linkage import DeliveryLinkage


@pytest.fixture
def delivery(tmp_path):
    service = DesktopService(tmp_path / 'data' / 'anonymous.db')
    project_id, = service.receive_text('匿名流程系统')['project_ids']
    material = Path(service.workspaces.get_workspace(project_id)['path']) / '使用手册.docx'
    material.write_bytes(b'anonymous exact original')
    service.set_status(project_id, '待交付')
    result = service.confirm_delivery(project_id, confirmed=True)
    return service, project_id, result, material


def test_opted_in_targets_receive_only_the_frozen_artifact_once(tmp_path, delivery):
    service, project_id, result, material = delivery
    server = tmp_path / 'server'
    wechat = tmp_path / 'wechat'
    server.mkdir()
    wechat.mkdir()
    linkage = DeliveryLinkage(service)
    assert linkage.settings() == {'server_root': '', 'wechat_root': ''}
    assert linkage.configure(str(server), str(wechat)) == {
        'server_root': str(server), 'wechat_root': str(wechat)}
    material.write_bytes(b'changed after delivery')
    receipts = linkage.process_delivery(project_id, result)
    assert [item['target'] for item in receipts] == ['server', 'wechat']
    assert [item['status'] for item in receipts] == ['completed', 'completed']
    assert linkage.process_delivery(project_id, result) == receipts
    assert linkage.pending() == []
    assert service.db.delivery_history()['event_count'] == 1
    for root, item in zip((server, wechat), receipts, strict=True):
        folder, = root.iterdir()
        assert folder == Path(item['path'])
        assert (folder / material.name).read_bytes() == b'anonymous exact original'


def test_opt_out_and_managed_boundary_checks_are_durable(tmp_path, delivery):
    service, project_id, result, material = delivery
    linkage = DeliveryLinkage(service)
    with pytest.raises(ValueError, match='工作箱'):
        linkage.configure(str(material.parent), '')
    with pytest.raises(ValueError, match='工作箱'):
        linkage.configure('', result['archive']['path'])
    assert linkage.process_delivery(project_id, result) == []
    new_root = tmp_path / 'new-destination'
    new_root.mkdir()
    linkage.configure(str(new_root), '')
    assert linkage.process_delivery(project_id, result) == []
    assert list(new_root.iterdir()) == []


def test_cancelled_destination_retry_pins_original_root_and_respects_foreign_folder(tmp_path, delivery):
    service, project_id, result, material = delivery
    server = tmp_path / 'server'
    new_root = tmp_path / 'new-server'
    server.mkdir()
    new_root.mkdir()
    linkage = DeliveryLinkage(service)
    linkage.configure(str(server), '')
    cancel = Event()
    cancel.set()
    (cancelled,) = linkage.process_delivery(project_id, result, cancel_event=cancel)
    assert cancelled['status'] == 'cancelled'
    assert server.is_dir() and not list(server.iterdir())
    assert service.get_project(project_id)['status'] == '已交付'

    foreign = Path(cancelled['path'])
    foreign.mkdir()
    marker = foreign / 'other.txt'
    marker.write_bytes(b'foreign')
    restarted = DeliveryLinkage(DesktopService(service.db.path))
    restarted.configure(str(new_root), '')
    assert restarted.process_delivery(project_id, result) == [cancelled]
    failed = restarted.retry(result['event_id'], 'server')
    assert failed['status'] == 'failed'
    assert restarted.pending() == [failed]
    assert marker.read_bytes() == b'foreign'
    marker.unlink()
    foreign.rmdir()
    done = restarted.retry(result['event_id'], 'server')
    assert done['status'] == 'completed' and Path(done['path']).parent == server
    assert (Path(done['path']) / material.name).read_bytes() == b'anonymous exact original'
    assert list(new_root.iterdir()) == []
    assert restarted.pending() == []


def test_modified_archive_cannot_publish_material(tmp_path, delivery):
    service, project_id, result, material = delivery
    root = tmp_path / 'server'
    root.mkdir()
    linkage = DeliveryLinkage(service)
    linkage.configure(str(root), '')
    archive = Path(result['archive']['path']) / material.name
    archive.write_bytes(b'forged archive data')
    (failed,) = linkage.process_delivery(project_id, result)
    assert failed['status'] == 'failed'
    assert not Path(failed['path']).exists()
    archive.write_bytes(b'anonymous exact original')
    done = linkage.retry(result['event_id'], 'server')
    assert done['status'] == 'completed'
    assert (Path(done['path']) / material.name).read_bytes() == b'anonymous exact original'


def test_interrupt_during_copy_releases_temporary_file_and_can_resume(tmp_path, delivery):
    service, project_id, result, material = delivery
    root = tmp_path / 'server'
    root.mkdir()
    linkage = DeliveryLinkage(service)
    linkage.configure(str(root), '')
    cancel = Event()

    (stopped,) = linkage.process_delivery(project_id, result, cancel_event=cancel,
                                          progress=lambda *_: cancel.set())

    assert stopped['status'] == 'cancelled'
    assert not Path(stopped['path']).exists()
    stage, = root.iterdir()
    assert stage.name.startswith('.workbox-pending-') and not list(stage.iterdir())
    done = linkage.retry(result['event_id'], 'server')
    assert done['status'] == 'completed'
    assert not stage.exists()
    assert (Path(done['path']) / material.name).read_bytes() == b'anonymous exact original'


def test_zip_uses_registered_archive_and_preserves_origin_marker(tmp_path):
    service = DesktopService(tmp_path / 'data' / 'anonymous.db')
    project_id, = service.receive_text('匿名流程系统')['project_ids']
    package = tmp_path / '匿名流程系统.zip'
    with ZipFile(package, 'w') as zip_file:
        zip_file.writestr('readme.txt', 'anonymous original')
    origin = b'[ZoneTransfer]\r\nZoneId=3\r\n'
    Path(f'{package}:Zone.Identifier').write_bytes(origin)
    saved_zip = service.import_zip(package)['zip']
    result = service.confirm_delivery(project_id, saved_zip['id'], confirmed=True)
    root = tmp_path / 'outbox'
    root.mkdir()
    linkage = DeliveryLinkage(service)
    linkage.configure('', str(root))

    (done,) = linkage.process_delivery(project_id, result)

    assert done['status'] == 'completed'
    copied, = Path(done['path']).iterdir()
    assert copied.name == Path(saved_zip['path']).name
    assert copied.read_bytes() == package.read_bytes()
    assert Path(f'{copied}:Zone.Identifier').read_bytes() == origin
