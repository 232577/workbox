"""Original-site window and worker gateway for the shared Qt browser runtime."""

from dataclasses import dataclass, field
from hashlib import sha256
import json
from threading import Event, Lock
from time import monotonic, time
from unicodedata import normalize
from urllib.parse import urlsplit
from uuid import uuid4

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtWebEngineCore import (
    QWebEnginePage, QWebEngineProfile, QWebEngineScript, QWebEngineSettings, QWebEngineUrlRequestInterceptor,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QDialog, QVBoxLayout, QWidget

from workbox.team_delivery.contracts import Account, ActionUncertain, GatewayError, Snapshot, Stage, Task
from workbox.team_delivery.qt_site_script import OBSERVE, OPERATE


_ORIGINAL = 'https://zheshijia.dpdns.org'
_WORLD = QWebEngineScript.ScriptWorldId.ApplicationWorld.value
_TIMEOUT = 23
_WRITES = frozenset({'start-material', 'complete-material'})
_AUTH = frozenset({'/api/login', '/api/logout', '/api/change-password', '/api/setup'})
_STAGES = {stage.value: stage for stage in (Stage.READY, Stage.WORKING, Stage.RETURNED)}
_STAGES['已完成'] = Stage.COMPLETED
_MESSAGES = {
    'account': '原站登录已变化，请重新核对账号。',
    'target': '原站项目的编号、全名、负责人或工序发生变化，请重新核对。',
    'busy': '原站正在编辑、筛选或处理选中项目，请结束当前操作后再交件。',
    'not-visible': '请在原站本人材料清单按此项目编号查找。',
    'read': '暂时不能核对原站项目或登录状态，请稍后重试。',
    'origin': '原站窗口未就绪，请在工具箱内重新打开。',
    'closed': '原站窗口已退出，请重新核对。',
    'expired': '原站未收到可核对的结果，请稍后重试。',
}


def _approved_origin(value):
    parsed = urlsplit(value)
    if value.rstrip('/') == _ORIGINAL:
        return _ORIGINAL
    if (parsed.scheme == 'http' and parsed.hostname == '127.0.0.1' and parsed.port and
            parsed.netloc == f'127.0.0.1:{parsed.port}' and parsed.path in ('', '/') and
            not parsed.query and not parsed.fragment):
        return f'http://127.0.0.1:{parsed.port}'
    raise ValueError('请选择受信的原站地址。')


def _normalized(value):
    return normalize('NFC', value).strip().lower()


def _origin_key(url):
    scheme = url.scheme()
    if not url.isValid() or scheme not in ('http', 'https') or not url.host() or '@' in url.authority():
        return None
    return scheme, url.host().lower(), url.port(443 if scheme == 'https' else 80)


@dataclass(eq=False)
class _Request:
    operation: str
    expected: dict | None
    expected_epoch: int | None
    write_token: str | None
    ready: Event = field(default_factory=Event)
    key: str = field(default_factory=lambda: 'workboxSite' + uuid4().hex)
    deadline: float = field(default_factory=lambda: monotonic() + _TIMEOUT)
    utc_deadline: int = field(default_factory=lambda: int((time() + _TIMEOUT) * 1000))
    started: bool = False
    result: dict | None = None
    error: str = ''
    epoch: int = 0


class _OriginRequests(QWebEngineUrlRequestInterceptor):
    auth_changed = Signal()

    def __init__(self, origin, parent, pair_guard):
        super().__init__(parent)
        self._key = _origin_key(QUrl(origin))
        self._pair_guard = pair_guard

    def interceptRequest(self, request):
        url = request.requestUrl()
        if _origin_key(url) != self._key:
            request.block(True)
            return
        try:
            if request.requestMethod() == b'POST' and url.path() in _AUTH:
                if self._pair_guard is not None:
                    self._pair_guard.invalidate()
                self.auth_changed.emit()
            if self._pair_guard is not None and not self._pair_guard.accept_request(request):
                request.block(True)
        except Exception:
            request.block(True)


class _OriginalPage(QWebEnginePage):
    def __init__(self, profile, view, origin):
        super().__init__(profile, view)
        self._key = _origin_key(QUrl(origin))
        settings = self.settings()
        settings.setUnknownUrlSchemePolicy(QWebEngineSettings.UnknownUrlSchemePolicy.DisallowUnknownUrlSchemes)
        for feature in ('LocalContentCanAccessFileUrls', 'LocalContentCanAccessRemoteUrls', 'JavascriptCanOpenWindows',
                        'JavascriptCanAccessClipboard', 'JavascriptCanPaste', 'PluginsEnabled', 'DnsPrefetchEnabled'):
            settings.setAttribute(getattr(QWebEngineSettings.WebAttribute, feature), False)
        self.permissionRequested.connect(lambda permission: permission.deny())

    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
        return _origin_key(url) == self._key

    def chooseFiles(self, mode, old_files, accepted_mime_types):
        return []


class QtSiteSession(QObject):
    """Own the GUI-side window and release waiting readers before destroying its page."""

    requested = Signal(object)
    cancelled = Signal(object)

    def __init__(self, origin=_ORIGINAL, parent=None, *, pair_guard=None):
        super().__init__(parent)
        self.origin = _approved_origin(origin)
        self.page = self.window = self._profile = self._requests = None
        self._pair_guard = pair_guard
        self._lock = Lock()
        self._pending = set()
        self._opened = self._closed = False
        self._epoch = 0
        self.requested.connect(self._run, Qt.ConnectionType.QueuedConnection)
        self.cancelled.connect(self._cancel_script, Qt.ConnectionType.QueuedConnection)

    def request(self, operation, expected=None, *, expected_epoch=None):
        with self._lock:
            if self._closed:
                raise GatewayError(_MESSAGES['closed'])
            if not self._opened and operation != 'open':
                return self._epoch, {'account': None, 'rows': []}
        guard = self._pair_guard
        token = guard.write_token() if guard is not None and operation in _WRITES else None
        job = _Request(operation, expected, expected_epoch, token)
        dispatched = None
        try:
            with self._lock:
                if self._closed:
                    raise GatewayError(_MESSAGES['closed'])
                self._pending.add(job)
            self.requested.emit(job)
            if not job.ready.wait(_TIMEOUT + 1):
                self._finish(job, error='expired')
                self.cancelled.emit(job)
        finally:
            if token is not None:
                dispatched = guard.finish_write(token)
        if dispatched is False and (job.error == 'uncertain' or (job.result or {}).get('target')):
            raise GatewayError('原站请求未发送，请重新核对账号和页面。')
        if dispatched is True and job.error:
            raise ActionUncertain('原站请求已发送，请先回读核对，工具箱不会重发。')
        if job.error == 'uncertain':
            raise ActionUncertain('原站操作可能已执行，请先回读确认，工具箱不会重发。')
        if job.error:
            raise GatewayError(_MESSAGES.get(job.error, _MESSAGES['read']))
        return job.epoch, job.result

    def _finish(self, job, result=None, error='', *, interrupted=False):
        with self._lock:
            if job in self._pending:
                self._pending.remove(job)
                if job.operation in _WRITES and job.started and (interrupted or error == 'expired'):
                    error = 'uncertain'
                job.result, job.error = result, error
                job.ready.set()

    @Slot()
    def _identity_changed(self):
        self._invalidate_pair()
        with self._lock:
            self._epoch += 1
            aged = [job for job in self._pending if job.started and job.operation != 'open']
        for job in aged:
            self._finish(job, error='account', interrupted=True)
            self._cancel_script(job)

    def _invalidate_pair(self):
        if self._pair_guard is not None:
            self._pair_guard.invalidate()

    @Slot(object)
    def _cancel_script(self, job):
        if self.page is not None:
            key = json.dumps(job.key)
            self.page.runJavaScript(f'globalThis.workboxSitePending?.get({key})?.abort(); '
                                    f'delete globalThis[{key}];', _WORLD)

    def _new_window(self):
        owner = self.parent()
        window = QDialog(owner if isinstance(owner, QWidget) else None)
        window.setWindowTitle('这是家 · 原站')
        window.setWindowModality(Qt.WindowModality.NonModal)
        window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        window.resize(1060, 720)
        layout = QVBoxLayout(window)
        layout.setContentsMargins(0, 0, 0, 0)
        view = QWebEngineView(window)
        view.setAccessibleName('这是家原站')
        layout.addWidget(view)

        profile = QWebEngineProfile(self)
        profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)
        profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
        profile.setPersistentPermissionsPolicy(QWebEngineProfile.PersistentPermissionsPolicy.AskEveryTime)
        interceptor = _OriginRequests(self.origin, profile, self._pair_guard)
        interceptor.auth_changed.connect(self._identity_changed, Qt.ConnectionType.QueuedConnection)
        profile.setUrlRequestInterceptor(interceptor)
        profile.downloadRequested.connect(lambda download: download.cancel())
        page = _OriginalPage(profile, view, self.origin)
        observer = QWebEngineScript()
        observer.setName('workbox original-site account and interaction')
        observer.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        observer.setRunsOnSubFrames(False)
        observer.setWorldId(_WORLD)
        observer.setSourceCode(OBSERVE)
        page.scripts().insert(observer)
        page.loadStarted.connect(self._identity_changed)
        page.renderProcessTerminated.connect(lambda *_args: self._identity_changed())
        view.setPage(page)
        self.page, self.window, self._profile, self._requests = page, window, profile, interceptor
        with self._lock:
            self._opened = True
        page.load(QUrl(self.origin + '/'))

    def _show_window(self):
        if self.window.isMinimized():
            self.window.showNormal()
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    @Slot(object)
    def _run(self, job):
        with self._lock:
            if self._closed or job not in self._pending:
                return
            active = monotonic() < job.deadline
            authorized = job.expected_epoch is None or job.expected_epoch == self._epoch
            if active and authorized:
                job.started, job.epoch = True, self._epoch
        if not active:
            self._finish(job, error='expired')
            return
        if not authorized:
            self._finish(job, error='account')
            return
        if job.operation == 'open':
            if self.window is None:
                self._new_window()
            self._show_window()
            self._finish(job, result={})
            return

        page = self.page
        if page is None or _origin_key(page.url()) != _origin_key(QUrl(self.origin)):
            self._finish(job, result={'account': None, 'rows': []})
            return
        arguments = json.dumps(dict(origin=self.origin, operation=job.operation, expected=job.expected,
                                    deadline=job.utc_deadline, remaining=max(0, (job.deadline - monotonic()) * 1000),
                                    requestKey=job.key, writeToken=job.write_token))
        key = json.dumps(job.key)
        start = f'Promise.resolve(({OPERATE})({arguments})).then(r=>globalThis[{key}]=r); true'
        inspect = (f'(()=>{{const result=globalThis[{key}]; delete globalThis[{key}];'
                   f'return result?JSON.stringify(result):"";}})()')

        def poll():
            if job.ready.is_set():
                return
            if self.page is not page or self._epoch != job.epoch or monotonic() >= job.deadline:
                self._finish(job, error='expired', interrupted=True)
                self._cancel_script(job)
                return
            try:
                page.runJavaScript(inspect, _WORLD, received)
            except RuntimeError:
                self._finish(job, error='closed', interrupted=True)

        def received(raw):
            if job.ready.is_set():
                return
            if not raw:
                QTimer.singleShot(40, self, poll)
                return
            if self.page is not page or self._epoch != job.epoch:
                self._finish(job, error='account', interrupted=True)
                return
            try:
                result = json.loads(raw)
            except (TypeError, ValueError):
                self._finish(job, error='read', interrupted=True)
                return
            error = result.get('error', '')
            if not error and job.operation == 'reveal' and result.get('target'):
                self._show_window()
            self._finish(job, result=result, error=error)

        try:
            page.runJavaScript(start, _WORLD, lambda _: poll())
        except RuntimeError:
            self._finish(job, error='closed', interrupted=True)

    @Slot()
    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed, self._opened = True, False
            pending = tuple(self._pending)
        self._invalidate_pair()
        for job in pending:
            self._finish(job, error='closed', interrupted=True)
        page, window, profile = self.page, self.window, self._profile
        self.page = self.window = self._profile = self._requests = None
        if page is not None:
            page.runJavaScript('globalThis.workboxSiteClosed=true; '
                               'globalThis.workboxSitePending?.forEach(entry => entry.abort());', _WORLD)
            page.triggerAction(QWebEnginePage.WebAction.Stop)
            page.deleteLater()
        if window is not None:
            window.close()
            window.deleteLater()
        if profile is not None:
            profile.setUrlRequestInterceptor(None)
            profile.deleteLater()


