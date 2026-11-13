"""Windows title styling must preserve Qt's native window identity on reopen."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.skipif(sys.platform != 'win32', reason='Requires the Qt Windows platform and real HWNDs')
@pytest.mark.parametrize('close_method', ['qwindow', 'titlebar'])
def test_native_dialog_reopen_keeps_window_identity_and_input(close_method):
    # A fresh Windows dispatcher avoids inheriting the suite's offscreen app.
    # The only native message targets this process's anonymous dialog.
    script = r'''
import ctypes
from ctypes import wintypes as w
import sys
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QVBoxLayout, QWidget
from theme import apply_to, install_theme, set_theme

app = QApplication([])
app.setQuitOnLastWindowClosed(False)
assert app.platformName() == 'windows'
install_theme()
user32 = ctypes.WinDLL('user32', use_last_error=True)
user32.IsWindow.argtypes = [w.HWND]
user32.IsWindow.restype = w.BOOL
user32.SendMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
user32.SendMessageW.restype = w.LPARAM

dialog = QDialog()
dialog.setWindowTitle('Anonymous theme window identity regression')
layout = QVBoxLayout(dialog)
entry = QLineEdit(dialog)
entry.setText('preserved anonymous input')
layout.addWidget(entry)
apply_to(dialog, 'dialog')

def hidden_theme_change(mode):
    assert not dialog.isVisible()
    previous = (int(dialog.effectiveWinId()), dialog.testAttribute(Qt.WA_WState_Created))
    set_theme(mode, persist=False)
    app.processEvents()
    current = (int(dialog.effectiveWinId()), dialog.testAttribute(Qt.WA_WState_Created))
    assert current == previous, ('Theme created native resources while hidden', previous, current)

def show_and_check_identity():
    dialog.show()
    assert QTest.qWaitForWindowExposed(dialog, 3000), 'Anonymous dialog was not exposed'
    app.processEvents()
    # Only query QWindow.winId while exposed: querying a hidden window can create it.
    actual = int(dialog.windowHandle().winId())
    effective = int(dialog.effectiveWinId())
    assert user32.IsWindow(actual), ('Not a live native window', actual)
    assert effective == actual, ('Stale QWidget window identity after reopen', effective, actual)
    assert QWidget.find(actual) is dialog, ('Native HWND does not map back to dialog', actual)
    assert entry.text() == 'preserved anonymous input'
    return actual

try:
    hidden_theme_change('dark')
    hidden_theme_change('light')
    for mode, hidden_mode in [('light', 'dark'), ('dark', 'light')]:
        set_theme(mode, persist=False)
        old_hwnd = show_and_check_identity()
        # Ordinary hide is allowed to retain its HWND. Theme changes must leave it alone.
        dialog.hide()
        app.processEvents()
        hidden_theme_change(hidden_mode)
        show_and_check_identity()
        if sys.argv[1] == 'qwindow':
            assert dialog.windowHandle().close(), 'Qt rejected native window close'
        else:
            user32.SendMessageW(old_hwnd, 0x0112, 0xF060, 0)  # WM_SYSCOMMAND / SC_CLOSE
        app.processEvents()
        assert not dialog.isVisible(), 'Native close left the dialog visible'
        assert not user32.IsWindow(old_hwnd), 'Close did not exercise native platform destruction'
        hidden_theme_change(mode)
        show_and_check_identity()
        dialog.hide()
        app.processEvents()
    print('Native close/reopen window identity and input preserved')
finally:
    dialog.hide()
    dialog.deleteLater()
    app.processEvents()
'''
    environment = dict(os.environ, QT_QPA_PLATFORM='windows')
    result = subprocess.run([sys.executable, '-c', script, close_method],
                            cwd=Path(__file__).resolve().parents[1], env=environment,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Native close/reopen window identity and input preserved' in result.stdout
