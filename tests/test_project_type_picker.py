import sqlite3

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog

from desktop_service import DesktopService
import project_types_ui


@pytest.fixture(scope='module')
def qapp():
    return QApplication.instance() or QApplication([])


def fixture_projects(tmp_path):
    service = DesktopService(tmp_path / 'picker.db')
    service.db.create_capture('隔离验证', '类型选择', [('项目甲', False), ('项目乙', False)])
    return service, service.list_projects()


def test_picker_cancel_and_accept_only_choose_without_writing(qapp, tmp_path, monkeypatch):
    service, projects = fixture_projects(tmp_path)
    kind = service.create_project_type('自定义选择')
    monkeypatch.setattr(project_types_ui.ProjectTypePickerDialog, 'exec', lambda self: QDialog.Rejected)
    assert project_types_ui.choose_project_type(service, None, projects) == (False, None)
    monkeypatch.setattr(project_types_ui.ProjectTypePickerDialog, 'exec', lambda self: (
        self.type_combo.setCurrentIndex(self.type_combo.findData(kind['id'])), self.accept(), self.result())[-1])
    assert project_types_ui.choose_project_type(service, None, projects) == (True, kind['id'])
    assert all(service.get_project(p['id'])['project_type_id'] is None for p in projects)


def test_mixed_types_require_explicit_choice_and_unclassified_is_valid(qapp, tmp_path):
    service, projects = fixture_projects(tmp_path)
    kind = service.create_project_type('共同选择')
    service.set_project_types([projects[0]['id']], kind['id'])
    projects = [service.get_project(p['id']) for p in projects]
    dialog = project_types_ui.ProjectTypePickerDialog(service, projects)
    assert '2' in dialog.selection_label.text()
    assert not dialog.save_button.isEnabled()
    dialog.accept()
    assert dialog.result() != QDialog.Accepted
    dialog.type_combo.setCurrentIndex(dialog.type_combo.findData(None))
    assert dialog.save_button.isEnabled()
    dialog.accept()
    assert dialog.result() == QDialog.Accepted
    assert dialog.selected_type_id is None
    assert service.get_project(projects[0]['id'])['project_type_id'] == kind['id']


def test_inactive_current_and_stale_disabled_types_cannot_be_returned(qapp, tmp_path):
    service, projects = fixture_projects(tmp_path)
    kind = service.create_project_type('停用选择')
    service.set_project_types([p['id'] for p in projects], kind['id'])
    service.set_project_type_active(kind['id'], False)
    projects = [service.get_project(p['id']) for p in projects]
    dialog = project_types_ui.ProjectTypePickerDialog(service, projects)
    assert '停用' in dialog.type_combo.currentText()
    assert not dialog.save_button.isEnabled()
    dialog.accept()
    assert dialog.result() != QDialog.Accepted
    service.set_project_type_active(kind['id'], True)
    dialog.refresh_types()
    assert dialog.type_combo.currentData() == kind['id']
    service.set_project_type_active(kind['id'], False)
    dialog.accept()
    assert dialog.result() != QDialog.Accepted
    assert dialog.error.text()


def test_manage_types_keeps_stable_id_and_independent_saved_changes(qapp, tmp_path, monkeypatch):
    service, projects = fixture_projects(tmp_path)
    kind = service.create_project_type('选择前名称')
    dialog = project_types_ui.ProjectTypePickerDialog(service, projects)
    dialog.type_combo.setCurrentIndex(dialog.type_combo.findData(kind['id']))
    def manage(manager):
        service.rename_project_type(kind['id'], '管理后名称')
        manager.types_changed.emit()
        return QDialog.Accepted
    monkeypatch.setattr(project_types_ui.ProjectTypesDialog, 'exec', manage)
    dialog.manage_types()
    assert dialog.type_combo.currentData() == kind['id']
    assert dialog.type_combo.currentText() == '管理后名称'
    dialog.reject()
    assert next(t for t in service.list_project_types() if t['id'] == kind['id'])['name'] == '管理后名称'
    assert all(service.get_project(p['id'])['project_type_id'] is None for p in projects)


def test_type_refresh_error_and_archived_projects_fail_closed(qapp, tmp_path, monkeypatch):
    service, projects = fixture_projects(tmp_path)
    dialog = project_types_ui.ProjectTypePickerDialog(service, projects)
    monkeypatch.setattr(service, 'list_project_types', lambda **kw: (_ for _ in ()).throw(RuntimeError('读取失败')))
    dialog.refresh_types()
    assert '读取失败' in dialog.error.text()
    assert not dialog.save_button.isEnabled()
    dialog.update_choice()
    assert not dialog.save_button.isEnabled()
    projects[0]['archived_at'] = '2026-10-29'
    blocked = project_types_ui.ProjectTypePickerDialog(service, projects)
    assert not blocked.save_button.isEnabled()


