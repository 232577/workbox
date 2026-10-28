"""主题切换只改变显示，不重建列表或改写业务设置。"""
import importlib
import importlib.util

import pytest
import shiboken6
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QDialog, QListWidget, QMenu, QScrollBar


def theme_module():
    assert importlib.util.find_spec('theme') is not None, '缺少统一主题模块'
    return importlib.import_module('theme')


def test_unified_theme_module_is_available():
    theme_module()


@pytest.fixture(scope='module')
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


@pytest.fixture
def theme(qapp):
    module = theme_module()
    module.install_theme()
    yield module
    module.install_theme()


class Settings:
    def __init__(self, mode='light'):
        self.values = {'appearance_theme': mode, 'automatic_zip_delivery': False,
                       'stable_wait_seconds': 17, 'orb_position': [20, 40]}
        self.writes = []
        self.fail = False

    def get_setting(self, key, default=None):
        return self.values.get(key, default)

    def set_setting(self, key, value):
        if self.fail:
            raise OSError('隔离设置保存失败')
        self.writes.append((key, value))
        self.values[key] = value


def contrast(first, second):
    def luminance(value):
        color = QColor(value)
        channels = [color.redF(), color.greenF(), color.blueF()]
        channels = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in channels]
        return sum(c * w for c, w in zip(channels, [.2126, .7152, .0722]))
    low, high = sorted([luminance(first), luminance(second)])
    return (high + .05) / (low + .05)


@pytest.mark.parametrize('mode', ['light', 'dark'])
def test_text_and_semantic_colors_have_readable_contrast(theme, mode):
    colors = theme.tokens(mode)
    for foreground, background in [('ink', 'bg'), ('ink', 'surface'), ('muted', 'surface'),
                                   ('accent_text', 'accent'), ('selection_text', 'selection')]:
        assert contrast(colors[foreground], colors[background]) >= 4.5, (mode, foreground)
    for state in ['state_new', 'state_pending', 'state_working', 'state_delivered']:
        assert contrast(colors[state], colors['surface']) >= 4.5, (mode, state)
    assert colors['surface'] in theme.stylesheet(mode)


@pytest.mark.parametrize('mode', ['light', 'dark'])
def test_secondary_text_and_input_boundaries_preserve_strong_contrast(theme, mode):
    colors = theme.tokens(mode)
    assert contrast(colors['muted'], colors['surface']) >= 7
    assert contrast(colors['muted'], colors['bg']) >= 7
    assert contrast(colors['selection'], colors['surface']) >= 3
    assert contrast(colors['control_border'], colors['surface']) >= 3


def test_install_reads_saved_theme_without_writing_and_keeps_other_settings(theme, qapp):
    store = Settings('dark')
    before = dict(store.values)
    theme.install_theme(store)
    assert theme.current_mode() == theme.resolved_mode() == 'dark'
    assert store.values == before and store.writes == []
    assert qapp.palette().color(QPalette.Window).name().upper() == '#181C24'
    assert qapp.font().family() == 'Microsoft YaHei UI'
    assert qapp.font().pointSize() == 10


@pytest.mark.parametrize('invalid', ['bad', None, 7, ['dark']])
def test_invalid_stored_value_falls_back_without_overwriting(theme, invalid):
    store = Settings(invalid)
    theme.install_theme(store)
    assert theme.current_mode() == theme.resolved_mode() == 'light'
    assert store.values['appearance_theme'] == invalid and store.writes == []


def test_save_failure_keeps_previous_mode_palette_and_widget(theme, qapp):
    store = Settings()
    theme.install_theme(store)
    dialog = QDialog()
    theme.apply_to(dialog)
    previous = (dialog.styleSheet(), qapp.styleSheet(), qapp.palette().color(QPalette.Window))
    events = []
    theme.theme_signal().connect(events.append)
    try:
        store.fail = True
        with pytest.raises(OSError, match='保存失败'):
            theme.set_theme('dark')
        assert theme.current_mode() == theme.resolved_mode() == 'light'
        assert previous == (dialog.styleSheet(), qapp.styleSheet(), qapp.palette().color(QPalette.Window))
        assert events == [] and store.writes == []
    finally:
        theme.theme_signal().disconnect(events.append)
        dialog.close()


