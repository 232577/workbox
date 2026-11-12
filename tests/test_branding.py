import struct
from pathlib import Path

import pytest
from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

import branding


@pytest.fixture(scope='module')
def qapp():
    return QApplication.instance() or QApplication([])


def test_vector_assets_are_valid_and_match_runtime():
    root = Path(__file__).resolve().parents[1]
    for path in (root / 'assets/workbox.svg', root / 'website/public/favicon.svg'):
        data = path.read_bytes()
        assert QSvgRenderer(QByteArray(data)).isValid()
        assert data == branding.svg_bytes()


@pytest.mark.parametrize('size', [16, 24, 32, 48, 64, 128, 256, 512])
def test_icon_has_visible_mark_and_transparent_corners(qapp, size):
    image = branding.icon_image(size)
    assert image.size().width() == image.size().height() == size
    assert image.pixelColor(0, 0).alpha() == 0
    assert image.pixelColor(size // 2, size // 8).alpha() > 240
    vivid = sum(
        1 for y in range(size) for x in range(size)
        if (c := image.pixelColor(x, y)).green() > 110
        and c.green() > c.red() * 1.4 and c.alpha() > 180
    )
    assert vivid > size * size * .15
    assert vivid < size * size * .55


def test_windows_ico_contains_seven_decodable_native_sizes(qapp, tmp_path):
    path = tmp_path / 'WorkBox.ico'
    branding.write_ico(path)
    data = path.read_bytes()
    assert struct.unpack_from('<HHH', data) == (0, 1, 7)
    for index, size in enumerate(branding.ICON_SIZES):
        width, height, _, _, planes, bits, length, offset = struct.unpack_from(
            '<BBBBHHII', data, 6 + index * 16
        )
        assert (width or 256, height or 256) == (size, size)
        assert (planes, bits) == (1, 32)
        image = QImage.fromData(data[offset:offset + length], 'PNG')
        assert not image.isNull()
        assert image.width() == image.height() == size
    assert not QImage(str(path)).isNull()


def test_runtime_icon_exposes_native_sizes(qapp):
    from ui import app_icon
    icon = app_icon()
    assert {(s.width(), s.height()) for s in icon.availableSizes()} == {
        (size, size) for size in branding.ICON_SIZES
    }
    assert not icon.pixmap(16, 16).isNull()


@pytest.mark.parametrize('size', [32, 40, 48, 56, 64])
@pytest.mark.parametrize('dpr', [1, 2])
def test_floating_mark_keeps_badge_and_progress_outside_the_symbol(qapp, size, dpr):
    from floating import FloatingBall
    ball = FloatingBall()
    ball.set_orb_size(size)
    try:
        def render():
            image = QImage(size * dpr, size * dpr, QImage.Format_ARGB32_Premultiplied)
            image.setDevicePixelRatio(dpr)
            image.fill(Qt.transparent)
            ball.render(image)
            return image

        idle = render()
        ball.set_summary(8)
        counted = render()
        ball.set_progress(True, 67)
        progressing = render()
        assert ball.size().width() == ball.size().height() == size
        assert idle != counted and idle != progressing
        unit = size * dpr / 48
        center = (round(16 * unit), round(16 * unit), round(16 * unit), round(16 * unit))
        assert idle.copy(*center) == counted.copy(*center)
        assert idle.copy(*center) == progressing.copy(*center)
        mark = QImage(size * dpr, size * dpr, QImage.Format_ARGB32_Premultiplied)
        mark.setDevicePixelRatio(dpr)
        mark.fill(Qt.transparent)
        painter = QPainter(mark)
        painter.scale(size / 48, size / 48)
        branding.draw_mark(painter, QRectF(5, 5, 38, 38))
        painter.end()
        # 中心前景与共享矢量一致；底色混合允许两级抗锯齿舍入误差。
        for y in range(round(13 * unit), round(39 * unit)):
            for x in range(round(11 * unit), round(37 * unit)):
                if mark.pixelColor(x, y).alpha() == 255:
                    actual = idle.pixelColor(x, y).getRgb()
                    expected = mark.pixelColor(x, y).getRgb()
                    assert max(abs(a - b) for a, b in zip(actual, expected)) <= 2
    finally:
        ball.close()


@pytest.mark.parametrize('size', [0, -1, 4097, 2.5])
def test_raster_size_is_bounded(size):
    with pytest.raises(ValueError):
        branding.icon_image(size)
