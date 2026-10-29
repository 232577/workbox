"""Single-instance native lifecycle and sequential, cancellable intake jobs."""

from collections import deque
import hashlib
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QLockFile, QPoint, QRect, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QFrame, QHBoxLayout, QMessageBox, QPlainTextEdit, QSystemTrayIcon, QVBoxLayout
import shiboken6

from compact_ui import CompactPanel, button, label, zip_delivery_hint
from floating import FloatingBall, decode_mime
from ui import FileJob, app_icon
from native_files import plain_path
from theme import apply_to
from dialog_lifecycle import transient_dialog


def clamp_position(position, size):
    screens = QApplication.screens()
    point = QPoint(*position)
    screen = next((s for s in screens if s.availableGeometry().contains(point)), screens[0])
    area = screen.availableGeometry().adjusted(6, 6, -6, -6)
    return QPoint(max(area.left(), min(point.x(), area.right()-size.width()+1)),
                  max(area.top(), min(point.y(), area.bottom()-size.height()+1)))


class FeedbackBubble(QFrame):
    open_requested = Signal()
    cancel_requested = Signal()

    def __init__(self):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setWindowTitle('WorkBox 收件反馈')
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.navigation_target = None
        apply_to(self, 'feedback')
        self.setFixedWidth(318)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        self.text = label('', 'title', True)
        layout.addWidget(self.text)
        row = QHBoxLayout()
        view = button('查看')
        close = button('收起')
        self.cancel = button('取消处理')
        view.clicked.connect(self.open_requested)
        self.cancel.clicked.connect(self.cancel_requested)
        close.clicked.connect(self.hide)
        row.addWidget(view)
        row.addWidget(self.cancel)
        row.addWidget(close)
        layout.addLayout(row)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.hide)

    def show_message(self, text, orb, busy=False, *, target=None):
        self.navigation_target = target
        self.text.setText(text[:260])
        self.text.setToolTip(text)
        self.cancel.setVisible(busy)
        self.adjustSize()
        area = orb.screen().availableGeometry()
        x = orb.x()-self.width()-10 if orb.x() > area.center().x() else orb.x()+orb.width()+10
        self.move(clamp_position([x, orb.y()], self.size()))
        self.show()
        if busy:
            self.timer.stop()
        else:
            self.timer.start(6000)


