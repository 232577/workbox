"""折叠导航计数不改变图标水平中心，且整块按钮可操作。"""
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from theme import install_theme, set_theme
from ui_components import button
from compact_ui import CompactPanel
from desktop_service import DesktopService


@pytest.fixture(scope='module')
def app():
    instance = QApplication.instance() or QApplication([])
    instance.setQuitOnLastWindowClosed(False)
    return instance


def icon_bounds(image):
    pixels = [(x, y) for y in range(image.height()) for x in range(image.width())
              if (lambda c: c.red() > 240 and c.green() < 30 and c.blue() < 30)(image.pixelColor(x, y))]
    assert pixels
    return min(x for x, y in pixels), min(y for x, y in pixels), max(x for x, y in pixels), max(y for x, y in pixels)


@pytest.mark.parametrize('mode', ['light', 'dark'])
def test_count_below_icon_preserves_center_and_button_contract(app, mode, tmp_path):
    install_theme()
    set_theme(mode, persist=False)
    icon = QPixmap(16, 16)
    icon.fill(QColor('red'))
    # 实际导航是子控件；独立顶层窗口会被 Windows 强制扩大到系统最小宽度。
    host = QWidget()
    host.resize(160, 160)
    normal = button('', 'nav')
    normal.setParent(host)
    normal.setProperty('compactNav', True)
    normal.setIcon(QIcon(icon))
    normal.setFixedSize(56, 36)
    normal.show()
    counted = button('', 'nav')
    counted.setParent(host)
    counted.move(0, 50)
    counted.setProperty('compactNav', True)
    counted.setCheckable(False)
    counted.setIcon(QIcon(icon))
    counted.setFixedWidth(56)
    counted.show()
    host.show()
    app.processEvents()
    normal_center = sum(icon_bounds(normal.grab().toImage())[::2]) / 2
    spy = QSignalSpy(counted.clicked)
    for value in ('0', '17', '99+'):
        counted.setCompactCount(value)
        app.processEvents()
        label = counted.findChild(QLabel, 'navigationCountBadge')
        assert label and label.isVisible() and label.text() == value
        assert label.testAttribute(Qt.WA_TransparentForMouseEvents)
        assert label.focusPolicy() == Qt.NoFocus
        image = counted.grab().toImage()
        image.save(str(tmp_path / f'{mode}-{value}.png'))
        left, top, right, bottom = icon_bounds(image)
        ratio = image.devicePixelRatio()
        assert counted.width() == 56
        assert abs((left + right) / 2 - normal_center) <= ratio
        assert bottom < label.geometry().top() * ratio
        assert counted.rect().contains(label.geometry())
        QTest.mouseClick(counted, Qt.LeftButton, pos=label.geometry().center())
    assert spy.count() == 3
    counted.setFocus()
    app.processEvents()
    assert abs(sum(icon_bounds(counted.grab().toImage())[::2]) / 2 - normal_center) <= counted.devicePixelRatioF()
    counted.setEnabled(False)
    app.processEvents()
    assert not counted.isEnabled() and label.isVisible()
    counted.setCompactCount(None)
    assert label.isHidden()
    counted.setText('今日工作量 17')
    assert counted.sizeHint().width() >= counted.fontMetrics().horizontalAdvance(counted.text())
    counted.close()
    normal.close()
    host.close()


@pytest.mark.parametrize('mode', ['light', 'dark'])
@pytest.mark.parametrize('height', [480, 780])
def test_panel_count_icon_is_complete_across_resize_and_theme(app, mode, height, tmp_path):
    service = DesktopService(tmp_path / 'data' / 'workbox.db')
    install_theme(service)
    set_theme(mode, persist=False)
    panel = CompactPanel(service)
    panel.resize(660, height)
    panel.show()
    app.processEvents()
    pixmap = QPixmap(16, 16)
    pixmap.fill(QColor('red'))
    count_icon = QIcon(pixmap)
    count_icon.addPixmap(pixmap, QIcon.Disabled)
    panel.today_button.setIcon(count_icon)
    panel.nav_zip.setIcon(QIcon(pixmap))
    try:
        for value in ('0', '17', '99+'):
            panel._today_compact_caption = value
            panel.resize_navigation()
            app.processEvents()
            control = panel.today_button
            assert control.width() == 56
            badge = control.findChild(QLabel, 'navigationCountBadge')
            img = control.grab().toImage()
            img.save(str(tmp_path / f'{mode}-{height}-{value}.png'))
            left, top, right, bottom = icon_bounds(img)
            normal = icon_bounds(panel.nav_zip.grab().toImage())
            ratio = img.devicePixelRatio()
            assert (right - left + 1, bottom - top + 1) == (16 * ratio, 16 * ratio)
            assert abs((left + right - normal[0] - normal[2]) / 2) <= ratio
            assert bottom < badge.y() * ratio
            assert control.rect().contains(badge.geometry()) and badge.text() == value
        for enabled in (True, False):
            control.setEnabled(enabled)
            control.setFocus()
            app.processEvents()
            img = control.grab().toImage()
            left, top, right, bottom = icon_bounds(img)
            assert bottom - top + 1 == 16 * img.devicePixelRatio()
            assert bottom < badge.y() * img.devicePixelRatio()
        control.setEnabled(True)
        for _ in range(2):
            panel.resize(1100, height)
            app.processEvents()
            assert badge.isHidden() and panel.today_button.text()
            set_theme('dark' if mode == 'light' else 'light', persist=False)
            panel.resize(660, height)
            app.processEvents()
            img = panel.today_button.grab().toImage()
            left, top, right, bottom = icon_bounds(img)
            assert bottom - top + 1 == 16 * img.devicePixelRatio()
            assert bottom < badge.y() * img.devicePixelRatio()
            set_theme(mode, persist=False)
    finally:
        panel.document_pane.shutdown()
        panel.close()
        install_theme()


