"""Regressions for project file actions, typography and native keyboard access."""
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QDesktopServices, QFont, QFontMetrics
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QStyleOptionViewItem

from project_documents_ui import ProjectDocumentsPane
from theme import install_theme


@pytest.fixture
def pane():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    install_theme()
    widget = ProjectDocumentsPane(SimpleNamespace())
    widget.shutdown()  # These tests supply anonymous results directly.
    widget.project_id = 1
    widget.resize(380, 320)
    widget.show()
    yield widget
    widget.hide()
    widget.deleteLater()
    app.processEvents()


def document(path):
    return dict(id=path.name, name=path.name, relative_path=path.name, path=str(path),
                kind='工作文件', exists=True, size=18, modified_at=None, zip_id=None,
                is_directory=False)


def show_documents(pane, *documents):
    pane.apply_result(dict(project_id=1, workspace_path='fixture', errors=[], documents=list(documents)))
    QApplication.processEvents()


def test_file_actions_stay_above_the_table_without_shifting_rows(pane, tmp_path):
    show_documents(pane, document(tmp_path / '维护清单.txt'))
    assert pane.file_menu_button.isVisible() and not pane.open_button.isVisible()
    top = pane.table.mapTo(pane, QPoint()).y()
    assert pane.file_menu_button.mapTo(pane, pane.file_menu_button.rect().bottomLeft()).y() < top

    pane.table.selectRow(0)
    QApplication.processEvents()
    assert pane.open_button.isVisible() and pane.open_button.isEnabled()
    assert pane.open_button.mapTo(pane, pane.open_button.rect().bottomLeft()).y() < top
    assert pane.table.mapTo(pane, QPoint()).y() == top


def test_unselected_project_guidance_stays_with_the_section_heading(pane):
    pane.set_project(None)
    QApplication.processEvents()

    heading = pane.findChild(QLabel, 'sectionTitle')
    assert heading.isVisible() and pane.hint.isVisible() and not pane.table.isVisible()
    heading_top = heading.mapTo(pane, QPoint()).y()
    hint_top = pane.hint.mapTo(pane, QPoint()).y()
    assert heading_top < hint_top < heading_top + 54


@pytest.mark.parametrize('key', [Qt.Key_Return, Qt.Key_Enter])
def test_file_activation_keys_locate_program_files_without_opening_them(pane, tmp_path, monkeypatch, key):
    path = tmp_path / '示例程序.exe'
    path.write_bytes(b'anonymous fixture')
    opened, located = [], []
    monkeypatch.setattr(QDesktopServices, 'openUrl', lambda url: opened.append(url) or True)
    pane.folder_requested.connect(located.append)
    show_documents(pane, document(path))
    pane.table.selectRow(0)
    pane.table.setFocus()

    QTest.keyClick(pane.table, key)

    assert located == [str(tmp_path)] and not opened


def test_long_file_names_keep_regular_weight_and_full_model_text(pane, tmp_path):
    name = '设备巡检与交付清单_' + 'Operations_20261109_' * 5 + '.txt'
    show_documents(pane, document(tmp_path / name), document(tmp_path / '概要.txt'))
    pane.table.setColumnWidth(0, 168)
    pane.table.resizeRowsToContents()
    option = QStyleOptionViewItem()
    option.initFrom(pane.table)
    pane.table.delegate.initStyleOption(option, pane.table.model().index(0, 0))

    item = pane.table.item(0, 0)
    assert option.font.weight() == QFont.Normal
    assert pane.table.rowHeight(0) > 2 * pane.table.rowHeight(1)
    assert item.text() == name and name in item.toolTip()


def test_first_visible_file_rows_use_the_fitted_width(pane, tmp_path):
    pane.resize(620, 320)
    QApplication.processEvents()
    name = '设备巡检与交付清单_' + 'Operations_20261109_' * 5 + '.txt'
    show_documents(pane, document(tmp_path / name))

    initial_height = pane.table.rowHeight(0)
    pane.table.resizeRowsToContents()
    assert pane.table.rowHeight(0) == initial_height


def test_narrow_file_cell_reserves_wrapped_height_beside_the_icon(pane, tmp_path):
    name = '跨部门设备巡检第二阶段现场验收与迁移计划_' + 'Operations_20261109_' * 3 + '.docx'
    show_documents(pane, document(tmp_path / name))
    table = pane.table
    option = QStyleOptionViewItem()
    table.initViewItemOption(option)
    table.delegate.initStyleOption(option, table.model().index(0, 0))
    metrics = QFontMetrics(option.font)

    for width in range(120, 145):
        table.setColumnWidth(0, width)
        table.resizeRowsToContents()
        # Horizontal QSS padding is 16px per side; Qt also insets both text and icon by 3px.
        text_width = width - 32 - 6 - option.decorationSize.width() - 6
        needed = metrics.boundingRect(QRect(0, 0, text_width, 100000), Qt.TextWordWrap, option.text)
        assert table.rowHeight(0) >= needed.height() + 2 * table.delegate.margin, width
