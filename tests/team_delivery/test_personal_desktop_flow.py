"""The office is never configured in this end-to-end test of the two desktop entry points."""
from pathlib import Path
from time import monotonic, sleep

import pytest
from PySide6.QtCore import QMimeData, QPointF, Qt, QTimer, QUrl
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QApplication, QDialog

from anonymous_document import make_document
from test_qt_site import site, writes
from workbox.desktop.desktop_controller import FloatingController
from workbox.desktop.desktop_service import DesktopService
from workbox.team_delivery import application


def until(predicate, seconds=15):
    deadline = monotonic() + seconds
    while monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return
        sleep(.01)
    assert predicate()


@pytest.mark.parametrize('target', ['workspace', 'team'])
def test_drop_then_confirm_submits_only_the_bound_original_task(site, target, tmp_path, monkeypatch):
    browser, _, _, _, rows, history, _ = site
    rows[0].update(id=55, name='匿名待交件系统')
    monkeypatch.setattr(application, 'LIVE_SITE_URL', browser.origin)
    service = DesktopService(tmp_path / 'data' / 'workbox.db')
    desktop = FloatingController(service, tray_enabled=False)
    try:
        assert desktop.start()
        desktop.monitor_timer.stop()
        desktop.panel.nav_team.click()
        client = desktop.panel.team_controller
        page = desktop.panel.team_page
        until(lambda: not client._busy)
        page._login.click()
        until(lambda: any(item[1] == '/' for item in history))
        until(lambda: client.current_personal_snapshot() is not None, seconds=22)
        until(lambda: len(service.list_projects()) == 2 and not desktop.busy)
        project = next(p for p in service.list_projects() if p['name'] == rows[0]['name'])
        project_id = project['id']
        assert project['status'] == '待开发' and not writes(history)
        path = tmp_path / (project['name'] + '.docx')
        make_document(path)
        original = path.read_bytes()
        if target == 'workspace':
            desktop.panel.nav_projects.click()
            desktop.panel.intake.emit({'kind': 'files', 'paths': [str(path)]})
        else:
            until(lambda: not page.busy and not page.intake_block)
            mime = QMimeData()
            mime.setUrls([QUrl.fromLocalFile(str(path))])
            event = QDropEvent(QPointF(5, 5), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
            page.dropEvent(event)
            assert event.isAccepted()
        until(lambda: service.get_project(project_id)['status'] == '待交付' and not desktop.busy)
        stored = Path(service.workspaces.get_workspace(project_id)['path']) / path.name
        assert stored.read_bytes() == path.read_bytes() == original
        assert not writes(history) and rows[0]['matStatus'] == '待制作'

        desktop.panel.nav_projects.click()
        def accept_delivery():
            dialog = QApplication.activeModalWidget()
            if isinstance(dialog, QDialog):
                dialog.accept()
        QTimer.singleShot(50, accept_delivery)
        desktop.panel.confirm_delivery(project_id)
        until(lambda: service.get_project(project_id)['status'] == '已交付')
        until(lambda: rows[0]['matStatus'] == '已完成')
        until(lambda: not desktop.personal_site.projects.pending(client.current_personal_snapshot().account))
        assert writes(history) == [('/api/start-material', {'ids': [55]}),
                                   ('/api/complete-material', {'ids': [55]})]
        assert service.db.delivery_history()['event_count'] == 1
        desktop.panel.intake.emit({'kind': 'files', 'paths': [str(path)]})
        until(lambda: not desktop.busy)
        assert service.get_project(project_id)['status'] == '已交付'
        assert len(writes(history)) == 2
    finally:
        desktop.shutdown()
