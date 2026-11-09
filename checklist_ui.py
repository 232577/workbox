"""Native, cancellable list review; no import into projects or delivery records."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import sqlite3

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QFileDialog, QFrame,
    QGridLayout, QHBoxLayout, QHeaderView, QLabel, QProgressBar, QTableWidget,
    QTableWidgetItem, QVBoxLayout)

from checklist_reader import read_tables, suggest_columns, extract_entries, filter_entries
from checklist_review import review_entries
from theme import apply_to, tokens, theme_signal
from ui import FileJob
from ui_components import button, ComboBox, LineEdit, DialogHeader, MetricStrip, StatusBanner


def text_label(text='', name=None):
    label = QLabel(text); label.setTextFormat(Qt.PlainText); label.setWordWrap(True)
    if name:
        label.setObjectName(name)
    return label


class ChecklistDialog(QDialog):
    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.worker = self.material_dialog = None
        self.results, self.tables, self.entries = [], [], []
        self._loading = self._closing = False
        self.source_name = ''
        self.setWindowTitle('WorkBox · 名单与材料核对')
        self.resize(1120, 760); self.setMinimumSize(760, 620)
        self.setObjectName('checklistWorkspace'); apply_to(self, 'dialog')
        layout = QVBoxLayout(self); layout.setContentsMargins(20,18,20,16); layout.setSpacing(10)
        heading = QHBoxLayout()
        heading.addWidget(DialogHeader('名单核对', '把外部名单与已有项目、材料要求对照；不新建项目，不改变交付。',
                                       eyebrow='WORKBOX STUDIO / 清单工作台'), 1)
        self.import_button = button('导入名单', True)
        self.import_button.setAccessibleName('导入 Excel CSV TSV 名单')
        self.import_button.clicked.connect(self.choose_file); heading.addWidget(self.import_button)
        layout.addLayout(heading)
        self.source_label = text_label('还没有导入名单。支持 XLSX、CSV、TSV；旧 XLS 请另存为 XLSX。','muted')
        layout.addWidget(self.source_label)
        self.mapping = QFrame(); self.mapping.setObjectName('checklistMapping')
        grid = QGridLayout(self.mapping); grid.setContentsMargins(12,10,12,10); grid.setHorizontalSpacing(12)
        self.sheet, self.name_column, self.date_column = ComboBox(), ComboBox(), ComboBox()
        for col,(name,control) in enumerate((('工作表',self.sheet),('名称列',self.name_column),('日期列',self.date_column))):
            control.setAccessibleName(name); control.setMinimumWidth(0)
            grid.addWidget(text_label(name,'muted'),0,col);grid.addWidget(control,1,col)
            grid.setColumnStretch(col,1)
        self.scope, self.start_date, self.end_date = ComboBox(),ComboBox(),ComboBox()
        self.scope.addItem('全部日期','all');self.scope.addItem('指定时间段','range')
        for col,(name,control) in enumerate((('日期范围',self.scope),('开始日期',self.start_date),('结束日期',self.end_date))):
            control.setAccessibleName(name);control.setMinimumWidth(0)
            grid.addWidget(text_label(name,'muted'),2,col);grid.addWidget(control,3,col)
        layout.addWidget(self.mapping);self.mapping.hide()
        self.metrics = MetricStrip(('名单条目','尚未收录','需要核对','材料收齐'))
        self.metrics.setValues((0,0,0,0));layout.addWidget(self.metrics)
        filter_row=QHBoxLayout();self.search=LineEdit();self.search.setPlaceholderText('在当前核对结果中搜索名称或缺项')
        self.search.setAccessibleName('搜索核对结果');self.search.setClearButtonEnabled(True)
        self.view=ComboBox();self.view.addItem('全部结果','all');self.view.addItem('只看需要处理','attention')
        self.view.setAccessibleName('核对结果范围');filter_row.addWidget(self.search,1);filter_row.addWidget(self.view)
        layout.addLayout(filter_row)
        self.empty=text_label('导入一份名单，即可看清哪些项目还没收录，哪些材料尚未齐全。','checklistEmpty')
        self.empty.setAlignment(Qt.AlignCenter);layout.addWidget(self.empty)
        self.table=QTableWidget(0,7);self.table.setObjectName('checklistTable');self.table.setAccessibleName('名单核对结果')
        self.table.setHorizontalHeaderLabels(['名称','清单日期','来源行','项目状态','名称核对','材料进度','缺项 / 说明'])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows);self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setWordWrap(True);self.table.setShowGrid(False);self.table.setAlternatingRowColors(True)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel);self.table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.table.verticalHeader().hide();self.table.verticalHeader().setDefaultSectionSize(44)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        for col,width in enumerate((240,104,64,90,110,140,260)):self.table.setColumnWidth(col,width)
        layout.addWidget(self.table,1)
        self.notice=StatusBanner('导入只核对身份；材料需要点击下方按钮，按项目已保存的规则检查。')
        layout.addWidget(self.notice)
        self.progress=QProgressBar();self.progress.setAccessibleName('名单核对进度');self.progress.setFixedHeight(5)
        self.progress.setTextVisible(False);self.progress.hide();layout.addWidget(self.progress)
        footer=QFrame();footer.setObjectName('dialogActions');actions=QHBoxLayout(footer);actions.setContentsMargins(0,10,0,0)
        self.material_button=button('材料规则与详情');self.material_button.clicked.connect(self.open_material)
        self.folder_button=button('打开项目目录');self.folder_button.clicked.connect(self.open_folder)
        self.refresh_button=button('重新核对');self.refresh_button.clicked.connect(lambda:self.refresh(False))
        self.check_button=button('检查本次材料',True);self.check_button.clicked.connect(lambda:self.refresh(True))
        self.cancel_button=button('取消检查');self.cancel_button.clicked.connect(self.cancel);self.cancel_button.hide()
        self.close_button=button('关闭');self.close_button.clicked.connect(self.reject)
        for control in (self.import_button,self.material_button,self.folder_button,
                        self.refresh_button,self.check_button,self.cancel_button,self.close_button):
            control.setAutoDefault(False)
            control.setDefault(False)
        for widget in (self.material_button,self.folder_button):actions.addWidget(widget)
        actions.addStretch()
        for widget in (self.refresh_button,self.check_button,self.cancel_button,self.close_button):actions.addWidget(widget)
        layout.addWidget(footer)
        self.sheet.currentIndexChanged.connect(self._sheet_changed)
        for control in (self.name_column,self.date_column):control.currentIndexChanged.connect(self._mapping_changed)
        self.scope.currentIndexChanged.connect(self._range_changed)
        for control in (self.start_date,self.end_date):control.currentIndexChanged.connect(self._range_changed)
        self.search.textChanged.connect(self._render);self.view.currentIndexChanged.connect(self._render)
        self.table.itemSelectionChanged.connect(self._sync)
        self.table.cellDoubleClicked.connect(lambda *_:self.open_material())
        theme_signal().connect(self._render)
        self._sync()

    def choose_file(self):
        if self.worker is not None:return
        path,_=QFileDialog.getOpenFileName(self,'选择核对名单','','名单 (*.xlsx *.csv *.tsv)')
        if path:self.load_path(path)

    def load_path(self,path):
        if self.worker is not None:return
        source=str(path)
        def operation(*,cancel_event,progress):
            tables=read_tables(source,cancel_event=cancel_event)
            index=next((i for i,t in enumerate(tables) if any(any(str(v).strip() for v in row) for row in t['rows'])),0)
            nc,dc=suggest_columns(tables[index]);entries=extract_entries(tables[index],nc,dc,cancel_event=cancel_event)
            results=review_entries(self.service,entries,cancel_event=cancel_event)
            return tables,index,nc,dc,entries,results
        def loaded(result):
            self.tables,index,nc,dc,self.entries,self.results=result
            self.source_name=Path(source).name
            self._loading=True
            try:
                self.sheet.clear();self.sheet.addItems([t['name'] for t in self.tables]);self.sheet.setCurrentIndex(index)
                self._fill_columns(nc,dc);self.scope.setCurrentIndex(0);self._fill_dates()
            finally:self._loading=False
            self.mapping.show();self._show_result_context();self._render()
        self._start(operation,loaded,'正在读取名单并核对已有项目…')

    def _fill_columns(self,nc=None,dc=None):
        table=self.tables[self.sheet.currentIndex()]
        if nc is None:nc,dc=suggest_columns(table)
        self.name_column.clear();self.date_column.clear();self.date_column.addItem('不指定日期',-1)
        width=max((len(row) for row in table['rows']),default=0)
        for i in range(width):
            caption=f'第 {i+1} 列'
            self.name_column.addItem(caption,i);self.date_column.addItem(caption,i)
        self.name_column.setCurrentIndex(nc);self.date_column.setCurrentIndex(dc+1)

    def _fill_dates(self):
        days=sorted({row['date'] for row in self.entries if row['date']})
        for control in (self.start_date,self.end_date):
            old=control.currentData();control.clear()
            for day in days:control.addItem(day,day)
            control.setCurrentIndex(control.findData(old) if old in days else (len(days)-1 if control is self.end_date else 0))

    def _sheet_changed(self,*_):
        if self._loading or not self.tables:return
        self._loading=True
        try:self._fill_columns()
        finally:self._loading=False
        self._mapping_changed()

    def _mapping_changed(self,*_):
        if self._loading or not self.tables:return
        self.refresh(False, reset_dates=True)

    def _range_changed(self,*_):
        self._sync()
        if not self._loading and self.tables:self.refresh(False)

    def refresh(self,check_materials=False,*,reset_dates=False):
        if self.worker is not None or not self.tables:return
        table=self.tables[self.sheet.currentIndex()]
        nc,dc=self.name_column.currentData(),self.date_column.currentData()
        dates=(None,None) if reset_dates or self.scope.currentData()=='all' else (self.start_date.currentData(),self.end_date.currentData())
        def operation(*,cancel_event,progress):
            entries=extract_entries(table,nc,dc,cancel_event=cancel_event)
            selected=filter_entries(entries,*dates)
            results=review_entries(self.service,selected,check_materials=check_materials,cancel_event=cancel_event,progress=progress) if selected else []
            return entries,results
        def loaded(result):
            self.entries,self.results=result
            if reset_dates:
                self._loading=True
                try:self.scope.setCurrentIndex(0);self._fill_dates()
                finally:self._loading=False
            self._show_result_context();self._render()
        self._start(operation,loaded,'正在按已保存规则读取材料…' if check_materials else '正在核对名单…')

    def _start(self,operation,completed,message):
        self.notice.setText(message);self.notice.setTone('busy')
        self.progress.setRange(0,0);self.progress.show()
        job=FileJob(operation,self);self.worker=job
        job.progress_changed.connect(self._progress)
        job.finished.connect(lambda:self._finished(job,completed))
        self._sync();job.start()

    def _progress(self,value):
        self.progress.setRange(0,100);self.progress.setValue(value)

    def _finished(self,job,completed):
        if self.worker is not job:return
        job.wait();self.worker=None;self.progress.hide()
        if self._closing:
            job.deleteLater();super().reject();return
        if job.error:
            self.notice.setText('本次核对未完成，保留上次结果：'+job.error);self.notice.setTone('error')
        elif job.cancelled:
            self.notice.setText('已取消本次操作，保留上次结果；未修改项目和文件。');self.notice.setTone('neutral')
        else:
            completed(job.result)
        job.deleteLater();self._sync()

    def _show_result_context(self):
        self.source_label.setText(f'当前结果：{self.source_name} / {self.tables[self.sheet.currentIndex()]["name"]} · {len(self.results)} 条')
        unknown=sum(not row['date'] for row in self.entries)
        duplicates=sum(bool(row['duplicate']) for row in self.results)
        extra=f' {unknown} 条未注明完整日期，使用日期范围时不计入。' if unknown else ''
        if duplicates:extra+=f' {duplicates} 条重名清单逐行保留。'
        self.notice.setText('材料结果仅对应本次读取，不代表内容合格或已交付；文件变化后请重新检查。'+extra)
        self.notice.setTone('neutral')

    def _selected(self):
        item=self.table.item(self.table.currentRow(),0)
        return item.data(Qt.UserRole) if item is not None else None

    def _render(self,*_):
        selected=self._selected();old_key=(selected.get('sheet'),selected.get('row'),selected.get('name')) if selected else None
        scroll=self.table.verticalScrollBar().value();needle=self.search.text().strip().casefold();c=tokens()
        self.table.blockSignals(True);self.table.setUpdatesEnabled(False)
        try:
            self.table.setRowCount(0)
            for index,row in enumerate(self.results):
                attention=row['state']!='已关联' or row['material_state']!='已收齐'
                if self.view.currentData()=='attention' and not attention:continue
                if needle and needle not in (row['name']+' '+row['missing']).casefold():continue
                position=self.table.rowCount();self.table.insertRow(position)
                progress=row['material_state']
                if row['matched'] is not None and row['required'] is not None:progress+=f" · {row['matched']}/{row['required']}"
                values=(row['name'],row['date'] or row.get('source_date') or '未注明',str(row['row']),row['project_state'],row['state'],progress,row['missing'])
                for col,value in enumerate(values):
                    item=QTableWidgetItem(value);item.setData(Qt.UserRole,row)
                    item.setToolTip(value+f"\n来源：{row['sheet']} · 第 {row['row']} 行"+('\n清单中重复名称' if row['duplicate'] else ''))
                    if col in (4,5):
                        good=(col==4 and row['state']=='已关联') or (col==5 and row['material_state']=='已收齐')
                        pending=row['state']=='尚未收录' if col==4 else row['material_state']=='读取不完整'
                        tone='success' if good else 'error' if pending else 'warning'
                        item.setBackground(QColor(c[tone+'_bg']))
                        item.setForeground(QColor(c['state_delivered' if good else 'error' if pending else 'state_pending']))
                    self.table.setItem(position,col,item)
                if (row.get('sheet'),row.get('row'),row.get('name'))==old_key:self.table.selectRow(position)
            self.table.resizeRowsToContents();self.table.verticalScrollBar().setValue(scroll)
        finally:self.table.setUpdatesEnabled(True);self.table.blockSignals(False)
        counts=Counter(row['state'] for row in self.results);complete=sum(r['material_state']=='已收齐' for r in self.results)
        self.metrics.setValues((len(self.results),counts['尚未收录'],len(self.results)-complete-counts['尚未收录'],complete))
        self.empty.setVisible(not self.results);self._sync()

    def _sync(self,*_):
        busy=self.worker is not None
        for control in (self.import_button,self.mapping,self.refresh_button,self.check_button):control.setEnabled(not busy)
        self.refresh_button.setEnabled(not busy and bool(self.tables));self.check_button.setEnabled(not busy and bool(self.results))
        self.start_date.setEnabled(not busy and self.scope.currentData()=='range');self.end_date.setEnabled(not busy and self.scope.currentData()=='range')
        selected=self._selected();valid=selected is not None and selected.get('project_id') is not None
        self.material_button.setEnabled(not busy and valid);self.folder_button.setEnabled(not busy and valid)
        self.cancel_button.setVisible(busy)

    def open_material(self):
        selected=self._selected()
        if self.worker is not None or not selected or selected.get('project_id') is None:return
        from material_check_ui import MaterialCheckDialog
        try:
            dialog=MaterialCheckDialog(self.service,selected['project_id'],self);self.material_dialog=dialog
            try:dialog.exec()
            finally:dialog.shutdown();self.material_dialog=None;dialog.deleteLater()
        except (ValueError,KeyError,OSError,sqlite3.Error) as error:
            self.notice.setText(str(error));self.notice.setTone('error')

    def open_folder(self):
        selected=self._selected()
        if self.worker is not None or not selected or selected.get('project_id') is None:return
        try:
            project=self.service.get_project(selected['project_id'])
            if project.get('merged_into_id') or project.get('deleted_at') or project.get('archived_at'):
                raise ValueError('项目已变化，请重新核对。')
            root=(project.get('workspace') or {}).get('path')
            if not root or not Path(root).is_dir():raise ValueError('工作目录不存在；没有创建新目录。')
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(root)):raise ValueError('系统未能打开目录。')
        except (OSError,ValueError,KeyError,sqlite3.Error) as error:
            self.notice.setText(str(error));self.notice.setTone('error')

    def cancel(self):
        if self.worker is not None:self.worker.cancel_event.set()

    def reject(self):
        if self.worker is not None:
            self._closing=True;self.cancel();self.notice.setText('正在取消，读取线程停止后关闭…');return
        self._closing=False;super().reject()

    def closeEvent(self,event):
        if self.worker is not None:self.reject();event.ignore()
        else:super().closeEvent(event)

    def showEvent(self,event):
        self._closing=False;super().showEvent(event)

    def shutdown(self):
        if self.material_dialog is not None:self.material_dialog.shutdown()
        if self.worker is not None:
            job=self.worker;job.cancel_event.set()
            if not job.wait(15000):raise RuntimeError('名单读取尚未停止，保留窗口与线程。')
            self.worker=None;job.deleteLater()
