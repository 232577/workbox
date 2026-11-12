"""Capture real Qt widgets using disposable anonymous data, never an installed DB."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QEnterEvent, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication, QMenu

from branding import ICON_SIZES, icon_image, write_ico
from desktop_controller import FloatingController
from desktop_service import DesktopService
from floating import FloatingBall
from floating_settings import FLOATING_SIZES
from theme import set_theme


def save(image, path: Path) -> None:
    if not image.save(str(path)):
        raise RuntimeError(f'Could not save capture: {path.name}')


def capture(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    scale = app.primaryScreen().devicePixelRatio()
    for size in (*ICON_SIZES, 512):
        save(icon_image(size), output / f'icon-{size}.png')
    write_ico(output / 'WorkBox.ico')

    for mode in ('light', 'dark'):
        set_theme(mode, persist=False)
        sheet = QImage(760, 220, QImage.Format_ARGB32_Premultiplied)
        sheet.fill(QColor('#F4F5F8' if mode == 'light' else '#181C29'))
        painter = QPainter(sheet)
        painter.setPen(QColor('#19233B' if mode == 'light' else '#E8EDFF'))
        font = QFont('Segoe UI')
        font.setPixelSize(13)
        painter.setFont(font)
        painter.drawText(20, 24, f'WorkBox | actual Qt captures | {mode} | display scale {scale:g}x')
        for index, size in enumerate(FLOATING_SIZES):
            orb = FloatingBall()
            try:
                orb.set_orb_size(size)
                orb.show()
                app.processEvents()
                pixmap = orb.grab()
                save(pixmap, output / f'orb-{mode}-{size}.png')
                painter.drawPixmap(QPoint(24 + index * 100, 62), pixmap)
                painter.drawText(24 + index * 100, 150, f'{size} x {size}')
            finally:
                orb.close()
                orb.deleteLater()
        painter.end()
        save(sheet, output / f'sizes-{mode}.png')

        orb = FloatingBall()
        try:
            orb.show()
            app.processEvents()
            point = QPointF(24, 24)
            QApplication.sendEvent(orb, QEnterEvent(point, point, point))
            save(orb.grab(), output / f'hover-{mode}.png')
            orb.set_summary(123)
            save(orb.grab(), output / f'count-{mode}.png')
            orb.set_progress(True, 67)
            save(orb.grab(), output / f'progress-{mode}.png')
            orb.set_drop_hint('files')
            save(orb.grab(), output / f'drop-{mode}.png')
            menu = orb.context_menu()
            size_menu = next(m for m in menu.findChildren(QMenu) if m.title() == '悬浮窗尺寸')
            size_menu.popup(QPoint(120, 120))
            app.processEvents()
            save(size_menu.grab(), output / f'size-menu-{mode}.png')
            menu.close()
            size_menu.close()
            menu.deleteLater()
        finally:
            orb.close()
            orb.deleteLater()

    with tempfile.TemporaryDirectory(prefix='anonymous-', dir=output) as temporary:
        service = DesktopService(Path(temporary) / 'workbox.db')
        service.receive_text('1. 示例资料管理\n2. 示例项目工作台\n3. 示例文件归档')
        control = FloatingController(service, tray_enabled=False)
        try:
            if not control.start():
                raise RuntimeError('Isolated controller did not start')
            control.show_panel()
            control.panel.resize(1100, 740)
            for mode in ('light', 'dark'):
                set_theme(mode, persist=False)
                app.processEvents()
                save(control.panel.grab(), output / f'workbench-{mode}.png')
        finally:
            control.shutdown()
            app.processEvents()
    (output / 'capture.json').write_text(json.dumps({
        'capture': 'real Qt widget grabs, isolated anonymous fixtures',
        'platform': QApplication.platformName(),
        'device_pixel_ratio': scale,
        'floating_logical_sizes': list(FLOATING_SIZES),
        'installed_runtime_modified': False,
    }, indent=2), encoding='utf-8')
    print(f'Captured real Qt brand and workbench evidence: {output}')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='.local/cube-brand/captures')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = (root / args.output).resolve()
    if not output.is_relative_to((root / '.local').resolve()):
        parser.error('Captures must stay inside the ignored .local directory')
    capture(output)


if __name__ == '__main__':
    main()
