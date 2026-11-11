"""Failed shutdown retains the real shortcut dispatcher over anonymous data."""
import ctypes
from ctypes import wintypes
import hashlib
import sys

import pytest
from PySide6.QtWidgets import QApplication

import desktop_controller
from desktop_controller import FloatingController
from desktop_service import DesktopService
from global_shortcut import GlobalShortcut
from update_client import Release, RELEASE_ROOT


class Backend:
    """Replace only Windows registration; never reserve a desktop hotkey."""

    def __init__(self):
        self.active = {}

    def register(self, identity, modifiers, key):
        self.active[identity] = (modifiers, key)
        return True

    def unregister(self, identity):
        del self.active[identity]
        return True


@pytest.fixture(scope='module')
def qapp():
    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    return application


@pytest.fixture
def controller(qapp, tmp_path, monkeypatch):
    backend = Backend()
    monkeypatch.setattr(desktop_controller, 'GlobalShortcut',
                        lambda parent: GlobalShortcut(parent, backend=backend))
    service = DesktopService(tmp_path / 'data/workbox.db')
    service.set_setting('window_shortcut', 'Ctrl+Alt+K')
    value = FloatingController(service, tray_enabled=False)
    assert value.start()
    value.monitor_timer.stop()
    yield value, backend
    value.pending_update = None
    value.shutdown()
    value.dispose_ui()
    qapp.processEvents()


def press_shortcut(controller, registration, qapp, *, process_events=True):
    identity, (modifiers, key) = next(iter(registration.items()))
    message = wintypes.MSG()
    message.message = 0x0312
    message.wParam = identity
    # MOD_NOREPEAT is a registration flag, absent from WM_HOTKEY modifiers.
    message.lParam = (modifiers & ~0x4000) | (key << 16)
    handled, _ = controller.shortcut._native_event(
        b'windows_generic_MSG', ctypes.addressof(message))
    if process_events:
        qapp.processEvents()
    return handled


def installer(tmp_path):
    data = b'MZanonymous installer, never executed'
    tag = 'v1.0.0.20991231'
    filename = 'WorkBox-1.0.0-Setup-x64-20991231.exe'
    package = tmp_path / filename
    package.write_bytes(data)
    release = Release(tag, filename, len(data), hashlib.sha256(data).hexdigest(),
                      RELEASE_ROOT + f'download/{tag}/{filename}', 'Anonymous fixture')
    return release, package


@pytest.mark.parametrize('stage', ['reader', 'settings'])
def test_failed_normal_exit_keeps_registered_shortcut_usable(controller, qapp, monkeypatch, stage):
    value, backend = controller
    registration = dict(backend.active)

    def fail():
        raise OSError('anonymous shutdown failure')

    with monkeypatch.context() as patch:
        target = value.panel.document_pane if stage == 'reader' else value
        patch.setattr(target, 'shutdown' if stage == 'reader' else '_save_panel_geometry', fail)
        value.request_quit()
    assert not value.closed and not value.quitting and value.owns_lock
    assert value.service.get_setting('window_shortcut') == 'Ctrl+Alt+K'
    assert value.shortcut.shortcut == 'Ctrl+Alt+K'
    assert backend.active == registration
    assert not value.panel.isVisible()
    assert press_shortcut(value, registration, qapp)
    assert value.panel.isVisible()


def test_failed_update_backup_keeps_registered_shortcut_usable(
        controller, qapp, tmp_path, monkeypatch):
    import update_install
    value, backend = controller
    registration = dict(backend.active)
    release, package = installer(tmp_path)

    def fail_backup(database, root):
        assert database == value.service.db.path and value.owns_lock
        raise OSError('anonymous full disk')

    with monkeypatch.context() as patch:
        patch.setattr(sys, 'frozen', True, raising=False)
        patch.setattr(update_install, 'backup_database', fail_backup)
        with pytest.raises(OSError, match='anonymous full disk'):
            value.prepare_update_install(release, package)
    assert value.pending_update is None and value.update_backup is None
    assert not value.closed and not value.quitting and value.owns_lock
    assert value.service.get_setting('window_shortcut') == 'Ctrl+Alt+K'
    assert value.shortcut.shortcut == 'Ctrl+Alt+K'
    assert backend.active == registration
    assert press_shortcut(value, registration, qapp)
    assert value.panel.isVisible()


@pytest.mark.parametrize('updating', [False, True])
def test_successful_exit_releases_shortcut_and_rejects_queued_activation(
        controller, qapp, tmp_path, monkeypatch, updating):
    value, backend = controller
    registration = dict(backend.active)
    assert press_shortcut(value, registration, qapp, process_events=False)
    if updating:
        release, package = installer(tmp_path)
        with monkeypatch.context() as patch:
            patch.setattr(sys, 'frozen', True, raising=False)
            value.prepare_update_install(release, package)
        assert value.update_backup.is_file()
    else:
        value.request_quit()
    assert value.closed and not value.owns_lock
    assert value.shortcut.shortcut == '' and not backend.active
    assert not press_shortcut(value, registration, qapp)
    assert not value.panel.isVisible() and not value.orb.isVisible()
    assert value.service.get_setting('window_shortcut') == 'Ctrl+Alt+K'
