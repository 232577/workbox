"""Window activation preserves the anonymous local workspace and native state."""
import json
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QSystemTrayIcon, QVBoxLayout

from desktop_controller import FloatingController
from desktop_service import DesktopService


@pytest.fixture(scope='module')
def qapp():
    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    return application


@pytest.fixture
def controllers(qapp, tmp_path):
    created = []

    def start(*, tray_enabled=False):
        value = FloatingController(DesktopService(tmp_path / 'data/anonymous.db'),
                                   tray_enabled=tray_enabled)
        created.append(value)
        assert value.start()
        qapp.processEvents()
        return value

    yield start
    for value in reversed(created):
        value.shutdown()
        value.dispose_ui()


def normal_geometry(controller, qapp):
    controller.show_panel()
    controller.panel.showNormal()
    area = controller.orb.screen().availableGeometry()
    controller.panel.setGeometry(QRect(area.left() + 24, area.top() + 40, 660, 480))
    qapp.processEvents()
    return QRect(controller.panel.geometry())


def test_reopen_keeps_user_geometry_after_ball_moves(controllers, qapp):
    controller = controllers()
    expected = normal_geometry(controller, qapp)
    controller.panel.close()
    controller.orb.move(controller.orb.screen().availableGeometry().topLeft())
    controller.show_panel()
    qapp.processEvents()
    assert controller.panel.isVisible()
    assert controller.panel.geometry() == expected


def test_first_open_places_panel_by_ball_inside_available_screen(controllers, qapp):
    controller = controllers()
    area = controller.orb.screen().availableGeometry()
    controller.orb.move(area.left() + 20, area.top() + 100)
    assert not controller.panel.isVisible()
    controller.show_panel()
    qapp.processEvents()
    frame = controller.panel.frameGeometry()
    assert area.contains(frame)
    assert frame.adjusted(-100, -100, 100, 100).intersects(controller.orb.geometry())


def test_visible_panel_does_not_follow_moved_ball_or_repeated_open(controllers, qapp):
    controller = controllers()
    expected = normal_geometry(controller, qapp)
    controller.orb.move(controller.orb.screen().availableGeometry().topLeft())
    qapp.processEvents()
    assert controller.panel.geometry() == expected
    controller.show_panel()
    qapp.processEvents()
    assert controller.panel.geometry() == expected


@pytest.mark.parametrize('hide', ['close', 'background', 'minimize'])
def test_maximized_panel_restores_maximized_and_keeps_normal_geometry(
        controllers, qapp, monkeypatch, hide):
    monkeypatch.setattr(QSystemTrayIcon, 'isSystemTrayAvailable', lambda: True)
    controller = controllers(tray_enabled=True)
    expected = normal_geometry(controller, qapp)
    controller.panel.showMaximized()
    qapp.processEvents()
    assert controller.panel.isMaximized()
    if hide == 'close':
        controller.panel.close()
    elif hide == 'background':
        assert controller.run_in_background()
    else:
        controller.panel.showMinimized()
    qapp.processEvents()
    controller.show_panel()
    qapp.processEvents()
    assert controller.panel.isVisible() and not controller.panel.isMinimized()
    assert controller.panel.isMaximized()
    controller.show_panel()
    qapp.processEvents()
    assert controller.panel.isMaximized(), 'Repeated activation must not unmaximize the workspace'
    controller.panel.showNormal()
    qapp.processEvents()
    assert controller.panel.geometry() == expected


@pytest.mark.parametrize('maximized', [False, True])
def test_restart_restores_user_geometry_and_maximized_state(controllers, qapp, maximized):
    first = controllers()
    expected = normal_geometry(first, qapp)
    if maximized:
        first.panel.showMaximized()
        qapp.processEvents()
    first.shutdown()
    first.dispose_ui()
    second = controllers()
    assert not second.panel.isVisible(), 'A restored workspace still starts behind the floating ball'
    second.orb.move(second.orb.screen().availableGeometry().topLeft())
    second.show_panel()
    qapp.processEvents()
    assert second.panel.isMaximized() is maximized
    if maximized:
        second.panel.showNormal()
        qapp.processEvents()
    assert second.panel.geometry() == expected


