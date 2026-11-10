"""Desktop recovery integration; emitted events do not prove physical mixed-DPI support."""

import time
from types import SimpleNamespace

import pytest
import shiboken6
from PySide6.QtCore import QPoint, QRect, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from desktop_controller import FloatingController
from desktop_service import DesktopService
from window_environment import DesktopEnvironmentWatcher


@pytest.fixture(scope='module')
def qapp():
    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    return application


@pytest.fixture
def controller(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(QSystemTrayIcon, 'isSystemTrayAvailable', lambda: True)
    value = FloatingController(DesktopService(tmp_path / 'data' / 'anonymous.db'),
                               tray_enabled=True)
    try:
        assert value.start()
        qapp.processEvents()
        yield value
    finally:
        value.shutdown()
        value.dispose_ui()


def normal_panel(controller, qapp):
    controller.show_panel()
    controller.panel.showNormal()
    area = controller.orb.screen().availableGeometry()
    controller.panel.setGeometry(QRect(area.left() + 24, area.top() + 32, 660, 480))
    qapp.processEvents()
    assert area.contains(controller.panel.frameGeometry())
    return area


def test_start_owns_and_starts_desktop_environment_watcher(controller):
    watcher = controller.environment
    assert isinstance(watcher, DesktopEnvironmentWatcher)
    assert watcher.parent() is controller
    changes = []
    watcher.display_changed.connect(lambda: changes.append(True))
    screen = controller.orb.screen()
    screen.availableGeometryChanged.emit(screen.availableGeometry())
    screen.logicalDotsPerInchChanged.emit(screen.logicalDotsPerInch())
    QTest.qWait(190)
    assert changes == [True]


def test_display_change_returns_offscreen_normal_panel_and_ball_to_available_screen(
        controller, qapp):
    area = normal_panel(controller, qapp)
    controller.panel.move(area.right() + 1000, area.bottom() + 1000)
    controller.orb.move(area.right() + 2000, area.bottom() + 2000)
    qapp.processEvents()
    assert not area.intersects(controller.panel.frameGeometry())
    assert not area.intersects(controller.orb.frameGeometry())
    controller.environment.display_changed.emit()
    qapp.processEvents()
    assert controller.panel.isVisible() and not controller.panel.isMaximized()
    assert area.contains(controller.panel.frameGeometry())
    assert area.contains(controller.orb.frameGeometry())


def test_display_change_does_not_open_hidden_main_window(controller, qapp):
    area = controller.orb.screen().availableGeometry()
    controller.panel.move(area.right() + 1000, area.bottom() + 1000)
    controller.orb.move(area.right() + 2000, area.bottom() + 2000)
    assert not controller.panel.isVisible()
    controller.environment.display_changed.emit()
    qapp.processEvents()
    assert not controller.panel.isVisible()
    assert controller.orb.isVisible()
    assert area.contains(controller.orb.frameGeometry())


def test_display_change_does_not_move_legal_window_or_ball(controller, qapp):
    area = normal_panel(controller, qapp)
    controller.orb.move(area.right() - controller.orb.width() - 16,
                        area.bottom() - controller.orb.height() - 16)
    qapp.processEvents()
    expected_panel = QRect(controller.panel.geometry())
    expected_ball = QPoint(controller.orb.pos())
    for _ in range(3):
        controller.environment.display_changed.emit()
        qapp.processEvents()
    assert controller.panel.geometry() == expected_panel
    assert controller.orb.pos() == expected_ball


def test_background_tray_loss_restores_only_ball_and_archive_monitor_continues(
        controller, qapp, monkeypatch):
    controller.show_panel()
    assert controller.run_in_background()
    assert controller.background_mode
    assert not controller.panel.isVisible() and not controller.orb.isVisible()
    watcher = controller.environment
    watcher.display_changed.emit()
    qapp.processEvents()
    assert not controller.panel.isVisible() and not controller.orb.isVisible()
    watcher.tray_lost.emit()
    qapp.processEvents()
    assert controller.orb.isVisible()
    assert not controller.panel.isVisible()
    assert not controller.background_mode
    assert controller.monitor_timer.isActive()
    scans = []
    original = controller.service.scan_archives

    def scan(**kwargs):
        scans.append(True)
        return original(**kwargs)

    monkeypatch.setattr(controller.service, 'scan_archives', scan)
    controller.monitor_timer.timeout.emit()
    deadline = time.monotonic() + 5
    while controller.busy and time.monotonic() < deadline:
        QTest.qWait(10)
    assert not controller.busy
    assert scans == [True]
    assert controller.monitor_timer.isActive()
    assert not controller.panel.isVisible()


def test_shutdown_stops_environment_and_ignores_late_recovery_signals(controller, qapp):
    controller.show_panel()
    # Consume native show geometry notifications before testing post-shutdown moves.
    qapp.processEvents()
    assert controller.run_in_background()
    watcher = controller.environment
    controller.shutdown()
    assert controller.closed
    assert not any(timer.isActive() for timer in watcher.findChildren(QTimer))
    controller.panel.move(20000, 20000)
    controller.orb.move(21000, 21000)
    expected_panel = QRect(controller.panel.geometry())
    expected_ball = QPoint(controller.orb.pos())
    changes = []
    watcher.display_changed.connect(lambda: changes.append(True))
    screen = QApplication.primaryScreen()
    screen.logicalDotsPerInchChanged.emit(screen.logicalDotsPerInch())
    QTest.qWait(190)
    assert changes == [], 'Stopped watchers must disconnect screen subscriptions'
    watcher.display_changed.emit()
    watcher.tray_lost.emit()
    qapp.processEvents()
    assert controller.panel.geometry() == expected_panel
    assert controller.orb.pos() == expected_ball
    assert not controller.panel.isVisible() and not controller.orb.isVisible()


class GeometryScreen:
    """Supply only the geometry read by the controller's screen selection."""

    def __init__(self, area):
        self.area = QRect(area)

    def availableGeometry(self):
        return QRect(self.area)


def test_small_screen_constrains_panel_then_restores_normal_minimum(
        controller, qapp, monkeypatch):
    import desktop_controller

    normal_area = normal_panel(controller, qapp)
    normal_minimum = controller.panel.minimumSize()
    small_area = QRect(0, 0, 600, 400)
    screens = [GeometryScreen(small_area)]
    with monkeypatch.context() as patch:
        patch.setattr(desktop_controller, 'QApplication',
                      SimpleNamespace(screens=lambda: screens))
        controller.environment.display_changed.emit()
        qapp.processEvents()
        assert small_area.contains(controller.panel.frameGeometry())
        assert controller.panel.width() < normal_minimum.width()
        assert controller.panel.height() < normal_minimum.height()
        assert controller.panel.isVisible()
        screens[:] = [GeometryScreen(normal_area)]
        controller.environment.display_changed.emit()
        qapp.processEvents()
        assert controller.panel.minimumSize() == normal_minimum
        assert controller.panel.width() >= normal_minimum.width()
        assert controller.panel.height() >= normal_minimum.height()
        assert normal_area.contains(controller.panel.frameGeometry())


def test_recovery_keeps_panel_on_negative_origin_screen_independently_of_ball(
        controller, qapp, monkeypatch):
    import desktop_controller

    normal_panel(controller, qapp)
    left_area = QRect(-1280, 0, 1280, 1024)
    right_area = QRect(0, 0, 1280, 1024)
    controller.panel.setGeometry(QRect(-1100, 90, 660, 480))
    controller.orb.move(100, 150)
    qapp.processEvents()
    expected_panel = QRect(controller.panel.geometry())
    expected_ball = QPoint(controller.orb.pos())
    assert left_area.contains(controller.panel.frameGeometry())
    assert right_area.contains(controller.orb.frameGeometry())
    with monkeypatch.context() as patch:
        patch.setattr(desktop_controller, 'QApplication', SimpleNamespace(
            screens=lambda: [GeometryScreen(right_area), GeometryScreen(left_area)]))
        controller.environment.display_changed.emit()
        qapp.processEvents()
        assert controller.panel.geometry() == expected_panel
        assert controller.orb.pos() == expected_ball
        assert left_area.contains(controller.panel.frameGeometry())


def test_destroyed_background_tray_recovers_entry_and_allows_fallback_and_shutdown(
        controller, qapp):
    controller.show_panel()
    assert controller.run_in_background()
    tray = controller.tray
    assert shiboken6.isValid(tray)
    shiboken6.delete(tray)
    qapp.processEvents()
    assert not shiboken6.isValid(tray)
    assert controller.orb.isVisible()
    assert not controller.panel.isVisible()
    assert not controller.background_mode
    assert controller.monitor_timer.isActive()
    assert controller.run_in_background() is False
    assert controller.orb.isVisible() and not controller.panel.isVisible()
    controller.shutdown()
    assert controller.closed and not controller.owns_lock


@pytest.mark.parametrize('position', [QPoint(-330, 90), QPoint(0, 0)],
                         ids=['spanning-adjacent-screens', 'touching-screen-origin'])
def test_recovery_and_reopen_keep_windows_fully_covered_by_connected_screens(
        controller, qapp, monkeypatch, position):
    import desktop_controller

    normal_panel(controller, qapp)
    controller.panel.move(position)
    qapp.processEvents()
    frame = controller.panel.frameGeometry()
    if position.x() < 0:
        assert -800 <= frame.left() < 0 < frame.right() < 800
        assert 0 <= frame.top() < frame.bottom() < 800
    else:
        assert frame.topLeft() == QPoint(0, 0)
        assert frame.right() < 800 and frame.bottom() < 800
    expected = QRect(controller.panel.geometry())
    screens = [GeometryScreen(QRect(0, 0, 800, 800)),
               GeometryScreen(QRect(-800, 0, 800, 800))]
    with monkeypatch.context() as patch:
        patch.setattr(desktop_controller, 'QApplication', SimpleNamespace(
            screens=lambda: screens, activeModalWidget=QApplication.activeModalWidget))
        controller.environment.display_changed.emit()
        qapp.processEvents()
        assert controller.panel.geometry() == expected
        controller.panel.close()
        assert controller.show_panel()
        qapp.processEvents()
        assert controller.panel.geometry() == expected
