import gc
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QDate, QPoint, QSize, Qt
from PySide6.QtGui import QAccessible
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QStyle, QStyleOptionButton, QVBoxLayout, QWidget
from qfluentwidgets import FluentIcon, SettingCard

from compact_ui import DirectorySettingsDialog
from date_edit import DateEdit
from theme import install_theme, resolved_mode, set_theme
from ui_components import SpinBox, button


@pytest.fixture(params=['light', 'dark'])
def window(request):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    install_theme()
    set_theme(request.param, persist=False)
    root = QWidget()
    QVBoxLayout(root)
    yield root
    root.close()
    install_theme()


def test_calendar_opens_across_the_date_control_trailing_target(window):
    date = DateEdit()
    date.setCalendarPopup(True)
    date.setDisplayFormat('yyyy-MM-dd')
    date.setDate(QDate(2026, 11, 3))
    window.layout().addWidget(date)
    window.show()
    QApplication.processEvents()

    QTest.mouseClick(date, Qt.LeftButton, pos=QPoint(date.width() - 22, date.height() // 2))
    calendar = date.calendarWidget()
    try:
        assert calendar.isVisible()
        assert calendar.selectedDate() == date.date()
    finally:
        calendar.window().hide()


def test_number_steppers_expose_named_actions_and_follow_range_changes(window):
    spin = SpinBox()
    spin.setRange(1, 3)
    spin.setValue(2)
    window.layout().addWidget(spin)
    window.show()
    QApplication.processEvents()

    increase = QAccessible.queryAccessibleInterface(spin.upButton)
    decrease = QAccessible.queryAccessibleInterface(spin.downButton)
    assert increase.text(QAccessible.Name) == '增加数值'
    assert decrease.text(QAccessible.Name) == '减少数值'

    spin.setMaximum(2)
    assert spin.value() == 2 and increase.state().disabled
    assert not decrease.state().disabled
    QTest.keyClick(spin, Qt.Key_Down)
    assert spin.value() == 1 and decrease.state().disabled
    assert not increase.state().disabled
    spin.setWrapping(True)
    assert not decrease.state().disabled
    QTest.mouseClick(spin.downButton, Qt.LeftButton, pos=QPoint(24, spin.downButton.height() // 2))
    assert spin.value() == 2


def test_open_calendar_keeps_weekends_legible_after_a_live_theme_switch(window):
    date = DateEdit()
    date.setCalendarPopup(True)
    date.setDate(QDate(2026, 11, 3))
    window.layout().addWidget(date)
    window.show()
    QApplication.processEvents()
    QTest.mouseClick(date, Qt.LeftButton, pos=QPoint(date.width() - 22, date.height() // 2))
    assert date.calendarWidget().isVisible()

    try:
        # The calendar is owned by Qt, even when its Python wrapper is gone.
        gc.collect()
        next_mode = 'dark' if resolved_mode() == 'light' else 'light'
        set_theme(next_mode, persist=False)
        calendar = date.calendarWidget()
        weekend = calendar.weekdayTextFormat(Qt.Saturday).foreground().color()
        assert (weekend.lightness() > 128) == (next_mode == 'dark')
        assert calendar.selectedDate() == QDate(2026, 11, 3)
    finally:
        date.calendarWidget().window().hide()


def test_button_lays_out_the_complete_caption_and_icon_at_keyboard_focus(window):
    elsewhere = QPushButton('其他操作')
    action = button('打开工作目录')
    window.layout().addWidget(elsewhere, 0, Qt.AlignLeft)
    window.layout().addWidget(action, 0, Qt.AlignLeft)
    window.show()
    window.activateWindow()
    elsewhere.setFocus(Qt.TabFocusReason)
    QApplication.processEvents()
    without_icon = action.minimumSizeHint()

    action.setIconSize(QSize(20, 20))
    action.setIcon(FluentIcon.FOLDER)
    QApplication.processEvents()
    with_icon = action.minimumSizeHint()
    assert with_icon.width() >= without_icon.width() + action.iconSize().width()

    action.setFocus(Qt.TabFocusReason)
    QApplication.processEvents()
    assert action.hasFocus()
    assert action.minimumSizeHint() == with_icon
    option = QStyleOptionButton()
    action.initStyleOption(option)
    contents = action.style().subElementRect(QStyle.SE_PushButtonContents, option, action)
    assert not option.icon.isNull()
    assert contents.width() >= action.fontMetrics().horizontalAdvance(action.text()) + option.iconSize.width() + 4


def test_settings_card_descriptions_reflow_in_the_actual_dialog(window):
    settings = dict(work_root='', archive_root='', stable_seconds=10)
    service = SimpleNamespace(workspaces=SimpleNamespace(settings=lambda: settings),
                              get_setting=lambda _key, default=None: default)
    dialog = DirectorySettingsDialog(service, window)
    dialog.show()
    cards = dialog.findChildren(SettingCard)
    assert [card.contentLabel.text() for card in cards] == [
        '包含子目录中的实际文件，不计文件夹本身。',
        '文件保持稳定后再复制归档；原件保留，退出后暂停监视。',
    ]

    def card_heights(width):
        dialog.resize(width, 620)
        QTest.qWait(30)
        assert dialog.width() == width
        for card in cards:
            for text in (card.titleLabel, card.contentLabel):
                assert card.rect().contains(text.geometry())
                assert text.height() >= text.heightForWidth(text.width()), (width, text.text())
        return [card.height() for card in cards]

    try:
        wide = card_heights(900)
        narrow = card_heights(420)
        assert all(narrow_height > wide_height for narrow_height, wide_height in zip(narrow, wide))
        assert card_heights(900) == wide
    finally:
        dialog.close()
