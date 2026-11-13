"""Secondary surface contracts: anonymous Qt only, no publication or live data."""
import importlib
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QAccessible
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFrame


@pytest.fixture(scope='module')
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


@pytest.fixture
def service(tmp_path):
    from desktop_service import DesktopService
    service = DesktopService(tmp_path / 'data/workbox.db')
    service.receive_text('1. 匿名界面验收系统')
    return service


def make_dialog(kind, service):
    from compact_ui import DirectorySettingsDialog, ProjectDetailsDialog, ProjectDeliveryDialog, FileIntakeLogDialog
    from project_types_ui import ProjectTypePickerDialog, ProjectTypesDialog
    from project_change_ui import ProjectChangeDialog
    from onboarding_ui import FirstRunDialog, QuickStartDialog
    return {
        'settings': lambda: DirectorySettingsDialog(service),
        'details': lambda: ProjectDetailsDialog(service, service.get_project(1)),
        'delivery': lambda: ProjectDeliveryDialog(service.get_project(1)),
        'history': lambda: FileIntakeLogDialog(service),
        'type-picker': lambda: ProjectTypePickerDialog(service, [service.get_project(1)]),
        'types': lambda: ProjectTypesDialog(service),
        'rename': lambda: ProjectChangeDialog(service.get_project(1)),
        'welcome': lambda: FirstRunDialog(service),
        'guide': lambda: QuickStartDialog(service),
    }[kind]()


@pytest.mark.parametrize('kind', ['settings','details','delivery','history','type-picker','types','rename','welcome','guide'])
def test_secondary_dialog_has_shared_header_and_fixed_actions(qapp, service, kind):
    from ui_components import DialogHeader
    d = make_dialog(kind, service)
    try:
        d.show()
        for _ in range(5): qapp.processEvents()
        assert d.findChild(DialogHeader) is not None, kind + ' still lacks unified header'
        footer = d.findChild(QFrame, 'dialogActions')
        assert footer is not None, kind + ' lacks fixed action rail'
        assert d.rect().contains(footer.mapTo(d, footer.rect().bottomRight()))
        assert not d.windowFlags() & Qt.WindowStaysOnTopHint
    finally:
        d.close(); d.deleteLater(); qapp.processEvents()


def test_inline_error_is_plain_visible_only_when_present(qapp):
    assert (Path(__file__).parents[1] / 'dialog_chrome.py').is_file(), 'missing dialog chrome'
    module = importlib.import_module('dialog_chrome')
    error = module.InlineError()
    try:
        assert error.isHidden()
        error.setText('<b>未保存</b>')
        assert not error.isHidden() and error.textFormat() == Qt.PlainText
        assert error.property('tone') == 'error'
        error.clear()
        assert error.isHidden() and error.text() == ''
    finally: error.deleteLater()


def test_inline_error_accessibility_exposes_current_message_and_clears_it(qapp):
    from dialog_chrome import InlineError
    error = InlineError('设置未保存：匿名目录不合法')
    try:
        error.show(); qapp.processEvents()
        accessible = QAccessible.queryAccessibleInterface(error)
        assert accessible.text(QAccessible.Name) == '设置未保存：匿名目录不合法'
        error.setText('项目类型未保存：<匿名类型>已存在')
        qapp.processEvents()
        assert accessible.text(QAccessible.Name) == '项目类型未保存：<匿名类型>已存在'
        error.clear()
        qapp.processEvents()
        assert error.isHidden()
        assert accessible.text(QAccessible.Name) == ''
    finally: error.close(); error.deleteLater(); qapp.processEvents()


def test_settings_accessibility_exposes_current_save_state(qapp, service):
    from compact_ui import DirectorySettingsDialog
    before = service.workspaces.settings()
    d = DirectorySettingsDialog(service)
    try:
        d.show(); qapp.processEvents()
        accessible = QAccessible.queryAccessibleInterface(d.pending_label)
        assert accessible.text(QAccessible.Name) == '设置已同步'
        original = d.stable_seconds.value()
        d.stable_seconds.setValue(original + 1)
        qapp.processEvents()
        assert accessible.text(QAccessible.Name) == '有未保存的修改'
        d.stable_seconds.setValue(original)
        qapp.processEvents()
        assert accessible.text(QAccessible.Name) == '设置已同步'
        assert service.workspaces.settings() == before
    finally: d.reject(); d.deleteLater(); qapp.processEvents()


def test_shared_text_inputs_keep_readable_height_through_theme_and_disabled_states(qapp):
    from PySide6.QtWidgets import QDialog, QVBoxLayout
    from ui_components import LineEdit
    from theme import apply_to, set_theme
    d = QDialog(); apply_to(d)
    edit = LineEdit('匿名输入')
    QVBoxLayout(d).addWidget(edit)
    try:
        d.show()
        for mode in ('light', 'dark', 'light'):
            set_theme(mode, persist=False)
            for enabled in (True, False, True):
                edit.setEnabled(enabled)
                if enabled: edit.setFocus()
                for _ in range(4): qapp.processEvents()
                assert edit.height() >= 32
                assert edit.text() == '匿名输入'
    finally: d.close(); d.deleteLater(); set_theme('light', persist=False)


