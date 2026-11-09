"""待开发名单使用实际工作台和匿名数据库，保存对话框由用例控制。"""
from concurrent.futures import CancelledError
from pathlib import Path
import sys
import threading
import time

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

import delivery_export
from desktop_controller import FloatingController
from desktop_service import DesktopService
from native_files import identity
from theme import current_mode, set_theme


@pytest.fixture
def controller(tmp_path):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    service = DesktopService(tmp_path / 'data/workbox.db')
    service.receive_text('\n'.join(f'{n}. 匿名材料核对项目{n:02d}' for n in range(1, 32)))
    value = FloatingController(service, tray_enabled=False)
    assert value.start()
    value.monitor_timer.stop()
    value.show_panel()
    app.processEvents()
    yield value
    value.shutdown()
    value.dispose_ui()


def wait(controller):
    deadline = time.monotonic() + 5
    while controller.busy and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(.01)
    assert not controller.busy
    QApplication.processEvents()


def test_pending_export_button_belongs_to_pending_queue_in_light_and_dark(controller):
    panel = controller.panel
    button = panel.pending_export_button
    assert button.text() == '导出名单'
    assert button.accessibleName() == '导出全部待开发项目名称'
    assert '搜索' in button.toolTip() and '批次' in button.toolTip()
    assert not button.isVisible()
    prior_theme = current_mode()
    try:
        for theme in ('light', 'dark'):
            set_theme(theme)
            panel.resize(660, 480)
            panel.status_buttons['待开发'].click()
            QApplication.processEvents()
            assert panel.width() == 660
            assert button.isVisible() and button.isEnabled()
            assert button.width() >= button.minimumSizeHint().width()
            assert panel.project_queue_header.rect().contains(button.geometry())
            assert not button.geometry().intersects(panel.bulk_mode.geometry())
            panel.project_list.setCurrentRow(0)
            panel.show_project_detail()
            assert not button.isVisible()
            panel.project_back_button.click()
            assert button.isVisible()
            panel.tabs.setCurrentIndex(1)
            assert not button.isVisible()
            panel.tabs.setCurrentIndex(0)
            assert button.isVisible()
            panel.status_buttons['开发中'].click()
            assert not button.isVisible()
    finally:
        set_theme(prior_theme)


def test_export_passes_full_scope_and_overwrite_identity_without_refresh(controller, tmp_path, monkeypatch):
    panel = controller.panel
    target = tmp_path / 'table.xlsx'
    target.write_bytes(b'old')
    previous = identity(target.stat())
    calls = []

    def save(*args, **kwargs):
        assert Path(args[2]).name.startswith('待开发项目名单_')
        return str(target), ''

    def export(db, path, **kwargs):
        calls.append((db, path, kwargs))
        return {'project_count': 31, 'path': str(path)}

    monkeypatch.setattr(delivery_export, 'export_pending_projects', export, raising=False)
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', save)
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.Yes)
    panel.status_buttons['待开发'].click()
    panel.search.setText('匿名')
    panel.due_filter.setCurrentIndex(panel.due_filter.findData('unscheduled'))
    panel.project_list.setCurrentRow(7)
    QApplication.processEvents()
    panel.project_list.verticalScrollBar().setValue(223)
    scroll_value = panel.project_list.verticalScrollBar().value()
    item = panel.project_list.currentItem()
    changed = QSignalSpy(panel.data_changed)
    monkeypatch.setattr(controller, 'refresh', lambda: pytest.fail('只读导出触发业务刷新'))
    panel.pending_export_button.click()
    wait(controller)
    assert len(calls) == 1 and calls[0][:2] == (panel.service.db, target)
    assert calls[0][2]['replace_existing'] and calls[0][2]['expected_target_identity'] == previous
    assert changed.count() == 0
    assert panel.search.text() == '匿名' and panel.due_filter.currentData() == 'unscheduled'
    assert panel.project_list.currentItem() is item and item.data(Qt.UserRole) == panel.current_project['id']
    assert panel.project_list.verticalScrollBar().value() == scroll_value
    assert '31 个项目' in panel.notice.toolTip() and str(target) in panel.notice.toolTip()


def test_locating_intake_switches_to_all_and_hides_pending_export(controller):
    panel = controller.panel
    panel.status_buttons['待开发'].click()
    pid = panel.project_list.item(0).data(Qt.UserRole)
    assert panel.pending_export_button.isVisible()
    assert panel.focus_projects([pid])
    panel.show_project_queue()
    assert panel.status_buttons[None].isChecked()
    assert not panel.pending_export_button.isVisible()


def test_cancel_save_or_overwrite_never_starts_worker(controller, tmp_path, monkeypatch):
    panel = controller.panel
    panel.status_buttons['待开发'].click()
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **k: ('', ''))
    panel.pending_export_button.click()
    assert controller.worker is None and not controller.queue
    target = tmp_path / 'existing.xlsx'
    target.write_bytes(b'old')
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **k: (str(target), ''))
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.No)
    panel.pending_export_button.click()
    assert controller.worker is None and not controller.queue
    assert target.read_bytes() == b'old'


