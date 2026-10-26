"""项目菜单只作用于调用时的项目，隔离数据库，不打开真实目录。"""

from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtWidgets import QApplication, QDialog, QInputDialog, QMenu, QMessageBox, QPlainTextEdit

from compact_ui import CompactPanel
from desktop_service import DesktopService


@pytest.fixture(scope='module')
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


@pytest.fixture
def panel(qapp, tmp_path):
    service = DesktopService(tmp_path / 'workbox.db')
    service.db.create_capture('甲系统\n乙系统', '本批', [('甲系统', False), ('乙系统', False)])
    widget = CompactPanel(service)
    yield widget
    widget.close()
    widget.deleteLater()
    QApplication.processEvents()


def action(menu, text):
    for item in menu.actions():
        if item.text() == text:
            return item
        if item.menu():
            try:
                return action(item.menu(), text)
            except AssertionError:
                pass
    raise AssertionError(f'缺少菜单项：{text}')


def test_right_side_has_documents_and_no_retained_project_operation_widgets(panel):
    assert panel.document_pane.parentWidget() is panel.document_side
    for name in ('project_operations', 'detail', 'state_box', 'urgent_box', 'workspace_button',
                 'notes_button', 'rename_button', 'source_button', 'delivery_button', 'zip_box'):
        assert not hasattr(panel, name), f'不应保留旧项目操作控件 {name}'


def test_standard_menu_contains_all_project_actions(panel):
    menu = panel.project_context_menu(1)
    assert isinstance(menu, QMenu)
    for text in ('建工作目录', '开发状态', '待开发', '开发中', '待交付', '加急',
                 '备注与日期…', '改项目名…', '看原文', '归档项目', '确认交付…', 'ZIP 位置'):
        assert action(menu, text)
    assert action(menu, '待开发').isChecked()
    assert action(menu, '确认交付…').isEnabled() is False


def test_menu_actions_capture_clicked_project_instead_of_selection_or_batch(panel):
    panel.bulk_mode.setChecked(True)
    panel.project_list.selectAll()
    changed, urgent, folders = [], [], []
    panel.state_requested.connect(lambda *args: changed.append(args))
    panel.urgent_requested.connect(lambda *args: urgent.append(args))
    panel.workspace_requested.connect(folders.append)
    menu = panel.project_context_menu(2)
    action(menu, '开发中').trigger()
    action(menu, '加急').trigger()
    action(menu, '建工作目录').trigger()
    assert changed == [(2, '开发中')]
    assert urgent == [(2, True)]
    assert folders == [2]


def test_mouse_menu_resolves_item_at_position_and_blank_does_nothing(panel):
    panel.project_list.project_context_requested.disconnect()
    panel.show()
    QApplication.processEvents()
    panel.project_list.setCurrentRow(0)
    received = []
    panel.project_list.project_context_requested.connect(lambda *args: received.append(args))
    second = panel.project_list.item(1)
    point = panel.project_list.visualItemRect(second).center()
    panel.project_list.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Mouse, point, point))
    assert received[0][0] == second.data(Qt.UserRole)
    blank = QPoint(4, panel.project_list.viewport().height() - 2)
    assert panel.project_list.itemAt(blank) is None
    panel.project_list.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Mouse, blank, blank))
    assert len(received) == 1


def test_keyboard_menu_uses_focused_row(panel, monkeypatch):
    panel.project_list.project_context_requested.disconnect()
    panel.project_list.setCurrentRow(1)
    received = []
    panel.project_list.project_context_requested.connect(lambda *args: received.append(args))
    panel.project_list.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Keyboard, QPoint(), QPoint()))
    assert received[0][0] == panel.project_list.item(1).data(Qt.UserRole)


def test_archived_and_busy_menu_guards(panel):
    panel.service.db.bulk_update_projects([1], archived=True)
    menu = panel.project_context_menu(1)
    for text in ('开发状态', '加急', '改项目名…', '确认交付…', '建工作目录'):
        assert not action(menu, text).isEnabled()
    assert action(menu, '恢复项目').isEnabled()
    assert action(menu, '查看备注与日期…').isEnabled()
    panel.set_busy(True)
    menu = panel.project_context_menu(2)
    assert all(not item.isEnabled() for item in menu.actions() if not item.isSeparator())


def test_action_rechecks_busy_and_archival_after_menu_created(panel):
    received = []
    panel.state_requested.connect(lambda *args: received.append(args))
    menu = panel.project_context_menu(1)
    panel.set_busy(True)
    action(menu, '开发中').trigger()
    panel.set_busy(False)
    panel.service.db.bulk_update_projects([1], archived=True)
    action(menu, '开发中').trigger()
    assert received == []


def test_archive_confirmation_targets_one_id_and_defaults_to_no(panel, monkeypatch):
    questions, received = [], []
    def yes(*args):
        questions.append(args)
        return QMessageBox.Yes
    monkeypatch.setattr(QMessageBox, 'question', yes)
    panel.bulk_requested.connect(lambda *args: received.append(args))
    panel.project_list.setCurrentRow(0)
    action(panel.project_context_menu(2), '归档项目').trigger()
    assert received == [([2], {'archived': True})]
    assert questions[0][-1] == QMessageBox.No


