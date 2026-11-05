"""内嵌归档分类页，全部使用匿名库与 offscreen Qt。"""
import importlib
import importlib.util
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from archive_classification import ArchiveClassification
from database import Database


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def service(tmp_path):
    db = Database(tmp_path / 'anonymous.db')
    db.create_capture('匿名项目', '匿名批次', [('匿名项目' * 30, False), ('未知项目', False)])
    with db.connect() as connection:
        zip_id = connection.execute("INSERT INTO zips(project_id,path,filename,size,sha256,mtime_ns) VALUES (1,'Z:/anonymous/archive.zip','archive.zip',1,?,0)", ('a' * 64,)).lastrowid
        connection.execute("INSERT INTO zip_project_file_counts(zip_id,project_id,file_count,decided_status,rule) VALUES (?,1,4,'已交付','zip-file-count-v1')", (zip_id,))
        connection.execute("INSERT INTO delivery_events(project_id,project_name,batch_name,kind,zip_id,archive_path) VALUES (1,'匿名项目','匿名批次','manual-zip',?,'Z:/anonymous/archive.zip')", (zip_id,))
    return SimpleNamespace(db=db, archive_classification=ArchiveClassification(db))


def pane(service):
    return importlib.import_module('archive_classification_ui').ArchiveClassificationPane(service)


def settle(app, page):
    for _ in range(5):
        worker = page.worker
        if worker:
            assert worker.wait(10000)
        app.processEvents()
        if page.worker is None:
            return
    raise AssertionError('后台任务没有结束')


def test_inline_pane_module_exists():
    assert importlib.util.find_spec('archive_classification_ui') is not None


def test_recent_operations_survive_new_backend_and_are_read_only(service):
    classifier = service.archive_classification
    result = classifier.apply(classifier.preview()['token'], confirmed=True, enable_future=False)
    with service.db.connect() as connection:
        before = '\n'.join(connection.iterdump())
    operations = ArchiveClassification(service.db).recent_operations()
    assert operations[0]['operation_id'] == result['operation_id']
    assert operations[0]['changed_count'] == 1 and not operations[0]['undone']
    with service.db.connect() as connection:
        assert '\n'.join(connection.iterdump()) == before
    classifier.undo(result['operation_id'], confirmed=True)
    assert classifier.recent_operations()[0]['undone'] is True


def test_dirty_policy_requires_save_and_apply_never_enables_future_implicitly(app, service, monkeypatch):
    page = pane(service)
    changed = []
    page.statistics_changed.connect(lambda: changed.append(True))
    page.refresh(); settle(app, page)
    assert not page.future_enabled.isChecked()
    page.preview_existing(); settle(app, page)
    assert page.apply_button.isEnabled()
    page.minimum_files.setValue(5)
    assert not page.preview_button.isEnabled() and not page.apply_button.isEnabled()
    page.minimum_files.setValue(4)
    page.save_policy(); settle(app, page)
    page.preview_existing(); settle(app, page)
    monkeypatch.setattr(QMessageBox, 'question', lambda *args, **kwargs: QMessageBox.Yes)
    page.apply_preview(); settle(app, page)
    assert service.archive_classification.policy()['enabled'] is False
    assert changed == [True]
    assert page.operation_choice.count() >= 1
    page.shutdown()


def test_unknown_preview_and_narrow_long_names_are_readable(app, service):
    page = pane(service)
    page.resize(580, 580); page.show()
    settle(app, page)
    page.preview_existing(); settle(app, page)
    assert page.table.rowCount() == 2
    assert page.table.item(1, 3).text() == '未知'
    assert page.table.item(0, 0).toolTip() == '匿名项目' * 30
    for control in (page.save_button, page.preview_button, page.apply_button, page.undo_button):
        assert control.width() >= control.fontMetrics().horizontalAdvance(control.text()) + 12
        assert control.mapTo(page, control.rect().topRight()).x() < page.width()
    assert page.table.height() >= 130
    page.shutdown(); page.close()


def test_failed_and_stale_apply_leave_no_success_signal(app, service, monkeypatch):
    page = pane(service)
    changed = []
    page.statistics_changed.connect(lambda: changed.append(True))
    page.refresh(); settle(app, page)
    page.preview_existing(); settle(app, page)
    service.db.set_project_types([1], None)
    monkeypatch.setattr(QMessageBox, 'question', lambda *args, **kwargs: QMessageBox.Yes)
    page.apply_preview(); settle(app, page)
    assert '过期' in page.notice.text()
    assert not page.apply_button.isEnabled() and not changed
    page.shutdown()


