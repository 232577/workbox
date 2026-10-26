import importlib
from datetime import date, timedelta
from pathlib import Path
from zipfile import ZipFile

import pytest
from PySide6.QtCore import QDate, Qt, QTimer, QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from desktop_controller import FloatingController
from desktop_service import DesktopService
from compact_ui import ProjectDeliveryDialog


@pytest.fixture(scope='module')
def qapp():
    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    return application


@pytest.fixture
def app(qapp, tmp_path):
    controller = FloatingController(DesktopService(tmp_path / 'workbox.db'), tray_enabled=False)
    assert controller.start()
    controller.service.receive_text('1. 客户档案系统\n2. 合同归档工具\n3. 订单进度工具')
    controller.show_panel()
    yield controller
    controller.shutdown()


def wait(controller):
    for _ in range(800):
        QApplication.processEvents()
        if not controller.busy:
            return
        QTest.qWait(10)
    pytest.fail('项目操作未在限定时间内完成')


def choose(panel, pid):
    for index in range(panel.project_list.count()):
        if panel.project_list.item(index).data(Qt.UserRole) == pid:
            panel.project_list.setCurrentRow(index)
            return
    pytest.fail(f'列表中缺少项目 {pid}')


def accept_question():
    for widget in QApplication.topLevelWidgets():
        if isinstance(widget, QMessageBox) and widget.isVisible():
            widget.button(QMessageBox.Yes).click()


def project_action(panel, project_id, text):
    menu = panel._test_project_menu = panel.project_context_menu(project_id)
    for item in menu.actions():
        if item.text() == text:
            return item
        submenu = item.menu()
        if submenu:
            for child in submenu.actions():
                if child.text() == text:
                    return child
    pytest.fail(f'项目右键菜单缺少：{text}')


def test_notes_and_optional_deadline_save_clear_and_survive_restart(app):
    module = importlib.import_module('compact_ui')
    assert hasattr(module, 'ProjectDetailsDialog'), '缺少项目备注与日期编辑入口'
    dialog = module.ProjectDetailsDialog(app.service, app.service.get_project(1), app.panel)
    dialog.notes.setPlainText('客户要求：\n保留原始单据。')
    dialog.due_enabled.setChecked(True)
    dialog.due_date.setDate(QDate(2026, 11, 5))
    dialog.save_button.click()
    saved = DesktopService(app.service.db.path).get_project(1)
    assert saved['notes'] == '客户要求：\n保留原始单据。'
    assert saved['due_date'] == '2026-11-05'
    dialog = module.ProjectDetailsDialog(app.service, saved, app.panel)
    dialog.due_enabled.setChecked(False)
    dialog.save_button.click()
    assert app.service.get_project(1)['due_date'] is None


def test_notes_validation_preserves_input_and_cancel_leaves_database_unchanged(app):
    module = importlib.import_module('compact_ui')
    assert hasattr(module, 'ProjectDetailsDialog'), '缺少备注保存校验'
    dialog = module.ProjectDetailsDialog(app.service, app.service.get_project(1), app.panel)
    dialog.show()
    dialog.notes.setPlainText('长' * 10001)
    dialog.save_button.click()
    assert dialog.isVisible() and dialog.error.text()
    assert len(dialog.notes.toPlainText()) == 10001
    assert app.service.get_project(1)['notes'] == ''
    QTimer.singleShot(40, accept_question)
    dialog.reject()
    assert app.service.get_project(1)['notes'] == ''


def test_editing_notes_does_not_clamp_an_existing_early_calendar_date(app):
    from compact_ui import ProjectDetailsDialog
    app.service.update_project_details(1, '原备注', '1899-12-31')
    dialog = ProjectDetailsDialog(app.service, app.service.get_project(1), app.panel)
    dialog.notes.setPlainText('只修改备注')
    dialog.save_button.click()
    assert app.service.get_project(1)['due_date'] == '1899-12-31'


