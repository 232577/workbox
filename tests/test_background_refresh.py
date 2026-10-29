"""后台监视经真实控制器线程执行，不应周期性打断项目操作。"""

import time
from concurrent.futures import CancelledError
from threading import Event
from zipfile import ZipFile

import pytest
from PySide6.QtWidgets import QApplication

from desktop_controller import FloatingController
from desktop_service import DesktopService


@pytest.fixture(scope='module')
def qapp():
    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    return application


@pytest.fixture
def controller(qapp, tmp_path):
    service = DesktopService(tmp_path / 'data' / 'workbox.db')
    service.receive_text('1. 客户档案系统')
    value = FloatingController(service, tray_enabled=False)
    assert value.start()
    value.show_panel()
    value.panel.project_list.setCurrentRow(0)
    yield value
    value.shutdown()
    qapp.processEvents()


def wait_until(predicate):
    deadline = time.monotonic() + 5
    while not predicate() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(.005)
    QApplication.processEvents()
    assert predicate(), '真实后台任务未按时完成'


def observe(monkeypatch, target, name):
    calls = []
    original = getattr(target, name)
    def wrapped(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)
    monkeypatch.setattr(target, name, wrapped)
    return calls


def project_action(panel, text):
    menu = panel._test_project_menu = panel.project_context_menu(1)
    for item in menu.actions():
        if item.text() == text:
            return item
    pytest.fail(f'项目右键菜单缺少：{text}')


def test_repeated_real_timer_polls_do_not_toggle_controls_or_refresh(controller, monkeypatch):
    panel = controller.panel
    busy_calls = observe(monkeypatch, panel, 'set_busy')
    refresh_calls = observe(monkeypatch, panel, 'refresh')
    light_calls = observe(monkeypatch, panel, 'refresh_archive_status')
    assert controller.monitor_timer.isActive()
    assert controller.monitor_timer.interval() == 2000
    for _ in range(3):
        controller.monitor_timer.timeout.emit()
        assert controller.worker is not None
        assert project_action(panel, '开发状态').isEnabled(), '后台监视不应把项目菜单暂时禁用'
        assert panel.settings_button.isEnabled()
        assert panel.cancel_button.isHidden(), '静默监视不应闪出取消按钮'
        wait_until(lambda: not controller.busy)
    assert busy_calls == [], '每两秒切换忙碌状态会造成按钮和布局闪动'
    assert refresh_calls == []
    assert len(light_calls) == 3
    assert controller.monitor_timer.isActive()


def test_user_action_during_silent_scan_is_queued_and_applied(controller, monkeypatch):
    entered, finish = Event(), Event()
    original = controller.service.scan_archives
    def slow_scan(**kwargs):
        entered.set()
        assert finish.wait(4)
        return original(**kwargs)
    monkeypatch.setattr(controller.service, 'scan_archives', slow_scan)
    controller.monitor_timer.timeout.emit()
    try:
        wait_until(entered.is_set)
        assert project_action(controller.panel, '加急').isEnabled()
        project_action(controller.panel, '加急').trigger()
        assert len(controller.queue) == 1, '可操作的项目按钮不能因后台扫描静默丢弃请求'
        assert controller.panel.busy, '排入显式写入任务后仍需正常显示忙碌状态'
    finally:
        finish.set()
        wait_until(lambda: not controller.busy)
    assert controller.service.get_project(1)['urgent']
    assert project_action(controller.panel, '加急').isEnabled()


def test_silent_archive_progress_does_not_flash_ball_but_updates_delivery(controller, tmp_path, monkeypatch):
    package = tmp_path / '客户档案系统.zip'
    with ZipFile(package, 'w') as stream:
        for index in range(4):
            stream.writestr(f'{index}.txt', '隔离归档资料')
    controller.service.receive_file(package)
    controller.service.scan_archives(now=0)
    original = controller.service.scan_archives
    monkeypatch.setattr(controller.service, 'scan_archives', lambda **kwargs: original(now=11, **kwargs))
    busy_calls = observe(monkeypatch, controller.panel, 'set_busy')
    refresh_calls = observe(monkeypatch, controller.panel, 'refresh')
    progress_calls = observe(monkeypatch, controller.orb, 'set_progress')
    controller.monitor_timer.timeout.emit()
    wait_until(lambda: not controller.busy)
    assert busy_calls == []
    assert not any(args[0] for args in progress_calls), '后台复制进度不应使悬浮球反复切换忙碌外观'
    assert len(refresh_calls) == 1, '实际新增归档后必须立即更新项目和交付统计'
    assert controller.service.get_project(1)['status'] == '已交付'
    assert controller.panel.current_project['status'] == '已交付'
    assert controller.panel._today_caption == '今日工作量 1'
    assert package.exists()
    assert controller.monitor_timer.isActive()


def test_background_source_error_updates_notice_without_rebuilding_project(controller, tmp_path, monkeypatch):
    package = tmp_path / '客户档案系统.zip'
    with ZipFile(package, 'w') as stream:
        stream.writestr('说明.txt', '隔离来源')
    controller.service.receive_file(package)
    package.unlink()
    calls = observe(monkeypatch, controller.panel, 'refresh')
    controller.monitor_timer.timeout.emit()
    wait_until(lambda: not controller.busy)
    assert '暂未归档' in controller.panel.notice.text()
    assert calls == [], '归档来源错误只影响监视状态，不应重建当前项目详情'
    assert project_action(controller.panel, '开发状态').isEnabled()
    assert controller.monitor_timer.isActive()


@pytest.mark.parametrize('quit_after_cancel', [False, True])
def test_explicit_task_retains_busy_cancel_and_safe_exit(controller, quit_after_cancel):
    entered = Event()
    def operation(*, cancel_event, progress):
        entered.set()
        cancel_event.wait(4)
        raise CancelledError()
    controller.enqueue('隔离显式任务', operation)
    wait_until(entered.is_set)
    assert controller.panel.busy
    assert not project_action(controller.panel, '开发状态').isEnabled()
    assert not controller.panel.cancel_button.isHidden()
    if quit_after_cancel:
        controller.request_quit()
        wait_until(lambda: controller.closed)
        assert not controller.monitor_timer.isActive()
    else:
        controller.cancel_jobs()
        wait_until(lambda: not controller.busy)
        assert project_action(controller.panel, '开发状态').isEnabled()
        assert controller.panel.cancel_button.isHidden()
        assert controller.monitor_timer.isActive()
