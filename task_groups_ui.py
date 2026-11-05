"""任务组工作台：原文、当前交付关联和人工交接分别展示。"""
from datetime import datetime
from pathlib import Path
import copy
import re

from PySide6.QtCore import Qt, Signal, QItemSelectionModel, QRect
from PySide6.QtGui import QColor, QBrush, QFontMetrics
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QColorDialog,
    QDialog, QDialogButtonBox, QInputDialog, QLabel, QMenu, QMessageBox,
    QPlainTextEdit, QPushButton, QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
    QHBoxLayout, QHeaderView, QWidget, QStyleOptionViewItem)
from qfluentwidgets import TreeWidget
from qfluentwidgets.components.widgets.tree_view import TreeItemDelegate

from database import DELIVERY_TIMEZONE
from fluent_views import configure_native_scroll
from ui import FileJob
from ui_components import ComboBox as QComboBox, LineEdit as QLineEdit, CheckBox, button


class WrappedTreeDelegate(TreeItemDelegate):
    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        if index.column() == 0:
            option.text = re.sub(r'[A-Za-z0-9_./\\-]{12,}',
                                 lambda match: '\u200b'.join(match.group()), option.text)
            option.features |= QStyleOptionViewItem.WrapText
            option.textElideMode = Qt.ElideNone

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        if index.column() == 0:
            option = QStyleOptionViewItem(option)
            self.initStyleOption(option, index)
            width = max(1, self.parent().columnWidth(0) - 70)
            bounds = QFontMetrics(option.font).boundingRect(QRect(0, 0, width, 100000), Qt.TextWordWrap, option.text)
            size.setHeight(max(size.height(), bounds.height() + 18))
        return size


