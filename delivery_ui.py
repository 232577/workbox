"""交付历史与统计管理：首次有效交付计数，误记可撤销和恢复。"""
from datetime import datetime
from math import ceil
from pathlib import Path

from PySide6.QtCore import Qt, QDate, QLocale, QSignalBlocker, QTimer, Signal, QItemSelectionModel, QStandardPaths
from PySide6.QtGui import QTextOption
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog,
    QHBoxLayout, QHeaderView, QLineEdit, QMenu, QMessageBox, QSizePolicy, QTextBrowser,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QTabBar, QWidget, QSplitter, QFileDialog)

from compact_ui import button, label, WrappedNameDelegate
from theme import apply_to
from ui_components import LineEdit as QLineEdit, CheckBox as QCheckBox
from fluent_views import WorkbenchTableWidget
from qfluentwidgets import CardWidget
from database import DELIVERY_TIMEZONE
from ui import FileJob
from project_types_ui import choose_project_type
from date_edit import DateEdit
from native_files import identity

KIND_TEXT = {'auto-zip':'ZIP 自动交付','manual-zip':'手动 ZIP 交付',
             'manual-files':'手动材料交付','legacy':'旧交付事实'}
ROLE_TEXT = {'first':'首次计数','repeat':'补交不计','voided':'已撤销'}


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
        try:
            sizes = service.get_setting('delivery_panel_sizes', [400, 400])
        except Exception:
            sizes = None
        self._panel_sizes = sizes if (isinstance(sizes, list) and len(sizes) == 2
            and all(type(size) is int and 0 < size <= 100000 for size in sizes)) else [400, 400]
        self.rows,self.events = [],[]
        self._summary_signature = self._details_signature = None
        self._history,self._day = None,None
        self._following_today = True
        self._choosing_type = False
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
        self.scope_note = label('按北京时间统计个人工作量。代交项目可设为不计工作量，已交付状态和文件保留；真正误记请用下方“撤销误记”。','subtle',True)
        layout.addWidget(self.scope_note)
        controls = QHBoxLayout()
        self.today_button = button('今天')
        self.date_edit = DateEdit()
        self.date_edit.setAccessibleName('交付记录日期')
        self.date_edit.setLocale(QLocale(QLocale.Chinese,QLocale.China))
        self.date_edit.setDisplayFormat('yyyy-MM-dd')
        self.date_edit.setCalendarPopup(True)
        try:
            today = service.db.today_delivery_summary()['day']
        except Exception:
            today = datetime.now(DELIVERY_TIMEZONE).date().isoformat()
        self.date_edit.setDate(QDate.fromString(today,'yyyy-MM-dd'))
        self.all_dates = QCheckBox('全部日期')
        self.show_voided = QCheckBox('显示已撤销')
        self.search = QLineEdit()
        self.search.setAccessibleName('搜索交付项目或批次')
        self.search.setPlaceholderText('项目或批次')
        self.search.setClearButtonEnabled(True)
        self.search.setMaxLength(200)
        self.refresh_button = button('刷新')
        self.export_button = button('导出表格')
        self.export_button.setAccessibleName('导出全部有效交付项目')
        self.export_button.setToolTip('导出全部有效交付项目，每个项目一行，不受当前筛选影响。')
        for widget in (self.today_button,self.date_edit,self.all_dates,self.show_voided):
            controls.addWidget(widget)
        if embedded:
            controls.addStretch()
            layout.addLayout(controls)
            self.date_controls = controls
            controls = QHBoxLayout()
        self.search_controls = controls
        controls.addWidget(self.search,1)
        controls.addWidget(self.refresh_button)
        controls.addWidget(self.export_button)
        self.compact_filter_button = button('筛选')
        self.compact_filter_button.setAccessibleName('交付筛选与统计')
        self.compact_filter_menu = QMenu(self.compact_filter_button)
        self.compact_filter_button.setMenu(self.compact_filter_menu)
        self.compact_filter_menu.aboutToShow.connect(self._populate_compact_filters)
        self.compact_filter_button.hide()
        controls.addWidget(self.compact_filter_button)
        layout.addLayout(controls)
        self.summary = label('','subtle',True)
        layout.addWidget(self.summary)
        self.type_summary_card = CardWidget(self)
        self.type_summary_card.setBorderRadius(8)
        self.type_summary_card.setAccessibleName('交付类型统计')
        self.type_summary_card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self.type_summary_layout = QVBoxLayout(self.type_summary_card)
        self.type_summary_layout.setContentsMargins(12, 10, 12, 10)
        self.type_summary_layout.setSpacing(4)
        self.type_total = label('',wrap=True)
        font = self.type_total.font()
        font.setBold(True)
        self.type_total.setFont(font)
        # 原生 QLabel 用实际文字作为辅助名称，让合计及错误状态同步可读。
        self.type_total.setObjectName('deliveryTypeTotal')
        self.type_summary_layout.addWidget(self.type_total)
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
        self.type_counts.setMinimumHeight(28)
        self.type_counts.setMaximumHeight(64)
        self._summary_layout_timer = QTimer(self)
        self._summary_layout_timer.setSingleShot(True)
        self._summary_layout_timer.timeout.connect(self._fit_type_summary)
        self.type_counts.document().documentLayout().documentSizeChanged.connect(
            lambda *_: self._summary_layout_timer.start(0))
        self.type_summary_layout.addWidget(self.type_counts)
        layout.addWidget(self.type_summary_card)
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
        self.compact_total = label('', 'subtle')
        compact_view_layout.addStretch()
        compact_view_layout.addWidget(self.compact_total)
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
        project_actions = QHBoxLayout()
        self.project_selection_note = label('选择项目，可按 Ctrl 多选','subtle',True)
        self.project_selection_note.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred)
        self.change_type_button = button('修改类型…')
        self.exclude_count_button = button('不计工作量')
        self.include_count_button = button('恢复计入')
        self.exclude_count_button.setToolTip('适用于替他人交付：只排除个人工作量，项目状态、文件和交付记录保持不变。')
        self.include_count_button.setToolTip('恢复该项目的计数资格，仍按原首次有效交付日期计数。')
        project_actions.addWidget(self.project_selection_note,1)
        project_actions.addWidget(self.change_type_button)
        project_actions.addWidget(self.exclude_count_button)
        project_actions.addWidget(self.include_count_button)
        project_layout.addLayout(project_actions)
        self.table = self._table('交付项目列表',['项目','批次','首次有效交付','我的工作量','项目类型'],wrapped=True)
        self.table.setMinimumHeight(116)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setWordWrap(True)
        self.table.setTextElideMode(Qt.ElideNone)
        self.table.setWrapColumns({0, 4})
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._project_context_menu)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        for column, width in enumerate((250, 100, 150, 100, 220)):
            self.table.setColumnWidth(column, width)
        self._project_layout_anchor = None
        self._project_layout_timer = QTimer(self)
        self._project_layout_timer.setSingleShot(True)
        self._project_layout_timer.timeout.connect(self._resize_project_rows)
        self.table.width_about_to_change.connect(self._queue_project_layout)
        self.table.horizontalHeader().sectionResized.connect(self._queue_project_layout)
        project_layout.addWidget(self.table,1)
        self.details_hint = label('选择项目查看交付记录，可按住 Ctrl 或 Shift 多选明细。','subtle',True)
        detail_layout.addWidget(self.details_hint)
        self.details = self._table('所选项目交付明细',['交付时间（北京时间）','计数说明','交付方式','归档位置'])
        self.event_table = self.details
        self.details.setSelectionMode(QAbstractItemView.ExtendedSelection)
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
        layout.addWidget(self.record_splitter,1)
        self.reason = QLineEdit()
        self.reason.setAccessibleName('撤销或恢复备注')
        self.reason.setPlaceholderText('调整工作量或撤销、恢复记录的说明（可选）')
        self.reason.setMaxLength(1000)
        layout.addWidget(self.reason)
        actions = QHBoxLayout()
        self.open_folder_button = button('打开归档目录')
        self.void_button = button('撤销误记')
        self.restore_button = button('恢复记录')
        self.expand_details_button = button('放大明细')
        self.expand_details_button.setCheckable(True)
        self.expand_details_button.setToolTip('让交付明细使用整个列表区域，再次点击恢复上下分栏')
        self.close_button = button('返回项目' if embedded else '关闭')
        self.compact_actions_button = button('项目操作')
        self.compact_actions_button.setAccessibleName('所选交付记录操作')
        self.compact_action_menu = QMenu(self.compact_actions_button)
        self.compact_actions_button.setMenu(self.compact_action_menu)
        self.compact_action_menu.aboutToShow.connect(self._populate_compact_actions)
        self.compact_actions_button.hide()
        actions.addWidget(self.compact_actions_button)
        for widget in (self.open_folder_button,self.void_button,self.restore_button):
            actions.addWidget(widget)
        actions.addWidget(self.expand_details_button)
        actions.addStretch()
        actions.addWidget(self.close_button)
        layout.addLayout(actions)
        self.status_note = label('','subtle',True)
        self.status_note.hide()
        layout.addWidget(self.status_note)
        for widget in (self.today_button,self.refresh_button,self.export_button,self.open_folder_button,self.void_button,self.restore_button,self.close_button,self.exclude_count_button,self.include_count_button,self.change_type_button,self.expand_details_button):
            widget.setAutoDefault(False)
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.refresh)
        self.refresh_button.clicked.connect(self.refresh)
        self.export_button.clicked.connect(self.export_projects)
        self.today_button.clicked.connect(self.go_today)
        self.date_edit.dateChanged.connect(self._date_changed)
        self.date_edit.input_rejected.connect(self._date_input_rejected)
        self.date_edit.input_accepted.connect(self._date_input_accepted)
        self.all_dates.toggled.connect(self._scope_changed)
        self.show_voided.toggled.connect(self.refresh)
        self.search.textChanged.connect(self.refresh)
        self.table.itemSelectionChanged.connect(self.show_details)
        self.details.itemSelectionChanged.connect(self._update_open_action)
        self.open_folder_button.clicked.connect(self.open_selected_archive)
        self.void_button.clicked.connect(lambda:self.manage_selected(True))
        self.restore_button.clicked.connect(lambda:self.manage_selected(False))
        self.exclude_count_button.clicked.connect(lambda:self.manage_workload(False))
        self.include_count_button.clicked.connect(lambda:self.manage_workload(True))
        self.change_type_button.clicked.connect(self.manage_project_types)
        self.close_button.clicked.connect(self.return_requested.emit if embedded else self.close)
        self.expand_details_button.toggled.connect(self._sync_detail_focus)
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
        self.status_note.setText(message)
        self.status_note.show()

    def _date_input_accepted(self):
        if getattr(self, '_date_error', None) == self.status_note.text():
            self.status_note.hide()
        self._date_error = None

    def _fit_type_summary(self):
        limit = 32 if self.embedded or self.height() < 580 else 64
        height = min(limit, max(28, ceil(self.type_counts.document().size().height())
                                + 2 * self.type_counts.frameWidth()))
        if self.type_counts.maximumHeight() != height:
            self.type_counts.setMaximumHeight(height)

    def _remember_panel_sizes(self, *_):
        if not self._compact_embedded:
            sizes = self.record_splitter.sizes()
            if len(sizes) == 2 and all(sizes):
                self._panel_sizes = sizes
                self._panel_sizes_dirty = True

    def _restore_panel_sizes(self):
        if not self._compact_embedded and not self.expand_details_button.isChecked():
            available = sum(self.record_splitter.sizes())
            if available > 1:
                # 先按当前空间换算比例，避免 Qt 用新窗口的最小高度截断旧像素值。
                first = max(1, min(available - 1,
                                  round(available * self._panel_sizes[0] / sum(self._panel_sizes))))
                self.record_splitter.setSizes([first, available - first])

    def _sync_detail_focus(self, *_):
        expanded = self.expand_details_button.isChecked()
        self.expand_details_button.setText('恢复分栏' if expanded else '放大明细')
        if self.embedded:
            self._sync_embedded_layout()
        else:
            self.project_panel.setVisible(not expanded)
        if not expanded:
            self._panel_layout_timer.start(0)

    def _save_panel_sizes(self):
        if not self._panel_sizes_dirty:
            return
        try:
            self.service.set_setting('delivery_panel_sizes', self._panel_sizes)
        except Exception as error:
            self.status_note.setText('本次高度已调整，但未能保存布局：' + str(error))
            self.status_note.show()
        else:
            self._panel_sizes_dirty = False

    def _scope_changed(self,*_):
        self.date_edit.setEnabled(not self.all_dates.isChecked())
        self.refresh()

    def go_today(self):
        self.date_edit.discard_pending_input()
        self._following_today = True
        with QSignalBlocker(self.all_dates):
            self.all_dates.setChecked(False)
        self.date_edit.setEnabled(True)
        self.refresh()

    def refresh(self,*_):
        try:
            if self._following_today:
                today = self.service.db.today_delivery_summary()['day']
                date = QDate.fromString(today,'yyyy-MM-dd')
                # 同日刷新也会让 Qt 重写编辑文本，丢弃尚未输完的年份。
                if self.date_edit.date() != date:
                    with QSignalBlocker(self.date_edit):
                        self.date_edit.setDate(date)
            day = None if self.all_dates.isChecked() else self.date_edit.date().toPython()
            history = self.service.db.delivery_history(day,keyword=self.search.text())
            catalog = self.service.list_project_types(include_inactive=True)
            signature = (day,self.search.text(),self.show_voided.isChecked(),
                         tuple(tuple(sorted(row.items())) for row in history['events']),
                         tuple(tuple(sorted(row.items())) for row in catalog),
                         tuple(tuple(sorted(row.items())) for row in history.get('type_counts',[])))
        except Exception as error:
            self.summary.setText('读取交付记录失败：'+str(error)+'。可点击“刷新”重试。')
            self._history = None
            self._summary_signature = self._details_signature = None
            self.rows,self.events = [],[]
            self.type_total.setText('按当前类型 · 计入工作量 · 合计暂不可读')
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
        text = f"{scope} · 首次交付 {history['project_count']} 个项目 · 补交 {history['repeat_count']} 条 · 已撤销 {history['voided_count']} 条"
        if not history['events']:
            text += '，暂无交付记录。'
        grouped = {}
        for event in history['events']:
            if event['voided'] and not self.show_voided.isChecked():
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
        self.summary.setText(text+f' · 不计工作量 {excluded} 个项目')
        self.type_total.setText(f"按当前类型 · 计入工作量 · 合计 {history['project_count']} 个项目")
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
            self.details_hint.setText('已选多个项目，可统一调整是否计入工作量。' if len(chosen)>1 else '选择项目查看交付记录，可按住 Ctrl 或 Shift 多选明细。')
            self._update_open_action()
            return
        events = [event for event in self._history['events'] if event['project_id'] == pid
                  and (self.show_voided.isChecked() or not event['voided'])]
        self.details_hint.setText(f'所选项目显示 {len(events)} 条记录；“补交不计”不会增加交付数。')
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
        idle = self.worker is None and not self._choosing_type
        self.export_button.setEnabled(idle)
        self.open_folder_button.setEnabled(idle and self._selected_archive_folder() is not None)
        self.void_button.setEnabled(idle and any(not event['voided'] for event in events))
        self.restore_button.setEnabled(idle and any(event['voided'] for event in events))
        projects=self._selected_projects()
        self.exclude_count_button.setEnabled(idle and any(row['included_in_workload'] for row in projects))
        self.include_count_button.setEnabled(idle and any(not row['included_in_workload'] for row in projects))
        editable = bool(projects) and all(not row['project_archived'] and not row['project_deleted'] for row in projects)
        self.change_type_button.setEnabled(idle and editable)
        self.change_type_button.setToolTip('归档或已删除的项目不能修改类型。' if projects and not editable
                                         else '修改所选项目的当前类型，交付日期、状态和工作量资格保持不变。')
        self.project_selection_note.setText(f'已选 {len(projects)} 个项目' if projects else '选择项目，可按 Ctrl 多选')
        self.reason.setEnabled(idle)
        if hasattr(self,'compact_total'):
            self.compact_tabs.setTabText(0,f'项目记录（{len(self.rows)}）')
            self.compact_tabs.setTabText(1,f'交付明细（{len(self.events)}）')
            count = self._history['project_count'] if self._history is not None else None
            self.compact_total.setText(f'合计 {count} 个' if count is not None else '统计暂不可读')
            self.compact_total.setToolTip(self.summary.text()+'\n'+self.type_counts.toPlainText())

    def open_selected_archive(self):
        if self.worker or self._choosing_type:
            return
        folder = self._selected_archive_folder()
        if folder:
            self.folder_requested.emit(folder)

    def export_projects(self):
        if self.worker or self._choosing_type:
            return
        try:
            from export_ui import choose_workbook_destination
            chosen = choose_workbook_destination(self)
        except (OSError, ValueError) as error:
            self.status_note.setText('导出未完成：' + str(error))
            self.status_note.show()
            return
        if chosen is None:
            self.status_note.setText('已取消导出。')
            self.status_note.show()
            return
        target, target_id = chosen
        def run_export(**kwargs):
            from delivery_export import export_delivered_projects
            return export_delivered_projects(self.service.db, target,
                replace_existing=target_id is not None, expected_target_identity=target_id, **kwargs)
        self.worker = FileJob(run_export, self)
        self.worker.management_kind = 'export'
        self.worker.finished.connect(self._export_finished)
        self.status_note.setText('正在导出全部有效交付项目…')
        self.status_note.show()
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
        self.status_note.setText(message)
        self.status_note.setToolTip(message)
        self.status_note.show()
        worker.deleteLater()
        self._update_open_action()

    def _project_context_menu(self,point):
        index = self.table.indexAt(point)
        if not index.isValid():
            return
        if not self.table.selectionModel().isRowSelected(index.row(),self.table.rootIndex()):
            self.table.selectRow(index.row())
        self._update_open_action()
        menu = QMenu(self)
        action = menu.addAction('修改类型…')
        action.setEnabled(self.change_type_button.isEnabled())
        action.triggered.connect(self.manage_project_types)
        menu.exec(self.table.viewport().mapToGlobal(point))

    def manage_project_types(self):
        if self.worker or self._choosing_type:
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
            self.status_note.setText('操作未完成：'+str(error)+'。可重试。')
            self.status_note.show()
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
        self.status_note.setText('正在修改所选项目的类型…')
        self.status_note.show()
        self._update_open_action()
        self.worker.start()

    def manage_workload(self,included):
        if self.worker or self._choosing_type:
            return
        projects=[row for row in self._selected_projects() if row['included_in_workload']!=included]
        if not projects:
            return
        names='\n'.join(row['project_name'] for row in projects[:5])
        if len(projects)>5:
            names+=f'\n另有 {len(projects)-5} 个项目'
        verb='恢复计入工作量' if included else '不计入工作量'
        text=f'将所选 {len(projects)} 个项目设为“{verb}”？\n\n{names}\n\n'
        text+=('恢复后按原首次有效交付日期统计，不会把历史任务改算到今天。' if included
               else '适用于代他人交付。该项目的历史与后续交付均不计入个人工作量，可随时恢复。')
        text+='\n项目状态保持不变，原文件、交付记录和归档保留。'
        if QMessageBox.question(self,verb,text,QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:
            return
        ids=[row['project_id'] for row in projects]
        reason=self.reason.text()
        self.worker=FileJob(lambda **_:self.service.set_project_delivery_counting(ids,included,confirmed=True,reason=reason),self)
        self.worker.management_kind='workload'
        self.worker.managed_project_id=None
        self.worker.finished.connect(self._mutation_finished)
        self.status_note.setText('正在调整所选项目的工作量…')
        self.status_note.show()
        self._update_open_action()
        self.worker.start()

    def manage_selected(self,voided):
        if self.worker or self._choosing_type:
            return
        events = [event for event in self._selected_events() if event['voided'] != voided]
        if not events:
            return
        verb = '撤销' if voided else '恢复'
        names = '、'.join(dict.fromkeys(event['project_name'] for event in events))
        text = f'{verb}所选 {len(events)} 条交付记录？\n\n项目：{names}\n\n'
        text += '\n'.join(beijing_timestamp(event['occurred_at'])+' · '+ROLE_TEXT[event['counting_role']]
                          for event in events[:5])+'\n\n'
        if len(events)>5:
            text += f'另有 {len(events)-5} 条已选记录。\n\n'
        text += ('撤销后这些记录不参与统计；如果还有其他有效交付，将以最早的一次计数。' if voided
                 else '恢复后将重新计算首次有效交付，计数日期可能回到更早的一天。')
        text += ('\n对应项目将退回待交付。' if voided
                 else '\n恢复只恢复记录的统计有效性，项目当前状态保持不变。')
        text += '\n原文件、原始记录和归档文件保留。'
        if QMessageBox.question(self,verb+'交付记录',text,QMessageBox.Yes|QMessageBox.No,QMessageBox.No) != QMessageBox.Yes:
            return
        ids,reason = [event['id'] for event in events],self.reason.text()
        self.worker = FileJob(lambda **_:self.service.db.set_delivery_events_voided(
            ids,voided=voided,confirmed=True,reason=reason),self)
        self.worker.managed_project_id = events[0]['project_id']
        self.worker.finished.connect(self._mutation_finished)
        self.status_note.setText('正在'+verb+'所选记录…')
        self.status_note.show()
        self._update_open_action()
        self.worker.start()

    def _mutation_finished(self):
        worker = self.worker
        if worker is None:
            return
        self.worker = None
        if worker.error:
            self.status_note.setText('操作未完成：'+worker.error+'。可重试。')
        elif getattr(worker,'management_kind',None)=='project_type':
            self.status_note.setText(f"已修改 {worker.result['changed_count']} 个项目的类型，按当前类型统计；首次交付日期和工作量资格保持不变。")
            self.operation_completed.emit(self.status_note.text())
            self.statistics_changed.emit()
        elif getattr(worker,'management_kind',None)=='workload':
            count=worker.result['changed_count']
            self.status_note.setText(f'已恢复 {count} 个项目计入工作量，按原首次有效交付日期统计；项目状态保持不变。'
                                    if worker.result['included'] else
                                    f'已将 {count} 个项目设为不计工作量；项目状态、交付记录和归档保持不变。')
            self.reason.clear()
            self.operation_completed.emit(self.status_note.text())
            self.statistics_changed.emit()
        else:
            verb = '撤销' if worker.result['voided'] else '恢复'
            self.status_note.setText(f"已{verb} {worker.result['changed_count']} 条记录，统计已更新。")
            reset_projects = worker.result.get('reset_project_ids', [])
            if reset_projects:
                self.status_note.setText(f"已撤销 {worker.result['changed_count']} 条记录，{len(reset_projects)} 个项目已退回待交付，原文件和归档保留。")
            self.reason.clear()
            self.operation_completed.emit(self.status_note.text())
            self.statistics_changed.emit()
        worker.deleteLater()
        self.refresh()
        if not worker.error and self._selected_key(self.table)==worker.managed_project_id:
            self.details.clearSelection()
            self.details.setCurrentCell(-1,-1)
        self._update_open_action()

    def resizeEvent(self,event):
        if hasattr(self,'details_hint'):
            compact=self.height()<580
            self.scope_note.setVisible(not compact)
            self.details_hint.setVisible(not compact)
            self.type_summary_layout.setContentsMargins(8 if compact else 12, 6 if compact else 10,
                                                       8 if compact else 12, 6 if compact else 10)
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
        if self.isVisible() and hasattr(self, '_panel_layout_timer'):
            # 外层滚动区在切页后可能再次定尺寸，按最终可用空间恢复偏好。
            self._panel_layout_timer.start(0)

    def _populate_compact_filters(self):
        menu = self.compact_filter_menu
        menu.clear()
        menu.addAction('今天',self.go_today)
        for caption,control in (('全部日期',self.all_dates),('显示已撤销',self.show_voided)):
            action = menu.addAction(caption)
            action.setCheckable(True)
            action.setChecked(control.isChecked())
            action.toggled.connect(control.setChecked)
        menu.addAction('刷新',self.refresh)
        stats = menu.addMenu('类型统计')
        if self._history is None:
            stats.addAction('暂不可读，请刷新重试').setEnabled(False)
        else:
            stats.addAction(f"合计 {self._history['project_count']} 个项目").setEnabled(False)
            for row in self._history.get('type_counts',[]):
                stats.addAction(f"{row['type_name'] or '未分类'}：{row['project_count']}").setEnabled(False)

    def _populate_compact_actions(self):
        self._update_open_action()
        menu = self.compact_action_menu
        menu.clear()
        controls = ((self.change_type_button,self.exclude_count_button,self.include_count_button)
                    if self.compact_tabs.currentIndex()==0 else
                    (self.open_folder_button,self.void_button,self.restore_button))
        for control in controls:
            action = menu.addAction(control.text())
            action.setEnabled(control.isEnabled())
            action.setToolTip(control.toolTip())
            action.triggered.connect(control.click)

    def _sync_embedded_layout(self,*_):
        if not self.embedded or not hasattr(self,'status_note'):
            return
        host = self.window()
        compact = host.height()<620 or host.width()<820
        if compact != self._compact_embedded or not self._embedded_layout_initialized:
            if compact and self.expand_details_button.isChecked():
                with QSignalBlocker(self.compact_tabs):
                    self.compact_tabs.setCurrentIndex(1)
            self._compact_embedded = compact
            self._embedded_layout_initialized = True
            for control in (self.date_edit,self.search,self.refresh_button,self.export_button):
                self.date_controls.removeWidget(control)
                self.search_controls.removeWidget(control)
            if compact:
                self.search_controls.insertWidget(0,self.date_edit)
                self.search_controls.insertWidget(1,self.search,1)
                self.search_controls.insertWidget(2,self.refresh_button)
                self.search_controls.insertWidget(3,self.export_button)
            else:
                self.date_controls.insertWidget(1,self.date_edit)
                self.date_controls.insertWidget(4,self.search,1)
                self.date_controls.insertWidget(5,self.refresh_button)
                self.date_controls.insertWidget(6,self.export_button)
            if not compact:
                self._panel_layout_timer.start(0)
        details = self.compact_tabs.currentIndex()==1
        if compact and not details and self.expand_details_button.isChecked():
            with QSignalBlocker(self.expand_details_button):
                self.expand_details_button.setChecked(False)
        expanded = self.expand_details_button.isChecked()
        self.expand_details_button.setText('恢复分栏' if expanded else '放大明细')
        self.expand_details_button.setVisible(not compact)
        for control in (self.today_button,self.all_dates,self.show_voided,self.refresh_button,
                        self.scope_note,self.summary,self.type_summary_card,self.project_selection_note,
                        self.change_type_button,self.exclude_count_button,self.include_count_button,
                        self.details_hint,self.open_folder_button,self.void_button,self.restore_button):
            control.setVisible(not compact)
        self.compact_filter_button.setVisible(compact)
        self.compact_view_bar.setVisible(compact)
        self.compact_actions_button.setVisible(compact)
        self.compact_actions_button.setText('记录操作' if details else '项目操作')
        self.table.setVisible(not compact or not details)
        self.details.setVisible(not compact or details)
        self.project_panel.setVisible((not compact and not expanded) or (compact and not details))
        self.detail_panel.setVisible(not compact or details)
        self.table.setMinimumHeight(92)
        self.details.setMinimumHeight(92)
        self.scope_note.hide()
        self.compact_filter_button.setToolTip(self.scope_note.text())
        self.type_summary_layout.setContentsMargins(8,6,8,6)
        self._summary_layout_timer.start(0)
        self.reason.setVisible(not compact or details)
        self.status_note.setMaximumHeight(32 if compact else 16777215)
        self.status_note.setToolTip(self.status_note.text())
        self.layout().setContentsMargins(6 if compact else 9,6 if compact else 9,
                                        6 if compact else 9,6 if compact else 9)
        self.layout().setSpacing(4 if compact else 6)

    def heightForWidth(self,width):
        # 外层滚动只用于小窗口；不能用两张表的首选高度把正常窗口撑长。
        layout = self.layout()
        if getattr(self,'embedded',False) and layout is not None:
            minimum = layout.minimumHeightForWidth(width)
            if minimum >= 0:
                return minimum
        return super().heightForWidth(width)

    def shutdown(self):
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
        if self.worker:
            self.status_note.setText('操作进行中，请稍后关闭。')
            self.status_note.show()
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
