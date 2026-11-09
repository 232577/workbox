import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QApplication, QLineEdit, QMenu, QPushButton, QStyle, QStyleOptionComboBox, QVBoxLayout, QWidget,
)

import theme
from ui_components import CheckBox, ComboBox, LineEdit


@pytest.fixture(params=['light', 'dark'])
def appearance(request):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    theme.install_theme()
    theme.set_theme(request.param, persist=False)
    yield app
    theme.set_theme('light', persist=False)


def test_batch_selector_keeps_arrow_and_text_stationary_at_keyboard_focus(appearance):
    window = QWidget()
    layout = QVBoxLayout(window)
    elsewhere = QPushButton('其他操作')
    combo = ComboBox()
    combo.addItem('所有批次')
    layout.addWidget(elsewhere)
    layout.addWidget(combo)
    window.show()
    window.activateWindow()

    def geometry(focus):
        focus.setFocus(Qt.TabFocusReason)
        appearance.processEvents()
        assert focus.hasFocus()
        option = QStyleOptionComboBox()
        combo.initStyleOption(option)
        style = combo.style()
        return (
            style.subControlRect(QStyle.CC_ComboBox, option, QStyle.SC_ComboBoxArrow, combo),
            style.subControlRect(QStyle.CC_ComboBox, option, QStyle.SC_ComboBoxEditField, combo),
        )

    try:
        assert geometry(elsewhere) == geometry(combo)
    finally:
        window.close()


def test_keyboard_selected_menu_action_uses_shared_soft_selection(appearance):
    menu = QMenu()
    action = menu.addAction('打开所在目录')
    menu.addAction('查看收件记录')
    menu.popup(menu.pos())
    menu.setActiveAction(action)
    appearance.processEvents()
    try:
        grab = menu.grab()
        for entry, token in zip(menu.actions(), ['row_selected', 'surface']):
            row = menu.actionGeometry(entry)
            point = row.center()
            point.setX(row.right() - 12)
            assert pixel_color(grab, point) == QColor(theme.tokens()[token])
    finally:
        menu.close()


def test_native_selector_popup_reuses_menu_selection(appearance):
    combo = ComboBox()
    combo.addItems(['所有批次', '最新收件批次', '未记录批次'])
    combo.setCurrentIndex(1)
    combo.resize(185, 32)
    combo.show()
    combo.showPopup()
    appearance.processEvents()
    try:
        view = combo.view()
        assert view.isVisible()
        current = view.visualRect(view.currentIndex())
        point = current.center()
        point.setX(min(current.right(), view.viewport().rect().right()) - 12)
        assert pixel_color(view.viewport().grab(), point) == QColor(theme.tokens()['row_selected'])
    finally:
        combo.hidePopup()
        combo.close()


def test_checked_box_visibly_changes_at_keyboard_focus(appearance):
    window = QWidget()
    layout = QVBoxLayout(window)
    elsewhere = QPushButton('其他操作')
    check = CheckBox('批量选择')
    check.setChecked(True)
    layout.addWidget(elsewhere)
    layout.addWidget(check)
    window.show()
    window.activateWindow()
    try:
        elsewhere.setFocus(Qt.TabFocusReason)
        appearance.processEvents()
        before = check.grab().toImage()
        size = check.sizeHint()
        check.setFocus(Qt.TabFocusReason)
        appearance.processEvents()
        assert check.hasFocus()
        assert check.grab().toImage() != before
        assert check.sizeHint() == size
    finally:
        window.close()


def test_placeholder_keeps_theme_ink_after_control_polish(appearance):
    for control in (LineEdit, QLineEdit):
        field = control()
        field.setPlaceholderText('搜索项目')
        field.ensurePolished()
        assert field.palette().color(QPalette.PlaceholderText) == QColor(theme.tokens()['placeholder'])


def pixel_color(grab, point):
    scale = grab.devicePixelRatio()
    return grab.toImage().pixelColor(round(point.x() * scale), round(point.y() * scale))
