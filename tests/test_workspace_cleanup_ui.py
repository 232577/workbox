from pathlib import Path
from time import monotonic, sleep
from zipfile import ZipFile

import pytest
from PySide6.QtCore import QPoint, QTimer, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QListWidget, QMenu, QPushButton, QStyle, QStyleOptionComboBox, QWidget

from compact_ui import CompactPanel
from desktop_service import DesktopService


@pytest.fixture
def panel(tmp_path):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    service = DesktopService(tmp_path / 'data/workbox.db')
    service.receive_text('1. 匿名检修平台\n2. 匿名作业平台')
    for project_id in (1, 2):
        workspace = Path(service.workspaces.get_workspace(project_id)['path'])
        (workspace / '匿名材料.txt').write_text('隔离文档', encoding='utf-8')
    value = CompactPanel(service)
    value.resize(660, 480)
    value.show()
    app.processEvents()
    yield value
    value.document_pane.shutdown()
    value.hide()


def wait_for_reader(pane):
    deadline = monotonic() + 5
    while pane.worker is not None and monotonic() < deadline:
        QApplication.processEvents()
        sleep(.01)
    assert pane.worker is None


def test_closed_filters_show_active_count_and_clear_without_touching_primary_scope(panel):
    panel.search.setText('匿名')
    panel.status_buttons['待开发'].click()
    assert panel.batch_filter.isVisible() and panel.bulk_mode.isVisible()
    panel.batch_filter.setCurrentIndex(1)
    assert not panel.due_filter.isVisible()
    QTest.keyClick(panel.project_filter_button, Qt.Key_Space)
    assert panel.due_filter.isVisible()
    panel.due_filter.setCurrentIndex(panel.due_filter.findData('unscheduled'))
    panel.project_list.setCurrentRow(0)
    item = panel.project_list.currentItem()
    assert item is not None
    QTest.keyClick(panel.project_filter_button, Qt.Key_Space)
    assert not panel.due_filter.isVisible()
    assert '1' in panel.project_filter_button.text() and panel.clear_project_filters.isVisible()
    assert '未设日期' in panel.project_filter_button.accessibleDescription()
    batch = panel.batch_filter
    option = QStyleOptionComboBox()
    batch.initStyleOption(option)
    field = batch.style().subControlRect(QStyle.CC_ComboBox, option, QStyle.SC_ComboBoxEditField, batch)
    assert field.width() >= batch.fontMetrics().horizontalAdvance(batch.currentText())
    assert panel.project_list.currentItem() is item and item.isSelected()
    panel.clear_project_filters.click()
    assert panel.due_filter.currentData() is None and not panel.clear_project_filters.isVisible()
    assert panel.search.text() == '匿名' and panel.status_buttons['待开发'].isChecked()
    assert panel.batch_filter.currentIndex() == 1
    assert panel.project_list.currentItem() is item and item.isSelected()
    assert panel.width() == 660 and panel.height() == 480


