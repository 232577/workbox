from PySide6.QtWidgets import QApplication, QDialog
import time
from concurrent.futures import CancelledError
from threading import Event

import pytest
from PySide6.QtCore import Qt


def test_preview_requires_explicit_check_and_invalidates_on_edit():
    app = QApplication.instance() or QApplication([])
    from project_change_ui import ProjectChangeDialog
    dialog = ProjectChangeDialog({'id': 1, 'name': '甲'})
    requests = []
    dialog.preview_requested.connect(requests.append)
    dialog.name_edit.setText('乙')
    assert not dialog.confirm_button.isEnabled()
    dialog.check_button.click()
    assert requests[-1]['name'] == '乙'
    dialog.show_preview(requests[-1]['serial'], {'kind': 'rename', 'token': 'v1',
        'source': {'name': '甲', 'status': '待开发'}, 'new_name': '乙'})
    assert dialog.confirm_button.isEnabled()
    dialog.name_edit.setText('丙')
    assert not dialog.confirm_button.isEnabled()
    dialog.reject()
    assert dialog.result() == QDialog.Rejected
    dialog.deleteLater()
    app.processEvents()


def test_multiple_candidates_require_explicit_choice_and_second_preview():
    app = QApplication.instance() or QApplication([])
    from project_change_ui import ProjectChangeDialog
    dialog = ProjectChangeDialog({'id': 1, 'name': '甲'})
    requests = []
    dialog.preview_requested.connect(requests.append)
    dialog.check_button.click()
    dialog.show_preview(requests[-1]['serial'], {'kind': 'choose_target', 'candidates': [
        {'id': 2, 'name': '甲', 'status': '待交付'}, {'id': 3, 'name': '甲', 'status': '待开发'}]})
    assert not dialog.confirm_button.isEnabled()
    assert dialog.target_box.currentData() is None
    dialog.target_box.setCurrentIndex(1)
    dialog.check_button.click()
    assert requests[-1]['target_id'] == 2
    dialog.show_preview(requests[-1]['serial'], {'kind': 'merge', 'token': 'v2',
        'source': {'name': '甲'}, 'target': {'id': 2, 'name': '甲', 'status': '待交付'}})
    assert dialog.confirm_button.text() == '确认合并'
    dialog.confirm_button.click()
    assert dialog.result() == QDialog.Accepted
    assert dialog.request['confirmed_merge'] is True
    assert dialog.request['expected_token'] == 'v2'
    assert dialog.request['merge_target_id'] == 2
    dialog.deleteLater()
    app.processEvents()


def test_single_same_name_candidate_merge_uses_preview_target():
    app = QApplication.instance() or QApplication([])
    from project_change_ui import ProjectChangeDialog
    dialog = ProjectChangeDialog({'id': 1, 'name': '同名项目'})
    requests = []
    dialog.preview_requested.connect(requests.append)
    dialog.check_button.click()
    dialog.show_preview(requests[-1]['serial'], {'kind': 'merge', 'token': 'checked',
        'source': {'id': 1, 'name': '同名项目', 'status': '待开发'},
        'target': {'id': 2, 'name': '同名项目', 'status': '待交付'},
        'result_status': '待交付'})
    assert '待交付' in dialog.summary.toPlainText()
    dialog.confirm_button.click()
    assert dialog.request['merge_target_id'] == 2
    assert dialog.request['confirmed_merge'] is True
    assert dialog.request['name'] == '同名项目'
    dialog.deleteLater()
    app.processEvents()


def test_late_preview_after_name_change_and_blocked_plan_cannot_confirm():
    app = QApplication.instance() or QApplication([])
    from project_change_ui import ProjectChangeDialog
    dialog = ProjectChangeDialog({'id': 1, 'name': '甲'})
    requests = []
    dialog.preview_requested.connect(requests.append)
    dialog.check_button.click()
    serial = requests[-1]['serial']
    dialog.name_edit.setText('乙')
    dialog.show_preview(serial, {'kind': 'rename', 'token': 'old'})
    assert not dialog.confirm_button.isEnabled()
    dialog.check_button.click()
    dialog.show_preview(requests[-1]['serial'], {'kind': 'blocked', 'token': 'bad', 'warning': '目录冲突'})
    assert not dialog.confirm_button.isEnabled()
    assert '目录冲突' in dialog.summary.toPlainText()
    dialog.reject()
    assert dialog.request is None
    dialog.deleteLater()
    app.processEvents()


