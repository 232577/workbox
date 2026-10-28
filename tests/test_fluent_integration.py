"""正式业务入口使用统一 Fluent 基础控件，同时保留原生模型和保存规则。"""
import pytest
from PySide6.QtWidgets import QApplication, QComboBox, QTableWidget, QScrollBar
from qfluentwidgets import PushButton, PrimaryPushButton, CheckBox, isDarkTheme
from compact_ui import CompactPanel, DirectorySettingsDialog
from desktop_service import DesktopService
from theme import install_theme, set_theme
from ui_components import LineEdit, SpinBox


@pytest.fixture(scope='module')
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def test_business_controls_are_fluent_with_native_data_and_scroll(qapp, tmp_path):
    service = DesktopService(tmp_path / 'data/workbox.db')
    install_theme(service)
    panel = CompactPanel(service)
    settings = DirectorySettingsDialog(service, panel)
    assert isinstance(panel.files_button, PrimaryPushButton)
    assert isinstance(panel.settings_button, PushButton)
    assert isinstance(panel.search, LineEdit)
    assert isinstance(panel.urgent_filter, CheckBox)
    assert isinstance(settings.zip_delivery_min_files, SpinBox)
    assert isinstance(settings.work_root, LineEdit)
    assert isinstance(panel.archive_filter, QComboBox)
    assert panel.archive_filter.itemData(0) is False
    assert isinstance(panel.document_pane.table, QTableWidget)
    assert type(panel.project_list.verticalScrollBar()) is QScrollBar
    panel.document_pane.shutdown()


def test_fluent_theme_and_saved_theme_change_together_without_workflow_edits(qapp, tmp_path):
    service = DesktopService(tmp_path / 'data/workbox.db')
    install_theme(service)
    panel = CompactPanel(service)
    before = service.workspaces.settings()
    panel.search.setText('保留搜索内容')
    try:
        set_theme('dark', service)
        assert isDarkTheme()
        assert service.get_setting('appearance_theme') == 'dark'
        assert service.workspaces.settings() == before
        assert panel.search.text() == '保留搜索内容'
        set_theme('light', service)
        assert not isDarkTheme()
    finally:
        panel.document_pane.shutdown()
        install_theme()
