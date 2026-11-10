"""Simulated screen events do not prove physical mixed-DPI dual-screen support."""

from types import SimpleNamespace

import pytest
import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QRect, QTimer, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from window_environment import DesktopEnvironmentWatcher


class Screen(QObject):
    availableGeometryChanged = Signal(QRect)
    logicalDotsPerInchChanged = Signal(float)


class Displays(QObject):
    screenAdded = Signal(object)
    screenRemoved = Signal(object)

    def __init__(self, screens):
        super().__init__()
        self.current = screens

    def screens(self):
        return list(self.current)


@pytest.fixture(scope='module')
def qapp():
    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    return application


@pytest.fixture
def environment(qapp, monkeypatch):
    import window_environment

    displays = Displays([Screen()])
    monkeypatch.setattr(window_environment, 'QApplication',
                        SimpleNamespace(instance=lambda: displays))
    owner = QObject()
    watcher = DesktopEnvironmentWatcher(owner)
    changed, lost = [], []
    watcher.display_changed.connect(lambda: changed.append(True))
    watcher.tray_lost.connect(lambda: lost.append(True))
    yield SimpleNamespace(watcher=watcher, displays=displays, owner=owner,
                          changed=changed, lost=lost)
    if shiboken6.isValid(watcher):
        watcher.stop()
    if shiboken6.isValid(owner):
        owner.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def settle():
    QTest.qWait(190)


def tray_timer(watcher):
    return next(timer for timer in watcher.findChildren(QTimer)
                if timer.interval() >= 1000)


def background_tray(environment, monkeypatch):
    state = {'available': True, 'visible': True, 'checks': 0}

    def available():
        state['checks'] += 1
        return state['available']

    monkeypatch.setattr(QSystemTrayIcon, 'isSystemTrayAvailable', staticmethod(available))
    tray = QSystemTrayIcon(environment.owner)
    monkeypatch.setattr(tray, 'isVisible', lambda: state['visible'])
    return tray, state


def test_start_is_quiet_and_repeated_start_coalesces_geometry_and_dpi(environment):
    watcher = environment.watcher
    watcher.start()
    watcher.start()
    settle()
    assert environment.changed == []
    screen = environment.displays.current[0]
    for _ in range(4):
        screen.availableGeometryChanged.emit(QRect(0, 0, 800, 600))
        screen.logicalDotsPerInchChanged.emit(144)
    assert environment.changed == []
    settle()
    assert environment.changed == [True]
    screen.logicalDotsPerInchChanged.emit(96)
    settle()
    assert environment.changed == [True, True]


def test_added_screen_is_observed_and_removed_screen_is_disconnected(environment):
    watcher, displays = environment.watcher, environment.displays
    watcher.start()
    added = Screen()
    displays.current.append(added)
    displays.screenAdded.emit(added)
    displays.screenAdded.emit(added)
    settle()
    assert environment.changed == [True]
    added.logicalDotsPerInchChanged.emit(144)
    settle()
    assert environment.changed == [True, True]
    displays.current.remove(added)
    displays.screenRemoved.emit(added)
    settle()
    assert len(environment.changed) == 3
    added.availableGeometryChanged.emit(QRect())
    settle()
    assert len(environment.changed) == 3


def test_removed_screen_can_be_destroyed_before_stop(environment):
    watcher, displays = environment.watcher, environment.displays
    watcher.start()
    removed = displays.current.pop()
    displays.screenRemoved.emit(removed)
    shiboken6.delete(removed)
    settle()
    assert environment.changed == [True]
    watcher.stop()
    watcher.stop()


def test_stop_cancels_pending_changes_and_restart_does_not_duplicate(environment):
    watcher, displays = environment.watcher, environment.displays
    watcher.start()
    displays.current[0].logicalDotsPerInchChanged.emit(144)
    watcher.stop()
    watcher.stop()
    displays.current[0].logicalDotsPerInchChanged.emit(96)
    displays.screenAdded.emit(Screen())
    settle()
    assert environment.changed == []
    watcher.start()
    displays.current[0].logicalDotsPerInchChanged.emit(120)
    settle()
    assert environment.changed == [True]


@pytest.mark.parametrize('failure', ['available', 'visible', 'destroyed'])
def test_background_tray_loss_emits_once_and_stops_checks(environment, monkeypatch, failure):
    watcher = environment.watcher
    tray, state = background_tray(environment, monkeypatch)
    watcher.start()
    watcher.set_background_tray(tray)
    timer = tray_timer(watcher)
    assert timer.isActive()
    timer.timeout.emit()
    assert environment.lost == []
    if failure == 'destroyed':
        shiboken6.delete(tray)
    else:
        state[failure] = False
    timer.timeout.emit()
    assert environment.lost == [True]
    assert not timer.isActive()
    checks = state['checks']
    timer.timeout.emit()
    assert environment.lost == [True]
    assert state['checks'] == checks


def test_leaving_background_or_stopping_cancels_tray_monitor(environment, monkeypatch):
    watcher = environment.watcher
    tray, state = background_tray(environment, monkeypatch)
    watcher.start()
    timer = tray_timer(watcher)
    assert not timer.isActive()
    watcher.set_background_tray(tray)
    watcher.set_background_tray(None)
    assert not timer.isActive()
    state['available'] = False
    timer.timeout.emit()
    assert environment.lost == []
    watcher.set_background_tray(tray)
    watcher.stop()
    timer.timeout.emit()
    assert environment.lost == []


def test_parent_destruction_prevents_pending_and_future_callbacks(environment, monkeypatch):
    watcher = environment.watcher
    tray, _state = background_tray(environment, monkeypatch)
    watcher.start()
    watcher.set_background_tray(tray)
    environment.displays.current[0].logicalDotsPerInchChanged.emit(144)
    shiboken6.delete(environment.owner)
    environment.displays.current[0].logicalDotsPerInchChanged.emit(96)
    environment.displays.screenAdded.emit(Screen())
    settle()
    assert not shiboken6.isValid(watcher)
    assert environment.changed == []
    assert environment.lost == []
