"""桌面收件与项目查看。网页读取和交件由调用方处理。"""

from pathlib import Path

from PySide6.QtCore import QEvent, QSignalBlocker, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QFileDialog, QFrame, QGridLayout, QHeaderView, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from workbox.team_delivery.contracts import MATERIAL_PREPARATION_STAGES, DropResult, Outcome, Snapshot, Stage, Task


_COPY = Qt.DropAction.CopyAction
_WAITING = '等待文件'
_AWAITING_WEB = '待网页提交'


def _label(text='', *, name='', heading=False, secondary=False):
    label = QLabel(text)
    label.setObjectName(name)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setProperty('heading', heading)
    if heading:
        font = label.font()
        font.setBold(True)
        label.setFont(font)
    label.setProperty('secondary', secondary)
    return label


def _local_path(filename: str) -> Path | None:
    path = Path(filename)
    if filename.startswith(('//', '\\\\')) or not path.is_absolute():
        return None
    return path


def _is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


class _FileInbox(QWidget):
    received = Signal(str)
    rejected = Signal(str)

    def __init__(self, flags=Qt.WindowType.Widget):
        super().__init__(None, flags)
        self.busy = False
        self.intake_block = ''
        self.setAcceptDrops(True)

    def _drop_path(self, event):
        if self.busy:
            return None, '正在处理，请稍后再试。'
        if self.intake_block:
            return None, self.intake_block
        urls = event.mimeData().urls()
        if not urls:
            return None, '请选择本机保存的文件。'
        if len(urls) != 1:
            return None, '一次只收一件文件。'
        url = urls[0]
        if not url.isLocalFile() or url.host() or url.hasQuery() or url.hasFragment():
            return None, '请选择本机保存的文件。'
        path = _local_path(url.toLocalFile())
        if path is None:
            return None, '请选择本机保存的文件。'
        if not event.possibleActions() & _COPY:
            return None, '请用复制方式拖入文件，原件保留在原处。'
        return path, ''

    def dragEnterEvent(self, event):
        path, _ = self._drop_path(event)
        if path is None:
            event.ignore()
        else:
            event.setDropAction(_COPY)
            event.accept()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dropEvent(self, event):
        path, message = self._drop_path(event)
        if path is not None:
            if _is_file(path):
                event.setDropAction(_COPY)
                event.accept()
                self.received.emit(str(path))
                return
            message = '找不到本机文件，请确认原件已保存。'
        event.ignore()
        if not self.busy and not self.intake_block:
            self.rejected.emit(message)


