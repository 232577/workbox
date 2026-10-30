"""长项目名、文档名通过 Qt 换行完整显示，重排不替换业务对象。"""
import pytest
from PySide6.QtCore import Qt, QPoint, QPointF
from PySide6.QtGui import QTextLayout, QTextOption
from PySide6.QtWidgets import QApplication, QAbstractItemView, QStyle, QStyleOptionViewItem
from PySide6.QtTest import QTest

from compact_ui import CompactPanel
from desktop_service import DesktopService
from theme import install_theme, set_theme


LONG_PROJECT = '智能设备运行状态采集与远程维护综合管理平台'
LONG_DOCUMENT = LONG_PROJECT + '_OperationManual_Revision20261028.docx'


@pytest.fixture(scope='module')
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


@pytest.fixture
def panel(qapp, tmp_path):
    service = DesktopService(tmp_path / 'data/workbox.db')
    service.db.create_capture('隔离排版验证', '资料', [(LONG_PROJECT, False), ('短名称系统', False)])
    value = CompactPanel(service)
    value.show()
    qapp.processEvents()
    yield value
    value.document_pane.shutdown()
    value.hide()


def settle(qapp):
    QTest.qWait(120)
    for _ in range(5):
        qapp.processEvents()
        QTest.qWait(20)


def assert_complete_item_text_fits(view, item):
    """按 Qt 标准委托实际绘制框、字体和断行文本测量，覆盖最后一行及尾缀。"""
    index = view.indexFromItem(item)
    option = QStyleOptionViewItem()
    view.initViewItemOption(option)
    view.itemDelegateForIndex(index).initStyleOption(option, index)
    option.state = (option.state | QStyle.State_Selected if item.isSelected()
                    else option.state & ~QStyle.State_Selected)
    option.state = (option.state | QStyle.State_HasFocus if view.hasFocus() and view.currentItem() is item
                    else option.state & ~QStyle.State_HasFocus)
    option.rect = view.visualItemRect(item)
    from qfluentwidgets import ListWidget, TableWidget
    if isinstance(view, (ListWidget, TableWidget)):
        # 以库1.11.3的实际绘制契约独立测量：item水平11/16px + Qt文本边距3px。
        # 不在Fluent样式代理上重入subElementRect，另有真实像素测试覆盖尾字和对比度。
        content = option.rect.adjusted(14 if isinstance(view, ListWidget) else 19,
                                       view.itemDelegate().margin,
                                       -14 if isinstance(view, ListWidget) else -19,
                                       -view.itemDelegate().margin)
    else:
        content = view.style().subElementRect(QStyle.SE_ItemViewItemText, option, view)
        margin = view.style().pixelMetric(QStyle.PM_FocusFrameHMargin, None, view) + 1
        content.adjust(margin, 0, -margin, 0)
    text_option = QTextOption()
    text_option.setWrapMode(QTextOption.WordWrap)
    text_option.setTextDirection(option.direction)
    text_option.setAlignment(QStyle.visualAlignment(option.direction, option.displayAlignment))
    layout = QTextLayout(option.text, option.font)
    layout.setTextOption(text_option)
    height = 0.0
    covered = 0
    layout.beginLayout()
    while True:
        line = layout.createLine()
        if not line.isValid():
            break
        line.setLineWidth(content.width())
        line.setPosition(QPointF(0, height))
        height += line.height()
        covered += line.textLength()
        assert line.naturalTextWidth() <= content.width(), (item.text(), content, line.naturalTextWidth())
    layout.endLayout()
    assert covered == len(option.text), (item.text(), covered, len(option.text))
    assert height <= content.height(), (item.text(), content, height, option.font.toString())


def test_project_names_wrap_and_rows_reflow_without_losing_identity(panel, qapp):
    view = panel.project_list
    long_item = next(view.item(i) for i in range(view.count()) if LONG_PROJECT in view.item(i).text())
    view.setCurrentItem(long_item)
    project_id = long_item.data(Qt.UserRole)
    panel.project_splitter.setSizes([250, 750])
    settle(qapp)
    narrow_height = view.visualItemRect(long_item).height()
    assert view.wordWrap() and view.textElideMode() == Qt.ElideNone
    assert not view.uniformItemSizes()
    assert_complete_item_text_fits(view, long_item)
    original_text = long_item.text()
    view.setCurrentItem(next(view.item(i) for i in range(view.count()) if view.item(i) is not long_item))
    settle(qapp)
    assert view.visualItemRect(long_item).height() == narrow_height
    assert view.model().data(view.indexFromItem(long_item), Qt.DisplayRole) == original_text
    view.setCurrentItem(long_item)
    panel.project_splitter.setSizes([500, 500])
    settle(qapp)
    assert view.visualItemRect(long_item).height() < narrow_height
    panel.refresh()
    assert view.currentItem() is long_item and long_item.data(Qt.UserRole) == project_id


