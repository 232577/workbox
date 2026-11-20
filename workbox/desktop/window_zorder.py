"""Non-activating Windows stacking repair for the floating intake orb only."""
import ctypes
from functools import lru_cache
import os
import sys


@lru_cache(maxsize=1)
def _api():
    from ctypes import wintypes as w
    api = ctypes.WinDLL('user32', use_last_error=True)
    api.IsWindow.argtypes = [w.HWND]
    api.IsWindow.restype = w.BOOL
    api.IsWindowVisible.argtypes = [w.HWND]
    api.IsWindowVisible.restype = w.BOOL
    api.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
    api.GetWindowThreadProcessId.restype = w.DWORD
    api.SetWindowPos.argtypes = [w.HWND, w.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, w.UINT]
    api.SetWindowPos.restype = w.BOOL
    return api


def keep_window_topmost(hwnd: int) -> bool:
    """Reassert order, never show/activate/move a window or affect another process."""
    if sys.platform != 'win32' or type(hwnd) is not int or hwnd <= 0:
        return False
    try:
        from ctypes import wintypes as w
        api = _api()
        pid = w.DWORD()
        if not api.IsWindow(hwnd) or not api.IsWindowVisible(hwnd):
            return False
        if not api.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)) or pid.value != os.getpid():
            return False
        # HWND_TOPMOST plus NOSIZE|NOMOVE|NOACTIVATE|NOOWNERZORDER.
        # Do not use SHOWWINDOW, foreground activation, or a hide/show flag reset.
        return bool(api.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010 | 0x0200))
    except (OSError, AttributeError, ValueError):
        # A failed desktop operation must not interrupt file intake or shutdown.
        return False
