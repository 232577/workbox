"""真实 Fluent 列表和表格，保留 Qt 模型、原生滚动条与完整名称。

滚动适配依赖 PySide6-Fluent-Widgets==1.11.3 的内部接管方式；升级时必须
重新运行视图生命周期、拖动和换行回归。对象均随父视图销毁，不拆删库滚动对象。
"""
from __future__ import annotations

import math
import re

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPalette, QPen, QTextCharFormat, QTextLayout, QTextOption
from PySide6.QtWidgets import QAbstractItemView, QListView, QStyle, QStyleOptionViewItem, QWidget
from qfluentwidgets import ListWidget, SmoothMode, TableWidget, setCustomStyleSheet
from qfluentwidgets.components.widgets.list_view import ListItemDelegate
from qfluentwidgets.components.widgets.table_view import TableItemDelegate


PROJECT_NAME_ROLE = int(Qt.UserRole) + 10
PROJECT_DETAIL_ROLE = int(Qt.UserRole) + 11
PROJECT_BATCH_MARKERS_ROLE = int(Qt.UserRole) + 12
ITEM_TEXT_TONE_ROLE = int(Qt.UserRole) + 13

PROJECT_STATUS_TONES = {'待开发': 'state_new', '开发中': 'state_working',
                        '待交付': 'state_pending', '已交付': 'state_delivered'}


def _batch_badge_background(color, mode):
    from theme import tokens
    base, accent = QColor(tokens(mode)['surface']), QColor(color)
    if not accent.isValid():
        accent = QColor('#777777')
    weight = .22 if mode == 'dark' else .10
    return QColor(*(round(base_channel * (1 - weight) + accent_channel * weight)
                    for base_channel, accent_channel in zip(base.getRgb()[:3], accent.getRgb()[:3])))


def _batch_progress_counts(marker):
    counts = tuple(marker.get(field) for field in (
        'total_count', 'delivered_count', 'remaining_count', 'pending_delivery_count'))
    if (all(type(value) is int and value >= 0 for value in counts)
            and counts[0] == counts[1] + counts[2] and counts[3] <= counts[2]):
        total, delivered, undelivered, pending = counts
        return total, delivered, pending, undelivered - pending
    return None


def batch_progress_summary(marker):
    counts = _batch_progress_counts(marker)
    if counts is None:
        return ''
    total, delivered, pending, remaining = counts
    if total == 0:
        return '本批无有效项目'
    return f'已交付{delivered}/{total}，待交付{pending}，剩余{remaining}'


def _batch_badge_parts(marker):
    code = str(marker.get('label') or marker.get('code') or '')
    counts = _batch_progress_counts(marker)
    if counts is None:
        return code, ''
    total, delivered, pending, remaining = counts
    if total == 0:
        return code, ' 无项目'
    if delivered == total:
        return code, ' 已齐'
    return code, f' 余{remaining}' if remaining else f' 待交付{pending}'


def _batch_badge_layout(markers, metrics, width):
    """优先保留完整编号；极窄时仍保留第一枚标记和更多数量。"""
    badges = []
    available = max(0, width)
    for marker in markers[:2]:
        code, progress = _batch_badge_parts(marker)
        badge_width = 24 + metrics.horizontalAdvance(code + progress)
        remaining = len(markers) - len(badges) - 1
        summary = f'+{remaining}批' if remaining else ''
        reserve = 5 + metrics.horizontalAdvance(summary) if summary else 0
        if badge_width + reserve > available:
            if not badges and available - reserve > 24:
                badges.append((marker, available - reserve))
            break
        badges.append((marker, badge_width))
        available -= badge_width + 5
    extra = len(markers) - len(badges)
    return badges, (f'+{extra}批' if badges and extra else f'{extra}批' if extra else '')


