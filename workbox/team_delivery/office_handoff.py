"""Hand one archived manual to the office, then verify the code worker's copy."""

from base64 import b64encode
from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

from workbox.team_delivery.contracts import DropResult, GatewayError, Outcome
from workbox.team_delivery.site_attempt import site_account, submit_original_site
from workbox.team_delivery.storage import MaterialStore


class OfficeWriteNotSent(GatewayError):
    """The embedded browser positively confirmed that no POST was dispatched."""


class HandoffLog:
    def __init__(self, path: Path):
        self.path = path

    def _open(self):
        db = sqlite3.connect(self.path, timeout=8)
        db.execute('''CREATE TABLE IF NOT EXISTS toolbox_manual_handoffs(
            origin TEXT, member TEXT, actor TEXT, task TEXT, digest TEXT, business TEXT NOT NULL,
            PRIMARY KEY(origin, member, actor, task, digest))''')
        db.execute('''CREATE TABLE IF NOT EXISTS toolbox_site_attempts(
            origin TEXT, member TEXT, actor TEXT, task TEXT, digest TEXT, title TEXT, original TEXT,
            PRIMARY KEY(origin, member, actor, task, digest))''')
        db.execute('''CREATE TABLE IF NOT EXISTS toolbox_site_operations(
            origin TEXT NOT NULL, actor TEXT NOT NULL, task TEXT NOT NULL, round_id TEXT NOT NULL,
            title TEXT NOT NULL, digest TEXT NOT NULL, original TEXT NOT NULL,
            phase TEXT NOT NULL, revision TEXT NOT NULL, claim TEXT NOT NULL,
            PRIMARY KEY(origin, task, actor, round_id))''')
        return db

    def lookup(self, key):
        with closing(self._open()) as db:
            row = db.execute('''SELECT business FROM toolbox_manual_handoffs
                WHERE origin=? AND member=? AND actor=? AND task=? AND digest=?''', key).fetchone()
            return row[0] if row else None

    def release_unsent(self, key, business):
        with closing(self._open()) as db, db:
            db.execute('''DELETE FROM toolbox_manual_handoffs
                WHERE origin=? AND member=? AND actor=? AND task=? AND digest=? AND business=?''', (*key, business))

    def reserve(self, key, business):
        with closing(self._open()) as db, db:
            changed = db.execute('INSERT OR IGNORE INTO toolbox_manual_handoffs VALUES(?,?,?,?,?,?)',
                                 (*key, business)).rowcount
            return changed == 1

    def previous(self, member, actor, title):
        # Keep the worker's call signature. An office change does not lose the
        # original-site recovery path for the same account and archived file.
        if not self.path.is_file():
            return ()
        with closing(self._open()) as db:
            rows = db.execute('''SELECT actor, task, original, digest FROM toolbox_site_attempts WHERE title=?
                UNION SELECT actor, task, original, digest FROM toolbox_site_operations WHERE title=?''',
                              (title, title))
            owner = site_account(actor)
            return sorted({(task, original, digest) for user, task, original, digest in rows
                           if site_account(user or '') == owner})


def account_key(actor):
    return getattr(actor, 'username', '') or actor.id


def handoff_key(member, prepared):
    return (member.origin, member.user_id, account_key(prepared.actor), prepared.task.id, prepared.material.sha256)


def handoff_manual(office, member, prepared, journal, check_accounts):
    material, task = prepared.material, prepared.task
    key = handoff_key(member, prepared)
    already = journal.lookup(key)
    check_accounts()
    try:
        business = already or office.call(member, 'target', task.title)
        if not business:
            raise GatewayError('办公室没有唯一匹配的本人手册任务，请在团队办理中核对分发。')
        if material.path.resolve(strict=True) != material.path or material.path.name != material.name:
            raise GatewayError('手册原件路径已变化，请重新交件。')
        with TemporaryDirectory(prefix='workbox-office-') as temporary:
            with MaterialStore(Path(temporary)).stage_word(material.path) as staged:
                if staged.sha256 != material.sha256:
                    raise GatewayError('归档手册的内容已变化，请重新交件。')
                office.call(member, 'prepare', business, task.title, material.name, bool(already))
                with staged.path.open('rb') as handle:
                    while chunk := handle.read(256 * 1024):
                        office.call(member, 'append', b64encode(chunk).decode('ascii'))
        check_accounts()
        if not already and journal.reserve(key, business):
            try:
                office.call(member, 'write')
            except OfficeWriteNotSent:
                journal.release_unsent(key, business)
                raise
            except GatewayError:
                # A lost response can follow a successful commit. Read the same handoff below.
                pass
        if journal.lookup(key) != business or not office.call(member, 'verify'):
            raise GatewayError('办公室尚未核实代码人员能取到本次原件。请在团队办理中核对该项目。')
        check_accounts()
        return business
    finally:
        try:
            office.call(member, 'clear')
        except GatewayError:
            pass


def deliver_prepared(office, member, prepared, journal, check_accounts, gateway):
    business = handoff_manual(office, member, prepared, journal, check_accounts)
    try:
        complete = submit_original_site(gateway, prepared, journal, check_accounts)
    except GatewayError as error:
        complete = False
        note = str(error)
    else:
        note = '请在原站核对后重试回读。'
    check_accounts()
    return DropResult(Outcome.SUBMITTED if complete else Outcome.PENDING,
                      '办公室手册已交接，代码同事可取原件。' + (
                          '这是家已确认材料提交。' if complete else '这是家状态待核对。' + note),
                      prepared.task.id, prepared.material, receipt_id=business)
