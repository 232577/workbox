"""项目已有文档的只读异步列表，未变化时保持条目、选择和滚动。"""
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QSignalBlocker, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QAbstractItemView, QHBoxLayout, QHeaderView, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QSizePolicy, QMessageBox

from compact_ui import button, label, local_timestamp, standard_list_scrolling
from native_files import plain_path
from project_documents import list_project_documents
from ui import FileJob, human_size


DOCUMENT_EXTENSIONS = {'.txt', '.md', '.pdf', '.doc', '.docx', '.rtf', '.odt',
    '.xls', '.xlsx', '.csv', '.ppt', '.pptx', '.png', '.jpg', '.jpeg', '.gif',
    '.bmp', '.webp', '.zip', '.7z', '.rar', '.json', '.xml', '.yaml', '.yml', '.log'}


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
        self.hint = label('选择左侧项目，查看对应的文件。', 'subtle')
        self.hint.setMinimumWidth(0)
        self.hint.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Fixed)
        layout.addWidget(self.hint)
        self.table = QTableWidget(0, 3)
        self.table.setObjectName('documentTable')
        self.table.setAccessibleName('项目文档列表')
        self.table.setHorizontalHeaderLabels(['文件名称', '类别', '大小'])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.table.setTextElideMode(Qt.ElideMiddle)
        self.table.setWordWrap(False)
        standard_list_scrolling(self.table)
        self.table.setMinimumHeight(90)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        layout.addWidget(self.table, 1)
        actions = QHBoxLayout()
        actions.setSpacing(4)
        self.open_button = button('打开文件', True)
        self.folder_button = button('所在目录')
        self.refresh_button = button('刷新文档')
        self.open_button.setEnabled(False)
        self.folder_button.setEnabled(False)
        self.refresh_button.setEnabled(False)
        actions.addWidget(self.open_button)
        actions.addWidget(self.folder_button)
        actions.addStretch()
        actions.addWidget(self.refresh_button)
        layout.addLayout(actions)
        self.table.itemSelectionChanged.connect(self.update_actions)
        self.table.itemDoubleClicked.connect(lambda _: self.open_selected())
        self.open_button.clicked.connect(self.open_selected)
        self.folder_button.clicked.connect(self.open_selected_folder)
        self.refresh_button.clicked.connect(self.request_refresh)
        for bar in (self.table.verticalScrollBar(), self.table.horizontalScrollBar()):
            bar.sliderReleased.connect(self._apply_deferred)
        self.timer = QTimer(self)
        self.timer.setInterval(3000)
        self.timer.timeout.connect(self.request_refresh)

    def set_project(self, project_id):
        if project_id == self.project_id:
            self.request_refresh()
            return
        self.project_id = project_id
        self._generation += 1
        self._signature = None
        self._deferred = None
        self.documents = []
        self._read_error = self._result_errors = False
        self.table.setRowCount(0)
        self.refresh_button.setEnabled(project_id is not None)
        self.hint.setText('正在读取项目文件…' if project_id is not None else '选择左侧一个项目，查看对应的文件。')
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
                self.hint.setText('文件列表读取失败，点击“刷新文档”重试。')
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
        self.documents = result['documents']
        new_ids = [row['id'] for row in self.documents]
        versions=sorted(row['zip_id'] for row in self.documents if row.get('zip_id') is not None)
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
                        self.table.setItem(row_index, column, item)
                    if item.text() != value:
                        item.setText(value)
                    item.setToolTip(tooltip)
                    if column == 0:
                        item.setData(Qt.UserRole, record['id'])
            if selected_id in new_ids:
                self.table.selectRow(new_ids.index(selected_id))
            elif old_ids != new_ids:
                self.table.clearSelection()
                self.table.setCurrentCell(-1,-1)
            if anchor_id in new_ids:
                self.table.scrollToItem(self.table.item(new_ids.index(anchor_id),0),QAbstractItemView.PositionAtTop)
                self.table.verticalScrollBar().setValue(self.table.verticalScrollBar().value()-anchor_top)
            else:
                self.table.verticalScrollBar().setValue(vertical)
            self.table.horizontalScrollBar().setValue(horizontal)
        self._show_hint(result)
        self._signature = signature
        self._deferred = None
        self.update_actions()

    def _show_hint(self,result):
        self._read_error = False
        self._result_errors = bool(result['errors'])
        count = len(result['documents'])
        caption = f'{count} 项文件与归档' if count else '这个项目还没有文件，可将文件拖入工作箱。'
        if result['errors']:
            caption = f'{count} 项文件与归档 · 部分位置暂不可读'
        if self.hint.text()!=caption:
            self.hint.setText(caption)
        tooltip='\n'.join(result['errors']) or (result['workspace_path'] or '项目尚未登记工作目录。')
        if self.hint.toolTip()!=tooltip:
            self.hint.setToolTip(tooltip)
        self._sync_layout()

    def set_compact(self,compact):
        self._compact=compact
        self._sync_layout()

    def _sync_layout(self):
        self.layout().setContentsMargins(0, 0 if self._compact else 6, 0, 0)
        show=not self._compact or not self.documents or self._read_error or self._result_errors
        if self.hint.isHidden()==show:
            self.hint.setVisible(show)
        minimum=64 if self._compact and show else 90
        if self.table.minimumHeight()!=minimum:
            self.table.setMinimumHeight(minimum)

    def _apply_deferred(self):
        if self._deferred:
            self.apply_result(self._deferred)

    def current_document(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self.documents) or not self.table.selectedItems():
            return None
        return self.documents[row]

    def update_actions(self):
        record = self.current_document()
        self.open_button.setEnabled(bool(record and record['exists']))
        self.folder_button.setEnabled(bool(record))
        if record and record.get('is_directory'):
            text = '打开归档目录'
        elif record and Path(record['path']).suffix.casefold() not in DOCUMENT_EXTENSIONS:
            text = '在文件夹查看'
        else:
            text = '打开文件'
        self.open_button.setText(text)
        self.open_button.setToolTip('仅根据你的点击打开文档；程序、脚本和未知类型只打开所在文件夹。')

    def open_selected_folder(self):
        record = self.current_document()
        if record:
            self.folder_requested.emit(str(Path(record['path']).parent))

    def open_selected(self):
        record = self.current_document()
        if not record:
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
