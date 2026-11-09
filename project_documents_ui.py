"""项目已有文档的只读异步列表，未变化时保持条目、选择和滚动。"""
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QSignalBlocker, QUrl, QSize, Signal
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import QAbstractItemView, QHBoxLayout, QHeaderView, QMenu, QTableWidgetItem, QVBoxLayout, QWidget, QSizePolicy, QMessageBox
from qfluentwidgets import FluentIcon

from compact_ui import button, label, local_timestamp
from fluent_views import ITEM_TEXT_TONE_ROLE, WorkbenchTableWidget
from native_files import plain_path
from project_documents import list_project_documents
from theme import theme_signal, tokens
from ui import FileJob, human_size


DOCUMENT_EXTENSIONS = {'.txt', '.md', '.pdf', '.doc', '.docx', '.rtf', '.odt',
    '.xls', '.xlsx', '.csv', '.ppt', '.pptx', '.png', '.jpg', '.jpeg', '.gif',
    '.bmp', '.webp', '.zip', '.7z', '.rar', '.json', '.xml', '.yaml', '.yml', '.log'}


class DocumentTable(WorkbenchTableWidget):
    row_height = 36
    open_requested = Signal()
    width_about_to_change = Signal()
    width_changed = Signal()

    def keyPressEvent(self, event):
        if (event.key() in (Qt.Key_Return, Qt.Key_Enter)
                and event.modifiers() in (Qt.NoModifier, Qt.KeypadModifier)):
            event.accept()
            if not event.isAutoRepeat():
                self.open_requested.emit()
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event):
        changed = event.oldSize().width() != event.size().width()
        if changed:
            self.width_about_to_change.emit()
        super().resizeEvent(event)
        if changed:
            self.width_changed.emit()


