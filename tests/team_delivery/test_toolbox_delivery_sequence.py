from base64 import b64decode
from contextlib import closing
from dataclasses import replace
from hashlib import sha256
import sqlite3
from types import SimpleNamespace

import pytest

from anonymous_document import make_document
from test_site_submission import _open, anonymous_site
from workbox.team_delivery import live_site, site_submission
from workbox.team_delivery.application import _Worker
from workbox.team_delivery.contracts import GatewayError, Outcome, Stage
from workbox.team_delivery.workbox_identity import WorkBoxAccount


class Office:
    project = 'a4ea2ca0-5359-4d6b-801e-6ef19ea5a14a'

    def __init__(self, site_posts):
        self.member = WorkBoxAccount('http://127.0.0.1:18770', '7', 'anonymous-office', 'session-one')
        self.writes = []
        self.download = None
        self.can_receive = True
        self.switch_on_verify = False
        self.verifications = []
        self.calls = []
        self._upload = bytearray()
        self._site_posts = site_posts

    def current_account(self):
        return self.member

    def call(self, member, method, *args):
        self.calls.append(method)
        if member != self.member:
            raise GatewayError('办公室登录已变化。')
        if method == 'identity':
            return {'id': member.user_id, 'username': member.username}
        if method == 'target':
            return self.project
        if method == 'prepare':
            project, _, self._filename, _ = args
            assert project == self.project
            self._upload.clear()
        elif method == 'append':
            self._upload.extend(b64decode(args[0], validate=True))
        elif method == 'write':
            payload = bytes(self._upload)
            self.writes.append((self.project, self._filename, payload))
            if self.can_receive:
                self.download = payload
        elif method == 'verify':
            available = self.download is not None and self.download == bytes(self._upload)
            self.verifications.append((len(self._site_posts), available))
            if self.switch_on_verify:
                self.member = replace(member, user_id='99', session='session-two')
            return available
        elif method == 'clear':
            self._upload.clear()
        else:
            raise AssertionError(f'unexpected office call: {method}')


@pytest.fixture
def delivery(tmp_path, anonymous_site, monkeypatch):
    open_browser = live_site.open_browser

    def open_isolated(driver):
        def launch(**options):
            options['headless'] = True
            return driver.chromium.launch(**options)
        return open_browser(SimpleNamespace(chromium=SimpleNamespace(launch=launch)))

    monkeypatch.setattr(live_site, 'open_browser', open_isolated)
    browser, account, task = _open(anonymous_site, Stage.WORKING)
    _, _, projects, posts, faults = anonymous_site
    projects[1]['name'] = '其他匿名项目'

    def refresh():
        # The anonymous page exposes the same list update without a toolbar.
        browser._page.evaluate('refresh()')
        return browser.snapshot()

    monkeypatch.setattr(browser, 'refresh_snapshot', refresh)
    directory = tmp_path / 'shared'
    directory.mkdir()
    journal = tmp_path / 'delivery.sqlite'
    worker = _Worker(browser.base_url, directory, journal)
    worker._gateway = browser
    office = Office(posts)
    pairs = []
    results = []
    worker.pair_ready.connect(lambda *pair: pairs.append(pair))
    worker.result_ready.connect(results.append)
    worker.use_office(office)
    worker.confirm_accounts((office.member, account))
    assert pairs[-1][:3] == (office.member, account, True)

    source = tmp_path / f'{task.title}.docx'
    make_document(source)

    def drop():
        count = len(results)
        worker.submit_file(str(source), task.id, account)
        assert len(results) == count + 1
        return results[-1]

    return SimpleNamespace(drop=drop, office=office, pairs=pairs, projects=projects, posts=posts,
                           faults=faults, source=source, journal=journal, directory=directory)


def assert_original_saved(delivery, result):
    original = delivery.source.read_bytes()
    assert result.material.path.is_relative_to(delivery.directory)
    assert result.material.path.read_bytes() == original
    assert result.material.sha256 == sha256(original).hexdigest()
    assert delivery.office.writes == [(delivery.office.project, delivery.source.name, original)]


def original_site_attempts(delivery):
    with closing(sqlite3.connect(delivery.journal)) as db:
        return db.execute('SELECT task FROM toolbox_site_operations').fetchall()


def test_no_original_site_writes_until_office_can_retrieve_the_manual(delivery):
    delivery.projects[0]['matStatus'] = '待制作'
    delivery.office.can_receive = False

    result = delivery.drop()

    assert result.outcome is Outcome.PENDING
    assert_original_saved(delivery, result)
    assert delivery.office.verifications == [(0, False)]
    assert delivery.posts == []
    assert [row['matStatus'] for row in delivery.projects] == ['待制作', '待制作']
    assert original_site_attempts(delivery) == []


def test_original_site_timeout_and_repeat_drop_do_not_write_twice(delivery, monkeypatch):
    monkeypatch.setattr(site_submission, '_TIMEOUT_MS', 1500)
    delivery.faults.update({'retain', 'lose-reply'})
    submit = site_submission.submit_via_visible_site
    submissions = []

    def record_submission(gateway, account, task, **kwargs):
        submissions.append((task.id, delivery.office.verifications[-1]))
        return submit(gateway, account, task, **kwargs)

    monkeypatch.setattr(site_submission, 'submit_via_visible_site', record_submission)
    first = delivery.drop()
    expected_post = ('/api/complete-material', {'ids': [201]})
    assert delivery.posts == [expected_post]
    repeated = delivery.drop()

    assert first.outcome is repeated.outcome is Outcome.PENDING
    assert first.receipt_id == repeated.receipt_id == delivery.office.project
    assert first.material == repeated.material
    assert_original_saved(delivery, repeated)
    assert delivery.office.verifications == [(0, True), (1, True)]
    assert original_site_attempts(delivery) == [('201',)]
    assert submissions == [('201', (0, True))]
    assert delivery.posts == [expected_post]
    assert [row['matStatus'] for row in delivery.projects] == ['进行中', '待制作']


def test_office_account_swap_after_receipt_blocks_original_site_and_next_drop(delivery):
    old_member = delivery.office.member
    delivery.office.switch_on_verify = True

    first = delivery.drop()
    office_calls = delivery.office.calls[:]
    repeated = delivery.drop()

    assert first.outcome is Outcome.PENDING
    assert_original_saved(delivery, first)
    assert delivery.office.verifications == [(0, True)]
    assert delivery.office.member.username == old_member.username
    assert delivery.office.member.user_id != old_member.user_id
    assert delivery.pairs[-1][2] is False
    assert repeated.outcome is Outcome.BLOCKED and repeated.material is None
    assert delivery.office.calls == office_calls
    assert delivery.posts == []
    assert [row['matStatus'] for row in delivery.projects] == ['进行中', '待制作']
    assert original_site_attempts(delivery) == []
