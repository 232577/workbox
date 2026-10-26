# Third-party notices / 第三方组件说明

WorkBox v1.0 uses Python, SQLite and Qt for Python (PySide6 / Shiboken6).
The Windows package contains shared Qt libraries and the Python runtime;
PyInstaller is used to create the executable. WorkBox source and rebuild instructions are available in this repository and `README.md`.
The clean installer includes these notices and the license texts; it does not
include personal data or development/verification directories.

本程序未嵌入或分发系统字体；使用 Windows 上已安装的字体。
没有使用 Qt WebEngine、第三方在线服务或远程数据库。

## Components

- CPython 3.11.9: Python Software Foundation License and notices in `licenses/Python-LICENSE.txt`.
- Qt / PySide6 / Shiboken6 6.8.3: Qt for Python Community Edition, LGPLv3/GPLv3 alternatives.
  See the unmodified LGPL/GPL texts under `licenses/` and the Qt documentation below.
  The commercial-license reference shipped in the wheel is not a commercial license grant to this application.
- SQLite: supplied through Python's sqlite3 module; upstream describes SQLite as public domain.
- PyInstaller 6.22.0 bootloader: see `licenses/PyInstaller-COPYING.txt`, including its distribution exception.

Qt libraries are shipped as separate shared libraries in `_internal/`. Rebuilding
WorkBox with a compatible Qt for Python distribution is documented; this project
does not prohibit replacement of those libraries or debugging modifications to them.

## Upstream documentation and corresponding sources

- Qt for Python licenses: https://doc.qt.io/qtforpython-6/licenses.html
- Qt third-party attribution index: https://doc.qt.io/qt-6.8/licenses-used-in-qt.html
- Qt 6.8.3 source release: https://download.qt.io/archive/qt/6.8/6.8.3/single/
- PySide / Shiboken 6.8.3 source release: https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.8.3-src/
- Python 3.11.9: https://www.python.org/downloads/release/python-3119/
- SQLite copyright information: https://sqlite.org/copyright.html
- PyInstaller source: https://github.com/pyinstaller/pyinstaller/tree/v6.22.0

开发交付包与干净安装包均保留上述说明、对应上游源代码链接及随包许可证。
