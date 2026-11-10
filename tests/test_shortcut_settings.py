"""快捷键设置独立即时保存，失败不改变已保存偏好或有效注册。"""
from pathlib import Path
from sqlite3 import OperationalError

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QAccessible, QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QVBoxLayout

from shortcut_settings import ShortcutSettings
from ui_components import button


@pytest.fixture(scope='module')
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    import theme
    theme.install_theme()
    return app


class FakeService:
    def __init__(self, saved=''):
        self.saved = saved
        self.writes = []
        self.fail_save = False

    def get_setting(self, key, default=''):
        assert key == 'window_shortcut'
        return self.saved

    def set_setting(self, key, value):
        assert key == 'window_shortcut'
        if self.fail_save:
            raise RuntimeError('设置未能保存')
        self.saved = value
        self.writes.append((key, value))
        return True


class FakeManager:
    def __init__(self, shortcut='', supported=True):
        self.shortcut = shortcut
        self.supported = supported
        self.calls = []
        self.failure = None

    def apply(self, text, persist=None, rollback=None):
        self.calls.append(text)
        if self.failure:
            raise self.failure
        if text and not self.supported:
            raise OSError('当前平台不支持全局快捷键')
        if persist is not None:
            persist(text)
        self.shortcut = text
        return text


class IncompleteRollbackManager(FakeManager):
    def apply(self, text, persist=None, rollback=None):
        persist(text)
        raise OSError('快捷键恢复未全部完成')


def test_default_is_off_without_registration_or_writes(qapp):
    service, manager = FakeService(), FakeManager()
    widget = ShortcutSettings(service, manager)
    assert not widget.enabled_checkbox.isChecked()
    assert not widget.sequence_edit.isEnabled()
    assert widget.sequence_edit.keySequence().toString(QKeySequence.PortableText) == 'Ctrl+Alt+W'
    assert widget.sequence_edit.maximumSequenceLength() == 1
    assert manager.calls == service.writes == []
    assert '已关闭' in widget.status_label.text()
    widget.enabled_checkbox.setChecked(True)
    assert widget.sequence_edit.isEnabled()
    assert manager.calls == service.writes == []


def test_apply_success_is_immediate_and_independent_of_parent_cancel(qapp):
    service, manager = FakeService(), FakeManager()
    dialog = QDialog()
    layout = QVBoxLayout(dialog)
    widget = ShortcutSettings(service, manager, dialog)
    layout.addWidget(widget)
    widget.enabled_checkbox.setChecked(True)
    widget.apply_button.click()
    dialog.reject()
    assert manager.shortcut == service.saved == 'Ctrl+Alt+W'
    assert service.writes == [('window_shortcut', 'Ctrl+Alt+W')]
    assert '已生效' in widget.status_label.text()
    assert 'Ctrl+Alt+W' in widget.status_label.text()
    assert not widget.error.text()


@pytest.mark.parametrize('failure', [ValueError('组合不符合要求'), OSError('快捷键已被占用')])
def test_failed_apply_preserves_effective_and_saved_shortcut(qapp, failure):
    service, manager = FakeService('Ctrl+Alt+W'), FakeManager('Ctrl+Alt+W')
    manager.failure = failure
    widget = ShortcutSettings(service, manager)
    widget.sequence_edit.setKeySequence(QKeySequence('Ctrl+Shift+J'))
    widget.apply_button.click()
    assert manager.shortcut == service.saved == 'Ctrl+Alt+W'
    assert service.writes == []
    assert str(failure) in widget.error.text()
    assert '重试' in widget.error.text()
    assert 'Ctrl+Alt+W' in widget.status_label.text()
    manager.failure = None
    widget.apply_button.click()
    assert manager.shortcut == service.saved == 'Ctrl+Shift+J'
    assert widget.error.text() == ''


def test_save_failure_preserves_registration_and_is_actionable(qapp):
    service, manager = FakeService('Ctrl+Alt+W'), FakeManager('Ctrl+Alt+W')
    service.fail_save = True
    widget = ShortcutSettings(service, manager)
    widget.sequence_edit.setKeySequence(QKeySequence('Ctrl+Shift+J'))
    widget.apply_button.click()
    assert manager.shortcut == service.saved == 'Ctrl+Alt+W'
    assert service.writes == []
    assert '设置未能保存' in widget.error.text()


def test_incomplete_rollback_shows_actual_saved_and_effective_values(qapp):
    service = FakeService('Ctrl+Alt+W')
    manager = IncompleteRollbackManager('Ctrl+Alt+W')
    widget = ShortcutSettings(service, manager)
    widget.sequence_edit.setKeySequence(QKeySequence('Ctrl+Shift+J'))
    widget.apply_button.click()
    assert service.saved == 'Ctrl+Shift+J'
    assert manager.shortcut == 'Ctrl+Alt+W'
    assert '当前生效：Ctrl+Alt+W' in widget.status_label.text()
    assert '已保存：Ctrl+Shift+J' in widget.status_label.text()
    assert '不一致' in widget.status_label.text()
    assert '重新应用' in widget.status_label.text()
    assert '原设置保留' not in widget.error.text()
    assert widget.enabled_checkbox.isChecked()
    assert widget.sequence_edit.keySequence().toString(QKeySequence.PortableText) == 'Ctrl+Shift+J'


