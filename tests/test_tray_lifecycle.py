from threading import Event

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QPushButton, QSystemTrayIcon

from desktop_controller import FloatingController
from desktop_service import DesktopService


@pytest.fixture(scope='module')
def qapp():
    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    return application


def start(tmp_path):
    controller = FloatingController(DesktopService(tmp_path/'data/workbox.db'), tray_enabled=True)
    assert controller.start()
    controller.show_panel()
    return controller


def visible_button(panel, text):
    matches = [b for b in panel.findChildren(QPushButton) if b.text() == text]
    assert len(matches) == 1, f'生产界面缺少明确的“{text}”按钮'
    return matches[0]


def test_exit_button_still_really_exits_and_removes_tray(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(QSystemTrayIcon, 'isSystemTrayAvailable', lambda: True)
    controller = start(tmp_path)
    exited = QSignalSpy(controller.exit_requested)
    try:
        visible_button(controller.panel, '退出').click()
        assert controller.closed and controller.quitting
        assert not controller.monitor_timer.isActive()
        assert not controller.tray.isVisible()
        assert exited.count() == 1
    finally:
        controller.shutdown()


def test_tray_is_registered_even_when_shell_is_not_ready_yet(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(QSystemTrayIcon, 'isSystemTrayAvailable', lambda: False)
    controller = start(tmp_path)
    try:
        assert controller.tray is not None, '启动时托盘暂不可用导致永久没有图标'
        assert not controller.tray.icon().isNull()
        assert controller.tray.isVisible()
        assert controller.tray.contextMenu() is not None
    finally:
        controller.shutdown()


@pytest.mark.parametrize('reason', [QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick])
def test_background_button_keeps_monitor_and_tray_then_activation_restores(qapp, tmp_path, monkeypatch, reason):
    monkeypatch.setattr(QSystemTrayIcon, 'isSystemTrayAvailable', lambda: True)
    controller = start(tmp_path)
    exited = QSignalSpy(controller.exit_requested)
    try:
        visible_button(controller.panel, '后台运行').click()
        assert not controller.panel.isVisible() and not controller.orb.isVisible()
        assert controller.tray.isVisible() and controller.monitor_timer.isActive()
        assert not controller.closed and not controller.quitting and exited.count() == 0
        controller.notify('已完成一次后台归档')
        assert not controller.bubble.isVisible()
        controller.tray.activated.emit(reason)
        assert controller.panel.isVisible() and controller.orb.isVisible()
        assert not controller.closed
    finally:
        controller.shutdown()


def test_background_without_available_tray_preserves_floating_entry(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(QSystemTrayIcon, 'isSystemTrayAvailable', lambda: False)
    controller = start(tmp_path)
    try:
        visible_button(controller.panel, '后台运行').click()
        assert controller.orb.isVisible()
        assert controller.monitor_timer.isActive() and not controller.closed
        assert '托盘' in controller.panel.notice.text()
    finally:
        controller.shutdown()


def test_background_does_not_cancel_current_file_job_and_menu_restores(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(QSystemTrayIcon, 'isSystemTrayAvailable', lambda: True)
    controller = start(tmp_path)
    entered, finish = Event(), Event()
    def operation(*, cancel_event, progress):
        entered.set()
        assert finish.wait(5)
        assert not cancel_event.is_set()
    try:
        controller.enqueue('隔离文件任务', operation)
        for _ in range(100):
            QApplication.processEvents()
            if entered.is_set():
                break
            QTest.qWait(10)
        assert entered.is_set()
        visible_button(controller.panel, '后台运行').click()
        assert controller.busy and not controller.worker.cancel_event.is_set()
        next(a for a in controller.tray.contextMenu().actions() if a.text() == '打开项目管理').trigger()
        assert controller.panel.isVisible() and controller.orb.isVisible()
    finally:
        finish.set()
        controller.shutdown()


def test_tray_disabled_background_falls_back_to_orb(qapp, tmp_path):
    controller = FloatingController(DesktopService(tmp_path/'data/workbox.db'), tray_enabled=False)
    try:
        assert controller.start()
        controller.show_panel()
        visible_button(controller.panel, '后台运行').click()
        assert controller.tray is None and controller.orb.isVisible()
        assert not controller.closed and controller.monitor_timer.isActive()
    finally:
        controller.shutdown()
