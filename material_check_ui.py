"""Read-only material inspection with explicit project rule settings."""
import sqlite3
import uuid

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QHBoxLayout,
    QHeaderView, QPlainTextEdit, QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget,
    QVBoxLayout, QWidget, QApplication, QProgressBar, QFrame)

from material_matching import FAMILIES, Requirement, check_snapshot, preset_rules, validate_rules
from material_check_store import check_project, load_rules, save_rules
from project_documents import list_project_documents
from theme import apply_to
from ui import FileJob, button, label
from ui_components import StatusBanner, DialogHeader, MetricStrip, ComboBox as QComboBox, CheckBox
from qfluentwidgets import FluentIcon


class MaterialCheckDialog(QDialog):
    def __init__(self, service, project_id, parent=None):
        super().__init__(parent)
        self.service, self.project_id = service, project_id
        self.worker = None
        self.report = self.snapshot = None
        self.selections = {}
        self._loading = self._rendering = self._closed = self._close_pending = False
        self._revision = 0
        project = check_project(service, project_id)
        self.software_name = project['name']
        self.setWindowTitle('材料检查 · ' + self.software_name)
        self.resize(900, 680)
        self.setMinimumSize(600, 540)
        apply_to(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(8)
        self.header = DialogHeader('材料检查', self.software_name, icon=FluentIcon.DOCUMENT,
                                   eyebrow='项目工具 / 材料与缺项')
        layout.addWidget(self.header)
        hint = label('只读文件名与类型检查，不代表内容合格或项目已交付。', 'muted', True)
        hint.setTextFormat(Qt.PlainText)
        layout.addWidget(hint)
        self.metrics = MetricStrip(('所需材料', '已匹配', '尚缺', '待选择文件'))
        layout.addWidget(self.metrics)
        self.status_label = StatusBanner()
        layout.addWidget(self.status_label)
        self.progress = QProgressBar()
        self.progress.setAccessibleName('正在读取项目文件')
        self.progress.setRange(0,0);self.progress.setFixedHeight(4);self.progress.setTextVisible(False)
        self.progress.hide();layout.addWidget(self.progress)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        rules_page = QWidget();rules_layout = QVBoxLayout(rules_page)
        rules_layout.setContentsMargins(0, 8, 0, 0)
        top = QHBoxLayout()
        self.preset = QComboBox()
        self.preset.addItems(['说明书与代码', '五件材料', '空白规则'])
        self.preset.setCurrentText('五件材料')
        self.preset.setToolTip('选择后点击应用预设；不会自动覆盖已保存规则。')
        self.preset.setAccessibleName('材料规则预设')
        self.preset_button = button('应用预设')
        self.add_button = button('添加一项')
        self.remove_button = button('移除所选', 'danger')
        self.add_button.setIcon(FluentIcon.ADD)
        for widget in (self.preset,self.preset_button,self.add_button,self.remove_button):top.addWidget(widget)
        top.addStretch();rules_layout.addLayout(top)
        self.rule_table = QTableWidget(0, 4)
        self.rule_table.setObjectName('materialRules')
        self.rule_table.setAccessibleName('材料规则')
        self.rule_table.setHorizontalHeaderLabels(['材料名称', '文件类型', '份数', '关键词'])
        self.rule_table.horizontalHeaderItem(3).setToolTip('全部关键词都需命中；留空时只检查文件类型。')
        self.rule_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.rule_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.rule_table.verticalHeader().hide()
        # Native combo/spin controls include themed editor and arrow padding.
        # Preserve readable values instead of squeezing them into header widths.
        self.rule_table.setColumnWidth(0,120);self.rule_table.setColumnWidth(1,185);self.rule_table.setColumnWidth(2,135)
        self.rule_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.rule_table.setColumnWidth(3,280)
        self.rule_table.verticalHeader().setDefaultSectionSize(44)
        rules_layout.addWidget(self.rule_table,1)
        tip = label('逗号、空格或 + 分隔；全部命中，顺序不限。用“软件名称”代入当前项目名。\n同一文件命中多项时需选择归属；不同文件路径按不同份数计，不比较文件内容。', 'muted', True)
        tip.setTextFormat(Qt.PlainText);rules_layout.addWidget(tip)
        self.tabs.addTab(rules_page,'材料规则')
        results_page=QWidget();results_layout=QVBoxLayout(results_page)
        results_layout.setContentsMargins(0,8,0,0)
        results_toolbar=QHBoxLayout()
        self.missing_only=CheckBox('只看缺项')
        self.missing_only.setAccessibleName('只看缺项')
        self.missing_only.setToolTip('仅筛选下方材料行，不改变检查结果或待选择文件。')
        self.copy_summary_button=button('复制检查摘要')
        self.copy_summary_button.setToolTip('仅在点击时把当前检查摘要复制到剪贴板；不会上传或保存文件。')
        self.copy_summary_button.setIcon(FluentIcon.COPY)
        results_toolbar.addWidget(self.missing_only);results_toolbar.addStretch()
        results_toolbar.addWidget(self.copy_summary_button);results_layout.addLayout(results_toolbar)
        self.result_table=QTableWidget(0,4)
        self.result_table.setObjectName('materialResults')
        self.result_table.setAccessibleName('材料检查结果')
        self.result_table.setHorizontalHeaderLabels(['材料','匹配 / 所需','缺少','对应工作文件'])
        self.result_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.result_table.verticalHeader().hide()
        self.result_table.setWordWrap(True)
        for col,width in ((0,140),(1,90),(2,50)):self.result_table.setColumnWidth(col,width)
        self.result_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.result_table.setColumnWidth(3,340)
        results_layout.addWidget(self.result_table,2)
        self.ambiguity_hint=label('多个候选的文件：选择后仅计入一个要求。','muted',True)
        results_layout.addWidget(self.ambiguity_hint)
        self.ambiguity_table=QTableWidget(0,2)
        self.ambiguity_table.setObjectName('materialChoices')
        self.ambiguity_table.setAccessibleName('待选择的材料归属')
        self.ambiguity_table.setHorizontalHeaderLabels(['工作文件','人工选择归属'])
        self.ambiguity_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.ambiguity_table.verticalHeader().hide()
        self.ambiguity_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.ambiguity_table.setColumnWidth(0,300);self.ambiguity_table.setColumnWidth(1,330)
        self.ambiguity_table.verticalHeader().setDefaultSectionSize(44)
        results_layout.addWidget(self.ambiguity_table,1)
        self.details=QPlainTextEdit()
        self.details.setReadOnly(True);self.details.setMaximumHeight(100)
        self.details.setAccessibleName('检查限制与未匹配文件')
        results_layout.addWidget(self.details)
        self.tabs.addTab(results_page,'检查结果')
        self.footer=QFrame();self.footer.setObjectName('actionBar')
        bottom=QHBoxLayout(self.footer);bottom.setContentsMargins(0,10,0,0)
        self.save_button=button('保存本项目规则')
        self.save_state=label('尚未保存','saveState')
        self.check_button=button('检查 / 刷新文件','primary')
        self.close_button=button('关闭')
        bottom.addWidget(self.save_button);bottom.addWidget(self.save_state);bottom.addStretch()
        bottom.addWidget(self.check_button);bottom.addWidget(self.close_button)
        layout.addWidget(self.footer)
        for table in (self.rule_table,self.result_table,self.ambiguity_table):
            table.setShowGrid(False);table.setAlternatingRowColors(True)
            table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
            table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.missing_only.toggled.connect(lambda _:self._render_report())
        self.copy_summary_button.clicked.connect(self.copy_summary)
        for w in (self.preset_button,self.add_button,self.remove_button,self.save_button,self.check_button,self.close_button):
            w.setAccessibleName(w.text())
        self.rule_table.itemChanged.connect(self._invalidate)
        self.preset_button.clicked.connect(lambda:self.set_rules(preset_rules(self.preset.currentText())))
        self.add_button.clicked.connect(self.add_rule)
        self.remove_button.clicked.connect(self.remove_rule)
        self.save_button.clicked.connect(self.save_rules)
        self.check_button.clicked.connect(self.refresh)
        self.close_button.clicked.connect(self.reject)
        try:
            saved=load_rules(service,project_id)
            self.set_rules(saved if saved is not None else preset_rules('五件材料'))
            self.save_state.setText('已保存规则' if saved is not None else '预设未保存')
            self.status_label.setText('点击检查后读取当前项目文件。规则只有明确保存才会保留。')
        except (ValueError,TypeError,KeyError,sqlite3.Error) as error:
            self.set_rules(())
            self.status_label.setText('保存的规则无法读取，原设置未改变：'+str(error))
            self.status_label.setTone('error')
        self._sync()

    def set_rules(self,rules):
        validate_rules(rules)
        self._loading=True
        try:
            self.rule_table.setRowCount(0)
            for r in rules:
                row=self.rule_table.rowCount();self.rule_table.insertRow(row)
                name=QTableWidgetItem(r.label);name.setData(Qt.UserRole,r.id)
                self.rule_table.setItem(row,0,name)
                family=QComboBox()
                for key,(title,_) in FAMILIES.items():family.addItem(title,key)
                family.setCurrentIndex(family.findData(r.family))
                family.currentIndexChanged.connect(self._invalidate)
                self.rule_table.setCellWidget(row,1,family)
                count=QSpinBox();count.setRange(1,20);count.setValue(r.count)
                count.valueChanged.connect(self._invalidate)
                self.rule_table.setCellWidget(row,2,count)
                self.rule_table.setItem(row,3,QTableWidgetItem(r.keywords))
        finally:self._loading=False
        self._invalidate()

    def rules(self):
        values=tuple(Requirement(self.rule_table.item(i,0).data(Qt.UserRole),self.rule_table.item(i,0).text(),
            self.rule_table.cellWidget(i,1).currentData(),self.rule_table.cellWidget(i,2).value(),
            self.rule_table.item(i,3).text()) for i in range(self.rule_table.rowCount()))
        return validate_rules(values)

    def add_rule(self):
        try:
            if self.rule_table.rowCount()>=40:raise ValueError('最多40项材料规则。')
            self.set_rules((*self.rules(),Requirement(uuid.uuid4().hex,'新材料','word')))
        except ValueError as error:self.status_label.setText(str(error))

    def remove_rule(self):
        row=self.rule_table.currentRow()
        if row>=0:
            self.rule_table.removeRow(row);self._invalidate()

    def _invalidate(self,*_):
        if self._loading:return
        self._revision+=1
        self.report=self.snapshot=None;self.selections={}
        self.result_table.setRowCount(0);self.ambiguity_table.setRowCount(0);self.details.clear()
        self.status_label.setText('规则已修改，请重新检查；尚未保存。')
        self.ambiguity_hint.hide();self.ambiguity_table.hide()
        self.status_label.setTone('neutral')
        self.save_state.setText('尚未保存')
        self.metrics.setValues(('—','—','—','—'))
        self._sync()

    def _sync(self):
        idle=self.worker is None and not self._closed
        for widget in (self.rule_table,self.preset,self.preset_button,self.add_button,self.remove_button,self.save_button,self.check_button):
            widget.setEnabled(idle)
        self.close_button.setText('取消并关闭' if self.worker is not None else '关闭')
        self.close_button.setAccessibleName(self.close_button.text())
        self.copy_summary_button.setEnabled(idle and self.report is not None)
        self.missing_only.setEnabled(idle and self.report is not None)
        self.progress.setVisible(self.worker is not None)

    def save_rules(self):
        if self.worker is not None or self._closed:return
        try:
            save_rules(self.service,self.project_id,self.rules())
            self.status_label.setText('本项目规则已保存；项目状态和交付统计未改变。')
            self.status_label.setTone('success');self.save_state.setText('规则已保存')
        except (ValueError,KeyError,sqlite3.Error) as error:
            self.status_label.setText('规则未保存：'+str(error));self.status_label.setTone('error')

    def refresh(self):
        if self.worker is not None or self._closed:return
        try:
            self.rules();project=check_project(self.service,self.project_id)
            if project.get('block_reason'):raise ValueError('工作目录需要恢复，暂不能检查。')
        except (ValueError,KeyError,sqlite3.Error) as error:
            self.report=self.snapshot=None
            self.result_table.setRowCount(0);self.ambiguity_table.setRowCount(0);self.details.clear()
            self.metrics.setValues(('—','—','—','—'))
            self.status_label.setText('检查未完成：'+str(error));self.status_label.setTone('error')
            self._sync();return
        self.report=self.snapshot=None;self.selections={}
        self.result_table.setRowCount(0);self.ambiguity_table.setRowCount(0);self.details.clear()
        self.status_label.setText('正在读取已登记项目的当前工作文件…')
        self.status_label.setTone('busy')
        self.metrics.setValues(('—','—','—','—'))
        self.worker=FileJob(lambda **kw:list_project_documents(self.service,self.project_id,cancel_event=kw.get('cancel_event')),self)
        self.worker._material_revision=self._revision
        self.worker.finished.connect(self._finished)
        self._sync();self.worker.start()

    def _finished(self):
        worker=self.sender()
        if worker is None or worker is not self.worker:return
        self.worker=None
        try:
            if not self._closed and not self._close_pending:
                if worker.cancelled:raise ValueError('已取消读取。')
                if worker.error:raise ValueError(worker.error)
                if worker._material_revision!=self._revision:raise ValueError('规则已变化，请重新检查。')
                if not isinstance(worker.result,dict) or worker.result.get('project_id')!=self.project_id:
                    raise ValueError('读取结果不属于当前项目。')
                project=check_project(self.service,self.project_id)
                self.software_name=project['name'];self.snapshot=worker.result
                self.header.setSubtitle(self.software_name)
                self.setWindowTitle('材料检查 · '+self.software_name)
                self._render_report();self.tabs.setCurrentIndex(1)
        except (ValueError,TypeError,KeyError,sqlite3.Error) as error:
            self.report=self.snapshot=None
            self.status_label.setText('检查未完成：'+str(error))
            self.status_label.setTone('error')
        finally:
            worker.deleteLater();self._sync()
        if self._close_pending:
            self._closed=True;super().reject()

    def _render_report(self):
        if self.snapshot is None:return
        self.report=check_snapshot(self.snapshot,self.rules(),self.software_name,selections=self.selections)
        r=self.report;self._rendering=True
        try:
            docs={d['id']:d for d in self.snapshot['documents']}
            names=lambda ids:'\n'.join(docs[i].get('relative_path') or docs[i]['name'] for i in ids)
            visible_rows=[row for row in r.rows if not self.missing_only.isChecked() or row.missing]
            self.result_table.setRowCount(len(visible_rows))
            for i,row in enumerate(visible_rows):
                for j,value in enumerate((row.label,f'{row.matched} / {row.required}',str(row.missing),names(row.file_ids))):
                    item=QTableWidgetItem(value);item.setToolTip(value);self.result_table.setItem(i,j,item)
            self.result_table.resizeRowsToContents()
            # Keep selected conflicts visible so a user can revise their choice.
            raw=check_snapshot(self.snapshot,self.rules(),self.software_name)
            self.ambiguity_table.setRowCount(len(raw.ambiguous))
            labels={rule.id:rule.label for rule in self.rules()}
            for i,(fid,options) in enumerate(raw.ambiguous.items()):
                self.ambiguity_table.setItem(i,0,QTableWidgetItem(names([fid])))
                combo=QComboBox();combo.addItem('请选择（不自动计入）',None)
                for key in options:combo.addItem(labels[key],key)
                combo.setCurrentIndex(combo.findData(self.selections.get(fid)))
                combo.currentIndexChanged.connect(lambda _index,key=fid,control=combo:self.choose(key,control.currentData()))
                self.ambiguity_table.setCellWidget(i,1,combo)
            self.ambiguity_hint.setVisible(bool(raw.ambiguous));self.ambiguity_table.setVisible(bool(raw.ambiguous))
            missing=sum(row.missing for row in r.rows)
            if r.status=='complete':text='按当前规则已收齐（仅文件名/类型检查，不代表内容合格或项目已交付）。'
            elif r.status=='incomplete':text='检查不完整，不能确认收齐。请处理下方读取提示后刷新。'
            elif r.status=='unconfigured':text='尚未配置材料规则，不能判断是否收齐。'
            else:text=f'尚缺 {missing} 份 · 待选择 {len(r.ambiguous)} 个文件。'
            self.status_label.setText(text)
            self.status_label.setTone('success' if r.status=='complete' else 'warning' if r.status in ('missing','ambiguous','incomplete') else 'neutral')
            self.metrics.setValues((sum(row.required for row in r.rows),sum(row.matched for row in r.rows),missing,len(r.ambiguous)))
            details=list(r.warnings)
            if r.ignored:details.append(f'未计入 {r.ignored} 项历史归档、缺失文件或目录行。')
            if r.unmatched:details.append('未匹配文件（不会移动或删除）：\n'+names(r.unmatched))
            self.details.setPlainText('\n'.join(details) or '只读检查已完成；规则保存和文件处理相互独立。')
        finally:
            self._rendering=False
            self._sync()

    def summary_text(self):
        """Generate a local textual snapshot; never read/copy clipboard implicitly."""
        if self.report is None or self.snapshot is None:return ''
        r=self.report
        state=('检查不完整，不能确认收齐' if r.status=='incomplete' else
               '尚未配置规则' if r.status=='unconfigured' else
               f'尚缺 {sum(row.missing for row in r.rows)} 份；待选择 {len(r.ambiguous)} 个文件')
        lines=[f'材料检查 · {self.software_name}',state,
               '仅文件名/类型检查，不代表内容合格或项目已交付。']
        docs={d['id']:d for d in self.snapshot['documents']}
        for row in r.rows:
            lines.append(f'{row.label}：{row.matched}/{row.required}，缺少 {row.missing}')
            lines.extend('  '+(docs[fid].get('relative_path') or docs[fid]['name']) for fid in row.file_ids)
        lines.extend(r.warnings)
        return '\n'.join(lines)

    def copy_summary(self):
        if self.worker is not None or self._closed:return
        text=self.summary_text()
        if text:QApplication.clipboard().setText(text)

    def resizeEvent(self,event):
        if hasattr(self,'metrics'):
            self.metrics.setVisible(self.height()>=620)
            self.header.eyebrow.setVisible(self.height()>=620)
            self.save_state.setVisible(self.width()>=700)
        super().resizeEvent(event)

    def choose(self,file_id,rule_id):
        if self._rendering:return
        if rule_id is None:self.selections.pop(file_id,None)
        else:self.selections[file_id]=rule_id
        try:self._render_report()
        except (ValueError,KeyError) as error:
            self.report=None;self.status_label.setText('检查未完成：'+str(error))

    def reject(self):
        if self.worker is not None:
            self._close_pending=True;self.worker.cancel_event.set()
            self.status_label.setText('正在取消读取，结束后关闭；不会强制停止线程。')
            self.status_label.setTone('busy')
            return
        self._closed=True;super().reject()

    def closeEvent(self,event):
        if self.worker is not None:self.reject();event.ignore()
        else:super().closeEvent(event)

    def shutdown(self):
        if self.worker is not None:
            self.worker.cancel_event.set()
            if not self.worker.wait(15000):
                raise RuntimeError('材料读取尚未结束，保留窗口和文件锁，未强制退出。')
            self.worker.finished.disconnect(self._finished)
            self.worker.deleteLater();self.worker=None
        self._closed=True
        super().reject()
