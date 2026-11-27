from pathlib import Path
from threading import Event
from time import monotonic, sleep
from zipfile import ZipFile

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from workbox.desktop.desktop_controller import FloatingController
from workbox.desktop.desktop_service import DesktopService


@pytest.fixture
def project(tmp_path):
    service = DesktopService(tmp_path / 'data' / 'workbox.db')
    project_id, = service.receive_text('匿名归档检索系统')['project_ids']
    source = Path(service.workspaces.get_workspace(project_id)['path']) / '匿名使用手册.docx'
    source.write_bytes(b'anonymous manual')
    service.set_status(project_id, '待交付')
    return service, project_id, source


def _wait(until):
    deadline = monotonic() + 25
    while monotonic() < deadline and not until():
        QApplication.processEvents()
        sleep(0.015)
    assert until()


def test_drag_pins_only_unambiguous_current_file_or_delivered_archive(project):
    from workbox.desktop.project_file_drag import resolve_drag_file

    service, project_id, source = project
    assert resolve_drag_file(service, project_id) == source
    other = source.with_name('匿名其他材料.txt')
    other.write_bytes(b'another file')
    with pytest.raises(ValueError, match='多个'):
        resolve_drag_file(service, project_id)
    other.unlink()
    result = service.confirm_delivery(project_id, confirmed=True)
    frozen = Path(result['archive']['path']) / source.name
    assert resolve_drag_file(service, project_id) == frozen
    source.write_bytes(b'edited after handoff')
    assert frozen.read_bytes() == b'anonymous manual'


def test_drag_uses_the_linked_completed_zip_without_extracting(tmp_path, project):
    from workbox.desktop.project_file_drag import resolve_drag_file

    service, project_id, source = project
    source.unlink()
    package = tmp_path / '匿名归档检索系统.zip'
    with ZipFile(package, 'w') as archive:
        archive.writestr('匿名材料.txt', '匿名资料')
    record = service.import_zip(package)['zip']
    service.confirm_delivery(project_id, record['id'], confirmed=True)
    assert resolve_drag_file(service, project_id) == Path(record['path'])


def test_title_drag_offers_copy_and_releases_mime_after_cancel(project, monkeypatch):
    from workbox.desktop import project_file_drag

    service, project_id, source = project
    trace = []

    class Drag:
        def __init__(self, parent):
            trace.append(parent)

        def setMimeData(self, mime):
            trace.append(mime.urls()[0].toLocalFile())

        def exec(self, supported, default):
            trace.append((supported, default))
            return Qt.IgnoreAction

        def deleteLater(self):
            trace.append('released')

    monkeypatch.setattr(project_file_drag, 'QDrag', Drag)
    title = object()
    project_file_drag.drag_project_file(service, project_id, title)
    assert trace == [title, source.as_posix(), (Qt.CopyAction, Qt.CopyAction), 'released']
    assert source.read_bytes() == b'anonymous manual'
    assert service.db.delivery_history()['event_count'] == 0


def test_actual_controller_wires_board_linkage_settings_and_retry(project, tmp_path, monkeypatch):
    from workbox.desktop.compact_ui import ProjectDeliveryDialog

    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    service, project_id, source = project
    server = tmp_path / 'server'
    wechat = tmp_path / 'wechat'
    server.mkdir()
    wechat.mkdir()
    controller = FloatingController(service, tray_enabled=False, project_board_default=True)
    monkeypatch.setattr(ProjectDeliveryDialog, 'exec', lambda *_: pytest.fail('Unexpected dialog'))
    try:
        assert controller.start()
        controller.monitor_timer.stop()
        controller.show_panel()
        panel = controller.panel
        panel.show_delivery_linkage_settings()
        settings = panel.delivery_linkage_page
        settings.server_edit.setText(str(server))
        settings.wechat_edit.setText(str(wechat))
        settings.save_button.click()
        _wait(lambda: settings.save_button.isEnabled() and not controller.busy)
        assert service.delivery_linkage.settings()['server_root'] == str(server)
        server.rmdir()
        panel.show_project_board()
        action = panel.project_board.lanes['待交付'].actions[project_id]
        action.click()
        _wait(lambda: not controller.busy)
        assert project_id in panel.project_board.lanes['已完成'].rows
        assert '服务器目录复制失败' in panel.notice.text()
        assert next(wechat.glob('*/匿名使用手册.docx')).read_bytes() == source.read_bytes()
        assert [job['status'] for job in service.delivery_linkage.pending()] == ['failed']

        server.mkdir()
        panel.show_delivery_linkage_settings()
        settings.retry_button.click()
        _wait(lambda: not controller.busy and not service.delivery_linkage.pending())
        assert next(server.glob('*/匿名使用手册.docx')).read_bytes() == source.read_bytes()
        assert service.db.delivery_history()['event_count'] == 1

        second, = service.receive_text('匿名批量交付系统')['project_ids']
        root = Path(service.workspaces.get_workspace(second)['path'])
        (root / '匿名代码材料.txt').write_bytes(b'anonymous code')
        service.set_status(second, '待交付')
        controller.deliver_projects([second])
        _wait(lambda: not controller.busy)
        assert next(server.glob('*/匿名代码材料.txt')).read_bytes() == b'anonymous code'
        assert service.db.delivery_history()['event_count'] == 2
    finally:
        controller.shutdown()
        controller.dispose_ui()


def test_cancel_after_local_commit_keeps_linkage_pending_without_copy(project, tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    service, project_id, _ = project
    server = tmp_path / 'server'
    server.mkdir()
    service.delivery_linkage.configure(str(server), '')
    controller = FloatingController(service, tray_enabled=False, project_board_default=True)
    planned = Event()
    resume = Event()
    original = service.delivery_linkage.plan_delivery

    def pause(*args):
        jobs = original(*args)
        planned.set()
        assert resume.wait(25)
        return jobs

    monkeypatch.setattr(service.delivery_linkage, 'plan_delivery', pause)
    try:
        assert controller.start()
        controller.monitor_timer.stop()
        controller.show_panel()
        controller.panel.project_board.lanes['待交付'].actions[project_id].click()
        _wait(planned.is_set)
        controller.cancel_jobs()
        resume.set()
        _wait(lambda: not controller.busy)
        assert project_id in controller.panel.project_board.lanes['已完成'].rows
        assert not list(server.iterdir())
        assert [job['status'] for job in service.delivery_linkage.pending()] == ['pending']
        assert service.db.delivery_history()['event_count'] == 1
    finally:
        resume.set()
        controller.shutdown()
        controller.dispose_ui()
