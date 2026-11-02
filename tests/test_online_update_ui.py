"""Update UI and shutdown checks use only anonymous temporary data."""
import importlib
from pathlib import Path
import sys
import threading
import time

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QTimer
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

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
def controller(qapp,tmp_path):
    service=DesktopService(tmp_path/'data/workbox.db')
    c=FloatingController(service,tray_enabled=False);assert c.start()
    c.monitor_timer.stop()
    yield c
    c.pending_update=None
    c.shutdown();c.dispose_ui();qapp.processEvents()


def module():
    assert (Path(__file__).resolve().parents[1]/'update_ui.py').is_file(), 'Missing update dialog'
    return importlib.import_module('update_ui')


def test_entry_does_not_check_network_automatically(controller,monkeypatch):
    monkeypatch.setattr(update_client,'check_for_update',lambda **_:pytest.fail('no startup network'))
    assert hasattr(controller.panel,'update_button')
    assert controller.panel.update_button.text()=='检查更新'
    assert controller.pending_update is None


def test_check_same_build_and_error_can_retry(qapp,tmp_path,monkeypatch):
    ui=module();called=[]
    monkeypatch.setattr(ui,'check_for_update',lambda **_:(called.append(True) or None))
    d=ui.UpdateDialog(cache_root=tmp_path);d.show()
    try:
        assert not called and not d.download_button.isEnabled()
        d.check_button.click();wait(qapp,lambda:d.worker is None)
        assert called and '没有更新' in d.status_label.text()
        def fail(**_):raise OSError('isolated offline')
        monkeypatch.setattr(ui,'check_for_update',fail)
        d.check_button.click();wait(qapp,lambda:d.worker is None)
        assert 'isolated offline' in d.status_label.text()
        assert d.check_button.isEnabled() and not d.install_button.isEnabled()
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


def test_download_requires_separate_default_no_confirmation(qapp,tmp_path,monkeypatch):
    ui=module();monkeypatch.setattr(sys,'frozen',True,raising=False)
    d=ui.UpdateDialog(cache_root=tmp_path);d.release=object();d.installer=tmp_path/'not-run.exe';d._sync()
    monkeypatch.setattr(ui,'verify_installer',lambda *a:None)
    seen=[]
    def question(*args):seen.append(args[-1]);return QMessageBox.No
    monkeypatch.setattr(QMessageBox,'question',question)
    d.confirm_install()
    assert seen==[QMessageBox.No] and d.result()!=QDialog.Accepted
    d.shutdown();d.deleteLater();qapp.processEvents()
