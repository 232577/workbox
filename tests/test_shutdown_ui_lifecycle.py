"""业务安全停机与真实入口的 Qt 窗口释放分开，测试只使用隔离目录。"""
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Event

if sys.platform == 'win32':
    # 隔离测试项目1系统错误弹窗，不修改系统调试器或生产入口。
    ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002)
    ctypes.windll.kernel32.WerSetFlags(0x0020)

import pytest
import shiboken6
from PySide6.QtCore import QThread
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from desktop_controller import FloatingController
from desktop_service import DesktopService
from ui import FileJob


@pytest.fixture(scope='module')
def qapp():
    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    return application


@pytest.fixture
def controller(qapp, tmp_path):
    value = FloatingController(DesktopService(tmp_path / 'data/workbox.db'), tray_enabled=False)
    assert value.start()
    yield value
    value.shutdown()
    if callable(getattr(value, 'dispose_ui', None)):
        value.dispose_ui()


def dispose(controller):
    assert callable(getattr(controller, 'dispose_ui', None)), '缺少业务停机之后的显式 Qt 窗口释放'
    controller.dispose_ui()


def wait_for(qapp, event):
    for _ in range(100):
        qapp.processEvents()
        if event.is_set():
            return
        QTest.qWait(10)
    pytest.fail('隔离任务未按时启动')


def test_shutdown_keeps_readable_objects_until_explicit_dispose(controller, qapp):
    controller.show_panel()
    controller.panel.show_today_deliveries()
    roots = (controller.orb, controller.panel, controller.bubble)
    today = controller.panel.today_dialog
    foreign = QDialog()
    foreign.show()
    destroyed = []
    for name, widget in zip(('orb', 'panel', 'bubble', 'today'), (*roots, today)):
        widget.destroyed.connect(lambda _=None, key=name: destroyed.append((key, QApplication.instance() is qapp)))
    try:
        controller.shutdown()
        saved = (controller.service.get_setting('orb_position'), controller.service.get_setting('manager_geometry'))
        assert controller.closed and not controller.owns_lock
        assert all(shiboken6.isValid(widget) for widget in (*roots, today))
        assert not controller.panel.isVisible()
        dispose(controller)
        assert all(not shiboken6.isValid(widget) for widget in (*roots, today))
        assert {name for name, _ in destroyed} == {'orb', 'panel', 'bubble', 'today'}
        assert all(alive for _, alive in destroyed)
        assert shiboken6.isValid(foreign) and foreign.isVisible()
        assert saved == (controller.service.get_setting('orb_position'), controller.service.get_setting('manager_geometry'))
        controller.shutdown()
        dispose(controller)
    finally:
        foreign.close()


def test_dispose_requires_successful_shutdown_and_keeps_lock(controller):
    roots = (controller.orb, controller.panel, controller.bubble)
    assert callable(getattr(controller, 'dispose_ui', None)), '缺少显式 Qt 窗口释放'
    with pytest.raises(RuntimeError, match='停机|结束|shutdown'):
        controller.dispose_ui()
    assert controller.owns_lock and not controller.closed
    assert all(shiboken6.isValid(widget) for widget in roots)


def test_dispose_detaches_tray_menu_before_its_orb_owner_is_destroyed(qapp, tmp_path):
    controller = FloatingController(DesktopService(tmp_path / 'data/workbox.db'), tray_enabled=True)
    assert controller.start()
    tray, menu = controller.tray, controller.tray.contextMenu()
    try:
        assert menu is not None and shiboken6.isValid(menu)
        controller.shutdown()
        assert not tray.isVisible() and shiboken6.isValid(menu)
        dispose(controller)
        assert tray.contextMenu() is None
        assert not shiboken6.isValid(menu)
    finally:
        controller.shutdown()
        if callable(getattr(controller, 'dispose_ui', None)):
            controller.dispose_ui()


