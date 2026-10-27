"""右键删除与恢复只作用于指定项目，保留隔离夹具文件。"""
import time

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication,QMessageBox

from desktop_controller import FloatingController
from desktop_service import DesktopService


@pytest.fixture(scope='module')
def qapp():
    app=QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


@pytest.fixture
def controller(qapp,tmp_path):
    service=DesktopService(tmp_path/'data/workbox.db')
    service.receive_text('1. 甲系统\n2. 乙系统')
    source=tmp_path/'乙系统.txt'
    source.write_bytes(b'isolated retained file')
    service.receive_file(source)
    value=FloatingController(service,tray_enabled=False)
    assert value.start()
    value.monitor_timer.stop()
    value.show_panel()
    yield value
    value.shutdown()


def menu_action(panel,pid,text):
    menu=panel._test_delete_menu=panel.project_context_menu(pid)
    return next((a for a in menu.actions() if a.text()==text),None)


def wait(controller):
    end=time.monotonic()+8
    while controller.busy and time.monotonic()<end:
        QApplication.processEvents()
        time.sleep(.01)
    QApplication.processEvents()
    assert not controller.busy


def listed(panel):
    return {panel.project_list.item(i).data(Qt.UserRole) for i in range(panel.project_list.count())}


def test_delete_cancel_confirm_and_restore_target_actual_menu_project(controller,monkeypatch):
    panel=controller.panel
    service=controller.service
    from pathlib import Path
    workspace=Path(service.workspaces.get_workspace(2)['path'])/'乙系统.txt'
    original=workspace.read_bytes()
    panel.project_list.setCurrentRow(next(i for i in range(panel.project_list.count()) if panel.project_list.item(i).data(Qt.UserRole)==1))
    questions=[]
    monkeypatch.setattr(QMessageBox,'question',lambda *args:questions.append(args) or QMessageBox.No)
    action=menu_action(panel,2,'删除项目…')
    assert action is not None,'右键缺少删除项目'
    action.trigger()
    wait(controller)
    assert service.db.counts()['projects']==2
    assert questions[-1][-1]==QMessageBox.No
    assert '乙系统' in questions[-1][2] and '原文件' in questions[-1][2] and '恢复' in questions[-1][2]
    monkeypatch.setattr(QMessageBox,'question',lambda *args:QMessageBox.Yes)
    menu_action(panel,2,'删除项目…').trigger()
    wait(controller)
    assert listed(panel)=={1}
    assert service.get_project(2)['deleted_at']
    assert service.db.counts()['projects']==1 and controller.orb.count==1
    assert workspace.read_bytes()==original
    trash=panel.archive_filter.findData('deleted')
    assert trash>=0,'缺少已删除项目入口'
    panel.archive_filter.setCurrentIndex(trash)
    assert listed(panel)=={2}
    assert '已删除' in panel.project_list.item(0).text()
    assert not panel.bulk_mode.isEnabled()
    assert menu_action(panel,2,'恢复项目') is None
    restore=menu_action(panel,2,'恢复已删除项目')
    assert restore is not None and restore.isEnabled()
    restore.trigger()
    wait(controller)
    assert not service.get_project(2)['deleted_at']
    assert not service.get_project(2)['archived_at']
    assert listed(panel)==set()
    assert service.db.counts()['projects']==2 and controller.orb.count==2
    assert workspace.read_bytes()==original
    assert '日常列表' in panel.notice.text()


def test_restoring_deleted_archived_project_keeps_original_archive_state(controller,monkeypatch):
    service,panel=controller.service,controller.panel
    service.bulk_update_projects([2],archived=True)
    before=service.get_project(2)['archived_at']
    monkeypatch.setattr(QMessageBox,'question',lambda *args:QMessageBox.Yes)
    action=menu_action(panel,2,'删除项目…')
    assert action is not None,'已归档项目也应支持保留文件的删除'
    action.trigger();wait(controller)
    menu_action(panel,2,'恢复已删除项目').trigger();wait(controller)
    assert service.get_project(2)['archived_at']==before
    assert '已归档列表' in panel.notice.text()


def test_deletion_actions_recheck_busy_and_deleted_state(controller,monkeypatch):
    panel=controller.panel
    questions=[]
    monkeypatch.setattr(QMessageBox,'question',lambda *args:questions.append(args) or QMessageBox.Yes)
    action=menu_action(panel,2,'删除项目…')
    assert action is not None
    panel.set_busy(True)
    action.trigger()
    assert not questions
    panel.set_busy(False)
    controller.service.delete_project(2,confirmed=True)
    action.trigger()
    assert not questions