def test_picker_long_name_preview_and_native_frame(qapp, tmp_path):
    service, projects = fixture_projects(tmp_path)
    name = '项目类型名称' * 6
    kind = service.create_project_type(name)
    dialog = project_types_ui.ProjectTypePickerDialog(service, projects)
    dialog.type_combo.setCurrentIndex(dialog.type_combo.findData(kind['id']))
    dialog.resize(360, 340)
    dialog.show()
    qapp.processEvents()
    assert not dialog.windowFlags() & Qt.FramelessWindowHint
    assert dialog.current_name.text() == name
    assert dialog.current_name.wordWrap()
    assert dialog.type_combo.width() <= dialog.width()
    dialog.close()


def test_deleted_dictionary_choice_remains_unavailable_after_refresh(qapp, tmp_path, monkeypatch):
    service, projects = fixture_projects(tmp_path)
    kind = service.create_project_type('外部移除类型')
    dialog = project_types_ui.ProjectTypePickerDialog(service, projects)
    dialog.type_combo.setCurrentIndex(dialog.type_combo.findData(kind['id']))
    original = service.list_project_types
    monkeypatch.setattr(service, 'list_project_types', lambda **kw: [t for t in original(**kw) if t['id'] != kind['id']])
    dialog.refresh_types()
    assert dialog.type_combo.currentData() == kind['id']
    assert '不可用' in dialog.type_combo.currentText()
    assert not dialog.save_button.isEnabled()
    dialog.accept()
    assert dialog.result() != QDialog.Accepted
    dialog.type_combo.setCurrentIndex(dialog.type_combo.findData(None))
    dialog.accept()
    assert dialog.result() == QDialog.Accepted


def test_manage_can_add_type_then_choose_and_cancel_assignment(qapp, tmp_path, monkeypatch):
    service, projects = fixture_projects(tmp_path)
    dialog = project_types_ui.ProjectTypePickerDialog(service, projects)
    created = []
    def manage(manager):
        created.append(service.create_project_type('管理中新增类型'))
        manager.types_changed.emit()
        return QDialog.Accepted
    monkeypatch.setattr(project_types_ui.ProjectTypesDialog, 'exec', manage)
    dialog.manage_types()
    index = dialog.type_combo.findData(created[0]['id'])
    assert index >= 0
    dialog.type_combo.setCurrentIndex(index)
    assert dialog.save_button.isEnabled()
    dialog.reject()
    assert all(service.get_project(p['id'])['project_type_id'] is None for p in projects)


@pytest.mark.parametrize('error_type', [OSError, ValueError, RuntimeError, sqlite3.OperationalError, KeyError])
def test_manage_read_error_preserves_choice_and_allows_retry(qapp, tmp_path, monkeypatch, error_type):
    service, projects = fixture_projects(tmp_path)
    kind = service.create_project_type('保留当前选择')
    dialog = project_types_ui.ProjectTypePickerDialog(service, projects)
    dialog.type_combo.setCurrentIndex(dialog.type_combo.findData(kind['id']))
    original = service.list_project_types
    monkeypatch.setattr(service, 'list_project_types', lambda **kw: (_ for _ in ()).throw(error_type('管理读取失败')))
    dialog.manage_types()
    assert '管理读取失败' in dialog.error.text()
    assert dialog.type_combo.currentData() == kind['id']
    assert not dialog.save_button.isEnabled()
    assert dialog.manage_button.isEnabled()
    assert all(service.get_project(p['id'])['project_type_id'] is None for p in projects)
    monkeypatch.setattr(service, 'list_project_types', original)
    monkeypatch.setattr(project_types_ui.ProjectTypesDialog, 'exec', lambda self: QDialog.Rejected)
    dialog.manage_types()
    assert not dialog.error.text()
    assert dialog.type_combo.currentData() == kind['id']
    assert dialog.save_button.isEnabled()


def test_manage_does_not_hide_unexpected_errors(qapp, tmp_path, monkeypatch):
    service, projects = fixture_projects(tmp_path)
    dialog = project_types_ui.ProjectTypePickerDialog(service, projects)
    monkeypatch.setattr(service, 'list_project_types', lambda **kw: (_ for _ in ()).throw(TypeError('未知错误')))
    with pytest.raises(TypeError, match='未知错误'):
        dialog.manage_types()
