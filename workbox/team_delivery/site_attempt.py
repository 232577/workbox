"""Persist the original site's write boundary separately from office receipts."""

from contextlib import closing
from dataclasses import dataclass
from functools import partial
from hashlib import sha256
from unicodedata import normalize
from uuid import uuid4

from workbox.team_delivery.contracts import ActionUncertain, GatewayError, Stage


_CONFLICT = '原站项目的账号、原件或办理轮次已变化，请在原站核对旧记录。'
_NOT_READY = '请在原站核对上次请求及本次任务工序。'
_START = (Stage.READY, Stage.RETURNED)


def site_account(username):
    return normalize('NFC', username).strip().lower()


def _round(task):
    # Only a server-issued assignment/round identifier qualifies. The displayed
    # revision changes when a phase succeeds and cannot authorize a new round.
    value = getattr(task, 'round_id', '')
    return value if isinstance(value, str) else ''


@dataclass(frozen=True)
class _Attempt:
    phase: str
    revision: str
    claim: str = ''


class PersonalWriteNotSent(GatewayError):
    """The current personal phase did not dispatch and may be retried."""


class _Ledger:
    def __init__(self, journal, origin, actor, current, digest, original='', *, personal_round=None):
        username = site_account(actor.username)
        if not origin or not username:
            raise GatewayError('无法核对原站地址或登录账号。')
        self._journal = journal
        version = _round(current) if personal_round is None else f'personal:{personal_round}'
        self._key = origin.rstrip('/'), username, current.id, version
        self._binding = current.title, digest, original
        self._personal_round = personal_round

    def _find(self, db):
        origin, actor, task, round_id = self._key
        legacy = db.execute('SELECT actor, title, digest FROM toolbox_site_attempts WHERE task=?', (task,)).fetchall()
        if legacy:
            title, digest, _ = self._binding
            if not all((site_account(user or ''), name, checksum) == (actor, title, digest)
                       for user, name, checksum in legacy):
                raise GatewayError(_CONFLICT)
            # Old rows record neither the original origin nor the dispatched phase.
            return _Attempt('legacy', '')
        rows = db.execute('''SELECT actor, round_id, title, digest, phase, revision, claim
            FROM toolbox_site_operations WHERE origin=? AND task=?''', (origin, task)).fetchall()
        current = None
        predecessor_completed = self._personal_round in (None, 1)
        for owner, version, title, digest, phase, revision, claim in rows:
            if round_id != version:
                if self._personal_round is not None and version.startswith('personal:'):
                    previous = int(version.removeprefix('personal:'))
                    if 0 < previous < self._personal_round and phase == 'completed':
                        predecessor_completed |= previous == self._personal_round - 1
                        continue
                elif self._personal_round is None and round_id and version and not version.startswith('personal:'):
                    continue
            if (owner, version, title, digest) != (actor, round_id, *self._binding[:2]):
                raise GatewayError(_CONFLICT)
            current = _Attempt(phase, revision, claim)
        if not predecessor_completed:
            raise GatewayError('未核实原站上一轮已完成，请先在原站核对返工任务。')
        return current

    def current(self):
        with closing(self._journal._open()) as db:
            return self._find(db)

    def change(self, expected, after):
        with closing(self._journal._open()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            if self._find(db) != expected:
                return False
            if expected is None:
                db.execute('INSERT INTO toolbox_site_operations VALUES(?,?,?,?,?,?,?,?,?,?)',
                           (*self._key, *self._binding, after.phase, after.revision, after.claim))
            elif after is None:
                db.execute('DELETE FROM toolbox_site_operations WHERE origin=? AND actor=? AND task=? AND round_id=?',
                           self._key)
            else:
                db.execute('''UPDATE toolbox_site_operations SET phase=?, revision=?, claim=?
                    WHERE origin=? AND actor=? AND task=? AND round_id=?''',
                           (after.phase, after.revision, after.claim, *self._key))
        return True


def _require_same_task(expected, current, actor):
    if ((current.id, current.title, current.owner_id) != (expected.id, expected.title, actor.id)
            or not current.revision or (expected.revision and _round(current) != _round(expected))):
        raise GatewayError(_CONFLICT)


def _mark_finished(ledger, prior, current):
    if current.stage is not Stage.COMPLETED:
        return False
    if prior.phase == 'completed':
        return prior.revision == current.revision
    if prior.phase == 'complete_pending':
        ledger.change(prior, _Attempt('completed', current.revision))
    return True


def _write(ledger, prior, current, phase, writer, actor, check_accounts, read, require_local=None):
    claim = _Attempt(phase + '_pending', current.revision, uuid4().hex)
    reserved = False

    def observe(actual_phase, event):
        nonlocal reserved
        if actual_phase != phase:
            raise GatewayError(_NOT_READY)
        if event == 'before_dispatch':
            if require_local is not None:
                require_local()
            if reserved or not ledger.change(prior, claim):
                raise GatewayError(_NOT_READY)
            reserved = True
        elif event == 'not_sent' and reserved:
            ledger.change(claim, prior)
            reserved = False

    try:
        observed = writer(actor, current, check_accounts=check_accounts, phase_observer=observe)
    except ActionUncertain:
        if not reserved:
            raise
        observed = read()
        if phase == 'start':
            # If the start is already visible here, pin the exact working revision.
            # A later observed Working row without a stable round may be a rework.
            if observed.stage is Stage.WORKING and observed.revision != current.revision:
                ledger.change(claim, _Attempt('working', observed.revision))
            return None
        _mark_finished(ledger, claim, observed)
        return observed
    if not reserved:
        raise GatewayError(_NOT_READY)
    _require_same_task(current, observed, actor)
    if phase == 'start':
        if observed.stage is not Stage.WORKING or observed.revision == current.revision:
            raise GatewayError(_NOT_READY)
        working = _Attempt('working', observed.revision)
        if not ledger.change(claim, working):
            raise GatewayError(_NOT_READY)
        return observed
    if observed.stage is not Stage.COMPLETED:
        raise GatewayError(_NOT_READY)
    _mark_finished(ledger, claim, observed)
    return observed


def _submit_stages(gateway, actor, task, ledger, current, read, check_accounts, *, personal=False,
                   check_local=None, require_local=None):
    prior = ledger.current()
    if prior and prior.phase in ('legacy', 'complete_pending', 'completed'):
        return _mark_finished(ledger, prior, current)
    if prior and prior.phase == 'start_pending':
        if not _round(current) or current.stage is not Stage.WORKING or current.revision == prior.revision:
            return False
        working = _Attempt('working', current.revision)
        if not ledger.change(prior, working):
            return False
        prior = working
    if prior:
        if current.stage is not Stage.WORKING or current.revision != prior.revision:
            return False
    else:
        if current != task:
            raise GatewayError(_CONFLICT)
        if current.stage in _START:
            start = partial(gateway.start_verified, personal=True, check_local=check_local) if personal else gateway.start_verified
            current = _write(ledger, None, current, 'start', start, actor, check_accounts, read, require_local)
            if current is None:
                return False
            prior = _Attempt('working', current.revision)
        elif current.stage is not Stage.WORKING:
            return False
    complete = partial(gateway.complete_verified, personal=True, check_local=check_local) if personal else gateway.complete_verified
    completed = _write(ledger, prior, current, 'complete', complete, actor, check_accounts, read, require_local)
    return completed.stage is Stage.COMPLETED


def submit_personal_site(gateway, actor, task, delivery_id, journal, check_account, *, verify_only=False,
                         check_local=None, round_id=1):
    """Use a committed local delivery event as the durable request binding."""
    if type(round_id) is not int or round_id < 1:
        raise GatewayError('无法核对这次原站交付的办理轮次。')

    def read():
        check_account()
        current = gateway.read_material_task(actor, task)
        _require_same_task(task, current, actor)
        return current

    def require_local():
        if (check_local is None and round_id > 1 or check_local is not None and check_local() is not True):
            raise GatewayError('本次本地交付已变化，原站不会继续提交。请核对已保留的交付。')

    try:
        current = read()
    except GatewayError as error:
        if verify_only:
            raise
        raise PersonalWriteNotSent(str(error)) from error
    digest = sha256(('personal-delivery:' + delivery_id).encode('utf-8')).hexdigest()
    ledger = _Ledger(journal, gateway.base_url, actor, current, digest, personal_round=round_id)
    prior = ledger.current()
    if current.stage is Stage.COMPLETED:
        if prior is None:
            if round_id > 1:
                return None
            if ledger.change(None, _Attempt('completed', current.revision)):
                return current
            prior = ledger.current()
        if prior is not None and _mark_finished(ledger, prior, current):
            return current
        return None
    if verify_only:
        return None

    try:
        verified = _submit_stages(gateway, actor, task, ledger, current, read, check_account, personal=True,
                                  check_local=check_local, require_local=require_local)
    except ActionUncertain:
        raise
    except GatewayError as error:
        require_local()
        saved = ledger.current()
        if saved is None or saved.phase == 'working' and current.stage in (Stage.WORKING, *_START):
            raise PersonalWriteNotSent(str(error)) from error
        raise
    if verified:
        final = read()
        saved = ledger.current()
        if saved is not None and _mark_finished(ledger, saved, final):
            return final
    return None


def submit_original_site(gateway, prepared, journal, check_accounts):
    actor, task = prepared.actor, prepared.task
    origin = getattr(gateway, 'base_url', '')
    if all(callable(getattr(gateway, method, None))
           for method in ('read_material_task', 'start_verified', 'complete_verified')):
        def read():
            current = gateway.read_material_task(actor, task)
            _require_same_task(task, current, actor)
            return current

        current = read()
        ledger = _Ledger(journal, origin, actor, current, prepared.material.sha256, str(prepared.material.path))
        return _submit_stages(gateway, actor, task, ledger, current, read, check_accounts)

    # Older injected gateways expose a single opaque operation. Treat an
    # interrupted call as potentially having sent the completion.
    ledger = _Ledger(journal, origin, actor, task, prepared.material.sha256, str(prepared.material.path))
    if ledger.current() is not None:
        return gateway.read_submission(actor, task)
    check_accounts()
    claim = _Attempt('complete_pending', task.revision, uuid4().hex)
    if not ledger.change(None, claim):
        return gateway.read_submission(actor, task)
    try:
        gateway.submit_verified(actor, task, check_accounts=check_accounts)
    except ActionUncertain:
        return gateway.read_submission(actor, task)
    return True
