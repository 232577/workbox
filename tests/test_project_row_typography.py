"""项目名与详情独立排版，仍使用 Fluent 原有交互状态。"""
import math

import pytest
from PySide6.QtCore import QItemSelectionModel, QPointF, QRect, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QTextLayout, QTextOption
from PySide6.QtWidgets import QApplication, QListWidgetItem, QStyleOptionViewItem
from qfluentwidgets.components.widgets.list_view import ListItemDelegate

import fluent_views


@pytest.fixture(scope='module')
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def project_view(name='设备监测项目', detail='待开发 · 类型：材料'):
    view = fluent_views.WorkbenchListWidget()
    view.setItemDelegate(fluent_views.ProjectListDelegate(view))
    item = QListWidgetItem(name + '\n' + detail)
    item.setData(Qt.UserRole, 731)
    item.setToolTip('原始完整项目说明')
    item.setData(fluent_views.PROJECT_NAME_ROLE, name)
    item.setData(fluent_views.PROJECT_DETAIL_ROLE, detail)
    view.addItem(item)
    return view, item, view.model().index(0, 0)


def option_for(view, width, height=2000):
    option = QStyleOptionViewItem()
    option.initFrom(view)
    option.rect = QRect(0, 0, width, height)
    return option


def test_real_project_list_populates_separate_text_and_updates_in_place(qapp,tmp_path):
    from compact_ui import CompactPanel
    from desktop_service import DesktopService
    service = DesktopService(tmp_path/'data/workbox.db')
    service.db.create_capture('文字层级','隔离批次',[('项目名称验证系统',False)])
    panel = CompactPanel(service)
    try:
        item = panel.project_list.item(0)
        panel.project_list.setCurrentItem(item)
        assert isinstance(panel.project_list.delegate,fluent_views.ProjectListDelegate)
        assert item.data(fluent_views.PROJECT_NAME_ROLE)=='项目名称验证系统'
        assert '待开发' in item.data(fluent_views.PROJECT_DETAIL_ROLE)
        assert item.text()==item.data(fluent_views.PROJECT_NAME_ROLE)+'\n'+item.data(fluent_views.PROJECT_DETAIL_ROLE)
        service.set_status(1,'开发中')
        panel.refresh()
        assert panel.project_list.item(0) is item and item.isSelected()
        assert '开发中' in item.data(fluent_views.PROJECT_DETAIL_ROLE)
        assert item.data(Qt.UserRole)==1
    finally:
        panel.document_pane.shutdown()
        panel.hide()


def measured_height(text, font, width):
    layout = QTextLayout(text.replace('\n', '\u2028'), font)
    options = QTextOption()
    options.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
    layout.setTextOption(options)
    layout.beginLayout()
    height = 0
    while True:
        line = layout.createLine()
        if not line.isValid():
            break
        line.setLineWidth(width)
        line.setPosition(QPointF(0, height))
        height += line.height()
    layout.endLayout()
    return height


def test_independent_roles_keep_display_tooltip_and_identity(qapp):
    assert fluent_views.PROJECT_NAME_ROLE == int(Qt.UserRole) + 10
    assert fluent_views.PROJECT_DETAIL_ROLE == int(Qt.UserRole) + 11
    view, item, index = project_view()
    original = (item.text(), item.toolTip(), item.data(Qt.UserRole))
    option = option_for(view, 230)
    view.delegate.initStyleOption(option, index)
    view.delegate.sizeHint(option, index)
    assert original == (item.text(), item.toolTip(), item.data(Qt.UserRole))
    assert isinstance(view.delegate, ListItemDelegate)


