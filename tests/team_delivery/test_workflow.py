from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from pathlib import Path
import os
import sqlite3
from threading import Barrier, Lock
from zipfile import ZipFile

import pytest

from workbox.team_delivery.contracts import Account, ActionUncertain, GatewayError, Handoff, Outcome, Snapshot, Stage, Task
from workbox.team_delivery.storage import Journal, MaterialStore
from workbox.team_delivery.workflow import WorkflowService


ACCOUNT = Account('employee-a', '匿名材料员', 'material', 'session-a')
TASK_ID = 'TEST-012'
TITLE = '匿名项目完整名称'
DOC_NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'


def docx(path: Path, body: str = 'anonymous'):
    with ZipFile(path, 'w') as archive:
        archive.writestr('[Content_Types].xml',
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '</Types>')
        archive.writestr('_rels/.rels',
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="r1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="word/document.xml"/></Relationships>')
        archive.writestr('word/document.xml',
            f'<w:document xmlns:w="{DOC_NS}"><w:body><w:p><w:r><w:t>{body}</w:t></w:r></w:p></w:body></w:document>')
    return path


class DemoGateway:
    def __init__(self, stage=Stage.READY):
        self.account = ACCOUNT
        self.project = Task(TASK_ID, TITLE, ACCOUNT.id, stage, 'initial')
        self.other_projects = ()
        self.events = {}
        self.calls = []
        self.begin_hook = None
        self.submit_hook = None
        self.committed_hook = None
        self.hide_completed = False

    def snapshot(self):
        tasks = (self.project, *self.other_projects)
        return Snapshot(self.account, tuple(task for task in tasks
                        if not self.hide_completed or task.stage is not Stage.COMPLETED))

    def task(self, task_id):
        return next((task for task in (self.project, *self.other_projects) if task.id == task_id), None)

    def guard(self, account, task, expected):
        if account != self.account or task != self.project or self.project.stage is not expected:
            raise GatewayError('任务已变更')

    def begin(self, account, task, request_id):
        self.calls.append(('begin', request_id))
        binding = ('begin', account.id, task.id, task.revision)
        if request_id in self.events:
            receipt = self.events[request_id]
            assert receipt[:4] == binding
            return receipt[4]
        self.guard(account, task, Stage.READY)
        resulting_revision = 'started'
        self.project = replace(task, stage=Stage.WORKING, revision=resulting_revision)
        self.events[request_id] = (*binding, resulting_revision)
        if self.begin_hook:
            self.begin_hook()
        return resulting_revision

    def submit(self, account, task, request_id, material):
        self.calls.append(('submit', request_id))
        if request_id in self.events:
            assert self.events[request_id] == ('submit', account.id, task.id, task.revision)
            return
        self.guard(account, task, Stage.WORKING)
        assert material.path.is_file()
        if self.submit_hook:
            self.submit_hook()
        self.events[request_id] = ('submit', account.id, task.id, task.revision)
        self.project = replace(task, stage=Stage.COMPLETED, revision='complete',
                               handoff=Handoff('receipt-1', account.id, request_id))
        if self.committed_hook:
            self.committed_hook()


@pytest.fixture
def setup(tmp_path):
    source = docx(tmp_path / f'{TITLE}.docx')
    return source, tmp_path / 'materials', tmp_path / 'intake.sqlite'


@pytest.mark.parametrize('stage, actions', [
    (Stage.READY, ['begin', 'submit']), (Stage.WORKING, ['submit']),
])
def test_file_is_verified_and_saved_before_original_actions(setup, stage, actions):
    source, root, journal = setup
    gateway = DemoGateway(stage)
    service = WorkflowService(gateway, root, journal)
    result = service.submit_file(source, None, ACCOUNT)

    assert result.outcome is Outcome.SUBMITTED
    assert [step for step, _ in gateway.calls] == actions
    assert result.task_id == TASK_ID and result.receipt_id == 'receipt-1'
    assert result.prepared is False
    assert result.material.path.is_relative_to(root)
    assert result.material.path.name == source.name
    assert result.material.path.read_bytes() == source.read_bytes()
    assert journal.is_file()


@pytest.mark.parametrize('stage', [Stage.READY, Stage.WORKING])
def test_prepare_only_saves_one_original_without_site_mutations(setup, stage):
    source, root, journal = setup
    gateway = DemoGateway(stage)
    original = gateway.project

    first = WorkflowService(gateway, root, journal, prepare_only=True).submit_file(source, None, ACCOUNT)
    repeated = WorkflowService(gateway, root, journal, prepare_only=True).submit_file(source, TASK_ID, ACCOUNT)

    assert first.outcome is repeated.outcome is Outcome.PENDING
    assert first.message == repeated.message == '原件已保存，待网页提交'
    assert first.task_id == repeated.task_id == TASK_ID
    assert first.receipt_id is repeated.receipt_id is None
    assert first.prepared is repeated.prepared is True
    assert first.prepared_task == repeated.prepared_task == original
    assert first.material == repeated.material
    assert first.material.path.read_bytes() == source.read_bytes()
    assert list(root.rglob('*.docx')) == [first.material.path]
    assert gateway.project == original and not gateway.calls and not gateway.events
    with closing(sqlite3.connect(journal)) as db:
        assert db.execute('SELECT actor_id, task_id, revision FROM prepared_materials').fetchall() == [
            (ACCOUNT.id, TASK_ID, original.revision)
        ]


def test_prepared_archive_is_stable_after_relogin_and_with_a_second_journal(setup):
    source, root, journal = setup
    first_gateway = DemoGateway()
    first = WorkflowService(first_gateway, root, journal, prepare_only=True).submit_file(source, None, ACCOUNT)
    fresh_gateway = DemoGateway()
    fresh_account = Account('new-observation-id', ACCOUNT.name, 'material', 'new-session')
    fresh_gateway.account = fresh_account
    fresh_gateway.project = replace(fresh_gateway.project, owner_id=fresh_account.id)
    for database in (journal, journal.with_name('second-desktop.sqlite')):
        repeated = WorkflowService(fresh_gateway, root, database, prepare_only=True).submit_file(source, TASK_ID, fresh_account)
        assert repeated.prepared and repeated.material == first.material
    assert list(root.rglob('*.docx')) == [first.material.path]
    assert not first_gateway.calls and not fresh_gateway.calls


def test_prepared_revisions_keep_original_name_and_both_contents(setup):
    source, root, journal = setup
    gateway = DemoGateway()
    service = WorkflowService(gateway, root, journal, prepare_only=True)
    first = service.submit_file(source, TASK_ID, ACCOUNT)
    original = source.read_bytes()
    revised = docx(source, 'revised anonymous content').read_bytes()
    second = service.submit_file(source, TASK_ID, ACCOUNT)

    assert first.prepared and second.prepared
    assert first.material.path.name == second.material.path.name == source.name
    assert first.material.path.parent != second.material.path.parent
    assert first.material.path.read_bytes() == original
    assert second.material.path.read_bytes() == revised
    source.write_bytes(original)
    assert service.submit_file(source, TASK_ID, ACCOUNT).material == first.material
    assert sorted(root.rglob('*.docx')) == sorted([first.material.path, second.material.path])
    assert not gateway.calls


def test_archive_refuses_to_overwrite_an_existing_version_with_wrong_bytes(setup):
    source, root, journal = setup
    gateway = DemoGateway()
    service = WorkflowService(gateway, root, journal, prepare_only=True)
    saved = service.submit_file(source, TASK_ID, ACCOUNT)
    saved.material.path.write_bytes(b'changed after archival')

    result = service.submit_file(source, TASK_ID, ACCOUNT)
    assert not result.prepared and result.material is None
    assert saved.material.path.read_bytes() == b'changed after archival'
    assert not gateway.calls


def test_published_archive_is_reused_after_local_log_failure(setup, monkeypatch):
    source, root, journal = setup
    gateway = DemoGateway()
    interrupted = WorkflowService(gateway, root, journal, prepare_only=True)

    def fail_log(*_):
        raise OSError('anonymous interruption')

    monkeypatch.setattr(interrupted._journal, 'record_preparation', fail_log)
    first = interrupted.submit_file(source, TASK_ID, ACCOUNT)
    retried = WorkflowService(gateway, root, journal, prepare_only=True).submit_file(source, TASK_ID, ACCOUNT)

    assert not first.prepared and first.outcome is Outcome.PENDING
    assert retried.prepared and retried.material == first.material
    assert list(root.rglob('*.docx')) == [retried.material.path]
    assert not gateway.calls


def test_prepared_archive_preserves_legacy_random_location_and_row(setup):
    source, root, journal = setup
    gateway = DemoGateway()
    old_store = MaterialStore(root)
    old_log = Journal(journal)
    with old_store.stage_word(source) as staged:
        old = old_log.reserve(ACCOUNT, gateway.project, staged, False)
        legacy = old_store.material(old, staged)
    prepared = WorkflowService(gateway, root, journal, prepare_only=True).submit_file(source, TASK_ID, ACCOUNT)
    other_desktop = journal.with_name('second-desktop.sqlite')
    repeated = WorkflowService(gateway, root, other_desktop, prepare_only=True).submit_file(source, TASK_ID, ACCOUNT)

    assert prepared.prepared and repeated.material == prepared.material
    assert prepared.material.path != legacy.path
    assert old_log.get(ACCOUNT.id, TASK_ID) == old
    assert legacy.path.read_bytes() == prepared.material.path.read_bytes() == source.read_bytes()
    assert not gateway.calls


def test_two_local_journals_publish_one_preparation_version(setup, monkeypatch):
    source, root, journal = setup
    services = [WorkflowService(DemoGateway(), root, path, prepare_only=True)
                for path in (journal, journal.with_name('second-desktop.sqlite'))]
    barrier = Barrier(2)
    publish = MaterialStore._publish_version

    def publish_together(store, staged, target):
        barrier.wait(timeout=5)
        return publish(store, staged, target)

    monkeypatch.setattr(MaterialStore, '_publish_version', publish_together)
    with ThreadPoolExecutor(2) as executor:
        results = list(executor.map(lambda service: service.submit_file(source, TASK_ID, ACCOUNT), services))

    assert all(result.prepared for result in results)
    assert results[0].material == results[1].material
    assert list(root.rglob('*.docx')) == [results[0].material.path]


def test_same_title_tasks_remain_distinct_even_with_identical_bytes(setup):
    source, root, journal = setup
    gateway = DemoGateway()
    gateway.other_projects = (replace(gateway.project, id='TEST-013'),)
    service = WorkflowService(gateway, root, journal, prepare_only=True)
    assert service.submit_file(source, None, ACCOUNT).outcome is Outcome.NEEDS_SELECTION
    first = service.submit_file(source, TASK_ID, ACCOUNT)
    second = service.submit_file(source, 'TEST-013', ACCOUNT)

    assert first.prepared and second.prepared
    assert first.material.sha256 == second.material.sha256
    assert first.material.path != second.material.path
    assert first.material.path.relative_to(root).parts[0] == TASK_ID
    assert second.material.path.relative_to(root).parts[0] == 'TEST-013'
    assert not gateway.calls


@pytest.mark.parametrize('legacy', [False, True])
def test_relogin_does_not_automatically_reuse_source_for_another_same_title_task(setup, legacy):
    source, root, journal = setup
    gateway = DemoGateway()
    if legacy:
        store = MaterialStore(root)
        with store.stage_word(source) as staged:
            old = Journal(journal).reserve(ACCOUNT, gateway.project, staged, False)
            saved = store.material(old, staged)
    else:
        saved = WorkflowService(gateway, root, journal, prepare_only=True).submit_file(source, None, ACCOUNT).material
    gateway.account = replace(ACCOUNT, id='new-observation-id', session='new-session')
    gateway.project = replace(gateway.project, id='TEST-013', owner_id=gateway.account.id)
    result = WorkflowService(gateway, root, journal, prepare_only=True).submit_file(source, None, gateway.account)

    assert result.outcome is Outcome.NEEDS_SELECTION and result.material is None
    assert list(root.rglob('*.docx')) == [saved.path]
    assert not gateway.calls


def test_concurrent_automatic_matches_in_one_journal_archive_only_the_admitted_task(setup):
    source, root, journal = setup
    first = DemoGateway()
    second = DemoGateway()
    second.account = replace(ACCOUNT, id='new-observation-id', session='new-session')
    second.project = replace(second.project, id='TEST-013', owner_id=second.account.id)

    def prepare(gateway):
        return WorkflowService(gateway, root, journal, prepare_only=True).submit_file(source, None, gateway.account)

    with ThreadPoolExecutor(2) as executor:
        results = list(executor.map(prepare, (first, second)))
    approved, = [result for result in results if result.prepared]
    refused, = [result for result in results if not result.prepared]
    assert refused.outcome is Outcome.NEEDS_SELECTION and refused.material is None
    assert list(root.rglob('*.docx')) == [approved.material.path]
    assert not first.calls and not second.calls


def test_pending_reservation_blocks_another_task_before_any_original_is_published(setup, monkeypatch):
    source, root, journal = setup
    first, second = DemoGateway(), DemoGateway()
    second.project = replace(second.project, id='TEST-013')
    service = WorkflowService(first, root, journal, prepare_only=True)
    other = WorkflowService(second, root, journal, prepare_only=True)
    archive_word = service._store.archive_word

    def attempt_another_task_before_archive(*args):
        assert not list(root.rglob('*.docx'))
        refused = other.submit_file(source, None, ACCOUNT)
        assert refused.outcome is Outcome.NEEDS_SELECTION and refused.material is None
        assert not (root / 'TEST-013').exists()
        return archive_word(*args)

    monkeypatch.setattr(service._store, 'archive_word', attempt_another_task_before_archive)
    approved = service.submit_file(source, None, ACCOUNT)
    assert approved.prepared and list(root.rglob('*.docx')) == [approved.material.path]
    assert not first.calls and not second.calls


def test_corrupted_pending_word_is_not_published(setup, monkeypatch):
    source, root, journal = setup
    gateway = DemoGateway()
    service = WorkflowService(gateway, root, journal, prepare_only=True)
    publish = service._store._publish_version

    def corrupt_before_publish(staged, target):
        staged.path.write_bytes(b'changed while waiting to publish')
        return publish(staged, target)

    monkeypatch.setattr(service._store, '_publish_version', corrupt_before_publish)
    result = service.submit_file(source, TASK_ID, ACCOUNT)
    assert not result.prepared and result.material is None
    assert not list(root.rglob('*.docx')) and not gateway.calls


def test_task_reassigned_while_reserving_is_not_published(setup, monkeypatch):
    source, root, journal = setup
    gateway = DemoGateway()
    service = WorkflowService(gateway, root, journal, prepare_only=True)
    reserve = service._journal.reserve_preparation

    def reassign_after_reservation(*args):
        accepted = reserve(*args)
        gateway.project = replace(gateway.project, owner_id='other-employee', revision='reassigned')
        return accepted

    monkeypatch.setattr(service._journal, 'reserve_preparation', reassign_after_reservation)
    result = service.submit_file(source, TASK_ID, ACCOUNT)
    assert not result.prepared and result.material is None
    assert not list(root.rglob('*.docx')) and not gateway.calls


def test_prepare_rejects_a_task_directory_redirected_outside_the_share(setup, tmp_path):
    source, root, journal = setup
    root.mkdir()
    elsewhere = tmp_path / 'unrelated-directory'
    elsewhere.mkdir()
    try:
        (root / TASK_ID).symlink_to(elsewhere, target_is_directory=True)
    except OSError as error:
        pytest.skip(f'Creating isolated directory symlinks is unavailable: {error}')
    gateway = DemoGateway()
    result = WorkflowService(gateway, root, journal, prepare_only=True).submit_file(source, TASK_ID, ACCOUNT)

    assert not result.prepared and result.material is None
    assert not list(elsewhere.iterdir()) and not gateway.calls


def test_archive_does_not_follow_an_existing_redirected_version(setup, tmp_path):
    source, root, journal = setup
    gateway = DemoGateway()
    service = WorkflowService(gateway, root, journal, prepare_only=True)
    first = service.submit_file(source, TASK_ID, ACCOUNT)
    version = first.material.path.parent
    elsewhere = tmp_path / 'unrelated-directory'
    version.rename(elsewhere)
    try:
        version.symlink_to(elsewhere, target_is_directory=True)
    except OSError as error:
        pytest.skip(f'Creating isolated directory symlinks is unavailable: {error}')
    result = service.submit_file(source, TASK_ID, ACCOUNT)

    assert not result.prepared and result.material is None
    assert list(elsewhere.iterdir()) == [elsewhere / source.name]
    assert (elsewhere / source.name).read_bytes() == source.read_bytes()
    assert not gateway.calls


def test_prepare_refuses_to_put_the_local_log_inside_the_shared_root(setup):
    source, root, _ = setup
    gateway = DemoGateway()
    result = WorkflowService(gateway, root, root / 'intake.sqlite', prepare_only=True).submit_file(source, TASK_ID, ACCOUNT)
    assert not result.prepared and not root.exists() and not gateway.calls


@pytest.mark.skipif(os.name != 'nt', reason='Windows drive and UNC paths')
def test_long_private_temp_name_can_publish_a_word_with_an_explorer_path(tmp_path):
    # Version and private temporary names add another 124 characters to this root.
    root = tmp_path / ('d' * max(1, 146 - len(str(tmp_path))))
    store = MaterialStore(root)
    source = docx(tmp_path / 'a.docx')
    with store.stage_word(source) as staged:
        saved = store.archive_word('00000055', staged)
    assert len(str(saved.path.parent / ('.intake-' + 'a' * 32 + '.tmp'))) >= 260
    assert not str(saved.path).startswith('\\\\?\\')
    assert saved.path.read_bytes() == source.read_bytes()
    assert list(saved.path.parent.iterdir()) == [saved.path]


def test_prepare_only_rejects_code_role_without_saving(setup):
    source, root, journal = setup
    gateway = DemoGateway()
    gateway.account = replace(ACCOUNT, role='code')
    result = WorkflowService(gateway, root, journal, prepare_only=True).submit_file(source, TASK_ID, gateway.account)
    assert result.outcome is Outcome.BLOCKED and result.material is None
    assert result.prepared is False
    assert not gateway.calls and not root.exists() and not journal.exists()


def test_prepare_only_does_not_present_completed_task_as_waiting_for_submit(setup):
    source, root, journal = setup
    gateway = DemoGateway(Stage.WORKING)
    assert WorkflowService(gateway, root, journal).submit_file(source, TASK_ID, ACCOUNT).outcome is Outcome.SUBMITTED
    gateway.calls.clear()

    result = WorkflowService(gateway, root, journal, prepare_only=True).submit_file(source, TASK_ID, ACCOUNT)
    assert result.outcome is Outcome.BLOCKED and result.receipt_id is None
    assert result.prepared is False
    assert '待网页提交' not in result.message and not gateway.calls


def test_missing_duplicate_and_conflicting_title_never_write(setup):
    source, root, journal = setup
    gateway = DemoGateway()
    service = WorkflowService(gateway, root, journal)
    incomplete = docx(source.with_name('匿名项目.docx'))
    assert service.submit_file(incomplete, None, ACCOUNT).outcome is Outcome.NEEDS_SELECTION
    gateway.other_projects = (replace(gateway.project, id='same-title-second-id'),)
    assert service.submit_file(source, None, ACCOUNT).outcome is Outcome.NEEDS_SELECTION
    gateway.other_projects = (replace(gateway.project, id='other-task', title='另一个项目'),)
    assert service.submit_file(source, 'other-task', ACCOUNT).outcome is Outcome.NEEDS_SELECTION
    assert not gateway.calls


def test_selecting_an_exact_task_id_resolves_duplicate_titles(setup):
    source, root, journal = setup
    gateway = DemoGateway(Stage.WORKING)
    gateway.other_projects = (replace(gateway.project, id='same-title-second-id'),)
    service = WorkflowService(gateway, root, journal)
    assert service.submit_file(source, None, ACCOUNT).outcome is Outcome.NEEDS_SELECTION
    assert service.submit_file(source, TASK_ID, ACCOUNT).outcome is Outcome.SUBMITTED


def test_reassignment_during_start_cannot_submit_saved_file(setup):
    source, root, journal = setup
    gateway = DemoGateway()
    gateway.begin_hook = lambda: setattr(gateway, 'project', replace(gateway.project, owner_id='employee-b', revision='reassigned'))
    result = WorkflowService(gateway, root, journal).submit_file(source, TASK_ID, ACCOUNT)
    assert result.outcome is Outcome.PENDING
    assert result.material.path.read_bytes() == source.read_bytes()
    assert [step for step, _ in gateway.calls] == ['begin']


def test_begin_receipt_version_rejects_work_returned_to_same_owner(setup):
    source, root, journal = setup

    class VersionedGateway(DemoGateway):
        def begin(self, account, task, request_id):
            revision = super().begin(account, task, request_id)
            self.project = replace(self.project, owner_id='employee-b', revision='reassigned')
            self.project = replace(self.project, owner_id=account.id, revision='reassigned-back')
            return revision

    gateway = VersionedGateway()
    result = WorkflowService(gateway, root, journal).submit_file(source, TASK_ID, ACCOUNT)
    assert result.outcome is Outcome.PENDING and result.material
    assert [step for step, _ in gateway.calls] == ['begin']


@pytest.mark.parametrize('unproved_revision', [None, '', ' ', 123])
def test_missing_begin_receipt_version_never_proceeds_to_submit(setup, unproved_revision):
    source, root, journal = setup

    class UnprovedGateway(DemoGateway):
        def begin(self, account, task, request_id):
            super().begin(account, task, request_id)
            return unproved_revision

    gateway = UnprovedGateway()
    result = WorkflowService(gateway, root, journal).submit_file(source, TASK_ID, ACCOUNT)
    assert result.outcome is Outcome.PENDING and result.material
    assert [step for step, _ in gateway.calls] == ['begin']


def test_restart_reuses_pending_submit_request_and_completed_readback(setup):
    source, root, journal = setup
    gateway = DemoGateway(Stage.WORKING)

    def lost_response():
        raise ActionUncertain('连接中断')

    gateway.submit_hook = lost_response
    first = WorkflowService(gateway, root, journal).submit_file(source, TASK_ID, ACCOUNT)
    assert first.outcome is Outcome.PENDING and first.material
    gateway.submit_hook = None
    second = WorkflowService(gateway, root, journal).submit_file(source, TASK_ID, ACCOUNT)
    third = WorkflowService(gateway, root, journal).submit_file(source, TASK_ID, ACCOUNT)
    assert second.outcome is third.outcome is Outcome.SUBMITTED
    assert second.material == third.material == first.material
    assert len(gateway.events) == 1
    assert gateway.calls == [('submit', gateway.project.handoff.request_id)] * 2


def test_timeout_after_remote_commit_uses_matching_handoff_without_replaying(setup):
    source, root, journal = setup
    gateway = DemoGateway(Stage.WORKING)
    gateway.hide_completed = True

    def lose_response():
        gateway.project = replace(gateway.project, owner_id='code-employee', revision='already-claimed')
        raise ActionUncertain('响应丢失')

    gateway.committed_hook = lose_response
    first = WorkflowService(gateway, root, journal).submit_file(source, None, ACCOUNT)
    repeated = WorkflowService(gateway, root, journal).submit_file(source, None, ACCOUNT)
    assert first.outcome is repeated.outcome is Outcome.SUBMITTED
    assert first.receipt_id == repeated.receipt_id == 'receipt-1'
    assert len(gateway.calls) == len(gateway.events) == 1


def test_crash_after_begin_is_reconciled_using_original_request(setup):
    source, root, journal = setup
    gateway = DemoGateway()

    class StoppedProcess(BaseException):
        pass

    def stop_before_readback():
        raise StoppedProcess

    gateway.begin_hook = stop_before_readback
    with pytest.raises(StoppedProcess):
        WorkflowService(gateway, root, journal).submit_file(source, TASK_ID, ACCOUNT)
    result = WorkflowService(gateway, root, journal).submit_file(source, TASK_ID, ACCOUNT)
    assert result.outcome is Outcome.SUBMITTED
    assert [action for action, _ in gateway.calls] == ['begin', 'begin', 'submit']
    assert gateway.calls[0] == gateway.calls[1]
    assert len(gateway.events) == 2


@pytest.mark.parametrize('mismatch', ['empty', 'actor', 'request', 'missing'])
def test_completion_requires_actor_and_request_proof(setup, mismatch):
    source, root, journal = setup
    gateway = DemoGateway(Stage.WORKING)

    def unrelated_completion():
        request = gateway.calls[-1][1]
        wrong_proof = {
            'empty': Handoff('', ACCOUNT.id, request),
            'actor': Handoff('someone-elses-receipt', 'employee-b', request),
            'request': Handoff('older-receipt', ACCOUNT.id, 'unrelated'),
            'missing': None,
        }[mismatch]
        gateway.project = replace(gateway.project, stage=Stage.COMPLETED, revision='other-action', handoff=wrong_proof)
        raise ActionUncertain('连接中断')

    gateway.submit_hook = unrelated_completion
    service = WorkflowService(gateway, root, journal)
    assert service.submit_file(source, TASK_ID, ACCOUNT).outcome is Outcome.PENDING
    assert service.submit_file(source, TASK_ID, ACCOUNT).outcome is Outcome.PENDING
    assert len(gateway.calls) == 1


@pytest.mark.parametrize('prepare_only', [False, True])
def test_same_owner_with_new_revision_after_storage_cannot_submit(setup, monkeypatch, prepare_only):
    source, root, journal = setup
    gateway = DemoGateway(Stage.WORKING)
    service = WorkflowService(gateway, root, journal, prepare_only=prepare_only)
    storage_method = 'archive_word' if prepare_only else 'material'
    save = getattr(service._store, storage_method)

    def save_then_change_version(*args):
        material = save(*args)
        gateway.project = replace(gateway.project, revision='new-cycle')
        return material

    monkeypatch.setattr(service._store, storage_method, save_then_change_version)
    result = service.submit_file(source, TASK_ID, ACCOUNT)
    assert result.outcome is Outcome.PENDING and result.material.path.is_file()
    assert result.prepared is False
    assert '待网页提交' not in result.message and not gateway.calls


def test_prepare_only_detects_session_switch_after_saving(setup, monkeypatch):
    source, root, journal = setup
    gateway = DemoGateway()
    service = WorkflowService(gateway, root, journal, prepare_only=True)
    save = service._store.archive_word

    def save_then_switch_session(task_id, staged):
        material = save(task_id, staged)
        gateway.account = replace(ACCOUNT, session='new-session')
        return material

    monkeypatch.setattr(service._store, 'archive_word', save_then_switch_session)
    result = service.submit_file(source, TASK_ID, ACCOUNT)
    assert result.outcome is Outcome.PENDING and result.material is None and result.task_id is None
    assert result.prepared is False
    assert '待网页提交' not in result.message and not gateway.calls
    assert len(list(root.rglob('*.docx'))) == 1


@pytest.mark.parametrize('read_error', [False, True])
def test_prepare_rechecks_session_after_task_read(setup, monkeypatch, read_error):
    source, root, journal = setup
    gateway = DemoGateway()
    service = WorkflowService(gateway, root, journal, prepare_only=True)
    task = gateway.task
    saved = False
    archive = service._store.archive_word

    def archive_word(*args):
        nonlocal saved
        result = archive(*args)
        saved = True
        return result

    def change_session_during_task_read(task_id):
        if not saved:
            return task(task_id)
        gateway.account = replace(ACCOUNT, id='other-employee', session='new-session')
        if read_error:
            raise GatewayError('读取时登录已变化')
        return None

    monkeypatch.setattr(service._store, 'archive_word', archive_word)
    monkeypatch.setattr(gateway, 'task', change_session_during_task_read)
    result = service.submit_file(source, TASK_ID, ACCOUNT)
    assert not result.prepared and result.material is None and result.task_id is None
    assert result.outcome is Outcome.PENDING and not gateway.calls


def test_session_switch_cannot_use_previous_material_or_request(setup):
    source, root, journal = setup
    gateway = DemoGateway()
    gateway.begin_hook = lambda: setattr(gateway, 'account', replace(ACCOUNT, session='different-session'))
    result = WorkflowService(gateway, root, journal).submit_file(source, TASK_ID, ACCOUNT)
    assert result.outcome is Outcome.PENDING and result.material is None and result.task_id is None
    assert [step for step, _ in gateway.calls] == ['begin']


def test_already_received_original_does_not_match_a_new_same_title_task(setup):
    source, root, journal = setup
    gateway = DemoGateway(Stage.WORKING)
    service = WorkflowService(gateway, root, journal)
    assert service.submit_file(source, None, ACCOUNT).outcome is Outcome.SUBMITTED
    gateway.other_projects = (gateway.project,)
    gateway.project = replace(gateway.project, id='new-same-title', stage=Stage.WORKING, handoff=None, revision='new')
    assert service.submit_file(source, None, ACCOUNT).outcome is Outcome.NEEDS_SELECTION
    assert len(gateway.calls) == 1


def test_two_service_instances_can_produce_only_one_handoff(setup):
    source, root, journal = setup
    gateway = DemoGateway(Stage.WORKING)
    lock = Lock()
    original_submit = gateway.submit

    def atomic_submit(*args):
        with lock:
            original_submit(*args)

    gateway.submit = atomic_submit
    services = [WorkflowService(gateway, root, journal), WorkflowService(gateway, root, journal)]
    with ThreadPoolExecutor(2) as executor:
        results = list(executor.map(lambda service: service.submit_file(source, TASK_ID, ACCOUNT), services))
    assert any(result.outcome is Outcome.SUBMITTED for result in results)
    assert len(gateway.events) == 1
    assert all(request_id == gateway.project.handoff.request_id for _, request_id in gateway.calls)
    assert WorkflowService(gateway, root, journal).submit_file(source, TASK_ID, ACCOUNT).outcome is Outcome.SUBMITTED


def test_arbitrary_zip_renamed_to_docx_is_rejected_before_mutation(setup):
    source, root, journal = setup
    with ZipFile(source, 'w') as archive:
        archive.writestr('not-a-document.txt', 'anonymous')
    gateway = DemoGateway()
    result = WorkflowService(gateway, root, journal).submit_file(source, TASK_ID, ACCOUNT)
    assert result.outcome is Outcome.BLOCKED and result.material is None
    assert not gateway.calls


def test_docx_with_invalid_xml_encoding_is_a_handled_file_error(setup):
    source, root, journal = setup
    replacement = source.with_suffix('.tmp')
    with ZipFile(source) as original, ZipFile(replacement, 'w') as archive:
        for member in original.namelist():
            data = original.read(member)
            if member == 'word/document.xml':
                data = b'<?xml version="1.0" encoding="UTF-7"?>' + data
            archive.writestr(member, data)
    replacement.replace(source)
    gateway = DemoGateway()
    result = WorkflowService(gateway, root, journal).submit_file(source, TASK_ID, ACCOUNT)
    assert result.outcome is Outcome.BLOCKED and not gateway.calls