def test_preview_runs_in_controller_queue_and_closed_dialog_is_ignored(tmp_path):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    from desktop_controller import FloatingController
    from desktop_service import DesktopService
    from project_change_ui import ProjectChangeDialog
    service = DesktopService(tmp_path / 'data' / 'workbox.db')
    service.db.create_capture('甲', '批次', [('甲', False)])
    calls = []
    def preview(pid, name, merge_target_id=None, **kwargs):
        calls.append((pid, name, merge_target_id))
        return {'kind': 'rename', 'token': 'checked', 'source': {'name': '甲'}}
    service.preview_project_rename = preview
    controller = FloatingController(service, tray_enabled=False)
    assert controller.start()
    dialog = ProjectChangeDialog({'id': 1, 'name': '甲'}, controller.panel)
    dialog.preview_requested.connect(lambda request: controller.preview_project_change(dialog, request))
    dialog.show()
    dialog.check_button.click()
    deadline = time.monotonic() + 5
    while controller.busy and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    app.processEvents()
    assert calls == [(1, '甲', None)]
    assert dialog.confirm_button.isEnabled()
    dialog.check_button.click()
    dialog.reject()
    deadline = time.monotonic() + 5
    while controller.busy and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    app.processEvents()
    assert dialog.request is None
    assert service.get_project(1)['name'] == '甲'
    dialog.deleteLater()
    controller.shutdown()
    app.processEvents()


def test_long_candidate_keeps_dialog_usable_and_exposes_full_path():
    app = QApplication.instance() or QApplication([])
    from project_change_ui import ProjectChangeDialog
    dialog = ProjectChangeDialog({'id': 1, 'name': '甲'})
    requests = []
    dialog.preview_requested.connect(requests.append)
    path = 'D:\\工作\\' + '很长的项目目录' * 80
    dialog.check_button.click()
    dialog.show_preview(requests[-1]['serial'], {'kind': 'choose_target', 'candidates': [
        {'id': 2, 'name': '甲', 'status': '待交付', 'workspace_path': path}]})
    dialog.resize(640, 520)
    dialog.show()
    app.processEvents()
    assert dialog.width() <= 640
    assert dialog.target_box.minimumWidth() == 0
    assert path in dialog.target_box.itemData(1, Qt.ToolTipRole)
    assert path in dialog.summary.toPlainText()
    assert dialog.name_edit.accessibleName() == '新的项目名称'
    assert dialog.target_box.accessibleName() == '同名项目保留目标'
    assert dialog.summary.accessibleName() == '项目变更检查结果'
    dialog.reject()
    dialog.deleteLater()
    app.processEvents()


@pytest.mark.parametrize('action', ['edit', 'close'])
def test_changing_or_closing_dialog_cancels_only_current_preview(tmp_path, action):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    from desktop_controller import FloatingController
    from desktop_service import DesktopService
    from project_change_ui import ProjectChangeDialog
    service = DesktopService(tmp_path / 'data' / 'workbox.db')
    service.db.create_capture('甲', '批次', [('甲', False)])
    entered, stopped, queued = Event(), Event(), Event()
    def preview(pid, name, merge_target_id=None, *, cancel_event, progress):
        entered.set()
        deadline = time.monotonic() + 1
        while not cancel_event.is_set() and time.monotonic() < deadline:
            time.sleep(.005)
        if cancel_event.is_set():
            stopped.set()
        raise CancelledError()
    service.preview_project_rename = preview
    controller = FloatingController(service, tray_enabled=False)
    assert controller.start()
    dialog = ProjectChangeDialog({'id': 1, 'name': '甲'}, controller.panel)
    dialog.preview_requested.connect(lambda request: controller.preview_project_change(dialog, request))
    dialog.show()
    dialog.check_button.click()
    deadline = time.monotonic() + 5
    while not entered.is_set() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    assert entered.is_set()
    controller.enqueue('后续任务', lambda **_: queued.set(), silent=True)
    if action == 'edit':
        dialog.name_edit.setText('乙')
    else:
        dialog.reject()
        dialog.deleteLater()
    deadline = time.monotonic() + 5
    while not queued.is_set() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    assert stopped.is_set() and queued.is_set()
    assert service.get_project(1)['name'] == '甲'
    if action == 'edit':
        assert not dialog.confirm_button.isEnabled()
        assert dialog.request is None
        dialog.reject()
        dialog.deleteLater()
    controller.shutdown()
    app.processEvents()
