"""Workspace navigation gives the available width to project and file work."""
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication

from compact_ui import CompactPanel
from desktop_service import DesktopService


@pytest.fixture
def panel(tmp_path):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    service = DesktopService(tmp_path / 'data' / 'anonymous.db')
    service.receive_text('\n'.join(f'{index}. 匿名资料项目{index:02d}' for index in range(1, 21)))
    value = CompactPanel(service)
    value.show()
    app.processEvents()
    yield value
    if value.today_dialog is not None:
        value.today_dialog.shutdown()
    value.document_pane.shutdown()
    value.hide()


def test_horizontal_workspace_keeps_labels_and_more_project_rows_in_small_window(panel):
    panel.resize(660, 480)
    QApplication.processEvents()
    assert panel.navigation.width() == panel.width()
    assert panel.navigation.height() <= 60
    assert panel.workbench.width() == panel.width()
    for control in (panel.nav_projects, panel.today_button, panel.settings_button):
        assert control.isVisible() and control.accessibleName()
        origin = control.mapTo(panel.navigation, QPoint())
        assert panel.navigation.rect().contains(control.rect().translated(origin))
    assert panel.nav_projects.text() and panel.today_button.text()
    assert panel.files_button.isVisible() and panel.search.isVisible()
    rows = [panel.project_list.visualItemRect(panel.project_list.item(index))
            for index in range(panel.project_list.count())]
    assert sum(panel.project_list.viewport().rect().contains(row) for row in rows) >= 4


def test_wide_workspace_allocates_more_space_to_files_and_keeps_search_scope(panel):
    panel.resize(1200, 780)
    QApplication.processEvents()
    assert panel.project_side.isVisible() and panel.document_side.isVisible()
    assert 300 <= panel.project_side.width() <= 400
    assert panel.document_side.width() >= 700
    assert panel.header_surface.isAncestorOf(panel.search)
    panel.search.setText('匿名')
    panel.tabs.setCurrentIndex(1)
    QApplication.processEvents()
    assert panel.search.isVisible() and panel.header_surface.isAncestorOf(panel.search)
    panel.tabs.setCurrentIndex(0)
    QApplication.processEvents()
    assert panel.search.text() == '匿名'
    assert panel.header_surface.isAncestorOf(panel.search)
    panel.project_list.setCurrentRow(0)
    selected = panel.current_project['id']
    panel.resize(660, 480)
    panel.show_project_detail()
    QApplication.processEvents()
    assert panel.project_back_button.isVisible()
    panel.project_back_button.click()
    assert panel.current_project['id'] == selected