def test_background_reader_and_business_job_stop_before_roots_are_destroyed(controller, qapp, monkeypatch):
    import project_documents_ui
    reader_entered, reader_stopped = Event(), Event()
    business_entered, business_stopped = Event(), Event()

    def read_documents(service, project_id, *, cancel_event):
        reader_entered.set()
        assert cancel_event.wait(5)
        reader_stopped.set()
        return dict(project_id=project_id, workspace_path='', errors=[], documents=[])

    def operation(*, cancel_event, progress):
        business_entered.set()
        assert cancel_event.wait(5)
        business_stopped.set()

    monkeypatch.setattr(project_documents_ui, 'list_project_documents', read_documents)
    controller.service.receive_text('1. 隔离退出验证系统')
    controller.show_panel()
    controller.panel.project_list.setCurrentRow(0)
    wait_for(qapp, reader_entered)
    controller.enqueue('隔离退出任务', operation)
    wait_for(qapp, business_entered)
    roots = (controller.orb, controller.panel, controller.bubble)
    readiness = []
    for widget in roots:
        widget.destroyed.connect(lambda: readiness.append(reader_stopped.is_set() and business_stopped.is_set()))
    controller.shutdown()
    assert reader_stopped.is_set() and business_stopped.is_set()
    assert controller.worker is None and controller.panel.document_pane.worker is None
    assert all(not worker.isRunning() for worker in controller.findChildren(QThread))
    dispose(controller)
    assert readiness == [True, True, True]
    assert all(not shiboken6.isValid(widget) for widget in roots)


def test_failed_worker_wait_never_disposes_ui_or_unlocks(controller, qapp, monkeypatch):
    entered, release = Event(), Event()

    def operation(**_):
        entered.set()
        assert release.wait(5)

    controller.enqueue('隔离等待任务', operation)
    wait_for(qapp, entered)
    worker = controller.worker
    roots = (controller.orb, controller.panel, controller.bubble)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(worker, 'wait', lambda *_: False)
            with pytest.raises(RuntimeError, match='文件操作仍未结束'):
                controller.shutdown()
        assert not controller.closed and controller.owns_lock and worker.isRunning()
        assert callable(getattr(controller, 'dispose_ui', None)), '缺少显式 Qt 窗口释放'
        with pytest.raises(RuntimeError):
            controller.dispose_ui()
        assert all(shiboken6.isValid(widget) for widget in roots)
    finally:
        release.set()
        controller.shutdown()


def test_cancelled_initial_setup_has_no_windows_and_dispose_is_repeatable(qapp, tmp_path):
    controller = FloatingController(DesktopService(tmp_path / 'data/workbox.db'), tray_enabled=False)
    assert not controller.start(setup_callback=lambda: False)
    assert controller.closed and not controller.owns_lock
    dispose(controller)
    dispose(controller)
    assert controller.orb is controller.panel is controller.bubble is None


def test_dispose_checks_running_child_threads_before_scheduling_any_window_deletion(controller, qapp):
    controller.shutdown()
    entered, release = Event(), Event()
    roots = (controller.orb, controller.panel, controller.bubble)

    def operation(**_):
        entered.set()
        assert release.wait(5)

    reader = FileJob(operation, controller.panel)
    reader.start()
    wait_for(qapp, entered)
    try:
        assert callable(getattr(controller, 'dispose_ui', None)), '缺少显式 Qt 窗口释放'
        with pytest.raises(RuntimeError, match='后台任务尚未结束'):
            controller.dispose_ui()
        assert all(shiboken6.isValid(widget) for widget in roots)
        assert reader.isRunning()
    finally:
        release.set()
        assert reader.wait(5000)
    dispose(controller)
    assert all(not shiboken6.isValid(widget) for widget in roots)


def test_queued_final_progress_cannot_touch_disposed_widgets(controller, qapp, monkeypatch):
    entered = Event()
    exceptions = []
    monkeypatch.setattr(sys, 'excepthook', lambda *error: exceptions.append(error))

    def operation(*, cancel_event, progress):
        entered.set()
        assert cancel_event.wait(5)
        # shutdown.wait() 期间排入 GUI 队列；业务线程结束不代表该进度回调已消费。
        progress(1, 1)

    controller.enqueue('隔离尾部进度', operation)
    wait_for(qapp, entered)
    controller.shutdown()
    dispose(controller)
    qapp.processEvents()
    assert exceptions == [], [str(error[1]) for error in exceptions]


