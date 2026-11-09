"""The workspace keeps queue tools local and updates reachable at both sizes."""
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from compact_ui import CompactPanel
from desktop_service import DesktopService


@pytest.mark.parametrize('size', [(1200, 780), (660, 480)])
def test_queue_tools_stay_with_the_queue_and_update_is_one_click(tmp_path, size):
    app = QApplication.instance() or QApplication([])
    panel = CompactPanel(DesktopService(tmp_path / 'data' / 'workbox.db'))
    panel.resize(*size)
    panel.show()
    QTest.qWait(100)
    try:
        queue = panel.project_splitter.widget(0)
        for control in (panel.project_list_heading, panel.batch_filter, panel.bulk_mode):
            assert queue.isAncestorOf(control)
            assert control.isVisible()
            position = control.mapTo(queue, QPoint(0, 0))
            assert queue.rect().contains(position)
            assert position.x() + control.width() <= queue.width()
        assert panel.update_button.isVisible()
        assert panel.footer_surface.isAncestorOf(panel.update_button)
        calls = []
        panel.update_requested.connect(lambda: calls.append('check'))
        panel.update_button.click()
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
        for _ in range(len(panel.findChildren(QWidget))):
            focused = app.focusWidget()
            assert focused is not None
            if focused is panel.update_button:
                break
            QTest.keyClick(focused, Qt.Key_Tab)
        assert app.focusWidget() is panel.update_button
        QTest.keyClick(panel.update_button, Qt.Key_Space)
        assert calls == ['check', 'check']
    finally:
        panel.document_pane.shutdown()
        panel.hide()
        panel.deleteLater()
        app.processEvents()
