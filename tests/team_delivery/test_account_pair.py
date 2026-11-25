from types import SimpleNamespace

from workbox.team_delivery.application import _Worker
from workbox.team_delivery.contracts import Account, GatewayError, Outcome, RefreshDeferred, Snapshot, Stage, Task
from workbox.team_delivery.workbox_identity import WorkBoxAccount
from anonymous_document import make_document


def sessions(tmp_path):
    worker = _Worker('unused', tmp_path / 'shared', tmp_path / 'journal.sqlite')
    worker._material_root.mkdir()
    member = WorkBoxAccount('http://127.0.0.1:18770', '7', 'sample-workbox')
    actor = Account('observed-a', '材料甲', 'material', 'session-1')
    state = SimpleNamespace(member=member, actor=actor)
    task = Task('55', '匿名项目', actor.id, Stage.WORKING, 'v1')
    def snapshot():
        return Snapshot(state.actor, (task,) if state.actor == actor else ())
    worker._gateway = SimpleNamespace(current_account=lambda: state.actor, snapshot=snapshot,
                                      refresh_snapshot=snapshot, task=lambda number: task if number == task.id else None)
    worker._workbox = SimpleNamespace(current_account=lambda: state.member)
    signals = []
    worker.pair_ready.connect(lambda *args: signals.append(args))
    worker.refresh()
    return worker, state, signals


def test_confirm_two_different_names_then_detect_swap_or_logout(tmp_path):
    worker, state, signals = sessions(tmp_path)
    expected = (state.member, state.actor)
    assert signals[-1][:3] == (*expected, False)
    worker.confirm_accounts(expected)
    assert signals[-1][:3] == (*expected, True)
    # The replacement has the same visible username but a different WorkBox id.
    state.member = WorkBoxAccount(expected[0].origin, '99', expected[0].username)
    worker.confirm_accounts(expected)
    assert signals[-1][:3] == (state.member, state.actor, False)
    state.member = expected[0]
    worker.refresh()
    assert signals[-1][2] is False
    worker.confirm_accounts(expected)
    state.actor = None
    worker.refresh()
    assert not signals[-1][2]


def test_cannot_use_a_stale_confirmation_to_prepare_a_word(tmp_path):
    worker, state, _ = sessions(tmp_path)
    worker.confirm_accounts((state.member, state.actor))
    state.member = None
    results = []
    worker.result_ready.connect(results.append)
    worker.submit_file(str(tmp_path / 'missing.docx'), '55', state.actor)
    assert results[-1].outcome is Outcome.BLOCKED
    assert '账号' in results[-1].message and results[-1].material is None
    assert list((tmp_path / 'shared').iterdir()) == []


def test_login_switch_while_confirming_requires_a_fresh_confirmation(tmp_path):
    from workbox.team_delivery.pair_guard import PairGuard

    worker, state, signals = sessions(tmp_path)
    guard = worker._pair_guard = PairGuard()
    expected = (state.member, state.actor)

    def switching():
        guard.invalidate()
        return state.actor

    worker._gateway.current_account = switching
    worker.confirm_accounts(expected)
    assert not guard.confirmed() and not signals[-1][2]
    worker._gateway.current_account = lambda: state.actor
    worker.confirm_accounts(expected)
    assert guard.confirmed() and signals[-1][2]
    guard.invalidate()
    worker.confirm_accounts(expected)
    assert guard.confirmed() and signals[-1][2]


def test_failed_workbox_read_revokes_the_confirmation(tmp_path):
    worker, state, signals = sessions(tmp_path)
    expected = (state.member, state.actor)
    worker.confirm_accounts(expected)
    def unavailable():
        raise GatewayError('暂时无法核对 WorkBox 登录。')
    worker._workbox.current_account = unavailable
    worker.refresh()
    assert signals[-1][0] is None and not signals[-1][2]
    worker._workbox.current_account = lambda: state.member
    worker.refresh()
    assert signals[-1][:3] == (*expected, False)


