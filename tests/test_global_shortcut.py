"""Explicit global shortcuts own only their registered Windows messages."""
import ctypes
import os
import subprocess
import sys
from ctypes import wintypes as w
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import QByteArray, QObject
from PySide6.QtWidgets import QApplication

from global_shortcut import GlobalShortcut, validate_shortcut


@pytest.fixture(scope='module')
def qapp():
    return QApplication.instance() or QApplication([])


class Backend:
    def __init__(self):
        self.active = {}
        self.calls = []
        self.conflicts = set()
        self.fail_unregister = set()

    def register(self, identity, modifiers, key):
        self.calls.append(('register', identity, modifiers, key))
        if (modifiers, key) in self.conflicts:
            raise OSError(1409, 'already registered')
        self.active[identity] = (modifiers, key)
        return True

    def unregister(self, identity):
        self.calls.append(('unregister', identity))
        if identity in self.fail_unregister:
            raise OSError(5, 'access denied')
        del self.active[identity]
        return True


@pytest.fixture
def manager(qapp):
    backend = Backend()
    value = GlobalShortcut(backend=backend)
    yield value, backend
    backend.fail_unregister.clear()
    value.stop()
    qapp.processEvents()


@pytest.mark.parametrize(('text', 'expected'), [
    ('', ''), ('  ', ''), ('shift + CTRL + k', 'Ctrl+Shift+K'),
    ('alt+ctrl+4', 'Ctrl+Alt+4'), ('Shift+Alt+f11', 'Alt+Shift+F11'),
    ('Shift+ctrl+ALT+f1', 'Ctrl+Alt+Shift+F1'),
])
def test_shortcuts_have_one_platform_independent_saved_form(text, expected):
    assert validate_shortcut(text) == expected


@pytest.mark.parametrize('text', [
    None, 1, [], 'K', 'Ctrl+K', 'Alt+F1', 'Ctrl+Shift', 'Ctrl+Ctrl+K',
    'Ctrl+Shift+F12', 'Ctrl+Shift+F13', 'Meta+Ctrl+K', 'Win+Shift+K',
    'Ctrl+Alt+K, Ctrl+Alt+L', 'Ctrl+Shift+Enter', 'Ctrl+Shift+é',
    'Ctrl+Shift++', 'Ctrl+Shift+F01', 'Ctrl+Shift+K' + ' ' * 65,
])
def test_unsafe_or_ambiguous_shortcuts_are_rejected(text):
    with pytest.raises(ValueError):
        validate_shortcut(text)


def test_default_has_no_registration_and_offscreen_does_not_capture_keys(qapp):
    value = GlobalShortcut()
    assert value.shortcut == ''
    assert value.supported is (sys.platform == 'win32' and qapp.platformName() == 'windows')
    if not value.supported:
        with pytest.raises(ValueError, match='Windows'):
            value.apply('Ctrl+Shift+K')
    value.stop()


def test_apply_registers_nonrepeat_before_persisting_and_releases_old_afterward(manager):
    value, backend = manager
    saved = []
    value.apply('Ctrl+Alt+K')
    old = next(iter(backend.active))

    def persist(text):
        saved.append(text)
        assert len(backend.active) == 2
        assert old in backend.active
        assert value.shortcut == 'Ctrl+Alt+K'

    assert value.apply('shift+ctrl+l', persist=persist) == 'Ctrl+Shift+L'
    assert saved == ['Ctrl+Shift+L']
    assert value.shortcut == 'Ctrl+Shift+L'
    assert len(backend.active) == 1 and old not in backend.active
    assert next(iter(backend.active.values())) == (0x4000 | 0x0002 | 0x0004, ord('L'))


def test_conflict_keeps_current_native_registration_and_saved_setting(manager):
    value, backend = manager
    value.apply('Ctrl+Alt+K')
    old = dict(backend.active)
    backend.conflicts.add((0x4006, ord('L')))
    saved = []
    with pytest.raises(ValueError, match='占用|注册'):
        value.apply('Ctrl+Shift+L', persist=saved.append)
    assert value.shortcut == 'Ctrl+Alt+K'
    assert backend.active == old and saved == []


