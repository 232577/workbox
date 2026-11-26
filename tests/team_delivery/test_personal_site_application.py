from dataclasses import replace
from types import SimpleNamespace

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QInputDialog

from workbox.team_delivery.application import IntakeController
from workbox.team_delivery.contracts import Account, Outcome, Snapshot, Stage, Task
from workbox.team_delivery.desktop import DesktopWindow


@pytest.fixture
def personal_intake(tmp_path):
    app = QApplication.instance() or QApplication([])
    page = DesktopWindow()
    controller = IntakeController(page, 'http://127.0.0.1:1', None, tmp_path / 'office.sqlite',
                                  settings=tmp_path / 'settings.json', gateway=SimpleNamespace(close=lambda: None))
    actor = Account('session-a', '匿名材料', 'material', 'a', 'material-user')
    requested = []
    controller.personal_file_requested.connect(lambda *args: requested.append(args))
    controller.start()
    controller.enable_personal_workspace()
    try:
        yield controller, actor, requested
    finally:
        controller.stop()
        page.close()
        app.processEvents()


@pytest.mark.parametrize('suffix', ['V1.0', ' V1.0', '（Ｖ１．０）', ' 1.0'])
def test_personal_word_without_version_keeps_exact_original_task(personal_intake, tmp_path, suffix):
    controller, actor, requested = personal_intake
    task = Task('55', '匿名系统' + suffix, actor.id, Stage.READY, 'ready-revision')
    other = Task('56', '匿名系统V2.0', 'other-owner', Stage.READY, 'other-revision')
    completed = Task('57', '匿名系统V3.0', actor.id, Stage.COMPLETED, 'completed-revision')
    controller._show_snapshot(Snapshot(actor, (task, other, completed)))
    source = tmp_path / '匿名系统.docx'
    source.write_bytes(b'fixture')

    controller.submit_file(str(source), None)

    assert len(requested) == 1
    assert requested[0][:2] == (str(source), actor)
    assert requested[0][2] is task


@pytest.mark.parametrize(('filename', 'plain_title'), [('匿名系统', '匿名系统'), ('匿名App', '匿名 ＡＰＰ')])
def test_personal_word_prefers_full_name_over_version_fallback(personal_intake, tmp_path, filename, plain_title):
    controller, actor, requested = personal_intake
    plain = Task('55', plain_title, actor.id, Stage.READY, 'plain-revision')
    versioned = Task('56', filename + 'V1.0', actor.id, Stage.READY, 'versioned-revision')
    controller._show_snapshot(Snapshot(actor, (versioned, plain)))
    source = tmp_path / (filename + '.docx')
    source.write_bytes(b'fixture')

    controller.submit_file(str(source), '56')
    assert requested == []
    controller.submit_file(str(source), None)
    assert requested == [(str(source), actor, plain)]


@pytest.mark.parametrize(('title', 'filename'), [
    ('匿名系统V1.0', '匿名系统V2.0'),
    ('匿名系统V1.0V2.0', '匿名系统V1.0'),
    ('匿名系统1.0', '匿名系统'),
])
def test_personal_word_does_not_route_mismatched_names(personal_intake, tmp_path, title, filename):
    controller, actor, requested = personal_intake
    task = Task('55', title, actor.id, Stage.READY, 'ready-revision')
    controller._show_snapshot(Snapshot(actor, (task,)))
    source = tmp_path / (filename + '.docx')
    source.write_bytes(b'fixture')

    controller.submit_file(str(source), None)
    controller.submit_file(str(source), task.id)
    assert requested == []


def test_personal_version_chooser_uses_full_titles_and_selected_task(personal_intake, tmp_path, monkeypatch):
    controller, actor, requested = personal_intake
    first = Task('55', '匿名系统V1.0', actor.id, Stage.READY, 'first-revision')
    second = Task('56', '匿名系统 2.0', actor.id, Stage.RETURNED, 'second-revision')
    controller._show_snapshot(Snapshot(actor, (first, second)))
    source = tmp_path / '匿名系统.docx'
    source.write_bytes(b'fixture')

    def choose(_parent, _title, _prompt, items, *_):
        assert requested == []
        assert any(first.title in item and item.endswith('#55') for item in items)
        selected = next(item for item in items if second.title in item and item.endswith('#56'))
        return selected, True

    monkeypatch.setattr(QInputDialog, 'getItem', choose)
    controller.submit_file(str(source), None)

    assert len(requested) == 1
    assert requested[0][:2] == (str(source), actor)
    assert requested[0][2] is second


@pytest.mark.parametrize('change', ['account', 'snapshot', 'office-login'])
def test_personal_version_chooser_discards_selection_after_change(personal_intake, tmp_path, monkeypatch, change):
    controller, actor, requested = personal_intake
    first = Task('55', '匿名系统V1.0', actor.id, Stage.READY, 'first-revision')
    second = Task('56', '匿名系统V2.0', actor.id, Stage.READY, 'second-revision')
    controller._show_snapshot(Snapshot(actor, (first, second)))
    source = tmp_path / '匿名系统.docx'
    source.write_bytes(b'fixture')
    choices, team_requests = [], []

    def choose(_parent, _title, _prompt, items, *_):
        selected = next(item for item in items if second.title in item and item.endswith('#56'))
        choices.append(selected)
        if change == 'account':
            controller._show_snapshot(Snapshot(replace(actor, session='changed-session'), (first, second)))
        elif change == 'snapshot':
            controller._show_snapshot(Snapshot(actor, (first, replace(second, revision='new-revision'))))
        else:
            controller._office_connection = SimpleNamespace(login_state=lambda: 'signed-in', close=lambda: None)
            controller._office_login_state_changed('signed-in')
        return selected, True

    def enqueue(command):
        if command[0] == controller._worker.submit_file:
            team_requests.append(command[1])
        else:
            enqueue_original(command)

    enqueue_original = controller._commands.put
    with monkeypatch.context() as patch:
        patch.setattr(QInputDialog, 'getItem', choose)
        patch.setattr(controller._commands, 'put', enqueue)
        controller.submit_file(str(source), None)

    assert len(choices) == 1
    assert requested == []
    assert team_requests == []