@dataclass(frozen=True, slots=True)
class SiteSubmissionReceipt:
    task_id: str
    account: str
    submitted_now: bool


class QtSiteGateway:
    """Present verified material facts to the worker without exposing the browser session."""

    def __init__(self, session):
        self._session = session
        self.base_url = session.origin
        self._account = self._observation = self._epoch = None
        self._unresolved = set()

    def close(self):
        self._account = self._observation = self._epoch = None

    def open_login(self):
        self._session.request('open')

    def _observe(self, epoch, identity):
        if self._observation is not None and (self._observation != identity or self._epoch != epoch):
            self._session._invalidate_pair()
        if not identity:
            self._account = self._observation = self._epoch = None
        elif self._observation != identity or self._epoch != epoch:
            identifier = uuid4().hex
            role = 'material' if identity['role'] == '材料' else 'code'
            self._account = Account('live-' + identifier, identity['name'], role, identifier, identity['username'])
            self._observation, self._epoch = identity, epoch
        return self._account

    def current_account(self):
        epoch, result = self._session.request('identity')
        return self._observe(epoch, result.get('account'))

    @staticmethod
    def _task(account, row):
        identifier, title = row.get('id'), row.get('title')
        if (not isinstance(identifier, str) or not identifier.isascii() or not identifier.isdecimal() or
                identifier.startswith('0') or len(identifier) > 16 or int(identifier) > 2**53 - 1 or
                not isinstance(title, str) or not title):
            raise GatewayError('原站项目编号或全名不完整，请在原站核对。')
        owner, updated, status = row.get('owner'), row.get('updated'), row.get('status')
        if not all(isinstance(value, str) for value in (owner, updated, status)) or not owner or not status:
            raise GatewayError('原站项目负责人或工序不完整，请在原站核对。')
        basis = (identifier, title, _normalized(account.username), owner, updated, status)
        digest = sha256(json.dumps(basis, ensure_ascii=False).encode()).hexdigest()
        return Task(identifier, title, account.id, _STAGES.get(status, Stage.UNKNOWN), 'observed-' + digest)

    def snapshot(self):
        epoch, result = self._session.request('snapshot')
        account = self._observe(epoch, result.get('account'))
        tasks = tuple(self._task(account, row) for row in result['rows']) if account else ()
        if len({task.id for task in tasks}) != len(tasks):
            raise GatewayError('原站本人材料有重复编号，请在原站核对。')
        return Snapshot(account, tasks)

    def poll_snapshot(self):
        return self.snapshot()

    def refresh_snapshot(self):
        return self.snapshot()

    def task(self, task_id):
        return next((item for item in self.snapshot().tasks if item.id == task_id), None)

    def _request_target(self, account, task, operation, known=None):
        if (account is None or account.role != 'material' or account != self._account or task.owner_id != account.id or
                not isinstance(task.id, str) or not task.id.isascii() or not task.id.isdecimal() or
                task.id.startswith('0') or len(task.id) > 16 or int(task.id) > 2**53 - 1 or not task.title):
            raise GatewayError('请先核对原站材料账号以及本人项目编号。')
        expected = {**(known or {}), 'account': self._observation, 'id': task.id, 'title': task.title}
        epoch, result = self._session.request(operation, expected, expected_epoch=self._epoch)
        if self._observe(epoch, result.get('account')) != account or not result.get('target'):
            raise GatewayError(_MESSAGES['account'])
        return result['target']

    def reveal_task(self, account, task):
        row = self._request_target(account, task, 'target')
        if self._task(account, row) != task:
            raise GatewayError(_MESSAGES['target'])
        self._request_target(account, task, 'reveal', known=row)

    def read_material_task(self, account, task):
        return self._task(account, self._request_target(account, task, 'target'))

    def read_submission(self, account, task):
        stage = self.read_material_task(account, task).stage
        if stage is Stage.COMPLETED:
            self._clear_task_uncertainty(account, task)
            return True
        if stage not in (Stage.READY, Stage.WORKING, Stage.RETURNED):
            raise GatewayError(_MESSAGES['target'])
        return False

    def start_verified(self, account, task, *, check_accounts=None, phase_observer=None):
        return self._apply_phase(account, task, 'start', (Stage.READY, Stage.RETURNED), Stage.WORKING,
                                 check_accounts, phase_observer)

    def complete_verified(self, account, task, *, check_accounts=None, phase_observer=None):
        completed = self._apply_phase(account, task, 'complete', (Stage.WORKING,), Stage.COMPLETED,
                                      check_accounts, phase_observer)
        self._clear_task_uncertainty(account, task)
        return completed

    def _clear_task_uncertainty(self, account, task):
        key = (_normalized(account.username), task.id)
        self._unresolved.difference_update((*key, phase) for phase in ('start', 'complete'))

    def _apply_phase(self, account, task, phase, allowed, desired, check_accounts, phase_observer):
        if check_accounts is not None:
            check_accounts()
        row = self._request_target(account, task, 'target')
        key = (_normalized(account.username), task.id, phase)
        if key in self._unresolved:
            raise ActionUncertain('上次原站请求尚未核实，请在原站核对状态。')
        current = self._task(account, row)
        if current != task or current.stage not in allowed:
            raise GatewayError(_MESSAGES['target'])

        # The observer must finish its durable reservation before requesting a network permit.
        if phase_observer is not None:
            phase_observer(phase, 'before_dispatch')
        try:
            verified = self._request_target(account, task, phase + '-material', known=row)
        except ActionUncertain:
            self._unresolved.add(key)
            self._record_phase(phase_observer, phase, 'uncertain')
            raise
        except GatewayError:
            self._record_phase(phase_observer, phase, 'not_sent')
            raise
        try:
            result = self._task(account, verified)
            if result.stage is not desired:
                self._record_phase(phase_observer, phase, 'uncertain')
                raise ActionUncertain('原站响应后的材料工序未确认，请回读核对。')
            self._record_phase(phase_observer, phase, 'verified')
        except GatewayError as error:
            self._unresolved.add(key)
            raise ActionUncertain(str(error)) from error
        if check_accounts is not None:
            try:
                check_accounts()
            except GatewayError as error:
                raise ActionUncertain('原站已核实该工序，请恢复账号确认后再核对。') from error
        return result

    @staticmethod
    def _record_phase(observer, phase, event):
        if observer is not None:
            try:
                observer(phase, event)
            except Exception as error:
                raise ActionUncertain('未能保存原站阶段记录，请回读核对。') from error

    def submit_verified(self, account, task, *, check_accounts=None, phase_observer=None):
        if check_accounts is not None:
            check_accounts()
        current = self.read_material_task(account, task)
        if current.stage is Stage.COMPLETED:
            self._clear_task_uncertainty(account, task)
            return SiteSubmissionReceipt(task.id, account.username, False)
        key = (_normalized(account.username), task.id)
        if any((*key, phase) in self._unresolved for phase in ('start', 'complete')):
            raise ActionUncertain('上次原站请求尚未核实，请在原站核对状态。')
        if current != task:
            raise GatewayError(_MESSAGES['target'])
        progressed = current.stage in (Stage.READY, Stage.RETURNED)
        if progressed:
            current = self.start_verified(account, current, check_accounts=check_accounts,
                                          phase_observer=phase_observer)
        try:
            self.complete_verified(account, current, check_accounts=check_accounts,
                                   phase_observer=phase_observer)
        except ActionUncertain:
            raise
        except GatewayError as error:
            if progressed:
                raise ActionUncertain('原站已有工序变化，请回读核对后再处理。') from error
            raise
        return SiteSubmissionReceipt(task.id, account.username, True)

    def begin(self, account, task, request_id):
        raise GatewayError('请完成办公室交件核对，再办理原站材料。')

    def submit(self, account, task, request_id, material):
        raise GatewayError('请完成办公室交件核对，再办理原站材料。')
