"""Opt-in Windows hotkeys using Qt's dispatcher, without keyboard monitoring."""

from __future__ import annotations

import ctypes
from ctypes import wintypes as w
from itertools import count
import logging
import re
import sys
import weakref

import shiboken6
from PySide6.QtCore import QAbstractNativeEventFilter, QObject, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtWidgets import QApplication


_MODIFIERS = {'ctrl': ('Ctrl', 0x0002), 'alt': ('Alt', 0x0001), 'shift': ('Shift', 0x0004)}
_NOREPEAT = 0x4000
_WM_HOTKEY = 0x0312
# Never reuse IDs: a message already queued for an old registration cannot
# activate a new shortcut or a different controller sharing this Qt thread.
_identities = count(1)


def parse_shortcut(text):
    """Return canonical text, Win32 modifiers and virtual key; empty disables."""
    if type(text) is not str or len(text) > 64:
        raise ValueError('快捷键须为不超过 64 个字符的组合。')
    if not text.strip():
        return '', 0, 0
    parts = [part.strip().casefold() for part in text.split('+')]
    modifiers, key = parts[:-1], parts[-1]
    if (len(modifiers) < 2 or len(set(modifiers)) != len(modifiers)
            or any(part not in _MODIFIERS for part in modifiers)
            or re.fullmatch(r'(?:[a-z0-9]|f(?:[1-9]|10|11))', key) is None):
        raise ValueError('请使用 Ctrl、Alt、Shift 中至少两个，搭配字母、数字或 F1–F11；不支持连续组合或 Windows 键。')
    canonical = '+'.join([display for name, (display, _flag) in _MODIFIERS.items()
                          if name in modifiers] + [key.upper()])
    flags = sum(_MODIFIERS[name][1] for name in modifiers)
    virtual_key = 0x70 + int(key[1:]) - 1 if key.startswith('f') and len(key) > 1 else ord(key.upper())
    return canonical, flags, virtual_key


def validate_shortcut(text):
    return parse_shortcut(text)[0]


class _WindowsBackend:
    """All calls belong to the Qt GUI thread which receives WM_HOTKEY."""

    def __init__(self):
        self.api = ctypes.WinDLL('user32', use_last_error=True)
        self.api.RegisterHotKey.argtypes = [w.HWND, ctypes.c_int, w.UINT, w.UINT]
        self.api.RegisterHotKey.restype = w.BOOL
        self.api.UnregisterHotKey.argtypes = [w.HWND, ctypes.c_int]
        self.api.UnregisterHotKey.restype = w.BOOL

    def register(self, identity, modifiers, key):
        if not self.api.RegisterHotKey(None, identity, modifiers, key):
            raise ctypes.WinError(ctypes.get_last_error())
        return True

    def unregister(self, identity):
        if not self.api.UnregisterHotKey(None, identity):
            raise ctypes.WinError(ctypes.get_last_error())
        return True


class _NativeFilter(QAbstractNativeEventFilter):
    def __init__(self, owner):
        super().__init__()
        self._owner = weakref.ref(owner)

    def nativeEventFilter(self, event_type, message):
        owner = self._owner()
        if owner is None or not shiboken6.isValid(owner):
            return False, 0
        return owner._native_event(event_type, message)


class _NativeResources:
    """Outlive QObject teardown without calling methods on its deleted wrapper."""

    def __init__(self, backend, native_filter):
        self.backend = backend
        self.filter = native_filter
        self.registrations = {}
        self.application = None

    def remove_filter(self):
        if self.application is not None:
            application, self.application = self.application, None
            if shiboken6.isValid(application):
                application.removeNativeEventFilter(self.filter)

    def release(self, identity):
        if self.backend.unregister(identity) is False:
            raise OSError('无法释放快捷键，请重试。')
        del self.registrations[identity]

    def close(self, *_destroyed):
        try:
            application = self.application
            if (application is not None and shiboken6.isValid(application)
                    and QThread.currentThread() != application.thread()):
                # Parent destruction can originate elsewhere; unregister on the
                # registration thread while the resource owner survives.
                QTimer.singleShot(0, application, self.close)
                return
            self.remove_filter()
            for identity in list(self.registrations):
                try:
                    self.release(identity)
                except Exception:
                    logging.getLogger(__name__).warning(
                        '全局快捷键 %s 未能释放；该注册可能保留到进程退出。', identity)
        except Exception:
            logging.getLogger(__name__).warning('全局快捷键析构清理失败；资源可能保留到进程退出。')


