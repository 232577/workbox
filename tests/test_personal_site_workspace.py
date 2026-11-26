"""Personal delivery uses anonymous local projects and an explicitly signed-in original site."""
from PySide6.QtWidgets import QApplication

from workbox.team_delivery.contracts import Account, Snapshot, Stage, Task
from workbox.team_delivery.desktop import DesktopWindow


def test_original_site_only_enables_local_intake_without_an_office_or_shared_directory():
    app = QApplication.instance() or QApplication([])
    page = DesktopWindow()
    actor = Account('local-session', '材料甲', 'material', 's1', 'material-a')
    snapshot = Snapshot(actor, (Task('55', '匿名系统', actor.id, Stage.READY, 'r1'),))
    try:
        page.configure_live(None)
        page.set_snapshot(snapshot)
        page.set_personal_mode(True)
        assert page._choose.isEnabled() and not page.intake_block
        assert '待交付' in page._hint.text() and '确认交付' in page._hint.text()
        assert page._directory_row.isHidden() and page._confirm_accounts.isHidden()
        page.set_unlinked_tasks(snapshot.tasks)
        page._tasks.selectRow(0)
        picked = []
        page.personal_binding_requested.connect(picked.append)
        assert not page._bind_task.isHidden()
        page._bind_task.click()
        assert picked == [snapshot.tasks[0]]
        page.show_read_failure('原站暂时无法读取')
        assert not page._choose.isEnabled()
        page.set_personal_mode(False)
        page.set_snapshot(snapshot)
        assert not page._choose.isEnabled()
        assert not page._directory_row.isHidden()
    finally:
        page.close()
        app.processEvents()


def test_personal_workspace_connects_through_the_normal_team_entry(tmp_path, monkeypatch):
    from workbox.desktop.desktop_controller import FloatingController
    from workbox.desktop.desktop_service import DesktopService
    from workbox.team_delivery.application import IntakeController

    app = QApplication.instance() or QApplication([])
    controller = FloatingController(DesktopService(tmp_path / 'data' / 'workbox.db'), tray_enabled=False)
    monkeypatch.setattr(IntakeController, 'start', lambda self: None)
    try:
        assert controller.start()
        controller.panel.nav_team.click()
        assert controller.personal_site is not None
        assert controller.personal_site.site is controller.panel.team_controller
    finally:
        controller.shutdown()
        app.processEvents()
