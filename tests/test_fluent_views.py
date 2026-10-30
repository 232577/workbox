"""真实 Fluent 视图保留 Qt 模型、原生滚动和完整名称显示。"""
import importlib

import pytest
import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent, QItemSelectionModel, QRect, Qt
from PySide6.QtGui import QAccessible, QFontDatabase, QFontMetrics, QTextLayout, QTextOption
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication, QListWidgetItem, QScrollBar, QStyleOptionViewItem, QTableWidgetItem
from qfluentwidgets import ListWidget, TableWidget, Theme, setTheme
from qfluentwidgets.components.widgets.list_view import ListItemDelegate
from qfluentwidgets.components.widgets.table_view import TableItemDelegate


@pytest.fixture(scope='module')
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    for path in ('C:/Windows/Fonts/msyh.ttc', 'C:/Windows/Fonts/segoeui.ttf'):
        QFontDatabase.addApplicationFont(path)
    return app


def views():
    return importlib.import_module('fluent_views')


def fill(view, count=30):
    text = '项目材料与设备维护_' + 'Long_Project_File_Name_20261029_' * 3
    if isinstance(view, ListWidget):
        for row in range(count):
            item = QListWidgetItem(f'{row:03d} {text}\n待交付 · 材料')
            item.setData(Qt.UserRole, row + 100)
            item.setToolTip(text)
            view.addItem(item)
    else:
        view.setRowCount(count)
        view.setColumnCount(2)
        view.setColumnWidth(0, 200)
        view.setColumnWidth(1, 80)
        view.setWrapColumns({0})
        for row in range(count):
            for column, value in enumerate((text + '.docx', '工作文件')):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, row + 100)
                item.setToolTip(text)
                view.setItem(row, column, item)
        view.resizeRowsToContents()
    return text


@pytest.mark.parametrize('kind', ['list', 'table'])
def test_actual_fluent_class_model_roles_and_shared_state(qapp, kind):
    module = views()
    view = module.WorkbenchListWidget() if kind == 'list' else module.WorkbenchTableWidget(0, 2)
    view.resize(320, 240)
    model = view.model()
    original = fill(view)
    view.show()
    qapp.processEvents()
    assert isinstance(view, ListWidget if kind == 'list' else TableWidget)
    assert isinstance(view.delegate, ListItemDelegate if kind == 'list' else TableItemDelegate)
    assert view.itemDelegate() is view.delegate
    assert view.model() is model
    item = view.item(0) if kind == 'list' else view.item(0, 0)
    assert item.data(Qt.UserRole) == 100 and item.toolTip() == original
    assert '\u200b' not in item.text()
    assert QAccessible.queryAccessibleInterface(view).role() in (QAccessible.List, QAccessible.Table)
    if kind == 'list':
        view.setCurrentRow(2)
    else:
        view.selectRow(2)
    view._setHoverRow(2)
    view._setPressedRow(3)
    view.updateSelectedRows()
    assert view.delegate.hoverRow == 2 and view.delegate.pressedRow == 3
    assert 2 in view.delegate.selectedRows


def test_table_model_selection_and_refresh_keep_fluent_highlight(qapp):
    table = views().WorkbenchTableWidget(3, 2)
    fill(table, 3)
    for row in (1, 2):
        table.clearSelection()
        table.selectionModel().select(table.model().index(row, 0),
                                      QItemSelectionModel.Select | QItemSelectionModel.Rows)
        table.selectionModel().setCurrentIndex(table.model().index(row, 0), QItemSelectionModel.NoUpdate)
        assert table.delegate.selectedRows == {row}
    table.setRowCount(0)
    assert table.delegate.selectedRows == set()