def test_expanding_project_filters_keeps_the_reading_anchor_and_selected_row(panel):
    panel.service.receive_text('\n'.join(f'{number}. 匿名巡检项目{number:02d}' for number in range(1, 19)))
    panel.refresh()
    projects = panel.project_list
    projects.setCurrentRow(0)
    selected = projects.currentItem()
    projects.verticalScrollBar().setValue(projects.verticalScrollBar().maximum() // 2)
    anchor = projects.itemAt(QPoint(2, 0))
    assert anchor is not None and anchor is not selected
    offset = projects.visualItemRect(anchor).top()
    for expanded in (True, False):
        panel.project_filter_button.setChecked(expanded)
        QApplication.processEvents()
        assert projects.currentItem() is selected and selected.isSelected()
        assert projects.itemAt(QPoint(2, 0)) is anchor
        assert projects.visualItemRect(anchor).top() == offset


def test_project_row_and_unselected_file_panel_only_show_applicable_content(panel):
    assert 'ZIP 0' not in panel.project_list.item(0).text()
    pane = panel.document_pane
    assert not pane.table.isVisible()
    assert not pane.open_button.isVisible()
    assert not panel.project_actions_button.isVisible()
    panel.project_list.setCurrentRow(0)
    panel.show_project_detail()
    assert panel.project_actions_button.isVisible() and panel.open_workspace_button.isVisible()
    assert panel.project_metadata.isVisible()
    assert any(action.text() == '项目类型' for action in panel.project_context_menu(panel.current_project['id']).actions())


def test_failed_document_read_offers_direct_retry_and_keeps_normal_refresh_in_menu(panel, monkeypatch):
    import project_documents_ui

    pane = panel.document_pane
    original = project_documents_ui.list_project_documents

    def fail(*_args, **_kwargs):
        raise OSError('匿名文档暂不可读')

    monkeypatch.setattr(project_documents_ui, 'list_project_documents', fail)
    panel.project_list.setCurrentRow(0)
    panel.show_project_detail()
    wait_for_reader(pane)
    assert '读取失败' in pane.hint.text()
    assert pane.refresh_button.isVisible() and pane.refresh_button.isEnabled()
    monkeypatch.setattr(project_documents_ui, 'list_project_documents', original)
    pane.refresh_button.click()
    wait_for_reader(pane)
    assert '读取失败' not in pane.hint.text()
    assert not pane.refresh_button.isVisible()
    assert isinstance(pane.file_menu_button.menu(), QMenu)
    assert any(action.text() == '刷新文档' and action.isEnabled() for action in pane.file_menu_button.menu().actions())
    assert not pane.folder_action.isEnabled()
    pane.table.selectRow(0)
    folder = QSignalSpy(pane.folder_requested)
    pane.folder_action.trigger()
    assert folder.count() == 1
    assert folder.at(0) == [str(Path(panel.current_project['workspace']['path']))]


def test_pending_review_hides_unrelated_search_and_preserves_project_query(panel):
    panel.search.setText('检修')
    panel.advanced_actions['review'].trigger()
    assert not panel.search.isVisible()
    assert not panel.review_button.isVisible() and not panel.dismiss_button.isVisible()
    panel.service.receive_text('这条匿名消息需要人工核对')
    panel.refresh()
    receipt = panel.service.pending_receipts()[0]
    assert panel.focus_receipt(receipt['id'])
    assert panel.review_button.isVisible() and panel.dismiss_button.isVisible()
    panel.nav_projects.click()
    assert panel.search.isVisible() and panel.search.text() == '检修'
    assert panel.project_list.count() == 1


def test_delivery_shell_hides_intake_controls_and_preserves_persistent_notice(panel):
    delivery = QWidget()
    panel.tabs.addTab(delivery, '交付记录管理')
    assert panel.paste_button.isVisible() and panel.files_button.isVisible()
    panel.tabs.setCurrentWidget(delivery)
    assert not panel.paste_button.isVisible() and not panel.files_button.isVisible()
    panel.nav_projects.click()
    panel._notice_timer.setInterval(10)
    panel.show_notice('匿名文件已收录', kind='intake-success')
    assert panel.notice.isVisible()
    QTest.qWait(30)
    assert not panel.notice.isVisible()
    panel.show_notice('匿名文件已收录', kind='intake-success')
    panel.tabs.setCurrentWidget(delivery)
    assert not panel.notice.isVisible()
    panel.show_notice('工作目录需要恢复')
    QTest.qWait(30)
    assert panel.notice.isVisible()
    panel.set_busy(True)
    assert panel.cancel_button.isVisible() and panel.notice.isVisible()


def test_zip_actions_follow_actual_selected_package(panel, tmp_path):
    service = panel.service
    paths = [tmp_path / '匿名文件.zip', tmp_path / '匿名检修平台-2-G.zip']
    for source in paths:
        with ZipFile(source, 'w') as archive:
            archive.writestr('匿名检修平台/说明.txt', '甲')
            archive.writestr('匿名作业平台/说明.txt', '乙')
    ordinary, bundle = [service.import_zip(path)['zip'] for path in paths]
    panel.refresh()
    panel.advanced_actions['zip'].trigger()
    panel.zip_list.clearSelection()
    assert not panel.link_button.isVisible() and not panel.recognize_button.isVisible()
    rows = {panel.zip_list.item(row).data(Qt.UserRole): row for row in range(panel.zip_list.count())}
    panel.zip_list.setCurrentRow(rows[ordinary['id']])
    assert panel.link_button.isVisible() and not panel.recognize_button.isVisible()
    linked = QSignalSpy(panel.link_requested)

    def select_association():
        dialog = QApplication.activeModalWidget()
        if dialog is None:
            return
        choices = dialog.findChild(QListWidget)
        confirm = next((control for control in dialog.findChildren(QPushButton)
                        if control.text() == '选择此项目'), None)
        if choices is None or confirm is None:
            dialog.reject()
            return
        project = next(choices.item(row) for row in range(choices.count())
                       if choices.item(row).data(Qt.UserRole) == 1)
        choices.setCurrentItem(project)
        confirm.click()

    QTimer.singleShot(40, select_association)
    panel.link_button.click()
    assert linked.count() == 1 and linked.at(0) == [ordinary['id'], 1]
    panel.zip_list.setCurrentRow(rows[bundle['id']])
    assert not panel.link_button.isVisible() and panel.recognize_button.isVisible()
    assert isinstance(panel.zip_menu_button.menu(), QMenu)