def test_failed_persistence_releases_candidate_and_preserves_current_shortcut(manager):
    value, backend = manager
    value.apply('Ctrl+Alt+K')
    old = dict(backend.active)

    def fail(_text):
        raise OSError('disk unavailable')

    with pytest.raises(OSError, match='disk unavailable'):
        value.apply('Ctrl+Shift+L', persist=fail)
    assert backend.active == old and value.shortcut == 'Ctrl+Alt+K'


def test_same_shortcut_does_not_reregister_and_can_persist_current_setting(manager):
    value, backend = manager
    value.apply('Ctrl+Alt+K')
    calls = list(backend.calls)
    saved = []
    assert value.apply('alt+ctrl+k', persist=saved.append) == 'Ctrl+Alt+K'
    assert backend.calls == calls and saved == ['Ctrl+Alt+K']


def test_disable_and_repeated_stop_release_registration(manager):
    value, backend = manager
    value.apply('Ctrl+Alt+K')
    saved = []
    assert value.apply('', persist=saved.append) == ''
    assert not backend.active and value.shortcut == '' and saved == ['']
    value.stop()
    value.stop()
    assert not backend.active


def test_disable_persistence_failure_keeps_current_shortcut(manager):
    value, backend = manager
    value.apply('Ctrl+Alt+K')
    old = dict(backend.active)

    def fail(_text):
        raise OSError('disk unavailable')

    with pytest.raises(OSError, match='disk unavailable'):
        value.apply('', persist=fail)
    assert backend.active == old and value.shortcut == 'Ctrl+Alt+K'


@pytest.mark.parametrize('replacement', ['', 'Ctrl+Shift+L'])
def test_unregister_failure_rolls_back_saved_setting_and_candidate(manager, replacement):
    value, backend = manager
    value.apply('Ctrl+Alt+K')
    old = dict(backend.active)
    backend.fail_unregister.update(old)
    saved = []
    with pytest.raises(OSError):
        value.apply(replacement, persist=saved.append)
    assert value.shortcut == 'Ctrl+Alt+K' and backend.active == old
    assert saved == [replacement, 'Ctrl+Alt+K']


def message(identity, modifiers=0x0003, key=ord('K'), *, kind=0x0312, hwnd=None):
    value = w.MSG()
    value.hWnd = hwnd
    value.message = kind
    value.wParam = identity
    value.lParam = modifiers | (key << 16)
    return value


def deliver(value, native, event_type=b'windows_dispatcher_MSG'):
    return value._filter.nativeEventFilter(QByteArray(event_type), ctypes.addressof(native))


def test_nonzero_shiboken_pointer_may_have_false_truth_value(manager, qapp):
    import shiboken6
    value, backend = manager
    value.apply('Ctrl+Alt+K')
    native = message(next(iter(backend.active)))
    pointer = shiboken6.VoidPtr(ctypes.addressof(native), 0)
    assert int(pointer) != 0 and not pointer
    seen = []
    value.activated.connect(lambda: seen.append(True))
    assert value._filter.nativeEventFilter(QByteArray(b'windows_generic_MSG'), pointer) == (True, 0)
    qapp.processEvents()
    assert seen == [True]


@pytest.mark.parametrize('hotkey_event_type', [b'windows_dispatcher_MSG', b'windows_generic_MSG'])
def test_only_owned_current_hotkey_dispatches_once_outside_native_filter(manager, qapp, hotkey_event_type):
    value, backend = manager
    value.apply('Ctrl+Alt+K')
    identity = next(iter(backend.active))
    seen = []
    value.activated.connect(lambda: seen.append(value.shortcut))
    for native, event_type in [
        (message(identity), b'xcb_generic_event_t'),
        (message(identity + 1), b'windows_dispatcher_MSG'),
        (message(identity, kind=0x0100), b'windows_dispatcher_MSG'),
        (message(identity, modifiers=0x0006), b'windows_dispatcher_MSG'),
        (message(identity, key=ord('L')), b'windows_dispatcher_MSG'),
        (message(identity, hwnd=123), b'windows_dispatcher_MSG'),
    ]:
        assert deliver(value, native, event_type) == (False, 0)
    native = message(identity)
    assert deliver(value, native, hotkey_event_type) == (True, 0)
    assert deliver(value, native, hotkey_event_type) == (True, 0)
    assert seen == []
    qapp.processEvents()
    assert seen == ['Ctrl+Alt+K']
    assert deliver(value, native, hotkey_event_type) == (True, 0)
    qapp.processEvents()
    assert seen == ['Ctrl+Alt+K', 'Ctrl+Alt+K']


