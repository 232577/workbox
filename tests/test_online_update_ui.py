"""Update UI and shutdown checks use only anonymous temporary data."""
import hashlib
from io import BytesIO
import importlib
from pathlib import Path
import sys
import threading
import time

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QTimer
from PySide6.QtGui import QAccessible
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QPushButton

from desktop_controller import FloatingController
from desktop_service import DesktopService
import update_client


@pytest.fixture(scope='module')
def qapp():
    app=QApplication.instance() or QApplication([]);app.setQuitOnLastWindowClosed(False)
    return app


def wait(qapp, predicate):
    deadline=time.monotonic()+6
    while time.monotonic()<deadline:
        qapp.processEvents()
        if predicate():return
        time.sleep(.01)
    assert predicate()


@pytest.fixture
def controller(qapp,tmp_path,monkeypatch):
    unrequested_checks = []
    monkeypatch.setattr(module(), 'check_for_update', lambda **_: unrequested_checks.append(True))
    service=DesktopService(tmp_path/'data/workbox.db')
    c=FloatingController(service,tray_enabled=False);assert c.start()
    c.monitor_timer.stop()
    yield c
    c.pending_update=None
    c.shutdown();c.dispose_ui();qapp.processEvents()
    assert not unrequested_checks


def module():
    assert (Path(__file__).resolve().parents[1]/'update_ui.py').is_file(), 'Missing update dialog'
    return importlib.import_module('update_ui')


def test_entry_does_not_check_network_automatically(controller):
    assert hasattr(controller.panel,'update_button')
    assert controller.panel.update_button.text()=='检查更新'
    assert controller.pending_update is None


def test_explicit_update_entry_checks_without_a_second_click(controller,monkeypatch):
    checked = threading.Event()

    def check(**_):
        checked.set()
        return None

    def close_after_check():
        dialog = controller.update_dialog
        if dialog is not None and checked.is_set() and dialog.worker is None:
            dialog.reject()

    monkeypatch.setattr(module(), 'check_for_update', check)
    timer = QTimer()
    timer.timeout.connect(close_after_check)
    timeout = QTimer()
    timeout.setSingleShot(True)
    timeout.timeout.connect(lambda: controller.update_dialog.reject())
    timer.start(10)
    timeout.start(900)
    try:
        controller.panel.update_button.click()
    finally:
        timer.stop()
        timeout.stop()
    assert checked.is_set()
    assert controller.update_dialog is None and controller.pending_update is None


def test_check_same_build_and_error_can_retry(qapp,tmp_path,monkeypatch):
    ui=module();called=[]
    monkeypatch.setattr(ui,'check_for_update',lambda **_:(called.append(True) or None))
    d=ui.UpdateDialog(cache_root=tmp_path);d.show()
    try:
        assert not called and not d.update_button.isEnabled()
        d.check_button.click();wait(qapp,lambda:d.worker is None)
        assert called and '没有更新' in d.status_label.text()
        def fail(**_):raise OSError('isolated offline')
        monkeypatch.setattr(ui,'check_for_update',fail)
        d.check_button.click();wait(qapp,lambda:d.worker is None)
        assert 'isolated offline' in d.status_label.text()
        assert d.check_button.isEnabled() and not d.update_button.isEnabled()
    finally:d.shutdown();d.close();d.deleteLater();qapp.processEvents()


def test_closing_cancels_network_before_dialog_disposal(qapp,tmp_path,monkeypatch):
    ui=module();entered=threading.Event();stopped=threading.Event()
    def operation(*,cancel_event,**_):
        entered.set();cancel_event.wait(5);stopped.set()
        from concurrent.futures import CancelledError
        raise CancelledError()
    monkeypatch.setattr(ui,'check_for_update',operation)
    d=ui.UpdateDialog(cache_root=tmp_path);d.show();d.check_button.click()
    wait(qapp,entered.is_set);d.reject()
    wait(qapp,lambda:d.worker is None)
    assert stopped.is_set() and d.result()==QDialog.Rejected
    d.shutdown();d.deleteLater();qapp.processEvents()


