"""项目双击通过真实列表事件打开对应目录，使用隔离工作区。"""
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from desktop_controller import FloatingController
from desktop_service import DesktopService


@pytest.fixture
def controller(tmp_path):
    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    service = DesktopService(tmp_path/'data/workbox.db')
    service.receive_text('1. 双击甲系统\n2. 双击乙系统')
    controller = FloatingController(service, tray_enabled=False)
    assert controller.start()
    controller.monitor_timer.stop()
    controller.show_panel()
    application.processEvents()
    yield controller
    controller.shutdown()


@pytest.mark.parametrize('archived', [False, True])
def test_double_click_opens_actual_row_in_multi_selection(controller, monkeypatch, archived):
    opened = []
    monkeypatch.setattr(QDesktopServices, 'openUrl', lambda url: opened.append(Path(url.toLocalFile())) or True)
    panel = controller.panel
    if archived:
        controller.service.bulk_update_projects([1, 2], archived=True)
        panel.archive_filter.setCurrentIndex(panel.archive_filter.findData(True))
    panel.bulk_mode.setChecked(True)
    panel.project_list.selectAll()
    item = next(panel.project_list.item(i) for i in range(panel.project_list.count())
                if panel.project_list.item(i).data(Qt.UserRole) == 2)
    panel.project_list.scrollToItem(item)
    QApplication.processEvents()
    point = panel.project_list.visualItemRect(item).center()
    QTest.mouseClick(panel.project_list.viewport(), Qt.LeftButton, pos=point)
    QTest.mouseDClick(panel.project_list.viewport(), Qt.LeftButton, pos=point)
    QApplication.processEvents()
    assert opened == [Path(controller.service.workspaces.get_workspace(2)['path'])]
    assert all(project['status'] == '待开发' for project in controller.service.list_projects(archived=None))


def test_archived_missing_workspace_prompts_without_attempting_creation(controller, monkeypatch):
    directory = Path(controller.service.workspaces.get_workspace(2)['path'])
    directory.rmdir()
    controller.service.bulk_update_projects([2], archived=True)
    attempts = []
    monkeypatch.setattr(controller, 'enqueue', lambda *args, **kwargs: attempts.append(args))
    controller.open_workspace(2)
    assert attempts == [] and not directory.exists()
    assert '已归档' in controller.panel.notice.text() and '目录' in controller.panel.notice.text()
