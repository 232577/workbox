"""Cached checklist state and cancellation remain usable after reopening."""
import os
import sqlite3
import threading
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from checklist_ui import ChecklistDialog
from desktop_service import DesktopService
from native_files import cancelled
from theme import install_theme, set_theme


def wait_for(app, predicate):
    deadline = time.monotonic() + 15
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(10)
    assert predicate(), 'Anonymous checklist operation did not finish'
    app.processEvents()


def business_snapshot(path):
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as connection:
        connection.execute('BEGIN')
        tables = connection.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                    "AND name!='desktop_settings' ORDER BY name").fetchall()
        return {name: connection.execute('SELECT * FROM "' + name.replace('"', '""') + '"').fetchall()
                for (name,) in tables}


@pytest.fixture
def checklist(tmp_path):
    app = QApplication.instance() or QApplication([])
    service = DesktopService(tmp_path / 'data/workbox.db')
    service.receive_text('1. 匿名核对项目系统')
    install_theme(service)
    source = tmp_path / 'anonymous.csv'
    source.write_text('名称,日期\n' + '\n'.join(
        f'匿名核对项目系统,2026-11-{day:02d}' for day in range(1, 26)), encoding='utf-8')
    dialog = ChecklistDialog(service)
    dialog.resize(780, 640)
    dialog.table.setColumnWidth(0, 275)
    dialog.show()
    before = business_snapshot(service.db.path)
    dialog.load_path(source)
    wait_for(app, lambda: dialog.worker is None)
    assert len(dialog.results) == 25
    try:
        yield app, service, dialog, before
    finally:
        dialog.shutdown()
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.parametrize('close_method', ['button', 'escape', 'window'])
def test_reopen_preserves_import_results_filter_selection_and_scroll(checklist, close_method):
    app, service, dialog, before = checklist
    dialog.search.setText('匿名核对')
    dialog.view.setCurrentIndex(1)
    for mode in ('dark', 'light'):
        # Settle visible theme layout before measuring close/reopen preservation.
        set_theme(mode, service=service)
        app.processEvents()
        QTest.qWait(30)
        dialog.table.selectRow(20)
        dialog.table.verticalScrollBar().setValue(dialog.table.verticalScrollBar().maximum())
        saved = (list(dialog.results), dialog._selected(), dialog.table.verticalScrollBar().value())
        if close_method == 'button':
            dialog.close_button.click()
        elif close_method == 'escape':
            QTest.keyClick(dialog, Qt.Key_Escape)
        else:
            dialog.close()
        assert not dialog.isVisible()
        dialog.show()
        app.processEvents()
        assert dialog.results == saved[0] and dialog._selected() == saved[1]
        assert dialog.table.verticalScrollBar().value() == saved[2]
        assert dialog.table.columnWidth(0) == 275
        assert dialog.search.text() == '匿名核对' and dialog.view.currentData() == 'attention'
        assert dialog.import_button.isEnabled() and dialog.check_button.isEnabled()
    assert business_snapshot(service.db.path) == before


def test_cancel_then_reopen_restores_actions_without_applying_cancelled_results(checklist):
    app, service, dialog, before = checklist
    started = threading.Event()
    applied = []
    previous = list(dialog.results)
    def operation(*, cancel_event, progress):
        started.set()
        cancel_event.wait(10)
        cancelled(cancel_event)
    dialog._start(operation, applied.append, '匿名检查')
    wait_for(app, started.is_set)
    dialog.close_button.click()
    wait_for(app, lambda: dialog.worker is None and not dialog.isVisible())
    dialog.show()
    app.processEvents()
    assert applied == [] and dialog.results == previous
    assert dialog.import_button.isEnabled(), 'Import remains disabled after cancelled close/reopen'
    assert dialog.refresh_button.isEnabled() and dialog.check_button.isEnabled()
    assert dialog.cancel_button.isHidden()
    assert dialog.notice.property('tone') == 'neutral'
    assert dialog.notice.text() == '已取消本次操作，保留上次结果；未修改项目和文件。'
    assert business_snapshot(service.db.path) == before