def test_personal_workspace_uses_current_material_snapshot_and_exact_task(tmp_path):
    app = QApplication.instance() or QApplication([])
    page = DesktopWindow()
    controller = IntakeController(page, 'http://127.0.0.1:1', None, tmp_path / 'office.sqlite',
                                  settings=tmp_path / 'settings.json', gateway=SimpleNamespace(close=lambda: None))
    actor = Account('session-a', '匿名材料', 'material', 'a', 'material-user')
    task = Task('55', '匿名项目', actor.id, Stage.READY, 'ready-revision')
    snapshot = Snapshot(actor, (task,))
    received, requested = [], []
    try:
        controller.start()
        controller.personal_snapshot_changed.connect(received.append)
        controller.personal_file_requested.connect(lambda *args: requested.append(args))
        controller.enable_personal_workspace()
        controller._show_snapshot(snapshot)
        controller._show_snapshot(snapshot)
        assert received[-2:] == [snapshot, snapshot]
        assert controller.current_personal_snapshot() == snapshot

        source = tmp_path / '匿名项目.docx'
        source.write_bytes(b'fixture')
        controller.submit_file(str(source), '56')
        assert requested == []
        controller.submit_file(str(source), '55')
        assert requested == [(str(source), actor, task)]

        state = SimpleNamespace(value='unknown')
        controller._office_connection = SimpleNamespace(login_state=lambda: state.value,
                                                        current_account=lambda: None, close=lambda: None)
        controller._office_login_state_changed('unknown')
        assert controller.current_personal_snapshot() is None and received[-1] is None
        state.value = 'signed-out'
        controller._office_login_state_changed('signed-out')
        assert controller.current_personal_snapshot() == snapshot
        controller._show_read_failure('fixture unavailable')
        assert controller.current_personal_snapshot() is None and received[-1] is None
    finally:
        controller.stop()
        page.close()
        app.processEvents()


def test_personal_recovery_reads_exact_completed_task_outside_current_list(tmp_path):
    app = QApplication.instance() or QApplication([])
    page = DesktopWindow()
    actor = Account('session-a', '匿名材料', 'material', 'a', 'material-user')
    other = Task('56', '其他项目', actor.id, Stage.READY, 'other-ready')
    verified = Task('55', '匿名项目', actor.id, Stage.COMPLETED, 'completed-revision')
    reads, results = [], []

    def read(account, task):
        reads.append((account, task.id, task.title))
        return verified

    gateway = SimpleNamespace(base_url='http://127.0.0.1:1', current_account=lambda: actor,
                              read_material_task=read, close=lambda: None)
    controller = IntakeController(page, gateway.base_url, None, tmp_path / 'office.sqlite',
                                  settings=tmp_path / 'settings.json', gateway=gateway)
    try:
        controller.start()
        controller.personal_delivery_finished.connect(results.append)
        controller.enable_personal_workspace()
        controller._show_snapshot(Snapshot(actor, (other,)))
        assert not controller.submit_personal_delivery(actor.username, '56', other.title, '2', source_revision='stale')
        assert not controller.submit_personal_delivery('different-user', '55', verified.title, '1', verify_only=True)
        assert not controller.submit_personal_delivery(actor.username, '55', verified.title, '1',
                                                       verify_only=True, resume_working=True)
        assert reads == []
        assert controller.submit_personal_delivery(actor.username, '55', verified.title, '1', verify_only=True,
                                                   source_revision='stale')
        for _ in range(150):
            if results:
                break
            QTest.qWait(20)
        assert reads == [(actor, '55', verified.title)]
        assert len(results) == 1 and results[0].outcome is Outcome.SUBMITTED
        assert results[0].delivery_id == '1' and results[0].receipt_id == verified.revision
    finally:
        controller.stop()
        page.close()
        app.processEvents()


def test_manual_refresh_uses_only_the_binding_snapshot_in_personal_mode(tmp_path):
    app = QApplication.instance() or QApplication([])
    page = DesktopWindow()
    actor = Account('session-a', '匿名材料', 'material', 'a', 'material-user')
    fresh = Snapshot(actor, (Task('55', '匿名项目', actor.id, Stage.READY, 'fresh'),))
    gateway = SimpleNamespace(refresh_snapshot=lambda: fresh, close=lambda: None)
    controller = IntakeController(page, 'http://127.0.0.1:1', None, tmp_path / 'office.sqlite',
                                  settings=tmp_path / 'settings.json', gateway=gateway, personal_sync_enabled=True)
    bound, legacy = [], []
    try:
        controller.start()
        controller.enable_personal_workspace()
        controller._show_snapshot(Snapshot(actor))
        controller.personal_snapshot_changed.connect(bound.append)
        controller.personal_sync_requested.connect(legacy.append)
        page.personal_sync_requested.emit()
        for _ in range(150):
            if not controller._busy:
                break
            QTest.qWait(20)
        assert bound == [fresh]
        assert legacy == []
        assert '按任务编号同步' in page._sync_notice.text()
    finally:
        controller.stop()
        page.close()
        app.processEvents()
