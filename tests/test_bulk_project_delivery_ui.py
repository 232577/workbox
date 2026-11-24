import time
from pathlib import Path
from threading import Event

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from workbox.desktop.desktop_controller import FloatingController
from workbox.desktop.desktop_service import DesktopService


@pytest.fixture
def prepared_controller(tmp_path):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    service = DesktopService(tmp_path / 'data' / 'workbox.db')
    controller = FloatingController(service, tray_enabled=False)
    assert controller.start()
    controller.monitor_timer.stop()
    try:
        names = ('甲测试项目', '乙测试项目', '丙测试项目')
        project_ids = service.receive_text('\n'.join(f'{i}. {name}' for i, name in enumerate(names, 1)))['project_ids']
        originals = {}
        for project_id, name in zip(project_ids, names):
            original = tmp_path / f'{name}.docx'
            original.write_bytes(f'fixture {project_id}'.encode())
            service.receive_file(original, project_id=project_id)
            originals[project_id] = original
        controller.refresh()
        controller.show_panel()
        panel = controller.panel
        panel.bulk_mode.setChecked(True)
        for index in range(panel.project_list.count()):
            row = panel.project_list.item(index)
            row.setSelected(row.data(Qt.UserRole) in project_ids[:2])
        option = panel.bulk_action.findData('deliver')
        assert option >= 0, '批量操作应提供设为已交付'
        panel.bulk_action.setCurrentIndex(option)
        yield controller, project_ids, originals
    finally:
        controller.shutdown()


def test_bulk_delivery_requires_confirmation_and_archives_only_selected_projects(prepared_controller, monkeypatch):
    controller, project_ids, originals = prepared_controller
    questions = []
    answers = iter((QMessageBox.No, QMessageBox.Yes))

    def confirm(parent, title, message, buttons, default):
        questions.append((title, message, default))
        return next(answers)

    monkeypatch.setattr(QMessageBox, 'question', confirm)
    controller.panel.bulk_apply.click()
    assert not controller.busy
    db = controller.service.db
    assert all(db.get_project(pid)['status'] == '待交付' for pid in project_ids)
    assert all(not db.delivery_events_for_project(pid) for pid in project_ids)

    controller.panel.bulk_apply.click()
    deadline = time.monotonic() + 30
    while controller.busy and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    assert not controller.busy
    assert len(questions) == 2
    assert all('2 个' in message and default == QMessageBox.No for _, message, default in questions)

    for pid in project_ids[:2]:
        assert db.get_project(pid)['status'] == '已交付'
        event, = db.delivery_events_for_project(pid)
        assert event['counted']
        original = originals[pid]
        assert (Path(event['archive_path']) / original.name).read_bytes() == original.read_bytes()
    assert db.get_project(project_ids[2])['status'] == '待交付'
    assert db.delivery_events_for_project(project_ids[2]) == []
    assert {p['project_id'] for p in db.today_delivery_summary()['projects']} == set(project_ids[:2])


def test_bulk_delivery_refuses_a_selection_containing_a_development_project(prepared_controller, monkeypatch):
    controller, project_ids, _ = prepared_controller
    controller.service.set_status(project_ids[1], '开发中')
    monkeypatch.setattr(QMessageBox, 'question', lambda *_: pytest.fail('不应弹出确认窗口'))
    notices = []
    monkeypatch.setattr(controller.panel, 'show_notice', lambda text, **_: notices.append(text))
    controller.panel.bulk_apply.click()
    assert not controller.busy
    assert notices and '待交付' in notices[-1]
    assert all(not controller.service.db.delivery_events_for_project(pid) for pid in project_ids)


def test_cancelling_queued_delivery_keeps_the_background_scan_running(prepared_controller, monkeypatch):
    controller, project_ids, _ = prepared_controller
    release_scan = Event()

    def scan(*, cancel_event, progress):
        release_scan.wait(10)
        return {'archived': [], 'pending': 0, 'errors': []}

    monkeypatch.setattr(controller.service, 'scan_archives', scan)
    monkeypatch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.Yes)
    notices = []
    monkeypatch.setattr(controller, 'notify', lambda text, **_: notices.append(text))
    try:
        controller.check_archives(silent=True)
        assert controller.worker is not None
        controller.panel.bulk_apply.click()
        assert len(controller.queue) == 1
        controller.cancel_jobs()
        assert not controller.queue
        assert not controller.worker.cancel_event.is_set()
        assert not controller.panel.busy
        assert '已取消批量交付' in notices[-1]
        assert all(not controller.service.db.delivery_events_for_project(pid) for pid in project_ids)
    finally:
        release_scan.set()
