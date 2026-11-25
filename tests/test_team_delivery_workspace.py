from PySide6.QtWidgets import QApplication, QPushButton

from workbox.desktop.compact_ui import CompactPanel
from workbox.desktop.desktop_service import DesktopService


def test_shipping_team_entry_starts_offline_without_a_second_browser_engine(tmp_path):
    import os
    import subprocess
    import sys
    program = """
import sys
class NoPlaywright:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'playwright', 'greenlet', 'pyee'}:
            raise ModuleNotFoundError(fullname)
sys.meta_path.insert(0, NoPlaywright())
from pathlib import Path
from PySide6.QtWidgets import QApplication
from workbox.team_delivery.application import IntakeController
from workbox.team_delivery.desktop import DesktopWindow
app = QApplication([])
root = Path(sys.argv[1])
window = DesktopWindow()
controller = IntakeController(window,'http://127.0.0.1:1',root,root/'journal',settings=root/'settings')
controller.start()
assert controller._site_session.page is None
controller.stop()
assert not controller._thread.is_alive()
"""
    completed = subprocess.run([sys.executable, '-c', program, str(tmp_path)], capture_output=True, text=True,
                               env={**os.environ, 'QT_QPA_PLATFORM':'offscreen'}, timeout=40)
    assert completed.returncode == 0, completed.stderr


def test_confirmed_office_and_original_receipts_remove_only_the_completed_row(tmp_path):
    from workbox.team_delivery.application import IntakeController
    from workbox.team_delivery.contracts import Account, DropResult, Material, Outcome, Snapshot, Stage, Task
    from workbox.team_delivery.desktop import DesktopWindow
    app = QApplication.instance() or QApplication([])
    page = DesktopWindow()
    controller = IntakeController(page, 'http://127.0.0.1:1', tmp_path, tmp_path/'journal', settings=tmp_path/'settings')
    actor = Account('a','材料员','material','s')
    tasks = tuple(Task(n, '同名项目', actor.id, Stage.READY, '1') for n in ('55', '56'))
    material = Material('同名项目.docx', tmp_path/'同名项目.docx', 'sha')
    try:
        controller._show_snapshot(Snapshot(actor, tasks))
        controller._last_drop = (str(material.path), '55', actor)
        controller._show_result(DropResult(Outcome.PENDING, '原站待核对', '55', material, 'office-project'))
        assert controller._snapshot.tasks == tasks
        controller._show_result(DropResult(Outcome.SUBMITTED, '已确认', '55', material, 'office-project'))
        assert controller._snapshot.tasks == (tasks[1],)
        assert page._tasks.rowCount() == 1 and page._tasks.item(0, 1).text() == '56'
    finally:
        controller.stop()
        page.close()


def test_returned_same_name_task_is_selectable_only_for_its_owner(tmp_path, monkeypatch):
    from workbox.team_delivery import application
    from workbox.team_delivery.contracts import Account, Snapshot, Stage, Task
    from workbox.team_delivery.desktop import DesktopWindow

    app = QApplication.instance() or QApplication([])
    page = DesktopWindow()
    controller = application.IntakeController(page, 'http://127.0.0.1:1', tmp_path, tmp_path/'journal',
                                               settings=tmp_path/'settings')
    actor = Account('a', '材料员', 'material', 's')
    same_name = '同名项目'
    tasks = (Task('55', same_name, actor.id, Stage.RETURNED, 'r2'),
             Task('56', same_name, actor.id, Stage.READY, 'r1'),
             Task('57', same_name, 'other', Stage.RETURNED, 'r2'))
    submitted = []

    def choose(parent, title, label, items, *args):
        assert items == ['请选择项目', '同名项目 · #55', '同名项目 · #56']
        return items[1], True

    try:
        controller.start()
        controller._show_snapshot(Snapshot(actor, tasks))
        assert page._eligible(tasks[0]) and not page._eligible(tasks[2])
        monkeypatch.setattr(application.QInputDialog, 'getItem', choose)
        monkeypatch.setattr(controller, 'submit_file', lambda filename, number: submitted.append(number))
        controller._resolve_project('同名项目.docx', actor.id)
        assert submitted == ['55']
    finally:
        controller.stop()
        page.close()


def test_personal_manual_opens_one_team_page_without_transferring_before_login(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    service = DesktopService(tmp_path / 'anonymous.db')
    panel = CompactPanel(service)
    try:
        assert panel.team_page is None
        from workbox.team_delivery.application import IntakeController
        monkeypatch.setattr(IntakeController, 'start', lambda self: None)
        path = tmp_path / '匿名项目.docx'
        path.write_bytes(b'fixture')
        panel.open_team_delivery(str(path))
        page = panel.team_page
        assert page.window() is panel and panel.tabs.currentWidget().isAncestorOf(page)
        assert page.findChild(QPushButton, 'usePersonalDocument').isEnabled() is False
        assert not any(w.objectName() == 'floatingInbox' for w in app.topLevelWidgets())
        assert page.pending_document.toolTip() == str(path)
        assert panel.document_pane.team_action.objectName() == 'sendToTeam'
        panel.nav_projects.click()
        panel.nav_team.click()
        assert panel.team_page is page
        assert panel.tabs.currentWidget().isAncestorOf(page)
    finally:
        panel.document_pane.shutdown()
        panel.close()


def test_team_installer_does_not_apply_a_personal_edition_update(monkeypatch, tmp_path):
    from types import SimpleNamespace
    import sys
    import pytest
    from workbox.desktop.desktop_controller import FloatingController
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, '_MEIPASS', str(tmp_path), raising=False)
    with pytest.raises(ValueError, match='团队版安装包'):
        FloatingController.prepare_update_install(SimpleNamespace(), object(), tmp_path / 'personal.exe')