class DesktopWindow(_FileInbox):
    """工作箱内的团队交件页面。"""

    refresh_requested = Signal()
    personal_sync_requested = Signal()
    personal_binding_requested = Signal(object)
    login_requested = Signal()
    workbox_login_requested = Signal()
    office_address_requested = Signal()
    office_close_requested = Signal()
    workbox_pair_requested = Signal()
    select_root_requested = Signal()
    reveal_task_requested = Signal()
    manual_upload_requested = Signal()
    file_dropped = Signal(str, object)
    shown = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName('desktopWindow')
        self.setWindowTitle('Online 交件助手')
        self.setMinimumSize(580, 440)
        self.resize(780, 550)
        self._personal_path = None
        self._live = False
        self._personal_mode = False
        self._unlinked_tasks = frozenset()
        self._personal_notice_timer = QTimer(self, singleShot=True, interval=6500)
        self._personal_notice_timer.timeout.connect(self._clear_personal_notice)
        self._read_error = ''
        self._deferred_read = ''
        self._waiting_for_snapshot = False
        self._personal_sync_pending = False
        self._personal_sync_message = ''
        self._workbox_username = ''
        self._workbox_error = ''
        self._accounts_confirmed = False
        self._snapshot = Snapshot(None)
        self._result_title = _WAITING
        self._result_info = ''
        self._prepared_path = ''
        self._prepared_task: Task | None = None
        self._site_completed = False
        self._intake_revision = 0
        self._request_revision: int | None = None
        self._setup_panel()
        self.received.connect(self._receive_file)
        self.rejected.connect(self._show_rejection)
        self.set_snapshot(Snapshot(None))

    def _setup_panel(self):
        identity = QGridLayout()
        identity.setVerticalSpacing(7)
        self._heading = _label('材料交件', heading=True)
        identity.addWidget(self._heading, 0, 0, 1, 2)
        self._refresh = QPushButton('刷新任务')
        self._refresh.clicked.connect(self.refresh_requested)
        self._personal_sync = QPushButton('同步到个人项目', objectName='syncPersonalProjects')
        self._personal_sync.setToolTip('同步当前账号的全部材料待办。在 WorkBox 的个人项目中管理进度。')
        self._personal_sync.clicked.connect(self.personal_sync_requested)
        self._personal_sync.hide()
        task_controls = QHBoxLayout()
        task_controls.setSpacing(6)
        task_controls.addWidget(self._refresh)
        task_controls.addWidget(self._personal_sync)
        identity.addLayout(task_controls, 0, 2)
        self._account = _label(name='account', secondary=True)
        self._account.setWordWrap(True)
        identity.addWidget(self._account, 1, 0)
        identity.setColumnStretch(0, 1)
        self._login = QPushButton('查看演示网页', objectName='openLogin')
        self._login.clicked.connect(self.login_requested)
        identity.addWidget(self._login, 1, 1)

        self._workbox_account = _label('WorkBox Online · 未连接', name='workboxAccount', secondary=True)
        self._workbox_account.setWordWrap(True)
        identity.addWidget(self._workbox_account, 2, 0)
        self._workbox_login = QPushButton('团队办理', objectName='openWorkBoxLogin')
        self._workbox_login.setToolTip('分发、领取、上传材料和下载提交包都在这里。')
        self._workbox_login.clicked.connect(self.workbox_login_requested)
        identity.addWidget(self._workbox_login, 2, 1)
        self._confirm_accounts = QPushButton('确认两个账号', objectName='confirmAccounts')
        self._confirm_accounts.clicked.connect(self.workbox_pair_requested)
        identity.addWidget(self._confirm_accounts, 2, 2)
        self._workbox_account.hide()
        self._workbox_login.hide()
        self._confirm_accounts.hide()

        self._sync_notice = _label(name='syncNotice', secondary=True)
        self._sync_notice.setWordWrap(True)

        self._directory_row = QWidget()
        directory = QHBoxLayout(self._directory_row)
        directory.setContentsMargins(0, 0, 0, 0)
        directory.addWidget(_label('手册留底目录'))
        self._directory = QLineEdit(objectName='sharedRoot')
        self._directory.setAccessibleName('交件目录')
        self._directory.setReadOnly(True)
        self._directory.setAcceptDrops(False)
        self._directory.setPlaceholderText('尚未选择交件目录')
        directory.addWidget(self._directory, 1)
        self._select_root = QPushButton('选择目录…', objectName='selectRoot')
        self._select_root.clicked.connect(self.select_root_requested)
        directory.addWidget(self._select_root)
        self._directory_row.hide()

        drop_area = QFrame(objectName='dropArea')
        intake = QHBoxLayout(drop_area)
        intake.setContentsMargins(18, 14, 16, 14)
        instructions = QVBoxLayout()
        instructions.addWidget(_label('拖入同名 Word', heading=True))
        self._hint = _label('一次一件，按你当前的任务匹配。', secondary=True)
        self._hint.setWordWrap(True)
        instructions.addWidget(self._hint)
        intake.addLayout(instructions, 1)
        self._choose = QPushButton('选择文件…', objectName='chooseFile')
        self._choose.clicked.connect(self._choose_file)
        self._open_shortcut = QShortcut(QKeySequence(QKeySequence.StandardKey.Open), self)
        self._open_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._open_shortcut.activated.connect(self._choose_file)
        intake.addWidget(self._choose)

        task_actions = QHBoxLayout()
        task_actions.addWidget(_label('我的任务'))
        self._empty = _label('暂无任务', secondary=True)
        task_actions.addWidget(self._empty)
        self._search = QLineEdit(objectName='projectSearch')
        self._search.setPlaceholderText('搜索项目名称或编号')
        self._search.setAccessibleName('搜索项目名称或编号')
        self._search.setClearButtonEnabled(True)
        self._search.setAcceptDrops(False)
        self._search.textChanged.connect(lambda: self._render_tasks(self._selected_task()))
        self._search.installEventFilter(self)
        task_actions.addWidget(self._search, 1)
        self._bind_task = QPushButton('关联本机项目…', objectName='linkPersonalProject')
        self._bind_task.clicked.connect(lambda: self.personal_binding_requested.emit(self._selected_task()))
        self._bind_task.hide()
        task_actions.addWidget(self._bind_task)

        self._tasks = QTableWidget(0, 3, objectName='tasks')
        self._tasks.setAccessibleName('我的材料任务')
        self._tasks.setHorizontalHeaderLabels(['项目名称', '任务编号', '原站状态'])
        self._tasks.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._tasks.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._tasks.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._tasks.setAcceptDrops(False)
        self._tasks.viewport().setAcceptDrops(False)
        self._tasks.setAlternatingRowColors(True)
        self._tasks.verticalHeader().hide()
        header = self._tasks.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._tasks.itemSelectionChanged.connect(self._update_controls)

        self._result = _label(name='lastResult')
        self._result.setWordWrap(True)
        self._result.setMinimumHeight(46)
        self._result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self._saved_path_row = QWidget()
        saved_path = QHBoxLayout(self._saved_path_row)
        saved_path.setContentsMargins(0, 0, 0, 0)
        self._saved_path = QLineEdit(objectName='savedPath')
        self._saved_path.setAccessibleName('原件保存路径')
        self._saved_path.setReadOnly(True)
        self._saved_path.setAcceptDrops(False)
        saved_path.addWidget(self._saved_path, 1)
        self._handoff_row = QWidget()
        handoff = QHBoxLayout(self._handoff_row)
        handoff.setContentsMargins(0, 0, 0, 0)
        self._upload_manual = QPushButton('查看办公室交接', objectName='workboxManualUpload')
        self._upload_manual.setToolTip('先在 WorkBox 窗口打开该项目的手册上传页。')
        self._upload_manual.clicked.connect(self.manual_upload_requested)
        handoff.addWidget(self._upload_manual)
        self._reveal_task = QPushButton('定位原站任务', objectName='revealTask')
        self._reveal_task.clicked.connect(self.reveal_task_requested)
        handoff.addWidget(self._reveal_task)
        handoff.addStretch(1)
        self._handoff_row.hide()
        copy_path = QPushButton('复制路径', objectName='copySavedPath')
        copy_path.clicked.connect(self._copy_saved_path)
        saved_path.addWidget(copy_path)
        open_directory = QPushButton('打开原件目录', objectName='openSavedDirectory')
        open_directory.clicked.connect(self._open_saved_directory)
        saved_path.addWidget(open_directory)
        self._saved_path_row.hide()

        self._pages = QStackedWidget(self)
        inbox = QWidget(self._pages)
        self._pages.addWidget(inbox)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._pages)
        layout = QVBoxLayout(inbox)
        layout.setContentsMargins(22, 18, 22, 14)
        layout.setSpacing(12)
        layout.addLayout(identity)
        layout.addWidget(self._sync_notice)
        layout.addWidget(self._directory_row)
        layout.addWidget(drop_area)
        self.personal_row = QWidget()
        personal = QHBoxLayout(self.personal_row)
        personal.setContentsMargins(0, 0, 0, 0)
        self.pending_document = _label(name='pendingPersonalDocument')
        self.pending_document.setWordWrap(True)
        self.use_document = QPushButton('使用这个手册', objectName='usePersonalDocument')
        self.use_document.clicked.connect(self._use_personal_document)
        personal.addWidget(self.pending_document, 1)
        personal.addWidget(self.use_document)
        layout.addWidget(self.personal_row)
        self.personal_row.hide()
        layout.addLayout(task_actions)
        layout.addWidget(self._tasks, 1)
        layout.addWidget(self._result)
        layout.addWidget(self._handoff_row)
        layout.addWidget(self._saved_path_row)

    def show_office(self):
        if self._pages.count() == 1:
            from workbox.team_delivery.office_panel import OfficePanel
            area = QWidget()
            layout = QVBoxLayout(area)
            layout.setContentsMargins(6, 4, 6, 0)
            buttons = QHBoxLayout()
            back = QPushButton('返回我的待办', objectName='officeBackToTasks')
            back.clicked.connect(lambda: self._pages.setCurrentIndex(0))
            buttons.addWidget(back)
            buttons.addStretch(1)
            server = QPushButton('更换服务器')
            server.clicked.connect(self.office_address_requested)
            buttons.addWidget(server)
            close = QPushButton('关闭团队网页', objectName='closeOfficeSession')
            close.setToolTip('结束本机的办公室会话，回到「这是家」待办。')
            close.clicked.connect(self.office_close_requested)
            buttons.addWidget(close)
            layout.addLayout(buttons)
            self.office_panel = OfficePanel(area)
            layout.addWidget(self.office_panel, 1)
            self._pages.addWidget(area)
        self._pages.setCurrentIndex(1)
        return self.office_panel

    def show_tasks(self):
        self._pages.setCurrentIndex(0)

    def eventFilter(self, watched, event):
        if (watched is self._search and event.type() == QEvent.Type.KeyPress
                and event.key() == Qt.Key.Key_Escape and self._search.text()):
            self._search.clear()
            return True
        return super().eventFilter(watched, event)

    def showEvent(self, event):
        super().showEvent(event)
        self.shown.emit()

    def _identity(self):
        account = self._snapshot.account
        return (account.id, account.role, account.session) if account else None

    def _clear_preparation(self):
        self._prepared_path = ''
        self._prepared_task = None
        self._site_completed = False

    def _reset_intake(self):
        self._personal_notice_timer.stop()
        # Previously requested results remain stale even if the same accounts reconnect.
        self._intake_revision += 1
        self._clear_preparation()
        self._result_title, self._result_info = _WAITING, ''

    def _account_caption(self):
        account = self._snapshot.account
        if not account:
            return '这是家 · 未连接' if self._live else '尚未登录'
        if not self._live:
            return account.name
        role = {'material': '材料人员', 'code': '代码人员'}.get(account.role, '只读')
        return f'这是家 · {account.name} · {role}'

    def _has_material_account(self):
        account = self._snapshot.account
        return bool(account and account.role == 'material' and not self._read_error
                    and not self._waiting_for_snapshot)

    def set_workbox_account(self, username: str, matched: bool, message: str) -> None:
        """显示当前账号和配对核验结果。"""
        if not self._live:
            return
        confirmed = bool(username and matched and not message and self._has_material_account())
        if username != self._workbox_username or (self._accounts_confirmed and not confirmed):
            self._reset_intake()
        self._workbox_username = username
        self._workbox_error = message
        self._accounts_confirmed = confirmed
        caption = username or ('无法读取账号' if message else '未连接')
        self._workbox_account.setText(f'WorkBox Online · {caption}')
        self._update_controls()

    def configure_live(self, shared_root: str | None) -> None:
        """显示交件准备模式。目录由调用方选择和保存。"""
        if not self._live:
            self._live = True
            self._reset_intake()
            self.setWindowTitle('团队交件')
            self._heading.setText(self.windowTitle())
            self._login.setText('打开这是家/登录')
            self._directory_row.show()
            self._workbox_account.show()
            self._workbox_login.show()
            self._confirm_accounts.show()
            self._account.setText('这是家 · 任务暂未同步' if self._read_error else self._account_caption())
        path = shared_root or ''
        if path != self._directory.text():
            self._reset_intake()
        self._directory.setText(path)
        self._directory.setCursorPosition(0)
        self._directory.setToolTip(path)
        self._update_intake_hint()
        self._update_controls()

    def set_personal_mode(self, enabled: bool):
        if self._personal_mode == enabled:
            return
        self._personal_mode = enabled
        self._reset_intake()
        self._directory_row.setVisible(self._live and not enabled)
        self._confirm_accounts.setVisible(self._live and not enabled)
        self._update_intake_hint()
        self._update_controls()

    def _update_intake_hint(self):
        if self._personal_mode:
            self._hint.setText('拖入与项目同名的 Word，收好后变为「待交付」。在项目里确认交付后，'
                               '工作箱会到「这是家」提交该任务。')
        elif self._directory.text():
            self._hint.setText('拖入成品 Word，工具箱会先向办公室交接手册，再到「这是家」提交材料状态。')
        else:
            self._hint.setText('请先选择交件目录，再拖入 Word。')

    def show_personal_result(self, title: str, detail: str = '', *, temporary=False):
        if self._personal_mode:
            self._personal_notice_timer.stop()
            self._result_title, self._result_info = title, detail
            self._update_controls()
            if temporary:
                self._personal_notice_timer.start()

    def _clear_personal_notice(self):
        self._result_title, self._result_info = _WAITING, ''
        self._update_controls()

    def set_unlinked_tasks(self, tasks):
        self._unlinked_tasks = frozenset(task.id for task in tasks)
        self._update_controls()

    def enable_personal_sync(self) -> None:
        self._personal_sync.show()
        self._update_controls()

    def set_personal_sync_status(self, message: str, *, pending: bool) -> None:
        self._personal_sync_message = message
        self._personal_sync_pending = pending
        self._update_controls()

    def _eligible(self, task: Task | None) -> bool:
        account = self._snapshot.account
        return bool(task and account and account.role == 'material' and task.owner_id == account.id
                    and task.stage in MATERIAL_PREPARATION_STAGES)

    def _selected_task(self):
        if not self._tasks.selectedItems():
            return None
        item = self._tasks.item(self._tasks.currentRow(), 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _own_tasks(self):
        account = self._snapshot.account
        return [task for task in self._snapshot.tasks if account and task.owner_id == account.id
                and task.stage != Stage.COMPLETED]

    def set_snapshot(self, snapshot: Snapshot):
        same_snapshot = self._snapshot == snapshot and not self._waiting_for_snapshot
        previous = self._identity()
        selected = self._selected_task()
        self._snapshot = snapshot
        self._read_error = ''
        self._deferred_read = ''
        self._waiting_for_snapshot = False
        same_account = previous == self._identity()
        if not same_account:
            self._accounts_confirmed = False
            self._reset_intake()
            if not self._personal_sync_pending:
                self._personal_sync_message = ''
            with QSignalBlocker(self._search):
                self._search.clear()
        prepared = self._prepared_task
        if prepared is not None:
            self._site_completed = any(
                task.stage == Stage.COMPLETED and (task.id, task.title, task.owner_id)
                == (prepared.id, prepared.title, prepared.owner_id)
                for task in snapshot.tasks
            )
        self._account.setText(self._account_caption())
        self._account.setToolTip('')
        if same_snapshot:
            self._update_controls()
        else:
            self._render_tasks(selected if same_account else None, preserve_view=same_account)

    def _render_tasks(self, selected: Task | None, *, preserve_view: bool = False):
        vertical = self._tasks.verticalScrollBar()
        horizontal = self._tasks.horizontalScrollBar()
        position = (vertical.value(), horizontal.value())
        all_tasks = self._own_tasks()
        query = self._search.text().strip().casefold()
        if query.startswith('#') and len(query) > 1:
            tasks = [task for task in all_tasks if query[1:] == task.id.casefold()]
        else:
            tasks = [task for task in all_tasks if query in task.title.casefold() or query in task.id.casefold()]
        if self._waiting_for_snapshot:
            self._empty.setText('等待同步')
        elif query:
            self._empty.setText(f'{len(tasks)} / {len(all_tasks)} 项' if tasks else '未找到项目')
        else:
            self._empty.setText(f'{len(tasks)} 项' if tasks else '暂无任务')
        with QSignalBlocker(self._tasks):
            self._tasks.clearContents()
            self._tasks.clearSelection()
            self._tasks.setRowCount(len(tasks))
            for row, task in enumerate(tasks):
                for column, value in enumerate((task.title, task.id, task.stage.value)):
                    item = QTableWidgetItem(value)
                    item.setToolTip(value)
                    self._tasks.setItem(row, column, item)
                self._tasks.item(row, 0).setData(Qt.ItemDataRole.UserRole, task)
                if selected and selected.id == task.id:
                    self._tasks.selectRow(row)
        if preserve_view:
            vertical.setValue(position[0])
            horizontal.setValue(position[1])
        self._update_controls()

    def show_read_failure(self, message: str):
        if self._live and not self._read_error:
            self._accounts_confirmed = False
            self._reset_intake()
        self._read_error = message or '暂时读不到项目，请刷新后再试。'
        self._deferred_read = ''
        self._account.setText('这是家 · 任务暂未同步' if self._live else '任务暂未同步')
        self._account.setToolTip(self._read_error)
        self._update_controls()

    def show_read_deferred(self, message: str, has_snapshot: bool):
        self._deferred_read = message
        self._waiting_for_snapshot = not has_snapshot
        if self._waiting_for_snapshot:
            self._empty.setText('等待同步')
        self._update_controls()

    def set_busy(self, busy: bool):
        self.busy = busy
        self._update_controls()

    def show_result(self, result: DropResult):
        self._personal_notice_timer.stop()
        revision, self._request_revision = self._request_revision, None
        if revision is not None and revision != self._intake_revision:
            return
        self._clear_preparation()
        if self._live and result.receipt_id and result.material is not None:
            self._prepared_path = str(result.material.path)
            self._result_title = '交件完成' if result.outcome == Outcome.SUBMITTED else '办公室已交接，原站待核对'
            self._result_info = result.message
        elif (self._live and self._accounts_confirmed and result.outcome == Outcome.PENDING
                and result.material is not None and not result.receipt_id):
            self._prepared_path = str(result.material.path)
            if result.prepared:
                self._result_title, self._result_info = _AWAITING_WEB, ''
                task = result.prepared_task
                if task is not None and task.id == result.task_id and self._eligible(task):
                    self._prepared_task = task
            else:
                self._result_title, self._result_info = '原件已保存，待核对', result.message
        elif self._live and result.outcome == Outcome.SUBMITTED:
            self._result_title = '交件状态待核对'
            self._result_info = '请在这是家核对实际交件状态。'
        else:
            self._result_title = '材料已提交' if result.outcome == Outcome.SUBMITTED else result.outcome.value
            self._result_info = result.message
        self._update_controls()

    def show_task_location(self, message: str):
        if self._prepared_task is not None:
            self._result_info = message
            self._update_controls()

    def offer_personal_document(self, filename):
        self._personal_path = str(filename)
        self.pending_document.setText(f'来自个人项目：{Path(filename).name}')
        self.pending_document.setToolTip(self._personal_path)
        self.personal_row.show()
        self._update_controls()

    def _use_personal_document(self):
        path = _local_path(self._personal_path or '')
        if path is None or not _is_file(path):
            self._show_rejection('文件已移动或不可读取，请回到项目重新选择。')
        elif not self.busy and not self.intake_block:
            self._receive_file(str(path))

    def _receive_file(self, filename: str):
        self._personal_path = None
        self.personal_row.hide()
        self._emit_file(filename, None)

    def _emit_file(self, filename: str, task_id: str | None):
        if self.busy or self.intake_block:
            return
        if self._prepared_path:
            self._clear_preparation()
            self._result_title, self._result_info = _WAITING, ''
            self._update_controls()
        self._request_revision = self._intake_revision
        self.file_dropped.emit(filename, task_id)

    def _choose_file(self):
        if self.busy or self.intake_block:
            return
        filename, _ = QFileDialog.getOpenFileName(self, '选择本地 Word 文件', '', 'Word 文件 (*.docx)')
        if not filename:
            return
        path = _local_path(filename)
        if path is None:
            self._show_rejection('请选择本机保存的文件。')
        elif not _is_file(path):
            self._show_rejection('找不到本机文件，请确认原件已保存。')
        else:
            self._receive_file(str(path))

    def _show_rejection(self, message: str):
        self._personal_notice_timer.stop()
        self._result_title, self._result_info = '未接收文件', message
        self._clear_preparation()
        self._update_controls()

    def _copy_saved_path(self):
        if self._prepared_path:
            QApplication.clipboard().setText(self._prepared_path)

    def _open_saved_directory(self):
        if self._prepared_path:
            directory = Path(self._prepared_path).parent
            opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))
            self._result_info = '' if opened else '暂时无法打开保存目录，可复制路径后手动打开。'
            self._update_controls()

    def _sync_status(self):
        if not self._live:
            return '', ''
        if self._read_error:
            prefix = '下方为上次同步的任务，交件已暂停。' if self._snapshot.account else '任务同步失败，交件已暂停。'
            return '任务暂未同步', f'{prefix}{self._read_error}'
        if self._waiting_for_snapshot:
            return '等待同步本人任务', ''
        if not self._has_material_account():
            return '手册匹配需登录材料账号', '分发、领件和打包请点「团队办理」。做手册请先登录「这是家」。'
        if self._personal_mode:
            return '', '本人待办会同步到「项目」。'
        if self._workbox_error:
            return '请重新核对账号', self._workbox_error
        if not self._workbox_username:
            return '请登录 WorkBox Online', '已读取本人任务。办理团队交件请点「团队办理」登录办公室账号。'
        if not self._accounts_confirmed:
            return '请确认两个账号', '已读取本人任务。请核对两个账号，再点击“确认两个账号”。'
        return '', '两个账号已确认。已读取本人任务。'

    def _update_controls(self):
        selected = self._selected_task()
        self._bind_task.setVisible(bool(self._personal_mode and selected and selected.id in self._unlinked_tasks))
        self._bind_task.setEnabled(not self.busy)
        web_block, notice = self._sync_status()
        directory_block = ('请先选择交件目录。'
                           if self._live and not self._personal_mode and not self._directory.text() else '')
        self.intake_block = web_block or directory_block
        notice = '\n'.join(filter(None, (notice, self._deferred_read, self._personal_sync_message)))
        self._sync_notice.setText(notice)
        self._sync_notice.setVisible(bool(notice))
        self._choose.setEnabled(not self.busy and not self.intake_block)
        self.use_document.setEnabled(bool(self._personal_path and self._choose.isEnabled()))
        self._login.setEnabled(not self.busy)
        self._workbox_login.setEnabled(not self.busy)
        self._confirm_accounts.setEnabled(bool(self._live and not self.busy and self._has_material_account()
                                               and self._workbox_username and not self._workbox_error
                                               and not self._accounts_confirmed))
        self._refresh.setEnabled(not self.busy)
        self._personal_sync.setEnabled(bool(self._live and self._has_material_account()
                                            and not (self.busy or self._read_error
                                                     or self._waiting_for_snapshot or self._personal_sync_pending)))
        self._select_root.setEnabled(not self.busy)
        title, info = self._result_title, self._result_info
        if self._site_completed:
            title = '网页显示材料完成'
        if self.busy:
            title, info = '正在处理…', ''
        elif web_block and title == _WAITING:
            title = web_block
        elif directory_block:
            if title == _WAITING:
                title = directory_block
            else:
                info = '\n'.join(filter(None, (info, directory_block)))
        show_path = bool(self._prepared_path and not self.busy and not directory_block)
        show_reveal = show_path and self._prepared_task is not None and not self._site_completed
        self._saved_path_row.setVisible(show_path)
        self._handoff_row.setVisible(show_reveal)
        self._reveal_task.setVisible(show_reveal)
        self._reveal_task.setEnabled(show_reveal)
        if self._saved_path.text() != self._prepared_path:
            self._saved_path.setText(self._prepared_path)
            self._saved_path.setCursorPosition(0)
            self._saved_path.setToolTip(self._prepared_path)
        self._result.setText(f'{title}\n{info}' if info else title)
