from types import SimpleNamespace

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from workbox.team_delivery.application import IntakeController
from workbox.team_delivery.contracts import Account, Outcome, Snapshot, Stage, Task
from workbox.team_delivery.desktop import DesktopWindow


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
