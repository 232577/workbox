"""Opt-in hotkeys reuse the existing single-window and safe-shutdown paths."""
import pytest
from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QVBoxLayout

from desktop_controller import FloatingController
from desktop_service import DesktopService


class Shortcut(QObject):
    activated = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.shortcut = ''
        self.supported = True
        self.applications = []
        self.stopped = False
        self.conflict = False

    def apply(self, text, persist=None, rollback=None):
        self.applications.append(text)
        if self.conflict:
            raise ValueError('组合键已被占用，请更换组合键。')
        if persist:
            persist(text)
        self.shortcut = text
        return text

    def stop(self):
        self.shortcut = ''
        self.stopped = True


@pytest.fixture(scope='module')
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


@pytest.fixture
def controller(qapp, tmp_path, monkeypatch):
    import desktop_controller
    monkeypatch.setattr(desktop_controller, 'GlobalShortcut', Shortcut)
    value = FloatingController(DesktopService(tmp_path / 'anonymous.db'), tray_enabled=False)
    assert value.start()
    qapp.processEvents()
    try:
        yield value
    finally:
        value.shutdown()
        value.dispose_ui()


def test_default_does_not_register_or_change_preferences(controller):
    assert controller.shortcut.applications == []
    assert controller.service.get_setting('window_shortcut') is None
    assert controller.panel.window_shortcut is controller.shortcut


def test_shortcut_wakes_background_and_only_hides_active_main_window(controller, qapp, monkeypatch):
    panel = controller.panel
    controller.shortcut.activated.emit()
    qapp.processEvents()
    assert panel.isVisible()
    panel.search.setText('匿名筛选')
    monkeypatch.setattr(panel, 'isActiveWindow', lambda: True)
    controller.shortcut.activated.emit()
    qapp.processEvents()
    assert not panel.isVisible() and controller.orb.isVisible()
    assert not controller.background_mode and controller.monitor_timer.isActive()
    controller.shortcut.activated.emit()
    qapp.processEvents()
    assert panel.isVisible() and panel.search.text() == '匿名筛选'
    monkeypatch.setattr(panel, 'isActiveWindow', lambda: False)
    controller.shortcut.activated.emit()
    assert panel.isVisible(), 'A shortcut from another application must wake, not hide'


def test_shortcut_restores_minimized_maximized_window(controller, qapp):
    controller.show_panel()
    controller.panel.showMaximized()
    qapp.processEvents()
    controller.panel.showMinimized()
    qapp.processEvents()
    controller.shortcut.activated.emit()
    qapp.processEvents()
    assert controller.panel.isMaximized() and not controller.panel.isMinimized()


def test_shortcut_keeps_unsaved_modal_and_workspace(controller, qapp, monkeypatch):
    controller.show_panel()
    panel = controller.panel
    panel.search.setText('匿名查询')
    dialog = QDialog(panel)
    dialog.setWindowModality(Qt.ApplicationModal)
    draft = QLineEdit('未保存内容', dialog)
    QVBoxLayout(dialog).addWidget(draft)
    dialog.show()
    qapp.processEvents()
    raised = []
    monkeypatch.setattr(dialog, 'raise_', lambda: raised.append(True))
    try:
        controller.shortcut.activated.emit()
        qapp.processEvents()
        assert raised and QApplication.activeModalWidget() is dialog
        assert dialog.isVisible() and panel.isVisible()
        assert draft.text() == '未保存内容' and panel.search.text() == '匿名查询'
    finally:
        dialog.close()
        dialog.deleteLater()


def test_shutdown_releases_shortcut_and_late_signal_cannot_reopen(controller, qapp):
    controller.show_panel()
    qapp.processEvents()
    controller.shutdown()
    assert controller.shortcut.stopped and not controller.owns_lock
    controller.shortcut.activated.emit()
    qapp.processEvents()
    assert not controller.panel.isVisible() and not controller.orb.isVisible()


