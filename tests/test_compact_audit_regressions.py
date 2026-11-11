"""Anonymous regressions for partial refresh, search bounds and bulk selection."""
from contextlib import contextmanager
import sqlite3
import sys

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from compact_ui import CompactPanel
from desktop_service import DesktopService


@pytest.fixture
def panel(tmp_path):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    service = DesktopService(tmp_path / 'anonymous.db')
    with service.db.connect() as connection:
        bid = connection.execute("INSERT INTO batches(name) VALUES ('anonymous audit')").lastrowid
        mid = connection.execute("INSERT INTO messages(batch_id,raw_text) VALUES (?,'anonymous audit')",
                                 (bid,)).lastrowid
        connection.executemany('INSERT INTO projects(message_id,batch_id,name) VALUES (?,?,?)',
                               [(mid, bid, f'anonymous project {i:04d}') for i in range(3)])
    widget = CompactPanel(service)
    yield widget
    widget.document_pane.shutdown()
    widget.hide()
    widget.deleteLater()
    app.processEvents()


def test_archive_error_clears_details_when_project_leaves_filter(panel, monkeypatch):
    panel.status_buttons['待开发'].click()
    panel.project_list.setCurrentRow(0)
    pid = panel.current_project['id']
    panel.service.set_status(pid, '开发中')

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError('anonymous archive read failure')

    monkeypatch.setattr(panel.service.snapshots, 'list_versions', fail)
    panel.refresh()
    assert not panel.project_list.selectedItems()
    assert panel.current_project is None
    assert panel.document_pane.project_id is None
    assert not panel.project_actions_button.isEnabled()
    assert 'anonymous archive read failure' in panel.notice.text()


@pytest.mark.parametrize('reader', ['pending_receipts', 'list_zips'])
@pytest.mark.parametrize('error_type', [sqlite3.OperationalError, OSError])
def test_auxiliary_read_error_syncs_committed_project_view_and_can_retry(panel, monkeypatch, reader, error_type):
    panel.status_buttons['待开发'].click()
    panel.project_list.setCurrentRow(0)
    pid = panel.current_project['id']
    panel.service.set_status(pid, '开发中')
    original = getattr(panel.service, reader)

    def fail(*args, **kwargs):
        raise error_type('anonymous auxiliary read failure')

    monkeypatch.setattr(panel.service, reader, fail)
    panel.refresh()
    assert not panel.project_list.selectedItems()
    assert panel.current_project is None
    assert panel.document_pane.project_id is None
    assert not panel.project_actions_button.isEnabled()
    assert 'anonymous auxiliary read failure' in panel.notice.text()
    monkeypatch.setattr(panel.service, reader, original)
    panel.service.set_status(pid, '待开发')
    panel.refresh()
    item = next(panel.project_list.item(i) for i in range(panel.project_list.count())
                if panel.project_list.item(i).data(Qt.UserRole) == pid)
    panel.project_list.setCurrentItem(item)
    assert panel.current_project['id'] == pid
    assert panel.document_pane.project_id == pid


def test_project_snapshot_failure_keeps_the_previous_list_and_details(panel, monkeypatch):
    panel.status_buttons['待开发'].click()
    panel.project_list.setCurrentRow(0)
    previous_item = panel.project_list.currentItem()
    previous_project = panel.current_project
    panel.service.set_status(previous_project['id'], '开发中')

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError('anonymous primary snapshot failure')

    monkeypatch.setattr(panel.service, 'project_workbench_snapshot', fail)
    with pytest.raises(sqlite3.OperationalError, match='anonymous primary snapshot failure'):
        panel.refresh()
    assert panel.project_list.currentItem() is previous_item
    assert panel.current_project is previous_project
    assert panel.current_project['status'] == '待开发'


def test_search_limit_does_not_escape_the_qt_slot(panel, monkeypatch):
    errors = []
    monkeypatch.setattr(sys, 'excepthook', lambda cls, value, tb: errors.append((cls.__name__, str(value))))
    panel.search.setText('x' * 1001)
    assert errors == [], errors
    assert panel.search.text() == 'x' * 1000
    assert panel.project_list.count() == 0
    panel.search.clear()
    assert panel.project_list.count() == 3


def test_archive_error_clears_unreadable_selected_details_and_can_retry(panel, monkeypatch):
    panel.project_list.setCurrentRow(0)
    pid = panel.current_project['id']
    original = panel.service.snapshots.list_versions

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError('anonymous selected archive read failure')

    monkeypatch.setattr(panel.service.snapshots, 'list_versions', fail)
    panel.refresh()
    assert panel.project_list.currentItem().data(Qt.UserRole) == pid
    assert panel.current_project is None
    assert panel.document_pane.project_id is None
    assert not panel.project_actions_button.isEnabled()
    assert 'anonymous selected archive read failure' in panel.notice.text()
    assert not panel.change_type_button.isEnabled()
    monkeypatch.setattr(panel.service.snapshots, 'list_versions', original)
    panel.refresh()
    assert panel.current_project['id'] == pid
    assert panel.document_pane.project_id == pid


def test_bulk_type_button_tracks_archival_state_after_refresh(panel):
    pid = panel.project_list.item(0).data(Qt.UserRole)
    panel.service.bulk_update_projects([pid], archived=True)
    panel.archive_filter.setCurrentIndex(panel.archive_filter.findData(None))
    panel.bulk_mode.setChecked(True)
    panel.project_list.selectAll()
    assert len(panel.project_list.selectedItems()) == 3
    assert not panel.change_type_button.isEnabled()
    panel.service.bulk_update_projects([pid], archived=False)
    panel.refresh()
    assert len(panel.project_list.selectedItems()) == 3
    assert panel.change_type_button.isEnabled()


def test_bulk_cached_button_state_cannot_bypass_action_validation(panel, monkeypatch):
    panel.bulk_mode.setChecked(True)
    panel.project_list.selectAll()
    ids = [item.data(Qt.UserRole) for item in panel.project_list.selectedItems()]
    assert panel.change_type_button.isEnabled()
    panel.service.bulk_update_projects([ids[0]], archived=True)
    writes = []
    monkeypatch.setattr(panel.service, 'set_project_types', lambda *args: writes.append(args))
    panel.assign_project_types(ids, None)
    assert not writes
    assert '请先恢复已归档或已删除的项目' in panel.notice.text()


def test_bulk_selection_uses_bounded_metadata_reads(panel, monkeypatch):
    with panel.service.db.connect() as connection:
        mid, bid = connection.execute('SELECT message_id,batch_id FROM projects LIMIT 1').fetchone()
        connection.executemany('INSERT INTO projects(message_id,batch_id,name) VALUES (?,?,?)',
                               [(mid, bid, f'anonymous bulk project {i:04d}') for i in range(497)])
    panel.refresh()
    calls = []
    connect = panel.service.db.connect

    @contextmanager
    def traced():
        calls.append(True)
        with connect() as connection:
            yield connection

    monkeypatch.setattr(panel.service.db, 'connect', traced)
    panel.bulk_mode.setChecked(True)
    calls.clear()
    panel.project_list.selectAll()
    assert len(panel.project_list.selectedItems()) == 500
    assert panel.change_type_button.isEnabled()
    assert len(calls) <= 10, f'Bulk selection opened {len(calls)} connections'