def workspace_state(panel):
    return (panel.search.text(), panel.tabs.currentIndex(),
            panel.urgent_filter.isChecked(), panel.archive_filter.currentData(),
            panel.current_project['id'] if panel.current_project else None,
            tuple(item.data(Qt.UserRole) for item in panel.project_list.selectedItems()),
            panel.project_list.verticalScrollBar().value())


@pytest.mark.parametrize('source', ['orb', 'tray', 'second_instance'])
def test_activation_raises_existing_modal_without_changing_workspace(
        controllers, qapp, monkeypatch, source):
    monkeypatch.setattr(QSystemTrayIcon, 'isSystemTrayAvailable', lambda: True)
    controller = controllers(tray_enabled=True)
    expected_geometry = normal_geometry(controller, qapp)
    panel = controller.panel
    panel.search.setText('匿名草稿')
    panel.urgent_filter.setChecked(True)
    panel.tabs.setCurrentIndex(1)
    qapp.processEvents()
    expected_state = workspace_state(panel)
    dialog = QDialog(panel)
    dialog.setWindowModality(Qt.ApplicationModal)
    layout = QVBoxLayout(dialog)
    draft = QLineEdit('尚未保存的匿名输入', dialog)
    layout.addWidget(draft)
    dialog.show()
    qapp.processEvents()
    assert QApplication.activeModalWidget() is dialog
    activated = []
    monkeypatch.setattr(dialog, 'raise_', lambda: activated.append('modal.raise'))
    monkeypatch.setattr(dialog, 'activateWindow', lambda: activated.append('modal.activate'))
    monkeypatch.setattr(panel, 'raise_', lambda: activated.append('panel.raise'))
    monkeypatch.setattr(panel, 'activateWindow', lambda: activated.append('panel.activate'))
    second = None
    try:
        if source == 'orb':
            controller.orb.open_requested.emit()
        elif source == 'tray':
            controller.tray.activated.emit(QSystemTrayIcon.Trigger)
        else:
            second = FloatingController(controller.service, tray_enabled=False)
            assert second.start() is False
        for _ in range(100):
            qapp.processEvents()
            if activated:
                break
            QTest.qWait(10)
        assert 'modal.raise' in activated and 'modal.activate' in activated
        assert not any(target.startswith('panel.') for target in activated)
        assert dialog.isVisible() and not dialog.isMinimized()
        assert draft.text() == '尚未保存的匿名输入'
        assert panel.geometry() == expected_geometry
        assert workspace_state(panel) == expected_state
    finally:
        dialog.close()
        dialog.deleteLater()
        if second is not None:
            second.shutdown()
            second.dispose_ui()


