"""基础 Fluent 控件保持原生 Qt 契约，隔离验证且不连接业务数据。"""
import ctypes
import importlib
import importlib.util
import sys

if sys.platform == 'win32':
    ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002)
    ctypes.windll.kernel32.WerSetFlags(0x20)

import pytest
import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QTimer, Qt
from PySide6.QtGui import QContextMenuEvent, QKeySequence
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog,
                              QLineEdit, QMenu, QPushButton, QSpinBox, QVBoxLayout)


def components():
    assert importlib.util.find_spec('ui_components') is not None, '缺少共享 Fluent 控件层'
    return importlib.import_module('ui_components')


def test_shared_fluent_components_module_exists():
    components()


@pytest.fixture(scope='module')
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


@pytest.fixture
def widgets(qapp):
    created = []
    yield created
    for widget in created:
        if shiboken6.isValid(widget):
            widget.close()
            widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    qapp.processEvents()
    assert all(not shiboken6.isValid(widget) for widget in created)


def test_qt_types_and_existing_button_roles(qapp, widgets):
    ui = components()
    for factory, native in [(ui.LineEdit, QLineEdit), (ui.CheckBox, QCheckBox),
                            (ui.SpinBox, QSpinBox), (ui.ComboBox, QComboBox)]:
        control = factory()
        widgets.append(control)
        assert isinstance(control, native)
    from qfluentwidgets import PrimaryPushButton, PushButton
    for argument, expected_name, expected_type in [(False, '', PushButton),
                                                   (True, 'primary', PrimaryPushButton),
                                                   ('primary', 'primary', PrimaryPushButton),
                                                   ('filter', 'filter', PushButton)]:
        control = ui.button('确认收录', argument)
        widgets.append(control)
        assert isinstance(control, expected_type)
        assert isinstance(control, QPushButton)
        assert control.objectName() == expected_name


def test_native_combobox_preserves_data_and_signals(qapp, widgets):
    combo = components().ComboBox()
    widgets.append(combo)
    combo.addItem('全部项目', None)
    combo.addItem('项目版本 02', {'version_id': 27})
    combo.addItem('待交付', 8)
    combo.setMinimumContentsLength(6)
    combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
    index_spy = QSignalSpy(combo.currentIndexChanged)
    text_spy = QSignalSpy(combo.currentTextChanged)
    combo.setCurrentIndex(1)
    assert combo.currentData() == {'version_id': 27}
    assert combo.itemData(2) == 8
    assert combo.findText('待交付') == 2
    assert combo.findData(8) == 2
    assert combo.count() == 3
    assert index_spy.count() == 1 and text_spy.count() == 1
    combo.clear()
    assert combo.count() == 0 and combo.currentData() is None


def test_lineedit_checkbox_spinbox_native_contracts(qapp, widgets):
    ui = components()
    edit, check, spin = ui.LineEdit('旧名称'), ui.CheckBox('只看加急'), ui.SpinBox()
    widgets.extend([edit, check, spin])
    edit.setText('项目名称更新')
    edit.selectAll()
    edit.insert('新名称')
    edit.undo()
    assert edit.text() == '项目名称更新'
    edit.setClearButtonEnabled(True)
    edit.setReadOnly(True)
    assert edit.isReadOnly()
    check.setChecked(True)
    assert check.isChecked()
    spin.setRange(5, 300)
    spin.setValue(299)
    spin.stepUp()
    assert spin.value() == 300
    spin.setValue(400)
    assert spin.value() == 300


@pytest.mark.parametrize('kind', ['line', 'spin'])
def test_editing_context_menu_remains_real_native_qmenu(qapp, widgets, kind):
    ui = components()
    edit = ui.LineEdit('项目资料') if kind == 'line' else ui.SpinBox()
    widgets.append(edit)
    edit.show()
    observed = []

    def close_popup():
        popup = qapp.activePopupWidget()
        observed.append(type(popup))
        if popup is not None:
            popup.close()

    QTimer.singleShot(80, close_popup)
    if kind == 'line':
        event = QContextMenuEvent(QContextMenuEvent.Mouse, QPoint(8, 8), edit.mapToGlobal(QPoint(8, 8)))
        edit.contextMenuEvent(event)
        assert event.isAccepted()
    else:
        edit._showContextMenu(QPoint(8, 8))
    assert observed == [QMenu]


@pytest.mark.parametrize('mode', ['light', 'dark'])
def test_theme_has_no_persistence_and_keeps_control_state(qapp, widgets, monkeypatch, mode):
    ui = components()
    from qfluentwidgets import qconfig
    monkeypatch.setattr(qconfig, 'save', lambda *a, **k: pytest.fail('主题初始化不得写入库配置'))
    edit = ui.LineEdit('保留未保存文字')
    combo = ui.ComboBox()
    combo.addItem('已选版本', 17)
    widgets.extend([edit, combo])
    edit.setSelection(0, 2)
    ui.init_fluent_theme(mode, '#0f766e')
    assert edit.text() == '保留未保存文字' and edit.selectedText() == '保留'
    assert combo.currentData() == 17


@pytest.mark.parametrize('mode', ['light', 'dark'])
def test_button_geometry_default_and_shortcut_are_native(qapp, widgets, mode):
    ui = components()
    ui.init_fluent_theme(mode, '#0f766e')
    dialog = QDialog()
    widgets.append(dialog)
    layout = QVBoxLayout(dialog)
    control = ui.button('确认并保存项目设置', True)
    layout.addWidget(control)
    control.setAutoDefault(True)
    control.setDefault(True)
    control.setShortcut(QKeySequence('Ctrl+Return'))
    spy = QSignalSpy(control.clicked)
    dialog.show()
    dialog.activateWindow()
    control.setFocus()
    QTest.qWait(50)
    sizes = []
    for enabled, checked in [(True, False), (False, False), (True, True)]:
        control.setEnabled(enabled)
        control.setCheckable(True)
        control.setChecked(checked)
        sizes.append(control.sizeHint())
        assert control.sizeHint().width() >= control.fontMetrics().horizontalAdvance(control.text()) + 20
        assert control.sizeHint().height() >= control.fontMetrics().height() + 6
    assert sizes[0] == sizes[1] == sizes[2]
    assert control.isDefault() and control.autoDefault()
    QTest.keyClick(control, Qt.Key_Return, Qt.ControlModifier)
    QTest.qWait(150)
    assert spy.count() == 1


def test_native_button_menu_and_arrow_reserve_space(qapp, widgets):
    ui = components()
    control = ui.button('更多项目操作')
    widgets.append(control)
    without_menu = control.sizeHint().width()
    menu = QMenu(control)
    menu.addAction('工作目录')
    control.setMenu(menu)
    control.show()
    assert control.menu() is menu
    assert control.sizeHint().width() >= without_menu + 8
    assert control.sizeHint().width() >= control.fontMetrics().horizontalAdvance(control.text()) + 30