def test_set_theme_persists_only_preference_and_rejects_invalid_values(theme):
    store = Settings()
    theme.install_theme(store)
    before = dict(store.values)
    assert theme.set_theme('dark') is True
    assert store.values == {**before, 'appearance_theme': 'dark'}
    assert store.writes == [('appearance_theme', 'dark')]
    with pytest.raises(ValueError):
        theme.set_theme('sepia')
    assert store.writes == [('appearance_theme', 'dark')]


def test_no_service_and_nonpersisting_changes_do_not_write(theme):
    assert theme.set_theme('dark') is True
    assert theme.current_mode() == 'dark'
    store = Settings()
    theme.install_theme(store)
    theme.set_theme('dark', persist=False)
    assert store.writes == [] and store.values['appearance_theme'] == 'light'


def test_live_hidden_widgets_scrollbars_and_selection_survive_switch(theme, qapp):
    dialog = QDialog()
    listing = QListWidget(dialog)
    listing.addItems([str(i) for i in range(100)])
    listing.setCurrentRow(15)
    bar = QScrollBar(Qt.Vertical)
    bar.setRange(0, 100)
    bar.setValue(36)
    theme.apply_to(dialog)
    theme.apply_scrollbar(bar)
    try:
        theme.set_theme('dark')
        assert not dialog.isVisible()
        assert dialog.palette().color(QPalette.Window) == QColor(theme.tokens()['bg'])
        assert dialog.styleSheet() == '', '窗口应继承统一主题，不能保留旧的局部皮肤'
        assert listing.currentRow() == 15 and listing.count() == 100
        assert bar.value() == 36
        colors = theme.tokens()
        for role, token in [(QPalette.Button, 'scroll_handle'), (QPalette.ButtonText, 'ink'),
                            (QPalette.Dark, 'scroll_hover'), (QPalette.Shadow, 'border'),
                            (QPalette.Window, 'bg')]:
            assert bar.palette().color(role) == QColor(colors[token])
        # 无父级新建弹窗和菜单继承应用主题。
        orphan = QDialog()
        menu = QMenu()
        assert orphan.palette().color(QPalette.Window) == QColor(colors['bg'])
        assert menu.palette().color(QPalette.WindowText) == QColor(colors['ink'])
        orphan.close()
        menu.close()
    finally:
        dialog.close()
        bar.close()


def test_destroyed_widgets_are_ignored(theme):
    dialog = QDialog()
    bar = QScrollBar()
    theme.apply_to(dialog)
    theme.apply_scrollbar(bar)
    shiboken6.delete(dialog)
    shiboken6.delete(bar)
    theme.set_theme('dark')
    theme.set_theme('light')


def test_system_color_changes_only_follow_in_system_mode(theme, qapp):
    hints = qapp.styleHints()
    events = []
    theme.manager().changed.connect(events.append)
    try:
        hints.colorSchemeChanged.emit(Qt.ColorScheme.Dark)
        assert theme.resolved_mode() == 'light'
        theme.set_theme('system', persist=False)
        assert theme.current_mode() == 'system' and theme.resolved_mode() == 'dark'
        hints.colorSchemeChanged.emit(Qt.ColorScheme.Light)
        assert theme.current_mode() == 'system' and theme.resolved_mode() == 'light'
        theme.set_theme('dark', persist=False)
        hints.colorSchemeChanged.emit(Qt.ColorScheme.Light)
        assert theme.current_mode() == theme.resolved_mode() == 'dark'
        assert events == ['dark', 'light', 'dark']
    finally:
        theme.manager().changed.disconnect(events.append)