def test_show_close_and_background_keep_search_selection_scroll_and_navigation(
        controllers, qapp, monkeypatch):
    monkeypatch.setattr(QSystemTrayIcon, 'isSystemTrayAvailable', lambda: True)
    controller = controllers(tray_enabled=True)
    controller.service.receive_text('\n'.join(f'{index}. 匿名测试项目{index:02d}'
                                              for index in range(1, 46)))
    normal_geometry(controller, qapp)
    panel = controller.panel
    panel.search.setText('匿名测试项目')
    qapp.processEvents()
    assert panel.project_list.count() == 45
    panel.project_list.setCurrentRow(20)
    scrollbar = panel.project_list.verticalScrollBar()
    scrollbar.setValue(scrollbar.maximum() // 2)
    qapp.processEvents()
    assert scrollbar.value() > 0
    expected = workspace_state(panel)
    for hide in ('visible', 'close', 'background'):
        if hide == 'close':
            panel.close()
        elif hide == 'background':
            assert controller.run_in_background()
        controller.show_panel()
        qapp.processEvents()
        assert workspace_state(panel) == expected, hide
    panel.tabs.setCurrentIndex(1)
    expected = workspace_state(panel)
    panel.close()
    controller.show_panel()
    qapp.processEvents()
    assert workspace_state(panel) == expected


@pytest.mark.parametrize('saved_state', ['legacy', 'corrupt', 'future_version'])
def test_legacy_or_unreadable_window_state_uses_saved_normal_geometry(
        controllers, qapp, tmp_path, saved_state):
    service = DesktopService(tmp_path / 'data/anonymous.db')
    area = qapp.primaryScreen().availableGeometry()
    expected = QRect(area.left() + 24, area.top() + 40, 660, 480)
    service.set_setting('manager_geometry', [expected.x(), expected.y(),
                                            expected.width(), expected.height()])
    if saved_state != 'legacy':
        if saved_state == 'future_version':
            native_window = QDialog()
            native_window.setGeometry(expected)
            geometry = bytearray(native_window.saveGeometry())
            # Qt's own geometry stream, with an unsupported future major version.
            geometry[4:6] = b'\xff\xff'
            value = geometry.hex()
            native_window.deleteLater()
        else:
            value = 'damaged geometry bytes'
        # Simulate stored data from an older/future application, bypassing today's
        # input validation without mocking the persistence or Qt restore APIs.
        with service.db.connect() as connection:
            connection.execute('INSERT INTO desktop_settings(key,value) VALUES (?,?)',
                               ('manager_window_state', json.dumps(value)))
    controller = controllers()
    assert not controller.panel.isVisible()
    controller.orb.move(area.topLeft())
    controller.show_panel()
    qapp.processEvents()
    assert controller.panel.isVisible() and not controller.panel.isMaximized()
    assert controller.panel.geometry() == expected
    assert area.contains(controller.panel.frameGeometry())


@pytest.mark.parametrize('value', [None, 1, [], '', '0', 'zz', 'aa bb', 'aa' * 2049],
                         ids=['none', 'number', 'list', 'empty', 'odd_length',
                              'non_hex', 'whitespace', 'oversized'])
def test_malformed_window_state_setting_is_rejected_without_overwriting(tmp_path, value):
    service = DesktopService(tmp_path / 'data/anonymous.db')
    service.set_setting('manager_window_state', 'aabb')
    with pytest.raises(ValueError):
        service.set_setting('manager_window_state', value)
    assert service.get_setting('manager_window_state') == 'aabb'


@pytest.mark.parametrize('source', ['feedback', 'background'])
def test_feedback_and_background_requests_preserve_open_modal_and_workspace(
        controllers, qapp, monkeypatch, source):
    monkeypatch.setattr(QSystemTrayIcon, 'isSystemTrayAvailable', lambda: True)
    controller = controllers(tray_enabled=True)
    expected_geometry = normal_geometry(controller, qapp)
    panel = controller.panel
    panel.search.setText('未完成的匿名筛选')
    panel.urgent_filter.setChecked(True)
    qapp.processEvents()
    expected_state = workspace_state(panel)
    controller.bubble.navigation_target = ('tab', 1)
    dialog = QDialog(panel)
    dialog.setWindowModality(Qt.ApplicationModal)
    draft = QLineEdit('尚未保存的匿名编辑', dialog)
    QVBoxLayout(dialog).addWidget(draft)
    draft.setModified(True)
    dialog.show()
    qapp.processEvents()
    assert QApplication.activeModalWidget() is dialog
    try:
        if source == 'feedback':
            controller.bubble.open_requested.emit()
        else:
            assert controller.run_in_background() is False
        qapp.processEvents()
        assert QApplication.activeModalWidget() is dialog
        assert dialog.isVisible() and draft.text() == '尚未保存的匿名编辑'
        assert draft.isModified()
        assert panel.isVisible() and controller.orb.isVisible()
        assert panel.geometry() == expected_geometry
        assert workspace_state(panel) == expected_state
        assert panel.tabs.currentIndex() == 0, 'Feedback must not navigate behind a modal'
    finally:
        dialog.close()
        dialog.deleteLater()