@pytest.mark.parametrize('mode', ['light', 'dark'])
@pytest.mark.parametrize('width,height', [(660, 480), (660, 780), (1200, 480), (1200, 780)])
def test_all_sidebar_icons_share_horizontal_center(app, mode, width, height, tmp_path):
    service = DesktopService(tmp_path / 'data' / 'workbox.db')
    install_theme(service)
    set_theme(mode, persist=False)
    panel = CompactPanel(service)
    panel.resize(width, height)
    panel.show()
    app.processEvents()
    pixmap = QPixmap(16, 16)
    pixmap.fill(QColor('red'))
    for control, _ in panel.nav_controls:
        control.setIcon(QIcon(pixmap))
    app.processEvents()
    try:
        centers = []
        for control, caption in panel.nav_controls:
            image = control.grab().toImage()
            left, top, right, bottom = icon_bounds(image)
            ratio = image.devicePixelRatio()
            assert (right - left + 1, bottom - top + 1) == (16 * ratio, 16 * ratio), caption
            origin = control.mapTo(panel.navigation, QPoint(0, 0)).x()
            centers.append((caption, origin + (left + right) / (2 * ratio)))
        assert max(x for _, x in centers) - min(x for _, x in centers) <= 1, centers
    finally:
        panel.document_pane.shutdown()
        panel.close()
        install_theme()


@pytest.mark.parametrize('mode', ['light', 'dark'])
@pytest.mark.parametrize('height', [480, 780])
def test_real_menu_navigation_keeps_label_arrow_and_native_contract(app, mode, height, tmp_path):
    service = DesktopService(tmp_path / 'data/workbox.db')
    install_theme(service)
    set_theme(mode, persist=False)
    panel = CompactPanel(service)
    panel.resize(1200, height)
    panel.show()
    app.processEvents()
    control = panel.appearance_button
    pixmap = QPixmap(16, 16)
    pixmap.fill(QColor('red'))
    control.setIcon(QIcon(pixmap))
    background = '#222222' if mode == 'dark' else 'white'
    control.setStyleSheet(f'QToolButton#nav {{ color: #00ff00; background: {background}; }}')
    try:
        control.clearFocus()
        app.processEvents()
        image = control.grab().toImage()
        ratio = image.devicePixelRatio()
        left, top, right, bottom = icon_bounds(image)
        text = [(x, y) for y in range(image.height()) for x in range(image.width() - int(20 * ratio))
                if (lambda c: c.green() > 150 and c.red() < 100 and c.blue() < 100)(image.pixelColor(x, y))]
        arrow = [(x, y) for y in range(int(4 * ratio), image.height() - int(4 * ratio))
                 for x in range(image.width() - int(20 * ratio), image.width() - int(3 * ratio))
                 if (image.pixelColor(x, y).lightness() > 180 if mode == 'dark'
                     else image.pixelColor(x, y).lightness() < 100)]
        assert text and arrow
        assert right < min(x for x, _ in text)
        assert max(x for x, _ in text) < min(x for x, _ in arrow)
        assert abs((min(y for _, y in arrow) + max(y for _, y in arrow)) / 2
                   - (image.height() - 1) / 2) <= 2 * ratio
        image.save(str(tmp_path / f'{mode}-{height}-menu.png'))
    finally:
        panel.document_pane.shutdown()
        panel.close()
        install_theme()
