import time
from pathlib import Path
from zipfile import ZipFile
from datetime import date

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from desktop_controller import FloatingController
from desktop_service import DesktopService
from compact_ui import ProjectDeliveryDialog


@pytest.fixture(scope='module')
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


@pytest.fixture
def controller(qapp, tmp_path):
    service = DesktopService(tmp_path/'data/workbox.db')
    service.receive_text('1. 甲平台')
    source = tmp_path/'甲平台_申请书.docx'
    source.write_bytes(b'isolated word-only fixture')
    service.receive_file(source)
    controller = FloatingController(service, tray_enabled=False)
    assert controller.start()
    controller.monitor_timer.stop()
    controller.show_panel()
    controller.panel.project_list.setCurrentRow(0)
    yield controller
    if getattr(controller.panel, 'today_dialog', None):
        controller.panel.today_dialog.close()
    controller.shutdown()


def wait(controller):
    end = time.monotonic()+10
    while controller.busy and time.monotonic()<end:
        QApplication.processEvents()
        time.sleep(.01)
    QApplication.processEvents()
    assert not controller.busy


def project_action(panel, text):
    menu = panel._test_project_menu = panel.project_context_menu(1)
    return next(item for item in menu.actions() if item.text() == text)


def deliver_work_files(panel, *, accept=True, seen=None):
    def answer():
        for dialog in QApplication.topLevelWidgets():
            if isinstance(dialog, ProjectDeliveryDialog) and dialog.isVisible():
                dialog.version_box.setCurrentIndex(dialog.version_box.findData(None))
                if seen is not None:
                    seen.append((dialog.explanation.text(), dialog.cancel_button.isDefault()))
                (dialog.confirm_button if accept else dialog.cancel_button).click()
    QTimer.singleShot(40, answer)
    project_action(panel, '确认交付…').trigger()


def test_word_only_confirmation_archives_then_records_one_project_twice(controller):
    panel, service = controller.panel, controller.service
    assert project_action(panel, '确认交付…').isEnabled(), '只有Word也需要可用的人工交付入口'
    questions=[]
    deliver_work_files(panel, seen=questions)
    wait(controller)
    assert questions[0][1]
    assert '工作目录' in questions[0][0]
    assert service.get_project(1)['status'] == '已交付'
    summary=service.db.today_delivery_summary()
    assert (summary['project_count'],summary['event_count']) == (1,1)
    assert panel.today_button.text() == '今日交付 1'
    assert '首次交付已计入今日统计' in panel.notice.text()
    events=service.db.delivery_events_for_project(1)
    archive=Path(events[0]['archive_path'])
    assert (archive/'甲平台_申请书.docx').read_bytes()==b'isolated word-only fixture'
    original=Path(service.workspaces.get_workspace(1)['path'])/'甲平台_申请书.docx'
    assert original.read_bytes()==b'isolated word-only fixture'
    service.receive_file(original)
    panel.refresh()
    deliver_work_files(panel)
    wait(controller)
    summary=service.db.today_delivery_summary()
    assert (summary['project_count'],summary['event_count']) == (1,2)
    assert panel.today_button.text() == '今日交付 1'
    assert '本次为补交，不增加今日首次交付数' in panel.notice.text()


def test_prior_delivery_confirmation_feedback_does_not_claim_a_new_count(controller):
    service=controller.service
    service.db.set_prior_delivery_projects([1],effective_day=date.fromisoformat(service.db.today_delivery_summary()['day']),
        confirmed=True,reason='隔离历史交付说明')
    deliver_work_files(controller.panel)
    wait(controller)
    assert service.get_project(1)['status']=='已交付'
    assert controller.panel.today_button.text()=='今日交付 0'
    assert '本次为补交，不增加今日首次交付数' in controller.panel.notice.text()


def test_cancel_word_delivery_does_not_archive_or_count(controller):
    assert project_action(controller.panel, '确认交付…').isEnabled()
    deliver_work_files(controller.panel, accept=False)
    wait(controller)
    assert controller.service.get_project(1)['status']=='待交付'
    assert controller.service.db.today_delivery_summary()['project_count']==0


def test_today_count_refresh_is_lightweight_and_dialog_is_modeless(controller, tmp_path, qapp):
    panel=controller.panel
    assert panel.today_button.text()=='今日交付 0'
    resets=QSignalSpy(panel.project_list.model().modelReset)
    package=tmp_path/'甲平台.zip'
    with ZipFile(package,'w') as zip_file:
        for i in range(4): zip_file.writestr(f'{i}.txt','隔离资料')
    controller.service.import_zip(package)
    panel.refresh_archive_status()
    assert panel.today_button.text()=='今日交付 1'
    assert resets.count()==0
    panel.today_button.click()
    qapp.processEvents()
    assert panel.today_dialog.isVisible() and QApplication.activeModalWidget() is None
    assert panel.today_dialog.table.rowCount()==1
    panel.today_dialog.close()
    panel.resize(660,492)
    qapp.processEvents()
    assert panel.width()==660 and panel.rect().contains(panel.today_button.geometry().center())


def test_unknown_file_creates_project_without_selected_project_fallback(controller,tmp_path,monkeypatch):
    source=tmp_path/'新客户系统_代码（Python）.txt'
    source.write_text('新项目内容',encoding='utf-8')
    def must_not_choose(*args):
        raise AssertionError('零候选应自动新建，不弹选择框')
    monkeypatch.setattr(controller.panel,'choose_file_project',must_not_choose)
    controller.receive({'kind':'files','paths':[str(source)]})
    wait(controller)
    created=next(p for p in controller.service.list_projects() if p['name']=='新客户系统')
    assert created['id']!=1 and created['status']=='待交付'
    workspace=controller.service.workspaces.get_workspace(created['id'])
    assert (Path(workspace['path'])/source.name).read_bytes()==source.read_bytes()
    assert '已新建' in controller.panel.notice.text()
