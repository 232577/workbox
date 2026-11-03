"""Only the small intake orb should repair its desktop stacking order."""
import importlib
import os
import sys
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPoint, QSize, Qt
from PySide6.QtGui import QResizeEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

import floating
from compact_ui import CompactPanel
from desktop_service import DesktopService


@pytest.fixture(scope='module')
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


@pytest.fixture
def orb(qapp):
    widget = floating.FloatingBall()
    yield widget
    widget.hide()
    widget.deleteLater()
    qapp.processEvents()


def native_platform_probe(monkeypatch):
    # Keep PySide's real offscreen platform descriptor untouched during paint.
    proxy = SimpleNamespace(platformName=lambda: 'windows',
        activePopupWidget=QApplication.activePopupWidget,
        activeModalWidget=QApplication.activeModalWidget)
    monkeypatch.setattr(floating, 'QApplication', proxy)
    return proxy


def test_repair_lifecycle_exists_only_while_orb_is_visible(orb, qapp):
    timer = getattr(orb, '_topmost_timer', None)
    assert timer is not None, 'Orb needs persistent, non-activating stacking repair'
    assert timer.parent() is orb and 800 <= timer.interval() <= 2000
    assert not timer.isActive()
    orb.show(); qapp.processEvents()
    assert timer.isActive()
    orb.hide(); qapp.processEvents()
    assert not timer.isActive()
    orb.show(); qapp.processEvents()
    assert timer.isActive()
    orb.close(); qapp.processEvents()
    assert not timer.isActive()


def test_orb_repair_keeps_rect_and_does_not_open_manager(orb, qapp, monkeypatch):
    assert callable(getattr(orb, '_ensure_topmost', None)), 'Missing stacking repair'
    calls = []
    monkeypatch.setattr(floating, 'keep_window_topmost', lambda hwnd: calls.append(hwnd))
    native_platform_probe(monkeypatch)
    opened = []
    orb.open_requested.connect(lambda: opened.append(True))
    orb.move(80, 90); orb.show(); qapp.processEvents()
    before = orb.geometry()
    calls.clear()
    orb._ensure_topmost()
    assert calls == [int(orb.winId())]
    assert orb.geometry() == before and opened == []
    assert orb.testAttribute(Qt.WA_ShowWithoutActivating)
    assert orb.windowFlags() & Qt.WindowStaysOnTopHint


@pytest.mark.parametrize('reason', ['hidden', 'dragging', 'popup', 'modal'])
def test_orb_never_fights_hidden_state_drag_or_own_dialogs(orb, qapp, monkeypatch, reason):
    assert callable(getattr(orb, '_ensure_topmost', None)), 'Missing stacking repair'
    calls = []
    monkeypatch.setattr(floating, 'keep_window_topmost', lambda hwnd: calls.append(hwnd))
    proxy = native_platform_probe(monkeypatch)
    orb.show(); qapp.processEvents(); calls.clear()
    if reason == 'hidden':
        orb.hide()
    elif reason == 'dragging':
        orb._press = QPoint(1, 1)
    elif reason == 'popup':
        monkeypatch.setattr(proxy, 'activePopupWidget', lambda: object())
    else:
        monkeypatch.setattr(proxy, 'activeModalWidget', lambda: object())
    orb._ensure_topmost()
    assert calls == []


def test_real_modal_defers_repair_then_releases_it(orb, qapp, monkeypatch):
    assert callable(getattr(orb, '_ensure_topmost', None)), 'Missing stacking repair'
    calls = []
    monkeypatch.setattr(floating, 'keep_window_topmost', lambda hwnd: calls.append(hwnd))
    native_platform_probe(monkeypatch)
    orb.show()
    dialog = QDialog(); dialog.setWindowModality(Qt.ApplicationModal); dialog.show()
    qapp.processEvents(); calls.clear()
    try:
        orb._ensure_topmost(); assert calls == []
        dialog.close(); qapp.processEvents()
        orb._ensure_topmost(); assert calls
    finally:
        dialog.close(); dialog.deleteLater()


def test_expanded_manager_is_never_always_on_top(qapp, tmp_path):
    panel = CompactPanel(DesktopService(tmp_path/'data/workbox.db'))
    try:
        assert not panel.windowFlags() & Qt.WindowStaysOnTopHint
        panel.show(); qapp.processEvents()
        panel.hide(); panel.show(); qapp.processEvents()
        assert not panel.windowFlags() & Qt.WindowStaysOnTopHint
    finally:
        # The production manager defers layout with zero-delay callbacks.
        # Complete those callbacks before disposing this isolated test window.
        QTest.qWait(60)
        panel.close(); panel.deleteLater(); qapp.processEvents()


def test_queued_manager_layout_does_not_outlive_window(qapp, tmp_path, monkeypatch):
    import shiboken6
    panel = CompactPanel(DesktopService(tmp_path/'data/workbox.db'))
    errors = []
    monkeypatch.setattr(sys, 'excepthook', lambda *args: errors.append(str(args[1])))
    panel.resizeEvent(QResizeEvent(QSize(800, 600), QSize(900, 700)))
    shiboken6.delete(panel)
    QTest.qWait(30)
    assert not errors, errors


class FakeAPI:
    def __init__(self):
        self.visible = self.valid = True
        self.pid = os.getpid()
        self.calls = []
    def IsWindow(self, hwnd): return self.valid
    def IsWindowVisible(self, hwnd): return self.visible
    def GetWindowThreadProcessId(self, hwnd, out):
        out._obj.value = self.pid
        return 1
    def SetWindowPos(self, *args):
        self.calls.append(args)
        return 1


def adapter():
    assert importlib.util.find_spec('window_zorder'), 'Native non-activating adapter missing'
    return importlib.import_module('window_zorder')


def test_native_call_only_changes_own_z_order(monkeypatch):
    module = adapter(); api = FakeAPI()
    monkeypatch.setattr(module, '_api', lambda: api)
    monkeypatch.setattr(module.sys, 'platform', 'win32')
    assert module.keep_window_topmost(123)
    assert api.calls == [(123, -1, 0, 0, 0, 0, 0x0010 | 0x0001 | 0x0002 | 0x0200)]


@pytest.mark.parametrize('kind', ['invalid', 'foreign', 'hidden', 'zero'])
def test_native_refuses_unsafe_or_hidden_targets(monkeypatch, kind):
    module = adapter(); api = FakeAPI()
    monkeypatch.setattr(module, '_api', lambda: api)
    monkeypatch.setattr(module.sys, 'platform', 'win32')
    if kind == 'invalid': api.valid = False
    if kind == 'foreign': api.pid = os.getpid()+1
    if kind == 'hidden': api.visible = False
    assert not module.keep_window_topmost(0 if kind == 'zero' else 123)
    assert not api.calls


def test_native_failure_does_not_crash_file_intake(monkeypatch):
    module = adapter()
    monkeypatch.setattr(module.sys, 'platform', 'win32')
    def unavailable(): raise OSError('isolated platform failure')
    monkeypatch.setattr(module, '_api', unavailable)
    assert module.keep_window_topmost(123) is False


def test_repair_build_is_newer_than_installed_handoff_fix():
    from update_client import CURRENT_TAG, build_key
    assert build_key(CURRENT_TAG) > build_key('v1.0.0.20261103.2')