@pytest.mark.parametrize('kind', ['list', 'table'])
def test_native_scroll_objects_tracking_and_released_signal(qapp, kind):
    module = views()
    view = module.WorkbenchListWidget() if kind == 'list' else module.WorkbenchTableWidget()
    view.resize(320, 240)
    fill(view)
    view.show()
    qapp.processEvents()
    bar = view.verticalScrollBar()
    module.configure_native_scroll(view)
    module.configure_native_scroll(view)
    assert type(bar) is QScrollBar and view.verticalScrollBar() is bar
    assert view.verticalScrollBarPolicy() == Qt.ScrollBarAlwaysOn
    assert bar.hasTracking() and bar.maximum() > 0
    released = QSignalSpy(bar.sliderReleased)
    bar.setSliderDown(True)
    for value in (bar.maximum() // 3, bar.maximum() // 2):
        bar.setSliderPosition(value)
        assert bar.isSliderDown() and bar.value() == value
    bar.setSliderDown(False)
    assert released.count() == 1


@pytest.mark.parametrize('kind', ['list', 'table'])
def test_full_long_text_height_covers_qt_text_layout_without_model_edits(qapp, kind):
    module = views()
    view = module.WorkbenchListWidget() if kind == 'list' else module.WorkbenchTableWidget()
    view.resize(300, 240)
    fill(view, 2)
    view.show()
    qapp.processEvents()
    if kind == 'list':
        view.doItemsLayout()
    else:
        view.resizeRowsToContents()
    index = view.model().index(0, 0)
    option = QStyleOptionViewItem()
    view.delegate.initStyleOption(option, index)
    assert '\u200b' in option.text
    width = view.viewport().width() - 22 if kind == 'list' else view.columnWidth(0) - 32
    text_option = QTextOption()
    text_option.setWrapMode(QTextOption.WordWrap)
    required = 0
    for paragraph in option.text.split('\n'):
        layout = QTextLayout(paragraph, option.font)
        layout.setTextOption(text_option)
        layout.beginLayout()
        while True:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(width)
            required += line.height()
        layout.endLayout()
    height = view.visualItemRect(view.item(0)).height() if kind == 'list' else view.rowHeight(0)
    assert height >= required + 2 * view.delegate.margin
    assert '\u200b' not in index.data(Qt.DisplayRole)
    if kind == 'table':
        plain = QStyleOptionViewItem()
        view.delegate.initStyleOption(plain, view.model().index(0, 1))
        assert not plain.features & QStyleOptionViewItem.WrapText


def test_chinese_filename_at_wrap_boundary_includes_qt_text_margins(qapp):
    table = views().WorkbenchTableWidget(1, 1)
    table.setWrapColumns({0})
    table.setColumnWidth(0, 300)
    name = '图像采集与状态统一监管平台_软件设计说明书与项目资料_终稿版本_2026年10月29日.docx'
    table.setItem(0, 0, QTableWidgetItem(name))
    table.resizeRowsToContents()
    option = QStyleOptionViewItem()
    table.delegate.initStyleOption(option, table.model().index(0, 0))
    # QSS 左右32px之外，Qt 文本绘制还保留每边3px。此名称恰好跨过两行边界。
    required = QFontMetrics(option.font).boundingRect(
        QRect(0, 0, 300 - 32 - 6, 100000), Qt.TextWordWrap, option.text).height()
    assert table.rowHeight(0) >= required + 2 * table.delegate.margin
    assert table.item(0, 0).text() == name


@pytest.mark.parametrize('mode, foreground', [('light', '#172b3a'), ('dark', '#f0f4f8')])
@pytest.mark.parametrize('kind', ['list', 'table'])
def test_real_workbench_styles_keep_selected_text_and_metadata_readable(qapp, tmp_path, mode, foreground, kind):
    from PySide6.QtGui import QColor
    from PySide6.QtTest import QTest
    from compact_ui import CompactPanel
    from desktop_service import DesktopService
    from theme import install_theme, set_theme

    service = DesktopService(tmp_path / 'data/workbox.db')
    name = '跨部门项目文档收录整理与版本交付协同管理系统'
    service.db.create_capture('独立界面验证', '资料', [(name, False), ('短名称工具', False)])
    install_theme(service)
    panel = CompactPanel(service)
    panel.resize(1200, 780)
    panel.show()
    panel.project_splitter.setSizes([380, 620])
    set_theme(mode, persist=False)
    QTest.qWait(130)
    view = panel.project_list
    item = next(view.item(i) for i in range(view.count()) if name in view.item(i).text())
    if kind == 'table':
        pane = panel.document_pane
        pane.shutdown()
        pane.project_id = 1
        filename = name + '_OperationManual_Complete_Revision20261029.docx'
        record = dict(id='fixture-manual', name=filename, relative_path=filename,
                      path='fixture/' + filename, kind='工作文件', exists=True, size=15,
                      modified_at=None, zip_id=None, is_directory=False)
        pane.apply_result(dict(project_id=1, workspace_path='fixture', errors=[], documents=[record]))
        view = pane.table
        item = view.item(0, 0)
    QTest.mouseClick(view.viewport(), Qt.LeftButton, pos=view.visualItemRect(item).center())
    QTest.qWait(130)
    assert item.isSelected() and view.hasFocus()
    index = view.indexFromItem(item)
    option = QStyleOptionViewItem()
    view.initViewItemOption(option)
    view.delegate.initStyleOption(option, index)
    width = view.viewport().width() - 22 - 6 if kind == 'list' else view.columnWidth(0) - 32 - 6
    required = QFontMetrics(option.font).boundingRect(
        QRect(0, 0, width, 100000), Qt.TextWordWrap, option.text).height()
    # 从整窗截图裁取，确保透明 Fluent 委托与实际父窗口底色已经合成。
    frame = panel.grab().toImage()
    item_rect = view.visualItemRect(item)
    origin = view.viewport().mapTo(panel, item_rect.topLeft())
    scale = frame.devicePixelRatio()
    rendered = frame.copy(QRect(round(origin.x() * scale), round(origin.y() * scale),
                               round(item_rect.width() * scale), round(item_rect.height() * scale)))
    assert rendered.height() / rendered.devicePixelRatio() >= required + 2 * view.delegate.margin
    expected = QColor(foreground)
    def same_ink(color):
        return max(abs(color.red() - expected.red()), abs(color.green() - expected.green()),
                   abs(color.blue() - expected.blue())) < 30
    ink = sum(same_ink(rendered.pixelColor(x, y))
              for y in range(rendered.height()) for x in range(20, rendered.width() - 20))
    assert ink > 100, (mode, ink, item.text())
    # 用真实像素验证末行元数据，不能只测委托返回的尺寸。
    last_ink = sum(same_ink(rendered.pixelColor(x, y))
                  for y in range(round(rendered.height() * .68), round(rendered.height() * .92))
                  for x in range(20, rendered.width() - 20))
    assert last_ink > 20, (mode, last_ink, item.text())
    background = rendered.pixelColor(rendered.width() // 2, round(3 * rendered.devicePixelRatio()))
    def luminance(color):
        channels = [value / 255 for value in (color.red(), color.green(), color.blue())]
        linear = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4
                  for value in channels]
        return sum(weight * value for weight, value in zip((.2126, .7152, .0722), linear))
    high, low = sorted((luminance(expected), luminance(background)), reverse=True)
    assert (high + .05) / (low + .05) >= 4.5, (mode, background.name())
    panel.document_pane.shutdown()
    panel.hide()
    install_theme()


@pytest.mark.parametrize('kind', ['list', 'table'])
def test_real_workbench_mouse_drag_uses_original_qt_scrollbar(qapp, tmp_path, kind):
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QStyle, QStyleOptionSlider
    from compact_ui import CompactPanel
    from desktop_service import DesktopService
    from theme import install_theme

    service = DesktopService(tmp_path / 'data/workbox.db')
    service.db.create_capture('独立鼠标拖动验证', '资料',
                              [(f'项目材料归档与状态维护工具{index:03d}', False) for index in range(80)])
    install_theme(service)
    panel = CompactPanel(service)
    panel.resize(1200, 780)
    panel.show()
    QTest.qWait(130)
    pane = panel.document_pane
    pane.shutdown()
    if kind == 'list':
        view = panel.project_list
        selected = view.item(2)
    else:
        pane.project_id = 1
        documents = [dict(id=f'fixture-{index}', name=f'文件{index:03d}.txt',
                          relative_path=f'文件{index:03d}.txt', path=f'fixture/文件{index:03d}.txt',
                          kind='工作文件', exists=True, size=15, modified_at=None,
                          zip_id=None, is_directory=False) for index in range(80)]
        pane.apply_result(dict(project_id=1, workspace_path='fixture', errors=[], documents=documents))
        view = pane.table
        selected = view.item(2, 0)
    view.scrollToItem(selected)
    QTest.mouseClick(view.viewport(), Qt.LeftButton, pos=view.visualItemRect(selected).center())
    assert selected.isSelected()
    selected_id = selected.data(Qt.UserRole)
    bar = view.verticalScrollBar()
    original = bar
    bar.setValue(bar.maximum() // 3)
    QTest.qWait(30)
    option = QStyleOptionSlider()
    bar.initStyleOption(option)
    handle = bar.style().subControlRect(QStyle.CC_ScrollBar, option, QStyle.SC_ScrollBarSlider, bar)
    pressed, released = QSignalSpy(bar.sliderPressed), QSignalSpy(bar.sliderReleased)
    QTest.mousePress(bar, Qt.LeftButton, pos=handle.center())
    assert bar.isSliderDown() and pressed.count() == 1
    before = bar.value()
    QTest.mouseMove(bar, handle.center() + QPoint(0, 50), delay=20)
    assert bar.value() > before and bar.isSliderDown()
    QTest.mouseRelease(bar, Qt.LeftButton, pos=handle.center() + QPoint(0, 50))
    assert not bar.isSliderDown() and released.count() == 1
    assert view.verticalScrollBar() is original
    assert selected.isSelected() and selected.data(Qt.UserRole) == selected_id
    panel.hide()
    install_theme()


def test_repeated_native_lifetimes_and_theme_changes(qapp):
    module = views()
    for cycle in range(105):
        listing = module.WorkbenchListWidget()
        table = module.WorkbenchTableWidget(0, 2)
        for view in (listing, table):
            view.resize(320, 220)
            fill(view, 8)
            view.show()
        qapp.processEvents()
        listing.setCurrentRow(3)
        table.selectRow(3)
        for theme in (Theme.DARK, Theme.LIGHT):
            setTheme(theme, save=False)
            qapp.processEvents()
            assert listing.currentItem().data(Qt.UserRole) == 103
            assert 3 in table.delegate.selectedRows
        for view in (listing, table):
            view.hide()
            view.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        qapp.processEvents()
        assert not shiboken6.isValid(listing) and not shiboken6.isValid(table)