def test_backup_precedes_unlock_and_launch_follows_shutdown(controller,tmp_path,monkeypatch):
    import update_install
    package=tmp_path/'setup.exe';package.write_bytes(b'not-executed')
    release=object();sequence=[]
    assert hasattr(controller,'prepare_update_install'), 'Missing safe update integration'
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    monkeypatch.setattr(update_client,'verify_installer',lambda p,r:sequence.append('verify'))
    def backup(database,root):
        assert controller.owns_lock and not controller.closed
        sequence.append('backup');target=tmp_path/'backup.db';target.write_bytes(b'fixture');return target
    monkeypatch.setattr(update_install,'backup_database',backup)
    monkeypatch.setattr(update_install,'launch_installer',lambda *a:sequence.append('launch'))
    controller.prepare_update_install(release,package)
    assert controller.closed and not controller.owns_lock and 'launch' not in sequence
    controller.dispose_ui();controller.launch_pending_update()
    assert sequence==['verify','backup','launch']
    controller.launch_pending_update();assert sequence.count('launch')==1


def test_shutdown_failure_never_starts_installer(controller,tmp_path,monkeypatch):
    import update_install
    assert hasattr(controller,'prepare_update_install')
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    monkeypatch.setattr(update_client,'verify_installer',lambda *a:None)
    monkeypatch.setattr(update_install,'launch_installer',lambda *a:pytest.fail('unsafe installation'))
    with monkeypatch.context() as patch:
        patch.setattr(controller,'shutdown',lambda:(_ for _ in ()).throw(RuntimeError('isolated stop failure')))
        with pytest.raises(RuntimeError,match='isolated stop failure'):
            controller.prepare_update_install(object(),tmp_path/'setup.exe')
    assert controller.pending_update is None and controller.owns_lock and not controller.closed


def test_source_mode_cannot_replace_development_checkout(controller,tmp_path,monkeypatch):
    assert hasattr(controller,'prepare_update_install')
    monkeypatch.delattr(sys,'frozen',raising=False)
    with pytest.raises(ValueError):controller.prepare_update_install(object(),tmp_path/'setup.exe')
    assert not controller.closed and controller.owns_lock


def test_backup_failure_restores_current_session_without_install(controller,tmp_path,monkeypatch):
    import update_install
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    monkeypatch.setattr(update_client,'verify_installer',lambda *a:None)
    monkeypatch.setattr(update_install,'backup_database',lambda *a:(_ for _ in ()).throw(OSError('isolated full disk')))
    with pytest.raises(OSError,match='isolated full disk'):
        controller.prepare_update_install(object(),tmp_path/'setup.exe')
    assert controller.pending_update is None and controller.owns_lock and not controller.closed
    assert not controller.panel.document_pane._closed


def test_busy_session_is_not_cancelled_for_update(controller,tmp_path,monkeypatch):
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    controller.queue.append(('fixture',lambda **_:None,False,None))
    with pytest.raises(RuntimeError):controller.prepare_update_install(object(),tmp_path/'setup.exe')
    assert controller.queue and not controller.quitting and controller.pending_update is None
    controller.queue.clear()