def _fluent_item_styles(selector: str, horizontal_padding: int, mode: str, *, use_item_palette=False) -> str:
    """局部覆盖 Fluent 和父窗口样式；行状态由库委托钩子统一绘制。"""
    from theme import tokens
    colors = tokens(mode)
    color = colors['ink']
    item_color = '' if use_item_palette else f'color: {color}; selection-color: {color};'
    states = ('', ':hover', ':focus', ':selected', ':selected:hover', ':selected:focus',
              ':hover:focus', ':selected:active', ':selected:!active', ':!selected:hover',
              ':selected:active:focus', ':selected:!active:focus')
    items = ',\n'.join(f'{selector}::item{state}' for state in states)
    header = f'''
    QHeaderView {{ background: {colors['subtle_surface']}; border: none; }}
    QHeaderView::section, QHeaderView::section:horizontal {{
        color: {colors['muted']}; background: {colors['subtle_surface']};
        border: none; border-bottom: 1px solid {colors['border']};
        height: 18px; padding: 6px 16px; font-size: 12px; font-weight: 600;
    }}
    QHeaderView::section:hover {{ background: {colors['hover']}; }}
    QTableCornerButton::section {{ background: {colors['surface']}; border: none; }}
    ''' if selector == 'QTableView' else ''
    project_background = 'QListView#projectList { background: transparent; }' if selector == 'QListView' else ''
    return f'''{selector} {{
        color: {color}; selection-color: {color}; background: {colors['surface']};
        selection-background-color: transparent; alternate-background-color: transparent;
    }}
    {items} {{
        height: auto; padding: 0px {horizontal_padding}px; border: none;
        background: transparent; {item_color}
        selection-background-color: transparent;
    }}
    {header}
    {project_background}'''


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


class _WorkbenchRowStates:
    """沿用 Fluent 的交互状态，以产品语义色替换默认灰底与侧边粗条。"""

    def _idle_row_color(self, colors):
        return colors['surface']

    def _row_rule_inset(self):
        return 15 if isinstance(self.parent(), TableWidget) else None

    def _row_rect(self, option):
        view = self.parent()
        if isinstance(view, TableWidget):
            # 每个单元格仍由 Fluent 裁切；同一行的完整轮廓不会出现列间接缝。
            return QRectF(-view.horizontalScrollBar().value() + 1, option.rect.top(),
                          view.horizontalHeader().length() - 2, option.rect.height())
        return QRectF(option.rect).adjusted(1, 0, -1, 0)

    def _drawBackground(self, painter, option, index):
        from theme import tokens
        colors = tokens()
        selected = index.row() in self.selectedRows
        hovered = index.row() == self.hoverRow
        pressed = index.row() == self.pressedRow
        focused = self.parent().hasFocus() and self.parent().currentIndex().row() == index.row()
        color = colors['row_selected'] if selected or pressed else (
            colors['hover'] if hovered else self._idle_row_color(colors))
        rect = self._row_rect(option)
        painter.save()
        try:
            painter.setBrush(QColor(color))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(rect, 4, 4)
            if focused or selected:
                painter.setBrush(Qt.NoBrush)
                painter.setPen(QPen(QColor(colors['accent' if focused else 'row_selected_border']), 1))
                painter.drawRoundedRect(rect.adjusted(.5, .5, -.5, -.5), 4, 4)
            elif not (selected or hovered or pressed) and index.row() + 1 < index.model().rowCount():
                inset = self._row_rule_inset()
                if inset is not None and rect.width() > 2 * inset:
                    rule = QColor(colors['border'])
                    rule.setAlpha(130)
                    painter.setPen(QPen(rule, 1))
                    painter.drawLine(QPointF(rect.left() + inset, rect.bottom()),
                                     QPointF(rect.right() - inset, rect.bottom()))
        finally:
            painter.restore()

    def _drawIndicator(self, painter, option, index):
        # 选择由整行浅色承载；键盘焦点轮廓在背景钩子绘制，避免长行侧边粗条。
        pass


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
            # Qt 在图标左右也分别保留 3px，窄列少算会让文件扩展名落在绘制框外。
            text_width = max(1, text_width - option.decorationSize.width() - 6)
        option.rect.setWidth(text_width)
        size = super().sizeHint(option, index)
        measured = QFontMetrics(option.font).boundingRect(
            QRect(0, 0, text_width, 100000), Qt.TextWordWrap, option.text)
        size.setHeight(max(size.height(), measured.height() + 2 * self.margin + 8))
        if is_list:
            size.setWidth(width)
        return size


class WrappedFluentListDelegate(_WorkbenchRowStates, _WrappedFluentText, ListItemDelegate):
    """保留 Fluent 状态、原生选择与完整文本测量。"""