def test_selected_project_title_wraps_in_its_available_width(panel, qapp):
    view = panel.project_list
    view.setCurrentItem(next(view.item(i) for i in range(view.count()) if LONG_PROJECT in view.item(i).text()))
    panel.project_splitter.setSizes([650, 350])
    settle(qapp)
    title = panel.project_title
    assert title.text() == LONG_PROJECT and title.wordWrap()
    assert title.height() >= title.heightForWidth(title.width())


@pytest.mark.parametrize('document_name', [LONG_DOCUMENT,
    'OperationManual_Revision20261028_CompleteReferenceDocument.pdf'])
@pytest.mark.parametrize('narrow_size, wide_size', [
    ((1120, 760), (1120, 760)),
    ((660, 492), (1120, 492)),
])
def test_document_names_wrap_and_reflow_without_replacing_items(
        panel, qapp, document_name, narrow_size, wide_size, record_property):
    panel.resize(*narrow_size)
    pane = panel.document_pane
    pane.project_id = 1
    record = dict(id='work:manual', name=document_name, relative_path=document_name,
                  path=document_name, kind='工作文件',
                  exists=True, size=4096, modified_at=None, zip_id=None, is_directory=False)
    result = dict(project_id=1, workspace_path='', errors=[], documents=[record])
    pane.apply_result(result)
    panel.project_splitter.setSizes([650, 350])
    settle(qapp)
    table = pane.table
    narrow_height = table.rowHeight(0)
    narrow_width = table.columnWidth(0)
    narrow_sizes = panel.project_splitter.sizes()
    assert table.wordWrap() and table.textElideMode() == Qt.ElideNone
    assert_complete_item_text_fits(table, table.item(0, 0))
    item = table.item(0, 0)
    assert item.text() == document_name
    assert table.model().data(table.model().index(0, 0), Qt.DisplayRole) == document_name
    table.selectRow(0)
    panel.project_splitter.setSizes([220, 780])
    settle(qapp)
    assert table.columnWidth(0) > narrow_width
    assert_complete_item_text_fits(table, item)
    # 最小窗口的分栏只有约 8 px 可拖动空间，不保证跨越文字换行边界。
    # 扩大真实窗口后仍严格验证列宽增加、行高缩减，且两种宽度都显示完整文字。
    panel.resize(*wide_size)
    panel.project_splitter.setSizes([220, 780])
    settle(qapp)
    assert table.columnWidth(0) > narrow_width, (
        panel.size(), narrow_sizes, panel.project_splitter.sizes(),
        narrow_width, table.columnWidth(0),
        [panel.project_splitter.widget(i).minimumSizeHint() for i in range(2)])
    assert table.rowHeight(0) < narrow_height, (
        panel.size(), narrow_sizes, panel.project_splitter.sizes(),
        narrow_width, table.columnWidth(0), narrow_height, table.rowHeight(0))
    assert_complete_item_text_fits(table, item)
    assert item.text() == document_name
    assert table.model().data(table.model().index(0, 0), Qt.DisplayRole) == document_name
    record_property('column_widths', f'{narrow_width}->{table.columnWidth(0)}')
    record_property('row_heights', f'{narrow_height}->{table.rowHeight(0)}')
    pane.apply_result(result)
    assert table.item(0, 0) is item and pane.current_document()['id'] == record['id']


