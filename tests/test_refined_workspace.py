"""The workspace keeps queue tools local and updates reachable at both sizes."""
import pytest
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from compact_ui import CompactPanel
from desktop_service import DesktopService


@pytest.mark.parametrize('size', [(1200, 780), (660, 480)])
def test_queue_tools_stay_with_the_queue_and_update_is_keyboard_reachable_in_menu(tmp_path, size):
    app = QApplication.instance() or QApplication([])
    panel = CompactPanel(DesktopService(tmp_path / 'data' / 'workbox.db'))
    panel.resize(*size)
    panel.show()
    QTest.qWait(100)
    try:
        queue = panel.project_splitter.widget(0)
        for control in (panel.project_list_heading, panel.added_date_filter, panel.bulk_mode):
            assert queue.isAncestorOf(control)
            assert control.isVisible()
            position = control.mapTo(queue, QPoint(0, 0))
            assert queue.rect().contains(position)
            assert position.x() + control.width() <= queue.width()
        assert panel.update_button.isHidden()
        calls = []
        panel.update_requested.connect(lambda: calls.append('check'))
        update = panel.advanced_actions['update']
        seen = []
        def choose_update():
            menu = panel.advanced_menu
            seen.append(menu.isVisible() and update.isEnabled())
            QTest.mouseClick(menu, Qt.LeftButton, pos=menu.actionGeometry(update).center())
        QTimer.singleShot(80, choose_update)
        panel.settings_button.click()
        assert seen == [True]
        assert calls == ['check']
        assert panel.update_button.accessibleName() == '检查更新'
        assert not panel.update_button.isCheckable()
        assert panel.width() == size[0]
        panel.today_button.setFocus()
        QTest.keyClick(panel.today_button, Qt.Key_Tab)
        assert app.focusWidget() is panel.settings_button
        QTest.keyClick(panel.settings_button, Qt.Key_Tab)
        assert app.focusWidget() is panel.search
        QTest.keyClick(panel.search, Qt.Key_Backtab)
        assert app.focusWidget() is panel.settings_button
        QTest.keyClick(panel.settings_button, Qt.Key_Backtab)
        assert app.focusWidget() is panel.today_button
        QTest.keyClick(panel.today_button, Qt.Key_Tab)
        assert app.focusWidget() is panel.settings_button
        def choose_update_by_keyboard():
            menu = panel.advanced_menu
            seen.append(menu.isVisible() and update.isEnabled())
            menu.setActiveAction(update)
            QTest.keyClick(menu, Qt.Key_Return)
        QTimer.singleShot(80, choose_update_by_keyboard)
        QTest.keyClick(panel.settings_button, Qt.Key_Space)
        assert seen == [True, True]
        assert calls == ['check', 'check']
    finally:
        panel.document_pane.shutdown()
        panel.hide()
        panel.deleteLater()
        app.processEvents()
