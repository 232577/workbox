from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass, replace
from hashlib import sha256
import sqlite3
from threading import Barrier
from types import SimpleNamespace

import pytest

from anonymous_document import make_document
from workbox.team_delivery.contracts import Account, ActionUncertain, GatewayError, Material, Outcome, Stage, Task
from workbox.team_delivery.office_handoff import HandoffLog, deliver_prepared
from workbox.team_delivery.office_identity import WorkBoxAccount


class Office:
    def __init__(self, member='7'):
        self.member = WorkBoxAccount('http://127.0.0.1:18770', member, '匿名办公室', 'office-session')

    def call(self, member, method, *args):
        assert member == self.member
        if method == 'target':
            return 'a4ea2ca0-5359-4d6b-801e-6ef19ea5a14a'
        return True


@dataclass(frozen=True, slots=True)
class RoundTask(Task):
    round_id: str = ''


class Site:
    base_url = 'http://127.0.0.1:18871'

    def __init__(self, stage, *, lose=None):
        self.row = Task('201', '匿名项目', 'original-session', stage, 'before')
        self.posts = []
        self.lose = lose
        self.fail_before_post = False
        self.lose_without_change = False
        self.before_dispatch = lambda: None

    def read_material_task(self, actor, task):
        if (self.row.id, self.row.title, self.row.owner_id) != (task.id, task.title, actor.id):
            raise GatewayError('当前项目无法核对')
        return self.row

    def _write(self, phase, actor, task, check_accounts, observe):
        check_accounts()
        assert self.read_material_task(actor, task) == task
        self.before_dispatch()
        observe(phase, 'before_dispatch')
        if self.fail_before_post == phase:
            observe(phase, 'not_sent')
            raise GatewayError('明确未发送')
        self.posts.append(phase)
        if not (self.lose == phase and self.lose_without_change):
            self.row = replace(self.row, stage=Stage.WORKING if phase == 'start' else Stage.COMPLETED,
                               revision=phase + '-confirmed')
        if self.lose == phase:
            observe(phase, 'uncertain')
            raise ActionUncertain('匿名超时')
        observe(phase, 'verified')
        return self.row

    def start_verified(self, actor, task, *, check_accounts, phase_observer):
        return self._write('start', actor, task, check_accounts, phase_observer)

    def complete_verified(self, actor, task, *, check_accounts, phase_observer):
        return self._write('complete', actor, task, check_accounts, phase_observer)

    def submit_verified(self, actor, task, *, check_accounts):
        observers = dict(check_accounts=check_accounts, phase_observer=lambda *_: None)
        if task.stage is Stage.READY:
            task = self.start_verified(actor, task, **observers)
        return self.complete_verified(actor, task, **observers)

    def read_submission(self, actor, task):
        return self.read_material_task(actor, task).stage is Stage.COMPLETED


@pytest.fixture
def fixture(tmp_path):
    path = tmp_path.resolve() / '匿名项目.docx'
    make_document(path)
    material = Material(path.name, path, sha256(path.read_bytes()).hexdigest())
    actor = Account('original-session', '匿名材料', 'material', 'original-session', 'Fixture')
    return material, actor, tmp_path / 'intake.sqlite'


def drop(fixture, site, member='7', *, actor=None, material=None):
    source, previous_actor, db = fixture
    actor = actor or previous_actor
    office = Office(member)
    prepared = SimpleNamespace(actor=actor, task=site.row, material=material or source)
    return deliver_prepared(office, office.member, prepared, HandoffLog(db), lambda: None, site)


def test_verified_start_readback_allows_one_completion_after_reopen(fixture):
    site = Site(Stage.READY, lose='start')
    assert drop(fixture, site).outcome is Outcome.PENDING
    assert site.posts == ['start']

    actor = replace(fixture[1], id='reopened', session='reopened', username='fixture')
    reopened = Site(Stage.WORKING)
    reopened.row, reopened.posts = replace(site.row, owner_id=actor.id), site.posts
    assert drop(fixture, reopened, actor=actor).outcome is Outcome.SUBMITTED
    assert drop(fixture, reopened, '19', actor=actor).outcome is Outcome.SUBMITTED
    assert reopened.posts == ['start', 'complete']


def test_unknown_completion_deduplicates_across_office_members_and_sessions(fixture):
    site = Site(Stage.WORKING, lose='complete')
    site.lose_without_change = True
    assert drop(fixture, site).outcome is Outcome.PENDING

    actor = replace(fixture[1], id='reopened', session='reopened', username=' fixture ')
    reopened = Site(Stage.WORKING)
    reopened.row, reopened.posts = replace(site.row, owner_id=actor.id), site.posts
    assert drop(fixture, reopened, '19', actor=actor).outcome is Outcome.PENDING
    assert reopened.posts == ['complete']
    reopened.row = replace(reopened.row, stage=Stage.COMPLETED, revision='later')
    assert drop(fixture, reopened, '19', actor=actor).outcome is Outcome.SUBMITTED
    assert reopened.posts == ['complete']