def test_project_splitter_reflow_preserves_reading_anchor_away_from_selection(panel, qapp):
    panel.service.db.create_capture('隔离分栏验证', '资料',
                                    [(f'{LONG_PROJECT}{i:03d}', False) for i in range(100)])
    panel.refresh()
    view = panel.project_list
    view.setCurrentRow(0)
    panel.project_splitter.setSizes([500, 500])
    settle(qapp)
    view.scrollToItem(view.item(70), QAbstractItemView.PositionAtTop)
    view.verticalScrollBar().setValue(view.verticalScrollBar().value() + 9)
    item = view.itemAt(QPoint(2, 0))
    anchor = item.data(Qt.UserRole), view.visualItemRect(item).top()
    selected_id = view.currentItem().data(Qt.UserRole)
    for sizes in ([240, 760], [500, 500], [310, 690]):
        panel.project_splitter.setSizes(sizes)
        settle(qapp)
        item = view.itemAt(QPoint(2, 0))
        assert (item.data(Qt.UserRole), view.visualItemRect(item).top()) == anchor
        assert view.currentItem().data(Qt.UserRole) == selected_id


def test_document_splitter_reflow_preserves_reading_anchor_away_from_selection(panel, qapp):
    pane = panel.document_pane
    pane.project_id = 1
    records = [dict(id=f'work:{i}', name=LONG_DOCUMENT, relative_path=f'{i:03d}_{LONG_DOCUMENT}',
                    path=f'{i:03d}_{LONG_DOCUMENT}', kind='工作文件', exists=True, size=4096,
                    modified_at=None, zip_id=None, is_directory=False) for i in range(100)]
    pane.apply_result(dict(project_id=1, workspace_path='', errors=[], documents=records))
    table = pane.table
    table.selectRow(0)
    panel.project_splitter.setSizes([240, 760])
    settle(qapp)
    table.scrollToItem(table.item(60, 0), QAbstractItemView.PositionAtTop)
    table.verticalScrollBar().setValue(table.verticalScrollBar().value() + 9)
    row = table.rowAt(0)
    anchor = table.item(row, 0).data(Qt.UserRole), table.rowViewportPosition(row)
    for sizes in ([650, 350], [240, 760], [400, 600]):
        panel.project_splitter.setSizes(sizes)
        settle(qapp)
        row = table.rowAt(0)
        assert (table.item(row, 0).data(Qt.UserRole), table.rowViewportPosition(row)) == anchor
        assert pane.current_document()['id'] == 'work:0'


def test_changed_project_caption_preserves_reading_anchor_without_reset(panel, qapp):
    from compact_ui import populate
    view = panel.project_list
    rows = [dict(id=i, name=f'项目{i:03d}系统') for i in range(100)]
    populate(view, rows, lambda row: row['name'])
    settle(qapp)
    view.scrollToItem(view.item(60), QAbstractItemView.PositionAtTop)
    view.verticalScrollBar().setValue(view.verticalScrollBar().value() + 9)
    item = view.itemAt(QPoint(2, 0))
    anchor = item.data(Qt.UserRole), view.visualItemRect(item).top()
    rows[0]['name'] = '长' * 200
    populate(view, rows, lambda row: row['name'])
    settle(qapp)
    item = view.itemAt(QPoint(2, 0))
    assert (item.data(Qt.UserRole), view.visualItemRect(item).top()) == anchor


def test_document_width_drag_coalesces_native_row_measurement(panel, qapp, monkeypatch):
    pane = panel.document_pane
    pane.project_id = 1
    records = [dict(id=f'work:{i}', name=LONG_DOCUMENT, relative_path=f'{i:03d}_{LONG_DOCUMENT}',
                    path=LONG_DOCUMENT, kind='工作文件', exists=True, size=4096,
                    modified_at=None, zip_id=None, is_directory=False) for i in range(100)]
    pane.apply_result(dict(project_id=1, workspace_path='', errors=[], documents=records))
    settle(qapp)
    calls = []
    original = pane.table.resizeRowsToContents
    def measured():
        calls.append(True)
        original()
    monkeypatch.setattr(pane.table, 'resizeRowsToContents', measured)
    for sizes in ([650, 350], [240, 760], [400, 600]):
        panel.project_splitter.setSizes(sizes)
        QTest.qWait(10)
    settle(qapp)
    assert len(calls) == 1