def test_automatic_refresh_uses_passive_browser_path_regardless_of_prior_account(tmp_path):
    worker, state, signals = sessions(tmp_path)
    calls = []
    worker._last_account = None
    worker._gateway.poll_snapshot = lambda: calls.append('passive') or Snapshot(state.actor)
    worker._gateway.refresh_snapshot = lambda: calls.append('navigation') or Snapshot(state.actor)
    worker.refresh(automatic=True)
    assert calls == ['passive']
    assert signals[-1][:3] == (state.member, state.actor, False)


def test_deferred_refresh_preserves_preparation_until_an_actual_account_change(tmp_path):
    worker, state, signals = sessions(tmp_path)
    worker.confirm_accounts((state.member, state.actor))
    source = tmp_path / '匿名项目.docx'
    make_document(source)
    worker.submit_file(str(source), '55', state.actor)
    prepared = worker._prepared_target
    assert prepared is not None

    def delayed():
        raise RefreshDeferred('正在输入')

    worker._gateway.poll_snapshot = delayed
    deferred, failed, snapshots = [], [], []
    worker.read_deferred.connect(lambda *args: deferred.append(args))
    worker.read_failed.connect(failed.append)
    worker.snapshot_ready.connect(snapshots.append)
    worker.refresh(automatic=True)
    assert deferred == [(state.actor, '正在输入')]
    assert signals[-1][:3] == (state.member, state.actor, True)
    assert worker._prepared_target == prepared and prepared.material.path.is_file()
    assert not failed and not snapshots

    state.actor = Account('observed-b', '材料乙', 'material', 'session-2')
    worker.refresh(automatic=True)
    assert deferred[-1] == (state.actor, '正在输入')
    assert signals[-1][:3] == (state.member, state.actor, False)
    assert worker._prepared_target is None and prepared.material.path.is_file()
    state.actor = None
    worker.refresh(automatic=True)
    assert snapshots == [Snapshot(None)]


def test_switch_during_archive_hides_the_saved_path_and_disables_handoff(tmp_path, monkeypatch):
    worker, state, signals = sessions(tmp_path)
    worker.confirm_accounts((state.member, state.actor))
    original = tmp_path / '匿名项目.docx'
    make_document(original)
    store = worker._processor()._store
    archive = store.archive_word
    def finish_then_switch(*args):
        saved = archive(*args)
        state.member = None
        return saved
    monkeypatch.setattr(store, 'archive_word', finish_then_switch)
    results = []
    worker.result_ready.connect(results.append)
    worker.submit_file(str(original), '55', state.actor)
    assert not signals[-1][2] and results[-1].outcome is Outcome.BLOCKED
    assert results[-1].material is None and not results[-1].prepared and worker._prepared_target is None
    stored, = (tmp_path / 'shared').rglob('*.docx')
    assert stored.read_bytes() == original.read_bytes()


def test_manual_upload_offer_rechecks_both_accounts_and_the_selected_task(tmp_path):
    worker, state, _ = sessions(tmp_path)
    worker.confirm_accounts((state.member, state.actor))
    source = tmp_path / '匿名项目.docx'
    make_document(source)
    worker.submit_file(str(source), '55', state.actor)
    offers, filled = [], []
    worker.upload_offer.connect(lambda *args: offers.append(args))
    worker._workbox.manual_target = lambda title: 'test-uuid'
    def fill(title, project, material, *, check_accounts):
        check_accounts()
        filled.append((title, project, material.path))
    worker._workbox.fill_manual_form = fill
    worker.offer_manual_upload()
    assert len(offers) == 1
    worker.fill_manual_upload(*offers[0])
    assert filled == [('匿名项目', 'test-uuid', worker._prepared_target.material.path)]
    worker._gateway.task = lambda _: None
    worker.fill_manual_upload(*offers[0])
    assert len(filled) == 1
    state.member = None
    worker.fill_manual_upload(*offers[0])
    assert len(filled) == 1 and worker._prepared_target is None