def test_failed_verification_of_saved_state_is_explicit(qapp):
    class UnreadableAfterSave(FakeService):
        def get_setting(self, key, default=''):
            if self.writes:
                raise OperationalError('无法读取设置')
            return super().get_setting(key, default)

    service = UnreadableAfterSave('Ctrl+Alt+W')
    manager = IncompleteRollbackManager('Ctrl+Alt+W')
    widget = ShortcutSettings(service, manager)
    widget.sequence_edit.setKeySequence(QKeySequence('Ctrl+Shift+J'))
    widget.apply_button.click()
    assert '当前生效：Ctrl+Alt+W' in widget.status_label.text()
    assert '无法核对已保存' in widget.status_label.text()
    assert '原设置保留' not in widget.error.text()
    assert widget.sequence_edit.keySequence().toString(QKeySequence.PortableText) == 'Ctrl+Shift+J'


def test_rollback_restores_stored_intention_instead_of_empty_effective_key(qapp):
    class FailedNativeChange(FakeManager):
        def apply(self, text, persist=None, rollback=None):
            persist(text)
            assert callable(rollback)
            rollback()
            raise OSError('无法释放原快捷键')

    service = FakeService('Ctrl+Alt+W')
    manager = FailedNativeChange()
    widget = ShortcutSettings(service, manager)
    widget.sequence_edit.setKeySequence(QKeySequence('Ctrl+Shift+J'))
    widget.apply_button.click()
    assert service.saved == 'Ctrl+Alt+W'
    assert manager.shortcut == ''
    assert service.writes == [('window_shortcut', 'Ctrl+Shift+J'), ('window_shortcut', 'Ctrl+Alt+W')]
    assert '未生效' in widget.status_label.text()
    assert '已保存的组合：Ctrl+Alt+W' in widget.status_label.text()


def test_cannot_read_previous_preference_stops_before_native_apply(qapp):
    class UnreadableService(FakeService):
        fail_read = False

        def get_setting(self, key, default=''):
            if self.fail_read:
                raise OperationalError('无法读取原快捷键设置')
            return super().get_setting(key, default)

    service = UnreadableService('Ctrl+Alt+W')
    manager = FakeManager('Ctrl+Alt+W')
    widget = ShortcutSettings(service, manager)
    widget.sequence_edit.setKeySequence(QKeySequence('Ctrl+Shift+J'))
    service.fail_read = True
    widget.apply_button.click()
    assert manager.calls == service.writes == []
    assert '无法读取原快捷键设置' in widget.error.text()
    assert '无法核对已保存' in widget.status_label.text()
    assert manager.shortcut == service.saved == 'Ctrl+Alt+W'


def test_cleared_recording_cannot_silently_disable_an_enabled_shortcut(qapp):
    service, manager = FakeService('Ctrl+Alt+W'), FakeManager('Ctrl+Alt+W')
    widget = ShortcutSettings(service, manager)
    widget.sequence_edit.clear()
    widget.apply_button.click()
    assert manager.shortcut == service.saved == 'Ctrl+Alt+W'
    assert manager.calls == service.writes == []
    assert '请先按下快捷键组合' in widget.error.text()


@pytest.mark.parametrize('sequence', ['Ctrl+W', 'Ctrl+Alt+F12', 'Meta+Ctrl+W', 'Ctrl+Alt+PgUp'])
def test_invalid_recording_stops_before_registration_or_persistence(qapp, sequence):
    service, manager = FakeService('Ctrl+Alt+W'), FakeManager('Ctrl+Alt+W')
    widget = ShortcutSettings(service, manager)
    widget.sequence_edit.setKeySequence(QKeySequence(sequence))
    widget.apply_button.click()
    assert manager.calls == service.writes == []
    assert manager.shortcut == service.saved == 'Ctrl+Alt+W'
    assert '未应用' in widget.error.text()
    assert '重试' in widget.error.text()


def test_disable_requires_apply_and_clears_saved_and_effective(qapp):
    service, manager = FakeService('Ctrl+Alt+W'), FakeManager('Ctrl+Alt+W')
    widget = ShortcutSettings(service, manager)
    widget.enabled_checkbox.setChecked(False)
    assert manager.shortcut == service.saved == 'Ctrl+Alt+W'
    widget.apply_button.click()
    assert manager.shortcut == service.saved == ''
    assert service.writes == [('window_shortcut', '')]
    assert '已关闭' in widget.status_label.text()


def test_saved_startup_conflict_does_not_claim_it_is_effective(qapp):
    widget = ShortcutSettings(FakeService('Ctrl+Alt+W'), FakeManager())
    assert widget.enabled_checkbox.isChecked()
    assert '未生效' in widget.status_label.text()
    assert 'Ctrl+Alt+W' in widget.status_label.text()
    assert '应用' in widget.status_label.text()