def test_panel_opens_notes_editor_and_refreshes_searchable_result(app):
    panel = app.panel
    assert project_action(panel, 1, '备注与日期…').isEnabled(), '主面板尚未接入备注菜单入口'
    choose(panel, 1)
    def edit():
        for dialog in QApplication.topLevelWidgets():
            if type(dialog).__name__ == 'ProjectDetailsDialog' and dialog.isVisible():
                dialog.notes.setPlainText('等待发票回执')
                dialog.save_button.click()
    QTimer.singleShot(40, edit)
    project_action(panel, 1, '备注与日期…').trigger()
    panel.search.setText('发票回执')
    assert panel.project_list.count() == 1
    assert panel.project_list.item(0).data(Qt.UserRole) == 1


def test_bulk_changes_only_selected_projects_and_archive_restore_keeps_details(app):
    panel = app.panel
    assert hasattr(panel, 'bulk_mode'), '缺少批量选择入口'
    app.service.update_project_details(1, '必须保留的备注', '2026-11-05')
    panel.bulk_mode.setChecked(True)
    for index in range(panel.project_list.count()):
        item = panel.project_list.item(index)
        item.setSelected(item.data(Qt.UserRole) in (1, 2))
    panel.bulk_action.setCurrentIndex(panel.bulk_action.findData('status:开发中'))
    panel.bulk_apply.click()
    wait(app)
    assert app.service.get_project(1)['status'] == '开发中'
    assert app.service.get_project(2)['status'] == '开发中'
    assert app.service.get_project(3)['status'] == '待开发'
    panel.bulk_action.setCurrentIndex(panel.bulk_action.findData('archive'))
    QTimer.singleShot(40, accept_question)
    panel.bulk_apply.click()
    wait(app)
    assert panel.project_list.count() == 1 and app.orb.count == 1
    panel.archive_filter.setCurrentIndex(panel.archive_filter.findData(True))
    assert panel.project_list.count() == 2
    panel.select_all_button.click()
    panel.bulk_action.setCurrentIndex(panel.bulk_action.findData('restore'))
    panel.bulk_apply.click()
    wait(app)
    assert panel.project_list.count() == 0 and app.orb.count == 3
    restored = app.service.get_project(1)
    assert restored['notes'] == '必须保留的备注'
    assert restored['due_date'] == '2026-11-05'
    assert restored['status'] == '开发中' and restored['archived_at'] is None


def test_deadline_filter_clears_hidden_bulk_selection(app):
    panel = app.panel
    assert hasattr(panel, 'due_filter'), '缺少截止日期筛选'
    app.service.update_project_details(1, '', (date.today() - timedelta(days=1)).isoformat())
    panel.refresh()
    panel.bulk_mode.setChecked(True)
    panel.select_all_button.click()
    assert len(panel.project_list.selectedItems()) == 3
    panel.due_filter.setCurrentIndex(panel.due_filter.findData('overdue'))
    assert panel.project_list.count() == 1
    assert not panel.project_list.selectedItems() and not panel.bulk_apply.isEnabled()
    panel.due_filter.setCurrentIndex(0)
    assert panel.project_list.count() == 3 and not panel.project_list.selectedItems()


def test_archive_is_read_only_and_can_be_restored_from_single_project(app):
    panel = app.panel
    assert project_action(panel, 1, '归档项目').isEnabled(), '缺少归档与恢复菜单入口'
    choose(panel, 1)
    QTimer.singleShot(40, accept_question)
    project_action(panel, 1, '归档项目').trigger()
    wait(app)
    panel.archive_filter.setCurrentIndex(panel.archive_filter.findData(True))
    choose(panel, 1)
    assert not project_action(panel, 1, '开发状态').isEnabled() and not project_action(panel, 1, '改项目名…').isEnabled()
    assert project_action(panel, 1, '看原文').isEnabled() and project_action(panel, 1, '查看备注与日期…').isEnabled()
    project_action(panel, 1, '恢复项目').trigger()
    wait(app)
    assert app.service.get_project(1)['archived_at'] is None