@pytest.mark.parametrize('action', ['change', 'disable', 'stop'])
def test_queued_activation_is_discarded_after_change_disable_or_stop(manager, qapp, action):
    value, backend = manager
    value.apply('Ctrl+Alt+K')
    old = next(iter(backend.active))
    seen = []
    value.activated.connect(lambda: seen.append(True))
    native = message(old)
    assert deliver(value, native) == (True, 0)
    if action == 'change':
        value.apply('Ctrl+Shift+L')
    elif action == 'disable':
        value.apply('')
    else:
        value.stop()
    qapp.processEvents()
    assert seen == []
    assert deliver(value, native) == (False, 0)


def test_controllers_never_share_native_ids_in_one_application(qapp):
    backend = Backend()
    first = GlobalShortcut(backend=backend)
    second = GlobalShortcut(backend=backend)
    try:
        first.apply('Ctrl+Alt+K')
        second.apply('Ctrl+Shift+L')
        assert len(backend.active) == 2
        first.stop()
        assert len(backend.active) == 1
        assert second.shortcut == 'Ctrl+Shift+L'
    finally:
        first.stop()
        second.stop()


def test_stop_suppresses_queued_activation_even_when_native_release_fails(manager, qapp):
    value, backend = manager
    value.apply('Ctrl+Alt+K')
    identity = next(iter(backend.active))
    seen = []
    value.activated.connect(lambda: seen.append(True))
    assert deliver(value, message(identity)) == (True, 0)
    backend.fail_unregister.add(identity)
    with pytest.raises(OSError):
        value.stop()
    qapp.processEvents()
    assert seen == [] and value.shortcut == ''
    assert deliver(value, message(identity)) == (False, 0)
    backend.fail_unregister.clear()
    value.stop()
    assert not backend.active


def test_disable_retries_residual_ownership_after_failed_stop(manager):
    value, backend = manager
    value.apply('Ctrl+Alt+K')
    identity = next(iter(backend.active))
    backend.fail_unregister.add(identity)
    with pytest.raises(OSError):
        value.stop()
    with pytest.raises(OSError):
        value.apply('')
    assert identity in backend.active
    backend.fail_unregister.clear()
    value.apply('')
    assert not backend.active and value.shortcut == ''


def test_failed_candidate_cleanup_remains_owned_and_can_be_retried(manager):
    value, backend = manager
    value.apply('Ctrl+Alt+K')
    original = dict(backend.active)

    def fail_after_candidate(_text):
        backend.fail_unregister.update(set(backend.active) - set(original))
        raise OSError('disk unavailable')

    with pytest.raises(OSError, match='恢复未全部完成'):
        value.apply('Ctrl+Shift+L', persist=fail_after_candidate)
    assert value.shortcut == 'Ctrl+Alt+K' and len(backend.active) == 2
    with pytest.raises(OSError):
        value.apply('Ctrl+Shift+M')
    backend.fail_unregister.clear()
    value.apply('Ctrl+Shift+L')
    assert value.shortcut == 'Ctrl+Shift+L' and len(backend.active) == 1


