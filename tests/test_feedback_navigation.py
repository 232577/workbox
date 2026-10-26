"""收录反馈只在用户点击该条“查看”时定位实际处理结果。"""

import time
from threading import Event
from zipfile import ZipFile

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

from desktop_controller import FloatingController
from desktop_service import DesktopService


@pytest.fixture(scope='module')
def qapp():
    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    return application


@pytest.fixture
def controller(qapp, tmp_path, monkeypatch):
    value = FloatingController(DesktopService(tmp_path / 'workbox.db'), tray_enabled=False)
    assert value.start()
    value.monitor_timer.stop()
    value.focus_calls = []
    # 面板的定位和滚动由其界面测试覆盖，这里观察控制器传出的实际收录目标。
    monkeypatch.setattr(value.panel, 'focus_projects', lambda ids: value.focus_calls.append(tuple(ids)) or True, raising=False)
    try:
        yield value
    finally:
        value.shutdown()
        qapp.processEvents()


def wait_finished(controller):
    deadline = time.monotonic() + 8
    while controller.busy and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(.01)
    assert not controller.busy, '隔离收录任务未完成'
    QApplication.processEvents()


def view(controller):
    next(button for button in controller.bubble.findChildren(QPushButton) if button.text() == '查看').click()


def intake_text(controller, text):
    controller.receive({'kind': 'text', 'text': text})
    wait_finished(controller)


def test_real_multi_project_receipt_only_navigates_on_its_feedback_view(controller):
    intake_text(controller, '1. 原有系统')
    intake_text(controller, '1. 客户档案系统\n2. 合同归档工具')
    assert controller.service.db.counts()['projects'] == 3
    assert controller.focus_calls == []
    controller.orb.open_requested.emit()
    assert controller.focus_calls == []
    view(controller)
    assert controller.focus_calls == [(2, 3)]


def test_duplicate_receipt_still_locates_existing_batch(controller):
    intake_text(controller, '1. 客户档案系统\n2. 合同归档工具')
    intake_text(controller, '1. 客户档案系统\n2. 合同归档工具')
    assert controller.service.db.counts()['projects'] == 2
    assert '未重复创建' in controller.bubble.text.text()
    view(controller)
    assert controller.focus_calls == [(1, 2)]


def test_real_ordinary_file_result_locates_its_project(controller, tmp_path):
    intake_text(controller, '1. 客户档案系统')
    path = tmp_path / '客户档案系统.txt'
    path.write_text('隔离需求内容', encoding='utf-8')
    controller.receive({'kind': 'files', 'paths': [str(path)]})
    wait_finished(controller)
    assert controller.service.intake_log.list_recent()[0]['status'] == 'copied'
    assert controller.focus_calls == []
    view(controller)
    assert controller.focus_calls == [(1,)]


def test_real_uncertain_text_feedback_opens_pending_review(controller):
    intake_text(controller, '1. 客户档案系统')
    intake_text(controller, '这里是说明，不是一份项目清单。')
    assert controller.service.pending_receipts()
    assert controller.panel.tabs.currentIndex() == 0
    view(controller)
    assert controller.panel.tabs.currentIndex() == 2
    assert controller.focus_calls == []


def test_real_unmatched_zip_feedback_locates_newly_created_project(controller, tmp_path):
    intake_text(controller, '1. 客户档案系统')
    path = tmp_path / '未知包.zip'
    with ZipFile(path, 'w') as stream:
        stream.writestr('说明.txt', '隔离内容')
    controller.receive({'kind': 'files', 'paths': [str(path)]})
    wait_finished(controller)
    assert controller.service.snapshots.list_sources()[0]['project_id'] == 2
    view(controller)
    assert controller.panel.tabs.currentIndex() == 0
    assert controller.focus_calls == [(2,)]


def test_empty_silent_archive_poll_preserves_feedback_and_target(controller):
    intake_text(controller, '1. 客户档案系统')
    before = controller.bubble.text.text()
    controller.enqueue('检查 ZIP 归档', lambda **kw: controller.service.scan_archives(**kw), silent=True)
    wait_finished(controller)
    assert controller.bubble.text.text() == before
    assert controller.panel.notice.text() == before
    view(controller)
    assert controller.focus_calls == [(1,)]


def test_empty_silent_poll_does_not_refresh_project_controls(controller, monkeypatch):
    calls = []
    monkeypatch.setattr(controller.panel, 'refresh', lambda: calls.append('refresh'))
    controller.check_archives(silent=True)
    wait_finished(controller)
    assert calls == [], '无归档变化时不应每两秒重建筛选和项目详情'


def test_busy_feedback_does_not_reuse_previous_success_target(controller):
    intake_text(controller, '1. 客户档案系统')
    finish = Event()
    controller.enqueue('本次新任务', lambda **_: finish.wait(5))
    try:
        view(controller)
        assert controller.focus_calls == []
    finally:
        finish.set()
        wait_finished(controller)


def test_failed_task_feedback_does_not_reuse_previous_success_target(controller):
    intake_text(controller, '1. 客户档案系统')
    def fail(**_):
        raise OSError('本次隔离文件不可读')
    controller.enqueue('收录失败', fail)
    wait_finished(controller)
    view(controller)
    assert controller.focus_calls == []
    assert '失败' in controller.bubble.text.text()


def test_nonreceipt_notification_cannot_reuse_previous_target(controller):
    intake_text(controller, '1. 客户档案系统')
    controller.notify('本次消息没有项目目标')
    view(controller)
    assert controller.focus_calls == []


@pytest.mark.parametrize('bundle_matches', [True, False])
def test_real_background_bundle_archive_only_navigates_after_view(controller, tmp_path, bundle_matches):
    intake_text(controller, '1. 客户档案系统\n2. 合同归档工具')
    path = tmp_path / '客户档案系统-2-G.zip'
    with ZipFile(path, 'w') as stream:
        stream.writestr('客户档案系统/说明.txt', '隔离内容')
        if bundle_matches:
            stream.writestr('合同归档工具/说明.txt', '隔离内容')
    controller.receive({'kind': 'files', 'paths': [str(path)]})
    wait_finished(controller)
    controller.service.scan_archives(now=0)
    controller.enqueue('检查 ZIP 归档', lambda **kw: controller.service.scan_archives(now=11, **kw), silent=True)
    wait_finished(controller)
    assert controller.service.db.counts()['zips'] == 1
    assert controller.focus_calls == []
    assert controller.panel.tabs.currentIndex() == 0
    view(controller)
    if bundle_matches:
        assert controller.focus_calls == [(1, 2)]
    else:
        assert controller.focus_calls == []
        assert controller.panel.tabs.currentIndex() == 1


def test_drag_hint_view_cannot_reuse_previous_success_target(controller):
    intake_text(controller, '1. 客户档案系统')
    controller.drop_hint('松开以收进本次文字')
    view(controller)
    assert controller.focus_calls == []

