"""真实 Fluent 列表和表格，保留 Qt 模型、原生滚动条与完整名称。

滚动适配依赖 PySide6-Fluent-Widgets==1.11.3 的内部接管方式；升级时必须
重新运行视图生命周期、拖动和换行回归。对象均随父视图销毁，不拆删库滚动对象。
"""
from __future__ import annotations

import math
import re

from PySide6.QtCore import QPointF, QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QTextLayout, QTextOption
from PySide6.QtWidgets import QAbstractItemView, QListView, QStyle, QStyleOptionViewItem, QWidget
from qfluentwidgets import ListWidget, SmoothMode, TableWidget, setCustomStyleSheet
from qfluentwidgets.components.widgets.list_view import ListItemDelegate
from qfluentwidgets.components.widgets.table_view import TableItemDelegate


PROJECT_NAME_ROLE = int(Qt.UserRole) + 10
PROJECT_DETAIL_ROLE = int(Qt.UserRole) + 11


def _fluent_item_styles(selector: str, horizontal_padding: int, color: str) -> str:
    """隔离父窗口旧 item QSS；背景和选择指示仍由库委托绘制。"""
    states = ('', ':hover', ':focus', ':selected', ':selected:hover', ':selected:focus',
              ':hover:focus', ':selected:active', ':selected:!active', ':!selected:hover',
              ':selected:active:focus', ':selected:!active:focus')
    items = ',\n'.join(f'{selector}::item{state}' for state in states)
    header = 'QHeaderView::section { padding: 0px 5px; }' if selector == 'QTableView' else ''
    return f'''{selector} {{
        color: {color}; selection-color: {color};
        selection-background-color: transparent; alternate-background-color: transparent;
    }}
    {items} {{
        height: auto; padding: 0px {horizontal_padding}px; border: none;
        background: transparent; color: {color}; selection-color: {color};
        selection-background-color: transparent;
    }}
    {header}'''


def configure_native_scroll(widget):
    """关闭库的滚动接管，恢复现有 Qt 滚动条；可重复调用。"""
    delegate = getattr(widget, 'scrollDelegate', None) or getattr(widget, 'scrollDelagate', None)
    if delegate is None:
        raise TypeError('此滚动适配只用于 Fluent 列表和表格。')
    if not getattr(widget, '_workbox_native_scroll', False):
        delegate.verticalSmoothScroll.setSmoothMode(SmoothMode.NO_SMOOTH)
        delegate.horizonSmoothScroll.setSmoothMode(SmoothMode.NO_SMOOTH)
        widget.viewport().removeEventFilter(delegate)
        for overlay in (delegate.vScrollBar, delegate.hScrollBar):
            widget.removeEventFilter(overlay)
            overlay.setForceHidden(True)
            overlay.ani.stop()
            overlay.groove.opacityAni.stop()
        # 1.11.3 在实例上覆写策略方法。删除后由 Qt 原生方法设置策略。
        for name in ('setVerticalScrollBarPolicy', 'setHorizontalScrollBarPolicy'):
            if name in widget.__dict__:
                delattr(widget, name)
        widget.horizontalScrollBar().setStyleSheet('')
        widget._workbox_native_scroll = True
    widget.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
    widget.setHorizontalScrollBarPolicy(
        Qt.ScrollBarAsNeeded if isinstance(widget, TableWidget) else Qt.ScrollBarAlwaysOff)
    widget.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
    widget.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
    for bar in (widget.verticalScrollBar(), widget.horizontalScrollBar()):
        bar.setTracking(True)
    return delegate


class _WrappedFluentText:
    """只改显示选项与尺寸，库原生绘制、交互状态和模型原文保持不变。"""

    def _wraps(self, index):
        return True

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        # 库更换显示字体后 Qt 的 metrics 仍可能属于视图原字体；测绘必须一致。
        option.fontMetrics = QFontMetrics(option.font)
        if not self._wraps(index):
            option.features &= ~QStyleOptionViewItem.WrapText
            return
        # 长英文的断行机会只用于绘制，不写入名称、复制文本或工具提示。
        option.text = re.sub(r'[A-Za-z0-9_./\\-]{12,}',
                             lambda match: '\u200b'.join(match.group()), option.text)
        option.features |= QStyleOptionViewItem.WrapText
        option.textElideMode = Qt.ElideNone

    def sizeHint(self, option, index):
        if not self._wraps(index):
            return super().sizeHint(option, index)
        view = self.parent()
        option = QStyleOptionViewItem(option)
        is_list = isinstance(view, ListWidget)
        width = view.viewport().width() if is_list else view.columnWidth(index.column())
        self.initStyleOption(option, index)
        # 1.11.3 QSS 的左右内缩分别是 list 11px、table 16px。
        # 不调用 QSS subElementRect：混合委托布局时该路径曾导致 native AV。
        # QStyledItemDelegate 在 QSS padding 内另留标准 focus frame 文本边距。
        text_width = max(1, width - (22 if is_list else 32) - 6)
        if index.data(Qt.CheckStateRole) is not None:
            text_width = max(1, text_width - 24)
        if index.data(Qt.DecorationRole) is not None:
            text_width = max(1, text_width - option.decorationSize.width() - 4)
        option.rect.setWidth(text_width)
        size = super().sizeHint(option, index)
        measured = QFontMetrics(option.font).boundingRect(
            QRect(0, 0, text_width, 100000), Qt.TextWordWrap, option.text)
        size.setHeight(max(size.height(), measured.height() + 2 * self.margin + 8))
        if is_list:
            size.setWidth(width)
        return size