@pytest.mark.parametrize('change', ['other-owner', 'changed-working-version', 'changed-file'])
def test_changed_binding_cannot_continue_an_ambiguous_start(fixture, change, tmp_path):
    site = Site(Stage.READY, lose='start')
    assert drop(fixture, site).outcome is Outcome.PENDING
    actor, material = fixture[1], fixture[0]
    if change == 'other-owner':
        actor = replace(actor, id='new-owner', username='new-owner')
        site.row = replace(site.row, owner_id=actor.id)
    if change == 'changed-working-version':
        site.row = replace(site.row, revision='new assignment')
    if change == 'changed-file':
        newer = tmp_path / 'new version' / material.name
        newer.parent.mkdir()
        make_document(newer)
        with newer.open('ab') as stream:
            stream.write(b'changed contents')
        material = Material(newer.name, newer, sha256(newer.read_bytes()).hexdigest())
    assert drop(fixture, site, '19', actor=actor, material=material).outcome is Outcome.PENDING
    assert site.posts == ['start']


def test_positive_unsent_allows_retry_but_does_not_release_other_stage(fixture):
    site = Site(Stage.READY)
    site.fail_before_post = 'complete'
    assert drop(fixture, site).outcome is Outcome.PENDING
    assert site.posts == ['start']
    site.fail_before_post = None
    assert drop(fixture, site).outcome is Outcome.SUBMITTED
    assert site.posts == ['start', 'complete']


@pytest.mark.parametrize('has_round', [False, True])
def test_delayed_start_only_resumes_with_a_stable_round(fixture, has_round):
    site = Site(Stage.READY, lose='start')
    site.lose_without_change = True
    if has_round:
        site.row = RoundTask(site.row.id, site.row.title, site.row.owner_id, site.row.stage,
                             site.row.revision, round_id='server-round-1')
    assert drop(fixture, site).outcome is Outcome.PENDING
    site.row = replace(site.row, stage=Stage.WORKING, revision='later working')
    assert drop(fixture, site, '19').outcome is (Outcome.SUBMITTED if has_round else Outcome.PENDING)
    assert site.posts == (['start', 'complete'] if has_round else ['start'])


def test_only_a_server_confirmed_new_round_can_reopen_completion(fixture):
    site = Site(Stage.WORKING)
    site.row = RoundTask(site.row.id, site.row.title, site.row.owner_id, site.row.stage,
                         site.row.revision, round_id='server-round-1')
    assert drop(fixture, site).outcome is Outcome.SUBMITTED
    site.row = replace(site.row, stage=Stage.WORKING, revision='changed revision')
    assert drop(fixture, site).outcome is Outcome.PENDING
    site.row = replace(site.row, round_id='server-round-2')
    assert drop(fixture, site).outcome is Outcome.SUBMITTED
    assert site.posts == ['complete', 'complete']


def test_concurrent_office_members_claim_completion_once(fixture):
    first, second = Site(Stage.WORKING), Site(Stage.WORKING)
    ready = Barrier(2, timeout=10)
    second.posts = first.posts
    first.before_dispatch = second.before_dispatch = ready.wait
    with ThreadPoolExecutor(max_workers=2) as workers:
        replies = [workers.submit(drop, fixture, site, member)
                   for site, member in ((first, '7'), (second, '19'))]
        outcomes = {reply.result().outcome for reply in replies}
    assert outcomes == {Outcome.SUBMITTED, Outcome.PENDING}
    assert first.posts == ['complete']


def test_existing_database_unknown_attempt_is_read_only_across_office_members(fixture):
    material, _, db_path = fixture
    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute('''CREATE TABLE toolbox_site_attempts(
            origin TEXT, member TEXT, actor TEXT, task TEXT, digest TEXT, title TEXT, original TEXT,
            PRIMARY KEY(origin, member, actor, task, digest))''')
        db.execute('INSERT INTO toolbox_site_attempts VALUES(?,?,?,?,?,?,?)',
                   ('http://127.0.0.1:18770', '7', 'Fixture', '201', material.sha256,
                    material.path.stem, str(material.path)))
    site = Site(Stage.WORKING)
    assert drop(fixture, site, '19').outcome is Outcome.PENDING
    assert site.posts == []
    site.row = replace(site.row, stage=Stage.COMPLETED, revision='already completed')
    assert drop(fixture, site, '19').outcome is Outcome.SUBMITTED
    assert HandoffLog(db_path).previous(Office('19').member, 'fixture', material.path.stem) == [
        ('201', str(material.path), material.sha256)]
    assert site.posts == []
    with closing(sqlite3.connect(db_path)) as db:
        assert db.execute('SELECT COUNT(*) FROM toolbox_site_attempts').fetchone() == (1,)
