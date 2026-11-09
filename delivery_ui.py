"""交付历史与统计管理：首次有效交付计数，误记可撤销和恢复。"""
from datetime import datetime
from html import escape
from math import ceil
from pathlib import Path

from PySide6.QtCore import Qt, QDate, QEvent, QLocale, QPoint, QSignalBlocker, QTimer, Signal, QItemSelectionModel
from PySide6.QtGui import QTextOption
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox,
    QHBoxLayout, QHeaderView, QMenu, QSizePolicy, QTextBrowser,
    QTableWidgetItem, QVBoxLayout, QTabBar, QWidget, QSplitter)

from compact_ui import button, label
from theme import apply_to
from ui_components import LineEdit as QLineEdit
from fluent_views import WorkbenchTableWidget
from database import DELIVERY_TIMEZONE
from ui import FileJob
from project_types_ui import choose_project_type
from date_edit import DateEdit

KIND_TEXT = {'auto-zip':'ZIP 自动交付','manual-zip':'手动 ZIP 交付',
             'manual-files':'手动材料交付','legacy':'旧交付事实'}
ROLE_TEXT = {'first':'首次计数','repeat':'补交不计','voided':'已撤销'}
SCOPE_NOTE = '按北京时间统计个人工作量。代交可设为不计工作量，误记可撤销。'
DEFAULT_PANEL_SIZES = (2, 1)


def beijing_timestamp(value):
    try:
        moment = datetime.fromisoformat(value.replace('Z','+00:00'))
        return value if moment.tzinfo is None else moment.astimezone(DELIVERY_TIMEZONE).strftime('%Y-%m-%d %H:%M:%S')
    except (TypeError,ValueError,AttributeError):
        return str(value or '')


class DeliveryProjectTable(WorkbenchTableWidget):
    width_about_to_change = Signal()

    def resizeEvent(self,event):
        if event.oldSize().width() != event.size().width():
            self.width_about_to_change.emit()
        super().resizeEvent(event)


