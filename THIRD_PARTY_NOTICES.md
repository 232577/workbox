# Third-party notices / 第三方组件说明

WorkBox v1.0 uses Python, SQLite and Qt for Python (PySide6 / Shiboken6).
The Windows package contains shared Qt libraries and the Python runtime;
PyInstaller is used to create the executable. WorkBox source and rebuild instructions are available in this repository and `README.md`.
The clean installer includes these notices and the license texts; it does not
include personal data or development/verification directories.

本程序未嵌入或分发系统字体；使用 Windows 上已安装的字体。
没有使用 Qt WebEngine、第三方在线服务或远程数据库。

## Components

- WorkBox's own source in the Fluent enhanced build is released under GPL-3.0-only,
  as authorized by the project owner on 2026-10-28. See `licenses/GPL-3.0.txt`
  in this package, or `LICENSE` in the source repository. Third-party components
  retain their own notices.
- PySide6-Fluent-Widgets 1.11.3: GPLv3, Copyright zhiyiYo. The application uses
  its basic widgets and icon resources, with WorkBox compatibility adapters;
  see `licenses/QFluentWidgets-LICENSE.txt`.
- PySideSix-Frameless-Window 0.8.2: LGPLv3; an import dependency of Fluent Widgets.
  WorkBox keeps its native window frame and Qt menus. See
  `licenses/QtFramelessWindow-LICENSE.txt`.
- darkdetect 0.8.0: BSD 3-Clause; see `licenses/Darkdetect-LICENSE.txt`.
- pywin32 312: Python Software Foundation and component-specific notices.
  Its eight declared license files and additional MAPI attribution notice are
  retained as `licenses/PyWin32-*.txt`.

- CPython 3.11.9: Python Software Foundation License and notices in `licenses/Python-LICENSE.txt`.
- Qt / PySide6 / Shiboken6 6.8.3: Qt for Python Community Edition, LGPLv3/GPLv3 alternatives.
  See the unmodified LGPL/GPL texts under `licenses/` and the Qt documentation below.
  The commercial-license reference shipped in the wheel is not a commercial license grant to this application.
- SQLite: supplied through Python's sqlite3 module; upstream describes SQLite as public domain.
- PyInstaller 6.22.0 bootloader: see `licenses/PyInstaller-COPYING.txt`, including its distribution exception.
- QDarkStyle 3.2.3: code under the MIT License; theme images under Creative Commons
  Attribution 4.0 International. Copyright Colin Duquesnoy (2013–2019) and,
  for images, Daniel Cosmo Pizetta (2019). WorkBox uses the unmodified compiled
  theme resources and adds its own stylesheet after the upstream stylesheet;
  the resource images are unmodified. See `licenses/QDarkStyle-LICENSE.txt`
  for the complete code and image license texts and attribution conditions.
- QtPy 2.4.3: MIT License; see `licenses/QtPy-LICENSE.txt`.
- packaging 26.3: Apache License 2.0 or BSD 2-Clause License; a runtime dependency
  of QtPy. See `licenses/Packaging-LICENSE.txt`,
  `licenses/Packaging-LICENSE-APACHE.txt` and `licenses/Packaging-LICENSE-BSD.txt`.

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
- QDarkStyle sources and theme images: https://github.com/ColinDuquesnoy/QDarkStyleSheet
- QDarkStyle code/image licenses: https://github.com/ColinDuquesnoy/QDarkStyleSheet/blob/master/LICENSE.rst
- QtPy source and license: https://github.com/spyder-ide/qtpy
- PySide6 Fluent Widgets source: https://github.com/zhiyiYo/PyQt-Fluent-Widgets/tree/PySide6
- Qt Frameless Window source: https://github.com/zhiyiYo/PyQt-Frameless-Window
- darkdetect source: https://github.com/albertosottile/darkdetect
- pywin32 source: https://github.com/mhammond/pywin32/tree/b312

开发交付包与干净安装包均保留上述说明、对应上游源代码链接及随包许可证。
