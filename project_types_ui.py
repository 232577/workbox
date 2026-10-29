"""项目类型维护；每次明确操作单独保存，不混入工作设置的取消事务。"""
import sqlite3

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QInputDialog, QListWidget, QListWidgetItem, QListView, QVBoxLayout

from theme import apply_to
from ui_components import button


class ProjectTypesDialog(QDialog):
    types_changed = Signal()

    def __init__(self, service, parent=None):
        super().__init__(parent)
        from compact_ui import label, standard_list_scrolling, WrappedNameDelegate
        self.service = service
        self.setWindowTitle('项目类型')
        apply_to(self)
        self.resize(540, 440)
        layout = QVBoxLayout(self)
        layout.addWidget(label('项目类型', 'pageTitle'))
        layout.addWidget(label('每次操作立即保存。停用后保留项目归属和历史，新增项目不能再选择该类型。', 'subtle', True))
        self.type_list = QListWidget()
        self.type_list.setWordWrap(True)
        self.type_list.setAccessibleName('项目类型列表')
        standard_list_scrolling(self.type_list)
        self.type_list.setUniformItemSizes(False)
        self.type_list.setTextElideMode(Qt.ElideNone)
        self.type_list.setResizeMode(QListView.Adjust)
        self.type_list.setItemDelegate(WrappedNameDelegate(self.type_list))
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
        self.type_list.clear()
        for kind in self.service.list_project_types(include_inactive=True):
            item = QListWidgetItem(f"{kind['name']} · {kind.get('project_count', 0)} 个项目" + (' · 已停用' if not kind['active'] else ''))
            item.setData(Qt.UserRole, kind)
            item.setToolTip(kind['name'])
            self.type_list.addItem(item)
            if kind['id'] == selected_id:
                self.type_list.setCurrentItem(item)
        self.update_actions()

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
