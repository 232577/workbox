"""主窗口内的归档分类配置、预览及可恢复批量操作。"""
from PySide6.QtCore import Qt, Signal, QSignalBlocker
from PySide6.QtWidgets import (
    QAbstractItemView, QGridLayout, QHBoxLayout, QHeaderView, QMessageBox,
    QSizePolicy, QTableWidgetItem, QVBoxLayout, QWidget,
)

from compact_ui import button, label, local_timestamp
from fluent_views import WorkbenchTableWidget
from ui import FileJob
from ui_components import CheckBox, ComboBox, SpinBox


class ArchiveClassificationPane(QWidget):
    return_requested = Signal()
    statistics_changed = Signal()

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.backend = service.archive_classification
        self.worker = None
        self._loaded = self._dirty = self._external_busy = self._shutting_down = False
        self._preview = None
        self._saved_policy = {}
        self._operations = []
        self._loading_controls = False
        self._pending_load = False
        self._pending_notice = ''
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(6)
        header = QHBoxLayout()
        header.addWidget(label('归档分类', 'sectionTitle'))
        header.addStretch()
        self.return_button = button('返回任务组')
        header.addWidget(self.return_button)
        layout.addLayout(header)
        self.future_enabled = CheckBox('以后交付按本规则分类')
        self.future_enabled.setAccessibleName('以后交付按本规则分类')
        self.minimum_files = SpinBox()
        self.minimum_files.setRange(1, 10000)
        self.minimum_files.setValue(4)
        self.minimum_files.setAccessibleName('可运行归档文件数下限')
        self.minimum_files.setMinimumWidth(145)
        self.minimum_files.setMaximumWidth(170)
        self.high_type = ComboBox()
        self.high_type.setAccessibleName('达到文件数下限的目标类型')
        self.low_type = ComboBox()
        self.low_type.setAccessibleName('低于文件数下限的目标类型')
        for control in (self.high_type, self.low_type):
            control.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            control.setMinimumWidth(90)
        policy_header = QHBoxLayout()
        policy_header.addWidget(self.future_enabled)
        policy_header.addStretch()
        self.save_button = button('保存规则')
        policy_header.addWidget(self.save_button)
        layout.addLayout(policy_header)
        form = QGridLayout()
        self.high_label = label('文件数 ≥')
        self.low_label = label('文件数 0—3')
        form.addWidget(self.high_label, 0, 0)
        form.addWidget(self.minimum_files, 0, 1)
        form.addWidget(self.high_type, 0, 2)
        form.addWidget(self.low_label, 1, 0, 1, 2)
        form.addWidget(self.low_type, 1, 2)
        form.setColumnStretch(2, 1)
        layout.addLayout(form)
        self.rule_note = label('数量取最近有效交付的单项目归档；未知数量保持原类型。', 'subtle', True)
        layout.addWidget(self.rule_note)
        self.notice = label('正在读取归档分类规则。', 'notice', True)
        self.notice.setAccessibleName('归档分类操作结果')
        self.notice.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout.addWidget(self.notice)
        self.table = WorkbenchTableWidget(0, 5)
        self.table.setAccessibleName('归档分类预览')
        self.table.setHorizontalHeaderLabels(['项目', '当前类型', '目标类型', '文件数', '核对结果'])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setWordWrap(True)
        self.table.setTextElideMode(Qt.ElideNone)
        self.table.setMinimumHeight(130)
        self.table.verticalHeader().hide()
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(False)
        for index, width in enumerate((260, 110, 110, 85, 280)):
            self.table.setColumnWidth(index, width)
        header.sectionResized.connect(lambda *_: self.table.resizeRowsToContents())
        layout.addWidget(self.table, 1)
        actions = QHBoxLayout()
        self.preview_button = button('预览已有交付')
        self.apply_button = button('应用本次分类', True)
        actions.addWidget(self.preview_button)
        actions.addWidget(self.apply_button)
        actions.addStretch()
        layout.addLayout(actions)
        undo_row = QHBoxLayout()
        self.operation_choice = ComboBox()
        self.operation_choice.setAccessibleName('选择已保存的批量分类操作')
        self.operation_choice.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.operation_choice.setMinimumWidth(100)
        self.undo_button = button('撤销所选分类')
        undo_row.addWidget(self.operation_choice, 1)
        undo_row.addWidget(self.undo_button)
        layout.addLayout(undo_row)
        self.future_enabled.toggled.connect(self._mark_dirty)
        self.minimum_files.valueChanged.connect(self._mark_dirty)
        self.high_type.currentIndexChanged.connect(self._mark_dirty)
        self.low_type.currentIndexChanged.connect(self._mark_dirty)
        self.operation_choice.currentIndexChanged.connect(self._update_controls)
        self.return_button.clicked.connect(self.return_requested.emit)
        self.save_button.clicked.connect(self.save_policy)
        self.preview_button.clicked.connect(self.preview_existing)
        self.apply_button.clicked.connect(self.apply_preview)
        self.undo_button.clicked.connect(self.undo_selected)
        self._update_controls()

    def _read_state(self):
        return dict(policy=self.backend.policy(), types=self.service.db.list_project_types(),
                    operations=self.backend.recent_operations())

    def refresh(self, *_):
        if self._dirty:
            self.notice.setText('规则尚未保存，请先保存后预览。')
            return
        self._run('load', self._read_state)

    def _run(self, kind, operation, *, after_notice=''):
        if self.worker or self._external_busy or self._shutting_down:
            if kind == 'load' and not self._shutting_down:
                self._pending_load = True
                if after_notice:
                    self._pending_notice = after_notice
            return
        worker = FileJob(lambda **_: operation(), self)
        worker.classification_kind = kind
        worker.after_notice = after_notice
        self.worker = worker
        self.notice.setText('正在处理归档分类…')
        self._update_controls()
        worker.finished.connect(self._finished)
        worker.start()

    def _finished(self):
        worker = self.worker
        if worker is None:
            return
        self.worker = None
        kind, result, error = worker.classification_kind, worker.result, worker.error
        after_notice = worker.after_notice
        worker.deleteLater()
        if error or worker.cancelled:
            self._preview = None
            self.notice.setText('操作未完成：' + (error or '已取消。'))
            self._update_controls()
            self._drain_refresh()
            return
        if kind in ('load', 'save'):
            self._show_state(result)
            self.notice.setText(after_notice or ('规则已保存，请预览已有交付。' if kind == 'save' else '请先预览，核对后再应用已有交付分类。'))
        elif kind == 'preview':
            self._preview = result
            self._show_rows(result['rows'])
            self.notice.setText(f"本次将调整 {result['changed_count']} 项；跳过 {len(result['skipped'])} 项。" if result['rows'] else '没有可核对的项目。')
        else:
            self._preview = None
            skipped = result.get('skipped', [])
            prefix = '已恢复' if kind == 'undo' else '已调整'
            message = f"{prefix} {result['changed_count']} 项；跳过 {len(skipped)} 项。"
            if skipped:
                reasons = list(dict.fromkeys(row.get('skip_reason', '') for row in skipped if row.get('skip_reason')))
                self.notice.setToolTip('；'.join(reasons))
                message += ' ' + '；'.join(reasons[:2])
                if len(reasons) > 2:
                    message += f' 另有 {len(reasons) - 2} 类原因，悬停查看。'
            self.notice.setText(message)
            self.statistics_changed.emit()
            if not self._shutting_down:
                self._run('load', self._read_state, after_notice=message)
        self._update_controls()
        self._drain_refresh()

    def _drain_refresh(self):
        # 主窗口收件可能在 statistics_changed 的同步回调中接管 busy。
        # 只合并读取请求；不能丢失刚完成批次的撤销列表，也不能覆盖未保存表单。
        if self._pending_load and not (self.worker or self._external_busy or self._dirty or self._shutting_down):
            notice = self._pending_notice
            self._pending_load = False
            self._pending_notice = ''
            self._run('load', self._read_state, after_notice=notice)

    def _show_state(self, state):
        self._loading_controls = True
        try:
            policy = self._saved_policy = state['policy']
            self.future_enabled.setChecked(policy['enabled'])
            self.minimum_files.setValue(policy['minimum_files'])
            for control, key in ((self.high_type, 'high_type_id'), (self.low_type, 'low_type_id')):
                with QSignalBlocker(control):
                    control.clear()
                    control.addItem('请选择有效类型', None)
                    for row in state['types']:
                        control.addItem(row['name'], row['id'])
                    index = control.findData(policy[key])
                    control.setCurrentIndex(max(index, 0))
            previous = self.operation_choice.currentData()
            self._operations = [row for row in state['operations'] if row['kind'] == 'batch' and row['changed_count']]
            with QSignalBlocker(self.operation_choice):
                self.operation_choice.clear()
                for row in self._operations:
                    text = f"{local_timestamp(row['created_at'])} · {row['changed_count']} 项" + (' · 已撤销' if row['undone'] else '')
                    self.operation_choice.addItem(text, row['operation_id'])
                index = self.operation_choice.findData(previous)
                if index >= 0:
                    self.operation_choice.setCurrentIndex(index)
            self._loaded = True
            self._dirty = False
            self._preview = None
            self._show_rows([])
            self._update_rule_labels()
        finally:
            self._loading_controls = False

    def _update_rule_labels(self):
        self.low_label.setText(f'文件数 0—{self.minimum_files.value() - 1}')

    def _mark_dirty(self, *_):
        if self._loading_controls:
            return
        self._dirty = True
        self._preview = None
        self._update_rule_labels()
        self.notice.setText('规则尚未保存，请先保存后预览。')
        self._update_controls()

    def _update_controls(self, *_):
        busy = self.worker is not None or self._external_busy or self._shutting_down
        self.return_button.setEnabled(not busy)
        valid_targets = self.high_type.currentData() is not None and self.low_type.currentData() is not None
        for control in (self.future_enabled, self.minimum_files, self.high_type, self.low_type):
            control.setEnabled(self._loaded and not busy)
        self.save_button.setEnabled(self._loaded and not busy and valid_targets)
        self.preview_button.setEnabled(self._loaded and not busy and not self._dirty)
        self.apply_button.setEnabled(not busy and not self._dirty and self._preview is not None and bool(self._preview['changed_count']))
        selected = next((row for row in self._operations if row['operation_id'] == self.operation_choice.currentData()), None)
        self.operation_choice.setEnabled(not busy and bool(self._operations))
        self.undo_button.setEnabled(not busy and selected is not None and not selected['undone'])

    def set_busy(self, busy):
        self._external_busy = bool(busy)
        self._update_controls()
        self._drain_refresh()

    def save_policy(self):
        if not self.save_button.isEnabled():
            return
        enabled, minimum = self.future_enabled.isChecked(), self.minimum_files.value()
        high, low = self.high_type.currentData(), self.low_type.currentData()
        self._pending_load = False
        self._pending_notice = ''
        def save():
            self.backend.set_policy(enabled, minimum, high, low)
            return self._read_state()
        self._run('save', save)

    def preview_existing(self):
        if self.preview_button.isEnabled():
            self._run('preview', self.backend.preview)

    def _show_rows(self, rows):
        selected = self.table.item(self.table.currentRow(), 0)
        selected_id = selected.data(Qt.UserRole) if selected else None
        scroll = self.table.verticalScrollBar().value()
        self.table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            result = row['skip_reason'] or ('将调整' if row['changed'] else '保持原类型')
            values = [row['project_name'], row['old_type_name'], row['new_type_name'] or '保持',
                      str(row['count']) if row['count'] is not None else '未知', result]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                item.setData(Qt.UserRole, row['project_id'])
                self.table.setItem(index, column, item)
            if row['project_id'] == selected_id:
                self.table.selectRow(index)
        self.table.resizeRowsToContents()
        self.table.verticalScrollBar().setValue(scroll)

    def apply_preview(self):
        if not self.apply_button.isEnabled():
            return
        preview = self._preview
        count = preview['changed_count']
        if QMessageBox.question(self, '确认归档分类', f'将按当前预览调整 {count} 个项目类型，未知及受保护项目保持。确认应用？') != QMessageBox.Yes:
            return
        enabled = self._saved_policy['enabled']
        self._run('apply', lambda: self.backend.apply(preview['token'], confirmed=True, enable_future=enabled))

    def undo_selected(self):
        if not self.undo_button.isEnabled():
            return
        operation_id = self.operation_choice.currentData()
        if QMessageBox.question(self, '撤销归档分类', '只恢复所选批次，此后手工选择或其他类型变更的项目会跳过。确认撤销？') != QMessageBox.Yes:
            return
        self._run('undo', lambda: self.backend.undo(operation_id, confirmed=True))

    def shutdown(self):
        self._shutting_down = True
        if self.worker:
            if not self.worker.wait(15000):
                self._shutting_down = False
                raise RuntimeError('归档分类操作仍未结束，请稍后退出。')
            self.worker.finished.disconnect(self._finished)
            self._finished()
        self._update_controls()

    def showEvent(self, event):
        super().showEvent(event)
        if not self._loaded and not self.worker:
            self.refresh()

    def resume_after_failed_update(self):
        if self.worker is not None:
            raise RuntimeError('归档分类操作尚未结束，不能重复恢复界面。')
        self._shutting_down = False
        self.set_busy(False)
        if not self._dirty:
            self.refresh()

    def closeEvent(self, event):
        if self.worker:
            self.notice.setText('归档分类正在处理，请完成后再关闭。')
            event.ignore()
            return
        super().closeEvent(event)
