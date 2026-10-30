"""文档页采用真实 Fluent 表格，刷新仍依赖 Qt 原生拖动和文档 ID。"""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication
from qfluentwidgets import TableWidget

from fluent_views import WorkbenchTableWidget
from project_documents_ui import ProjectDocumentsPane


@pytest.fixture(scope='module')
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def result(count=50):
    return {'project_id': 1, 'workspace_path': 'fixture', 'errors': [], 'documents': [
        {'id': f'fixture-{index:03d}', 'name': f'文件{index:03d}.txt',
         'relative_path': f'文件{index:03d}.txt', 'path': f'fixture/文件{index:03d}.txt',
         'kind': '工作文件', 'exists': True, 'size': 15, 'modified_at': None,
         'zip_id': None, 'is_directory': False} for index in range(count)]}


def test_document_table_adopts_single_fluent_delegate_and_keeps_refresh_anchor(qapp):
    pane = ProjectDocumentsPane(SimpleNamespace())
    pane.project_id = 1
    pane.table.resize(460, 250)
    pane.table.show()
    first = result()
    pane.apply_result(first)
    qapp.processEvents()
    assert isinstance(pane.table, (TableWidget, WorkbenchTableWidget))
    assert pane.table.itemDelegateForColumn(0) is None
    assert pane.table.delegate.wrapColumns == {0}
    pane.table.selectRow(20)
    bar = pane.table.verticalScrollBar()
    bar.setValue(420)
    qapp.processEvents()
    anchor = pane._document_anchor()
    selected_id = pane.current_document()['id']
    item = pane.table.item(20, 0)
    resets = QSignalSpy(pane.table.model().modelReset)
    pane.apply_result(first)
    assert pane.table.item(20, 0) is item and resets.count() == 0
    assert bar.value() == 420
    changed = deepcopy(first)
    extra = deepcopy(changed['documents'][0])
    extra.update(id='new-fixture', relative_path='000新增.txt', path='fixture/000新增.txt')
    changed['documents'].insert(0, extra)
    released = QSignalSpy(bar.sliderReleased)
    bar.setSliderDown(True)
    bar.setSliderPosition(470)
    moving_anchor = pane._document_anchor()
    pane.apply_result(changed)
    assert pane.table.rowCount() == 50 and pane._deferred is changed
    assert bar.isSliderDown() and bar.value() == 470
    bar.setSliderDown(False)
    qapp.processEvents()
    assert released.count() == 1 and pane.table.rowCount() == 51
    assert pane.current_document()['id'] == selected_id
    assert pane._document_anchor() == moving_anchor
    assert pane.current_document()['id'] == pane.table.currentItem().data(Qt.UserRole)
    assert anchor[0] is not None
    pane.shutdown()
