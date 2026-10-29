"""代交只排除工作量，真实交付状态、文件与历史保持。"""
from datetime import datetime, timezone
import time
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox, QAbstractItemView
import database
from desktop_service import DesktopService
from delivery_ui import TodayDeliveryDialog

@pytest.fixture(scope='module')
def qapp():
    app=QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app

@pytest.fixture
def dialog(qapp,tmp_path,monkeypatch):
    class Frozen(datetime):
        @classmethod
        def now(cls,tz=None):
            moment=datetime(2026,10,29,4,tzinfo=timezone.utc)
            return moment.astimezone(tz) if tz else moment.replace(tzinfo=None)
    monkeypatch.setattr(database,'datetime',Frozen)
    service=DesktopService(tmp_path/'data/workbox.db')
    service.receive_text('1. 代交甲平台\n2. 自做乙平台')
    archive=tmp_path/'archive';archive.mkdir()
    (archive/'说明.txt').write_text('保留原文件',encoding='utf-8')
    with service.db.connect() as c:
        c.execute("UPDATE projects SET status='已交付',delivered_at='2026-10-29T02:00:00Z'")
        for pid,name,at in ((1,'代交甲平台','2026-10-28T02:00:00Z'),(1,'代交甲平台','2026-10-29T02:00:00Z'),(2,'自做乙平台','2026-10-29T03:00:00Z')):
            c.execute('INSERT INTO delivery_events(project_id,project_name,batch_name,kind,archive_path,occurred_at) VALUES(?,?,?,\'manual-files\',?,?)',(pid,name,'本批',str(archive),at))
    result=TodayDeliveryDialog(service);result.show();qapp.processEvents()
    yield result
    result.shutdown()

def select(dialog,pid):
    row=next(i for i in range(dialog.table.rowCount()) if dialog.table.item(i,0).data(Qt.UserRole)==pid)
    dialog.table.selectRow(row)

def finish(dialog):
    until=time.monotonic()+5
    while dialog.worker is not None and time.monotonic()<until:
        QApplication.processEvents();time.sleep(.01)
    QApplication.processEvents()
    assert dialog.worker is None

def test_exclude_and_restore_keep_delivered_state_and_files(dialog,monkeypatch):
    assert hasattr(dialog,'exclude_count_button')
    select(dialog,2)
    before=dialog.service.get_project(2)
    seen=[]
    monkeypatch.setattr(QMessageBox,'question',lambda *args:seen.append(args) or QMessageBox.Yes)
    dialog.reason.setText('替同事交付')
    dialog.exclude_count_button.click();finish(dialog)
    assert seen[-1][-1]==QMessageBox.No and '状态保持不变' in seen[-1][2]
    assert dialog.service.db.today_delivery_summary()['project_count']==0
    assert dialog.service.get_project(2)==before
    assert '不计工作量' in dialog.table.item(dialog.table.currentRow(),3).text()
    assert '不计工作量' in dialog.details.item(0,1).text()
    assert dialog.include_count_button.isEnabled() and not dialog.exclude_count_button.isEnabled()
    dialog.include_count_button.click();finish(dialog)
    assert dialog.service.db.today_delivery_summary()['project_count']==1
    assert dialog.service.get_project(2)==before
    with dialog.service.db.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM delivery_events').fetchone()[0]==3
        assert c.execute('SELECT COUNT(*) FROM delivery_event_actions').fetchone()[0]==0
        assert c.execute('SELECT COUNT(*) FROM project_delivery_count_actions').fetchone()[0]==2

def test_restoring_prior_project_does_not_make_it_todays_work(dialog,monkeypatch):
    assert hasattr(dialog,'exclude_count_button')
    select(dialog,1)
    monkeypatch.setattr(QMessageBox,'question',lambda *args:QMessageBox.Yes)
    dialog.exclude_count_button.click();finish(dialog)
    dialog.include_count_button.click();finish(dialog)
    assert dialog.service.db.today_delivery_summary()['project_count']==1
    assert '2026-10-28' in dialog.table.item(dialog.table.currentRow(),2).text()
    assert '原首次有效交付日期' in dialog.status_note.text()

def test_cancel_and_failure_do_not_change_count_or_note(dialog,monkeypatch):
    assert hasattr(dialog,'exclude_count_button')
    select(dialog,2);dialog.reason.setText('填写的说明')
    monkeypatch.setattr(QMessageBox,'question',lambda *args:QMessageBox.No)
    dialog.exclude_count_button.click();finish(dialog)
    assert dialog.service.db.today_delivery_summary()['project_count']==1
    monkeypatch.setattr(QMessageBox,'question',lambda *args:QMessageBox.Yes)
    def fail(*args,**kwargs):raise OSError('隔离写入失败')
    monkeypatch.setattr(dialog.service,'set_project_delivery_counting',fail)
    dialog.exclude_count_button.click();finish(dialog)
    assert dialog.reason.text()=='填写的说明' and '操作未完成' in dialog.status_note.text()
    assert dialog.exclude_count_button.isEnabled()

def test_multiple_projects_use_project_ids_not_event_ids(dialog,monkeypatch):
    assert hasattr(dialog,'exclude_count_button')
    assert dialog.table.selectionMode()==QAbstractItemView.ExtendedSelection
    dialog.table.selectAll()
    assert dialog.details.rowCount()==0
    monkeypatch.setattr(QMessageBox,'question',lambda *args:QMessageBox.Yes)
    dialog.exclude_count_button.click();finish(dialog)
    assert dialog.service.db.today_delivery_summary()['project_count']==0
    assert all(p['status']=='已交付' for p in dialog.service.list_projects())
    with dialog.service.db.connect() as c:
        assert {r[0] for r in c.execute('SELECT project_id FROM project_delivery_count_actions')}=={1,2}
