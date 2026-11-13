"""Daily commands stay direct while occasional tools use the native menu."""
import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication

from compact_ui import CompactPanel
from desktop_service import DesktopService


@pytest.fixture
def panel(tmp_path):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    service = DesktopService(tmp_path / 'data' / 'anonymous.db')
    service.receive_text('1. 匿名入口项目')
    value = CompactPanel(service)
    value.show()
    app.processEvents()
    yield value
    if value.checklist_dialog is not None:
        value.checklist_dialog.shutdown()
        value.checklist_dialog.close()
    value.document_pane.shutdown()
    value.close()


@pytest.mark.parametrize('size', [(1200, 780), (660, 480)])
def test_daily_header_and_footer_keep_only_their_commands(panel, size):
    panel.resize(*size)
    QApplication.processEvents()
    for page in (0, 1, 2, 0):
        panel.tabs.setCurrentIndex(page)
        QApplication.processEvents()
        assert panel.checklist_button.isHidden()
        assert panel.update_button.isHidden()
        assert panel.background_button.isVisible() and panel.exit_button.isVisible()
        if page in (0, 1):
            assert panel.paste_button.isVisible() and panel.files_button.isVisible()
    panel.set_busy(True)
    assert panel.cancel_button.isVisible()
    assert panel.update_button.isHidden() and panel.checklist_button.isHidden()


@pytest.mark.parametrize('activation', ['mouse', 'keyboard'])
def test_update_remains_reachable_through_actual_native_menu(panel, activation):
    action = panel.advanced_actions['update']
    fired = QSignalSpy(panel.update_requested)
    observations = []

    def choose_update():
        menu = panel.advanced_menu
        observations.append(menu.isVisible() and action.isEnabled())
        if activation == 'mouse':
            QTest.mouseClick(menu, Qt.LeftButton, pos=menu.actionGeometry(action).center())
        else:
            menu.setActiveAction(action)
            QTest.keyClick(menu, Qt.Key_Return)

    QTimer.singleShot(80, choose_update)
    panel.settings_button.click()
    QTest.qWait(30)
    assert observations == [True]
    assert fired.count() == 1
    assert not panel.advanced_menu.isVisible()


def test_advanced_menu_groups_tools_without_retired_workflows(panel):
    groups = []
    for action in panel.advanced_menu.actions():
        if action.isSeparator():
            continue
        if not action.isEnabled() and action.text() in ('工作与规则', '核对与记录', '应用'):
            groups.append((action.text(), []))
        else:
            assert groups, 'Every tool needs a visible purpose group'
            groups[-1][1].append(action.text())
    assert groups == [
        ('工作与规则', ['工作设置…', '归档分类']),
        ('核对与记录', ['名单核对', '待核对', '收录记录', 'ZIP 归档库', '监视详情']),
        ('应用', ['外观', '使用指南', '检查更新']),
    ]
    assert panel.task_groups_page is None and not hasattr(panel, 'nav_task_groups')
