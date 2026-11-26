from time import monotonic

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from workbox.desktop.desktop_controller import FloatingController
from workbox.desktop.desktop_service import DesktopService
from workbox.team_delivery.application import IntakeController
from workbox.team_delivery.contracts import Account, Snapshot, Stage, Task


def wait_for(predicate):
    deadline = monotonic() + 5
    while not predicate() and monotonic() < deadline:
        QTest.qWait(10)
    assert predicate()


def test_team_sync_runs_through_personal_storage_queue(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    service = DesktopService(tmp_path / 'anonymous.db')
    controller = FloatingController(service, tray_enabled=False)
    tasks = (('37', '匿名工作材料'), ('38', '匿名技术材料'))
    submitted = []
    messages = []

    def sync_site_tasks(received, *, cancel_event):
        submitted.append((received, cancel_event.is_set()))
        return {
            'created_project_ids': [1],
            'linked_project_ids': [],
            'unchanged_project_ids': [],
            'conflicts': [{'task_id': '38', 'title': '匿名技术材料', 'reason': '存在同名记录'}],
        }

    monkeypatch.setattr(service, 'sync_site_tasks', sync_site_tasks, raising=False)
    monkeypatch.setattr(IntakeController, 'start', lambda self: None)
    monkeypatch.setattr(IntakeController, 'set_personal_sync_result',
                        lambda self, message: messages.append(message), raising=False)
    try:
        assert controller.start()
        controller.monitor_timer.stop()
        controller.panel.open_team_delivery()
        controller.panel.team_controller.personal_sync_requested.emit(tasks)
        wait_for(lambda: bool(messages))
        assert submitted == [(tasks, False)]
        assert len(messages) == 1
        assert '新建 1 项' in messages[0]
        assert '待核对 1 项' in messages[0]
        assert '#38' in messages[0] and '存在同名记录' in messages[0]
    finally:
        controller.shutdown()


def test_click_sync_imports_personal_projects_and_keeps_progress_on_repeat(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    service = DesktopService(tmp_path / 'anonymous.db')
    current = service.receive_text('匿名现有材料管理系统')['project_ids'][0]
    service.set_status(current, '开发中')
    actor = Account('operator', '匿名材料员', 'material', 'session')

    class AnonymousGateway:
        def __init__(self):
            self.tasks = (
                Task('41', '匿名新增材料管理系统', actor.id, Stage.READY, 'r1'),
                Task('42', '匿名现有材料管理系统', actor.id, Stage.RETURNED, 'r1'),
                Task('43', '匿名其他材料管理系统', 'other', Stage.READY, 'r1'),
            )

        def refresh_snapshot(self):
            return Snapshot(actor, self.tasks)

        poll_snapshot = refresh_snapshot

        def current_account(self):
            return actor

        def close(self):
            pass

    gateway = AnonymousGateway()
    original = IntakeController.__init__

    def create_controller(self, *args, **kwargs):
        original(self, *args, gateway=gateway, **kwargs)

    monkeypatch.setattr(IntakeController, '__init__', create_controller)
    controller = FloatingController(service, tray_enabled=False)
    try:
        assert controller.start()
        controller.monitor_timer.stop()
        controller.panel.open_team_delivery()
        page = controller.panel.team_page
        team = controller.panel.team_controller
        sync = page.findChild(QPushButton, 'syncPersonalProjects')
        notice = page.findChild(QLabel, 'syncNotice')
        team.refresh()
        wait_for(sync.isEnabled)
        sync.click()
        wait_for(lambda: '新建 1 项，关联 1 项' in notice.text())
        projects = {row['name']: row for row in service.list_projects()}
        assert set(projects) == {'匿名新增材料管理系统', '匿名现有材料管理系统'}
        assert projects['匿名现有材料管理系统']['status'] == '开发中'
        new = projects['匿名新增材料管理系统']
        assert new['status'] == '待开发'

        service.set_status(new['id'], '待交付')
        gateway.tasks = (Task('41', '匿名原站改名管理系统', actor.id, Stage.WORKING, 'r2'), *gateway.tasks[1:])
        wait_for(sync.isEnabled)
        sync.click()
        wait_for(lambda: '已有 2 项' in notice.text())
        assert service.get_project(new['id'])['name'] == '匿名新增材料管理系统'
        assert service.get_project(new['id'])['status'] == '待交付'
        assert len(service.list_projects()) == 2
    finally:
        controller.shutdown()