def test_external_busy_and_shutdown_do_not_abandon_worker(app, service):
    page = pane(service)
    page.refresh(); settle(app, page)
    page.set_busy(True)
    assert not page.preview_button.isEnabled() and not page.save_button.isEnabled()
    page.set_busy(False)
    assert page.preview_button.isEnabled()
    page.preview_existing()
    page.shutdown()
    assert page.worker is None
    assert page.table.rowCount() == 2
    page.close()


def test_undo_after_reopening_uses_backend_history(app, service, monkeypatch):
    result = service.archive_classification.apply(service.archive_classification.preview()['token'], confirmed=True, enable_future=False)
    page = pane(service)
    page.refresh(); settle(app, page)
    assert page.operation_choice.currentData() == result['operation_id']
    monkeypatch.setattr(QMessageBox, 'question', lambda *args, **kwargs: QMessageBox.Yes)
    page.undo_selected(); settle(app, page)
    assert '已恢复 1 项' in page.notice.text()
    assert not page.undo_button.isEnabled()
    page.shutdown()


def test_read_failure_empty_data_and_confirmation_cancel(app, service, monkeypatch):
    page = pane(service)
    original = service.archive_classification.policy
    monkeypatch.setattr(service.archive_classification, 'policy', lambda: (_ for _ in ()).throw(ValueError('匿名读取失败')))
    page.refresh(); settle(app, page)
    assert '匿名读取失败' in page.notice.text()
    assert not page.preview_button.isEnabled()
    monkeypatch.setattr(service.archive_classification, 'policy', original)
    page.refresh(); settle(app, page)
    page.preview_existing(); settle(app, page)
    monkeypatch.setattr(QMessageBox, 'question', lambda *args, **kwargs: QMessageBox.No)
    page.apply_preview()
    assert page.worker is None
    assert service.archive_classification.recent_operations() == []
    page.shutdown()


def test_busy_close_is_blocked_and_return_signal_is_inline(app, service):
    import threading
    from PySide6.QtGui import QCloseEvent
    page = pane(service)
    page.refresh(); settle(app, page)
    returned = []
    page.return_requested.connect(lambda: returned.append(True))
    page.return_button.click()
    assert returned == [True]
    release = threading.Event()
    page._run('load', lambda: (release.wait(10), page._read_state())[1])
    event = QCloseEvent()
    page.closeEvent(event)
    assert not event.isAccepted() and page.worker is not None
    release.set()
    page.shutdown()
    assert page.worker is None


def test_recent_batches_are_not_hidden_by_many_automatic_observations(service):
    classifier = service.archive_classification
    initial = classifier.apply(classifier.preview()['token'], confirmed=True)
    from archive_classification import classify_new_delivery
    with service.db.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        for _ in range(31):
            event_id = connection.execute("INSERT INTO delivery_events(project_id,project_name,batch_name,kind,zip_id,archive_path) SELECT project_id,project_name,batch_name,kind,zip_id,archive_path FROM delivery_events ORDER BY id LIMIT 1").lastrowid
            classify_new_delivery(connection, event_id)
    assert classifier.recent_operations(limit=1)[0]['operation_id'] == initial['operation_id']


def test_apply_refresh_waits_for_external_busy_then_updates_undo_list(app, service, monkeypatch):
    page = pane(service)
    page.refresh(); settle(app, page)
    page.preview_existing(); settle(app, page)
    page.statistics_changed.connect(lambda: page.set_busy(True))
    monkeypatch.setattr(QMessageBox, 'question', lambda *args, **kwargs: QMessageBox.Yes)
    page.apply_preview(); settle(app, page)
    assert page.operation_choice.count() == 0
    page.set_busy(False); settle(app, page)
    assert page.operation_choice.count() == 1
    assert page.undo_button.isEnabled()
    assert '已调整 1 项' in page.notice.text()
    page.shutdown()


def test_initial_show_blocked_by_busy_loads_after_unlock_without_overwriting_dirty_form(app, service):
    page = pane(service)
    page.set_busy(True)
    page.show(); app.processEvents()
    page.set_busy(False); settle(app, page)
    assert page.preview_button.isEnabled()
    page.minimum_files.setValue(7)
    page.set_busy(True)
    page.refresh()
    page.set_busy(False); settle(app, page)
    assert page.minimum_files.value() == 7
    assert not page.preview_button.isEnabled()
    page.shutdown(); page.close()
