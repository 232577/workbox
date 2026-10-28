import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QColor, QPalette
from compact_ui import CompactPanel, DirectorySettingsDialog
from desktop_controller import FeedbackBubble
from desktop_service import DesktopService
from onboarding_ui import QuickStartDialog
from theme import install_theme, set_theme, current_mode, resolved_mode


@pytest.fixture(scope='module')
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def test_switch_from_main_menu_preserves_selection_scroll_filters_and_configuration(qapp, tmp_path):
    service = DesktopService(tmp_path/'data/workbox.db')
    service.receive_text('\n'.join(f'{i+1}. 外观测试项目{i}系统' for i in range(35)))
    install_theme(service)
    panel = CompactPanel(service)
    panel.show()
    qapp.processEvents()
    panel.project_list.setCurrentRow(20)
    panel.project_list.verticalScrollBar().setValue(300)
    before = service.workspaces.settings()
    ids = [panel.project_list.item(i).data(Qt.UserRole) for i in range(panel.project_list.count())]
    selected = panel.project_list.currentItem().data(Qt.UserRole)
    try:
        assert hasattr(panel, 'theme_actions'), '主界面缺少可用主题入口'
        panel.theme_actions['dark'].trigger()
        qapp.processEvents()
        assert current_mode() == resolved_mode() == 'dark'
        assert service.get_setting('appearance_theme') == 'dark'
        assert panel.project_list.currentItem().data(Qt.UserRole) == selected
        assert [panel.project_list.item(i).data(Qt.UserRole) for i in range(panel.project_list.count())] == ids
        assert panel.project_list.verticalScrollBar().value() > 0
        assert service.workspaces.settings() == before
        panel.theme_actions['light'].trigger()
        assert current_mode() == 'light'
    finally:
        panel.document_pane.shutdown()
        panel.close()
        install_theme()


def test_open_and_hidden_dialogs_feedback_and_scrollbar_follow_one_theme(qapp, tmp_path):
    service = DesktopService(tmp_path/'data/workbox.db')
    install_theme(service)
    panel = CompactPanel(service)
    settings = DirectorySettingsDialog(service, panel)
    guide = QuickStartDialog(service, panel)
    feedback = FeedbackBubble()
    try:
        saved_input = settings.work_root.text() + '-edited'
        settings.work_root.setText(saved_input)
        set_theme('dark', service)
        for widget in (panel, settings, guide, feedback):
            assert widget.palette().color(QPalette.Window) in (QColor('#222833'), QColor('#181C24')), type(widget).__name__
        assert settings.work_root.text() == saved_input
        assert panel.project_list.verticalScrollBar().palette().color(panel.project_list.verticalScrollBar().backgroundRole()).isValid()
    finally:
        panel.document_pane.shutdown()
        for widget in (settings, guide, feedback, panel): widget.close()
        install_theme()