class FloatingController(QObject):
    exit_requested = Signal()

    def __init__(self, service, *, tray_enabled=True):
        super().__init__()
        self.service = service
        self.tray_enabled = tray_enabled
        self.orb = self.panel = self.bubble = self.tray = self.server = None
        self.worker = None
        self.queue = deque()
        self.errors = []
        self.closed = False
        self.quitting = False
        self.owns_lock = False
        self.silent_current = False
        self.background_mode = False
        self.choosing_intake = False
        self.monitor_timer = QTimer(self)
        self.monitor_timer.setInterval(2000)
        self.monitor_timer.timeout.connect(lambda: self.check_archives(silent=True))
        self.lock = QLockFile(str(service.db.path) + '.session.lock')
        self.lock.setStaleLockTime(0)
        self.server_name = 'WorkBox-' + hashlib.sha256(str(service.db.path).casefold().encode()).hexdigest()[:24]

    @property
    def busy(self):
        return self.choosing_intake or self.worker is not None or bool(self.queue)

    @property
    def foreground_busy(self):
        # 监视仍占用同一个串行任务槽，但不能每两秒禁用界面。
        return (self.choosing_intake or (self.worker is not None and not self.silent_current)
                or any(not item[2] for item in self.queue))

    def _sync_panel_busy(self):
        busy = self.foreground_busy
        if self.panel.busy != busy:
            self.panel.set_busy(busy)

    def start(self, *, setup_callback=None):
        if not self.lock.tryLock(0):
            socket = QLocalSocket()
            socket.connectToServer(self.server_name)
            if socket.waitForConnected(1000):
                socket.write(b'activate')
                socket.waitForBytesWritten(1000)
                socket.disconnectFromServer()
            return False
        self.owns_lock = True
        self.server = QLocalServer(self)
        QLocalServer.removeServer(self.server_name)
        if not self.server.listen(self.server_name):
            self.lock.unlock()
            self.owns_lock = False
            raise OSError('无法建立本地单实例通信。')
        self.server.newConnection.connect(self._activate)
        # 持有单实例锁后才打开首次向导；此时没有收件入口或监视任务。
        if setup_callback is not None and self.service.workspaces.setup_required():
            if not setup_callback():
                self.shutdown()
                return False
            if self.service.workspaces.setup_required():
                raise ValueError('首次设置尚未保存，未启动收件与归档。')
        self.orb = FloatingBall()
        self.panel = CompactPanel(self.service)
        self.bubble = FeedbackBubble()
        self.orb.intake.connect(self.receive)
        self.panel.intake.connect(self.receive)
        self.orb.open_requested.connect(self.show_panel)
        self.bubble.open_requested.connect(self.show_feedback)
        for signal in (self.orb.paste_requested, self.panel.paste_requested):
            signal.connect(self.paste)
        for signal in (self.orb.files_requested, self.panel.files_requested):
            signal.connect(self.choose_files)
        for signal in (self.orb.quit_requested, self.panel.quit_requested):
            signal.connect(self.request_quit)
        for signal in (self.orb.background_requested, self.panel.background_requested):
            signal.connect(self.run_in_background)
        self.orb.folder_requested.connect(lambda: self.open_folder(self.service.workspaces.settings()['archive_root']))
        self.panel.folder_requested.connect(self.open_folder)
        self.panel.cancel_requested.connect(self.cancel_jobs)
        self.panel.data_changed.connect(self.refresh)
        self.bubble.cancel_requested.connect(self.cancel_jobs)
        self.orb.position_changed.connect(self.save_position)
        self.orb.hint_changed.connect(self.drop_hint)
        self.panel.state_requested.connect(lambda pid, state: self.enqueue('状态已更新', lambda **_: self.service.set_status(pid, state)))
        self.panel.urgent_requested.connect(lambda pid, urgent: self.enqueue('加急标记已更新', lambda **_: self.service.set_urgent(pid, urgent)))
        self.panel.rename_requested.connect(lambda pid, name: self.enqueue('项目名称已更新', lambda **_: self.service.rename_project(pid, name)))
        self.panel.bulk_requested.connect(self.update_projects)
        self.panel.delete_requested.connect(lambda pid: self.set_project_deleted(pid, True))
        self.panel.restore_deleted_requested.connect(lambda pid: self.set_project_deleted(pid, False))
        self.panel.workspace_requested.connect(self.open_workspace)
        self.panel.scan_requested.connect(lambda: self.check_archives(silent=False))
        self.panel.approve_requested.connect(lambda zid: self.enqueue('已标记为可用版本', lambda **_: self.service.snapshots.approve_version(zid)))
        self.panel.bundle_requested.connect(lambda zid, batch: self.enqueue('核对包内项目', lambda **kw: self.service.snapshots.recognize_bundle(zid, batch_id=batch, **kw)))
        self.panel.delivery_requested.connect(lambda pid, zid: self.enqueue('已手动设为已交付', lambda **kw: self.service.confirm_delivery(pid, zid, confirmed=True, **kw)))
        self.panel.link_requested.connect(lambda zid, pid: self.enqueue('关联交付包', lambda **_: self._link_zip(zid, pid)))
        available = QApplication.primaryScreen().availableGeometry()
        saved = self.service.get_setting('orb_position', [available.right()-100, available.center().y()])
        self.orb.move(clamp_position(saved, self.orb.size()))
        geometry = self.service.get_setting('manager_geometry')
        if geometry and len(geometry) == 4:
            self.panel.setGeometry(QRect(*geometry))
        self.orb.show()
        self.refresh()
        # 即使外壳暂未就绪也注册图标；Qt 会在托盘恢复可用时自动添加。
        if self.tray_enabled:
            self.tray = QSystemTrayIcon(app_icon(), self)
            self.tray.setToolTip('WorkBox 工作箱 · 收项目建目录，ZIP 保留原件自动归档')
            self.tray.setContextMenu(self.orb.context_menu())
            self.tray.activated.connect(lambda reason: self.show_panel() if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) else None)
            self.tray.show()
        with self.service.db.connect() as connection:
            pending = connection.execute("SELECT COUNT(*) FROM managed_transfers WHERE state!='complete'").fetchone()[0]
        if pending:
            self.enqueue('检查上次归档结果', lambda **_: self.service.archive.recover())
        self.monitor_timer.start()
        return True

    def check_archives(self, *, silent=True):
        if self.busy or self.closed or self.quitting or QApplication.activeModalWidget() is not None:
            return
        self.enqueue('检查 ZIP 归档', lambda **kw: self.service.scan_archives(**kw), silent=silent)

    def open_workspace(self, project_id):
        def operation(**_):
            rows = self.service.ensure_workspaces(project_id)
            return {'open_folder': next(row['path'] for row in rows if row['project_id'] == project_id)}
        project = self.service.get_project(project_id)
        if project.get('workspace') and Path(project['workspace']['path']).is_dir():
            self.open_folder(project['workspace']['path'])
        elif project.get('archived_at'):
            self.notify('此项目已归档且工作目录不存在，请先恢复项目或检查目录位置。')
        elif not self.foreground_busy:
            self.enqueue('创建项目工作目录', operation)
        else:
            self.notify('正在处理文件，工作目录尚未就绪，请稍后再试。')

    def _activate(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            socket.readyRead.connect(lambda s=socket: s.readAll())
            socket.disconnected.connect(socket.deleteLater)
            modal = QApplication.activeModalWidget()
            if self.panel is None and modal is not None:
                modal.showNormal()
                modal.raise_()
                modal.activateWindow()
            else:
                self.show_panel()

    def refresh(self):
        if self.panel is None or self.closed:
            return
        self.panel.refresh()
        pending = sum(p['status'] != '已交付' for p in self.service.list_projects())
        self.orb.set_summary(pending)
        self._sync_panel_busy()

    def update_projects(self, project_ids, options):
        if self.foreground_busy:
            return
        ids, changes = list(project_ids), dict(options)
        operation = '已归档' if changes.get('archived') else ('已恢复' if changes.get('archived') is False else '已更新')
        self.enqueue(f'{operation} {len(ids)} 个项目', lambda **_: self.service.bulk_update_projects(ids, **changes))

    def set_project_deleted(self, project_id, deleted):
        if self.foreground_busy:
            return
        def change(**_):
            result = self.service.delete_project(project_id, confirmed=True) if deleted else self.service.restore_deleted_project(project_id)
            return {'project_lifecycle': result}
        self.enqueue('正在删除项目' if deleted else '正在恢复项目', change)

    def show_panel(self):
        if self.panel is None or self.closed:
            return
        self.background_mode = False
        self.orb.show()
        self.refresh()
        area = self.orb.screen().availableGeometry()
        self.panel.resize(min(self.panel.width(), area.width()-24), min(self.panel.height(), area.height()-48))
        was_visible = self.panel.isVisible()
        self.bubble.hide()
        self.panel.showNormal()
        if not was_visible:
            x = self.orb.x()-self.panel.width()-10
            if x < area.left():
                x = self.orb.x()+self.orb.width()+10
            self.panel.move(clamp_position([x, self.orb.y()-100], self.panel.frameGeometry().size()))
        else:
            self.panel.move(clamp_position([self.panel.x(), self.panel.y()], self.panel.frameGeometry().size()))
        self.panel.raise_()
        self.panel.activateWindow()

    def show_feedback(self):
        target = self.bubble.navigation_target
        self.show_panel()
        if self.panel is None or self.closed or target is None:
            return
        kind, value = target
        if kind == 'projects':
            self.panel.focus_projects(value)
        else:
            self.panel.tabs.setCurrentIndex(value)

    def run_in_background(self):
        if self.closed or self.quitting or self.panel is None:
            return False
        self.panel.hide()
        self.bubble.hide()
        self.bubble.timer.stop()
        if self.tray is None or not self.tray.isVisible() or not QSystemTrayIcon.isSystemTrayAvailable():
            self.background_mode = False
            self.orb.show()
            self.notify('系统托盘当前不可用，已保留悬浮球入口；自动归档继续运行。')
            return False
        self.background_mode = True
        self.orb.hide()
        return True

    def save_position(self, position):
        self.orb.move(clamp_position(position, self.orb.size()))
        self.service.set_setting('orb_position', [self.orb.x(), self.orb.y()])

    def drop_hint(self, hint):
        if hint:
            self.bubble.show_message(hint, self.orb)
        elif not self.busy:
            self.bubble.hide()

    def notify(self, text, *, busy=False, target=None):
        # 目标属于当前这条反馈；忙碌、错误和普通提示不得沿用上一条成功结果。
        self.bubble.navigation_target = target
        self.panel.show_notice(text)
        if not self.panel.isVisible() and not self.background_mode:
            self.bubble.show_message(text, self.orb, busy, target=target)

    def paste(self):
        # User requested this read; never connect clipboard.dataChanged or poll it.
        mime = QApplication.clipboard().mimeData()
        data = decode_mime(mime)
        if data['kind'] == 'files':
            self.notify('剪贴板里是文件，尚未收进。请主动拖入，或点击「收进文件」。')
            return
        if data['kind'] != 'unsupported':
            self.receive(data)
        else:
            dialog = QDialog(self.panel)
            dialog.setWindowTitle('粘贴项目消息')
            apply_to(dialog)
            dialog.resize(560, 340)
            layout = QVBoxLayout(dialog)
            edit = QPlainTextEdit()
            edit.setPlaceholderText('在这里粘贴或输入项目消息')
            layout.addWidget(edit)
            confirm = button('收录项目', True)
            layout.addWidget(confirm)
            confirm.clicked.connect(dialog.accept)
            with transient_dialog(dialog):
                if dialog.exec() == QDialog.Accepted:
                    self.receive({'kind': 'text', 'text': edit.toPlainText()})

    def choose_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self.panel if self.panel.isVisible() else self.orb,
            '选择要收进项目的文件；原件保留', '', '所有文件 (*)')
        if paths:
            self.receive({'kind': 'files', 'paths': paths})

    def open_folder(self, path):
        folder = Path(path)
        roots = self.service.workspaces.settings()
        if str(folder) in (roots['work_root'], roots['archive_root']):
            try:
                plain_path(folder).mkdir(parents=True, exist_ok=True)
            except (OSError, ValueError) as error:
                self.notify('无法创建目录：' + str(error))
                return
        if not folder.is_dir():
            self.notify('文件夹不存在，未打开任何 ZIP。')
        elif not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder.resolve()))):
            self.notify('无法打开文件夹：' + str(folder))

    def receive(self, payload):
        if self.quitting or self.closed:
            return
        if not self.busy:
            self.errors.clear()
        if payload.get('kind') == 'text':
            text = payload.get('text', '')
            self.enqueue('收录项目消息', lambda **_: self.service.receive_text(text))
        elif payload.get('kind') == 'files':
            paths = payload.get('paths', [])
            if not paths or len(paths) > 100 or len(self.queue)+len(paths) > 100:
                self.notify('一次最多接收 100 个文件或文件夹。')
                return
            for path in dict.fromkeys(paths):
                self.enqueue('收进文件或文件夹：' + Path(path).name,
                    lambda p=path, **kw: self.service.receive_path(p, **kw),
                    on_cancel=lambda p=path: self.service.record_file_decision(p, 'cancelled', {},
                        message='已取消尚未开始的收录，尚未判断归属；原件保留'))
        else:
            self.notify(payload.get('message', '没有收到项目文字或本地文件。'))

    def enqueue(self, caption, operation, *, silent=False, on_cancel=None):
        if self.quitting or self.closed:
            return
        if not self.busy:
            self.errors.clear()
        self.queue.append((caption, operation, silent, on_cancel))
        self._sync_panel_busy()
        if self.worker is None and not self.choosing_intake:
            self._next()

    def _next(self):
        if self.choosing_intake:
            return
        if self.quitting or not self.queue:
            if self.orb.busy:
                self.orb.set_progress(False)
            self._sync_panel_busy()
            if self.quitting:
                self.shutdown()
                self.exit_requested.emit()
            return
        self.caption, operation, self.silent_current, _ = self.queue.popleft()
        self.worker = FileJob(operation, self)
        if not self.silent_current:
            # wait() 结束后仍可能有已排队进度；停机后不再访问已释放的窗口。
            self.worker.progress_changed.connect(
                lambda percent: self.orb.set_progress(True, percent) if not self.closed else None)
        self.worker.finished.connect(self._completed)
        if not self.silent_current or self.orb.busy:
            self.orb.set_progress(not self.silent_current)
        self._sync_panel_busy()
        if not self.silent_current:
            self.notify(self.caption + '…', busy=True)
        self.worker.start()

    def _completed(self):
        if self.closed or self.worker is None:
            return
        worker = self.worker
        self.worker = None
        target = None if worker.cancelled or worker.error else self._result_target(worker.result)
        if worker.cancelled:
            message = '已取消未完成的处理；已成功入库的文件保留。'
        elif worker.error:
            message = '处理失败：' + worker.error
            self.errors.append(self.caption + '：' + worker.error)
        elif isinstance(worker.result, dict) and 'intake_choice' in worker.result:
            message = '请为文件选择对应的项目；原件保留。'
        elif isinstance(worker.result, dict) and worker.result.get('kind') == 'folder_result':
            result = worker.result
            message = (f"文件夹处理{'已取消' if result.get('cancelled') else '完成'}："
                       f"收进 {result['copied']} 个文件，重复 {result['duplicate']} 个，"
                       f"ZIP 待归档 {result['watching']} 个，"
                       f"失败 {result['failed']} 个，待选择 {result['needs_choice']} 个。原件保留。")
            if result['watching']:
                message += '\nZIP 稳定后复制归档。' + zip_delivery_hint(self.service.workspaces.settings())
            details = [item.get('source_path', '') + '：' + item.get('message', '')
                       for item in result.get('items', []) if item.get('status') in ('failed', 'needs_choice')]
            if details:
                message += '\n' + '\n'.join(details[:20])
            if result['failed']:
                self.errors.append(message)
        elif isinstance(worker.result, dict) and 'state' in worker.result:
            result = worker.result
            message = '已保留消息，请点击「待核对」检查项目名。' if result['state'] == 'needs-review' else f"{'已收录过，未重复创建' if result.get('duplicate') else '已收录'} {len(result['project_ids'])} 个项目。"
            if result.get('workspace_error'):
                message += '\n工作目录创建失败：' + result['workspace_error'] + '。选择项目后点击「工作目录」可重试。'
            elif result.get('workspaces'):
                message += ' 工作目录已就绪。'
        elif isinstance(worker.result, dict) and worker.result.get('watching'):
            message = '已开始观察 ZIP，原件保留。文件稳定后自动复制归档，保留原名。'
            if worker.result.get('working_zip_path'):
                message = 'ZIP 已按原名收进工作目录，原件保留。文件稳定后自动复制归档，保留原名。'
            message += ' ' + zip_delivery_hint(self.service.workspaces.settings())
            if self.service.is_bundle_candidate(Path(worker.result.get('source_path', '')).name):
                message += ' 合包先核对全部项目，待核对时不修改项目状态。'
            elif worker.result.get('project_id') is None:
                message += ' 归档后可在 ZIP 库选择对应项目。'
        elif isinstance(worker.result, dict) and 'file' in worker.result:
            project = self.service.db.get_project(worker.result['file']['project_id'])
            status = '已设为待交付' if project['status'] == '待交付' else '项目当前' + project['status']
            message = '文件已收进项目，' + status + '，原件保留。\n' + worker.result['file']['path']
        elif isinstance(worker.result, dict) and 'open_folder' in worker.result:
            self.open_folder(worker.result['open_folder'])
            message = '工作目录已打开。'
        elif isinstance(worker.result, dict) and 'archive' in worker.result and 'project' in worker.result:
            event = next((row for row in self.service.db.delivery_events_for_project(worker.result['project']['id'])
                if row['id'] == worker.result.get('event_id')), None)
            counting = ('该项目不计入个人工作量，交付记录已保留。' if event and not event.get('included_in_workload',True) else
                        '本次首次交付已计入今日统计。' if event and event['counted'] else
                        '本次为补交，不增加今日首次交付数。' if event and event['counting_role'] == 'repeat' else
                        '交付记录已保存。')
            message = '项目工作文件已按原名归档，已设为已交付。' + counting + '\n' + worker.result['archive']['path']
        elif isinstance(worker.result, dict) and 'project_lifecycle' in worker.result:
            result = worker.result['project_lifecycle']
            message = ('项目已移入已删除，原文件、归档和历史交付记录保留。' if result['deleted'] else
                       '项目已恢复到已归档列表，原文件保留。' if result['archived'] else
                       '项目已恢复到日常列表，原文件保留。')
        elif isinstance(worker.result, dict) and 'archived' in worker.result:
            result = worker.result
            message = '' if self.silent_current else f"本次归档 {len(result['archived'])} 个版本，{result['pending']} 个文件等待稳定。"
            if result['archived']:
                message = f"已保存 {len(result['archived'])} 个 ZIP 归档版本，原件和历史版本保留。"
                bundles = [self.service.db.get_zip_bundle(z['id']) for z in result['archived']]
                matched = [b for b in bundles if b and b['state'] == 'matched']
                pending = [b for b in bundles if b and b['state'] == 'needs-review']
                if matched:
                    message += f" 已匹配 {len(set(pid for b in matched for pid in b['project_ids']))} 个项目。"
                details = self._archive_state_summary(result['archived'])
                if details:
                    message += ' ' + details
                if pending:
                    message += '\n合包需要核对：' + '；'.join(b['reason'] for b in pending) + ' 可在 ZIP 库点击「识别包内项目」。'
            if result['errors']:
                message = '部分 ZIP 暂未归档，原件及已有版本保留。\n' + '\n'.join(item['source_path'] + '：' + item['error'] for item in result['errors'])
        elif isinstance(worker.result, dict) and 'zip' in worker.result:
            result = worker.result
            bundle = result.get('bundle') or self.service.db.get_zip_bundle(result['zip']['id'])
            if bundle:
                message = (f"已核对 {len(bundle['project_ids'])} 个项目。"
                           if bundle['state'] == 'matched' else '项目状态未修改：' + bundle['reason'])
            else:
                message = 'ZIP 已归档，原件保留。' if result.get('source_preserved') else 'ZIP 关联已更新。'
            if not bundle and result['zip']['project_id'] is None:
                message += ' 请在 ZIP 库选择对应项目。'
            details = self._archive_state_summary([result['zip']])
            if details:
                message += ' ' + details
        elif isinstance(worker.result, list) and worker.result:
            message = '有归档未完成，请查看以下位置；不会自动删除不明确的文件。\n' + '\n'.join(
                item.get('error', '') + '\n来源：' + item['source'] + '\n目标：' + item['destination'] for item in worker.result)
        else:
            message = self.caption
        if isinstance(worker.result, dict) and worker.result.get('created_project_ids') and not worker.error and not worker.cancelled:
            message = f"已新建 {len(worker.result['created_project_ids'])} 个项目及工作目录。" + message
        worker.deleteLater()
        if (self.silent_current and isinstance(worker.result, dict) and 'archived' in worker.result
                and not worker.result['archived']
                and not worker.error and not worker.cancelled):
            # 无业务变化时只更新归档等待信息，避免打断筛选弹窗、列表拖动与项目详情。
            self.panel.refresh_archive_status()
        else:
            self.refresh()
        if not self.queue and self.errors:
            message = f'本次有 {len(self.errors)} 项失败，其余文件独立处理。\n' + '\n'.join(self.errors)
            target = None
        if message:
            self.notify(message, target=target)
        if not worker.cancelled and not worker.error and isinstance(worker.result, dict):
            self._resolve_intake_choices(worker.result)
        self._next()

    def _resolve_intake_choices(self, result):
        if self.quitting or self.closed:
            return
        if (result.get('intake_choice') or result.get('needs_choice')) and not self.service.list_projects():
            self.notify('还没有可选项目。请先用「粘贴消息」创建项目，或恢复已有项目，再收进文件；原件保留。')
            return
        pending = []
        if result.get('intake_choice'):
            pending.append((result['intake_choice'], result['routing'], '', None))
        elif (result.get('kind') == 'folder_result' and not result.get('cancelled')
                and not result.get('manual_choice_attempted')):
            groups = {}
            for item in result.get('items', []):
                if item.get('status') == 'needs_choice' and item.get('retry_path'):
                    key = (item.get('root_path', item['retry_path']),
                           str(Path(item.get('relative_path', '.')).parent),
                           tuple(sorted(item.get('candidate_ids', []))))
                    groups.setdefault(key, []).append(item)
            for items in groups.values():
                item = items[0]
                pending.append((item['retry_path'], {'basis': item.get('basis', ''), 'candidates': []},
                                item.get('message', ''), items))
        self.choosing_intake = bool(pending)
        self._sync_panel_busy()
        try:
            seen = set()
            for path, routing, explanation, folder_items in pending:
                if self.quitting or self.closed or path in seen:
                    continue
                seen.add(path)
                title = Path(path).name + ('\n' + explanation if explanation else '')
                if folder_items and len(folder_items) > 1:
                    title = f"同目录的 {len(folder_items)} 个待选择文件\n" + '\n'.join(
                        Path(item['retry_path']).name for item in folder_items[:5]) + '\n' + explanation
                project_id = self.panel.choose_file_project(title)
                if project_id is None:
                    for cancelled_path in ([item['retry_path'] for item in folder_items] if folder_items else [path]):
                        self.service.record_file_decision(cancelled_path, 'cancelled', routing,
                            message='已取消项目选择；原件保留')
                    self.notify('已取消该项，原件保留：' + Path(path).name)
                else:
                    self.enqueue('收进文件或文件夹：' + Path(path).name,
                        lambda p=path, pid=project_id, items=folder_items, **kw:
                            self.service.receive_path(p, pid, manual=True, folder_items=items, **kw))
        finally:
            self.choosing_intake = False
            self._sync_panel_busy()

    def _link_zip(self, zip_id, project_id):
        self.service.link_zip(zip_id, project_id)
        return {'zip': self.service.db.get_zip(zip_id)}

    def _archive_state_summary(self, records):
        """旧版本只提供归档事实；反馈状态始终读取本次全部提交后的项目。"""
        latest = {}
        for record in sorted(records, key=lambda item: item['id'], reverse=True):
            bundle = self.service.db.get_zip_bundle(record['id'])
            if bundle and bundle['state'] == 'needs-review':
                continue
            for fact in self.service.db.get_zip_file_counts(record['id']):
                latest.setdefault(fact['project_id'], fact)
        lines = []
        for pid, fact in sorted(latest.items()):
            project = self.service.db.get_project(pid)
            status = '当前' + project['status']
            if project['status'] != fact['decided_status']:
                status = '归档时' + fact['decided_status'] + '；' + status
            lines.append(f"{project['name']}：{fact['file_count']} 个文件，{status}")
        return '；'.join(lines)

    def _result_target(self, result):
        if not isinstance(result, dict):
            return None
        if result.get('created_project_ids'):
            return ('projects', tuple(result['created_project_ids']))
        if isinstance(result.get('project'), dict) and result['project'].get('id'):
            return ('projects', (result['project']['id'],))
        if result.get('kind') == 'folder_result':
            ids = result.get('project_ids') or []
        elif 'state' in result:
            if result['state'] == 'needs-review':
                return ('tab', 2)
            ids = result.get('project_ids') or []
        elif result.get('watching'):
            ids = [result['project_id']] if result.get('project_id') is not None else []
            if not ids:
                return ('tab', 1)
        elif 'file' in result:
            pid = result['file'].get('project_id')
            ids = [pid] if pid is not None else []
        elif 'zip' in result:
            bundle = result.get('bundle') or self.service.db.get_zip_bundle(result['zip']['id'])
            if bundle:
                if bundle['state'] != 'matched':
                    return ('tab', 1)
                ids = bundle.get('project_ids') or []
            else:
                pid = result['zip'].get('project_id')
                ids = [pid] if pid is not None else []
            if not ids:
                return ('tab', 1)
        elif 'archived' in result:
            if result.get('errors'):
                return ('tab', 1)
            ids = []
            for archive in result['archived']:
                bundle = self.service.db.get_zip_bundle(archive['id'])
                if bundle:
                    if bundle['state'] != 'matched':
                        return ('tab', 1)
                    ids.extend(bundle.get('project_ids') or [])
                elif archive.get('project_id') is not None:
                    ids.append(archive['project_id'])
                else:
                    return ('tab', 1)
        else:
            return None
        return ('projects', tuple(dict.fromkeys(ids))) if ids else None

    def cancel_jobs(self):
        if self.worker:
            self.worker.cancel_event.set()
        pending = list(self.queue)
        self.queue.clear()
        for _, _, _, on_cancel in pending:
            if on_cancel:
                try:
                    on_cancel()
                except Exception as error:
                    self.notify('取消收录的记录写入失败：' + str(error))

    def request_quit(self):
        self.quitting = True
        self.monitor_timer.stop()
        self.cancel_jobs()
        if not self.worker:
            self.shutdown()
            self.exit_requested.emit()
        else:
            self.notify('正在结束当前文件操作，完成后退出。', busy=True)

    def shutdown(self):
        if self.closed:
            return
        self.monitor_timer.stop()
        self.cancel_jobs()
        if self.worker and not self.worker.wait(15000):
            raise RuntimeError('文件操作仍未结束，窗口与文件锁保持，不强制退出。')
        self.worker = None
        if self.panel is not None and getattr(self.panel, 'today_dialog', None) is not None:
            self.panel.today_dialog.shutdown()
        if self.panel is not None:
            self.panel.document_pane.shutdown()
        if self.orb:
            self.save_position([self.orb.x(), self.orb.y()])
            geometry = self.panel.geometry()
            self.service.set_setting('manager_geometry', [geometry.x(), geometry.y(), geometry.width(), geometry.height()])
            for widget in (self.orb, self.panel, self.bubble):
                widget.hide()
            self.bubble.timer.stop()
        if self.tray:
            self.tray.hide()
        if self.server:
            self.server.close()
        if self.owns_lock:
            self.lock.unlock()
            self.owns_lock = False
        self.closed = True

    def dispose_ui(self):
        """真实入口在安全停机之后、QApplication 仍存活时释放本控制器的根窗口。"""
        if not self.closed:
            raise RuntimeError('请先完成业务停机，再释放窗口。')
        application = QApplication.instance()
        if application is None or QThread.currentThread() != application.thread():
            raise RuntimeError('窗口必须在 QApplication 存活的界面线程中释放。')
        roots = [widget for widget in (self.orb, self.panel, self.bubble)
                 if widget is not None and shiboken6.isValid(widget)]
        threads = self.findChildren(QThread)
        for widget in roots:
            threads.extend(widget.findChildren(QThread))
        if self.worker is not None or any(thread.isRunning() for thread in threads):
            raise RuntimeError('后台任务尚未结束，窗口保持，不强制释放。')
        if self.tray is not None and shiboken6.isValid(self.tray):
            # 托盘菜单归悬浮球所有；先解除平台托盘对该菜单的引用。
            self.tray.setContextMenu(None)
        for widget in roots:
            widget.close()
            widget.deleteLater()
        # 主事件循环已返回，必须在 QApplication 析构前消费 Qt 延迟销毁事件。
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
