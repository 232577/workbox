"""交付历史与统计管理：首次有效交付计数，误记可撤销和恢复。"""
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QDate, QLocale, QSignalBlocker, QTimer, Signal, QItemSelectionModel
from PySide6.QtGui import QTextOption
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDateEdit, QDialog,
    QHBoxLayout, QHeaderView, QLineEdit, QMenu, QMessageBox, QSizePolicy, QTextBrowser,
    QTableWidget, QTableWidgetItem, QVBoxLayout)

from compact_ui import button, label, WrappedNameDelegate
from theme import apply_to
from ui_components import LineEdit as QLineEdit, CheckBox as QCheckBox
from database import DELIVERY_TIMEZONE
from ui import FileJob
from project_types_ui import choose_project_type

KIND_TEXT = {'auto-zip':'ZIP 自动交付','manual-zip':'手动 ZIP 交付',
             'manual-files':'手动材料交付','legacy':'旧交付事实'}
ROLE_TEXT = {'first':'首次计数','repeat':'补交不计','voided':'已撤销'}


def beijing_timestamp(value):
    try:
        moment = datetime.fromisoformat(value.replace('Z','+00:00'))
        return value if moment.tzinfo is None else moment.astimezone(DELIVERY_TIMEZONE).strftime('%Y-%m-%d %H:%M:%S')
    except (TypeError,ValueError,AttributeError):
        return str(value or '')


class DeliveryProjectTable(QTableWidget):
    width_about_to_change = Signal()

    def resizeEvent(self,event):
        if event.oldSize().width() != event.size().width():
            self.width_about_to_change.emit()
        super().resizeEvent(event)