def test_window_resize_does_not_reveal_selection_away_from_reading_anchor(panel, qapp):
    panel.service.db.create_capture('隔离分栏验证', '资料',
                                    [(f'{LONG_PROJECT}{i:03d}', False) for i in range(100)])
    panel.refresh()
    view = panel.project_list
    view.setCurrentRow(0)
    settle(qapp)
    view.scrollToItem(view.item(60), QAbstractItemView.PositionAtTop)
    view.verticalScrollBar().setValue(view.verticalScrollBar().value() + 9)
    item = view.itemAt(QPoint(2, 0))
    anchor = item.data(Qt.UserRole), view.visualItemRect(item).top()
    panel.resize(1000, 760)
    settle(qapp)
    item = view.itemAt(QPoint(2, 0))
    assert (item.data(Qt.UserRole), view.visualItemRect(item).top()) == anchor


def test_document_text_refresh_preserves_anchor_after_all_rows_grow(panel, qapp):
    pane = panel.document_pane
    pane.project_id = 1
    records = [dict(id=f'work:{i}', name=f'{i:03d}.txt', relative_path=f'{i:03d}.txt',
                    path=f'{i:03d}.txt', kind='工作文件', exists=True, size=4096,
                    modified_at=None, zip_id=None, is_directory=False) for i in range(100)]
    pane.apply_result(dict(project_id=1, workspace_path='', errors=[], documents=records))
    panel.project_splitter.setSizes([650, 350])
    settle(qapp)
    table = pane.table
    table.scrollToItem(table.item(60, 0), QAbstractItemView.PositionAtTop)
    table.verticalScrollBar().setValue(table.verticalScrollBar().value() + 9)
    row = table.rowAt(0)
    anchor = table.item(row, 0).data(Qt.UserRole), table.rowViewportPosition(row)
    updated = [dict(record, relative_path=f'{i:03d}_{LONG_DOCUMENT}') for i, record in enumerate(records)]
    pane.apply_result(dict(project_id=1, workspace_path='', errors=[], documents=updated))
    settle(qapp)
    row = table.rowAt(0)
    assert (table.item(row, 0).data(Qt.UserRole), table.rowViewportPosition(row)) == anchor


@pytest.mark.parametrize('mode', ['light', 'dark'])
@pytest.mark.parametrize('selection_state', ['unselected', 'selected', 'focused'])
def test_mouse_selected_project_paints_metadata_on_last_line(panel, qapp, mode, selection_state):
    name = '跨部门项目文档收录整理与版本交付协同管理系统'
    panel.service.db.create_capture('隔离实际绘制验证', '资料完整批次元数据', [(name, False)])
    panel.refresh()
    install_theme(panel.service)
    set_theme(mode, persist=False)
    view = panel.project_list
    item = next(view.item(i) for i in range(view.count()) if name in view.item(i).text())
    panel.project_splitter.setSizes([400, 600])
    panel.activateWindow()
    settle(qapp)
    QTest.mouseClick(view.viewport(), Qt.LeftButton, pos=view.visualItemRect(item).center())
    if selection_state == 'unselected':
        other = next(view.item(i) for i in range(view.count()) if view.item(i) is not item)
        QTest.mouseClick(view.viewport(), Qt.LeftButton, pos=view.visualItemRect(other).center())
    if selection_state != 'focused':
        panel.search.setFocus()
    settle(qapp)
    assert item.isSelected() is (selection_state != 'unselected')
    assert view.hasFocus() is (selection_state == 'focused')
    assert item.text().startswith(name + '\n') and '资料' in item.text()
    assert_complete_item_text_fits(view, item)
    # 检查真实标准委托paint的像素：末行元数据应有字形，而不是只有居中的首行省略号。
    # Fluent行背景是透明的；在真实父窗口上合成后检查字形，不能把透明像素当黑底。
    frame = panel.grab().toImage()
    rect = view.visualItemRect(item)
    origin = view.viewport().mapTo(panel, rect.topLeft())
    scale = frame.devicePixelRatio()
    rendered = frame.copy(round(origin.x()*scale), round(origin.y()*scale),
                          round(rect.width()*scale), round(rect.height()*scale))
    background = rendered.pixelColor(rendered.width() // 2, 3)
    pixels = 0
    for y in range(round(rendered.height() * .68), round(rendered.height() * .90)):
        for x in range(20, rendered.width() - 20):
            color = rendered.pixelColor(x, y)
            if max(abs(color.red() - background.red()), abs(color.green() - background.green()),
                   abs(color.blue() - background.blue())) > 100:
                pixels += 1
    assert pixels > 30, (mode, selection_state, rendered.size(), pixels, item.text())
    install_theme()