class ProjectListDelegate(WrappedFluentListDelegate):
    """项目名和详情独立换行，沿用 Fluent 行交互与工作台的选中、焦点样式。"""

    horizontalInset = 12
    blockSpacing = 2
    batchSpacing = 3
    batchHeight = 16
    verticalPadding = 5

    def __init__(self, parent):
        super().__init__(parent)
        self._painting_background = False

    def _idle_row_color(self, colors):
        return Qt.transparent

    def _row_rule_inset(self):
        return self.horizontalInset - 1

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        if self._painting_background:
            # QStyledItemDelegate.paint 会重新初始化选项，必须在此处隐藏原合并文本。
            option.text = ''

    def _text_layout(self, text, font, width, formats=()):
        # QTextLayout 用行分隔符表达显式换行；模型里的原文字不变。
        layout = QTextLayout(text.replace('\n', '\u2028'), font)
        text_option = QTextOption()
        text_option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        layout.setTextOption(text_option)
        if formats:
            layout.setFormats(formats)
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

    def _text_blocks(self, index, width, colors=None):
        font = QFont(self.parent().font())
        font.setPixelSize(14)
        font.setWeight(QFont.Medium)
        name = self._text_layout(str(index.data(PROJECT_NAME_ROLE)), font, width)
        font.setPixelSize(12)
        font.setWeight(QFont.Normal)
        detail = str(index.data(PROJECT_DETAIL_ROLE) or '')
        status = detail.split('\n', 1)[0].split(' · ', 1)[0]
        tone = PROJECT_STATUS_TONES.get(status)
        formats = []
        if tone:
            status_range = QTextLayout.FormatRange()
            status_range.start = 0
            status_range.length = len(status)
            status_range.format = QTextCharFormat()
            status_range.format.setFontWeight(QFont.Medium)
            if colors is not None:
                status_range.format.setForeground(QColor(colors[tone]))
            formats.append(status_range)
        return name, self._text_layout(detail, font, width, formats)

    def _row_layout(self, index, width, colors=None):
        name, detail = self._text_blocks(index, width, colors)
        detail_top = name[1] + self.blockSpacing
        height = detail_top + detail[1] if detail[1] else name[1]
        markers = index.data(PROJECT_BATCH_MARKERS_ROLE) or []
        badge_line = None
        if markers:
            font = QFont(self.parent().font())
            font.setPixelSize(11)
            metrics = QFontMetrics(font)
            x = math.ceil(detail[0].lineAt(0).naturalTextWidth()) + 8 if detail[1] else 0
            badges, summary = _batch_badge_layout(markers, metrics, width - x)
            full_badges, full_summary = _batch_badge_layout(markers, metrics, width)
            code, progress = _batch_badge_parts(markers[0])
            if (detail[0].lineCount() <= 1 and badges
                    and len(badges) == len(full_badges)
                    and badges[0][1] >= 24 + metrics.horizontalAdvance(code + progress)):
                y = detail_top + max(0, (detail[1] - self.batchHeight) / 2)
            else:
                x = 0
                y = height + self.batchSpacing
                badges, summary = full_badges, full_summary
            height = max(height, y + self.batchHeight)
            badge_line = (QPointF(x, y), font, metrics, badges, summary)
        return name[0], detail[0], detail_top, badge_line, height

    def sizeHint(self, option, index):
        if index.data(PROJECT_NAME_ROLE) is None:
            return super().sizeHint(option, index)
        width = option.rect.width()
        if width <= 0:
            width = self.parent().viewport().width()
        *_, height = self._row_layout(index, width - 2 * self.horizontalInset)
        return QSize(width, math.ceil(height) + 2 * (self.margin + self.verticalPadding))

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

        from theme import tokens, resolved_mode
        colors = tokens()
        enabled = bool(option.state & QStyle.State_Enabled and index.flags() & Qt.ItemIsEnabled)
        name_color = colors['ink'] if enabled else colors['disabled']
        detail_color = colors['muted'] if enabled else colors['disabled']
        name, detail, detail_top, badge_line, _ = self._row_layout(
            index, option.rect.width() - 2 * self.horizontalInset, colors if enabled else None)
        position = QPointF(option.rect.left() + self.horizontalInset,
                          option.rect.top() + self.margin + self.verticalPadding)
        painter.save()
        try:
            painter.setClipRect(option.rect, Qt.IntersectClip)
            painter.setPen(QColor(name_color))
            name.draw(painter, position)
            painter.setPen(QColor(detail_color))
            detail.draw(painter, position + QPointF(0, detail_top))
            if badge_line:
                offset, font, metrics, badges, summary = badge_line
                painter.setFont(font)
                self._draw_badges(painter, position + offset, metrics, badges, summary,
                                  resolved_mode(), name_color, detail_color)
        finally:
            painter.restore()

    def _draw_badges(self, painter, position, metrics, badges, summary, mode, name_color, detail_color):
        x, y = position.x(), position.y()
        center = y + self.batchHeight / 2
        baseline = center + (metrics.ascent() - metrics.descent()) / 2
        for marker, badge_width in badges:
            code, progress = _batch_badge_parts(marker)
            text = code + progress
            text_width = int(badge_width) - 24
            if metrics.horizontalAdvance(text) > text_width:
                code_width = text_width - metrics.horizontalAdvance(progress)
                if code_width > metrics.horizontalAdvance('…'):
                    text = metrics.elidedText(code, Qt.ElideMiddle, code_width) + progress
                else:
                    text = metrics.elidedText(text, Qt.ElideRight, text_width)
            color = QColor(marker.get('color') or '#777777')
            if not color.isValid():
                color = QColor('#777777')
            painter.setPen(Qt.NoPen)
            painter.setBrush(_batch_badge_background(color, mode))
            painter.drawRoundedRect(QRectF(x, y, badge_width, self.batchHeight), 4, 4)
            painter.setBrush(color)
            painter.drawEllipse(QPointF(x + 8, center), 2.5, 2.5)
            painter.setPen(QColor(name_color))
            painter.drawText(QPointF(x + 15, baseline), text)
            x += badge_width + 5
        if summary:
            painter.setPen(QColor(detail_color))
            painter.drawText(QPointF(x, baseline), summary)


