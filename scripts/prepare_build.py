"""Generate only build assets; no project data or ZIP inputs are touched."""

from pathlib import Path

from PySide6.QtWidgets import QApplication

from ui import app_icon


def main():
    root = Path(__file__).resolve().parents[1]
    output = root / 'build'
    output.mkdir(exist_ok=True)
    application = QApplication.instance() or QApplication([])
    icon = app_icon()
    path = output / 'WorkBox.ico'
    if not icon.pixmap(64, 64).save(str(path), 'ICO'):
        raise RuntimeError('Could not generate the Windows icon')
    print(f'Generated: {path}')
    return application


if __name__ == '__main__':
    main()
