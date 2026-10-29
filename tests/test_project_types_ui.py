import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from compact_ui import CompactPanel
from desktop_service import DesktopService


@pytest.fixture(scope='module')
def qapp():
    return QApplication.instance() or QApplication([])


def test_project_type_filter_context_and_bulk_use_real_ids(qapp, tmp_path):
    service = DesktopService(tmp_path / 'workbox.db')
    service.db.create_capture('隔离验证', '资料', [('资料工具', False), ('归档工具', False)])
    first, second = service.list_projects()
    kind = service.create_project_type('工具')
    kind_id = kind['id'] if isinstance(kind, dict) else kind
    panel = CompactPanel(service)
    try:
        menu = panel.project_context_menu(second['id'])
        submenu = next(a.menu() for a in menu.actions() if a.text() == '项目类型')
        next(a for a in submenu.actions() if a.data() == kind_id).trigger()
        assert service.get_project(second['id'])['project_type_id'] == kind_id
        assert service.get_project(first['id'])['project_type_id'] is None
        panel.type_filter.setCurrentIndex(panel.type_filter.findData(kind_id))
        assert panel.project_list.count() == 1
        assert panel.project_list.item(0).data(Qt.UserRole) == second['id']
        assert '工具' in panel.project_list.item(0).text()
        panel.type_filter.setCurrentIndex(panel.type_filter.findData(0))
        assert panel.project_list.count() == 1
        panel.bulk_mode.setChecked(True)
        panel.project_list.selectAll()
        panel.bulk_action.setCurrentIndex(panel.bulk_action.findData(f'type:{kind_id}'))
        panel.apply_bulk()
        assert service.get_project(first['id'])['project_type_id'] == kind_id
    finally:
        panel.document_pane.shutdown()
        panel.hide()


def test_type_manager_cancel_and_service_error_keep_data(qapp, tmp_path, monkeypatch):
    from project_types_ui import ProjectTypesDialog
    import project_types_ui
    service = DesktopService(tmp_path / 'workbox.db')
    dialog = ProjectTypesDialog(service)
    monkeypatch.setattr(project_types_ui.QInputDialog, 'getText', lambda *a, **kw: ('取消类型', False))
    dialog.add_type()
    assert service.list_project_types() == []
    monkeypatch.setattr(project_types_ui.QInputDialog, 'getText', lambda *a, **kw: ('工具', True))
    dialog.add_type()
    assert len(service.list_project_types()) == 1
    monkeypatch.setattr(service, 'create_project_type', lambda *a: (_ for _ in ()).throw(ValueError('名称已存在')))
    dialog.add_type()
    assert '名称已存在' in dialog.error.text()
    dialog.reject()
    assert len(service.list_project_types()) == 1


def test_type_manager_rename_disable_and_enable_are_immediate(qapp, tmp_path, monkeypatch):
    from project_types_ui import ProjectTypesDialog
    import project_types_ui
    service = DesktopService(tmp_path / 'workbox.db')
    service.create_project_type('工具')
    dialog = ProjectTypesDialog(service)
    dialog.type_list.setCurrentRow(0)
    monkeypatch.setattr(project_types_ui.QInputDialog, 'getText', lambda *a, **kw: ('内部工具', True))
    dialog.rename_type()
    assert service.list_project_types()[0]['name'] == '内部工具'
    dialog.toggle_type()
    assert service.list_project_types() == []
    assert dialog.toggle_button.text() == '启用'
    dialog.toggle_type()
    assert service.list_project_types()[0]['active']
    dialog.reject()
    assert service.list_project_types()[0]['name'] == '内部工具'


def test_inactive_current_type_remains_visible_and_cannot_be_assigned(qapp, tmp_path):
    service = DesktopService(tmp_path / 'workbox.db')
    service.db.create_capture('隔离验证', '资料', [('归档工具', False)])
    project = service.list_projects()[0]
    kind = service.create_project_type('工具')
    kind_id = kind['id'] if isinstance(kind, dict) else kind
    service.set_project_types([project['id']], kind_id)
    service.set_project_type_active(kind_id, False)
    panel = CompactPanel(service)
    try:
        menu = panel.project_context_menu(project['id'])
        action = next(a for a in menu._type_menu.actions() if a.data() == kind_id)
        assert action.isChecked()
        assert not action.isEnabled()
        assert '已停用' in action.text()
        assert '工具' in panel.project_list.item(0).text()
        filter_index = panel.type_filter.findData(kind_id)
        assert filter_index >= 0
        assert '已停用' in panel.type_filter.itemText(filter_index)
        panel.type_filter.setCurrentIndex(filter_index)
        assert panel.project_list.count() == 1
        assert panel.bulk_action.findData(f'type:{kind_id}') == -1
    finally:
        panel.document_pane.shutdown()
        panel.hide()