class ProjectDocumentsPane(QWidget):
    folder_requested = Signal(str)

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.project_id = None
        self.worker = None
        self.documents = []
        self._generation = 0
        self._pending = False
        self._closed = False
        self._compact = False
        self._read_error = False
        self._result_errors = False
        self._signature = None
        self._deferred = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 0)
        layout.setSpacing(4)
        tools = QHBoxLayout()
        tools.setSpacing(8)
        tools.addWidget(label('项目文件', 'sectionTitle'))
        self.file_count = label('', 'subtle')
        self.file_count.hide()
        tools.addWidget(self.file_count)
        tools.addStretch()
        self.open_button = button('打开文件')
        self.open_button.setEnabled(False)
        self.open_button.hide()
        self.file_menu_button = button('文件操作')
        self.file_menu_button.setObjectName('documentActionsButton')
        self.file_menu_button.setAccessibleName('更多文件操作')
        self.file_menu_button.setToolTip('定位所选文件或刷新项目文件。')
        self.file_menu = QMenu(self.file_menu_button)
        self.file_menu.setToolTipsVisible(True)
        self.file_menu_button.setMenu(self.file_menu)
        self.folder_action = self.file_menu.addAction('所在目录', self.open_selected_folder)
        self.folder_action.setToolTip('打开所选文件或归档的上一级目录。')
        self.file_menu.addAction('刷新文档', self.request_refresh)
        tools.addWidget(self.open_button)
        tools.addWidget(self.file_menu_button)
        layout.addLayout(tools)

        feedback = QHBoxLayout()
        feedback.setSpacing(8)
        self.hint = label('选择项目以查看文件。', 'subtle', True)
        self.hint.setMinimumWidth(0)
        self.hint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        feedback.addWidget(self.hint, 1)
        self.refresh_button = button('重试')
        self.refresh_button.setObjectName('retryDocumentsButton')
        self.refresh_button.setAccessibleName('重试读取项目文件')
        feedback.addWidget(self.refresh_button)
        layout.addLayout(feedback)
        for control in (self.open_button, self.file_menu_button, self.refresh_button):
            control.setProperty('workboxRole', 'secondary')

        self.table = DocumentTable(0, 3)
        self.table.setObjectName('documentTable')
        self.table.setAccessibleName('项目文档列表')
        self.table.setHorizontalHeaderLabels(['文件名称', '类别', '大小'])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.table.setTextElideMode(Qt.ElideNone)
        self.table.setWordWrap(True)
        self.table.setIconSize(QSize(16, 16))
        self.table.setShowGrid(False)
        self.table.setColumnTypography(muted={1, 2})
        self.table.setWrapColumns({0})
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(DocumentTable.row_height)
        self.table.verticalHeader().setMinimumSectionSize(DocumentTable.row_height)
        header = self.table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        header.setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeaderItem(2).setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.table.setColumnWidth(1, 94)
        self.table.setColumnWidth(2, 88)
        self._manual_columns = False
        self._fitting_columns = False
        header.sectionResized.connect(self._column_resized)
        self.table.width_changed.connect(self._fit_columns)
        self._row_layout_timer = QTimer(self)
        self._row_layout_timer.setSingleShot(True)
        self._row_layout_anchor = None
        self._row_layout_timer.timeout.connect(self._resize_rows_preserving_anchor)
        self.table.width_about_to_change.connect(self._queue_row_layout)
        self.table.horizontalHeader().sectionResized.connect(self._queue_row_layout)
        layout.addWidget(self.table, 1)
        # 只有表格隐藏时才吸收多余高度，空态提示仍紧挨标题。
        layout.addStretch(0)
        self.table.itemSelectionChanged.connect(self.update_actions)
        self.table.itemDoubleClicked.connect(lambda _: self.open_selected())
        self.table.open_requested.connect(self.open_selected)
        self.open_button.clicked.connect(self.open_selected)
        self.refresh_button.clicked.connect(self.request_refresh)
        for bar in (self.table.verticalScrollBar(), self.table.horizontalScrollBar()):
            bar.sliderReleased.connect(self._apply_deferred)
        self.timer = QTimer(self)
        self.timer.setInterval(3000)
        self.timer.timeout.connect(self.request_refresh)
        theme_signal().connect(self._refresh_document_icons)
        self._sync_layout()

    def _document_icons(self):
        color = QColor(tokens()['muted'])
        return {False: FluentIcon.DOCUMENT.icon(color=color),
                True: FluentIcon.FOLDER.icon(color=color)}

    def _refresh_document_icons(self, *_):
        icons = self._document_icons()
        with QSignalBlocker(self.table):
            for row, record in enumerate(self.documents):
                item = self.table.item(row, 0)
                if item is not None:
                    item.setIcon(icons[bool(record.get('is_directory'))])

    def _column_resized(self, *_):
        if not self._fitting_columns and self.table.isVisible():
            self._manual_columns = True

    def _fit_columns(self):
        if self._manual_columns:
            return
        width = max(120, self.table.viewport().width()
                    - self.table.columnWidth(1) - self.table.columnWidth(2))
        if self.table.columnWidth(0) != width:
            self._fitting_columns = True
            try:
                self.table.setColumnWidth(0, width)
            finally:
                self._fitting_columns = False

    def set_project(self, project_id):
        if project_id == self.project_id:
            self.request_refresh()
            return
        self.project_id = project_id
        self._generation += 1
        self._signature = None
        self._deferred = None
        self._row_layout_timer.stop()
        self._row_layout_anchor = None
        self.documents = []
        self._read_error = self._result_errors = False
        self.table.setRowCount(0)
        self.file_count.hide()
        self.table.horizontalHeaderItem(0).setToolTip('')
        self.table.setAccessibleDescription('')
        self.hint.setText('正在读取项目文件…' if project_id is not None else '选择项目以查看文件。')
        self.hint.setToolTip('')
        self.update_actions()
        self._sync_layout()
        if self.worker:
            self.worker.cancel_event.set()
        self._pending = project_id is not None
        self.request_refresh()

    def request_refresh(self):
        if self._closed or self.project_id is None or not self.isVisible():
            return
        if self.worker:
            self._pending = True
            return
        self._pending = False
        pid, generation = self.project_id, self._generation
        self.worker = FileJob(lambda **kw: list_project_documents(
            self.service, pid, cancel_event=kw.get('cancel_event')), self)
        self.worker._document_context = (pid, generation)
        self.worker.finished.connect(self._finished)
        self.worker.start()

    def _finished(self):
        worker = self.sender()
        if worker is None or worker is not self.worker:
            return
        self.worker = None
        if not self._closed and worker._document_context == (self.project_id, self._generation) and not worker.cancelled:
            if worker.error:
                self._read_error = True
                self._deferred = None
                count = len(self.documents)
                if count:
                    self._show_file_count(f'上次读取 {count} 项', f'上次读取到 {count} 项文件与归档，当前总数未知。')
                    self.hint.setText('文件列表读取失败，当前显示上次读取结果。')
                else:
                    self._show_file_count('总数未知', '文件与归档总数未知。')
                    self.hint.setText('文件列表读取失败。')
                self.hint.setToolTip(worker.error)
                self._sync_layout()
            else:
                self.apply_result(worker.result)
        worker.deleteLater()
        if self._pending and not self._closed:
            self.request_refresh()

    def apply_result(self, result):
        if result['project_id'] != self.project_id:
            return
        signature = (result['workspace_path'], tuple(result['errors']),
                     tuple(tuple(sorted(row.items())) for row in result['documents']))
        if signature == self._signature:
            self._deferred = None
            self._show_hint(result)
            return
        if any(bar.isSliderDown() for bar in (self.table.verticalScrollBar(), self.table.horizontalScrollBar())):
            self._deferred = result
            return
        selected = self.current_document()
        selected_id = selected['id'] if selected else None
        old_ids = [row['id'] for row in self.documents]
        anchor_row = self.table.rowAt(0)
        anchor_id = self.documents[anchor_row]['id'] if 0 <= anchor_row < len(self.documents) else None
        anchor_top = self.table.rowViewportPosition(anchor_row) if anchor_id else 0
        vertical, horizontal = self.table.verticalScrollBar().value(), self.table.horizontalScrollBar().value()
        # 本次刷新会同步测量全部行，取代更早排队的列宽布局。
        # 表头在重建时也可能发出尺寸信号，不能让它保存临时/过期锚点。
        self._row_layout_timer.stop()
        self._row_layout_anchor = (anchor_id, anchor_top)
        self.documents = result['documents']
        new_ids = [row['id'] for row in self.documents]
        versions=sorted(row['zip_id'] for row in self.documents if row.get('zip_id') is not None)
        icons = self._document_icons()
        with QSignalBlocker(self.table):
            if old_ids != new_ids:
                self.table.setRowCount(0)
                self.table.setRowCount(len(self.documents))
            for row_index, record in enumerate(self.documents):
                name=record['relative_path']
                if record.get('is_directory') and record['kind']=='材料归档':
                    name='交付材料 · '+local_timestamp(record.get('archived_at') or record.get('modified_at'))
                elif record['kind']=='ZIP归档':
                    version=versions.index(record['zip_id'])+1
                    name=f'V{version} · '+name+('（最新）' if record['zip_id']==versions[-1] else '')
                values = [name, record['kind'] if record['exists'] else '文件缺失',
                          '—' if record['size'] is None else human_size(record['size'])]
                tooltip = record['path'] + '\n' + record['kind']
                if record.get('modified_at'):
                    tooltip += '\n修改时间：' + local_timestamp(record['modified_at'])
                if not record['exists']:
                    tooltip += '\n文件已移动或删除，请检查所在目录。'
                for column, value in enumerate(values):
                    item = self.table.item(row_index, column)
                    if item is None:
                        item = QTableWidgetItem()
                        item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                        if column == 2:
                            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                        self.table.setItem(row_index, column, item)
                    if item.text() != value:
                        item.setText(value)
                    item.setToolTip(tooltip)
                    if column == 0:
                        item.setData(Qt.UserRole, record['id'])
                        item.setIcon(icons[bool(record.get('is_directory'))])
                    elif column == 1:
                        item.setData(ITEM_TEXT_TONE_ROLE, None if record['exists'] else 'error')
            if selected_id in new_ids:
                self.table.selectRow(new_ids.index(selected_id))
            elif old_ids != new_ids:
                self.table.clearSelection()
                self.table.setCurrentCell(-1,-1)
            # 首次出现的表格也按实际视口宽度测量，避免先用隐藏时的列宽拉高长文件名。
            self._show_hint(result)
            self.layout().activate()
            self._fit_columns()
            self.table.resizeRowsToContents()
            self.table.updateGeometries()
            if anchor_id in new_ids:
                self.table.scrollToItem(self.table.item(new_ids.index(anchor_id),0),QAbstractItemView.PositionAtTop)
                self.table.verticalScrollBar().setValue(self.table.verticalScrollBar().value()-anchor_top)
            else:
                self.table.verticalScrollBar().setValue(vertical)
            self.table.horizontalScrollBar().setValue(horizontal)
        self._row_layout_timer.stop()
        self._row_layout_anchor = None
        self._signature = signature
        self._deferred = None
        self.update_actions()

    def _show_hint(self,result):
        self._read_error = False
        self._result_errors = bool(result['errors'])
        count = len(result['documents'])
        if self._result_errors:
            count_label = f'已读取 {count} 项' if count else '总数未知'
            description = f'已读取 {count} 项文件与归档，总数未知。' if count else '文件与归档总数未知。'
            self._show_file_count(count_label, description)
            caption = '文件列表不完整。'
        else:
            self._show_file_count(f'{count} 项', f'{count} 项文件与归档')
            caption = f'{count} 项文件与归档' if count else '这个项目还没有文件，可将文件拖入工作箱。'
        if self.hint.text()!=caption:
            self.hint.setText(caption)
        tooltip='\n'.join(result['errors']) or (result['workspace_path'] or '项目尚未登记工作目录。')
        if self.hint.toolTip()!=tooltip:
            self.hint.setToolTip(tooltip)
        self.table.horizontalHeaderItem(0).setToolTip(tooltip)
        self._sync_layout()

    def _show_file_count(self, text, description):
        self.file_count.setText(text)
        self.file_count.setToolTip(description)
        self.file_count.show()
        self.table.setAccessibleDescription(description)

    def set_compact(self,compact):
        self._compact=compact
        self._sync_layout()

    def _sync_layout(self):
        self.layout().setContentsMargins(0, 0 if self._compact else 6, 0, 0)
        has_project = self.project_id is not None
        retry = self._read_error or self._result_errors
        self.hint.setVisible(not self.documents or retry)
        self.table.setVisible(bool(self.documents))
        self.file_menu_button.setVisible(has_project and (bool(self.documents) or not retry))
        self.refresh_button.setVisible(has_project and retry)
        # Fluent 表头叠加产品字体后可能高于原 Qt 表头；至少保留一行文档的视口。
        header = self.table.horizontalHeader()
        minimum = (max(header.height(), header.sizeHint().height()) + DocumentTable.row_height
                   + 2 * self.table.frameWidth())
        if self.table.minimumHeight() != minimum:
            self.table.setMinimumHeight(minimum)

    def _apply_deferred(self):
        if self._deferred:
            self.apply_result(self._deferred)

    def _resize_rows_preserving_anchor(self):
        anchor = self._row_layout_anchor
        self._row_layout_anchor = None
        if anchor is None:
            anchor = self._document_anchor()
        anchor_id, anchor_top = anchor
        self.table.resizeRowsToContents()
        self.table.updateGeometries()
        if anchor_id is not None and not self.table.verticalScrollBar().isSliderDown():
            row = next((i for i in range(self.table.rowCount())
                        if self.table.item(i, 0).data(Qt.UserRole) == anchor_id), None)
            if row is not None:
                self.table.scrollToItem(self.table.item(row, 0), QAbstractItemView.PositionAtTop)
                offset = max(anchor_top, 1 - self.table.rowHeight(row))
                bar = self.table.verticalScrollBar()
                bar.setValue(bar.value() - offset)

    def _document_anchor(self):
        row = self.table.rowAt(0)
        item = self.table.item(row, 0) if row >= 0 else None
        anchor_id = item.data(Qt.UserRole) if item else None
        anchor_top = self.table.rowViewportPosition(row) if item else 0
        return anchor_id, anchor_top

    def _queue_row_layout(self, *_):
        if self._row_layout_anchor is None:
            self._row_layout_anchor = self._document_anchor()
        # 连续拖动分栏时只在停顿后测量一次，不逐次阻塞界面。
        self._row_layout_timer.start(80)

    def current_document(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self.documents) or not self.table.selectedItems():
            return None
        return self.documents[row]

    def update_actions(self):
        record = self.current_document()
        self.open_button.setVisible(record is not None)
        self.open_button.setEnabled(bool(record and record['exists']))
        self.folder_action.setEnabled(bool(record))
        if record and record.get('is_directory'):
            text = '打开归档目录'
            tooltip = '打开所选归档目录。'
        elif record and Path(record['path']).suffix.casefold() not in DOCUMENT_EXTENSIONS:
            text = '在文件夹查看'
            tooltip = '打开所在目录，便于查找该文件。'
        else:
            text = '打开文件'
            tooltip = '使用系统关联的应用打开所选文件。'
        self.open_button.setText(text)
        self.open_button.setToolTip(tooltip if not record or record['exists'] else
                                    '文件已移动或删除。可查看所在目录或刷新文档。')

    def open_selected_folder(self):
        record = self.current_document()
        if record:
            self.folder_requested.emit(str(Path(record['path']).parent))

    def open_selected(self):
        record = self.current_document()
        if not record or not record['exists']:
            return
        try:
            path = plain_path(record['path'])
            if not path.exists():
                raise FileNotFoundError('文件已移动或删除，请刷新列表。')
            if path.is_dir():
                self.folder_requested.emit(str(path))
            elif path.suffix.casefold() not in DOCUMENT_EXTENSIONS:
                self.folder_requested.emit(str(path.parent))
            elif not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
                raise OSError('无法打开文件，请检查是否已安装对应的阅读软件。')
        except (OSError, ValueError) as error:
            self.hint.setText(str(error))
            self.hint.setToolTip(str(error))
            QMessageBox.warning(self,'无法打开文件',str(error))

    def _stop_reader(self):
        self.timer.stop()
        if self.worker:
            self.worker.cancel_event.set()
            if not self.worker.wait(15000):
                raise RuntimeError('文件列表读取尚未结束，请稍后重试退出。')
            self.worker.finished.disconnect(self._finished)
            self.worker.deleteLater()
            self.worker = None
        self._pending = self.project_id is not None

    def showEvent(self, event):
        super().showEvent(event)
        if not self._closed:
            self.timer.start()
            self.request_refresh()

    def hideEvent(self, event):
        self._stop_reader()
        super().hideEvent(event)

    def shutdown(self):
        self._closed = True
        self._stop_reader()

    def resume_after_failed_update(self):
        """Restore reads after upgrade preparation failed without releasing the data lock."""
        self._closed = False
        if self.isVisible():
            self.timer.start()
            self.request_refresh()
