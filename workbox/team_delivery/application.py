"""连接桌面收件、网页代办和本机原件存放。"""

from dataclasses import dataclass
import logging
from pathlib import Path
from queue import Queue
from threading import Thread
from time import monotonic

from PySide6.QtCore import QObject, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox

from workbox.team_delivery.configuration import load_shared_root, save_shared_root, shared_root, staging_journal
from workbox.team_delivery.contracts import (
    MATERIAL_PREPARATION_STAGES, Account, DropResult, GatewayError, Material, Outcome, RefreshDeferred, Snapshot, Stage, Task,
)
from workbox.team_delivery.desktop import DesktopWindow
from workbox.team_delivery.pair_guard import PairGuard
from workbox.team_delivery.workflow import WorkflowService


LIVE_SITE_URL = 'https://zheshijia.dpdns.org/'
_LIVE_REFRESH_MS = 15_000
_MIN_REFRESH_GAP = 5
_LOGIN_CHECK_MS = 2_000


@dataclass(frozen=True)
class PreparedManual:
    actor: Account
    task: Task
    material: Material


class _Worker(QObject):
    snapshot_ready = Signal(object)
    result_ready = Signal(object)
    read_failed = Signal(str)
    read_deferred = Signal(object, str)
    storage_changed = Signal(object)
    task_location_ready = Signal(str)
    idle = Signal()
    poll_finished = Signal()
    pair_ready = Signal(object, object, bool, str)
    upload_offer = Signal(object, object, str)

    def __init__(self, url: str, material_root: Path | None, journal_path: Path, *, gateway=None, pair_guard=None):
        super().__init__()
        self._material_root = material_root
        self._journal_path = journal_path
        self._gateway = gateway
        self._service = None
        self._last_account = None
        self._prepared_target: PreparedManual | None = None
        self._workbox = None
        self._embedded_office = False
        self._confirmed_pair = None
        self._pair_guard = pair_guard

    def _message(self, error: Exception, fallback: str) -> str:
        if isinstance(error, GatewayError):
            return str(error) or fallback
        logging.error('桌面交件处理失败', exc_info=error)
        return fallback

    def _connect(self) -> None:
        if self._gateway is None:
            raise GatewayError('原站窗口尚未就绪，请重新打开团队交件。')

    def _processor(self) -> WorkflowService:
        if self._material_root is None:
            raise GatewayError('请先选择已授权的交件目录。')
        try:
            if shared_root(self._material_root) != self._material_root:
                raise ValueError('交件目录已变化。')
        except ValueError as error:
            raise GatewayError('交件目录暂不可用，请检查连接后重试，或重新选择目录。') from error
        if self._service is None:
            self._service = WorkflowService(self._gateway, self._material_root, self._journal_path,
                                            prepare_only=True, check_preparation_account=self._guard_pair)
        return self._service

    def _forget_pair(self):
        if self._pair_guard is not None and self._pair_guard.confirmed():
            self._pair_guard.invalidate()
        self._confirmed_pair = self._prepared_target = None

    def _pair(self, actor):
        error = ''
        try:
            member = self._workbox.current_account() if self._workbox else None
        except (OSError, GatewayError) as exc:
            member, error = None, self._message(exc, '暂时无法核对 WorkBox 账号。')
        pair = (member, actor) if member and actor else None
        if (self._confirmed_pair != pair or
                self._pair_guard is not None and not self._pair_guard.confirmed()):
            self._forget_pair()
        confirmed = pair is not None and self._confirmed_pair == pair
        self.pair_ready.emit(member, actor, confirmed, error)
        return pair

    def _guard_pair(self):
        try:
            actor = self._gateway.current_account()
        except (OSError, GatewayError):
            self._pair(None)
            raise GatewayError('暂时无法核对这是家账号，请重新核对两个账号。') from None
        pair = self._pair(actor)
        if pair is None or pair != self._confirmed_pair:
            raise GatewayError('请先登录并核对当前两个账号。')

    def confirm_accounts(self, expected):
        try:
            self._connect()
            generation = self._pair_guard.generation() if self._pair_guard is not None else None
            actor = self._gateway.current_account()
            pair = self._pair(actor)
            if (pair is not None and pair == expected
                    and (self._pair_guard is None or self._pair_guard.confirm(generation))):
                self._confirmed_pair = pair
                self.pair_ready.emit(*pair, True, '')
            elif actor != self._last_account:
                self._observe_accounts()
        except Exception as exc:
            self._forget_pair()
            self.pair_ready.emit(None, None, False, self._message(exc, '请重新核对两个账号。'))
        finally:
            self.idle.emit()

    def use_office(self, connection):
        self._workbox = connection
        self._embedded_office = True
        self._pair(self._last_account)

    def _check_delivery_accounts(self):
        self._guard_pair()
        member = self._confirmed_pair[0]
        try:
            current = self._workbox.call(member, 'identity')
        except GatewayError:
            self._forget_pair()
            raise
        if current != {'id':member.user_id, 'username':member.username}:
            self._forget_pair()
            raise GatewayError('办公室登录已变化，请重新核对两个账号。')

    def configure_storage(self, filename: str, settings: Path) -> None:
        try:
            directory = shared_root(Path(filename))
            save_shared_root(settings, directory)
            if directory != self._material_root:
                self._prepared_target = None
            self._material_root = directory
            self._journal_path = staging_journal(settings.parent, directory)
            self._service = None
            self.storage_changed.emit(directory)
        except (OSError, ValueError):
            self.result_ready.emit(DropResult(Outcome.BLOCKED, '无法设置交件目录，请确认目录可以访问。'))
        finally:
            self.idle.emit()

    def _refresh_tasks(self, *, passive: bool = False) -> bool:
        try:
            if passive:
                snapshot = self._gateway.poll_snapshot()
            else:
                snapshot = self._gateway.refresh_snapshot()
            self._publish_snapshot(snapshot)
            return True
        except RefreshDeferred as error:
            try:
                self._observe_accounts(str(error))
            except Exception as account_error:
                self._report_read_failure(account_error)
        except Exception as error:
            self._report_read_failure(error)
        return False

    def _publish_snapshot(self, snapshot: Snapshot) -> None:
        self._last_account = snapshot.account
        if self._prepared_target is not None and snapshot.account != self._prepared_target.actor:
            self._prepared_target = None
        self.snapshot_ready.emit(snapshot)
        self._pair(snapshot.account)

    def _observe_accounts(self, deferred: str = '') -> None:
        current = self._gateway.current_account()
        if current != self._last_account:
            self._forget_pair()
            if current is None or current.role != 'material':
                self._publish_snapshot(Snapshot(current))
                return
            self._last_account = current
            deferred = deferred or '等待同步本人材料清单。'
        if deferred:
            self.read_deferred.emit(current, deferred)
        self._pair(current)

    def _report_read_failure(self, error: Exception) -> None:
        self._clear_previous_account()
        self._forget_pair()
        self.pair_ready.emit(None, None, False, '请刷新以重新核对当前账号。')
        self.read_failed.emit(self._message(error, '暂时读不到项目，请刷新后再试。'))

    def _clear_previous_account(self) -> None:
        if self._last_account is None:
            return
        try:
            if self._gateway.current_account() == self._last_account:
                return
        except Exception:
            pass
        self._last_account = None
        self._prepared_target = None
        self.snapshot_ready.emit(Snapshot(None))

    def refresh(self, automatic: bool = False) -> None:
        try:
            self._connect()
            self._refresh_tasks(passive=automatic)
        except Exception as error:
            self.read_failed.emit(self._message(error, '暂时无法读取项目，请重新打开登录页后刷新。'))
        finally:
            (self.poll_finished if automatic else self.idle).emit()

    def check_login(self) -> None:
        try:
            self._connect()
            self._observe_accounts()
        except Exception as error:
            self._report_read_failure(error)
        finally:
            self.poll_finished.emit()

    def open_login(self) -> None:
        try:
            self._connect()
            self._gateway.open_login()
            self._observe_accounts()
        except Exception as error:
            self.read_failed.emit(self._message(error, '暂时无法打开工作网页。'))
        finally:
            self.idle.emit()

    def submit_file(self, filename: str, task_id, account) -> None:
        self._prepared_target = None
        try:
            self._connect()
            try:
                service = self._processor()
            except GatewayError as error:
                self.result_ready.emit(DropResult(Outcome.BLOCKED, str(error)))
                return
            if not self._refresh_tasks():
                self.result_ready.emit(DropResult(Outcome.BLOCKED, '暂时无法核对最新任务，请刷新后再试。'))
                return
            try:
                self._guard_pair()
            except GatewayError as error:
                self.result_ready.emit(DropResult(Outcome.BLOCKED, str(error)))
                return
            result = self._resume_office(Path(filename), task_id, account) if self._embedded_office else None
            if result is None:
                result = service.submit_file(Path(filename), task_id, account)
            if result.material is not None:
                try:
                    self._guard_pair()
                except GatewayError as error:
                    self.result_ready.emit(DropResult(Outcome.BLOCKED, str(error)))
                    return
                if account is not None and result.prepared and result.prepared_task is not None:
                    self._prepared_target = PreparedManual(account, result.prepared_task, result.material)
                    if self._embedded_office:
                        from workbox.team_delivery.office_handoff import deliver_prepared, HandoffLog
                        try:
                            result = deliver_prepared(self._workbox, self._confirmed_pair[0], self._prepared_target,
                                                      HandoffLog(self._journal_path), self._check_delivery_accounts, self._gateway)
                        except (GatewayError, OSError, ValueError) as error:
                            result = DropResult(Outcome.PENDING, str(error), result.task_id, result.material)
            self.result_ready.emit(result)
            self._observe_accounts()
        except Exception as error:
            self.result_ready.emit(DropResult(Outcome.PENDING, self._message(error, '暂时无法确认交件，请查看项目状态后再试。')))
        finally:
            self.idle.emit()

    def _resume_office(self, source, selected_id, account):
        from tempfile import TemporaryDirectory
        from workbox.team_delivery.office_handoff import account_key, HandoffLog
        from workbox.team_delivery.storage import MaterialStore
        if account is None or self._confirmed_pair is None:
            return None
        member = self._confirmed_pair[0]
        prior = HandoffLog(self._journal_path).previous(member, account_key(account), source.stem)
        if not prior:
            return None
        active = [t for t in self._gateway.snapshot().tasks if t.owner_id == account.id
                  and t.title == source.stem and t.stage in MATERIAL_PREPARATION_STAGES]
        if active:
            return None
        with TemporaryDirectory(prefix='workbox-readback-') as temp:
            with MaterialStore(Path(temp)).stage_word(source) as staged:
                rows = [(number, path) for number, path, digest in prior
                        if digest == staged.sha256 and (selected_id is None or number == selected_id)]
                if len(rows) != 1:
                    return None
                number, original = rows[0]
                # Completed rows can leave “只看我的”. Reconcile the saved identity
                # against the current server record before accepting this repeat.
                task = Task(number, source.stem, account.id, Stage.COMPLETED, '')
                if not self._gateway.read_submission(account, task):
                    return None
                path = Path(original).resolve(strict=True)
                if not path.is_relative_to(self._material_root) or path.name != source.name:
                    raise GatewayError('原归档已发生变化，请核对原件。')
                return DropResult(Outcome.PENDING, '正在核对既有交接。', number, Material(source.name, path, staged.sha256),
                                  prepared=True, prepared_task=task)

    def reveal_saved_task(self) -> None:
        try:
            if self._prepared_target is None:
                raise GatewayError('请先保存并核对本次材料，再定位网页任务。')
            self._guard_pair()
            account, task = self._prepared_target.actor, self._prepared_target.task
            self._gateway.reveal_task(account, task)
            self.task_location_ready.emit(f'已在这是家定位任务 #{task.id}。')
        except Exception as error:
            self._clear_previous_account()
            self.task_location_ready.emit(self._message(error, '暂时无法定位网页任务，请刷新后重试。'))
        finally:
            self.idle.emit()

    def _current_manual(self):
        self._guard_pair()
        prepared = self._prepared_target
        if prepared is None or self._gateway.task(prepared.task.id) != prepared.task:
            raise GatewayError('原站任务已变化或没有本次归档，请重新核对任务。')
        self._guard_pair()
        return prepared

    def offer_manual_upload(self):
        try:
            prepared = self._current_manual()
            project = self._workbox.manual_target(prepared.task.title)
            self.upload_offer.emit(prepared, self._confirmed_pair, project)
        except Exception as error:
            self.task_location_ready.emit(self._message(error, '暂时无法核对 WorkBox 上传页。'))
            self.idle.emit()

    def fill_manual_upload(self, expected, pair, project):
        try:
            prepared = self._current_manual()
            if prepared != expected or self._confirmed_pair != pair:
                raise GatewayError('确认期间账号或归档原件已变化，请重新核对。')
            self._workbox.fill_manual_form(prepared.task.title, project, prepared.material, check_accounts=self._guard_pair)
            self.task_location_ready.emit('手册已选入 WorkBox 上传页。请在网页点击「上传并交接」，查看交接结果。')
        except Exception as error:
            self.task_location_ready.emit(self._message(error, '暂时无法选入原件，请检查 WorkBox 上传页。'))
        finally:
            self.idle.emit()

    def close(self) -> None:
        try:
            close_service = getattr(self._service, 'close', None)
            if callable(close_service):
                close_service()
        finally:
            try:
                if self._workbox is not None:
                    self._workbox.close()
            finally:
                if self._gateway is not None:
                    self._gateway.close()


