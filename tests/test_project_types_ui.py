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
    service.set_default_project_type(None)
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
    initial = service.list_project_types()
    dialog = ProjectTypesDialog(service)
    monkeypatch.setattr(project_types_ui.QInputDialog, 'getText', lambda *a, **kw: ('取消类型', False))
    dialog.add_type()
    assert service.list_project_types() == initial
    monkeypatch.setattr(project_types_ui.QInputDialog, 'getText', lambda *a, **kw: ('工具', True))
    dialog.add_type()
    assert len(service.list_project_types()) == len(initial) + 1
    monkeypatch.setattr(service, 'create_project_type', lambda *a: (_ for _ in ()).throw(ValueError('名称已存在')))
    dialog.add_type()
    assert '名称已存在' in dialog.error.text()
    dialog.reject()
    assert len(service.list_project_types()) == len(initial) + 1


def test_type_manager_rename_disable_and_enable_are_immediate(qapp, tmp_path, monkeypatch):
    from project_types_ui import ProjectTypesDialog
    import project_types_ui
    service = DesktopService(tmp_path / 'workbox.db')
    kind = service.create_project_type('工具')
    dialog = ProjectTypesDialog(service)
    item = next(dialog.type_list.item(i) for i in range(dialog.type_list.count())
                if dialog.type_list.item(i).data(Qt.UserRole)['id'] == kind['id'])
    dialog.type_list.setCurrentItem(item)
    monkeypatch.setattr(project_types_ui.QInputDialog, 'getText', lambda *a, **kw: ('内部工具', True))
    dialog.rename_type()
    assert next(t for t in service.list_project_types() if t['id'] == kind['id'])['name'] == '内部工具'
    dialog.toggle_type()
    assert not any(t['id'] == kind['id'] for t in service.list_project_types())
    assert dialog.toggle_button.text() == '启用'
    dialog.toggle_type()
    assert next(t for t in service.list_project_types() if t['id'] == kind['id'])['active']
    dialog.reject()
    assert next(t for t in service.list_project_types() if t['id'] == kind['id'])['name'] == '内部工具'


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


def test_visible_type_entry_uses_selected_ids_and_preserves_project_state(qapp, tmp_path, monkeypatch):
    import project_types_ui
    service = DesktopService(tmp_path / 'entry.db')
    service.db.create_capture('隔离验证', '分类入口', [('甲工具', False), ('乙工具', False)])
    first, second = service.list_projects()
    kind = service.create_project_type('入口分类')
    panel = CompactPanel(service)
    called = []
    def choose(svc, parent, projects):
        called.append([p['id'] for p in projects])
        return True, kind['id']
    monkeypatch.setattr(project_types_ui, 'choose_project_type', choose)
    try:
        assert panel.change_type_button.text() == '修改类型…'
        target = next(panel.project_list.item(i) for i in range(panel.project_list.count())
                      if panel.project_list.item(i).data(Qt.UserRole) == second['id'])
        panel.project_list.setCurrentItem(target)
        before = service.get_project(second['id'])
        panel.change_type_button.click()
        after = service.get_project(second['id'])
        assert called == [[second['id']]]
        assert after['project_type_id'] == kind['id']
        for key in ('status', 'urgent', 'delivered_at', 'archived_at', 'deleted_at', 'notes', 'due_date'):
            assert before.get(key) == after.get(key)
        assert '入口分类' in panel.project_type_label.text()
        panel.bulk_mode.setChecked(True)
        panel.project_list.selectAll()
        panel.change_type_button.click()
        assert set(called[-1]) == {first['id'], second['id']}
        assert service.get_project(first['id'])['project_type_id'] == kind['id']
        panel.set_busy(True)
        assert not panel.change_type_button.isEnabled()
    finally:
        panel.document_pane.shutdown()
        panel.hide()


def test_type_entry_cancel_filter_removal_and_archived_boundary(qapp, tmp_path, monkeypatch):
    import project_types_ui
    service = DesktopService(tmp_path / 'entry-boundary.db')
    service.set_default_project_type(None)
    service.db.create_capture('隔离验证', '分类入口', [('边界项目', False)])
    project = service.list_projects()[0]
    kind = service.create_project_type('移出筛选')
    panel = CompactPanel(service)
    try:
        panel.project_list.setCurrentRow(0)
        monkeypatch.setattr(project_types_ui, 'choose_project_type', lambda *a: (False, None))
        panel.change_selected_project_types()
        assert service.get_project(project['id'])['project_type_id'] is None
        panel.type_filter.setCurrentIndex(panel.type_filter.findData(0))
        panel.project_list.setCurrentRow(0)
        monkeypatch.setattr(project_types_ui, 'choose_project_type', lambda *a: (True, kind['id']))
        panel.change_selected_project_types()
        assert panel.project_list.count() == 0
        assert not panel.project_list.selectedItems()
        assert panel.current_project is None
        service.bulk_update_projects([project['id']], archived=True)
        panel.archive_filter.setCurrentIndex(panel.archive_filter.findData(True))
        panel.type_filter.setCurrentIndex(0)
        panel.project_list.setCurrentRow(0)
        assert not panel.change_type_button.isEnabled()
    finally:
        panel.document_pane.shutdown()
        panel.hide()


def test_type_entry_narrow_geometry_and_assignment_failure(qapp, tmp_path, monkeypatch):
    import project_types_ui
    service = DesktopService(tmp_path / 'entry-error.db')
    service.db.create_capture('隔离验证', '分类入口', [('可读项目', False)])
    project = service.list_projects()[0]
    marker = tmp_path / '原材料.txt'
    marker.write_text('保留原文与文件', encoding='utf-8')
    panel = CompactPanel(service)
    try:
        panel.resize(660, 480)
        panel.show()
        panel.project_list.setCurrentRow(0)
        qapp.processEvents()
        control = panel.change_type_button
        assert control.isVisible()
        assert control.isEnabled()
        assert control.width() >= control.sizeHint().width()
        assert control.parentWidget().rect().contains(control.geometry())
        before = service.get_project(project['id'])
        monkeypatch.setattr(project_types_ui, 'choose_project_type', lambda *a: (True, None))
        monkeypatch.setattr(service, 'set_project_types', lambda *a: (_ for _ in ()).throw(RuntimeError('保存失败')))
        panel.change_selected_project_types()
        assert service.get_project(project['id']) == before
        assert marker.read_text(encoding='utf-8') == '保留原文与文件'
    finally:
        panel.document_pane.shutdown()
        panel.hide()