@pytest.mark.parametrize('deferred', [False, True])
def test_failed_shortcut_release_keeps_workbench_usable_and_exit_can_retry(controller, monkeypatch, deferred):
    stop = controller.shortcut.stop
    attempts = []
    def fail_once():
        attempts.append(True)
        if len(attempts) == 1:
            raise OSError('测试：快捷键暂时无法释放')
        stop()
    monkeypatch.setattr(controller.shortcut, 'stop', fail_once)
    controller.show_panel()
    if deferred:
        controller.quitting = True
        controller.monitor_timer.stop()
        controller._next()
    else:
        controller.request_quit()
    assert not controller.quitting and not controller.closed
    assert controller.owns_lock and controller.monitor_timer.isActive()
    assert controller.show_panel() and controller.orb.isVisible()
    assert '退出未完成' in controller.panel.notice.text()
    controller.request_quit()
    assert controller.closed and not controller.owns_lock


def test_settings_entry_applies_independently_and_search_locates_shortcut(controller, qapp):
    from compact_ui import DirectorySettingsDialog
    dialog = DirectorySettingsDialog(controller.service, controller.panel)
    try:
        original = controller.service.workspaces.settings()
        dialog.settings_search.setText('快捷键')
        visible_sections = [section.titleLabel.text() for section, _ in dialog._setting_groups if not section.isHidden()]
        assert visible_sections == ['窗口与快捷键']
        controls = dialog.shortcut_settings
        controls.enabled_checkbox.setChecked(True)
        controls.apply_button.click()
        assert controller.shortcut.shortcut == 'Ctrl+Alt+W'
        assert controller.service.get_setting('window_shortcut') == 'Ctrl+Alt+W'
        dialog.reject()
        assert controller.service.get_setting('window_shortcut') == 'Ctrl+Alt+W'
        assert controller.service.workspaces.settings() == original
        with pytest.raises(ValueError):
            controller.service.set_setting('window_shortcut', 'Ctrl+W')
        assert controller.service.get_setting('window_shortcut') == 'Ctrl+Alt+W'
    finally:
        dialog.reject()
        dialog.deleteLater()
        qapp.processEvents()


def test_settings_inline_shortcut_error_grows_card_without_clipping(controller, qapp):
    from compact_ui import DirectorySettingsDialog
    dialog = DirectorySettingsDialog(controller.service, controller.panel)
    try:
        dialog.resize(640, 540)
        dialog.settings_search.setText('快捷键')
        dialog.show()
        for _ in range(4):
            qapp.processEvents()
        controls = dialog.shortcut_settings
        card = controls.parentWidget()
        previous_height = card.height()
        controller.shortcut.conflict = True
        controls.enabled_checkbox.setChecked(True)
        controls.apply_button.click()
        for _ in range(8):
            qapp.processEvents()
        error = controls.error
        assert error.isVisible() and '占用' in error.text()
        assert error.height() >= error.heightForWidth(error.width())
        assert card.height() > previous_height
        assert controls.rect().contains(error.geometry())
        assert card.rect().contains(error.mapTo(card, error.rect().bottomRight()))
        assert dialog.rect().contains(dialog.save_button.mapTo(dialog, dialog.save_button.rect().bottomRight()))
        failure_height = card.height()
        controller.shortcut.conflict = False
        controls.apply_button.click()
        for _ in range(8):
            qapp.processEvents()
        assert error.isHidden() and card.height() < failure_height
        assert controller.shortcut.shortcut == 'Ctrl+Alt+W'
    finally:
        dialog.reject()
        dialog.deleteLater()
        qapp.processEvents()


@pytest.mark.parametrize('conflict', [False, True])
def test_saved_shortcut_startup_and_second_instance_do_not_steal_registration(
        qapp, tmp_path, monkeypatch, conflict):
    import desktop_controller
    monkeypatch.setattr(desktop_controller, 'GlobalShortcut', Shortcut)
    service = DesktopService(tmp_path / 'anonymous.db')
    service.set_setting('window_shortcut', 'Ctrl+Alt+W')
    first = FloatingController(service, tray_enabled=False)
    first.shortcut.conflict = conflict
    second = FloatingController(service, tray_enabled=False)
    try:
        assert first.start()
        assert first.shortcut.applications == ['Ctrl+Alt+W']
        assert service.get_setting('window_shortcut') == 'Ctrl+Alt+W'
        assert first.orb.isVisible()
        if conflict:
            assert first.shortcut.shortcut == ''
            assert '快捷键' in first.panel.notice.text() and '占用' in first.panel.notice.text()
        else:
            assert first.shortcut.shortcut == 'Ctrl+Alt+W'
        assert second.start() is False
        assert second.shortcut.applications == []
    finally:
        second.shutdown()
        second.dispose_ui()
        first.shutdown()
        first.dispose_ui()
