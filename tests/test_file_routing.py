from concurrent.futures import CancelledError
from pathlib import Path
from threading import Event

import pytest
from PySide6.QtWidgets import QApplication

from desktop_controller import FloatingController
from desktop_service import DesktopService


@pytest.fixture
def service(tmp_path):
    result = DesktopService(tmp_path / 'data' / 'workbox.db')
    result.capture_message('1. 甲系统\n2. 乙系统', '本批')
    return result


def source_file(tmp_path, name='甲系统_代码（C#）.docx'):
    path = tmp_path / name
    path.write_bytes(b'isolated file content')
    return path


def test_code_filename_routes_without_selected_project(service, tmp_path):
    source = source_file(tmp_path)
    result = service.receive_file(source)
    assert result['file']['project_id'] == 1
    assert Path(result['file']['path']).read_bytes() == source.read_bytes()
    event = service.intake_log.list_recent()[0]
    assert event['status'] == 'copied' and event['manual'] is False
    assert event['candidate_ids'] == [1] and '代码' in event['basis']


def test_filename_directory_conflict_requires_choice(service):
    source = Path(service.workspaces.get_workspace(2)['path']) / '甲系统_代码（C++）.txt'
    source.write_bytes(b'isolated conflict')
    assert {p['id'] for p in service.file_candidates(source)} == {1, 2}
    with pytest.raises(ValueError, match='选择'):
        service.receive_file(source)
    assert source.read_bytes() == b'isolated conflict'
    assert service.intake_log.list_recent()[0]['status'] == 'needs-choice'
    result = service.receive_file(source, 1, manual=True)
    assert result['file']['project_id'] == 1
    event = service.intake_log.list_recent()[0]
    assert event['manual'] is True and '冲突' in event['basis']


def test_automatic_choice_is_rechecked_before_queued_copy(service, tmp_path):
    source = source_file(tmp_path)
    service.rename_project(1, '已改名系统')
    with pytest.raises(ValueError, match='重新选择'):
        service.receive_file(source, 1, manual=False)
    with service.db.connect() as connection:
        assert connection.execute('SELECT count(*) FROM work_files').fetchone()[0] == 0
    assert service.intake_log.list_recent()[0]['status'] == 'failed'


def test_archived_exact_name_does_not_degrade_to_another_active_project(service, tmp_path):
    service.capture_message('甲系统_v1.0', '旧批')
    service.bulk_update_projects([3], archived=True)
    source = source_file(tmp_path, '甲系统_v1.0_代码（C#）.txt')
    with pytest.raises(ValueError, match='选择'):
        service.receive_file(source)
    assert service.intake_log.list_recent()[0]['status'] == 'needs-choice'


def test_duplicate_attempt_has_new_log_without_duplicate_work_file(service, tmp_path):
    source = source_file(tmp_path)
    service.receive_file(source)
    assert service.receive_file(source)['duplicate']
    events = service.intake_log.list_recent()
    assert [e['status'] for e in events] == ['duplicate', 'copied']
    assert events[0]['work_file_id'] == events[1]['work_file_id']


def test_cancelled_copy_is_logged_and_keeps_source(service, tmp_path):
    source = source_file(tmp_path)
    event = Event()
    event.set()
    with pytest.raises(CancelledError):
        service.receive_file(source, cancel_event=event)
    assert service.intake_log.list_recent()[0]['status'] == 'cancelled'
    assert source.is_file()


def test_log_failure_rolls_back_copy_and_file_record(service, tmp_path, monkeypatch):
    source = source_file(tmp_path)
    original = service.intake_log.append
    def fail_success(path, status, **kwargs):
        if status == 'copied':
            raise RuntimeError('receipt unavailable')
        return original(path, status, **kwargs)
    monkeypatch.setattr(service.intake_log, 'append', fail_success)
    with pytest.raises(RuntimeError, match='receipt unavailable'):
        service.receive_file(source)
    with service.db.connect() as connection:
        assert connection.execute('SELECT count(*) FROM work_files').fetchone()[0] == 0
    assert not (Path(service.workspaces.get_workspace(1)['path']) / source.name).exists()
    assert source.is_file()


@pytest.mark.parametrize('known', [True, False])
def test_visible_selected_project_never_becomes_silent_fallback(service, tmp_path, monkeypatch, known):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    controller = FloatingController(service, tray_enabled=False)
    assert controller.start()
    source = source_file(tmp_path, '甲系统_代码（C#）.docx' if known else '未识别文件.docx')
    queued = []
    choices = []
    monkeypatch.setattr(controller, 'enqueue', lambda caption, operation, **kwargs: queued.append(operation))
    monkeypatch.setattr(controller.panel, 'choose_file_project', lambda filename: choices.append(filename))
    controller.panel.show()
    controller.panel.current_project = service.get_project(2)
    try:
        controller.receive({'kind': 'files', 'paths': [str(source)]})
        if known:
            assert not choices and len(queued) == 1
            result = queued[0]()
            assert result['file']['project_id'] == 1
        else:
            assert choices == [] and len(queued) == 1
            result = queued[0]()
            assert result['file']['project_id'] == 3
            assert service.get_project(3)['name'] == '未识别文件'
            assert service.get_project(3)['status'] == '待交付'
        assert source.is_file()
    finally:
        controller.shutdown()


def test_cancelling_queued_files_records_each_attempt(service, tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    controller = FloatingController(service, tray_enabled=False)
    assert controller.start()
    controller.monitor_timer.stop()
    monkeypatch.setattr(controller, '_next', lambda: None)
    first = source_file(tmp_path)
    second = source_file(tmp_path, '乙系统_代码（Python）.txt')
    try:
        controller.receive({'kind': 'files', 'paths': [str(first), str(second)]})
        assert len(controller.queue) == 2
        controller.cancel_jobs()
        events = service.intake_log.list_recent()
        assert len(events) == 2 and all(e['status'] == 'cancelled' for e in events)
        assert {e['project_id'] for e in events} == {1, 2}
        assert first.is_file() and second.is_file()
    finally:
        controller.shutdown()


def test_preflight_failure_is_logged_without_copy(service, tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    controller = FloatingController(service, tray_enabled=False)
    assert controller.start()
    source = source_file(tmp_path)
    def invalid_path(path):
        raise ValueError('拒绝目录联接')
    monkeypatch.setattr(service, 'file_routing', invalid_path)
    try:
        controller.receive({'kind': 'files', 'paths': [str(source)]})
        event = service.intake_log.list_recent()[0]
        assert event['status'] == 'failed' and '目录联接' in event['message']
        assert not controller.queue
    finally:
        controller.shutdown()