class WrappedFluentListDelegate(_WrappedFluentText, ListItemDelegate):
    """列表保留真正的 Fluent 选中、悬停、按下绘制。"""


class ProjectListDelegate(WrappedFluentListDelegate):
    """项目名和详情独立换行；背景、指示条和交互状态仍由 Fluent 负责。"""

    horizontalInset = 14
    blockSpacing = 4
    verticalPadding = 4

    def __init__(self, parent):
        super().__init__(parent)
        self._painting_background = False

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        if self._painting_background:
            # QStyledItemDelegate.paint 会重新初始化选项，必须在此处隐藏原合并文本。
            option.text = ''

    def _text_layout(self, text, font, width):
        # QTextLayout 用行分隔符表达显式换行；模型里的原文字不变。
        layout = QTextLayout(text.replace('\n', '\u2028'), font)
        text_option = QTextOption()
        text_option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        layout.setTextOption(text_option)
        layout.beginLayout()
        height = 0.0
        while True:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(max(1, width))
            line.setPosition(QPointF(0, height))
            height += line.height()
        layout.endLayout()
        return layout, height

    def _text_blocks(self, index, width):
        font = QFont(self.parent().font())
        font.setPixelSize(13)
        font.setWeight(QFont.DemiBold)
        name = self._text_layout(str(index.data(PROJECT_NAME_ROLE)), font, width)
        font.setPixelSize(11)
        font.setWeight(QFont.Normal)
        detail = index.data(PROJECT_DETAIL_ROLE) or ''
        return name, self._text_layout(str(detail), font, width)

    def sizeHint(self, option, index):
        if index.data(PROJECT_NAME_ROLE) is None:
            return super().sizeHint(option, index)
        width = option.rect.width()
        if width <= 0:
            width = self.parent().viewport().width()
        name, detail = self._text_blocks(index, width - 2 * self.horizontalInset)
        spacing = self.blockSpacing if detail[1] else 0
        height = math.ceil(name[1] + spacing + detail[1])
        return QSize(width, height + 2 * (self.margin + self.verticalPadding))

    def paint(self, painter, option, index):
        if index.data(PROJECT_NAME_ROLE) is None:
            return super().paint(painter, option, index)
        # 库会调整传入的 rect；独立副本保护后续文字测绘与调用方。
        background_option = QStyleOptionViewItem(option)
        self._painting_background = True
        try:
            super().paint(painter, background_option, index)
        finally:
            self._painting_background = False

        from theme import tokens
        colors = tokens()
        enabled = bool(option.state & QStyle.State_Enabled and index.flags() & Qt.ItemIsEnabled)
        name_color = colors['ink'] if enabled else colors['disabled']
        detail_color = colors['muted'] if enabled else colors['disabled']
        name, detail = self._text_blocks(index, option.rect.width() - 2 * self.horizontalInset)
        position = QPointF(option.rect.left() + self.horizontalInset,
                          option.rect.top() + self.margin + self.verticalPadding)
        painter.save()
        try:
            painter.setClipRect(option.rect, Qt.IntersectClip)
            painter.setPen(QColor(name_color))
            name[0].draw(painter, position)
            if detail[1]:
                position.setY(position.y() + name[1] + self.blockSpacing)
                painter.setPen(QColor(detail_color))
                detail[0].draw(painter, position)
        finally:
            painter.restore()


class WrappedFluentTableDelegate(_WrappedFluentText, TableItemDelegate):
    """整张表共用一个交互状态，仅指定列按内容换行。"""

    def __init__(self, parent):
        super().__init__(parent)
        self.wrapColumns = set()

    def _wraps(self, index):
        return index.column() in self.wrapColumns


class WorkbenchListWidget(ListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setItemDelegate(WrappedFluentListDelegate(self))
        self.setUniformItemSizes(False)
        self.setWordWrap(True)
        self.setTextElideMode(Qt.ElideNone)
        self.setResizeMode(QListView.Adjust)
        # 库内置的固定35px行高不适用于业务长名称。
        setCustomStyleSheet(self, _fluent_item_styles('QListView', 11, '#172B3A'),
                            _fluent_item_styles('QListView', 11, '#F0F4F8'))
        configure_native_scroll(self)
        # 业务刷新会直接改Qt模型/选择，库的鼠标回调之外也同步其选中绘制状态。
        self.selectionModel().selectionChanged.connect(self._sync_selection)
        self.model().modelReset.connect(self._sync_selection)

    def _sync_selection(self, *_):
        self.updateSelectedRows()


class WorkbenchTableWidget(TableWidget):
    def __init__(self, rows=0, columns=0, parent=None):
        # 兼容原 QTableWidget(rows, columns, parent) 与单 parent 构造。
        if isinstance(rows, QWidget) or rows is None:
            parent, rows = rows, 0
        super().__init__(parent)
        self.setRowCount(rows)
        self.setColumnCount(columns)
        self.setItemDelegate(WrappedFluentTableDelegate(self))
        self.setWordWrap(True)
        self.setTextElideMode(Qt.ElideNone)
        setCustomStyleSheet(self, _fluent_item_styles('QTableView', 16, '#172B3A'),
                            _fluent_item_styles('QTableView', 16, '#F0F4F8'))
        configure_native_scroll(self)
        self.selectionModel().selectionChanged.connect(self._sync_selection)
        self.model().modelReset.connect(self._sync_selection)

    def _sync_selection(self, *_):
        self.updateSelectedRows()

    def setWrapColumns(self, columns: set[int]):
        self.delegate.wrapColumns = set(columns)
        self.resizeRowsToContents()
        self.viewport().update()
