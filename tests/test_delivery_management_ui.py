import time
from datetime import datetime,timezone
from pathlib import Path

import pytest
from PySide6.QtCore import Qt,QDate
from PySide6.QtWidgets import QApplication,QMessageBox

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
            value=datetime(2026,10,26,4,tzinfo=timezone.utc)
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)
    monkeypatch.setattr(database,'datetime',Frozen)
    service=DesktopService(tmp_path/'data/workbox.db')
    service.receive_text('1. 首交甲平台\n2. 首交乙平台')
    archive=tmp_path/'archive'
    archive.mkdir()
    with service.db.connect() as c:
        for pid,name,at in ((1,'首交甲平台','2026-10-23T02:00:00Z'),(1,'首交甲平台','2026-10-26T02:00:00Z'),(2,'首交乙平台','2026-10-26T03:00:00Z')):
            c.execute('''INSERT INTO delivery_events(project_id,project_name,batch_name,kind,archive_path,occurred_at)
                VALUES(?,?,?,'manual-files',?,?)''',(pid,name,'测试批',str(archive),at))
    value=TodayDeliveryDialog(service)
    value.show()
    qapp.processEvents()
    yield value
    if hasattr(value,'shutdown'): value.shutdown()
    value.close()


def wait(dialog):
    end=time.monotonic()+8
    while getattr(dialog,'worker',None) is not None and time.monotonic()<end:
        QApplication.processEvents()
        time.sleep(.01)
    QApplication.processEvents()
    assert getattr(dialog,'worker',None) is None


def select_project(dialog,pid):
    row=next(i for i in range(dialog.table.rowCount()) if dialog.table.item(i,0).data(Qt.UserRole)==pid)
    dialog.table.selectRow(row)


def test_today_separates_first_deliveries_from_repeats(dialog):
    assert '首次交付 1 个项目' in dialog.summary.text()
    assert '补交 1 条' in dialog.summary.text()
    select_project(dialog,1)
    assert any('不计' in dialog.details.item(0,col).text() for col in range(dialog.details.columnCount()))


def test_first_show_rechecks_bounds_after_native_window_placement(dialog,qapp):
    dialog.hide()
    dialog.show()
    area=dialog.screen().availableGeometry().adjusted(6,6,-6,-6)
    # Windows 首次映射窗口时可能在 showEvent 返回后重新按父窗居中。
    dialog.move(area.right()-20,area.bottom()-20)
    qapp.processEvents()
    assert area.contains(dialog.frameGeometry())
    assert abs(dialog.frameGeometry().center().y()-area.center().y())<=2


def test_history_date_and_search_can_find_earlier_first_delivery(dialog,qapp):
    assert hasattr(dialog,'all_dates'), '缺少历史查询入口'
    dialog.all_dates.setChecked(True)
    dialog.search.setText('首交甲')
    assert dialog.table.rowCount()==1
    select_project(dialog,1)
    assert dialog.details.rowCount()==2
    assert '首次交付 1 个项目' in dialog.summary.text()
    dialog.all_dates.setChecked(False)
    dialog.date_edit.setDate(QDate(2026,10,23))
    assert dialog.details.rowCount()==1


def test_void_restore_are_confirmed_and_do_not_delete_history(dialog,monkeypatch):
    assert hasattr(dialog,'void_button'), '缺少误记撤销'
    dialog.all_dates.setChecked(True)
    select_project(dialog,1)
    row=next(i for i in range(dialog.details.rowCount()) if dialog.details.item(i,0).data(Qt.UserRole)==1)
    dialog.details.selectRow(row)
    before=dialog.service.db.delivery_history()
    monkeypatch.setattr(QMessageBox,'question',lambda *args:QMessageBox.No)
    dialog.void_button.click()
    wait(dialog)
    assert dialog.service.db.delivery_history()==before
    questions=[]
    monkeypatch.setattr(QMessageBox,'question',lambda *args:questions.append(args) or QMessageBox.Yes)
    dialog.reason.setText('误记日期')
    dialog.void_button.click()
    wait(dialog)
    assert questions[-1][-1]==QMessageBox.No
    assert '对应项目将退回待交付' in questions[-1][2]
    assert '原始记录和归档文件保留' in questions[-1][2]
    history=dialog.service.db.delivery_history()
    assert history['event_count']==3 and history['voided_count']==1
    assert dialog.service.db.today_delivery_summary()['project_count']==2
    assert not dialog.void_button.isEnabled(), '处理后需重新选记录，避免连续误点其他记录'
    dialog.show_voided.setChecked(True)
    select_project(dialog,1)
    row=next(i for i in range(dialog.details.rowCount()) if dialog.details.item(i,0).data(Qt.UserRole)==1)
    dialog.details.selectRow(row)
    dialog.restore_button.click()
    wait(dialog)
    assert dialog.service.db.today_delivery_summary()['project_count']==1
    with dialog.service.db.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM delivery_events').fetchone()[0]==3
        assert c.execute('SELECT COUNT(*) FROM delivery_event_actions').fetchone()[0]==2