class GlobalShortcut(QObject):
    """Own one effective shortcut; settings change only through apply's callback.

    Injected backends implement register(id, modifiers, vk) and unregister(id),
    returning True (or None) on success and raising OSError on failure. They allow
    deterministic tests without reserving real user shortcuts.
    """

    activated = Signal()
    _dispatch_requested = Signal(int)

    def __init__(self, parent=None, *, backend=None):
        super().__init__(parent)
        application = QApplication.instance()
        native = (sys.platform == 'win32' and application is not None
                  and application.platformName() == 'windows')
        self.supported = backend is not None or native
        self._backend = backend if backend is not None else (_WindowsBackend() if native else None)
        self.shortcut = ''
        self._active_id = None
        self._filter = _NativeFilter(self)
        self._resources = _NativeResources(self._backend, self._filter)
        self._registrations = self._resources.registrations
        self._changing = False
        self._generation = 0
        self._pending_generation = None
        self._dispatch_requested.connect(self._dispatch, Qt.QueuedConnection)
        self.destroyed.connect(self._resources.close, Qt.DirectConnection)

    @property
    def _installed(self):
        return self._resources.application is not None

    @staticmethod
    def _gui_application():
        application = QApplication.instance()
        if application is None or QThread.currentThread() != application.thread():
            raise RuntimeError('快捷键必须在 QApplication 存活的界面线程中设置。')
        return application

    def _install_filter(self):
        if not self._installed:
            application = self._gui_application()
            application.installNativeEventFilter(self._filter)
            self._resources.application = application

    def _remove_filter(self):
        self._resources.remove_filter()

    def _invalidate_pending(self):
        self._generation += 1
        self._pending_generation = None

    def _release(self, identity):
        self._resources.release(identity)

    def _rollback(self, candidate, persist, previous, rollback, *, restore_setting):
        errors = []
        if candidate is not None:
            try:
                self._release(candidate)
            except OSError as error:
                errors.append(error)
        if restore_setting and (rollback is not None or persist is not None):
            try:
                if rollback is not None:
                    rollback()
                else:
                    persist(previous)
            except Exception as error:
                errors.append(error)
        if not self._registrations:
            self._remove_filter()
        if errors:
            raise OSError('快捷键恢复未全部完成，请重试关闭快捷键并检查设置。') from errors[0]

    def apply(self, text, persist=None, rollback=None):
        """Keep the old native key on failure; rollback can restore saved intent."""
        canonical, modifiers, key = parse_shortcut(text)
        self._gui_application()
        if canonical and not self.supported:
            raise ValueError('全局快捷键当前仅支持 Windows 桌面环境。')
        if self._changing:
            raise RuntimeError('快捷键正在更新，请稍后重试。')
        # Retry a prior failed cleanup before claiming another change succeeded.
        for identity in list(self._registrations):
            if identity != self._active_id:
                self._release(identity)
        if not self._registrations:
            self._remove_filter()
        if canonical == self.shortcut:
            if persist is not None:
                persist(canonical)
            return canonical
        previous, old_id = self.shortcut, self._active_id
        self._changing = True
        self._invalidate_pending()
        candidate = None
        saved = False
        try:
            if canonical:
                candidate = next(_identities)
                if candidate > 0xBFFF:
                    candidate = None
                    raise ValueError('本次运行的快捷键调整次数过多，请重启 WorkBox 后重试。')
                try:
                    if self._backend.register(candidate, modifiers | _NOREPEAT, key) is False:
                        raise OSError(1409, 'hotkey registration failed')
                except OSError as error:
                    code = getattr(error, 'winerror', None) or error.errno
                    if code == 1409:
                        raise ValueError('此快捷键已被其他程序占用，请换一个组合。') from error
                    raise OSError('无法注册全局快捷键，请换一个组合或稍后重试。') from error
                self._registrations[candidate] = (modifiers, key)
                self._install_filter()
            if persist is not None:
                persist(canonical)
                saved = True
            if old_id is not None:
                self._release(old_id)
            self._active_id = candidate
            self.shortcut = canonical
            if not self._registrations:
                self._remove_filter()
            return canonical
        except Exception:
            # Registration failures never add ownership. A failed cleanup remains
            # tracked so stop() can retry instead of silently leaking a hotkey.
            owned_candidate = candidate if candidate in self._registrations else None
            self._rollback(owned_candidate, persist, previous, rollback, restore_setting=saved)
            raise
        finally:
            self._changing = False

    def stop(self):
        """Suppress activation immediately; retry any failed releases on next stop."""
        if self._installed or self._registrations:
            self._gui_application()
        self._invalidate_pending()
        self._active_id = None
        self.shortcut = ''
        self._remove_filter()
        first_error = None
        for identity in list(self._registrations):
            try:
                self._release(identity)
            except OSError as error:
                first_error = first_error or error
        if first_error is not None:
            raise OSError('无法释放全部全局快捷键，请重试关闭或退出 WorkBox。') from first_error

    def _native_event(self, event_type, message):
        if (self._changing or not self._installed or self._active_id is None
                or bytes(event_type) not in (b'windows_dispatcher_MSG', b'windows_generic_MSG')):
            return False, 0
        # Shiboken's non-null VoidPtr can have zero buffer length and be false.
        address = int(message) if message is not None else 0
        if not address:
            return False, 0
        native = w.MSG.from_address(address)
        if native.hWnd or native.message != _WM_HOTKEY or native.wParam != self._active_id:
            return False, 0
        modifiers, key = self._registrations[self._active_id]
        if (native.lParam & 0xFFFF) != modifiers or ((native.lParam >> 16) & 0xFFFF) != key:
            return False, 0
        if self._pending_generation != self._generation:
            self._pending_generation = self._generation
            self._dispatch_requested.emit(self._generation)
        # PySide6 6.8 returns (handled, LRESULT), including the ignored path.
        return True, 0

    @Slot(int)
    def _dispatch(self, generation):
        if generation != self._generation or self._pending_generation != generation:
            return
        self._pending_generation = None
        if self._installed and self._active_id is not None and not self._changing:
            self.activated.emit()
