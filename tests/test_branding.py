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


@pytest.mark.parametrize('size', [16, 24, 32, 48, 64, 128, 256])
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


def test_floating_mark_keeps_badge_and_progress_outside_the_symbol(qapp):
    from floating import FloatingBall
    ball = FloatingBall()
    try:
        def render():
            image = QImage(76, 76, QImage.Format_ARGB32_Premultiplied)
            image.fill(Qt.transparent)
            ball.render(image)
            return image

        idle = render()
        ball.set_summary(8)
        counted = render()
        ball.set_progress(True, 67)
        progressing = render()
        assert ball.size().width() == ball.size().height() == 76
        assert idle != counted and idle != progressing
        assert idle.copy(26, 26, 24, 24) == counted.copy(26, 26, 24, 24)
        assert idle.copy(26, 26, 24, 24) == progressing.copy(26, 26, 24, 24)
        mark = QImage(76, 76, QImage.Format_ARGB32_Premultiplied)
        mark.fill(Qt.transparent)
        painter = QPainter(mark)
        branding.draw_mark(painter, QRectF(10, 9, 56, 56))
        painter.end()
        # 中心前景与共享矢量一致；底色混合允许两级抗锯齿舍入误差。
        for y in range(20, 54):
            for x in range(18, 58):
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