class TodayDeliveryDialog(QDialog):
    folder_requested = Signal(str)
    statistics_changed = Signal()
    operation_completed = Signal(str)
    return_requested = Signal()

    def __init__(self,service,parent=None,*,embedded=False):
        super().__init__(parent)
        self.embedded = embedded
        self._compact_embedded = False
        self._embedded_layout_initialized = False
        if embedded:
            # 复用管理内容和任务生命周期，由主窗口承担导航和窗口行为。
            self.setWindowFlags(Qt.Widget)
        self.service = service
        self._panel_sizes_dirty = False
        self._details_collapsed = False
        try:
            sizes = service.get_setting('delivery_panel_sizes', list(DEFAULT_PANEL_SIZES))
        except Exception:
            sizes = None
        self._panel_sizes = sizes if (isinstance(sizes, list) and len(sizes) == 2
            and all(type(size) is int and 0 < size <= 100000 for size in sizes)) else list(DEFAULT_PANEL_SIZES)
        self.rows,self.events = [],[]
        self._summary_signature = self._details_signature = None
        self._history,self._day = None,None
        self._following_today = True
        self._external_busy = False
        self._choosing_type = False
        self._correction_dialog = None
        self._reason_drafts = {}
        self.worker = None
        self.setWindowTitle('交付记录管理')
        self.setModal(False)
        apply_to(self)
        if not embedded:
            self.setMinimumSize(680,460)
            area = self.screen().availableGeometry()
            self.resize(min(980,area.width()-24),min(680,area.height()-48))
        layout = QVBoxLayout(self)
        layout.setSpacing(4)
        controls = QHBoxLayout()
        self.date_edit = DateEdit()
        self.date_edit.setAccessibleName('交付记录日期')
        self.date_edit.setAccessibleDescription('按北京时间查询单日交付记录。')
        self.date_edit.setToolTip('按北京时间查询单日交付记录。')
        self.date_edit.setLocale(QLocale(QLocale.Chinese,QLocale.China))
        self.date_edit.setDisplayFormat('yyyy-MM-dd')
        self.date_edit.setCalendarPopup(True)
        try:
            today = service.db.today_delivery_summary()['day']
        except Exception:
            today = datetime.now(DELIVERY_TIMEZONE).date().isoformat()
        self.date_edit.setDate(QDate.fromString(today,'yyyy-MM-dd'))
        self.search = QLineEdit()
        self.search.setAccessibleName('搜索交付项目或批次')
        self.search.setPlaceholderText('搜索项目或批次')
        self.search.setClearButtonEnabled(True)
        self.search.setMaxLength(200)
        self.refresh_button = button('刷新')
        self.refresh_button.hide()
        self.export_button = button('导出全部')
        self.export_button.setAccessibleName('导出全部有效交付项目')
        self.export_button.setToolTip('导出全部有效交付项目，每个项目一行，不受当前筛选影响。')
        self.date_scope_button = button('全部日期')
        self.date_scope_button.setAccessibleName('交付记录日期范围：全部日期')
        self.date_scope_button.setToolTip('当前查询全部日期，点击可切换到单日。')
        self.date_scope_button.hide()
        controls.addWidget(self.date_edit)
        controls.addWidget(self.date_scope_button)
        controls.addWidget(self.search,1)
        self.compact_filter_button = button('筛选')
        self.compact_filter_button.setAccessibleName('交付筛选与统计')
        self.compact_filter_button.setToolTip(SCOPE_NOTE)
        self.compact_filter_menu = QMenu(self.compact_filter_button)
        self.compact_filter_button.setMenu(self.compact_filter_menu)
        self.today_action = self.compact_filter_menu.addAction('今天',self.go_today)
        date_scope_menu = QMenu(self.date_scope_button)
        self.date_scope_button.setMenu(date_scope_menu)
        date_scope_menu.addAction(self.today_action)
        date_scope_menu.addAction('选择日期', self._choose_date)
        self.all_dates = self.compact_filter_menu.addAction('全部日期')
        self.all_dates.setCheckable(True)
        self.show_voided = self.compact_filter_menu.addAction('显示已撤销')
        self.show_voided.setCheckable(True)
        self.refresh_action = self.compact_filter_menu.addAction('刷新',self.refresh)
        self.type_stats_menu = self.compact_filter_menu.addMenu('当前筛选的类型统计')
        self.scope_stats_action = self.compact_filter_menu.addAction('交付统计暂不可读')
        self.scope_stats_action.setEnabled(False)
        self.compact_filter_menu.aboutToShow.connect(self._populate_compact_filters)
        controls.addWidget(self.compact_filter_button)
        controls.addWidget(self.export_button)
        layout.addLayout(controls)
        self.summary = label('','subtle',True)
        self.summary.hide()
        summary_row = QHBoxLayout()
        summary_row.addWidget(self.summary,1)
        summary_row.addWidget(self.refresh_button)
        layout.addLayout(summary_row)
        self.type_summary = QWidget(self)
        self.type_summary.setAccessibleName('交付类型统计')
        self.type_summary.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        type_summary_layout = QVBoxLayout(self.type_summary)
        type_summary_layout.setContentsMargins(0, 0, 0, 0)
        type_summary_layout.setSpacing(0)
        self.type_total = label('',wrap=True)
        font = self.type_total.font()
        font.setBold(True)
        self.type_total.setFont(font)
        # 原生 QLabel 用实际文字作为辅助名称，让合计及错误状态同步可读。
        self.type_total.setObjectName('deliveryTypeTotal')
        type_summary_layout.addWidget(self.type_total)
        self.type_counts = QTextBrowser()
        self.type_counts.setAccessibleName('按当前类型计入工作量的项目数量')
        self.type_counts.setReadOnly(True)
        self.type_counts.setStyleSheet('QTextBrowser { background: transparent; border: none; padding: 0px; }')
        self.type_counts.document().setDocumentMargin(2)
        self.type_counts.setOpenLinks(False)
        self.type_counts.setAcceptDrops(False)
        self.type_counts.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.type_counts.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        self.type_counts.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred)
        self.type_counts.setMinimumHeight(24)
        self.type_counts.setMaximumHeight(64)
        self._summary_layout_timer = QTimer(self)
        self._summary_layout_timer.setSingleShot(True)
        self._summary_layout_timer.timeout.connect(self._fit_type_summary)
        self.type_counts.document().documentLayout().documentSizeChanged.connect(
            lambda *_: self._summary_layout_timer.start(0))
        type_summary_layout.addWidget(self.type_counts)
        layout.addWidget(self.type_summary)
        self.compact_view_bar = QWidget(self)
        compact_view_layout = QHBoxLayout(self.compact_view_bar)
        compact_view_layout.setContentsMargins(0,0,0,0)
        self.compact_tabs = QTabBar(self.compact_view_bar)
        self.compact_tabs.setAccessibleName('交付记录视图')
        self.compact_tabs.addTab('项目记录')
        self.compact_tabs.addTab('交付明细')
        self.compact_tabs.setExpanding(False)
        self.compact_tabs.setDocumentMode(True)
        self.compact_tabs.currentChanged.connect(self._sync_embedded_layout)
        compact_view_layout.addWidget(self.compact_tabs)
        compact_view_layout.addStretch()
        self.compact_view_bar.hide()
        layout.addWidget(self.compact_view_bar)
        self.record_splitter = QSplitter(Qt.Vertical, self)
        self.record_splitter.setObjectName('deliveryPanels')
        self.record_splitter.setAccessibleName('项目记录与交付明细高度')
        self.record_splitter.setChildrenCollapsible(False)
        if not embedded:
            # 可伸缩列表不把展开说明时的最小高度固化为 Windows 窗口下限。
            self.record_splitter.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
        self.record_splitter.setHandleWidth(10)
        self.record_splitter.setStyleSheet('''QSplitter#deliveryPanels::handle {
            background: palette(midlight); border-radius: 2px; margin: 2px 0;
        }
        QSplitter#deliveryPanels::handle:hover { background: palette(highlight); }''')
        self.project_panel = QWidget()
        project_layout = QVBoxLayout(self.project_panel)
        project_layout.setContentsMargins(0, 0, 0, 0)
        project_layout.setSpacing(4)
        self.detail_panel = QWidget()
        detail_layout = QVBoxLayout(self.detail_panel)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(4)
        self.project_heading = label('项目记录')
        self.detail_heading = label('交付明细', wrap=True)
        detail_heading_policy = self.detail_heading.sizePolicy()
        detail_heading_policy.setHorizontalPolicy(QSizePolicy.Ignored)
        self.detail_heading.setSizePolicy(detail_heading_policy)
        for heading in (self.project_heading, self.detail_heading):
            font = heading.font()
            font.setBold(True)
            heading.setFont(font)
        project_layout.addWidget(self.project_heading)
        detail_layout.addWidget(self.detail_heading)
        self.table = self._table('交付项目列表',['项目','批次','首次有效交付','我的工作量','项目类型'],wrapped=True)
        self.table.setMinimumHeight(116)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setWordWrap(True)
        self.table.setTextElideMode(Qt.ElideNone)
        self.table.setColumnTypography(emphasized={0}, muted={1, 2, 4})
        self.table.setWrapColumns({0, 4})
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(
            lambda point:self._record_context_menu(self.table,self.project_action_menu,point))
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        for column, width in enumerate((250, 100, 164, 100, 220)):
            self.table.setColumnWidth(column, width)
        self._project_layout_anchor = None
        self._project_layout_timer = QTimer(self)
        self._project_layout_timer.setSingleShot(True)
        self._project_layout_timer.timeout.connect(self._resize_project_rows)
        self.table.width_about_to_change.connect(self._queue_project_layout)
        self.table.horizontalHeader().sectionResized.connect(self._queue_project_layout)
        project_layout.addWidget(self.table,1)
        self.details_hint = label('在项目记录中选择一个项目，查看交付明细。','subtle',True)
        detail_layout.addWidget(self.details_hint)
        self.details = self._table('所选项目交付明细',['交付时间（北京时间）','计数说明','交付方式','归档位置'])
        self.event_table = self.details
        self.details.setColumnTypography(emphasized={0}, muted={2, 3})
        self.details.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.details.setContextMenuPolicy(Qt.CustomContextMenu)
        self.details.customContextMenuRequested.connect(
            lambda point:self._record_context_menu(self.details,self.record_action_menu,point))
        self.details.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        for column, width in enumerate((180, 120, 130, 280)):
            self.details.setColumnWidth(column, width)
        detail_layout.addWidget(self.details,1)
        self.record_splitter.addWidget(self.project_panel)
        self.record_splitter.addWidget(self.detail_panel)
        self.record_splitter.setStretchFactor(0, 1)
        self.record_splitter.setStretchFactor(1, 1)
        self.record_splitter.setSizes(self._panel_sizes)
        handle = self.record_splitter.handle(1)
        handle.setToolTip('上下拖动，调整项目记录与交付明细的高度')
        handle.setAccessibleName('拖动调整上下列表高度')
        self.record_splitter.splitterMoved.connect(self._remember_panel_sizes)
        self._panel_layout_timer = QTimer(self)
        self._panel_layout_timer.setSingleShot(True)
        self._panel_layout_timer.timeout.connect(self._restore_panel_sizes)
        self.record_splitter.installEventFilter(self)
        layout.addWidget(self.record_splitter,1)
        actions = QHBoxLayout()
        self.project_actions_button = button('项目操作')
        self.project_actions_button.setAccessibleName('所选交付项目操作')
        self.project_action_menu = QMenu(self.project_actions_button)
        self.project_action_menu.setToolTipsVisible(True)
        self.project_actions_button.setMenu(self.project_action_menu)
        self.change_type_action = self.project_action_menu.addAction('修改类型…',self.manage_project_types)
        self.exclude_count_action = self.project_action_menu.addAction('不计工作量',lambda:self.manage_workload(False))
        self.include_count_action = self.project_action_menu.addAction('恢复计入',lambda:self.manage_workload(True))
        self.exclude_count_action.setToolTip('代他人交付时只调整个人工作量，保留交付记录和项目状态。')
        self.include_count_action.setToolTip('按原首次有效交付日期恢复工作量。')
        self.record_actions_button = button('记录操作')
        self.record_actions_button.setAccessibleName('所选交付记录操作')
        self.record_action_menu = QMenu(self.record_actions_button)
        self.record_action_menu.setToolTipsVisible(True)
        self.record_actions_button.setMenu(self.record_action_menu)
        self.open_folder_action = self.record_action_menu.addAction('打开归档目录',self.open_selected_archive)
        self.void_action = self.record_action_menu.addAction('撤销误记',lambda:self.manage_selected(True))
        self.restore_action = self.record_action_menu.addAction('恢复记录',lambda:self.manage_selected(False))
        self.void_action.setToolTip('将误记的交付记录撤销，对应项目可能退回待交付。')
        self.restore_action.setToolTip('恢复交付记录的统计有效性，保留项目当前状态。')
        self.expand_details_action = self.record_action_menu.addAction('放大明细')
        self.expand_details_action.setCheckable(True)
        self.expand_details_action.setToolTip('让交付明细使用整个列表区域，再次选择恢复上下分栏。')
        self.close_button = button('返回项目' if embedded else '关闭')
        self.details_toggle = button('收起明细')
        self.details_toggle.setToolTip('显示或收起所选项目的交付明细。')
        actions.addWidget(self.project_actions_button)
        actions.addWidget(self.details_toggle)
        actions.addWidget(self.record_actions_button)
        actions.addStretch()
        actions.addWidget(self.close_button)
        layout.addLayout(actions)
        self.status_note = label('','subtle',True)
        self.status_note.hide()
        layout.addWidget(self.status_note)
        self._status_timer = QTimer(self)
        self._status_timer.setSingleShot(True)
        self._status_timer.timeout.connect(self.status_note.hide)
        for widget in (self.refresh_button,self.export_button,self.date_scope_button,self.compact_filter_button,
                       self.project_actions_button,self.details_toggle,self.record_actions_button,self.close_button):
            widget.setAutoDefault(False)
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.refresh)
        self.refresh_button.clicked.connect(self.refresh)
        self.export_button.clicked.connect(self.export_projects)
        self.date_edit.dateChanged.connect(self._date_changed)
        self.date_edit.input_rejected.connect(self._date_input_rejected)
        self.date_edit.input_accepted.connect(self._date_input_accepted)
        self.all_dates.toggled.connect(self._scope_changed)
        self.show_voided.toggled.connect(self.refresh)
        self.search.textChanged.connect(self.refresh)
        self.table.itemSelectionChanged.connect(self.show_details)
        self.details.itemSelectionChanged.connect(self._update_open_action)
        self.close_button.clicked.connect(self.return_requested.emit if embedded else self.close)
        self.expand_details_action.toggled.connect(self._sync_record_views)
        self.details_toggle.clicked.connect(self._toggle_details)
        self.refresh()

    @staticmethod
    def _table(name,headings,*,wrapped=False):
        table = (DeliveryProjectTable if wrapped else WorkbenchTableWidget)(0,len(headings))
        table.setAccessibleName(name)
        table.setHorizontalHeaderLabels(headings)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        table.setWordWrap(False)
        table.verticalHeader().hide()
        table.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        # 紧凑窗口仍能容纳表头及一行，余量优先交给项目和明细列表。
        table.setMinimumHeight(60)
        return table

    def _project_anchor(self):
        row = self.table.rowAt(0)
        item = self.table.item(row,0) if row >= 0 else None
        return (item.data(Qt.UserRole),self.table.rowViewportPosition(row)) if item else (None,0)

    def _queue_project_layout(self,*_):
        if self._project_layout_anchor is None:
            self._project_layout_anchor = self._project_anchor()
        # 沿用文档列表的原生行高测量与滚动定位，合并连续调宽产生的重排。
        self._project_layout_timer.start(80)

    def _resize_project_rows(self):
        anchor_id,anchor_top = self._project_layout_anchor or self._project_anchor()
        self._project_layout_anchor = None
        self.table.resizeRowsToContents()
        self.table.updateGeometries()
        if anchor_id is not None and not self.table.verticalScrollBar().isSliderDown():
            row = next((i for i in range(self.table.rowCount())
                        if self.table.item(i,0).data(Qt.UserRole)==anchor_id),None)
            if row is not None:
                self.table.scrollToItem(self.table.item(row,0),QAbstractItemView.PositionAtTop)
                offset = max(anchor_top,1-self.table.rowHeight(row))
                bar = self.table.verticalScrollBar()
                bar.setValue(bar.value()-offset)

    @staticmethod
    def _selected_key(table):
        item = table.item(table.currentRow(),0)
        return item.data(Qt.UserRole) if item and table.selectedItems() else None

    @staticmethod
    def _replace_rows(table,records,*,choose_first=False):
        selected = {table.item(index.row(),0).data(Qt.UserRole) for index in table.selectionModel().selectedRows()}
        current = TodayDeliveryDialog._selected_key(table)
        old_ids = [table.item(i,0).data(Qt.UserRole) for i in range(table.rowCount())]
        new_ids = [record[0] for record in records]
        vertical,horizontal = table.verticalScrollBar().value(),table.horizontalScrollBar().value()
        with QSignalBlocker(table):
            if old_ids != new_ids:
                table.setRowCount(0)
                table.setRowCount(len(records))
            for row,(key,values,tips) in enumerate(records):
                for col,value in enumerate(values):
                    item = table.item(row,col)
                    if item is None:
                        item = QTableWidgetItem()
                        item.setFlags(Qt.ItemIsSelectable|Qt.ItemIsEnabled)
                        table.setItem(row,col,item)
                    if item.text() != str(value):
                        item.setText(str(value))
                    item.setToolTip(tips.get(col,str(value)))
                    if col == 0:
                        item.setData(Qt.UserRole,key)
            selected.intersection_update(new_ids)
            if not selected and choose_first and new_ids:
                selected = {new_ids[0]}
            table.clearSelection()
            for row,key in enumerate(new_ids):
                if key in selected:
                    table.selectionModel().select(table.model().index(row,0),QItemSelectionModel.Select|QItemSelectionModel.Rows)
            key = current if current in selected else next((key for key in new_ids if key in selected),None)
            if key is None:
                table.setCurrentCell(-1,-1)
            else:
                table.selectionModel().setCurrentIndex(table.model().index(new_ids.index(key),0),QItemSelectionModel.NoUpdate)
            table.verticalScrollBar().setValue(vertical)
            table.horizontalScrollBar().setValue(horizontal)

    def _date_changed(self,*_):
        self._following_today = False
        self.refresh()

    def _date_input_rejected(self, message):
        self._date_error = message
        self._set_status(message)

    def _date_input_accepted(self):
        if getattr(self, '_date_error', None) == self.status_note.text():
            self.status_note.hide()
        self._date_error = None

    def _set_status(self, message, *, temporary=False):
        self._status_timer.stop()
        self.status_note.setText(message)
        self.status_note.setToolTip(message)
        self.status_note.show()
        if temporary:
            self._status_timer.start(6000)

    def _fit_type_summary(self):
        limit = 32 if self.embedded or self.height() < 580 else 64
        height = min(limit, max(24, ceil(self.type_counts.document().size().height())
                                + 2 * self.type_counts.frameWidth()))
        if self.type_counts.maximumHeight() != height:
            self.type_counts.setMaximumHeight(height)

    def _remember_panel_sizes(self, *_):
        if self.project_panel.isVisible() and self.detail_panel.isVisible():
            sizes = self.record_splitter.sizes()
            if len(sizes) == 2 and all(sizes):
                self._panel_sizes = sizes
                self._panel_sizes_dirty = True

    def _restore_panel_sizes(self):
        if self.project_panel.isVisible() and self.detail_panel.isVisible():
            available = sum(self.record_splitter.sizes())
            if available > 1:
                # 先按当前空间换算比例，避免 Qt 用新窗口的最小高度截断旧像素值。
                first = max(1, min(available - 1,
                                  round(available * self._panel_sizes[0] / sum(self._panel_sizes))))
                if tuple(self._panel_sizes) == DEFAULT_PANEL_SIZES:
                    # 初始明细留出可读区域。用户拖动后的比例按原值恢复。
                    header = self.details.horizontalHeader()
                    frame = 2 * self.details.frameWidth()
                    width = self.record_splitter.width() - frame - self.details.verticalScrollBar().sizeHint().width()
                    scrollbar = self.details.horizontalScrollBar().sizeHint().height() if header.length() > width else 0
                    readable_detail = (140 + self.detail_heading.sizeHint().height()
                                       + self.detail_panel.layout().spacing() + header.sizeHint().height()
                                       + frame + scrollbar)
                    if available > 2 * readable_detail:
                        first = min(first, available - readable_detail)
                self.record_splitter.setSizes([first, available - first])

    def _save_panel_sizes(self):
        if not self._panel_sizes_dirty:
            return
        try:
            self.service.set_setting('delivery_panel_sizes', self._panel_sizes)
        except Exception as error:
            self._set_status('本次高度已调整，但未能保存布局：' + str(error))
        else:
            self._panel_sizes_dirty = False

    def _scope_changed(self,*_):
        all_dates = self.all_dates.isChecked()
        if all_dates:
            self.date_edit.discard_pending_input()
        self.date_edit.setEnabled(not all_dates)
        self.date_edit.setVisible(not all_dates)
        self.date_scope_button.setVisible(all_dates)
        self.refresh()

    def _choose_date(self):
        self.all_dates.setChecked(False)
        self.date_edit.setFocus(Qt.OtherFocusReason)

    def go_today(self):
        self.date_edit.discard_pending_input()
        self._following_today = True
        with QSignalBlocker(self.all_dates):
            self.all_dates.setChecked(False)
        self._scope_changed()

    def refresh(self,*_):
        all_dates = self.all_dates.isChecked()
        show_voided = self.show_voided.isChecked()
        active = all_dates + show_voided
        filter_text = f'筛选（{active}）' if active else '筛选'
        filter_state = ' · 含已撤销' if show_voided else ''
        self.compact_filter_button.setText(filter_text + filter_state)
        self.compact_filter_button.setAccessibleName('交付' + filter_text + filter_state)
        keyword = self.search.text()
        try:
            if self._following_today and not all_dates:
                today = self.service.db.today_delivery_summary()['day']
                date = QDate.fromString(today,'yyyy-MM-dd')
                # 同日刷新也会让 Qt 重写编辑文本，丢弃尚未输完的年份。
                if self.date_edit.date() != date:
                    with QSignalBlocker(self.date_edit):
                        self.date_edit.setDate(date)
            day = None if all_dates else self.date_edit.date().toPython()
            history = self.service.db.delivery_history(day,keyword=keyword)
            catalog = self.service.list_project_types(include_inactive=True)
            signature = (day,keyword,show_voided,
                         tuple(tuple(sorted(row.items())) for row in history['events']),
                         tuple(tuple(sorted(row.items())) for row in catalog),
                         tuple(tuple(sorted(row.items())) for row in history.get('type_counts',[])))
        except Exception as error:
            self.summary.setText('读取交付记录失败：'+str(error)+'。')
            self.summary.show()
            self.refresh_button.show()
            self._history = None
            self._summary_signature = self._details_signature = None
            self.rows,self.events = [],[]
            self.type_summary.hide()
            self.type_total.setText('合计暂不可读')
            self.scope_stats_action.setText('交付统计暂不可读')
            self.compact_filter_button.setToolTip(SCOPE_NOTE)
            self.type_counts.clear()
            self._replace_rows(self.table,[])
            self._replace_rows(self.details,[])
            self.details_hint.setText('记录暂时不可读，请刷新重试。')
            self._update_open_action()
            return
        if signature == self._summary_signature:
            return
        if any(bar.isSliderDown() for table in (self.table,self.details)
               for bar in (table.verticalScrollBar(),table.horizontalScrollBar())):
            return
        self._day,self._history = day,history
        self._summary_signature = signature
        scope = day.isoformat() if day else '全部日期'
        grouped = {}
        for event in history['events']:
            if event['voided'] and not show_voided:
                continue
            row = grouped.setdefault(event['project_id'],dict(project_id=event['project_id'],project_name=event['project_name'],
                batch_name=event['batch_name'],first_at=event['first_at'],prior_delivery=event.get('prior_delivery',False),counted=False,has_valid=False,
                included_in_workload=event.get('included_in_workload',True),
                count_exclusion_reason=event.get('count_exclusion_reason',''),
                project_type_id=event.get('project_type_id'),project_type_name=event.get('project_type_name',''),
                project_type_active=event.get('project_type_active'),
                project_archived=event.get('project_archived',False),project_deleted=event.get('project_deleted',False)))
            row['counted'] |= event['counted']
            row['has_valid'] |= not event['voided']
        self.rows = list(grouped.values())
        excluded=sum(row['has_valid'] and not row['included_in_workload'] for row in self.rows)
        if keyword.strip() or history['events']:
            self.summary.setText('没有符合当前筛选的交付记录。')
        else:
            self.summary.setText('所选日期暂无交付记录。' if day else '暂无交付记录。')
        self.summary.setVisible(not self.rows)
        self.refresh_button.hide()
        self.type_summary.show()
        self.type_total.setText(f"当前筛选 · 工作量合计 {history['project_count']} 个项目"
                               f" · 补交 {history['repeat_count']} 条")
        voided_note = '（列表已隐藏）' if history['voided_count'] and not show_voided else ''
        breakdown = (f"首次交付 {history['project_count']} 个项目 · 补交 {history['repeat_count']} 条"
                     f" · 已撤销 {history['voided_count']} 条{voided_note} · 不计工作量 {excluded} 个项目")
        self.scope_stats_action.setText(f'{scope} · {breakdown}')
        self.type_total.setToolTip(f'{scope} · {breakdown}')
        self.compact_filter_button.setToolTip(SCOPE_NOTE+'\n'+f'{scope} · {breakdown}')
        counts = '  ·  '.join(f"{row['type_name'] or '未分类'}"+
            ('（已停用）' if row.get('active') is False else '')+f"：{row['project_count']}"
            for row in history.get('type_counts',[]))
        if self.type_counts.toPlainText() != counts:
            position = self.type_counts.verticalScrollBar().value()
            self.type_counts.setPlainText(counts)
            self.type_counts.verticalScrollBar().setValue(position)
        records = []
        for row in self.rows:
            state = ('已撤销' if not row['has_valid'] else '代交 / 不计工作量' if not row['included_in_workload']
                     else '计数 1 项' if row['counted'] else '补交不计')
            records.append((row['project_id'],(row['project_name'],row['batch_name'],
                beijing_timestamp(row['first_at']) or ('历史已交付，日期未记录' if row['prior_delivery'] else '无有效交付'),state,
                (row['project_type_name'] or '未分类')+('（已停用）' if row['project_type_active'] is False else '')),
                {0:row['project_name']+'\n批次：'+row['batch_name'],3:row['count_exclusion_reason'] or state}))
        self._queue_project_layout()
        self._replace_rows(self.table,records)
        self.show_details()

    def show_details(self):
        chosen=self._selected_projects()
        pid = chosen[0]['project_id'] if len(chosen)==1 else None
        if pid is None or self._history is None:
            self.events = []
            self._details_signature = None
            self._replace_rows(self.details,[])
            self.details_hint.setText('请选择单个项目查看交付明细。' if len(chosen)>1 else '在项目记录中选择一个项目，查看交付明细。')
            self._update_open_action()
            return
        events = [event for event in self._history['events'] if event['project_id'] == pid
                  and (self.show_voided.isChecked() or not event['voided'])]
        self.details_hint.setText('所选项目没有符合当前筛选的交付明细。')
        signature = (pid,self._day,tuple(tuple(sorted(event.items())) for event in events))
        if signature != self._details_signature:
            self.events = events
            records = []
            for event in events:
                tip = '首次有效交付：'+(beijing_timestamp(event['first_at']) or '无')
                if event.get('prior_delivery'):
                    tip = '历史已交付，首次日期未记录；本次作为补交，不增加交付数。'
                    if event.get('prior_delivery_reason'):
                        tip += '\n历史交付说明：'+event['prior_delivery_reason']
                if event['management_reason']:
                    tip += '\n管理备注：'+event['management_reason']
                if event['managed_at']:
                    tip += '\n处理时间：'+beijing_timestamp(event['managed_at'])
                if not event.get('included_in_workload',True):
                    tip += '\n该项目不计入个人工作量；项目状态和交付记录保持。'
                    if event.get('count_exclusion_reason'):
                        tip += '\n工作量说明：'+event['count_exclusion_reason']
                role = ('已撤销' if event['voided'] else '不计工作量' if not event.get('included_in_workload',True)
                        else ROLE_TEXT[event['counting_role']])
                values = (beijing_timestamp(event['occurred_at']),role,
                          KIND_TEXT.get(event['kind'],event['kind']),event['archive_path'] or '未记录归档位置')
                tips = {1:tip,2:'升级前已保存的交付事实，不补猜历史次数。'} if event['kind'] == 'legacy' else {1:tip}
                records.append((event['id'],values,tips))
            self._replace_rows(self.details,records,choose_first=True)
            self._details_signature = signature
        self._update_open_action()

    def _selected_events(self):
        ids = {self.details.item(index.row(),0).data(Qt.UserRole) for index in self.details.selectionModel().selectedRows()}
        return [event for event in self.events if event['id'] in ids]

    def _selected_projects(self):
        ids={self.table.item(index.row(),0).data(Qt.UserRole) for index in self.table.selectionModel().selectedRows()}
        return [row for row in self.rows if row['project_id'] in ids]

    def _selected_archive_folder(self):
        selected = self._selected_key(self.details)
        event = next((row for row in self.events if row['id'] == selected),None)
        if not event or not event['archive_path']:
            return None
        path = Path(event['archive_path'])
        if event['kind'] in ('auto-zip','manual-zip') or (event['kind'] == 'legacy' and path.suffix.casefold() == '.zip'):
            path = path.parent
        return str(path)

    def _update_open_action(self):
        events = self._selected_events()
        idle = self._actions_available()
        self.export_button.setEnabled(idle)
        self.open_folder_action.setEnabled(idle and self._selected_archive_folder() is not None)
        self.void_action.setEnabled(idle and any(not event['voided'] for event in events))
        self.restore_action.setEnabled(idle and any(event['voided'] for event in events))
        projects=self._selected_projects()
        self.exclude_count_action.setEnabled(idle and any(row['included_in_workload'] for row in projects))
        self.include_count_action.setEnabled(idle and any(not row['included_in_workload'] for row in projects))
        editable = bool(projects) and all(not row['project_archived'] and not row['project_deleted'] for row in projects)
        self.change_type_action.setEnabled(idle and editable)
        self.change_type_action.setToolTip('归档或已删除的项目不能修改类型。' if projects and not editable
                                         else '修改所选项目的当前类型，交付日期、状态和工作量资格保持不变。')
        self.project_actions_button.setText(f'项目操作（{len(projects)}）' if len(projects)>1 else '项目操作')
        self.project_actions_button.setEnabled(idle)
        self.record_actions_button.setEnabled(idle)
        self.project_heading.setText(f'项目记录 · {len(self.rows)}')
        self.compact_tabs.setTabText(0,f'项目记录（{len(self.rows)}）')
        self.compact_tabs.setTabText(1,f'交付明细（{len(self.events)}）')
        self._sync_record_views()

    def open_selected_archive(self):
        if not self._actions_available():
            return
        folder = self._selected_archive_folder()
        if folder:
            self.folder_requested.emit(folder)

    def export_projects(self):
        if not self._actions_available():
            return
        try:
            from export_ui import choose_workbook_destination
            chosen = choose_workbook_destination(self)
        except (OSError, ValueError) as error:
            self._set_status('导出未完成：' + str(error))
            return
        if chosen is None:
            self._set_status('已取消导出。',temporary=True)
            return
        target, target_id = chosen
        def run_export(**kwargs):
            from delivery_export import export_delivered_projects
            return export_delivered_projects(self.service.db, target,
                replace_existing=target_id is not None, expected_target_identity=target_id, **kwargs)
        self.worker = FileJob(run_export, self)
        self.worker.management_kind = 'export'
        self.worker.finished.connect(self._export_finished)
        self._set_status('正在导出全部有效交付项目…')
        self._update_open_action()
        self.worker.start()

    def _export_finished(self):
        worker = self.worker
        if worker is None:
            return
        self.worker = None
        if worker.cancelled:
            message = '已取消导出。'
        elif worker.error:
            message = '导出未完成：' + worker.error + '。可重试。'
        else:
            message = f"已导出 {worker.result['project_count']} 个项目，保存位置：{worker.result['path']}"
        self._set_status(message,temporary=not worker.error)
        worker.deleteLater()
        self._update_open_action()

    def _record_context_menu(self,table,menu,point):
        index = table.indexAt(point)
        if not index.isValid():
            return
        if not table.selectionModel().isRowSelected(index.row(),table.rootIndex()):
            table.selectRow(index.row())
        else:
            table.selectionModel().setCurrentIndex(index,QItemSelectionModel.NoUpdate)
        self._update_open_action()
        if self._actions_available():
            menu.exec(table.viewport().mapToGlobal(point))

    @property
    def business_busy(self):
        # 只反映本页占用的工作槽，外部排队任务不能让自己一直等待。
        return self.worker is not None or self._choosing_type or self._correction_dialog is not None

    def set_busy(self, busy):
        if self._external_busy != busy:
            self._external_busy = busy
            self._update_open_action()

    def _actions_available(self):
        return not (self._external_busy or self.business_busy)

    def manage_project_types(self):
        if not self._actions_available():
            return
        selected = self._selected_projects()
        if not selected or any(row['project_archived'] or row['project_deleted'] for row in selected):
            return
        ids = [row['project_id'] for row in selected]
        self._choosing_type = True
        self._update_open_action()
        try:
            projects = [self.service.get_project(pid) for pid in ids]
            if any(row.get('archived_at') or row.get('deleted_at') for row in projects):
                raise ValueError('所选项目已归档或删除，请刷新后重试。')
            accepted,type_id = choose_project_type(self.service,self,projects)
        except Exception as error:
            self._set_status('操作未完成：'+str(error)+'。可重试。')
            return
        finally:
            self._choosing_type = False
            # 字典管理即刻保存，取消项目分类选择后也应显示改名和启停结果。
            self.refresh()
            self._update_open_action()
        if not accepted:
            return
        self.worker = FileJob(lambda **_:self.service.set_project_types(ids,type_id),self)
        self.worker.management_kind = 'project_type'
        self.worker.managed_project_id = None
        self.worker.finished.connect(self._mutation_finished)
        self._set_status('正在修改所选项目的类型…')
        self._update_open_action()
        self.worker.start()

    def _confirm_correction(self,title,text,draft_key):
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        apply_to(dialog)
        dialog.resize(510,300)
        layout = QVBoxLayout(dialog)
        description = QTextBrowser()
        description.setPlainText(text)
        description.setAccessibleName('本次操作及影响')
        layout.addWidget(description,1)
        reason_label = label('说明（可选）')
        reason = QLineEdit()
        reason.setObjectName('deliveryCorrectionReason')
        reason.setAccessibleName('纠正说明（可选）')
        reason.setMaxLength(1000)
        reason.setText(self._reason_drafts.get(draft_key,''))
        reason_label.setBuddy(reason)
        layout.addWidget(reason_label)
        layout.addWidget(reason)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel)
        confirm = buttons.button(QDialogButtonBox.Ok)
        confirm.setText('确认')
        confirm.setAutoDefault(False)
        cancel = buttons.button(QDialogButtonBox.Cancel)
        cancel.setText('取消')
        cancel.setDefault(True)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        reason.setFocus()
        self._correction_dialog = dialog
        self._update_open_action()
        try:
            accepted = dialog.exec() == QDialog.Accepted
            draft = reason.text()
            if draft:
                self._reason_drafts[draft_key] = draft
            else:
                self._reason_drafts.pop(draft_key,None)
            return draft if accepted else None
        finally:
            self._correction_dialog = None
            self._update_open_action()
            dialog.deleteLater()

    def manage_workload(self,included):
        if not self._actions_available():
            return
        projects=[row for row in self._selected_projects() if row['included_in_workload']!=included]
        if not projects:
            return
        names='\n'.join(row['project_name'] for row in projects[:5])
        if len(projects)>5:
            names+=f'\n另有 {len(projects)-5} 个项目'
        verb='恢复计入工作量' if included else '不计入工作量'
        text=f'将所选 {len(projects)} 个项目设为“{verb}”？\n\n'
        text+=('恢复后按原首次有效交付日期统计，不会把历史任务改算到今天。' if included
               else '适用于代他人交付。该项目的历史与后续交付均不计入个人工作量，可随时恢复。')
        text+='\n项目状态保持不变，原文件、交付记录和归档保留。\n\n'+names
        ids=[row['project_id'] for row in projects]
        draft_key = ('workload',included,tuple(sorted(ids)))
        reason = self._confirm_correction(verb,text,draft_key)
        if reason is None:
            return
        self.worker=FileJob(lambda **_:self.service.set_project_delivery_counting(ids,included,confirmed=True,reason=reason),self)
        self.worker.management_kind='workload'
        self.worker.correction_draft_key=draft_key
        self.worker.managed_project_id=None
        self.worker.finished.connect(self._mutation_finished)
        self._set_status('正在调整所选项目的工作量…')
        self._update_open_action()
        self.worker.start()

    def manage_selected(self,voided):
        if not self._actions_available():
            return
        events = [event for event in self._selected_events() if event['voided'] != voided]
        if not events:
            return
        verb = '撤销' if voided else '恢复'
        names = '、'.join(dict.fromkeys(event['project_name'] for event in events))
        text = f'{verb}所选 {len(events)} 条交付记录？\n\n'
        text += ('撤销后这些记录不参与统计；如果还有其他有效交付，将以最早的一次计数。' if voided
                 else '恢复后将重新计算首次有效交付，计数日期可能回到更早的一天。')
        text += ('\n对应项目将退回待交付。' if voided
                 else '\n恢复只恢复记录的统计有效性，项目当前状态保持不变。')
        text += f'\n原文件、原始记录和归档文件保留。\n\n项目：{names}\n\n'
        text += '\n'.join(beijing_timestamp(event['occurred_at'])+' · '+ROLE_TEXT[event['counting_role']]
                          for event in events[:5])
        if len(events)>5:
            text += f'\n另有 {len(events)-5} 条已选记录。'
        ids = [event['id'] for event in events]
        draft_key = ('event',voided,tuple(sorted(ids)))
        reason = self._confirm_correction(verb+'交付记录',text,draft_key)
        if reason is None:
            return
        self.worker = FileJob(lambda **_:self.service.db.set_delivery_events_voided(
            ids,voided=voided,confirmed=True,reason=reason),self)
        self.worker.correction_draft_key = draft_key
        self.worker.managed_project_id = events[0]['project_id']
        self.worker.finished.connect(self._mutation_finished)
        self._set_status('正在'+verb+'所选记录…')
        self._update_open_action()
        self.worker.start()

    def _mutation_finished(self):
        worker = self.worker
        if worker is None:
            return
        self.worker = None
        successful = not (worker.error or worker.cancelled)
        if worker.error:
            message = '操作未完成：'+worker.error+'。可重试。'
        elif worker.cancelled:
            message = '已取消操作。'
        elif getattr(worker,'management_kind',None)=='project_type':
            message = f"已修改 {worker.result['changed_count']} 个项目的类型，按当前类型统计；首次交付日期和工作量资格保持不变。"
        elif getattr(worker,'management_kind',None)=='workload':
            count=worker.result['changed_count']
            message = (f'已恢复 {count} 个项目计入工作量，按原首次有效交付日期统计；项目状态保持不变。'
                       if worker.result['included'] else
                       f'已将 {count} 个项目设为不计工作量；项目状态、交付记录和归档保持不变。')
        else:
            verb = '撤销' if worker.result['voided'] else '恢复'
            message = f"已{verb} {worker.result['changed_count']} 条记录，统计已更新。"
            reset_projects = worker.result.get('reset_project_ids', [])
            if reset_projects:
                message = f"已撤销 {worker.result['changed_count']} 条记录，{len(reset_projects)} 个项目已退回待交付，原文件和归档保留。"
        self._set_status(message,temporary=not worker.error)
        if successful:
            self._reason_drafts.pop(getattr(worker,'correction_draft_key',None),None)
            self.operation_completed.emit(message)
            self.statistics_changed.emit()
        worker.deleteLater()
        self.refresh()
        if successful and self._selected_key(self.table)==worker.managed_project_id:
            self.details.clearSelection()
            self.details.setCurrentCell(-1,-1)
        self._update_open_action()

    def resizeEvent(self,event):
        if hasattr(self,'details_hint'):
            self._summary_layout_timer.start(0)
            if self.embedded:
                self._sync_embedded_layout()
            else:
                # Keep full-height inputs without squeezing a wrapped project
                # row below one readable line group in the 680x460 view.
                margin = 8
                self.layout().setContentsMargins(margin, margin, margin, margin)
                self.layout().setSpacing(4)
        super().resizeEvent(event)

    def eventFilter(self, watched, event):
        if (watched is getattr(self, 'record_splitter', None)
                and event.type() == QEvent.Resize and self.isVisible()):
            # 统计和提示的行高也会改变可用空间，按分隔器的实际尺寸恢复偏好。
            self._panel_layout_timer.start(0)
        return super().eventFilter(watched, event)

    def _populate_compact_filters(self):
        stats = self.type_stats_menu
        stats.clear()
        if self._history is None:
            stats.addAction('暂不可读，请刷新重试').setEnabled(False)
        else:
            stats.addAction(f"合计 {self._history['project_count']} 个项目").setEnabled(False)
            for row in self._history.get('type_counts',[]):
                name = (row['type_name'] or '未分类')+('（已停用）' if row.get('active') is False else '')
                stats.addAction(f"{name}：{row['project_count']}").setEnabled(False)

    def _sync_embedded_layout(self,*_):
        if not self.embedded or not hasattr(self,'status_note'):
            return
        host = self.window()
        compact = host.height()<620 or host.width()<820
        if compact != self._compact_embedded or not self._embedded_layout_initialized:
            if compact and self.expand_details_action.isChecked():
                with QSignalBlocker(self.compact_tabs):
                    self.compact_tabs.setCurrentIndex(1)
            self._compact_embedded = compact
            self._embedded_layout_initialized = True
        self.compact_view_bar.setVisible(compact)
        self.table.setMinimumHeight(92)
        self.details.setMinimumHeight(92)
        self._summary_layout_timer.start(0)
        self.status_note.setMaximumHeight(32 if compact else 16777215)
        vertical = 6 if compact else 9
        left = right = vertical
        header = getattr(host, 'header_surface', None)
        if header is not None:
            margins = header.layout().contentsMargins()
            # 页签的原生框已经给内容区加了一层内边距。
            inset = self.mapTo(host, QPoint()).x() - header.mapTo(host, QPoint()).x()
            left = max(0, margins.left() - inset)
            right = max(0, margins.right() - inset)
        self.layout().setContentsMargins(left, vertical, right, vertical)
        self.layout().setSpacing(4 if compact else 6)
        self._sync_record_views()

    def _toggle_details(self):
        self._details_collapsed = not self._details_collapsed
        self._sync_record_views()

    def _sync_record_views(self,*_):
        compact = self._compact_embedded
        detail_tab = self.compact_tabs.currentIndex()==1
        has_details = bool(self.events)
        if self.expand_details_action.isChecked() and (not has_details or (compact and not detail_tab)):
            with QSignalBlocker(self.expand_details_action):
                self.expand_details_action.setChecked(False)
        expanded = self.expand_details_action.isChecked()
        self.expand_details_action.setText('恢复分栏' if expanded else '放大明细')
        self.expand_details_action.setVisible(not compact)
        show_projects = not detail_tab if compact else not expanded
        show_details = detail_tab if compact else has_details and not self._details_collapsed
        restore_sizes = show_projects and show_details and (self.project_panel.isHidden() or self.detail_panel.isHidden())
        self.project_panel.setVisible(show_projects)
        self.detail_panel.setVisible(show_details)
        self.project_heading.setVisible(not compact)
        project_context = f"项目：{self.events[0]['project_name']}" if compact and has_details else ''
        self.detail_heading.setText(project_context or f'交付明细 · {len(self.events)}')
        self.detail_heading.setAccessibleName(project_context)
        self.detail_heading.setToolTip(f'<span>{escape(project_context)}</span>' if project_context else '')
        self.detail_heading.setVisible(not compact or has_details)
        self.details.setVisible(has_details)
        self.details_hint.setVisible(not has_details)
        self.project_actions_button.setVisible(show_projects and self.table.selectionModel().hasSelection())
        self.details_toggle.setVisible(not compact and show_projects and has_details)
        self.details_toggle.setText('查看明细' if self._details_collapsed else '收起明细')
        self.record_actions_button.setVisible(show_details and has_details)
        if restore_sizes:
            self._panel_layout_timer.start(0)

    def heightForWidth(self,width):
        # 外层滚动只用于小窗口；不能用两张表的首选高度把正常窗口撑长。
        layout = self.layout()
        if getattr(self,'embedded',False) and layout is not None:
            minimum = layout.minimumHeightForWidth(width)
            if minimum >= 0:
                return minimum
        return super().heightForWidth(width)

    def shutdown(self):
        if self._correction_dialog is not None:
            self._correction_dialog.reject()
        self.timer.stop()
        self._project_layout_timer.stop()
        self._summary_layout_timer.stop()
        self._panel_layout_timer.stop()
        if self.worker:
            if not self.worker.wait(15000):
                raise RuntimeError('交付记录管理操作仍未结束，请稍后再退出。')
            callback = self._export_finished if getattr(self.worker,'management_kind',None)=='export' else self._mutation_finished
            self.worker.finished.disconnect(callback)
            callback()
        self.close()

    def _fit_visible_screen(self):
        if self.embedded or not self.isVisible():
            return
        area = self.screen().availableGeometry().adjusted(6,6,-6,-6)
        frame = self.frameGeometry()
        # 在当前屏幕居中，避免靠下的父窗口把管理按钮带到任务栏后方。
        frame.moveCenter(area.center())
        self.move(max(area.left(),min(frame.left(),area.right()-frame.width()+1)),
                  max(area.top(),min(frame.top(),area.bottom()-frame.height()+1)))

    def showEvent(self,event):
        super().showEvent(event)
        self._sync_embedded_layout()
        self._fit_visible_screen()
        # Windows 首次映射可能在 showEvent 返回后重设居中位置，再核对一次可用工作区。
        if not self.embedded:
            QTimer.singleShot(0,self._fit_visible_screen)
        self.refresh()
        self._queue_project_layout()
        self._panel_layout_timer.start(0)
        self._summary_layout_timer.start(0)
        self.timer.start()

    def hideEvent(self,event):
        self.timer.stop()
        self._project_layout_timer.stop()
        self._summary_layout_timer.stop()
        self._panel_layout_timer.stop()
        self._save_panel_sizes()
        super().hideEvent(event)

    def closeEvent(self,event):
        if self.worker or (self._correction_dialog is not None and self._correction_dialog.isVisible()):
            self._set_status('操作进行中，请稍后关闭。')
            event.ignore()
            return
        self.timer.stop()
        self._project_layout_timer.stop()
        self._summary_layout_timer.stop()
        self._panel_layout_timer.stop()
        self._save_panel_sizes()
        super().closeEvent(event)

    def reject(self):
        # Escape 仍可关闭子确认框，但不能把主窗口当前内容页关成空白。
        if not self.embedded:
            super().reject()
