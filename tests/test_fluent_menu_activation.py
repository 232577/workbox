"""通过按钮真实入口展开原生外观菜单，不直接调用 menu.popup。"""
import ctypes
import sys

if sys.platform == 'win32':
    ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002)
    ctypes.windll.kernel32.WerSetFlags(0x0020)

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAccessible, QAccessibleActionInterface
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication

from compact_ui import CompactPanel
from desktop_service import DesktopService
from theme import install_theme, set_theme


@pytest.fixture(scope='module')
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


@pytest.mark.parametrize('mode', ['light', 'dark', 'system'])
@pytest.mark.parametrize('activation', ['mouse', 'space', 'click', 'accessible_show_menu'])
def test_appearance_button_opens_native_menu_through_its_activation(qapp, tmp_path, mode, activation):
    service = DesktopService(tmp_path / 'data/workbox.db')
    before = service.get_setting('appearance_theme', 'light')
    install_theme(service)
    set_theme(mode, persist=False)
    panel = CompactPanel(service)
    panel.show()
    qapp.processEvents()
    button = panel.appearance_button
    menu = button.menu()
    shown = QSignalSpy(menu.aboutToShow)
    observed = []

    def observe_and_close():
        observed.append((menu.isVisible(), qapp.activePopupWidget() is menu,
                         [action.text() for action in menu.actions()]))
        menu.close()

    observer = QTimer(menu)
    observer.setSingleShot(True)
    observer.timeout.connect(observe_and_close)
    try:
        button.setFocus()
        observer.start(80)
        if activation == 'mouse':
            QTest.mouseClick(button, Qt.LeftButton)
        elif activation == 'space':
            QTest.keyClick(button, Qt.Key_Space)
        elif activation == 'click':
            button.click()
        else:
            interface = QAccessible.queryAccessibleInterface(button)
            actions = interface.actionInterface()
            assert QAccessibleActionInterface.showMenuAction() in actions.actionNames()
            actions.doAction(QAccessibleActionInterface.showMenuAction())
        for _ in range(50):
            if observed:
                break
            QTest.qWait(20)
        assert shown.count() == 1
        assert observed == [(True, True, ['浅色', '深色', '跟随系统'])]
        assert service.get_setting('appearance_theme', 'light') == before
    finally:
        observer.stop()
        menu.close()
        panel.document_pane.shutdown()
        panel.close()
        install_theme()