class WrappedFluentTableDelegate(_WorkbenchRowStates, _WrappedFluentText, TableItemDelegate):
    """整张表共用一个交互状态，仅指定列按内容换行。"""

    def __init__(self, parent):
        super().__init__(parent)
        self.wrapColumns = set()
        self.emphasizedColumns = set()
        self.mutedColumns = set()

    def _wraps(self, index):
        return index.column() in self.wrapColumns

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        if not (self.emphasizedColumns or self.mutedColumns):
            return
        # 背景、悬停与焦点由上面的 Fluent 绘制钩子负责。避免 Qt 的样式规则覆盖单元格文字色。
        option.state &= ~(QStyle.State_Selected | QStyle.State_MouseOver | QStyle.State_HasFocus)
        if index.column() in self.emphasizedColumns:
            option.font.setWeight(QFont.DemiBold)
            option.fontMetrics = QFontMetrics(option.font)
        tone = index.data(ITEM_TEXT_TONE_ROLE)
        if not option.state & QStyle.State_Enabled or not index.flags() & Qt.ItemIsEnabled:
            tone = 'disabled'
        elif tone is None:
            tone = 'muted' if index.column() in self.mutedColumns else 'ink'
        from theme import tokens
        option.palette.setColor(QPalette.Text, QColor(tokens()[tone]))


class WorkbenchListWidget(ListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setItemDelegate(WrappedFluentListDelegate(self))
        self.setUniformItemSizes(False)
        self.setWordWrap(True)
        self.setTextElideMode(Qt.ElideNone)
        self.setResizeMode(QListView.Adjust)
        # 库内置的固定35px行高不适用于业务长名称。
        setCustomStyleSheet(self, _fluent_item_styles('QListView', 11, 'light'),
                            _fluent_item_styles('QListView', 11, 'dark'))
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
        self.setAlternatingRowColors(False)
        self.setWordWrap(True)
        self.setTextElideMode(Qt.ElideNone)
        setCustomStyleSheet(self, _fluent_item_styles('QTableView', 16, 'light'),
                            _fluent_item_styles('QTableView', 16, 'dark'))
        configure_native_scroll(self)
        self.selectionModel().selectionChanged.connect(self._sync_selection)
        self.model().modelReset.connect(self._sync_selection)

    def _sync_selection(self, *_):
        self.updateSelectedRows()

    def setWrapColumns(self, columns: set[int]):
        self.delegate.wrapColumns = set(columns)
        self.resizeRowsToContents()
        self.viewport().update()

    def setColumnTypography(self, *, emphasized=(), muted=()):
        self.delegate.emphasizedColumns = set(emphasized)
        self.delegate.mutedColumns = set(muted)
        use_item_palette = bool(self.delegate.emphasizedColumns or self.delegate.mutedColumns)
        setCustomStyleSheet(self, _fluent_item_styles('QTableView', 16, 'light', use_item_palette=use_item_palette),
                            _fluent_item_styles('QTableView', 16, 'dark', use_item_palette=use_item_palette))
