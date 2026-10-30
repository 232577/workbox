"""项目类型维护；每次明确操作单独保存，不混入工作设置的取消事务。"""
import sqlite3

from PySide6.QtCore import Qt, Signal, QSignalBlocker
from PySide6.QtWidgets import QDialog, QHBoxLayout, QInputDialog, QListWidget, QListWidgetItem, QListView, QVBoxLayout, QSizePolicy

from theme import apply_to
from ui_components import button, ComboBox
from dialog_lifecycle import transient_dialog
from fluent_views import WorkbenchListWidget


class ProjectTypePickerDialog(QDialog):
    """两个项目入口共用的类型选择；项目关联只由接受结果后的调用方保存。"""
    _UNSELECTED = '__choose_project_type__'

    def __init__(self, service, projects, parent=None):
        super().__init__(parent)
        from compact_ui import label
        self.service = service
        self.projects = list(projects)
        self.selected_type_id = None
        self._active_ids = set()
        self._types_available = False
        self._loaded = False
        self._editable = bool(self.projects) and len(self.projects) <= 500 and not any(
            p.get('archived_at') or p.get('deleted_at') for p in self.projects)
        self.setWindowTitle('修改项目类型')
        apply_to(self)
        self.resize(480, 350)
        self.setMinimumWidth(320)
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.addWidget(label('修改类型', 'pageTitle'))
        self.selection_label = label(f'为所选 {len(self.projects)} 个项目设置类型。', 'subtle', True)
        layout.addWidget(self.selection_label)
        self.type_combo = ComboBox()
        self.type_combo.setMinimumWidth(0)
        self.type_combo.setSizeAdjustPolicy(ComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.type_combo.setMinimumContentsLength(12)
        self.type_combo.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.type_combo.setAccessibleName('新的项目类型')
        layout.addWidget(self.type_combo)
        self.current_name = label('', 'subtle', True)
        self.current_name.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout.addWidget(self.current_name)
        layout.addWidget(label('仅在点击“保存类型”后修改项目归属。', 'subtle', True))
        self.management_note = label('“管理类型…”中的新增、改名、停用或启用会立即保存；取消此窗口不会撤销这些操作。', 'subtle', True)
        layout.addWidget(self.management_note)
        self.error = label('', 'error', True)
        layout.addWidget(self.error)
        layout.addStretch()
        row = QHBoxLayout()
        self.manage_button = button('管理类型…')
        self.manage_button.setAutoDefault(False)
        self.manage_button.clicked.connect(self.manage_types)
        row.addWidget(self.manage_button)
        row.addStretch()
        self.cancel_button = button('取消')
        self.cancel_button.setAutoDefault(False)
        self.cancel_button.clicked.connect(self.reject)
        row.addWidget(self.cancel_button)
        self.save_button = button('保存类型', True)
        self.save_button.setDefault(True)
        self.save_button.clicked.connect(self.accept)
        row.addWidget(self.save_button)
        layout.addLayout(row)
        self.type_combo.currentIndexChanged.connect(self.update_choice)
        common = {p.get('project_type_id') for p in self.projects}
        self._initial_choice = next(iter(common)) if len(common) == 1 else self._UNSELECTED
        self.refresh_types()

    def refresh_types(self):
        desired = self.type_combo.currentData() if self._loaded else self._initial_choice
        self._loaded = True
        try:
            kinds = self.service.list_project_types(include_inactive=True)
        except (OSError, ValueError, RuntimeError, sqlite3.Error, KeyError) as error:
            self._types_available = False
            self._active_ids.clear()
            self.error.setText('项目类型读取失败：' + str(error))
            self.save_button.setEnabled(False)
            return
        self.error.clear()
        self._types_available = True
        self._active_ids = {kind['id'] for kind in kinds if kind['active']}
        current_ids = {p.get('project_type_id') for p in self.projects if p.get('project_type_id') is not None}
        with QSignalBlocker(self.type_combo):
            self.type_combo.clear()
            self.type_combo.addItem('请选择新的项目类型', self._UNSELECTED)
            self.type_combo.addItem('未分类', None)
            for kind in kinds:
                if kind['active'] or kind['id'] in current_ids or kind['id'] == desired:
                    self.type_combo.addItem(kind['name'] + ('（已停用）' if not kind['active'] else ''), kind['id'])
                    index = self.type_combo.count() - 1
                    self.type_combo.setItemData(index, kind['name'], Qt.ToolTipRole)
                    if not kind['active']:
                        self.type_combo.model().item(index).setEnabled(False)
            # 当前类型若被外部删除，也不能静默改成未分类。
            if desired is not None and desired != self._UNSELECTED and self.type_combo.findData(desired) < 0:
                old = next((p.get('project_type_name') for p in self.projects if p.get('project_type_id') == desired), None)
                self.type_combo.addItem((old or '原类型') + '（已不可用）', desired)
                self.type_combo.model().item(self.type_combo.count() - 1).setEnabled(False)
            self.type_combo.setCurrentIndex(max(0, self.type_combo.findData(desired)))
        self.update_choice()

    def update_choice(self, *_):
        value = self.type_combo.currentData()
        valid = value is None or type(value) is int and value in self._active_ids
        self.save_button.setEnabled(self._editable and self._types_available and valid)
        self.current_name.setText(self.type_combo.currentText())
        self.type_combo.setToolTip(self.type_combo.currentText())
        if not self._editable:
            self.error.setText('请选择 1–500 个未归档、未删除的项目后修改类型。')

    def manage_types(self):
        try:
            with transient_dialog(ProjectTypesDialog(self.service, self)) as dialog:
                dialog.types_changed.connect(self.refresh_types)
                dialog.exec()
        except (OSError, ValueError, RuntimeError, sqlite3.Error, KeyError) as error:
            self._types_available = False
            self.error.setText('项目类型管理未打开：' + str(error) + '。请稍后重试。')
            self.save_button.setEnabled(False)
            return
        self.refresh_types()

    def accept(self):
        value = self.type_combo.currentData()
        if not self._editable or value == self._UNSELECTED:
            return
        # 模态窗口期间字典可能被其他入口修改，提交时再检查实际可分配类型。
        try:
            active_ids = {kind['id'] for kind in self.service.list_project_types() if kind['active']}
        except (OSError, ValueError, RuntimeError, sqlite3.Error, KeyError) as error:
            self.error.setText('项目类型读取失败：' + str(error))
            self.save_button.setEnabled(False)
            return
        if value is not None and (type(value) is not int or value not in active_ids):
            self.refresh_types()
            self.error.setText('该类型已停用或不存在，请选择其他类型。')
            self.save_button.setEnabled(False)
            return
        self.selected_type_id = value
        super().accept()


def choose_project_type(service, parent, projects) -> tuple[bool, int | None]:
    """返回用户确认的类型 ID；不写入项目关联，取消返回 (False, None)。"""
    with transient_dialog(ProjectTypePickerDialog(service, projects, parent)) as dialog:
        if dialog.exec() == QDialog.Accepted:
            return True, dialog.selected_type_id
    return False, None


class ProjectTypesDialog(QDialog):
    types_changed = Signal()

    def __init__(self, service, parent=None):
        super().__init__(parent)
        from compact_ui import label, standard_list_scrolling, WrappedNameDelegate
        from qfluentwidgets import CardWidget
        self.service = service
        self.setWindowTitle('项目类型')
        apply_to(self)
        self.resize(540, 540)
        layout = QVBoxLayout(self)
        layout.addWidget(label('项目类型', 'pageTitle'))
        layout.addWidget(label('每次操作立即保存，取消外层窗口不会撤销。停用后保留项目归属和历史，不能再分配该类型；停用默认类型后，新项目改为未分类。', 'subtle', True))
        default_card = CardWidget(self)
        default_layout = QVBoxLayout(default_card)
        default_layout.setContentsMargins(16, 12, 16, 12)
        default_layout.addWidget(label('新项目默认类型', 'title'))
        self.default_type_combo = ComboBox()
        self.default_type_combo.setAccessibleName('新项目默认类型')
        self.default_type_combo.setMinimumWidth(0)
        self.default_type_combo.setMinimumContentsLength(12)
        self.default_type_combo.setSizeAdjustPolicy(ComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.default_type_combo.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        default_layout.addWidget(self.default_type_combo)
        default_layout.addWidget(label('选择后立即保存到本机，仅用于以后创建的项目。已有项目的类型不变；选择“未分类”可取消默认类型。', 'subtle', True))
        layout.addWidget(default_card)
        self._saved_default_type = None
        self.default_type_combo.currentIndexChanged.connect(self.save_default_type)
        self.type_list = WorkbenchListWidget()
        self.type_list.setWordWrap(True)
        self.type_list.setAccessibleName('项目类型列表')
        standard_list_scrolling(self.type_list)
        self.type_list.setUniformItemSizes(False)
        self.type_list.setTextElideMode(Qt.ElideNone)
        self.type_list.setResizeMode(QListView.Adjust)
        self.empty = label('还没有自定义类型，点击“新增类型”。已有项目继续保持未分类。', 'subtle', True)
        layout.addWidget(self.empty)
        layout.addWidget(self.type_list, 1)
        self.error = label('', 'error', True)
        layout.addWidget(self.error)
        row = QHBoxLayout()
        self.add_button = button('新增类型', True)
        self.rename_button = button('改名')
        self.toggle_button = button('停用')
        self.add_button.clicked.connect(self.add_type)
        self.rename_button.clicked.connect(self.rename_type)
        self.toggle_button.clicked.connect(self.toggle_type)
        for control in (self.add_button, self.rename_button, self.toggle_button):
            row.addWidget(control)
        row.addStretch()
        close = button('关闭')
        close.clicked.connect(self.accept)
        row.addWidget(close)
        layout.addLayout(row)
        self.type_list.currentItemChanged.connect(self.update_actions)
        self.refresh()

    def refresh(self, selected_id=None):
        current = self.type_list.currentItem()
        selected_id = selected_id if selected_id is not None else current.data(Qt.UserRole)['id'] if current else None
        # 保留外层管理入口的读取失败契约：构造失败由调用方显示错误并允许重试。
        kinds = self.service.list_project_types(include_inactive=True)
        default_id = self.service.get_default_project_type()
        self._saved_default_type = default_id
        with QSignalBlocker(self.default_type_combo):
            self.default_type_combo.clear()
            self.default_type_combo.addItem('未分类', None)
            for kind in kinds:
                if kind['active']:
                    self.default_type_combo.addItem(kind['name'], kind['id'])
                    self.default_type_combo.setItemData(self.default_type_combo.count() - 1, kind['name'], Qt.ToolTipRole)
            self.default_type_combo.setCurrentIndex(max(0, self.default_type_combo.findData(default_id)))
        self.default_type_combo.setToolTip(self.default_type_combo.currentText())
        self.type_list.clear()
        for kind in kinds:
            item = QListWidgetItem(f"{kind['name']} · {kind.get('project_count', 0)} 个项目" + (' · 已停用' if not kind['active'] else ''))
            item.setData(Qt.UserRole, kind)
            item.setToolTip(kind['name'])
            self.type_list.addItem(item)
            if kind['id'] == selected_id:
                self.type_list.setCurrentItem(item)
        self.update_actions()

    def save_default_type(self, *_):
        value = self.default_type_combo.currentData()
        try:
            saved = self.service.set_default_project_type(value)
        except (OSError, ValueError, RuntimeError, sqlite3.Error, KeyError) as error:
            with QSignalBlocker(self.default_type_combo):
                self.default_type_combo.setCurrentIndex(max(0, self.default_type_combo.findData(self._saved_default_type)))
            self.default_type_combo.setToolTip(self.default_type_combo.currentText())
            self.error.setText('新项目默认类型未保存：' + str(error))
            return
        self._saved_default_type = saved
        self.default_type_combo.setToolTip(self.default_type_combo.currentText())
        self.error.clear()
        self.types_changed.emit()

    def update_actions(self, *_):
        self.empty.setVisible(self.type_list.count()==0)
        current = self.type_list.currentItem()
        self.rename_button.setEnabled(current is not None)
        self.toggle_button.setEnabled(current is not None)
        self.toggle_button.setText('启用' if current and not current.data(Qt.UserRole)['active'] else '停用')

    def perform(self, action):
        try:
            result = action()
        except (OSError, ValueError, RuntimeError, sqlite3.Error, KeyError) as error:
            self.error.setText('项目类型未保存：' + str(error))
            return
        self.error.clear()
        self.refresh(result['id'] if isinstance(result, dict) else result if type(result) is int else None)
        self.types_changed.emit()

    def add_type(self):
        name, accepted = QInputDialog.getText(self, '新增项目类型', '类型名称')
        if accepted:
            self.perform(lambda: self.service.create_project_type(name))

    def rename_type(self):
        current = self.type_list.currentItem()
        if not current:
            return
        kind = current.data(Qt.UserRole)
        name, accepted = QInputDialog.getText(self, '更改项目类型名称', '类型名称', text=kind['name'])
        if accepted:
            self.perform(lambda: self.service.rename_project_type(kind['id'], name))

    def toggle_type(self):
        current = self.type_list.currentItem()
        if current:
            kind = current.data(Qt.UserRole)
            self.perform(lambda: self.service.set_project_type_active(kind['id'], not kind['active']))