def test_delivery_dialog_supports_explicit_material_choice_and_guards_review(panel):
    from compact_ui import ProjectDeliveryDialog
    project = panel.service.get_project(1)
    project['status'] = '待交付'
    project['zips'] = [dict(id=7, filename='甲系统.zip', path='D:/isolated/甲系统.zip',
                            sha256='0' * 64, bundle_state='needs-review')]
    dialog = ProjectDeliveryDialog(project, panel)
    assert dialog.version_box.findData(None) >= 0
    dialog.version_box.setCurrentIndex(dialog.version_box.findData(7))
    assert not dialog.confirm_button.isEnabled()
    dialog.version_box.setCurrentIndex(dialog.version_box.findData(None))
    assert dialog.confirm_button.isEnabled()
    assert '保留原件' in dialog.explanation.text()
    assert dialog.cancel_button.isDefault()


def test_invalid_id_has_no_menu_and_double_click_still_emits_clicked_id(panel):
    assert panel.project_context_menu(99999) is None
    received = []
    panel.workspace_requested.connect(received.append)
    panel.project_list.itemDoubleClicked.emit(panel.project_list.item(1))
    assert received == [panel.project_list.item(1).data(Qt.UserRole)]


def test_real_menu_route_keeps_actual_clicked_id(panel, monkeypatch):
    emitted = []
    panel.state_requested.connect(lambda *args: emitted.append(args))
    factory = panel.project_context_menu
    def prepare(project_id):
        menu = factory(project_id)
        def activate():
            try:
                action(menu, '开发中').trigger()
            finally:
                menu.close()
        QTimer.singleShot(0, activate)
        return menu
    monkeypatch.setattr(panel, 'project_context_menu', prepare)
    panel.project_list.setCurrentRow(0)
    panel.project_list.project_context_requested.emit(2, QPoint(2, 3))
    assert emitted == [(2, '开发中')]


def test_rename_and_source_use_menu_id_when_selection_changes(panel, monkeypatch):
    received, source_text = [], []
    panel.rename_requested.connect(lambda *args: received.append(args))
    menu = panel.project_context_menu(2)
    panel.project_list.setCurrentRow(0)
    def name(*args, **kwargs):
        assert kwargs['text'] == '乙系统'
        return '乙系统二期', True
    monkeypatch.setattr(QInputDialog, 'getText', name)
    action(menu, '改项目名…').trigger()
    def source(dialog):
        source_text.append(dialog.findChild(QPlainTextEdit).toPlainText())
        return QDialog.Rejected
    monkeypatch.setattr(QDialog, 'exec', source)
    action(menu, '看原文').trigger()
    assert received == [(2, '乙系统二期')]
    assert source_text == ['甲系统\n乙系统']


def test_notes_dialog_uses_menu_id_and_cancel_preserves_data(panel, monkeypatch):
    from compact_ui import ProjectDetailsDialog
    panel.service.update_project_details(1, '甲备注', None)
    panel.service.update_project_details(2, '乙备注', None)
    panel.project_list.setCurrentRow(0)
    seen = []
    def edit(dialog):
        seen.append(dialog.notes.toPlainText())
        dialog.notes.setPlainText('取消的改动')
        return QDialog.Rejected
    monkeypatch.setattr(ProjectDetailsDialog, 'exec', edit)
    action(panel.project_context_menu(2), '备注与日期…').trigger()
    assert seen == ['乙备注']
    assert panel.service.get_project(2)['notes'] == '乙备注'


def test_manual_delivery_choices_target_menu_id_and_cancel_emits_nothing(panel, monkeypatch, tmp_path):
    from zipfile import ZipFile
    from compact_ui import ProjectDeliveryDialog
    path = tmp_path / '乙系统.zip'
    with ZipFile(path, 'w') as archive:
        archive.writestr('说明.txt', '乙材料')
    zip_id = panel.service.import_zip(path)['zip']['id']
    panel.service.db.set_status(2, '待交付')
    received = []
    panel.delivery_requested.connect(lambda *args: received.append(args))
    panel.project_list.setCurrentRow(0)
    menu = panel.project_context_menu(2)
    monkeypatch.setattr(ProjectDeliveryDialog, 'exec', lambda dialog: QDialog.Rejected)
    action(menu, '确认交付…').trigger()
    assert received == []
    monkeypatch.setattr(ProjectDeliveryDialog, 'exec', lambda dialog: QDialog.Accepted)
    action(menu, '确认交付…').trigger()
    assert received == [(2, None)]
    def choose_version(dialog):
        dialog.version_box.setCurrentIndex(dialog.version_box.findData(zip_id))
        return QDialog.Accepted
    monkeypatch.setattr(ProjectDeliveryDialog, 'exec', choose_version)
    action(menu, '确认交付…').trigger()
    assert received[-1] == (2, zip_id)


def test_zip_location_is_read_only_and_belongs_to_menu_project(panel, tmp_path):
    from zipfile import ZipFile
    path = tmp_path / '乙系统.zip'
    with ZipFile(path, 'w') as archive:
        archive.writestr('说明.txt', '乙材料')
    zip_id = panel.service.import_zip(path)['zip']['id']
    received = []
    panel.folder_requested.connect(received.append)
    before = panel.service.get_project(2)
    menu = panel.project_context_menu(2)
    positions = action(menu, 'ZIP 位置').menu()
    positions.actions()[0].trigger()
    panel.locate_project_zip(1, zip_id)
    assert received == [str(Path(before['zips'][0]['path']).parent)]
    assert panel.service.get_project(2) == before


def test_archived_existing_workspace_can_open_without_enabling_mutation(panel):
    panel.service.ensure_workspaces(1)
    panel.service.db.bulk_update_projects([1], archived=True)
    received = []
    panel.workspace_requested.connect(received.append)
    menu = panel.project_context_menu(1)
    assert action(menu, '工作目录').isEnabled()
    action(menu, '工作目录').trigger()
    assert received == [1]
