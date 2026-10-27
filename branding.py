"""WorkBox 品牌矢量与 Windows 图标的唯一生成源。"""

from __future__ import annotations

import struct
from pathlib import Path

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QRectF, Qt
from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer


ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)
_MARK = '''<path d="m12 24 12-8 8 7-12 8Z" fill="#8AE7DA"/>
<path d="m32 23 8-7 12 8-8 7Z" fill="#4DCAB9"/>
<path d="m16 29 8 4 3 10 5-7 5 7 3-10 8-4-4 17-7 4-5-7-5 7-7-4Z" fill="#53D6C2"/>
<path d="m32 36 5 7 3-10 8-4-4 17-7 4-5-7Z" fill="#25B5A4"/>'''


def svg_bytes(*, background: bool = True) -> bytes:
    """完整图标与悬浮球中心共用相同的折盒 W 路径。"""
    surface = '<rect width="64" height="64" rx="15" fill="#102D40"/>' if background else ''
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">\n'
            f'{surface}\n{_MARK}\n</svg>\n').encode('utf-8')


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
