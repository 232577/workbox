"""Persist the original site's write boundary separately from office receipts."""

from contextlib import closing
from dataclasses import dataclass
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


class _Ledger:
    def __init__(self, journal, origin, prepared, current):
        actor = site_account(prepared.actor.username)
        if not origin or not actor:
            raise GatewayError('无法核对原站地址或登录账号。')
        self._journal = journal
        self._key = origin.rstrip('/'), actor, current.id, _round(current)
        self._binding = current.title, prepared.material.sha256, str(prepared.material.path)

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
        for owner, version, title, digest, phase, revision, claim in rows:
            if round_id and version and round_id != version:
                continue
            if (owner, version, title, digest) != (actor, round_id, *self._binding[:2]):
                raise GatewayError(_CONFLICT)
            current = _Attempt(phase, revision, claim)
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


def _write(ledger, prior, current, phase, writer, actor, check_accounts, read):
    claim = _Attempt(phase + '_pending', current.revision, uuid4().hex)
    reserved = False

    def observe(actual_phase, event):
        nonlocal reserved
        if actual_phase != phase:
            raise GatewayError(_NOT_READY)
        if event == 'before_dispatch':
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


def _submit_stages(gateway, actor, task, ledger, current, read, check_accounts):
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
            current = _write(ledger, None, current, 'start', gateway.start_verified, actor, check_accounts, read)
            if current is None:
                return False
            prior = _Attempt('working', current.revision)
        elif current.stage is not Stage.WORKING:
            return False
    completed = _write(ledger, prior, current, 'complete', gateway.complete_verified, actor, check_accounts, read)
    return completed.stage is Stage.COMPLETED


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
        ledger = _Ledger(journal, origin, prepared, current)
        return _submit_stages(gateway, actor, task, ledger, current, read, check_accounts)

    # Older injected gateways expose a single opaque operation. Treat an
    # interrupted call as potentially having sent the completion.
    ledger = _Ledger(journal, origin, prepared, task)
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