class IntakeController(QObject):
    snapshot_changed = Signal(object)
    result_ready = Signal(object)

    def __init__(self, window, url: str, material_root: Path | None, journal_path: Path, *, settings: Path,
                 gateway=None):
        super().__init__(window)
        self._window = window
        self._snapshot = Snapshot(None)
        self._current_readable = False
        self._busy = False
        self._polling = False
        self._poll_on_idle = False
        self._last_refresh_started = float('-inf')
        self._login_check_until = 0.0
        self._waiting_workbox = False
        self._last_drop = None
        self._ask_for_project = False
        self._settings = settings
        self._office = None
        self._office_connection = None
        self._visible_pair = None
        self._directory = material_root
        self._site_session = None
        self._pair_guard = None
        if gateway is None:
            from workbox.team_delivery.qt_site import QtSiteSession, QtSiteGateway
            self._pair_guard = PairGuard()
            self._site_session = QtSiteSession(url, window, pair_guard=self._pair_guard)
            gateway = QtSiteGateway(self._site_session)
        self._worker = _Worker(url, material_root, journal_path, gateway=gateway, pair_guard=self._pair_guard)
        self._commands = Queue()
        self._thread = Thread(target=self._run_worker, name='online-intake-browser')
        self._worker.snapshot_ready.connect(self._show_snapshot, Qt.ConnectionType.QueuedConnection)
        self._worker.result_ready.connect(self._show_result, Qt.ConnectionType.QueuedConnection)
        self._worker.read_failed.connect(self._show_read_failure, Qt.ConnectionType.QueuedConnection)
        self._worker.read_deferred.connect(self._show_read_deferred, Qt.ConnectionType.QueuedConnection)
        self._worker.storage_changed.connect(self._show_storage, Qt.ConnectionType.QueuedConnection)
        self._worker.idle.connect(self._idle, Qt.ConnectionType.QueuedConnection)
        self._worker.poll_finished.connect(self._poll_finished, Qt.ConnectionType.QueuedConnection)
        window.refresh_requested.connect(self.refresh)
        window.shown.connect(self._page_shown)
        QGuiApplication.instance().applicationStateChanged.connect(self._application_state_changed)
        window.login_requested.connect(self.open_login)
        window.file_dropped.connect(self.submit_file)
        window.workbox_login_requested.connect(self._open_workbox)
        window.office_address_requested.connect(lambda: self._open_workbox(change_address=True))
        window.workbox_pair_requested.connect(self._confirm_accounts)
        self._worker.pair_ready.connect(self._show_pair, Qt.ConnectionType.QueuedConnection)
        window.select_root_requested.connect(self._choose_storage)
        window.reveal_task_requested.connect(self._reveal_saved_task)
        window.manual_upload_requested.connect(self._offer_manual_upload)
        self._worker.upload_offer.connect(self._confirm_manual_upload, Qt.ConnectionType.QueuedConnection)
        self._worker.task_location_ready.connect(self._show_task_location, Qt.ConnectionType.QueuedConnection)
        window.configure_live(str(material_root) if material_root is not None else None)
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(_LIVE_REFRESH_MS)
        self._timer.timeout.connect(self._poll)

    def start(self) -> None:
        self._thread.start()
        self._timer.start()
        if self._window.isVisible():
            self._poll(immediate=True)

    def stop(self) -> None:
        self._timer.stop()
        if self._office_connection:
            self._office_connection.close()
        if self._site_session is not None:
            self._site_session.close()
        if self._thread.is_alive():
            self._commands.put(None)
            self._thread.join()
        if self._office is not None:
            self._office.shutdown()

    def _run_worker(self) -> None:
        # 耗时交件串行执行，Qt 网页操作由会话转交主线程。
        try:
            while (command := self._commands.get()) is not None:
                action, args = command
                action(*args)
        finally:
            try:
                self._worker.close()
            except Exception as error:
                logging.error('关闭桌面浏览器失败', exc_info=error)

    def _admit(self) -> bool:
        if self._busy or not self._thread.is_alive():
            return False
        self._busy = True
        self._window.set_busy(True)
        return True

    @Slot()
    def refresh(self) -> None:
        if self._admit():
            self._last_refresh_started = monotonic()
            self._commands.put((self._worker.refresh, ()))

    @Slot()
    def _page_shown(self) -> None:
        self._poll(immediate=True)

    @Slot(Qt.ApplicationState)
    def _application_state_changed(self, state: Qt.ApplicationState) -> None:
        if state == Qt.ApplicationState.ApplicationActive:
            self._page_shown()

    @Slot()
    def _poll(self, immediate: bool = False) -> None:
        if not self._thread.is_alive():
            return
        if self._busy or self._polling:
            self._poll_on_idle |= immediate
            return
        now = monotonic()
        if self._login_check_until and now >= self._login_check_until:
            self._finish_login_check()
        gap = _MIN_REFRESH_GAP if immediate else _LIVE_REFRESH_MS / 1000
        if now - self._last_refresh_started >= gap:
            self._last_refresh_started = now
            self._polling = True
            self._commands.put((self._worker.refresh, (True,)))
            if not self._login_check_until:
                self._timer.start()
        elif self._login_check_until:
            self._polling = True
            self._commands.put((self._worker.check_login, ()))

    @Slot()
    def _poll_finished(self) -> None:
        self._polling = False
        self._poll_when_idle()

    def _poll_when_idle(self) -> None:
        if self._poll_on_idle and not (self._busy or self._polling):
            self._poll_on_idle = False
            self._poll(immediate=True)

    def _finish_login_check(self) -> None:
        if self._login_check_until:
            self._waiting_workbox = False
            self._login_check_until = 0.0
            self._timer.setInterval(_LIVE_REFRESH_MS)

    @Slot()
    def open_login(self) -> None:
        if self._admit():
            if self._snapshot.account is None:
                self._login_check_until = monotonic() + 120
                self._timer.setInterval(_LOGIN_CHECK_MS)
            self._commands.put((self._worker.open_login, ()))

    @Slot()
    def _choose_storage(self) -> None:
        if not self._admit():
            return
        chosen = ''
        try:
            chosen = QFileDialog.getExistingDirectory(self._window, '选择已授权的交件目录', str(self._directory or ''))
        finally:
            if not chosen:
                self._idle()
        if chosen:
            self._commands.put((self._worker.configure_storage, (chosen, self._settings)))

    @Slot(object, object, bool, str)
    def _show_pair(self, member, actor, matched, message):
        if member is not None and self._waiting_workbox:
            self._finish_login_check()
        self._visible_pair = (member, actor) if member and actor and self._snapshot.account == actor else None
        if member is not None and actor != self._snapshot.account:
            message = '这是家账号已变化，请刷新后核对。'
        self._window.set_workbox_account(member.username if member else '',
                                         matched and self._visible_pair is not None, message)

    @Slot()
    def _open_workbox(self, change_address=False):
        if not self._admit():
            return
        from workbox.team_delivery.office_identity import normalize_workbox_url
        try:
            address_file = self._settings.with_name('workbox-url.txt')
            previous = address_file.read_text('utf-8') if address_file.is_file() else ''
            url = previous
            if change_address or not previous:
                url, ok = QInputDialog.getText(self._window, '办公室服务器', '服务器地址', text=previous)
                if not ok:
                    self._idle()
                    return
            origin = normalize_workbox_url(url.strip())
            panel = self._window.show_office()
            if self._office is None:
                from workbox.team_delivery.office_connection import OfficeConnection
                self._office = panel
                panel.pair_guard = self._pair_guard
                self._office_connection = OfficeConnection(panel)
                panel.account_changed.connect(self._office_identity_changed)
            panel.connect_to(origin)
            if not previous or previous != origin:
                address_file.write_text(origin, encoding='utf-8')
        except (ValueError, OSError, GatewayError) as error:
            QMessageBox.warning(self._window, 'WorkBox 地址', str(error))
            self._idle()
            return
        self._office_identity_changed(panel.account)
        self._idle()

    @Slot(object)
    def _office_identity_changed(self, member):
        if self._office_connection is not None:
            self._commands.put((self._worker.use_office, (self._office_connection,)))

    @Slot()
    def _confirm_accounts(self):
        if self._visible_pair and self._admit():
            self._commands.put((self._worker.confirm_accounts, (self._visible_pair,)))

    @Slot(object)
    def _show_storage(self, directory: Path) -> None:
        self._directory = directory
        self._window.configure_live(str(directory))

    @Slot()
    def _reveal_saved_task(self) -> None:
        if self._admit():
            self._commands.put((self._worker.reveal_saved_task, ()))

    @Slot()
    def _offer_manual_upload(self):
        if self._office is not None:
            self._open_workbox()
        elif self._admit():
            self._commands.put((self._worker.offer_manual_upload, ()))

    @Slot(object, object, str)
    def _confirm_manual_upload(self, prepared, pair, project):
        question = (f'这是家任务 #{prepared.task.id}　{prepared.task.title}\n'
                    f'WorkBox 项目编号　{project}\n文件　{prepared.material.name}\n\n'
                    '核对为同一项目后，将手册选入当前 WorkBox 上传页。')
        answer = QMessageBox.question(self._window, '核对交件项目', question,
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                      QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            self._commands.put((self._worker.fill_manual_upload, (prepared, pair, project)))
        else:
            self._idle()

    @Slot(str)
    def _show_task_location(self, message: str) -> None:
        self._window.show_task_location(message)

    @Slot(str, object)
    def submit_file(self, filename: str, task_id) -> None:
        account = self._snapshot.account
        if account is None or not self._current_readable:
            self._show_result(DropResult(Outcome.BLOCKED, '请先刷新并确认当前材料账号。'))
        elif self._admit():
            self._last_drop = (filename, task_id, account)
            self._ask_for_project = False
            self._commands.put((self._worker.submit_file, (filename, task_id, account)))

    @Slot(object)
    def _show_snapshot(self, snapshot: Snapshot) -> None:
        self._snapshot = snapshot
        self._current_readable = True
        if snapshot.account is not None and not self._waiting_workbox:
            self._finish_login_check()
        self._window.set_snapshot(snapshot)
        self.snapshot_changed.emit(snapshot)

    @Slot(str)
    def _show_read_failure(self, message: str) -> None:
        self._current_readable = False
        self._finish_login_check()
        self._window.show_read_failure(message)

    @Slot(object, str)
    def _show_read_deferred(self, account: Account | None, message: str) -> None:
        if self._snapshot.account != account:
            self._snapshot = Snapshot(account)
            self._current_readable = False
            self._window.set_snapshot(self._snapshot)
            self.snapshot_changed.emit(self._snapshot)
            if self._login_check_until and not self._waiting_workbox:
                self._poll_on_idle = True
        self._window.show_read_deferred(message, self._current_readable)

    @Slot(object)
    def _show_result(self, result: DropResult) -> None:
        if result.outcome is Outcome.NEEDS_SELECTION and self._last_drop is not None and self._last_drop[1] is None:
            self._ask_for_project = True
        actor = self._snapshot.account
        if (actor is not None and self._last_drop and self._last_drop[2] == actor
                and result.outcome is Outcome.SUBMITTED and result.receipt_id):
            remaining = tuple(task for task in self._snapshot.tasks
                              if (task.id, task.owner_id) != (result.task_id, actor.id))
            if remaining != self._snapshot.tasks:
                self._snapshot = Snapshot(actor, remaining)
                self._window.set_snapshot(self._snapshot)
                self.snapshot_changed.emit(self._snapshot)
        self._window.show_result(result)
        self.result_ready.emit(result)

    @Slot()
    def _idle(self) -> None:
        self._busy = False
        self._window.set_busy(False)
        drop, self._last_drop = self._last_drop, None
        ask, self._ask_for_project = self._ask_for_project, False
        if ask and self._current_readable and drop is not None and self._snapshot.account == drop[2]:
            self._resolve_project(drop[0], drop[2].id)
        self._poll_when_idle()

    def _resolve_project(self, filename: str, actor_id: str) -> None:
        title = Path(filename).stem
        choices = {f'{task.title} · #{task.id}': task.id for task in self._snapshot.tasks
                   if task.title == title and task.owner_id == actor_id and task.stage in MATERIAL_PREPARATION_STAGES}
        if not choices or not self._admit():
            return
        try:
            selection, confirmed = QInputDialog.getItem(
                self._window, '核对交件项目', f'请选择「{Path(filename).name}」对应的项目',
                ['请选择项目', *choices], 0, False)
        finally:
            self._busy = False
            self._window.set_busy(False)
        if confirmed and selection in choices:
            self.submit_file(filename, choices[selection])
