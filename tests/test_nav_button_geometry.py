"""导航按钮真实字体和图标的绘制区域不应相互遮挡。"""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QAccessible, QAccessibleActionInterface, QColor, QIcon, QPixmap
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QMenu, QPushButton
from qfluentwidgets import FluentIcon, isDarkTheme

import ui_components as ui
from theme import install_theme, set_theme


@pytest.fixture(scope='module')
def app():
    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    return application


def colored_bounds(image, predicate, max_y=None):
    points = [(x, y) for y in range(image.height()) for x in range(image.width())
              if (max_y is None or y < max_y) and predicate(image.pixelColor(x, y))]
    assert points
    return (min(x for x, _ in points), min(y for _, y in points),
            max(x for x, _ in points), max(y for _, y in points))


def use_theme(mode):
    install_theme()
    set_theme(mode, persist=False)


def color_probe(control):
    # 仅覆盖测试用颜色，保留真实主题的边框、间距与对齐规则。
    background = '#222222' if isDarkTheme() else 'white'
    control.setStyleSheet(control.styleSheet() + f'\nQPushButton#nav {{ color: #00ff00; background: {background}; }}')


@pytest.mark.parametrize('mode', ['light', 'dark', 'system'])
def test_nav_icon_and_text_remain_separate_and_center_when_collapsed(app, mode, tmp_path):
    use_theme(mode)
    control = ui.button('外观', 'nav')
    pixmap = QPixmap(16, 16)
    pixmap.fill(QColor('red'))
    control.setIcon(QIcon(pixmap))
    color_probe(control)
    control.setFixedSize(140, 36)
    control.show()
    app.processEvents()
    for narrow in (False, True, False, True):
        control.setProperty('compactNav', narrow)
        control.setText('' if narrow else '外观')
        control.setFixedWidth(42 if narrow else 140)
        control.style().unpolish(control)
        control.style().polish(control)
        app.processEvents()
        image = control.grab().toImage()
        image.save(str(tmp_path / f'{mode}-{narrow}.png'))
        icon = colored_bounds(image, lambda c: c.red() > 240 and c.green() < 30 and c.blue() < 30)
        if narrow:
            assert abs((icon[0] + icon[2]) / 2 - (image.width() - 1) / 2) <= 2 * image.devicePixelRatio()
        else:
            text = colored_bounds(image, lambda c: c.green() > 150 and c.red() < 100 and c.blue() < 100,
                                  image.height() * 0.70)
            assert icon[2] < text[0], (icon, text)
    control.close()


def test_nav_menu_is_native_and_clickable(app):
    control = ui.button('外观', 'nav')
    menu = QMenu(control)
    menu.addAction('浅色')
    control.setMenu(menu)
    assert control.menu() is menu
    assert control.isCheckable()
    control.setCheckable(False)
    assert not control.isCheckable()
    control.close()


def test_fluent_icon_refreshes_with_theme_and_compact_text(app):
    use_theme('light')
    control = ui.button('今日工作量', 'nav')
    control.setIcon(FluentIcon.CALENDAR)
    original_icon = control.icon().cacheKey()
    set_theme('dark', persist=False)
    assert control.icon().cacheKey() != original_icon
    control.setProperty('compactNav', True)
    control.setText('99+')
    assert control.text() == '99+'
    control.setText('')
    assert control.text() == ''
    control.close()


def icon_color(control):
    image = control.icon().pixmap(32, 32).toImage()
    pixels = [image.pixelColor(x, y) for y in range(image.height()) for x in range(image.width())
              if image.pixelColor(x, y).alpha() > 200]
    assert pixels
    return pixels[0]


@pytest.mark.parametrize('mode', ['light', 'dark', 'system'])
def test_selected_fluent_icon_is_white_and_restores_theme_color(app, mode):
    use_theme(mode)
    control = ui.button('项目', 'nav')
    control.setIcon(FluentIcon.FOLDER)
    ordinary = icon_color(control)
    control.setChecked(True)
    selected = icon_color(control)
    assert min(selected.red(), selected.green(), selected.blue()) > 245
    for next_mode in ('dark', 'light'):
        set_theme(next_mode, persist=False)
        selected = icon_color(control)
        assert min(selected.red(), selected.green(), selected.blue()) > 245
    set_theme(mode, persist=False)
    control.setChecked(False)
    restored = icon_color(control)
    assert restored == ordinary
    control.close()