def test_settings_search_and_sections_keep_pending_values_without_writes(qapp, service):
    from compact_ui import DirectorySettingsDialog
    before = service.workspaces.settings()
    d = DirectorySettingsDialog(service)
    try:
        assert hasattr(d, 'settings_search'), 'settings require local search'
        assert hasattr(d, 'section_filter'), 'settings require section navigation'
        d.show(); qapp.processEvents()
        d.stable_seconds.setValue(23)
        assert '未保存' in d.pending_label.text()
        d.settings_search.setText('文件稳定')
        qapp.processEvents()
        assert sum(not section.isHidden() for section, _ in d._setting_groups) == 1
        assert service.workspaces.settings() == before
        d.settings_search.setText('不存在的匿名设置')
        assert not d.no_results.isHidden()
        d.settings_search.clear()
        d.section_filter.setCurrentIndex(1)
        assert sum(not section.isHidden() for section, _ in d._setting_groups) == 1
        d.section_filter.setCurrentIndex(0)
        assert all(not section.isHidden() for section, _ in d._setting_groups)
        assert d.stable_seconds.value() == 23
        d.reject()
        assert service.workspaces.settings() == before
    finally: d.deleteLater(); qapp.processEvents()


def test_enter_in_settings_search_never_saves(qapp, service, monkeypatch):
    from compact_ui import DirectorySettingsDialog
    d = DirectorySettingsDialog(service)
    calls = []
    monkeypatch.setattr(service.workspaces, 'configure', lambda *a, **k: calls.append(True))
    try:
        assert hasattr(d, 'settings_search'), 'missing safe search field'
        d.show(); d.settings_search.setFocus(); qapp.processEvents()
        QTest.keyClick(d.settings_search, Qt.Key_Return)
        qapp.processEvents()
        assert calls == [] and d.result() == 0 and d.isVisible()
    finally: d.reject(); d.deleteLater(); qapp.processEvents()


def test_type_filter_cannot_modify_a_hidden_selected_type(qapp, service):
    from project_types_ui import ProjectTypesDialog
    service.create_project_type('界面验收分类')
    before = service.list_project_types(include_inactive=True)
    d = ProjectTypesDialog(service)
    try:
        assert hasattr(d, 'type_search'), 'type manager needs a filter'
        d.type_list.setCurrentRow(0)
        d.type_search.setText('无结果验收')
        assert not d.rename_button.isEnabled() and not d.toggle_button.isEnabled()
        assert not d.empty.isHidden()
        d.type_search.setText('界面验收')
        visible = [d.type_list.item(i) for i in range(d.type_list.count()) if not d.type_list.item(i).isHidden()]
        assert len(visible) == 1 and '界面验收分类' in visible[0].text()
        d.type_search.clear()
        assert service.list_project_types(include_inactive=True) == before
    finally: d.close(); d.deleteLater(); qapp.processEvents()


def test_type_search_has_readable_height_and_enter_does_not_open_editor(qapp, service, monkeypatch):
    import project_types_ui
    calls = []
    monkeypatch.setattr(project_types_ui.QInputDialog, 'getText', lambda *a, **k: (calls.append(True) or '', False))
    d = project_types_ui.ProjectTypesDialog(service)
    try:
        d.show(); d.type_search.setFocus()
        for _ in range(5): qapp.processEvents()
        assert d.type_search.height() >= 32
        QTest.keyClick(d.type_search, Qt.Key_Return)
        qapp.processEvents()
        assert calls == [], 'search must not invoke the default add-type action'
    finally: d.close(); d.deleteLater(); qapp.processEvents()


def test_type_search_enter_is_filter_only(qapp, service, monkeypatch):
    import project_types_ui
    calls = []
    monkeypatch.setattr(project_types_ui.QInputDialog, 'getText', lambda *a, **k: (calls.append(True) or '', False))
    d = project_types_ui.ProjectTypesDialog(service)
    try:
        d.show(); d.type_search.setFocus()
        for _ in range(5): qapp.processEvents()
        QTest.keyClick(d.type_search, Qt.Key_Return)
        qapp.processEvents()
        assert calls == [] and d.isVisible()
    finally: d.close(); d.deleteLater(); qapp.processEvents()


@pytest.mark.parametrize('mode', ['light', 'dark'])
def test_settings_small_window_filter_and_error_keep_actions_visible(qapp, service, mode):
    from compact_ui import DirectorySettingsDialog
    from theme import set_theme
    set_theme(mode, persist=False)
    d = DirectorySettingsDialog(service)
    try:
        d.resize(600, 540); d.show()
        d.settings_search.setText('没有匹配的设置')
        d.error.setText('设置未保存：匿名路径不合法，请检查并重试。')
        for _ in range(8): qapp.processEvents()
        assert (d.width(), d.height()) == (600, 540)
        for control in (d.settings_search, d.section_filter, d.save_button, d.cancel_button, d.error):
            assert control.isVisible()
            assert d.rect().contains(control.mapTo(d, control.rect().bottomRight()))
    finally: d.close(); d.deleteLater(); qapp.processEvents(); set_theme('light', persist=False)
