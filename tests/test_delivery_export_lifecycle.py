"""全部交付表保存窗口与主控制器共用忙碌和退出边界。"""
from time import monotonic, sleep

import pytest
from PySide6.QtWidgets import QApplication, QFileDialog

from desktop_controller import FloatingController
from desktop_service import DesktopService
from delivery_ui import TodayDeliveryDialog


def wait_for(predicate):
    deadline = monotonic() + 8
    while not predicate() and monotonic() < deadline:
        QApplication.processEvents()
        sleep(.01)
    assert predicate(), '隔离交付表任务未按时结束'


@pytest.fixture
def controller(tmp_path):
    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    value = FloatingController(DesktopService(tmp_path / 'data' / 'isolated.db'), tray_enabled=False)
    assert value.start()
    value.monitor_timer.stop()
    value.show_panel()
    value.panel.show_today_deliveries()
    yield value
    value.panel.today_dialog.shutdown()
    value.shutdown()
    value.dispose_ui()


def test_picker_keeps_new_controller_work_queued_until_export_finishes(controller, tmp_path, monkeypatch):
    page = controller.panel.today_dialog
    target = tmp_path / 'anonymous.xlsx'
    observed, completed = [], []

    def choose(*args, **kwargs):
        controller.enqueue('匿名后续工作', lambda **_: completed.append(target.is_file()))
        observed.append((page.business_busy, page.export_button.isEnabled(),
                         controller.worker is None, len(controller.queue)))
        return str(target), ''

    monkeypatch.setattr(QFileDialog, 'getSaveFileName', choose)
    page.export_button.click()
    wait_for(lambda: not controller.busy)
    assert observed == [(True, False, True, 1)]
    assert completed == [True] and target.is_file()
    assert page.export_button.isEnabled()


@pytest.mark.parametrize('outcome', ['cancel', 'error'])
def test_cancelled_or_failed_picker_releases_queue_and_allows_retry(controller, tmp_path, monkeypatch, outcome):
    page = controller.panel.today_dialog
    completed, observed = [], []

    def choose(*args, **kwargs):
        controller.enqueue('匿名后续工作', lambda **_: completed.append('finished'))
        observed.append((controller.worker is None, len(controller.queue)))
        if outcome == 'error':
            raise OSError('匿名保存路径不可用')
        return '', ''

    monkeypatch.setattr(QFileDialog, 'getSaveFileName', choose)
    page.export_button.click()
    wait_for(lambda: not controller.busy)
    assert observed == [(True, 1)] and completed == ['finished']
    assert page.export_button.isEnabled()
    assert ('取消' if outcome == 'cancel' else '匿名保存路径不可用') in page.status_note.text()
    target = tmp_path / 'retry.xlsx'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **kw: (str(target), ''))
    page.export_button.click()
    wait_for(lambda: not controller.busy)
    assert target.is_file() and page.export_button.isEnabled()


@pytest.mark.parametrize('finish', ['shutdown', 'quit'])
def test_picker_return_after_page_finishes_cannot_start_export(controller, tmp_path, monkeypatch, finish):
    page = controller.panel.today_dialog
    target = tmp_path / 'never-created.xlsx'

    def choose(*args, **kwargs):
        if finish == 'quit':
            controller.request_quit()
            assert controller.closed and not controller.owns_lock
        else:
            page.shutdown()
        return str(target), ''

    monkeypatch.setattr(QFileDialog, 'getSaveFileName', choose)
    page.export_button.click()
    if page.worker is not None:
        wait_for(lambda: page.worker is None)
    assert page.worker is None and not target.exists()


def test_closed_dialog_discards_old_picker_but_can_reopen(controller, tmp_path, monkeypatch):
    page = TodayDeliveryDialog(controller.service)
    page.show()
    target = tmp_path / 'never-created.xlsx'
    closed = []
    def choose(*args, **kwargs):
        closed.append(page.close())
        return str(target), ''
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', choose)
    try:
        page.export_projects()
        if page.worker is not None:
            wait_for(lambda: page.worker is None)
        assert closed == [True] and not target.exists()
        # 正常关页可重新打开；只作废已关闭前发起的那次保存。
        page.show()
        retry = tmp_path / 'reopened.xlsx'
        monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **kw: (str(retry), ''))
        page.export_button.click()
        wait_for(lambda: page.worker is None)
        assert retry.is_file()
    finally:
        page.shutdown()
        page.deleteLater()
