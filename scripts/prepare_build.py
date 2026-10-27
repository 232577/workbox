"""Generate only build assets; no project data or ZIP inputs are touched."""

from pathlib import Path

from PySide6.QtWidgets import QApplication

from branding import write_ico


def main():
    root = Path(__file__).resolve().parents[1]
    output = root / 'build'
    output.mkdir(exist_ok=True)
    application = QApplication.instance() or QApplication([])
    path = output / 'WorkBox.ico'
    write_ico(path)
    print(f'Generated: {path}')
    return application


if __name__ == '__main__':
    main()