ENTRY_SCRIPT = r'''
import ctypes, json, os, sys
if sys.platform == 'win32':
    ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002)
    ctypes.windll.kernel32.WerSetFlags(0x0020)
from pathlib import Path
import shiboken6
from PySide6.QtCore import QThread, QTimer
from PySide6.QtWidgets import QApplication
import desktop_controller
from desktop_service import DesktopService
import main
data = Path(sys.argv[1])
service = DesktopService(data / 'workbox.db')
settings = service.workspaces.settings()
service.workspaces.configure(settings['work_root'], settings['archive_root'], complete_setup=True)
instances = []
fail_shutdown = os.environ.get('WORKBOX_TEST_SHUTDOWN_FAILURE') == '1'
class ObservedController(desktop_controller.FloatingController):
    def start(self, **kwargs):
        result = super().start(**kwargs)
        instances.append(self)
        self.show_panel()
        self.panel.show_today_deliveries()
        self.references = (self.orb, self.panel, self.bubble, self.panel.today_dialog)
        self.released_while_app_alive = False
        self.block_shutdown = fail_shutdown
        def finish_after_event_loop_starts():
            if QThread.currentThread().loopLevel() == 0:
                QTimer.singleShot(20, finish_after_event_loop_starts)
            elif fail_shutdown:
                # 测试只注入事件循环返回；quit() 会被主面板既有 closeEvent.ignore 拒绝。
                QApplication.instance().exit(0)
            else:
                self.request_quit()
        QTimer.singleShot(0, finish_after_event_loop_starts)
        return result
    def shutdown(self):
        if getattr(self, 'block_shutdown', False):
            raise RuntimeError('isolated shutdown failure')
        super().shutdown()
    def dispose_ui(self):
        assert QApplication.instance() is not None
        assert self.closed and not self.owns_lock
        super().dispose_ui()
        self.released_while_app_alive = all(not shiboken6.isValid(widget) for widget in self.references)
desktop_controller.FloatingController = ObservedController
try:
    result = main.main(['--data-dir', str(data), '--panel', '--no-tray'])
except RuntimeError as error:
    assert fail_shutdown and str(error) == 'isolated shutdown failure'
    controller = instances[0]
    assert controller.owns_lock and not controller.closed
    assert all(shiboken6.isValid(widget) for widget in controller.references)
    assert not controller.released_while_app_alive
    controller.block_shutdown = False
    controller.shutdown()
    controller.dispose_ui()
    result = 0
    print('SHUTDOWN_FAILURE_PROPAGATED=True', flush=True)
controller = instances[0]
assert result == 0
assert controller.released_while_app_alive, '真实入口返回前没有销毁本controller的业务根窗口'
assert not controller.owns_lock
assert service.get_setting('orb_position') and service.get_setting('manager_geometry')
print('SHUTDOWN_RESULT=' + json.dumps(dict(result=result, disposed=True, saved=True)), flush=True)
'''


@pytest.mark.parametrize('repeat', range(5))
def test_real_entry_disposes_owned_windows_before_qapplication_teardown(tmp_path, repeat):
    environment = dict(os.environ, PYTHONIOENCODING='utf-8')
    result = subprocess.run([sys.executable, '-c', ENTRY_SCRIPT, str(tmp_path / f'entry-{repeat}')],
        cwd=Path(__file__).resolve().parents[1], env=environment,
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=20)
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    payload = next((line.partition('=')[2] for line in result.stdout.splitlines()
                    if line.startswith('SHUTDOWN_RESULT=')), None)
    assert payload is not None, (result.stdout, result.stderr)
    assert json.loads(payload) == dict(result=0, disposed=True, saved=True)


def test_real_entry_preserves_shutdown_failure_without_disposing_or_unlocking(tmp_path):
    environment = dict(os.environ, PYTHONIOENCODING='utf-8', WORKBOX_TEST_SHUTDOWN_FAILURE='1')
    result = subprocess.run([sys.executable, '-c', ENTRY_SCRIPT, str(tmp_path / 'entry-failure')],
        cwd=Path(__file__).resolve().parents[1], env=environment,
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=20)
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    assert 'SHUTDOWN_FAILURE_PROPAGATED=True' in result.stdout