def test_new_small_package_returns_pending_and_selects_latest_without_deleting_history(app, tmp_path):
    for version in (1, 2):
        source = tmp_path / f'客户档案系统_v{version}.0.zip'
        with ZipFile(source, 'w') as archive:
            archive.writestr('test.txt', str(version))
        record = app.service.import_zip(source)['zip']
        if version == 1:
            confirmed_id = record['id']
            app.service.confirm_delivery(1, confirmed_id, confirmed=True)
    app.refresh()
    choose(app.panel, 1)
    dialog = ProjectDeliveryDialog(app.service.get_project(1), app.panel)
    assert dialog.version_box.itemData(1) == record['id'], '最近版本应排在ZIP版本首位'
    dialog.version_box.setCurrentIndex(dialog.version_box.findData(record['id']))
    assert dialog.version_box.currentData() == record['id']
    project = app.service.get_project(1)
    assert project['status'] == '待交付' and project['delivered_zip_id'] is None
    assert all(Path(package['path']).exists() for package in project['zips'])
    assert app.service.db.delivery_events_for_project(1)[0]['zip_id'] == confirmed_id


def test_new_controls_fit_compact_panel_and_bulk_mode(app, qapp):
    panel = app.panel
    assert hasattr(panel, 'bulk_mode'), '缺少紧凑批量工具栏'
    panel.resize(660, 492)
    choose(panel, 1)
    qapp.processEvents()
    assert panel.width() <= 660
    assert panel.document_pane.parentWidget() is panel.document_side
    assert panel.rect().contains(panel.document_pane.mapTo(panel, panel.document_pane.rect().bottomRight()))
    menu = panel.project_context_menu(1)
    menu.popup(panel.project_list.mapToGlobal(QPoint(20, 20)))
    qapp.processEvents()
    assert all(menu.rect().contains(menu.actionGeometry(item)) for item in menu.actions() if not item.isSeparator())
    menu.close()
    dialog = ProjectDeliveryDialog(app.service.get_project(1), panel)
    dialog.show()
    qapp.processEvents()
    for widget in (dialog.version_box, dialog.confirm_button, dialog.cancel_button):
        assert widget.height() >= widget.minimumSizeHint().height()
        assert dialog.rect().contains(widget.mapTo(dialog, widget.rect().bottomRight()))
    dialog.cancel_button.click()
    panel.bulk_mode.setChecked(True)
    qapp.processEvents()
    assert panel.rect().contains(panel.bulk_apply.mapTo(panel, panel.bulk_apply.rect().bottomRight()))


def test_background_refresh_keeps_chosen_delivery_version_and_confirmation_names_it(app, tmp_path):
    packages = []
    for version in (1, 2):
        source = tmp_path / f'客户档案系统_v{version}.0.zip'
        with ZipFile(source, 'w') as archive:
            archive.writestr('test.txt', str(version))
        packages.append(app.service.import_zip(source)['zip'])
    app.service.snapshots.approve_version(packages[0]['id'])
    app.refresh()
    choose(app.panel, 1)
    prompts = []
    def decline():
        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, ProjectDeliveryDialog) and widget.isVisible():
                assert widget.version_box.findData(packages[0]['id']) >= 0
                widget.version_box.setCurrentIndex(widget.version_box.findData(packages[1]['id']))
                app.refresh()
                assert widget.version_box.currentData() == packages[1]['id'], '后台刷新不得改掉人工选择的交付包'
                prompts.append(widget.version_box.currentText())
                widget.cancel_button.click()
    QTimer.singleShot(40, decline)
    project_action(app.panel, 1, '确认交付…').trigger()
    assert packages[1]['filename'] in prompts[0]
    assert app.service.get_project(1)['status'] == '待交付'
