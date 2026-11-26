"""Run an office form in its authenticated Qt page without moving its cookies."""

from dataclasses import dataclass, field
import json
from threading import Event, Lock
from time import monotonic
from uuid import uuid4

from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot

from workbox.team_delivery.contracts import GatewayError
from workbox.team_delivery.office_form import INSTALL
from workbox.team_delivery.office_handoff import OfficeWriteNotSent
from workbox.team_delivery.office_identity import _PROFILE_PATH, _READ_PROFILE


@dataclass(eq=False)
class _Call:
    member: object
    method: str
    args: tuple
    ready: Event = field(default_factory=Event)
    key: str = field(default_factory=lambda: 'request' + uuid4().hex)
    deadline: float = field(default_factory=lambda: monotonic() + 42)
    value: object = None
    error: str = ''
    unsent: bool = False
    started: bool = False


class OfficeConnection(QObject):
    requested = Signal(object)

    def __init__(self, panel):
        super().__init__(panel)
        self.panel = panel
        self._lock = Lock()
        self._account = panel.account
        self._login_state = panel.login_state
        self._pending = set()
        self._closed = False
        self.requested.connect(self._run, Qt.ConnectionType.QueuedConnection)
        panel.account_changed.connect(self._changed)
        panel.login_state_changed.connect(self._set_login_state)

    def current_account(self):
        with self._lock:
            return self._account

    def login_state(self):
        with self._lock:
            return self._login_state

    @Slot(str)
    def _set_login_state(self, state):
        with self._lock:
            if not self._closed:
                self._login_state = state

    def _changed(self, account):
        with self._lock:
            if self._closed:
                return
            previous = self._account
            self._account = account
            obsolete = [job for job in self._pending if job.member != account]
        if previous is not None and account != previous and self.panel.page is not None:
            self.panel.page.runJavaScript('globalThis.officeTransfer?.clear()', 1)
        for job in obsolete:
            self._finish(job, error='办公室登录已变化，请重新核对。')

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._account = None
            self._login_state = 'unknown'
            pending = tuple(self._pending)
        if self.panel.thread().isCurrentThread():
            self.panel.account_changed.disconnect(self._changed)
            self.panel.login_state_changed.disconnect(self._set_login_state)
            if self.panel.page is not None:
                self.panel.page.runJavaScript('globalThis.officeTransfer?.clear()', 1)
        for job in pending:
            self._finish(job, error='已退出团队办理。')

    def call(self, member, method, *args):
        guard = self.panel.pair_guard if method == 'write' else None
        token = None
        if guard is not None:
            try:
                token = guard.write_token()
            except GatewayError as error:
                raise OfficeWriteNotSent(str(error)) from error
            args = (token,)
        try:
            return self._call(member, method, args)
        except GatewayError as error:
            if token is not None:
                sent = guard.finish_write(token)
                token = None
                if not sent:
                    raise OfficeWriteNotSent('登录已变化或本次交接已取消，请重新核对后交件。') from error
                if isinstance(error, OfficeWriteNotSent):
                    raise GatewayError('办公室请求已发出，请核对同一项目的交接记录。') from error
            raise
        finally:
            if token is not None:
                guard.finish_write(token)

    def _call(self, member, method, args):
        job = _Call(member, method, args)
        with self._lock:
            if self._closed or member is None or self._account != member:
                kind = OfficeWriteNotSent if method == 'write' else GatewayError
                raise kind('请在工具箱登录办公室，并核对两个账号。')
            self._pending.add(job)
        self.requested.emit(job)
        if not job.ready.wait(45):
            self._finish(job, error='办公室操作超时，请核对该项目的交接记录。')
        if job.error:
            raise (OfficeWriteNotSent if job.unsent else GatewayError)(job.error)
        return job.value

    def _finish(self, job, value=None, error=''):
        with self._lock:
            if job in self._pending:
                self._pending.remove(job)
                job.value, job.error = value, error
                if error and job.method == 'write' and not job.started:
                    job.unsent = True
                job.ready.set()

    def _begin_script(self, job):
        # Share the worker timeout's lock so an unsent reservation cannot be
        # released between deciding to dispatch and recording that decision.
        with self._lock:
            if job not in self._pending or monotonic() >= job.deadline:
                return False
            job.started = True
            return True

    @Slot(object)
    def _run(self, job):
        if job.ready.is_set():
            return
        page = self.panel.page
        if page is None or self.panel.account != job.member:
            self._finish(job, error='办公室账号已变化。')
            return
        start = ''
        if job.method in ('target', 'prepare'):
            start = ('globalThis.officeTransfer?.clear();(' + INSTALL + ')('
                     + json.dumps(job.member.origin) + ',' + json.dumps(job.member.user_id) + ');')
        key = json.dumps(job.key)
        if job.method == 'identity':
            params = json.dumps(dict(origin=job.member.origin, path=_PROFILE_PATH, timeout=5000))
            operation = '(' + _READ_PROFILE + ')(' + params + ')'
        else:
            operation = 'globalThis.officeTransfer[' + json.dumps(job.method) + '](...' + json.dumps(job.args) + ')'
        invoke = ("(async()=>{try {" + start + " const v=await " + operation + ";globalThis["
                  + key + "]={ok:true,value:v};}catch(e){globalThis[" + key +
                  "]={ok:false,unsent:globalThis.officeTransfer?.wasSent()===false};}})(); true")
        # World 1 is ApplicationWorld. Page navigation discards uploaded chunks and responses.
        def poll():
            if job.ready.is_set():
                return
            if monotonic() >= job.deadline or self.panel.page is not page or self.panel.account != job.member:
                if self.panel.page is page:
                    page.runJavaScript('globalThis.officeTransfer?.clear()', 1)
                self._finish(job, error='未收到办公室回执，请重新核对交接。')
                return
            def received(raw):
                if job.ready.is_set():
                    return
                if not raw:
                    QTimer.singleShot(50, self, poll)
                    return
                result = json.loads(raw)
                job.unsent = job.method == 'write' and result.get('unsent') is True
                self._finish(job, result.get('value'), '' if result['ok'] else '办公室无法核实项目或办理状态，请打开团队办理核对。')
            page.runJavaScript("(()=>{const r=globalThis[" + key + "];delete globalThis[" +
                               key + "];return r?JSON.stringify(r):'';})()", 1, received)
        if not self._begin_script(job):
            self._finish(job, error='办公室请求已过期，请重试。')
            return
        try:
            page.runJavaScript(invoke, 1, lambda _: poll())
        except RuntimeError:
            self._finish(job, error='办公室窗口已关闭，请核对该项目。')