def test_empty_visible_list_can_export_and_error_stays_until_retry(controller, tmp_path, monkeypatch):
    panel = controller.panel
    panel.status_buttons['待开发'].click()
    panel.search.setText('匿名无匹配')
    assert panel.project_list.count() == 0
    assert panel.pending_export_button.isVisible() and panel.pending_export_button.isEnabled()
    target = tmp_path / 'out.xlsx'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **k: (str(target), ''))

    def fail(*args, **kwargs):
        raise OSError('匿名目标暂不可用')

    monkeypatch.setattr(delivery_export, 'export_pending_projects', fail, raising=False)
    panel.pending_export_button.click()
    wait(controller)
    assert '匿名目标暂不可用' in panel.notice.toolTip()
    assert not panel._notice_timer.isActive()
    assert panel.pending_export_button.isEnabled()
    monkeypatch.setattr(delivery_export, 'export_pending_projects',
                        lambda *a, **k: {'project_count': 0, 'path': str(target)}, raising=False)
    panel.pending_export_button.click()
    wait(controller)
    assert '已导出 0 个项目' in panel.notice.toolTip() and str(target) in panel.notice.toolTip()
    assert panel.search.text() == '匿名无匹配'


@pytest.mark.parametrize('quit_app', [False, True])
def test_export_is_cancellable_counts_as_busy_and_blocks_duplicate(controller, tmp_path, monkeypatch, quit_app):
    panel = controller.panel
    panel.status_buttons['待开发'].click()
    entered = threading.Event()
    dialogs = []

    def save(*args, **kwargs):
        dialogs.append(args)
        return str(tmp_path / 'out.xlsx'), ''

    def export(db, path, *, cancel_event, **kwargs):
        entered.set()
        assert cancel_event.wait(3)
        raise CancelledError()

    monkeypatch.setattr(QFileDialog, 'getSaveFileName', save)
    monkeypatch.setattr(delivery_export, 'export_pending_projects', export, raising=False)
    panel.pending_export_button.click()
    assert entered.wait(2)
    assert controller.busy and panel.cancel_button.isVisible()
    assert not panel.pending_export_button.isEnabled()
    panel.pending_export_requested.emit()
    assert len(dialogs) == 1
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    with pytest.raises(RuntimeError, match='文件任务'):
        controller.prepare_update_install(object(), tmp_path / 'anonymous.exe')
    assert controller.pending_update is None and not controller.quitting
    if quit_app:
        stopped = QSignalSpy(controller.exit_requested)
        controller.request_quit()
        wait(controller)
        assert controller.closed and stopped.count() == 1
    else:
        panel.cancel_button.click()
        wait(controller)
        assert '已取消导出' in panel.notice.toolTip()
        assert panel.pending_export_button.isEnabled()
    assert not (tmp_path / 'out.xlsx').exists()


def test_pending_export_waits_for_silent_scan_and_queued_export_can_cancel(controller, tmp_path, monkeypatch):
    panel = controller.panel
    panel.status_buttons['待开发'].click()
    panel.project_list.setCurrentRow(7)
    QApplication.processEvents()
    panel.project_list.verticalScrollBar().setValue(223)
    scroll_value = panel.project_list.verticalScrollBar().value()
    item = panel.project_list.currentItem()
    release_scan = threading.Event()
    exported, refreshes = [], []
    target = tmp_path / 'out.xlsx'

    def scan(*, cancel_event, **kwargs):
        assert release_scan.wait(3)
        if cancel_event.is_set():
            raise CancelledError()
        return {'archived': [], 'pending': 0, 'errors': []}

    def export(db, path, **kwargs):
        exported.append(path)
        return {'project_count': 31, 'path': str(path)}

    def save(*args, **kwargs):
        assert controller.busy and not panel.pending_export_button.isEnabled()
        return str(target), ''

    monkeypatch.setattr(QFileDialog, 'getSaveFileName', save)
    monkeypatch.setattr(delivery_export, 'export_pending_projects', export, raising=False)
    monkeypatch.setattr(controller, 'refresh', lambda: refreshes.append(True))
    monkeypatch.setattr(controller.service, 'scan_archives', scan)
    controller.check_archives()
    try:
        assert panel.pending_export_button.isEnabled()
        panel.pending_export_button.click()
        assert len(controller.queue) == 1 and not exported
        panel.cancel_button.click()
        assert not controller.queue and '已取消导出' in panel.notice.toolTip()
    finally:
        release_scan.set()
        wait(controller)
    assert '已取消导出' in panel.notice.toolTip() and not refreshes
    assert panel.project_list.currentItem() is item
    assert panel.project_list.verticalScrollBar().value() == scroll_value
    assert not exported and panel.pending_export_button.isEnabled()
    panel.pending_export_button.click()
    wait(controller)
    assert exported == [target]


@pytest.mark.parametrize('action', ['cancel_mixed_queue', 'quit', 'shutdown'])
def test_silent_scan_can_still_be_cancelled_with_queued_export(controller, tmp_path, monkeypatch, action):
    panel = controller.panel
    panel.status_buttons['待开发'].click()
    stopped = threading.Event()

    def scan(*, cancel_event, **kwargs):
        if cancel_event.wait(3):
            stopped.set()
            raise CancelledError()
        return {'archived': [], 'pending': 0, 'errors': []}

    def should_not_start(*args, **kwargs):
        pytest.fail('已取消的排队任务不能执行')

    monkeypatch.setattr(controller.service, 'scan_archives', scan)
    monkeypatch.setattr(delivery_export, 'export_pending_projects', should_not_start, raising=False)
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **k: (str(tmp_path / 'out.xlsx'), ''))
    controller.check_archives()
    panel.pending_export_button.click()
    assert len(controller.queue) == 1
    if action == 'cancel_mixed_queue':
        controller.enqueue('匿名其他前台操作', should_not_start)
        panel.cancel_button.click()
    elif action == 'quit':
        controller.request_quit()
    else:
        controller.shutdown()
    wait(controller)
    assert stopped.is_set() and not controller.queue and controller.worker is None
    assert controller.closed == (action != 'cancel_mixed_queue')