@pytest.fixture
def offered_update(qapp,tmp_path,monkeypatch):
    ui = module()
    data = b'MZanonymous installer, never executed'
    tag = 'v1.0.0.20991231'
    filename = 'WorkBox-1.0.0-Setup-x64-20991231.exe'
    release = update_client.Release(tag, filename, len(data), hashlib.sha256(data).hexdigest(),
                                   update_client.RELEASE_ROOT + f'download/{tag}/{filename}', '匿名发布说明')
    monkeypatch.setattr(ui, 'check_for_update', lambda **_: release)
    def open_installer(url):
        assert url == release.url
        response = BytesIO(data)
        response.headers = {'Content-Length': str(len(data))}
        return response

    monkeypatch.setattr(update_client, '_open', open_installer)
    dialogs = []

    def show(*, installed=True):
        monkeypatch.setattr(sys, 'frozen', installed, raising=False)
        dialog = ui.UpdateDialog(cache_root=tmp_path)
        dialogs.append(dialog)
        dialog.show()
        dialog.check()
        wait(qapp, lambda: dialog.worker is None)
        return dialog

    yield show
    for dialog in dialogs:
        dialog.shutdown()
        dialog.close()
        dialog.deleteLater()
    qapp.processEvents()


def action(dialog, caption):
    actions = [control for control in dialog.findChildren(QPushButton)
               if control.isVisible() and control.text() == caption]
    assert len(actions) == 1, f'Expected a single visible {caption} action'
    return actions[0]


def test_one_update_action_downloads_verifies_and_hands_off(qapp,offered_update,monkeypatch):
    dialog = offered_update()
    monkeypatch.setattr(QMessageBox, 'question', lambda *_: pytest.fail('a second confirmation was opened'))
    update = action(dialog, '更新并重启')
    assert not update.isDefault() and action(dialog, '关闭').isDefault()
    update.click()
    assert not update.isEnabled()
    wait(qapp, lambda: dialog.worker is None)
    assert dialog.result() == QDialog.Accepted
    update_client.verify_installer(dialog.installer, dialog.release)


def test_download_percentage_is_readable_outside_the_progress_fill(qapp,offered_update,monkeypatch):
    dialog = offered_update()
    open_installer = update_client._open
    allow_finish = threading.Event()

    def open_paused(url):
        response = open_installer(url)
        read = response.read

        def read_until_verified(size):
            if response.tell():
                allow_finish.wait(5)
            return read(size)

        response.read = read_until_verified
        return response

    monkeypatch.setattr(update_client, '_open', open_paused)
    try:
        action(dialog, '更新并重启').click()
        wait(qapp, lambda: dialog.progress.value() == 100)
        assert dialog.progress.isVisible() and not dialog.progress.isTextVisible()
        assert '100%' in dialog.status_label.text()
        accessible_progress = QAccessible.queryAccessibleInterface(dialog.progress).valueInterface()
        assert accessible_progress.currentValue() == 100
    finally:
        allow_finish.set()


def test_closing_during_download_blocks_queued_success(qapp,offered_update):
    dialog = offered_update()
    action(dialog, '更新并重启').click()
    assert dialog.worker.wait(5000)
    assert dialog.worker.result.is_file()
    # Let download finish before Qt delivers its signal, then close first.
    dialog.reject()
    wait(qapp, lambda: dialog.worker is None)
    assert dialog.result() == QDialog.Rejected and dialog.installer is None
    assert '正在取消' in dialog.status_label.text()


def test_source_mode_downloads_without_handing_off(qapp,offered_update):
    dialog = offered_update(installed=False)
    action(dialog, '下载更新').click()
    wait(qapp, lambda: dialog.worker is None)
    update_client.verify_installer(dialog.installer, dialog.release)
    assert dialog.isVisible() and dialog.result() != QDialog.Accepted


def test_failed_download_can_retry_the_same_release(qapp,offered_update,monkeypatch):
    dialog = offered_update()
    with monkeypatch.context() as patch:
        def fail(_):
            raise OSError('isolated download failure')

        patch.setattr(update_client, '_open', fail)
        action(dialog, '更新并重启').click()
        wait(qapp, lambda: dialog.worker is None)
        assert 'isolated download failure' in dialog.status_label.text()
        assert dialog.isVisible() and dialog.installer is None
    action(dialog, '更新并重启').click()
    wait(qapp, lambda: dialog.worker is None)
    assert dialog.result() == QDialog.Accepted
