"""新项目默认类型使用本机真实服务，即时保存并保留失败前选择。"""
import sqlite3

import pytest
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from desktop_service import DesktopService
from project_types_ui import ProjectTypesDialog


@pytest.fixture(scope='module')
def qapp():
    return QApplication.instance() or QApplication([])


def test_default_type_initialization_and_refresh_do_not_write(qapp, tmp_path, monkeypatch):
    service = DesktopService(tmp_path / 'data/workbox.db')
    calls = []
    monkeypatch.setattr(service, 'set_default_project_type', lambda value: calls.append(value))
    dialog = ProjectTypesDialog(service)
    assert dialog.default_type_combo.currentData() == service.get_default_project_type()
    assert dialog.default_type_combo.accessibleName() == '新项目默认类型'
    dialog.refresh()
    assert calls == []


def test_default_type_changes_save_immediately_and_notify_without_backfill(qapp, tmp_path):
    service = DesktopService(tmp_path / 'data/workbox.db')
    service.capture_message('既有项目', '隔离批次')
    before = service.list_projects()[0]
    kind = service.create_project_type('后续类型')
    dialog = ProjectTypesDialog(service)
    spy = QSignalSpy(dialog.types_changed)
    dialog.default_type_combo.setCurrentIndex(dialog.default_type_combo.findData(kind['id']))
    assert service.get_default_project_type() == kind['id']
    assert spy.count() == 1
    dialog.reject()
    assert service.get_default_project_type() == kind['id']
    assert service.get_project(before['id'])['project_type_id'] == before['project_type_id']


def test_default_type_failure_restores_selection_and_allows_retry(qapp, tmp_path, monkeypatch):
    service = DesktopService(tmp_path / 'data/workbox.db')
    dialog = ProjectTypesDialog(service)
    previous = service.get_default_project_type()
    save = service.set_default_project_type
    def fail(value):
        raise sqlite3.OperationalError('隔离写入失败')
    monkeypatch.setattr(service, 'set_default_project_type', fail)
    spy = QSignalSpy(dialog.types_changed)
    dialog.default_type_combo.setCurrentIndex(dialog.default_type_combo.findData(None))
    assert dialog.default_type_combo.currentData() == previous
    assert service.get_default_project_type() == previous
    assert '隔离写入失败' in dialog.error.text()
    assert spy.count() == 0
    monkeypatch.setattr(service, 'set_default_project_type', save)
    dialog.default_type_combo.setCurrentIndex(dialog.default_type_combo.findData(None))
    assert service.get_default_project_type() is None
    assert dialog.error.text() == ''
    assert spy.count() == 1


def test_default_type_rename_keeps_id_and_disable_clears_option(qapp, tmp_path):
    service = DesktopService(tmp_path / 'data/workbox.db')
    dialog = ProjectTypesDialog(service)
    default = service.get_default_project_type()
    service.rename_project_type(default, '改名后的默认类型')
    dialog.refresh()
    assert dialog.default_type_combo.currentData() == default
    assert dialog.default_type_combo.currentText() == '改名后的默认类型'
    service.set_project_type_active(default, False)
    dialog.refresh()
    assert dialog.default_type_combo.currentData() is None
    assert dialog.default_type_combo.findData(default) == -1
    assert dialog.default_type_combo.itemText(0) == '未分类'