def test_same_snapshot_keeps_the_existing_team_task_rows():
    from workbox.team_delivery.contracts import Account, Snapshot, Stage, Task
    from workbox.team_delivery.desktop import DesktopWindow

    app = QApplication.instance() or QApplication([])
    page = DesktopWindow()
    account = Account('anonymous', '材料甲', 'material', 'session')
    snapshot = Snapshot(account, (Task('55', '匿名项目', account.id, Stage.READY, 'v1'),))
    page.set_snapshot(snapshot)
    page._tasks.selectRow(0)
    item = page._tasks.item(0, 0)
    page.set_snapshot(snapshot)
    assert page._tasks.item(0, 0) is item
    assert page._selected_task() == snapshot.tasks[0]


def test_team_page_distinguishes_waiting_for_sync_from_a_verified_empty_list():
    from workbox.team_delivery.contracts import Account, Snapshot
    from workbox.team_delivery.desktop import DesktopWindow

    app = QApplication.instance() or QApplication([])
    page = DesktopWindow()
    page.configure_live(None)
    snapshot = Snapshot(Account('anonymous', '材料甲', 'material', 'session'))
    page.set_snapshot(snapshot)
    page.show_read_deferred('请打开原站个人清单。', False)
    assert page._empty.text() == '等待同步'
    assert page.intake_block == '等待同步本人任务'
    page.set_snapshot(snapshot)
    assert page._empty.text() == '暂无任务'


def test_team_tab_return_requests_one_refresh_and_timer_polls_every_15_seconds(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from workbox.team_delivery import application

    app = QApplication.instance() or QApplication([])
    now = [0.0]
    monkeypatch.setattr(application, 'monotonic', lambda: now[0])
    monkeypatch.setattr(application, 'Thread', lambda **kwargs: SimpleNamespace(
        start=lambda: None, join=lambda: None, is_alive=lambda: True))
    panel = CompactPanel(DesktopService(tmp_path / 'anonymous.db'))
    panel.show()
    try:
        panel.open_team_delivery()
        controller = panel.team_controller
        app.processEvents()
        assert controller._commands.get_nowait() == (controller._worker.refresh, (True,))
        assert controller._timer.interval() == 15_000
        controller._poll_finished()

        now[0] = 5.0
        panel.nav_projects.click()
        controller._timer.timeout.emit()
        assert controller._commands.empty()
        panel.nav_team.click()
        assert controller._commands.get_nowait() == (controller._worker.refresh, (True,))

        panel.nav_projects.click()
        panel.nav_team.click()
        controller._timer.timeout.emit()
        assert controller._commands.empty()
        controller._poll_finished()
        assert controller._commands.empty()

        now[0] = 19.0
        panel.nav_projects.click()
        controller._timer.timeout.emit()
        assert controller._commands.empty()
        now[0] = 20.0
        controller._timer.timeout.emit()
        assert controller._commands.get_nowait() == (controller._worker.refresh, (True,))
        controller._poll_finished()

        now[0] = 27.0
        panel.nav_team.click()
        assert controller._commands.get_nowait() == (controller._worker.refresh, (True,))
        controller._poll_finished()

        now[0] = 34.0
        app.applicationStateChanged.emit(application.Qt.ApplicationState.ApplicationActive)
        assert controller._commands.get_nowait() == (controller._worker.refresh, (True,))
    finally:
        panel.team_controller.stop()
        panel.document_pane.shutdown()
        panel.close()


def test_configured_team_workspace_is_available_at_start_without_opening_any_browser(tmp_path, monkeypatch):
    from workbox.desktop.desktop_controller import FloatingController
    from workbox.team_delivery import application

    app = QApplication.instance() or QApplication([])
    root = tmp_path / '团队交件'
    root.mkdir()
    (root / 'workbox-url.txt').write_text('http://127.0.0.1:18770', encoding='utf-8')
    service = DesktopService(tmp_path / 'anonymous.db')
    opened = []
    monkeypatch.setattr(application._Worker, 'open_login', lambda self: opened.append('browser'))
    controller = FloatingController(service, tray_enabled=False)
    try:
        assert controller.start()
        assert controller.panel.team_page is not None
        assert controller.panel.tabs.currentWidget() is controller.panel.team_page_container
        assert not controller.panel.isVisible()
        app.processEvents()
        assert opened == []
    finally:
        controller.shutdown()
    assert not controller.panel.team_controller._timer.isActive()
    assert not controller.panel.team_controller._thread.is_alive()


def test_windows_offline_browser_fallback_uses_the_installed_copy(monkeypatch, tmp_path):
    import sys
    from types import SimpleNamespace
    from playwright.sync_api import Error
    from workbox.team_delivery.browser import open_browser

    calls = []
    browser = object()
    def launch(**options):
        calls.append(options)
        if 'channel' in options:
            raise Error('System browser unavailable')
        return browser

    executable = tmp_path / 'Chromium' / 'chrome.exe'
    executable.parent.mkdir()
    executable.touch()
    monkeypatch.setattr(sys, 'executable', str(tmp_path / 'WorkBox.exe'))
    monkeypatch.setattr(sys, '_MEIPASS', str(tmp_path / '_internal'), raising=False)
    assert open_browser(SimpleNamespace(chromium=SimpleNamespace(launch=launch))) is browser
    assert [item.get('channel') for item in calls] == ['chrome', 'msedge', None]
    assert calls[-1] == dict(executable_path=str(executable), headless=False, chromium_sandbox=True)
