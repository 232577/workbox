"""增量更新界面与停机边界仅使用隔离目录和后台任务替身。"""
from concurrent.futures import CancelledError
from pathlib import Path
from types import SimpleNamespace
import sys
import threading
import time

import pytest
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication, QDialog

import update_client
import update_ui


@pytest.fixture(scope='module')
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def wait(qapp, predicate):
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return
        time.sleep(.01)
    assert predicate()


@pytest.fixture
def release():
    return SimpleNamespace(tag='v1.0.0.20991231', filename='WorkBox-1.0.0-Setup-x64-20991231.exe',
        size=8 * 1024 * 1024, sha256='a' * 64, url='', notes='匿名增量版本说明',
        delta=SimpleNamespace(from_tag=update_client.CURRENT_TAG, to_tag='v1.0.0.20991231',
            filename='anonymous.wbdelta', size=128 * 1024, sha256='b' * 64, url=''))


@pytest.fixture
def dialogs(qapp, tmp_path, monkeypatch, release):
    opened = []
    legacy_calls = []
    package = tmp_path / release.filename
    package.write_bytes(b'isolated download, never executed')

    def legacy(*args, **kwargs):
        legacy_calls.append((args, kwargs))
        return package

    monkeypatch.setattr(update_ui, 'download_release', legacy)
    monkeypatch.setattr(update_ui, 'check_for_update', lambda **_: release)

    def show(*, installed=True):
        monkeypatch.setattr(sys, 'frozen', installed, raising=False)
        dialog = update_ui.UpdateDialog(cache_root=tmp_path / 'cache')
        opened.append(dialog)
        dialog.show()
        dialog.check()
        wait(qapp, lambda: dialog.worker is None)
        return dialog

    yield show, legacy_calls, package
    for dialog in opened:
        dialog.shutdown()
        dialog.close()
        dialog.deleteLater()
    qapp.processEvents()


def backend(monkeypatch, download=None, verify=None):
    """此边界替身不执行网络、文件替换或真实安装。"""
    module = SimpleNamespace(download_update=download, verify_downloaded_update=verify)
    monkeypatch.setitem(sys.modules, 'incremental_update', module)
    return module


def test_installed_offer_shows_delta_and_available_full_size(dialogs):
    show, _, _ = dialogs
    dialog = show()
    text = dialog.status_label.text()
    assert '增量' in text and '128.0 KB' in text
    assert '完整安装包' in text and '8.0 MB' in text
    assert '兜底' in text or '改用' in text
    assert '安装完成' not in text


@pytest.mark.parametrize('mismatch', ['source', 'tag', 'target', 'size'])
def test_unsuitable_delta_is_not_advertised(dialogs, release, mismatch):
    show, _, _ = dialogs
    if mismatch == 'tag':
        release.delta.from_tag = 'v1.0.0.20000101'
    if mismatch == 'target':
        release.delta.to_tag = 'v1.0.0.20991230'
    if mismatch == 'size':
        release.delta.size = release.size
    dialog = show(installed=mismatch != 'source')
    assert '增量' not in dialog.status_label.text()
    assert '8.0 MB' in dialog.status_label.text()


def test_installed_download_dispatches_to_incremental_with_application_directory(qapp, dialogs, release, monkeypatch):
    show, legacy, _ = dialogs
    downloaded = Path('anonymous.wbdelta')
    calls = []

    def download(selected, cache, *, app_dir, cancel_event, progress, status):
        calls.append((selected, cache, app_dir))
        assert QThread.currentThread() != qapp.thread()
        status('正在下载并校验增量更新包…')
        progress(1, 1)
        return downloaded

    backend(monkeypatch, download=download)
    dialog = show()
    dialog.download()
    wait(qapp, lambda: dialog.worker is None)
    assert calls == [(release, dialog.cache_root, Path(sys.executable).resolve().parent)]
    assert not legacy
    assert dialog.installer == downloaded and isinstance(dialog.installer, Path)
    assert dialog.result() == QDialog.Accepted


def test_fallback_reason_and_progress_reach_only_gui_thread(qapp, dialogs, monkeypatch):
    show, legacy, package = dialogs
    entered = threading.Event()
    finish = threading.Event()
    reason = '增量更新不可用：匿名基线不符。正在改用完整安装包…'

    def download(*args, status, progress, **kwargs):
        status(reason)
        progress(1, 2)
        entered.set()
        assert finish.wait(5)
        return package

    backend(monkeypatch, download=download)
    dialog = show()
    threads = []
    original = dialog.status_label.setText

    def record(text):
        threads.append(QThread.currentThread())
        original(text)

    monkeypatch.setattr(dialog.status_label, 'setText', record)
    try:
        dialog.download()
        wait(qapp, lambda: entered.is_set() and '50%' in dialog.status_label.text())
        assert reason in dialog.status_label.text()
        assert all(thread == qapp.thread() for thread in threads)
        assert dialog.installer is None
        assert not legacy
    finally:
        finish.set()
        wait(qapp, lambda: dialog.worker is None)
    assert '安装完成' not in dialog.status_label.text()


def test_cancel_keeps_single_job_and_never_starts_legacy_fallback(qapp, dialogs, monkeypatch):
    show, legacy, _ = dialogs
    entered = threading.Event()
    stopped = threading.Event()
    calls = []

    def download(*args, cancel_event, status, **kwargs):
        calls.append(True)
        status('正在下载并校验增量更新包…')
        entered.set()
        cancel_event.wait(5)
        stopped.set()
        raise CancelledError()

    backend(monkeypatch, download=download)
    dialog = show()
    dialog.download()
    wait(qapp, entered.is_set)
    dialog.download()
    dialog.reject()
    wait(qapp, lambda: dialog.worker is None)
    assert stopped.is_set() and len(calls) == 1
    assert not legacy and dialog.installer is None
    assert dialog.result() == QDialog.Rejected