class TaskGroupsPage(QWidget):
    task_text_requested = Signal(str, object, bool)
    project_requested = Signal(int)
    classification_requested = Signal()
    receipt_requested = Signal(int)
    changed = Signal()

    def __init__(self, groups, parent=None):
        super().__init__(parent)
        self.groups = groups
        self.worker = None
        self._shutting_down = False
        self._external_busy = False
        self._rows_signature = None
        self.rows = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        filters = QHBoxLayout()
        self.scope = QComboBox()
        for name, value in [('未结任务组', 'open'), ('全部任务组', 'all'), ('今日收件', 'today')]:
            self.scope.addItem(name, value)
        self.search = QLineEdit()
        self.search.setPlaceholderText('搜索组号、任务号、项目或消息')
        self.search.setAccessibleName('搜索任务组')
        self.search.setClearButtonEnabled(True)
        filters.addWidget(self.scope)
        filters.addWidget(self.search, 1)
        self.only_unhanded = CheckBox('仅未交接')
        self.only_unhanded.setAccessibleName('仅显示未交接任务')
        filters.addWidget(self.only_unhanded)
        self.refresh_button = button('刷新')
        filters.addWidget(self.refresh_button)
        layout.addLayout(filters)
        self.note = QLabel('')
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        splitter = self.splitter = QSplitter(Qt.Horizontal)
        self.tree = TreeWidget()
        configure_native_scroll(self.tree)
        self.tree.setItemDelegate(WrappedTreeDelegate(self.tree))
        self.tree.setWordWrap(True)
        self.tree.setTextElideMode(Qt.ElideNone)
        self.tree.setHeaderLabels(['任务组 / 任务', '本次就绪', '人工交接'])
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setAccessibleName('任务组清单')
        self.tree.setAlternatingRowColors(False)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        splitter.addWidget(self.tree)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setAccessibleName('原消息和任务详情')
        splitter.addWidget(self.details)
        splitter.setSizes([600, 360])
        layout.addWidget(splitter, 1)
        actions = QHBoxLayout()
        self.add_button = button('添加消息')
        self.new_button = button('新建任务组')
        self.handoff_button = button('记录已交接', True)
        self.more_button = button('更多操作')
        self.copy_button = button('复制本组未交')
        for control in (self.new_button, self.add_button, self.handoff_button, self.more_button, self.copy_button):
            actions.addWidget(control)
        layout.addLayout(actions)
        menu = QMenu(self.more_button)
        for caption, callback in [('新一轮任务', self.new_round), ('查看项目文件', self._open_project),
                                  ('撤销交接', lambda: self._action('undo_handoff')),
                                  ('撤回任务', lambda: self._action('withdraw')),
                                  ('重开任务', lambda: self._action('reopen')),
                                  ('关联交付记录', self._bind), ('移动任务到其他组', self._move),
                                  ('重命名任务组', self._rename), ('设置组颜色', self._color),
                                  ('预览并导入历史来源', self._history),
                                  ('核对本组收件消息', self.review_message),
                                  ('导出本组表格', self.export_group),
                                  ('复制当前筛选的全部未交清单', lambda: self._copy(all_groups=True)),
                                  ('归档分类', self.classification_requested.emit)]:
            menu.addAction(caption, callback)
        self.more_button.setMenu(menu)
        self.scope.currentIndexChanged.connect(self.refresh)
        self.only_unhanded.toggled.connect(self.refresh)
        self.search.textChanged.connect(self.refresh)
        self.refresh_button.clicked.connect(self.refresh)
        self.tree.itemSelectionChanged.connect(self._show_details)
        self.tree.itemDoubleClicked.connect(lambda *_: self._open_project())
        self.add_button.clicked.connect(lambda: self._text(False))
        self.new_button.clicked.connect(self.new_group)
        self.handoff_button.clicked.connect(lambda: self._action('handoff'))
        self.copy_button.clicked.connect(self._copy)

    def refresh(self, *_):
        scope = self.scope.currentData()
        try:
            rows = self.groups.snapshot(query=self.search.text().strip(), open_only=scope == 'open',
                received_day=datetime.now(DELIVERY_TIMEZONE).date().isoformat() if scope == 'today' else None)
        except Exception as exc:
            self.note.setText('任务组读取失败：' + str(exc))
            return
        signature = (rows, self.only_unhanded.isChecked())
        if signature == self._rows_signature:
            self.rows = rows
            self.note.setText('没有符合条件的任务组。' if not rows else '')
            return
        self._rows_signature = copy.deepcopy(signature)
        selected = {item.data(0, Qt.UserRole) for item in self.tree.selectedItems()}
        current = self.tree.currentItem()
        current_key = current.data(0, Qt.UserRole) if current else None
        expanded = {self.tree.topLevelItem(i).data(0, Qt.UserRole) for i in range(self.tree.topLevelItemCount())
                    if self.tree.topLevelItem(i).isExpanded()}
        old_scroll = self.tree.verticalScrollBar().value()
        self.rows = rows
        self.tree.blockSignals(True)
        self.tree.clear()
        target = None
        for group in rows:
            code = group.get('code', group.get('group_code', ''))
            title = group.get('title', group.get('name', '')) or ''
            counts = group.get('counts', {})
            total = counts.get('total', len(group.get('tasks', [])))
            active = total - counts.get('withdrawn', 0)
            description = (f'{code}  {title}\n总 {total} · 待交接 {counts.get("pending", 0)}'
                           f' · 待核对 {counts.get("needs_review", 0)} · 撤回 {counts.get("withdrawn", 0)}')
            if group.get('history_batch_id') is not None:
                description += ' · 历史补录'
            if group.get('message_review_count'):
                description += f' · 待核对消息 {group["message_review_count"]}'
            parent = QTreeWidgetItem([description, f'就绪 {counts.get("ready", 0)}/{active}',
                                     f'已交接 {counts.get("handed", 0)}/{active}'])
            parent.setData(0, Qt.UserRole, ('group', group['id']))
            parent.setToolTip(0, f'{code}  {title}')
            color = QColor(group.get('color') or '')
            if color.isValid():
                color.setAlpha(45)
                for col in range(3):
                    parent.setBackground(col, QBrush(color))
            self.tree.addTopLevelItem(parent)
            for task in group.get('tasks', []):
                if self.only_unhanded.isChecked() and (task.get('handed_over') or task.get('withdrawn')):
                    continue
                name = f"{task.get('code', '')}  {task.get('project_name', '')}"
                state = ('已撤回' if task.get('withdrawn') else '已交接' if task.get('handed_over')
                         else '待核对' if task.get('needs_review') else '未交接')
                child = QTreeWidgetItem([name, '已就绪' if task.get('material_ready') else '未就绪', state])
                child.setData(0, Qt.UserRole, ('task', task['id']))
                child.setToolTip(0, name)
                if color.isValid():
                    tint = QColor(color)
                    tint.setAlpha(18)
                    for col in range(3):
                        child.setBackground(col, QBrush(tint))
                parent.addChild(child)
                if child.data(0, Qt.UserRole) in selected: child.setSelected(True)
                if child.data(0, Qt.UserRole) == current_key: target = child
            parent.setExpanded(parent.data(0, Qt.UserRole) in expanded)
            if parent.data(0, Qt.UserRole) in selected: parent.setSelected(True)
            if parent.data(0, Qt.UserRole) == current_key: target = parent
        if target:
            self.tree.setCurrentItem(target, 0, QItemSelectionModel.NoUpdate)
        self.tree.verticalScrollBar().setValue(old_scroll)
        self.tree.blockSignals(False)
        self.note.setText('没有符合条件的任务组。' if not rows else '')
        self._show_details()

    def _selected(self):
        keys = [item.data(0, Qt.UserRole) for item in self.tree.selectedItems()]
        return [task for group in self.rows for task in group.get('tasks', []) if ('task', task['id']) in keys]

    def _group(self):
        item = self.tree.currentItem()
        if item and item.parent(): item = item.parent()
        key = item.data(0, Qt.UserRole) if item else None
        return next((group for group in self.rows if key == ('group', group['id'])), None)

    def _show_details(self):
        group = self._group()
        if not group:
            self.details.setPlainText('选择任务组或任务，查看原消息和关联信息。')
            return
        try:
            from delivery_ui import beijing_timestamp
            messages = self.groups.messages(group['id'])
            text = '\n\n'.join(f"原消息 {m.get('sequence', '')}  收件时间（北京时间）：{beijing_timestamp(m.get('received_at', ''))}\n{m.get('raw_text', '')}" for m in messages)
            tasks = self._selected()
            if tasks:
                task = tasks[0]
                text += f"\n\n任务：{task.get('code', '')}  {task.get('project_name', '')}\n关联归档：{task.get('archive_path') or '未关联'}\n交付记录：{task.get('event_id') or '未关联'}"
                if task.get('needs_review'): text += '\n需要核对'
                source_group = task.get('original_group_id')
                if source_group and source_group != group['id']:
                    source = [m for m in self.groups.messages(source_group) if task['id'] in m.get('task_ids', [])]
                    if source:
                        text += '\n\n原任务号来源组消息：\n' + '\n\n'.join(m.get('raw_text', '') for m in source)
                if hasattr(self.groups, 'actions'):
                    labels = {'handoff': '已交接', 'undo-handoff': '撤销交接', 'withdraw': '撤回任务',
                              'reopen': '重开任务', 'move': '更正归组'}
                    actions = self.groups.actions(task['id'])
                    if actions:
                        text += '\n\n任务操作记录（北京时间）：\n' + '\n'.join(
                            f"{beijing_timestamp(action.get('created_at', ''))}  {labels.get(action['kind'], action['kind'])}"
                            + (f" · {action['reason']}" if action.get('reason') else '') for action in actions)
            self.details.setPlainText(text or '该组暂无原消息。')
        except Exception as exc:
            self.details.setPlainText('详情读取失败：' + str(exc))

    def select_ids(self, group_id, task_id=None):
        """定位指定任务；若被未结筛选隐藏，切换到全部。"""
        if self.scope.currentData() != 'all':
            self.scope.setCurrentIndex(self.scope.findData('all'))
        if task_id is not None:
            self.only_unhanded.setChecked(False)
        self.search.clear()
        self.refresh()
        for index in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(index)
            if group.data(0, Qt.UserRole) != ('group', group_id): continue
            group.setExpanded(True)
            target = group
            if task_id is not None:
                target = next((group.child(i) for i in range(group.childCount())
                               if group.child(i).data(0, Qt.UserRole) == ('task', task_id)), group)
            self.tree.setCurrentItem(target)
            self.tree.scrollToItem(target)
            return task_id is None or target is not group
        return False

    def new_round(self, text=''):
        if not isinstance(text, str):
            text = ''
        if not text:
            selected = self._selected()
            text = '\n'.join(f'{index}. {item["project_name"]}' for index, item in enumerate(selected, 1))
        self._text(True, text)

    def new_group(self):
        text, ok = QInputDialog.getMultiLineText(self, '新建任务组', '输入项目消息：', '')
        if ok and text.strip():
            self.task_text_requested.emit(text, None, False)

    def _text(self, new_round, initial_text=''):
        group = self._group()
        if not new_round and not group:
            self.note.setText('请先选择要追加消息的任务组。'); return
        text, ok = QInputDialog.getMultiLineText(self, '新一轮任务' if new_round else '添加消息到本组', '输入原消息：', initial_text)
        if ok and text.strip(): self.task_text_requested.emit(text, None if new_round else group['id'], new_round)

    def _open_project(self):
        tasks = self._selected()
        if len(tasks) == 1 and tasks[0].get('project_id'):
            self.project_requested.emit(tasks[0]['project_id'])
        else: self.note.setText('请选择一个有关联项目的任务。')

    def _run(self, operation, *, kind='mutation'):
        if self.worker or self._external_busy: return
        self.worker = FileJob(operation if kind == 'export' else lambda **_: operation(), self)
        self.worker.task_operation_kind = kind
        self.setEnabled(False)
        self.note.setText('正在处理…')
        self.worker.finished.connect(self._finished)
        self.worker.start()

    def _finished(self):
        worker, self.worker = self.worker, None
        if worker is None:
            return
        self.setEnabled(not self._external_busy)
        error = worker.error
        was_cancelled = worker.cancelled
        exported = getattr(worker, 'task_operation_kind', '') == 'export'
        result = worker.result
        worker.deleteLater()
        if not self._shutting_down:
            self.refresh()
        message = '已取消操作。' if was_cancelled else '操作未完成：' + error if error else '操作已完成。'
        if exported and not error and not was_cancelled:
            message = f"已导出本组 {result['task_count']} 项任务，保存位置：{result['path']}"
        self.note.setText(message)
        self.note.setToolTip(message)
        if not error and not was_cancelled and not exported and not self._shutting_down: self.changed.emit()

    def _action(self, name):
        tasks = self._selected()
        ids = [task['id'] for task in tasks]
        if not ids: self.note.setText('请先选择任务，可多选。'); return
        caption = {'handoff':'记录已交接', 'undo_handoff':'撤销交接', 'withdraw':'撤回任务', 'reopen':'重开任务'}[name]
        message = f'确认对所选 {len(ids)} 项执行“{caption}”？'
        options = {}
        if name == 'handoff':
            if any(not item.get('material_ready') or item.get('withdrawn') for item in tasks):
                self.note.setText('所选任务有材料未就绪项，请先关联有效交付记录。')
                return
            options['expected_bindings'] = {item['id']: item.get('binding_id') for item in tasks}
            message += '\n仅记录你已实际发出的以下关联版本，不发送任何文件：\n\n'
            message += '\n\n'.join(f'{item["code"]} · {item["project_name"]}\n{item.get("archive_path", "")}' for item in tasks)
        if QMessageBox.question(self, caption, message, QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self._run(lambda: getattr(self.groups, name)(ids, confirmed=True, **options))

    def _bind(self):
        ids = [task['id'] for task in self._selected()]
        if not ids: self.note.setText('请先选择任务。'); return
        try: choices = self.groups.delivery_choices(ids)
        except Exception as exc: self.note.setText('读取交付记录失败：' + str(exc)); return
        if not choices: self.note.setText('所选任务没有共同的有效交付记录。'); return
        labels = [f"#{row.get('id')} · {row.get('occurred_at', '')} · {Path(row.get('archive_path', '')).name}" for row in choices]
        value, ok = QInputDialog.getItem(self, '关联交付记录', '选择有效交付记录：', labels, 0, False)
        if ok: self._run(lambda: self.groups.bind_delivery(ids, choices[labels.index(value)]['id'], confirmed=True))

    def review_message(self):
        group = self._group()
        if group is None:
            self.note.setText('请先选择任务组。')
            return
        pending = [item for item in self.groups.messages(group['id'])
                   if item.get('state') == 'needs-review' and item.get('receipt_id')]
        if not pending:
            self.note.setText('本组没有待核对的消息。')
            return
        chosen = pending[0]
        if len(pending) > 1:
            labels = [f"消息{item['sequence']} · {item['raw_text'][:70]}" for item in pending]
            value, ok = QInputDialog.getItem(self, '选择待核对消息', '原消息：', labels, 0, False)
            if not ok:
                return
            chosen = pending[labels.index(value)]
        self.receipt_requested.emit(chosen['receipt_id'])

    def _move(self):
        tasks = self._selected()
        if len(tasks) != 1: self.note.setText('请选择一项任务。'); return
        try: groups = self.groups.snapshot(open_only=False)
        except Exception as exc: self.note.setText('读取任务组失败：' + str(exc)); return
        groups = [g for g in groups if g['id'] != tasks[0]['group_id']]
        if not groups: self.note.setText('没有可移入的其他任务组。'); return
        labels = [f"{g.get('code', '')}  {g.get('title', '')}" for g in groups]
        value, ok = QInputDialog.getItem(self, '移动任务', '选择目标组；原任务号保持不变：', labels, 0, False)
        if ok: self._run(lambda: self.groups.move_task(tasks[0]['id'], groups[labels.index(value)]['id'], confirmed=True))

    def _rename(self):
        group = self._group()
        if not group: self.note.setText('请先选择任务组。'); return
        value, ok = QInputDialog.getText(self, '重命名任务组', '名称：', text=group.get('title') or '')
        if ok and value.strip(): self._run(lambda: self.groups.rename_group(group['id'], value.strip()))

    def _color(self):
        group = self._group()
        if not group: self.note.setText('请先选择任务组。'); return
        color = QColorDialog.getColor(QColor(group.get('color') or '#48a99d'), self, '设置任务组颜色')
        if color.isValid(): self._run(lambda: self.groups.set_color(group['id'], color.name()))

    def _history(self):
        try: preview = self.groups.history_candidates()
        except Exception as exc: self.note.setText('历史来源读取失败：' + str(exc)); return
        rows = preview.get('rows', [])
        if not rows: self.note.setText('没有可导入的历史来源。'); return
        dialog = QDialog(self); dialog.setWindowTitle('选择历史来源')
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel('选择要导入的历史来源；导入不会自动确认交接。'))
        tree = QTreeWidget(); tree.setHeaderLabels(['历史来源']); layout.addWidget(tree)
        for row in rows:
            names = '、'.join(str(project.get('name', '')) for project in row.get('projects', []))
            originals = '\n'.join(str(message.get('raw_text', '')) for message in row.get('messages', []))
            description = f"{row.get('batch_name', '')}  {row.get('created_at', '')}  · {names}"
            item = QTreeWidgetItem([description])
            item.setToolTip(0, '项目：' + names + '\n原消息：\n' + originals)
            for message in row.get('messages', []):
                detail = QTreeWidgetItem(['原消息：' + str(message.get('raw_text', ''))])
                detail.setToolTip(0, str(message.get('raw_text', '')))
                item.addChild(detail)
            item.setData(0, Qt.UserRole, row.get('batch_id', row.get('id')))
            item.setCheckState(0, Qt.Unchecked); tree.addTopLevelItem(item)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject); layout.addWidget(buttons)
        if dialog.exec() != QDialog.Accepted: return
        ids = [tree.topLevelItem(i).data(0, Qt.UserRole) for i in range(tree.topLevelItemCount()) if tree.topLevelItem(i).checkState(0) == Qt.Checked]
        if ids: self._run(lambda: self.groups.import_history(preview['token'], batch_ids=ids, confirmed=True))

    def _copy(self, _checked=False, *, all_groups=False):
        group = self._group()
        if not all_groups and group is None:
            self.note.setText('请先选择要复制清单的任务组。')
            return
        rows = self.rows if all_groups else [group]
        lines = [f"{task.get('code', '')}  {task.get('project_name', '')}" for group in rows
                 for task in group.get('tasks', []) if not task.get('handed_over') and not task.get('withdrawn')]
        QApplication.clipboard().setText('\n'.join(lines))
        scope = '当前筛选范围' if all_groups else group.get('code', '本组')
        self.note.setText(f'已复制{scope}的 {len(lines)} 项未交清单。')

    def export_group(self):
        if self.worker or self._external_busy:
            return
        group = self._group()
        if group is None:
            self.note.setText('请先选择需要导出的任务组。')
            return
        try:
            from export_ui import choose_workbook_destination
            from delivery_export import export_delivered_projects
            chosen = choose_workbook_destination(self, 'WorkBox任务组_' + group['code'])
            if chosen is None:
                self.note.setText('已取消导出。')
                return
            target, previous = chosen
            self._run(lambda **kwargs: export_delivered_projects(self.groups.db, target, group_id=group['id'],
                replace_existing=previous is not None, expected_target_identity=previous, **kwargs), kind='export')
        except (OSError, ValueError) as error:
            self.note.setText('导出未完成：' + str(error))

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.note.setText('操作正在进行，请完成后再关闭。')
            event.ignore(); return
        super().closeEvent(event)

    def set_busy(self, busy):
        self._external_busy = bool(busy)
        self.setEnabled(not self._external_busy and self.worker is None)

    def shutdown(self):
        self._shutting_down = True
        if self.worker:
            if getattr(self.worker, 'task_operation_kind', '') == 'export':
                self.worker.cancel_event.set()
            if not self.worker.wait(15000):
                self._shutting_down = False
                raise RuntimeError('任务组操作仍未结束，窗口保持打开。')
            self.worker.finished.disconnect(self._finished)
            self._finished()
        return True

    def resume_after_failed_update(self):
        if self.worker is not None:
            raise RuntimeError('任务组操作尚未结束，不能重复恢复界面。')
        self._shutting_down = False
        self.set_busy(False)
        self.refresh()

    def resizeEvent(self, event):
        orientation = Qt.Vertical if self.width() < 700 else Qt.Horizontal
        if self.splitter.orientation() != orientation:
            self.splitter.setOrientation(orientation)
            self.splitter.setSizes([350, 180] if orientation == Qt.Vertical else [600, 360])
        super().resizeEvent(event)