def test_missing_manager_cannot_activate_or_write(qapp):
    service = FakeService('Ctrl+Alt+W')
    widget = ShortcutSettings(service)
    assert not widget.enabled_checkbox.isEnabled()
    assert not widget.sequence_edit.isEnabled()
    assert not widget.apply_button.isEnabled()
    assert '未生效' in widget.status_label.text()
    widget.apply_shortcut()
    assert service.writes == []


def test_unsupported_manager_can_clear_saved_preference_only(qapp):
    service, manager = FakeService('Ctrl+Alt+W'), FakeManager(supported=False)
    widget = ShortcutSettings(service, manager)
    assert not widget.sequence_edit.isEnabled()
    assert not widget.apply_button.isEnabled()
    assert '不支持' in widget.status_label.text()
    assert '未生效' in widget.status_label.text()
    widget.enabled_checkbox.setChecked(False)
    assert widget.apply_button.isEnabled()
    widget.apply_button.click()
    assert manager.shortcut == service.saved == ''
    assert not widget.enabled_checkbox.isEnabled()
    assert not widget.apply_button.isEnabled()


def test_unsupported_without_preference_cannot_enable_or_apply(qapp):
    service, manager = FakeService(), FakeManager(supported=False)
    widget = ShortcutSettings(service, manager)
    assert not widget.enabled_checkbox.isChecked()
    assert not widget.enabled_checkbox.isEnabled()
    assert not widget.sequence_edit.isEnabled()
    assert not widget.apply_button.isEnabled()
    assert manager.calls == service.writes == []
    assert '不支持' in widget.status_label.text()
    assert '已关闭' in widget.status_label.text()


def test_accessibility_search_copy_and_keyboard_do_not_submit_parent(qapp):
    dialog = QDialog()
    layout = QVBoxLayout(dialog)
    widget = ShortcutSettings(FakeService(), FakeManager(), dialog)
    layout.addWidget(widget)
    save = button('保存设置')
    save.setDefault(True)
    layout.addWidget(save)
    saved = []
    save.clicked.connect(lambda: saved.append(True))
    dialog.show()
    widget.enabled_checkbox.setChecked(True)
    widget.sequence_edit.setFocus()
    qapp.processEvents()
    QTest.keyClick(widget.sequence_edit, Qt.Key_J, Qt.ControlModifier | Qt.ShiftModifier)
    QTest.keyClick(widget.sequence_edit, Qt.Key_Return)
    assert not saved
    assert widget.sequence_edit.keySequence().toString(QKeySequence.PortableText) == 'Ctrl+Shift+J'
    assert QAccessible.queryAccessibleInterface(widget.enabled_checkbox).role() == QAccessible.CheckBox
    assert '快捷键' in widget.sequence_edit.accessibleName()
    assert '应用' in widget.apply_button.accessibleName()
    text = ' '.join([widget.enabled_checkbox.text(),
                     *(label.text() for label in widget.findChildren(QLabel))])
    for keyword in ('Ctrl', 'Alt', 'Shift', 'F1', 'F11', 'F12', '显示', '隐藏', '立即保存', '取消'):
        assert keyword in text


@pytest.mark.parametrize('mode', ['light', 'dark'])
@pytest.mark.parametrize('width', [480, 680])
def test_real_widget_layout_and_theme_captures(qapp, mode, width):
    import theme
    from qfluentwidgets import CardWidget
    theme.set_theme(mode, persist=False)
    dialog = QDialog()
    outer = QVBoxLayout(dialog)
    outer.setContentsMargins(16, 16, 16, 16)
    card = CardWidget(dialog)
    body = QVBoxLayout(card)
    body.setContentsMargins(16, 14, 16, 14)
    widget = ShortcutSettings(FakeService('Ctrl+Alt+W'), FakeManager(), card)
    body.addWidget(widget)
    outer.addWidget(card)
    theme.apply_to(dialog)
    dialog.resize(width, dialog.sizeHint().height())
    dialog.show()
    qapp.processEvents()
    assert dialog.width() == width
    controls = (widget.enabled_checkbox, widget.sequence_edit, widget.apply_button,
                widget.status_label, *widget.findChildren(QLabel))
    for control in controls:
        if not control.isVisible():
            continue
        top = control.mapTo(widget, QPoint(0, 0))
        assert widget.rect().contains(top)
        assert top.x() + control.width() <= widget.width()
        assert top.y() + control.height() <= widget.height()
    assert widget.sequence_edit.height() >= 32
    assert widget.apply_button.height() >= 30
    captures = Path('.local/window-shortcut-20261110')
    captures.mkdir(parents=True, exist_ok=True)
    capture = dialog.grab()
    assert capture.width() == round(dialog.width() * dialog.devicePixelRatioF())
    dpi = round(dialog.devicePixelRatioF() * 100)
    suffix = f'-dpi{dpi}' if dpi != 100 else ''
    assert capture.save(str(captures / f'ui-{mode}-{width}{suffix}.png'))
    theme.set_theme('light', persist=False)
