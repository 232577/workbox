"""项目改名与合并的只读预览和明确确认。"""
from threading import Event

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QSizePolicy, QVBoxLayout

from theme import apply_to
from ui_components import ComboBox, LineEdit, button
from dialog_chrome import add_header, add_actions


class ProjectChangeDialog(QDialog):
    preview_requested = Signal(object)

    def __init__(self, project, parent=None):
        super().__init__(parent)
        self.project_id = project['id']
        self.request = None
        self.plan = None
        self.serial = 0
        self.waiting = False
        self.preview_cancel = None
        self.setWindowTitle('修正项目名称')
        self.resize(620, 520)
        apply_to(self, 'dialog')
        layout = QVBoxLayout(self)
        add_header(layout, '修正项目名称', project['name'], eyebrow='WORKBOX / 先预览，再确认')
        layout.addWidget(QLabel('项目名称'))
        self.name_edit = LineEdit(project['name'])
        self.name_edit.setAccessibleName('新的项目名称')
        layout.addWidget(self.name_edit)
        layout.addWidget(QLabel('同名项目的保留目标'))
        self.target_box = ComboBox()
        self.target_box.setAccessibleName('同名项目保留目标')
        self.target_box.setMinimumWidth(0)
        self.target_box.setSizeAdjustPolicy(ComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.target_box.setMinimumContentsLength(12)
        self.target_box.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.target_box.addItem('请先检查变更', None)
        self.target_box.setEnabled(False)
        layout.addWidget(self.target_box)
        self.summary = QPlainTextEdit()
        self.summary.setAccessibleName('项目变更检查结果')
        self.summary.setReadOnly(True)
        self.summary.setPlainText('输入名称后点击“检查变更”。只修改项目工作目录名称，普通文件和 ZIP 文件名保持原样。')
        layout.addWidget(self.summary, 1)
        row = QHBoxLayout()
        self.check_button = button('检查变更')
        self.confirm_button = button('确认改名', True)
        self.confirm_button.setEnabled(False)
        cancel = button('取消')
        row.addWidget(self.check_button)
        row.addStretch()
        row.addWidget(cancel)
        row.addWidget(self.confirm_button)
        add_actions(layout, row)
        self.name_edit.textChanged.connect(self._name_changed)
        self.target_box.currentIndexChanged.connect(self._target_changed)
        self.check_button.clicked.connect(self._check)
        self.confirm_button.clicked.connect(self._confirm)
        cancel.clicked.connect(self.reject)
        self.finished.connect(self._cancel_preview)

    def _cancel_preview(self, *_):
        if self.preview_cancel is not None:
            self.preview_cancel.set()
            self.preview_cancel = None

    def _invalidate(self):
        self._cancel_preview()
        self.serial += 1
        self.plan = None
        self.confirm_button.setEnabled(False)

    def _name_changed(self):
        self._invalidate()
        self.target_box.blockSignals(True)
        self.target_box.clear()
        self.target_box.addItem('请先检查变更', None)
        self.target_box.setEnabled(False)
        self.target_box.blockSignals(False)

    def _target_changed(self):
        self._invalidate()

    def _check(self):
        if self.waiting:
            return
        self._invalidate()
        self.waiting = True
        self.preview_cancel = Event()
        self.destroyed.connect(self.preview_cancel.set)
        self.check_button.setEnabled(False)
        self.summary.setPlainText('正在检查变更…')
        self.preview_requested.emit({'project_id': self.project_id, 'name': self.name_edit.text(),
                                     'target_id': self.target_box.currentData(), 'serial': self.serial,
                                     'cancel_event': self.preview_cancel})

    def show_preview(self, serial, plan=None, error=None):
        self.waiting = False
        self.check_button.setEnabled(True)
        if serial != self.serial:
            return
        if error:
            self.summary.setPlainText('检查失败：' + str(error))
            return
        plan = plan or {}
        candidates = plan.get('candidates') or []
        if plan.get('kind') == 'choose_target':
            self.target_box.blockSignals(True)
            self.target_box.clear()
            self.target_box.addItem('请选择保留的项目', None)
            for item in candidates:
                text = f"{item.get('name', '')} · {item.get('status', '')} · {item.get('batch_name', '')} · {item.get('workspace_path') or '无工作目录'}"
                if item.get('blocked_reason'):
                    text += ' · 不可选：' + str(item['blocked_reason'])
                self.target_box.addItem(text, item.get('id'))
                self.target_box.setItemData(self.target_box.count() - 1, text, Qt.ToolTipRole)
                if item.get('blocked_reason'):
                    self.target_box.model().item(self.target_box.count() - 1).setEnabled(False)
            self.target_box.setCurrentIndex(0)
            self.target_box.setEnabled(True)
            self.target_box.blockSignals(False)
        self.summary.setPlainText(self._summary(plan))
        valid = plan.get('kind') in ('rename', 'merge') and bool(plan.get('token'))
        if plan.get('kind') == 'merge':
            valid = valid and bool((plan.get('target') or {}).get('id'))
        if valid:
            self.plan = plan
            self.confirm_button.setText('确认合并' if plan['kind'] == 'merge' else '确认改名')
            self.confirm_button.setEnabled(True)

    @staticmethod
    def _summary(plan):
        def describe(title, item):
            if not item:
                return []
            return [f"{title}：{item.get('name', '')}（{item.get('status', '未知状态')}）",
                    f"批次：{item.get('batch_name') or '未指定'}；类型：{item.get('type_name') or '未分类'}；计入工作量：{'是' if item.get('included', True) else '否'}",
                    f"工作目录：{item.get('workspace_path') or '无'}"]
        lines = describe('原项目', plan.get('source')) + describe('保留项目', plan.get('target'))
        for key, label in [('new_name', '新名称'), ('folder_from', '原目录'), ('folder_to', '变更后目录'),
                           ('copy_path', '合并资料位置'), ('result_status', '合并后状态'),
                           ('result_type_name', '合并后类型')]:
            if plan.get(key) is not None:
                lines.append(f"{label}：{plan[key]}")
        if plan.get('result_included') is not None:
            lines.append('合并后计入工作量：' + ('是' if plan['result_included'] else '否'))
        if plan.get('kind') == 'merge':
            lines.append('保留目标项目批次和类型；备注合并、日期取较早值，加急按任一项目保留。不生成交付记录。旧工作目录保留作备份。')
            lines.append('合并以最后核对的材料清单为准；请暂停编辑这两个目录，完成后在保留项目中继续工作。')
        if plan.get('warning'):
            lines.append(str(plan['warning']))
        if plan.get('kind') == 'choose_target':
            lines.append('请选择保留项目，然后再次检查变更。')
            for candidate in plan.get('candidates') or []:
                lines.extend(describe('候选项目', candidate))
                if candidate.get('blocked_reason'):
                    lines.append('不可选：' + str(candidate['blocked_reason']))
        if plan.get('kind') == 'blocked':
            lines.append('当前无法执行，请处理上述冲突后重新检查。')
        lines.append('普通文件和 ZIP 文件名保持原样。')
        return '\n'.join(lines)

    def _confirm(self):
        if not self.plan:
            return
        merge = self.plan['kind'] == 'merge'
        self.request = {'project_id': self.project_id, 'name': self.name_edit.text(),
                        'expected_token': self.plan['token'],
                        'merge_target_id': (self.plan.get('target') or {}).get('id') if merge else None,
                        'confirmed_merge': merge}
        self.accept()