@pytest.mark.parametrize('mode', ['light', 'dark', 'system'])
def test_compact_count_has_space_for_icon_and_text(app, mode):
    use_theme(mode)
    control = ui.button('99+', 'nav')
    pixmap = QPixmap(16, 16)
    pixmap.fill(QColor('red'))
    control.setIcon(QIcon(pixmap))
    color_probe(control)
    control.setProperty('compactNav', True)
    control.setFixedSize(56, 36)
    control.show()
    app.processEvents()
    image = control.grab().toImage()
    icon = colored_bounds(image, lambda c: c.red() > 240 and c.green() < 30 and c.blue() < 30)
    text = colored_bounds(image, lambda c: c.green() > 150 and c.red() < 100 and c.blue() < 100)
    assert icon[2] < text[0] and text[2] < image.width() - 1
    control.close()


@pytest.mark.parametrize('mode', ['light', 'dark', 'system'])
def test_menu_arrow_has_own_region_and_navigation_click_contract(app, mode, tmp_path):
    use_theme(mode)
    control = ui.button('外观', 'nav')
    pixmap = QPixmap(16, 16)
    pixmap.fill(QColor('red'))
    control.setIcon(QIcon(pixmap))
    color_probe(control)
    control.setAccessibleName('外观主题')
    menu = QMenu(control)
    menu.addAction('浅色')
    control.setMenu(menu)
    control.setFixedSize(140, 36)
    control.show()
    app.processEvents()
    for narrow in (False, True, False):
        control.setProperty('compactNav', narrow)
        control.setText('' if narrow else '外观')
        control.setFixedWidth(42 if narrow else 140)
        control.style().unpolish(control)
        control.style().polish(control)
        app.processEvents()
        image = control.grab().toImage()
        image.save(str(tmp_path / f'menu-{mode}-{narrow}.png'))
        icon = colored_bounds(image, lambda c: c.red() > 240 and c.green() < 30 and c.blue() < 30)
        ratio = image.devicePixelRatio()
        assert icon[0] >= 0 and icon[2] < image.width()
        arrow_pixels = [(x, y) for y in range(int(5 * ratio), int(image.height() - 5 * ratio))
                        for x in range(int(image.width() - 20 * ratio), int(image.width() - 3 * ratio))
                        if (image.pixelColor(x, y).lightness() > 180 if isDarkTheme()
                            else image.pixelColor(x, y).lightness() < 100)]
        assert arrow_pixels
        arrow_center_y = (min(y for _, y in arrow_pixels) + max(y for _, y in arrow_pixels)) / 2
        assert abs(arrow_center_y - (image.height() - 1) / 2) <= 2 * ratio
        assert all(not (icon[0] <= x <= icon[2] and icon[1] <= y <= icon[3])
                   for x, y in arrow_pixels)
        if not narrow:
            # Qt 的菜单三角也使用文字颜色，位于按钮右下角。
            image_without_arrow = image.copy(0, 0, int(image.width() - 20 * ratio), image.height())
            text = colored_bounds(image_without_arrow, lambda c: c.green() > 150 and c.red() < 100 and c.blue() < 100)
            assert icon[2] < text[0] and text[2] < min(x for x, _ in arrow_pixels), (icon, text, arrow_pixels)
    assert isinstance(control, QPushButton) and control.accessibleName() == '外观主题'
    control.setMenu(None)
    spy = QSignalSpy(control.clicked)
    control.click()
    assert spy.count() == 1 and control.isChecked()
    control.close()


def test_navigation_accessible_press_and_checked_state(app):
    control = ui.button('项目', 'nav')
    control.setAccessibleName('项目')
    control.show()
    app.processEvents()
    interface = QAccessible.queryAccessibleInterface(control)
    actions = interface.actionInterface()
    press = QAccessibleActionInterface.pressAction()
    assert press in actions.actionNames()
    assert interface.text(QAccessible.Text.Name) == '项目'
    assert not interface.state().checked
    spy = QSignalSpy(control.clicked)
    actions.doAction(press)
    QTest.qWait(150)
    assert spy.count() == 1 and control.isChecked() and interface.state().checked
    actions.doAction(press)
    QTest.qWait(150)
    assert spy.count() == 2 and not control.isChecked() and not interface.state().checked
    control.close()