@pytest.mark.parametrize('name', [
    '隔离测试项目9系统' * 4,
    'LongProjectNameWithoutAnyBreaks_20261030' * 5,
])
def test_narrow_rows_cover_both_text_blocks_without_elision(qapp, name):
    detail = '待交付 · 项目类型：长类型完整说明\n归档材料尚未确认' * 2
    view, item, index = project_view(name, detail)
    wide = view.delegate.sizeHint(option_for(view, 360), index)
    narrow = view.delegate.sizeHint(option_for(view, 180), index)
    name_font = QFont(view.font())
    name_font.setPixelSize(13)
    name_font.setWeight(QFont.DemiBold)
    detail_font = QFont(view.font())
    detail_font.setPixelSize(11)
    detail_font.setWeight(QFont.Normal)
    required = measured_height(name, name_font, 180 - 28)
    required += 4 + measured_height(detail, detail_font, 180 - 28)
    required += 2 * (view.delegate.margin + 4)
    assert narrow.height() >= math.ceil(required)
    assert narrow.height() > wide.height()
    assert item.text() == name + '\n' + detail


@pytest.mark.parametrize('mode', ['light', 'dark'])
@pytest.mark.parametrize('enabled', [True, False])
def test_offscreen_paint_uses_distinct_fonts_theme_colors_and_library_background(
        qapp, monkeypatch, mode, enabled):
    import theme
    colors = theme.tokens(mode)
    monkeypatch.setattr(theme, 'tokens', lambda: colors)
    view, item, index = project_view()
    if not enabled:
        item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
    captured = []
    original_layout = fluent_views.QTextLayout

    class RecordingLayout(original_layout):
        def __init__(self, text, font, *args):
            captured.append((text, QFont(font)))
            super().__init__(text, font, *args)

    monkeypatch.setattr(fluent_views, 'QTextLayout', RecordingLayout)
    background_calls = []
    background_texts = []
    original_paint = ListItemDelegate.paint

    def background_paint(delegate, painter, option, index):
        background_calls.append(delegate.selectedRows.copy())
        text_option = QStyleOptionViewItem(option)
        delegate.initStyleOption(text_option, index)
        background_texts.append(text_option.text)
        return original_paint(delegate, painter, option, index)

    monkeypatch.setattr(ListItemDelegate, 'paint', background_paint)
    view.setCurrentRow(0)
    option = option_for(view, 240, 100)
    image = QImage(240, 100, QImage.Format_ARGB32)
    image.fill(QColor(colors['surface']))
    painter = QPainter(image)
    try:
        view.delegate.paint(painter, option, index)
    finally:
        painter.end()
    fonts = {text: font for text, font in captured}
    name_font = fonts[item.data(fluent_views.PROJECT_NAME_ROLE)]
    detail_font = fonts[item.data(fluent_views.PROJECT_DETAIL_ROLE)]
    assert (name_font.pixelSize(), name_font.weight()) == (13, QFont.DemiBold)
    assert (detail_font.pixelSize(), detail_font.weight()) == (11, QFont.Normal)
    assert name_font.family() == detail_font.family() == view.font().family()
    assert background_calls == ([{0}] if enabled else [set()])
    assert background_texts == ['']
    restored = QStyleOptionViewItem(option)
    view.delegate.initStyleOption(restored, index)
    assert restored.text.replace('\u2028', '\n') == item.text()
    pixels = {image.pixelColor(x, y).name() for x in range(14, 226) for y in range(100)}
    for role in (('ink', 'muted') if enabled else ('disabled',)):
        assert QColor(colors[role]).name() in pixels
    assert option.rect == QRect(0, 0, 240, 100)


def test_selection_model_keeps_fluent_selected_rows_in_sync(qapp):
    view, _, _ = project_view()
    view.addItem('另一个项目')
    view.selectionModel().select(view.model().index(1, 0), QItemSelectionModel.Select)
    assert view.delegate.selectedRows == {1}
    view.clearSelection()
    assert view.delegate.selectedRows == set()


def test_empty_placeholder_retains_original_wrapped_delegate(qapp):
    view = fluent_views.WorkbenchListWidget()
    view.setItemDelegate(fluent_views.ProjectListDelegate(view))
    view.addItem('暂无项目，请创建项目')
    index = view.model().index(0, 0)
    option = option_for(view, 220)
    baseline = fluent_views.WrappedFluentListDelegate(view)
    assert view.delegate.sizeHint(option, index) == baseline.sizeHint(option, index)