class TodayDeliveryDialog(QDialog):
    folder_requested = Signal(str)
    statistics_changed = Signal()
    operation_completed = Signal(str)

    def __init__(self,service,parent=None):
        super().__init__(parent)
        self.service = service
        self.rows,self.events = [],[]
        self._summary_signature = self._details_signature = None
        self._history,self._day = None,None
        self._following_today = True
        self._choosing_type = False
        self.worker = None
        self.setWindowTitle('交付记录管理')
        self.setModal(False)
        apply_to(self)
        self.setMinimumSize(680,460)
        area = self.screen().availableGeometry()
        self.resize(min(980,area.width()-24),min(680,area.height()-48))
        layout = QVBoxLayout(self)
        layout.setSpacing(6)
        self.scope_note = label('按北京时间统计个人工作量。代交项目可设为不计工作量，已交付状态和文件保留；真正误记请用下方“撤销误记”。','subtle',True)
        layout.addWidget(self.scope_note)
        controls = QHBoxLayout()
        self.today_button = button('今天')
        self.date_edit = QDateEdit()
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
        for widget in (self.today_button,self.date_edit,self.all_dates,self.show_voided):
            controls.addWidget(widget)
        controls.addWidget(self.search,1)
        controls.addWidget(self.refresh_button)
        layout.addLayout(controls)
        self.summary = label('','subtle',True)
        layout.addWidget(self.summary)
        self.type_total = label('',wrap=True)
        font = self.type_total.font()
        font.setBold(True)
        self.type_total.setFont(font)
        # 原生 QLabel 用实际文字作为辅助名称，让合计及错误状态同步可读。
        self.type_total.setObjectName('deliveryTypeTotal')
        layout.addWidget(self.type_total)
        self.type_counts = QTextBrowser()
        self.type_counts.setAccessibleName('按当前类型计入工作量的项目数量')
        self.type_counts.setReadOnly(True)
        self.type_counts.document().setDocumentMargin(2)
        self.type_counts.setOpenLinks(False)
        self.type_counts.setAcceptDrops(False)
        self.type_counts.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.type_counts.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        self.type_counts.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred)
        self.type_counts.setMinimumHeight(40)
        self.type_counts.setMaximumHeight(72)
        layout.addWidget(self.type_counts)
        project_actions = QHBoxLayout()
        self.project_selection_note = label('选择项目，可按 Ctrl 多选','subtle',True)
        self.change_type_button = button('修改类型…')
        self.exclude_count_button = button('不计工作量')
        self.include_count_button = button('恢复计入')
        self.exclude_count_button.setToolTip('适用于替他人交付：只排除个人工作量，项目状态、文件和交付记录保持不变。')
        self.include_count_button.setToolTip('恢复该项目的计数资格，仍按原首次有效交付日期计数。')
        project_actions.addWidget(self.project_selection_note,1)
        project_actions.addWidget(self.change_type_button)
        project_actions.addWidget(self.exclude_count_button)
        project_actions.addWidget(self.include_count_button)
        layout.addLayout(project_actions)
        self.table = self._table('交付项目列表',['项目','批次','首次有效交付','我的工作量','项目类型'],wrapped=True)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setWordWrap(True)
        self.table.setTextElideMode(Qt.ElideNone)
        for column in (0,4):
            self.table.setItemDelegateForColumn(column,WrappedNameDelegate(self.table))
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._project_context_menu)
        for column in (0,1):
            self.table.horizontalHeader().setSectionResizeMode(column,QHeaderView.Stretch)
        for column in (2,3):
            self.table.horizontalHeader().setSectionResizeMode(column,QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4,QHeaderView.Interactive)
        self.table.setColumnWidth(4,240)
        self._project_layout_anchor = None
        self._project_layout_timer = QTimer(self)
        self._project_layout_timer.setSingleShot(True)
        self._project_layout_timer.timeout.connect(self._resize_project_rows)
        self.table.width_about_to_change.connect(self._queue_project_layout)
        self.table.horizontalHeader().sectionResized.connect(self._queue_project_layout)
        layout.addWidget(self.table,2)
        self.details_hint = label('选择项目查看交付记录，可按住 Ctrl 或 Shift 多选明细。','subtle',True)
        layout.addWidget(self.details_hint)
        self.details = self._table('所选项目交付明细',['交付时间（北京时间）','计数说明','交付方式','归档位置'])
        self.event_table = self.details
        self.details.setSelectionMode(QAbstractItemView.ExtendedSelection)
        for column in range(3):
            self.details.horizontalHeader().setSectionResizeMode(column,QHeaderView.ResizeToContents)
        self.details.horizontalHeader().setSectionResizeMode(3,QHeaderView.Stretch)
        layout.addWidget(self.details,1)
        self.reason = QLineEdit()
        self.reason.setAccessibleName('撤销或恢复备注')
        self.reason.setPlaceholderText('调整工作量或撤销、恢复记录的说明（可选）')
        self.reason.setMaxLength(1000)
        layout.addWidget(self.reason)
        actions = QHBoxLayout()
        self.open_folder_button = button('打开归档目录')
        self.void_button = button('撤销误记')
        self.restore_button = button('恢复记录')
        self.close_button = button('关闭')
        for widget in (self.open_folder_button,self.void_button,self.restore_button):
            actions.addWidget(widget)
        actions.addStretch()
        actions.addWidget(self.close_button)
        layout.addLayout(actions)
        self.status_note = label('','subtle',True)
        self.status_note.hide()
        layout.addWidget(self.status_note)
        for widget in (self.today_button,self.refresh_button,self.open_folder_button,self.void_button,self.restore_button,self.close_button,self.exclude_count_button,self.include_count_button,self.change_type_button):
            widget.setAutoDefault(False)
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.refresh)
        self.refresh_button.clicked.connect(self.refresh)
        self.today_button.clicked.connect(self.go_today)
        self.date_edit.dateChanged.connect(self._date_changed)
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
        self.close_button.clicked.connect(self.close)
        self.refresh()

    @staticmethod
    def _table(name,headings,*,wrapped=False):
        table = (DeliveryProjectTable if wrapped else QTableWidget)(0,len(headings))
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

    def _scope_changed(self,*_):
        self.date_edit.setEnabled(not self.all_dates.isChecked())
        self.refresh()

    def go_today(self):
        self._following_today = True
        with QSignalBlocker(self.all_dates):
            self.all_dates.setChecked(False)
        self.date_edit.setEnabled(True)
        self.refresh()

    def refresh(self,*_):
        try:
            if self._following_today:
                today = self.service.db.today_delivery_summary()['day']
                with QSignalBlocker(self.date_edit):
                    self.date_edit.setDate(QDate.fromString(today,'yyyy-MM-dd'))
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

    def open_selected_archive(self):
        if self.worker or self._choosing_type:
            return
        folder = self._selected_archive_folder()
        if folder:
            self.folder_requested.emit(folder)

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
            narrow = self.width()<820
            if self.table.isColumnHidden(1) != narrow:
                self._queue_project_layout()
                self.table.setColumnHidden(1,narrow)
                # 窄窗将日期与时分秒分成两行，把长名称的阅读宽度留给项目及类型。
                header = self.table.horizontalHeader()
                header.setSectionResizeMode(2,QHeaderView.Fixed if narrow else QHeaderView.ResizeToContents)
                if narrow:
                    self.table.setColumnWidth(2,110)
            compact=self.height()<580
            self.scope_note.setVisible(not compact)
            self.details_hint.setVisible(not compact)
            self.type_counts.setMaximumHeight(40 if compact else 72)
        super().resizeEvent(event)

    def shutdown(self):
        self.timer.stop()
        self._project_layout_timer.stop()
        if self.worker:
            if not self.worker.wait(15000):
                raise RuntimeError('交付记录管理操作仍未结束，请稍后再退出。')
            self.worker.finished.disconnect(self._mutation_finished)
            self._mutation_finished()
        self.close()

    def _fit_visible_screen(self):
        if not self.isVisible():
            return
        area = self.screen().availableGeometry().adjusted(6,6,-6,-6)
        frame = self.frameGeometry()
        # 在当前屏幕居中，避免靠下的父窗口把管理按钮带到任务栏后方。
        frame.moveCenter(area.center())
        self.move(max(area.left(),min(frame.left(),area.right()-frame.width()+1)),
                  max(area.top(),min(frame.top(),area.bottom()-frame.height()+1)))

    def showEvent(self,event):
        super().showEvent(event)
        self._fit_visible_screen()
        # Windows 首次映射可能在 showEvent 返回后重设居中位置，再核对一次可用工作区。
        QTimer.singleShot(0,self._fit_visible_screen)
        self.refresh()
        self._queue_project_layout()
        self.timer.start()

    def hideEvent(self,event):
        self.timer.stop()
        self._project_layout_timer.stop()
        super().hideEvent(event)

    def closeEvent(self,event):
        self.timer.stop()
        self._project_layout_timer.stop()
        super().closeEvent(event)