def test_unchanged_refresh_keeps_selection_and_stable_items(dialog):
    select_project(dialog,2)
    first=dialog.table.item(0,0)
    detail=dialog.details.item(0,0)
    dialog.refresh()
    assert dialog.table.item(0,0) is first and dialog.details.item(0,0) is detail


def test_management_failure_keeps_note_and_allows_retry(dialog,monkeypatch):
    select_project(dialog,2)
    dialog.reason.setText('填写的误记说明')
    monkeypatch.setattr(QMessageBox,'question',lambda *args:QMessageBox.Yes)
    original=dialog.service.db.set_delivery_events_voided
    def fail(*args,**kwargs): raise OSError('隔离写入失败')
    monkeypatch.setattr(dialog.service.db,'set_delivery_events_voided',fail)
    dialog.void_button.click()
    wait(dialog)
    assert '操作未完成' in dialog.status_note.text()
    assert dialog.reason.text()=='填写的误记说明' and dialog.void_button.isEnabled()
    assert dialog.service.db.delivery_history()['voided_count']==0
    monkeypatch.setattr(dialog.service.db,'set_delivery_events_voided',original)
    dialog.void_button.click()
    wait(dialog)
    assert dialog.service.db.delivery_history()['voided_count']==1


@pytest.mark.parametrize('delivered_filter',[False,True])
def test_management_updates_status_list_and_orb_without_waiting_for_timer(dialog,monkeypatch,delivered_filter):
    from desktop_controller import FloatingController
    dialog.close()
    with dialog.service.db.connect() as connection:
        connection.execute("UPDATE projects SET status='已交付',delivered_at='2026-10-26T03:00:00Z' WHERE id=2")
    controller=FloatingController(dialog.service,tray_enabled=False)
    assert controller.start()
    controller.monitor_timer.stop()
    try:
        if delivered_filter:
            controller.panel.status_buttons['已交付'].click()
        listed=lambda: {controller.panel.project_list.item(i).data(Qt.UserRole):controller.panel.project_list.item(i).text()
                        for i in range(controller.panel.project_list.count())}
        assert '已交付' in listed()[2]
        assert controller.orb.count==1
        controller.panel.show_notice('项目已设为已交付并计入今日统计。')
        controller.panel.show_today_deliveries()
        manager=controller.panel.today_dialog
        assert controller.panel.today_button.text()=='今日交付 1'
        select_project(manager,2)
        monkeypatch.setattr(QMessageBox,'question',lambda *args:QMessageBox.Yes)
        manager.void_button.click()
        wait(manager)
        assert controller.panel.today_button.text()=='今日交付 0'
        assert dialog.service.db.get_project(2)['status']=='待交付'
        assert controller.orb.count==2
        if delivered_filter:
            assert 2 not in listed()
        else:
            assert '待交付' in listed()[2]
        assert '1 个项目已退回待交付' in manager.status_note.text()
        assert '1 个项目已退回待交付' in controller.panel.notice.text()
        manager.show_voided.setChecked(True)
        select_project(manager,2)
        manager.restore_button.click()
        wait(manager)
        assert dialog.service.db.get_project(2)['status']=='待交付'
        assert controller.panel.today_button.text()=='今日交付 1'
        assert controller.orb.count==2
    finally:
        controller.shutdown()


def test_shutdown_waits_for_confirmed_management_write(dialog,monkeypatch):
    from threading import Event,Timer
    selected=Event()
    original=dialog.service.db.set_delivery_events_voided
    def delayed(*args,**kwargs):
        selected.wait(2)
        return original(*args,**kwargs)
    monkeypatch.setattr(dialog.service.db,'set_delivery_events_voided',delayed)
    monkeypatch.setattr(QMessageBox,'question',lambda *args:QMessageBox.Yes)
    select_project(dialog,2)
    dialog.void_button.click()
    release=Timer(.1,selected.set)
    release.start()
    dialog.shutdown()
    release.join()
    assert dialog.worker is None and not dialog.timer.isActive()
    assert dialog.service.db.delivery_history()['voided_count']==1
