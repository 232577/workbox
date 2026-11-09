import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from compact_ui import CompactPanel
from desktop_service import DesktopService


@pytest.fixture(scope='module')
def qapp():
    return QApplication.instance() or QApplication([])


def test_horizontal_navigation_keeps_tab_contract_and_actions(qapp, tmp_path):
    panel = CompactPanel(DesktopService(tmp_path / 'workbox.db'))
    panel.show()
    qapp.processEvents()
    try:
        assert panel.navigation.width() == panel.width()
        assert panel.navigation.height() <= 60
        assert not panel.tabs.tabBar().isVisible()
        assert panel.tabs.count() == 3
        assert panel.task_groups_page is None
        panel.advanced_actions['zip'].trigger()
        assert panel.tabs.currentIndex() == 1
        assert panel.page_title.text() == 'ZIP 归档'
        panel.tabs.setCurrentIndex(2)
        assert panel.nav_review.isChecked()
        panel.nav_projects.click()
        assert panel.tabs.currentIndex() == 0
        assert panel.page_title.text() == '项目'
        assert not panel.brand_icon.pixmap().isNull()
        assert panel.brand_label.text() == 'WorkBox'
        assert '计入个人工作量' in panel.today_button.toolTip()
        assert '代交' in panel.today_button.toolTip()
        panel.nav_projects.click()
        assert panel.nav_projects.isChecked()
        panel.show_today_deliveries()
        assert panel.tabs.currentIndex() == 3
        assert panel.tabs.currentWidget() is panel.delivery_page_container
        assert panel.today_button.isChecked()
        panel.nav_projects.click()
        assert panel.tabs.currentIndex() == 0
    finally:
        panel.document_pane.shutdown()
        panel.hide()


def test_narrow_workbench_keeps_horizontal_labels_and_controls_inside(qapp, tmp_path):
    panel = CompactPanel(DesktopService(tmp_path / 'workbox.db'))
    panel.resize(660, 480)
    panel.show()
    qapp.processEvents()
    try:
        assert panel.size().width() == 660
        assert panel.navigation.width() == panel.width()
        assert panel.navigation.height() <= 60
        assert panel.settings_button.accessibleName()=='高级设置'
        assert panel.today_button.isVisible() and panel.today_button.text() == '今日工作量 0'
        assert panel.today_button.accessibleDescription() == '今日工作量 0'
        rectangles = []
        for control, _ in panel.nav_controls:
            if control.isVisible():
                rect = control.rect().translated(control.pos())
                assert panel.navigation.rect().contains(rect)
                assert all(not rect.intersects(other) for other in rectangles)
                rectangles.append(rect)
        assert panel.brand_icon.isVisible()
        assert not panel.brand_label.isVisible()
        assert panel.brand_label.text() == 'WorkBox'
        assert not panel.archive_filter.isVisible() and not panel.type_filter.isVisible()
        for control in (panel.project_filter_button, panel.batch_filter):
            assert control.isVisible()
            rect = control.rect()
            rect.moveTopLeft(control.mapTo(panel, rect.topLeft()))
            assert panel.rect().contains(rect), (control.accessibleName(), rect)
        panel.project_filter_button.click()
        qapp.processEvents()
        for control in (panel.search, panel.files_button, panel.background_button,
                        panel.exit_button, panel.archive_filter, panel.type_filter):
            assert control.isVisible()
            rect = control.rect()
            rect.moveTopLeft(control.mapTo(panel, rect.topLeft()))
            assert panel.rect().contains(rect), (control.accessibleName(), rect)
        panel.project_filter_button.click()
        qapp.processEvents()
        assert not panel.archive_filter.isVisible() and not panel.type_filter.isVisible()
        assert panel.nav_zip.toolTip() == 'ZIP 归档'
    finally:
        panel.document_pane.shutdown()
        panel.hide()