def test_source_mode_keeps_full_download_without_install_handoff(qapp, dialogs, monkeypatch):
    show, legacy, package = dialogs
    backend(monkeypatch, download=lambda *args, **kwargs: pytest.fail('源码模式不得启用增量安装'))
    dialog = show(installed=False)
    dialog.download()
    wait(qapp, lambda: dialog.worker is None)
    assert len(legacy) == 1 and dialog.installer == package
    assert dialog.isVisible() and dialog.result() != QDialog.Accepted
    assert '已下载并校验' in dialog.status_label.text()
    assert '源码' in dialog.mode_note.text()


def test_late_status_and_success_cannot_override_cancelled_close(qapp, dialogs, monkeypatch):
    show, legacy, package = dialogs

    def download(*args, status, progress, **kwargs):
        status('增量更新不可用：匿名原因。正在改用完整安装包…')
        progress(1, 1)
        return package

    backend(monkeypatch, download=download)
    dialog = show()
    dialog.download()
    assert dialog.worker.wait(5000)
    dialog.reject()
    wait(qapp, lambda: dialog.worker is None)
    assert dialog.result() == QDialog.Rejected and dialog.installer is None
    assert '正在取消' in dialog.status_label.text()
    assert '匿名原因' not in dialog.status_label.text()
    assert not legacy


def test_shutdown_ignores_queued_status_after_waiting_for_network(qapp, dialogs, monkeypatch):
    show, _, package = dialogs
    entered = threading.Event()

    def download(*args, cancel_event, status, **kwargs):
        entered.set()
        cancel_event.wait(5)
        status('不得写入已停机窗口的匿名进度')
        return package

    backend(monkeypatch, download=download)
    dialog = show()
    dialog.download()
    wait(qapp, entered.is_set)
    dialog.shutdown()
    qapp.processEvents()
    assert dialog.worker is None and dialog._closed
    assert dialog.installer is None and dialog.result() != QDialog.Accepted
    assert '不得写入' not in dialog.status_label.text()


@pytest.fixture
def controller(qapp, tmp_path):
    from desktop_controller import FloatingController
    from desktop_service import DesktopService
    service = DesktopService(tmp_path / 'data' / 'workbox.db')
    instance = FloatingController(service, tray_enabled=False)
    assert instance.start()
    instance.monitor_timer.stop()
    yield instance
    instance.pending_update = None
    instance.shutdown()
    instance.dispose_ui()
    qapp.processEvents()


def test_unified_reverification_precedes_backup_unlock_and_handoff(controller, tmp_path, release, monkeypatch):
    import update_install
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    package = tmp_path / 'anonymous.wbdelta'
    calls = []

    def verify(path, selected, app_dir):
        assert controller.owns_lock and not controller.closed
        assert (path, selected, app_dir) == (package, release, Path(sys.executable).resolve().parent)
        calls.append('verify')

    def backup(*args):
        assert controller.owns_lock and not controller.closed
        calls.append('backup')
        return tmp_path / 'recovery.db'

    backend(monkeypatch, verify=verify)
    monkeypatch.setattr(update_install, 'backup_database', backup)
    monkeypatch.setattr(update_install, 'launch_installer', lambda *args: calls.append('launch'))
    controller.prepare_update_install(release, package)
    assert controller.closed and not controller.owns_lock
    assert calls == ['verify', 'backup']
    controller.launch_pending_update()
    assert calls == ['verify', 'backup', 'launch']
    controller.launch_pending_update()
    assert calls.count('launch') == 1


def test_reverification_failure_leaves_data_session_open(controller, tmp_path, release, monkeypatch):
    monkeypatch.setattr(sys, 'frozen', True, raising=False)

    def fail(*args):
        raise ValueError('匿名基线在下载后变化')

    backend(monkeypatch, verify=fail)
    with pytest.raises(ValueError, match='匿名基线在下载后变化'):
        controller.prepare_update_install(release, tmp_path / 'anonymous.wbdelta')
    assert controller.owns_lock and not controller.closed and not controller.quitting
    assert controller.pending_update is None and controller.update_backup is None


def test_backup_failure_keeps_lock_and_resumes_application(controller, tmp_path, release, monkeypatch):
    import update_install
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    backend(monkeypatch, verify=lambda *args: None)

    def fail(*args):
        raise OSError('匿名恢复点空间不足')

    monkeypatch.setattr(update_install, 'backup_database', fail)
    monkeypatch.setattr(update_install, 'launch_installer', lambda *args: pytest.fail('不得启动安装'))
    with pytest.raises(OSError, match='匿名恢复点空间不足'):
        controller.prepare_update_install(release, tmp_path / 'anonymous.wbdelta')
    assert controller.owns_lock and not controller.closed and not controller.quitting
    assert controller.pending_update is None and controller.update_backup is None
    assert not controller.panel.document_pane._closed


def test_busy_and_source_sessions_do_not_reverify_or_shutdown(controller, tmp_path, release, monkeypatch):
    backend(monkeypatch, verify=lambda *args: pytest.fail('尚不可停机时不得校验或安装'))
    monkeypatch.delattr(sys, 'frozen', raising=False)
    with pytest.raises(ValueError):
        controller.prepare_update_install(release, tmp_path / 'anonymous.wbdelta')
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    controller.queue.append(('anonymous', lambda **_: None, False, None))
    try:
        with pytest.raises(RuntimeError):
            controller.prepare_update_install(release, tmp_path / 'anonymous.wbdelta')
        assert controller.queue and controller.owns_lock and not controller.closed
        assert controller.pending_update is None
    finally:
        controller.queue.clear()
