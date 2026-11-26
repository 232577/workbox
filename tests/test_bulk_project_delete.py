from pathlib import Path
from time import monotonic

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from workbox.desktop.desktop_controller import FloatingController
from workbox.desktop.desktop_service import DesktopService


@pytest.fixture
def prepared(tmp_path):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    service = DesktopService(tmp_path / 'data' / 'workbox.db')
    controller = FloatingController(service, tray_enabled=False)
    assert controller.start()
    controller.monitor_timer.stop()
    try:
        ids = service.receive_text('1. 匿名首项管理系统\n2. 匿名次项管理系统\n3. 匿名其他管理系统')['project_ids']
        material = tmp_path / '匿名首项管理系统.docx'
        material.write_bytes(b'anonymous material')
        service.receive_file(material, project_id=ids[0])
        controller.refresh()
        yield controller, ids
    finally:
        controller.shutdown()


def test_context_delete_targets_only_the_selection_and_retains_files(prepared, monkeypatch):
    controller, ids = prepared
    panel = controller.panel
    service = controller.service
    original = service.get_project(ids[0])
    material = Path(original['workspace']['path']) / '匿名首项管理系统.docx'
    for row in range(panel.project_list.count()):
        item = panel.project_list.item(row)
        item.setSelected(item.data(Qt.UserRole) in ids[:2])
    menu = panel.project_context_menu(ids[0])
    delete = next(action for action in menu.actions() if action.text() == '批量删除（2 项）…')
    assert all(action.text() != '删除项目…' for action in menu.actions())
    prompts = []

    def answer(*args):
        prompts.append(args)
        return QMessageBox.No if len(prompts) == 1 else QMessageBox.Yes

    monkeypatch.setattr(QMessageBox, 'question', answer)
    delete.trigger()
    assert not controller.busy and not service.list_projects(deleted=True)
    delete.trigger()
    deadline = monotonic() + 5
    while controller.busy and monotonic() < deadline:
        QTest.qWait(10)
    assert not controller.busy
    assert all('2 个项目' in args[2] and args[-1] == QMessageBox.No for args in prompts)
    assert {p['id'] for p in service.list_projects(deleted=True)} == set(ids[:2])
    assert [p['id'] for p in service.list_projects()] == ids[2:]
    assert material.read_bytes() == b'anonymous material'


def test_missing_target_rolls_back_batch_and_restore_keeps_old_archive_state(prepared):
    controller, ids = prepared
    service = controller.service
    with pytest.raises(KeyError):
        service.delete_projects([ids[0], 987654], confirmed=True)
    assert not service.list_projects(deleted=True)
    service.bulk_update_projects(ids[1:2], archived=True)
    service.delete_projects(ids[:2], confirmed=True)
    service.restore_deleted_project(ids[0])
    service.restore_deleted_project(ids[1])
    assert service.get_project(ids[0])['archived_at'] is None
    assert service.get_project(ids[1])['archived_at']
