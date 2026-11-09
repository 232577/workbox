"""列表状态与文件图标应在两种主题中可读，并保留完整文件名。"""
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QFontMetrics
from PySide6.QtWidgets import (QApplication, QListWidgetItem, QStyleOptionViewItem,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from fluent_views import (PROJECT_DETAIL_ROLE, PROJECT_NAME_ROLE, ProjectListDelegate,
                          WorkbenchListWidget, WorkbenchTableWidget)
from project_documents_ui import ProjectDocumentsPane
from theme import install_theme, set_theme, tokens


@pytest.fixture(scope='module')
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


@pytest.mark.parametrize('mode', ['light', 'dark'])
@pytest.mark.parametrize('kind', ['list', 'table'])
def test_selected_and_hovered_rows_use_distinct_theme_surfaces(qapp, mode, kind):
    install_theme()
    set_theme(mode, persist=False)
    if kind == 'list':
        view = WorkbenchListWidget()
        view.setItemDelegate(ProjectListDelegate(view))
        for name in ('项目资料整理', '交付检查清单'):
            item = QListWidgetItem(name)
            item.setData(PROJECT_NAME_ROLE, name)
            item.setData(PROJECT_DETAIL_ROLE, '待交付 · 文档')
            view.addItem(item)
        view.setCurrentRow(0)
    else:
        view = WorkbenchTableWidget(2, 1)
        view.verticalHeader().hide()
        view.setColumnWidth(0, 350)
        for row, name in enumerate(('项目资料整理.docx', '交付检查清单.pdf')):
            view.setItem(row, 0, QTableWidgetItem(name))
            view.setRowHeight(row, 64)
        view.selectRow(0)
    view.resize(380, 210)
    view.show()
    view._setHoverRow(1)
    view._setPressedRow(-1)
    qapp.processEvents()
    frame = view.viewport().grab().toImage()
    scale = frame.devicePixelRatio()
    rects = [view.visualRect(view.model().index(row, 0)) for row in range(2)]
    samples = [frame.pixelColor(round(min(rect.right() - 24, view.viewport().width() - 24) * scale),
                               round((rect.top() + 10) * scale)) for rect in rects]
    assert samples[0].name() == QColor(tokens()['notice_bg']).name()
    assert samples[1] != samples[0]
    assert view.delegate.selectedRows == {0}
    view.hide()
    view.deleteLater()
    set_theme('light', persist=False)


def test_document_icons_keep_long_names_wrapped_and_theme_refresh_keeps_selection(qapp):
    install_theme()
    pane = ProjectDocumentsPane(SimpleNamespace())
    pane.shutdown()
    pane.project_id = 1
    pane.resize(470, 330)
    pane.show()
    filename = '跨部门项目资料与版本交付_' + 'OperationManual_20261104_' * 5 + '.docx'
    documents = [dict(id='anonymous-document', name=filename, relative_path=filename,
                      path='fixture/' + filename, kind='工作文件', exists=True, size=1024,
                      modified_at=None, zip_id=None, is_directory=False),
                 dict(id='anonymous-directory', name='归档材料', relative_path='归档材料',
                      path='fixture/归档材料', kind='工作文件', exists=True, size=None,
                      modified_at=None, zip_id=None, is_directory=True)]
    pane.apply_result(dict(project_id=1, workspace_path='fixture', errors=[], documents=documents))
    pane.table.setColumnWidth(0, 220)
    pane.table.resizeRowsToContents()
    pane.table.selectRow(0)
    qapp.processEvents()
    item = pane.table.item(0, 0)
    assert item.text() == filename and '\u200b' not in item.text()
    assert not item.icon().isNull() and not pane.table.item(1, 0).icon().isNull()
    assert item.icon().cacheKey() != pane.table.item(1, 0).icon().cacheKey()
    option = QStyleOptionViewItem()
    pane.table.delegate.initStyleOption(option, pane.table.model().index(0, 0))
    text_width = 220 - 32 - 6 - option.decorationSize.width() - 4
    required = QFontMetrics(option.font).boundingRect(
        QRect(0, 0, text_width, 100000), Qt.TextWordWrap, option.text).height()
    assert pane.table.rowHeight(0) >= required + 2 * pane.table.delegate.margin
    icon_key = item.icon().cacheKey()
    set_theme('dark', persist=False)
    qapp.processEvents()
    assert pane.table.item(0, 0) is item and item.isSelected()
    assert item.icon().cacheKey() != icon_key
    assert pane.current_document()['id'] == 'anonymous-document'
    assert pane.table.columnWidth(0) == 220
    pane.hide()
    pane.deleteLater()
    set_theme('light', persist=False)


def test_project_list_retains_six_complete_two_line_rows(qapp):
    install_theme()
    view = WorkbenchListWidget()
    view.setItemDelegate(ProjectListDelegate(view))
    for row in range(12):
        name = f'Project materials {row:02d}'
        item = QListWidgetItem(name)
        item.setData(PROJECT_NAME_ROLE, name)
        item.setData(PROJECT_DETAIL_ROLE, 'Ready for delivery')
        view.addItem(item)
    # Leave room for Windows font hinting while rejecting the former 7px row padding.
    view.resize(380, 310)
    view.show()
    qapp.processEvents()
    complete = [row for row in range(view.count())
                if view.viewport().rect().contains(view.visualItemRect(view.item(row)))]
    assert len(complete) >= 6, (len(complete), view.visualItemRect(view.item(0)).height())
    name, detail = view.delegate._text_blocks(view.model().index(0), 300)
    assert name[0].font().pixelSize() == 14
    assert detail[0].font().pixelSize() == 12
    view.hide()
    view.deleteLater()


def test_document_pane_retains_four_complete_short_file_rows(qapp):
    install_theme()
    pane = ProjectDocumentsPane(SimpleNamespace())
    pane.shutdown()
    pane.project_id = 1
    pane.resize(470, 282)
    pane.show()
    documents = [dict(id=f'document-{row}', name=f'Materials {row:02d}.txt',
                      relative_path=f'Materials {row:02d}.txt', path=f'fixture/{row:02d}.txt',
                      kind='Work file', exists=True, size=1024, modified_at=None,
                      zip_id=None, is_directory=False) for row in range(12)]
    pane.apply_result(dict(project_id=1, workspace_path='fixture', errors=[], documents=documents))
    qapp.processEvents()
    complete = [row for row in range(pane.table.rowCount())
                if pane.table.rowViewportPosition(row) >= 0 and
                pane.table.rowViewportPosition(row) + pane.table.rowHeight(row) <= pane.table.viewport().height()]
    assert len(complete) >= 4, (len(complete), pane.table.viewport().height(), pane.table.rowHeight(0))
    assert not pane.table.item(0, 0).icon().isNull()
    assert pane.file_count.isVisible() and pane.file_count.text() == '12 项'
    assert pane.table.horizontalHeaderItem(0).text() == '文件名称'
    assert pane.hint.isHidden()
    pane.apply_result(dict(project_id=1, workspace_path='fixture', errors=['Unavailable location'], documents=documents))
    assert not pane.hint.isHidden() and '文件列表不完整' in pane.hint.text()
    assert pane.table.rowCount() == 12
    assert pane.file_count.isVisible() and pane.file_count.text() == '已读取 12 项'
    assert '总数未知' in pane.file_count.toolTip()
    assert '总数未知' in pane.table.accessibleDescription()
    assert pane.table.horizontalHeaderItem(0).text() == '文件名称'
    pane.hide()
    pane.deleteLater()


@pytest.mark.parametrize('mode', ['light', 'dark'])
@pytest.mark.parametrize('kind', ['project', 'list', 'table'])
def test_only_project_list_idle_rows_blend_into_the_page(qapp, mode, kind):
    install_theme()
    set_theme(mode, persist=False)
    page = QWidget()
    page.setObjectName('manager')
    layout = QVBoxLayout(page)
    if kind == 'table':
        view = WorkbenchTableWidget(1, 1)
        view.setItem(0, 0, QTableWidgetItem('Materials.txt'))
        view.setColumnWidth(0, 340)
        view.setRowHeight(0, 48)
    else:
        view = WorkbenchListWidget()
        item = QListWidgetItem('Project materials')
        if kind == 'project':
            view.setObjectName('projectList')
            view.setItemDelegate(ProjectListDelegate(view))
            item.setData(PROJECT_NAME_ROLE, 'Project materials')
            item.setData(PROJECT_DETAIL_ROLE, 'Ready for delivery')
        view.addItem(item)
    view.setFocusPolicy(Qt.NoFocus)
    layout.addWidget(view)
    page.resize(380, 220)
    page.show()
    view.clearSelection()
    view._setHoverRow(-1)
    view._setPressedRow(-1)
    qapp.processEvents()
    frame = page.grab().toImage()
    scale = frame.devicePixelRatio()
    rect = view.visualRect(view.model().index(0, 0))
    origin = view.viewport().mapTo(page, QPoint(0, 0))
    expected = QColor(tokens()['bg' if kind == 'project' else 'surface']).name()
    assert frame.pixelColor(round((origin.x() + view.viewport().width() - 30) * scale),
                            round((origin.y() + rect.top() + 10) * scale)).name() == expected
    assert frame.pixelColor(round((origin.x() + view.viewport().width() - 30) * scale),
                            round((origin.y() + view.viewport().height() - 20) * scale)).name() == expected
    page.hide()
    page.deleteLater()
    set_theme('light', persist=False)
