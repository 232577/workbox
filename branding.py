"""WorkBox 品牌矢量与 Windows 图标的唯一生成源。"""

from __future__ import annotations

import struct
from pathlib import Path

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QRectF, Qt
from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer


ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)
_DEFINITIONS = '''<defs>
<linearGradient id="app-surface" x1="8" y1="0" x2="56" y2="64" gradientUnits="userSpaceOnUse"><stop stop-color="#1B294B"/><stop offset="1" stop-color="#0B1228"/></linearGradient>
<linearGradient id="top-face" x1="13" y1="12" x2="49" y2="30" gradientUnits="userSpaceOnUse"><stop stop-color="#35DFFF"/><stop offset=".5" stop-color="#559EFF"/><stop offset="1" stop-color="#AB75F5"/></linearGradient>
<linearGradient id="left-face" x1="9" y1="24" x2="32" y2="54" gradientUnits="userSpaceOnUse"><stop stop-color="#19C6F7"/><stop offset=".55" stop-color="#278BF0"/><stop offset="1" stop-color="#5364ED"/></linearGradient>
<linearGradient id="right-face" x1="33" y1="27" x2="54" y2="50" gradientUnits="userSpaceOnUse"><stop stop-color="#526FF1"/><stop offset=".5" stop-color="#5438D5"/><stop offset="1" stop-color="#913FEA"/></linearGradient>
</defs>'''
_OUTLINE = 'M28.5 7.8Q32 5.8 35.5 7.8L52.5 17.6Q56 19.6 56 23.6V40.4Q56 44.4 52.5 46.4L35.5 56.2Q32 58.2 28.5 56.2L11.5 46.4Q8 44.4 8 40.4V23.6Q8 19.6 11.5 17.6Z'
_MARK = f'''<path id="cube-right" d="{_OUTLINE}" fill="url(#right-face)"/>
<path id="cube-left" d="M9 20.5L29 32.1Q32 33.8 32 37.2V57.2Q30.5 57.3 28.5 56.2L11.5 46.4Q8 44.4 8 40.4V23.6Q8 21.8 9 20.5Z" fill="url(#left-face)"/>
<path id="cube-top" d="M11.5 17.6L28.5 7.8Q32 5.8 35.5 7.8L52.5 17.6Q55 19 55 20.5L35.2 31.9Q32 33.8 28.8 31.9L9 20.5Q9 19 11.5 17.6Z" fill="url(#top-face)"/>
<path d="M9 20.5L28.8 31.9Q32 33.8 35.2 31.9L55 20.5M32 33.4V56.8" fill="none" stroke="#B3DFFF" stroke-opacity=".8" stroke-width="1.2" stroke-linecap="round"/>
<path d="{_OUTLINE}" fill="none" stroke="#C0DEFF" stroke-opacity=".65" stroke-width=".8"/>
<path id="cube-w" transform="matrix(.92 -.531 0 1 34 36)" d="M0 0H3.6L5.4 9 8.1 0H11.3L13.8 9 15.5 0H19L15.6 15H12.2L9.7 6.9 7.1 15H3.6Z" fill="#FFFFFF"/>'''


def svg_bytes(*, background: bool = True) -> bytes:
    """应用与悬浮窗共用圆角立方体及白色 W；不依赖字体、位图或滤镜。"""
    surface = ('<rect x=".75" y=".75" width="62.5" height="62.5" rx="14.5" '
               'fill="url(#app-surface)" stroke="#7185CA" stroke-opacity=".32" stroke-width="1.5"/>') if background else ''
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">\n'
            f'{_DEFINITIONS}\n{surface}\n{_MARK}\n</svg>\n').encode('utf-8')


def draw_mark(painter: QPainter, bounds: QRectF) -> None:
    QSvgRenderer(QByteArray(svg_bytes(background=False))).render(painter, bounds)


def icon_image(size: int) -> QImage:
    if not isinstance(size, int) or isinstance(size, bool) or not 1 <= size <= 4096:
        raise ValueError('图标边长必须是 1–4096 之间的整数')
    image = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    try:
        QSvgRenderer(QByteArray(svg_bytes())).render(painter)
    finally:
        painter.end()
    return image


def brand_icon() -> QIcon:
    icon = QIcon()
    for size in ICON_SIZES:
        icon.addPixmap(QPixmap.fromImage(icon_image(size)))
    return icon


def write_ico(path: Path) -> None:
    """写入七档无损 PNG 帧，兼容 Windows 高 DPI 图标选择。"""
    frames = []
    for size in ICON_SIZES:
        buffer = QBuffer()
        buffer.open(QIODevice.WriteOnly)
        if not icon_image(size).save(buffer, 'PNG'):
            raise RuntimeError(f'无法生成 {size}px 图标')
        frames.append(bytes(buffer.data()))
    header = struct.pack('<HHH', 0, 1, len(frames))
    offset = len(header) + 16 * len(frames)
    entries = []
    for size, frame in zip(ICON_SIZES, frames):
        entries.append(struct.pack('<BBBBHHII', size % 256, size % 256, 0, 0,
                                   1, 32, len(frame), offset))
        offset += len(frame)
    Path(path).write_bytes(header + b''.join(entries) + b''.join(frames))
