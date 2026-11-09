"""History actions use explicit confirmation and the existing serialized worker."""
import threading

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from compact_ui import CompactPanel
from desktop_controller import FloatingController
from desktop_service import DesktopService


@pytest.fixture(scope='module')
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


@pytest.fixture
def panel(qapp, tmp_path):
    service = DesktopService(tmp_path / 'workbox.db')
    service.capture_message('匿名项目', '匿名批次')
    widget = CompactPanel(service)
    yield widget
    widget.close()
    widget.deleteLater()
    QApplication.processEvents()


def test_declining_history_confirmation_does_not_submit(panel, monkeypatch):
    calls = []
    panel.history_archive_requested.connect(lambda *args: calls.append(args))
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.No)
    panel.document_pane.history_archive_requested.emit({'project_id': 1, 'work_file_id': 7, 'name': '旧稿.txt'})
    assert calls == []


def test_confirming_history_names_file_and_submits_captured_identity(panel, monkeypatch):
    calls, prompts = [], []
    panel.history_archive_requested.connect(lambda *args: calls.append(args))
    def confirm(*args):
        prompts.append(args[2])
        return QMessageBox.Yes
    monkeypatch.setattr(QMessageBox, 'question', confirm)
    panel.document_pane.history_archive_requested.emit({'project_id': 1, 'work_file_id': 7, 'name': '旧稿.txt'})
    assert calls == [(1, 7)]
    assert '旧稿.txt' in prompts[0] and '交付' in prompts[0] and '历史' in prompts[0]


def test_busy_history_action_never_opens_confirmation(panel, monkeypatch):
    panel.set_busy(True)
    calls = []
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: calls.append(args))
    panel.document_pane.history_archive_requested.emit({'project_id': 1, 'work_file_id': 7, 'name': '旧稿.txt'})
    assert calls == []


def test_service_history_action_passes_confirmation_and_cancellation(tmp_path, monkeypatch):
    service = DesktopService(tmp_path / 'workbox.db')
    calls = []
    cancel = threading.Event()
    monkeypatch.setattr(service.workspaces, 'archive_existing',
                        lambda *args, **kwargs: calls.append((args, kwargs)) or {'archived_file_ids': [7]}, raising=False)
    result = service.archive_work_file(1, 7, confirmed=True, cancel_event=cancel, progress=lambda _: None)
    assert result == {'archived_file_ids': [7]}
    assert calls == [((1, 7), {'confirmed': True, 'cancel_event': cancel})]
    service.workspaces.document_versions.blocked = True
    with pytest.raises(RuntimeError):
        service.archive_work_file(1, 7, confirmed=True)
    assert len(calls) == 1


def test_controller_routes_history_action_through_existing_queue(qapp, tmp_path, monkeypatch):
    controller = FloatingController(DesktopService(tmp_path / 'workbox.db'), tray_enabled=False)
    assert controller.start()
    try:
        calls, queued = [], []
        monkeypatch.setattr(controller, 'enqueue', lambda caption, operation: queued.append((caption, operation)))
        monkeypatch.setattr(controller.service, 'archive_work_file',
                            lambda *args, **kwargs: calls.append((args, kwargs)), raising=False)
        controller.panel.history_archive_requested.emit(1, 7)
        assert len(queued) == 1
        assert calls == []
        cancel = threading.Event()
        queued[0][1](cancel_event=cancel)
        assert calls == [((1, 7), {'confirmed': True, 'cancel_event': cancel})]
    finally:
        controller.shutdown()
