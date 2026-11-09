"""Real Qt workflow: import is not creation; cancel/failed input preserve results."""
from importlib import util, import_module
import time

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from desktop_service import DesktopService
from theme import install_theme


@pytest.fixture
def app():
    app = QApplication.instance() or QApplication([])
    yield app


def make_dialog(service):
    assert util.find_spec('checklist_ui') is not None, 'Checklist workspace not implemented'
    return import_module('checklist_ui').ChecklistDialog(service)


def wait(app, dialog):
    deadline = time.monotonic()+15
    while dialog.worker is not None and time.monotonic()<deadline:
        app.processEvents(); time.sleep(.01)
    assert dialog.worker is None, 'reader did not finish'
    app.processEvents()


def test_native_import_review_and_failed_retry_keep_project_data(app,tmp_path):
    s=DesktopService(tmp_path/'data/db.sqlite'); s.receive_text('1. 示例甲系统'); install_theme(s)
    before=s.db.counts(); d=make_dialog(s); d.show()
    try:
        path=tmp_path/'list.csv'; path.write_text('软件名称,日期\n示例甲系统,2026-11-09\n缺少示例系统,2026-11-09',encoding='utf-8')
        d.load_path(path); wait(app,d)
        assert d.table.rowCount()==2 and s.db.counts()==before
        assert [r['state'] for r in d.results]==['已关联','尚未收录']
        saved=list(d.results)
        d.load_path(tmp_path/'missing.csv'); wait(app,d)
        assert d.results==saved and d.table.rowCount()==2
        assert d.notice.property('tone')=='error'
        assert s.db.counts()==before
    finally:
        d.shutdown(); d.close(); d.deleteLater(); app.processEvents()


def test_theme_changes_preserve_inputs_and_widths(app,tmp_path):
    s=DesktopService(tmp_path/'data/db.sqlite'); install_theme(s)
    d=make_dialog(s); d.show()
    try:
        d.search.setText('示例')
        from theme import set_theme
        for mode in ('dark','light'):
            set_theme(mode, service=s)
            for width in (780,1120):
                d.resize(width,700);app.processEvents()
                assert d.search.text()=='示例'
                assert d.import_button.isVisible() and d.check_button.isVisible()
                assert d.check_button.geometry().right() <= d.width()
        assert not bool(d.windowFlags() & Qt.WindowStaysOnTopHint)
    finally:
        d.shutdown();d.close();d.deleteLater();app.processEvents()


def test_main_entry_is_visible_and_does_not_restore_task_groups(app,tmp_path):
    from compact_ui import CompactPanel
    s=DesktopService(tmp_path/'data/db.sqlite');install_theme(s);p=CompactPanel(s)
    try:
        p.resize(1180,760);p.show();app.processEvents()
        assert hasattr(p,'checklist_button'), 'Native checklist entry missing'
        assert p.checklist_button.text()=='名单核对'
        assert p.checklist_button.isVisible()
        assert p.task_groups_page is None
        assert not hasattr(p,'nav_task_groups')
    finally:
        p.document_pane.shutdown()
        if p.today_dialog is not None:p.today_dialog.shutdown()
        p.close();p.deleteLater();app.processEvents()


def test_shorter_reimport_never_uses_previous_table_indexes(app,tmp_path):
    s=DesktopService(tmp_path/'data/db.sqlite');install_theme(s);d=make_dialog(s)
    from checklist_reader import extract_entries
    from checklist_review import review_entries
    try:
        first=extract_entries({'name':'s','rows':[['甲系统'],['乙系统'],['丙系统']]},0)
        d.results=review_entries(s,first);d._render();d.table.selectRow(2)
        previous=d._selected()
        d.results=review_entries(s,first[:1]);d._render()
        assert d.table.rowCount()==1
        assert d._selected() is None or d._selected()['name']=='甲系统'
        assert previous['name']=='丙系统'
    finally:
        d.shutdown();d.close();d.deleteLater();app.processEvents()


def test_cancelled_reader_closes_after_finishing_without_project_writes(app,tmp_path):
    from native_files import cancelled
    s=DesktopService(tmp_path/'data/db.sqlite');install_theme(s);d=make_dialog(s);d.show()
    before=s.db.counts();applied=[]
    def operation(*,cancel_event,progress):
        while not cancel_event.wait(.01):pass
        cancelled(cancel_event)
    try:
        d._start(operation,applied.append,'正在读取')
        d.reject();wait(app,d)
        assert not d.isVisible() and applied==[]
        assert s.db.counts()==before
    finally:
        d.shutdown();d.close();d.deleteLater();app.processEvents()


def test_search_return_does_not_start_import_or_material_read(app,tmp_path,monkeypatch):
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QFileDialog
    calls=[]
    monkeypatch.setattr(QFileDialog,'getOpenFileName',lambda *a,**k:(calls.append(True) or ('','')))
    s=DesktopService(tmp_path/'data/db.sqlite');install_theme(s);d=make_dialog(s);d.show();app.processEvents()
    try:
        d.search.setFocus();d.search.setText('示例')
        QTest.keyClick(d.search,Qt.Key_Return);app.processEvents()
        assert calls==[] and d.worker is None
        assert not d.import_button.autoDefault()
    finally:
        d.shutdown();d.close();d.deleteLater();app.processEvents()