def test_destroying_qobject_parent_releases_native_resources_without_dead_signals(qapp):
    import shiboken6
    backend = Backend()
    parent = QObject()
    value = GlobalShortcut(parent, backend=backend)
    value.apply('Ctrl+Alt+K')
    identity = next(iter(backend.active))
    native_filter = value._filter
    seen = []
    value.activated.connect(lambda: seen.append(True))
    assert deliver(value, message(identity)) == (True, 0)
    shiboken6.delete(parent)
    assert not shiboken6.isValid(value)
    assert not backend.active
    assert not value._installed
    assert native_filter.nativeEventFilter(QByteArray(b'windows_generic_MSG'),
                                           ctypes.addressof(message(identity))) == (False, 0)
    qapp.processEvents()
    assert seen == []


def test_destruction_cleanup_logs_release_failure_without_raising_from_qt(qapp, caplog):
    import shiboken6
    backend = Backend()
    parent = QObject()
    value = GlobalShortcut(parent, backend=backend)
    value.apply('Ctrl+Alt+K')
    identity = next(iter(backend.active))
    backend.fail_unregister.add(identity)
    native = message(identity)
    shiboken6.delete(parent)
    assert not value._installed
    assert deliver(value, native) == (False, 0)
    assert '进程退出' in caplog.text
    # The simulated API error represents an unreleased registration, not a
    # successful cleanup. Its plain resource owner can still retry safely.
    assert identity in backend.active
    backend.fail_unregister.clear()
    value._resources.close()
    assert not backend.active


@pytest.mark.parametrize('replacement', ['', 'Ctrl+Shift+L'])
def test_rollback_restores_saved_intent_when_it_differs_from_effective_shortcut(manager, replacement):
    value, backend = manager
    value.apply('Ctrl+Alt+K')
    backend.fail_unregister.update(backend.active)
    saved = {'value': 'Ctrl+Shift+M'}
    previous = saved['value']
    with pytest.raises(OSError):
        value.apply(replacement, persist=lambda text: saved.update(value=text),
                    rollback=lambda: saved.update(value=previous))
    assert value.shortcut == 'Ctrl+Alt+K'
    assert saved['value'] == 'Ctrl+Shift+M'


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows native registration and event dispatcher')
def test_windows_native_registration_conflict_release_and_qt_message_dispatch():
    # A fresh Qt Windows dispatcher proves the binding return contract without
    # sending keystrokes to any real application or opening a person's database.
    script = r'''
import ctypes
from ctypes import wintypes as w
from PySide6.QtCore import QElapsedTimer, QObject
from PySide6.QtWidgets import QApplication
from global_shortcut import GlobalShortcut
app = QApplication([])
first, second = GlobalShortcut(), GlobalShortcut()
seen = []
first.activated.connect(lambda: seen.append(True))
try:
    for key in 'KLMNPQRSTUVWXYZ0123456789':
        try:
            first.apply('Ctrl+Alt+Shift+' + key)
            break
        except ValueError:
            continue
    assert first.shortcut, 'No available anonymous test shortcut'
    combination = first.shortcut
    try:
        second.apply(combination)
    except ValueError:
        pass
    else:
        raise AssertionError('Duplicate global combination unexpectedly registered')
    api = ctypes.WinDLL('user32', use_last_error=True)
    api.PostThreadMessageW.argtypes = [w.DWORD, w.UINT, w.WPARAM, w.LPARAM]
    api.PostThreadMessageW.restype = w.BOOL
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentThreadId.restype = w.DWORD
    assert api.PostThreadMessageW(kernel.GetCurrentThreadId(), 0x0312,
                                  first._active_id, 7 | (ord(key) << 16))
    timer = QElapsedTimer()
    timer.start()
    while not seen and timer.elapsed() < 2000:
        app.processEvents()
    assert seen == [True], seen
    first.stop()
    second.apply(combination)
    second.stop()
    parent = QObject()
    child = GlobalShortcut(parent)
    child.apply(combination)
    import shiboken6
    shiboken6.delete(parent)
    first.apply(combination)
    first.stop()
    print('native registration, conflict, Qt dispatch, and release passed')
finally:
    first.stop()
    second.stop()
'''
    environment = dict(os.environ, QT_QPA_PLATFORM='windows')
    result = subprocess.run([sys.executable, '-c', script], cwd=Path(__file__).resolve().parents[1],
                            env=environment, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Qt dispatch, and release passed' in result.stdout
