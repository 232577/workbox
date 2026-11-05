"""交付表导出入口；仅使用隔离数据和模拟保存对话框。"""
import sys
import threading
import time
import types

import pytest
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from desktop_service import DesktopService
from delivery_ui import TodayDeliveryDialog
from native_files import identity


@pytest.fixture
def page(tmp_path):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    page = TodayDeliveryDialog(DesktopService(tmp_path / 'data' / 'isolated.db'), embedded=True)
    page.show()
    app.processEvents()
    yield page
    page.shutdown()
    page.deleteLater()
    app.processEvents()


def wait(page):
    end = time.monotonic() + 5
    while page.worker is not None and time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(.01)
    assert page.worker is None


def test_cancel_does_not_start_worker(page, monkeypatch):
    from delivery_ui import QFileDialog, QMessageBox
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **k: ('', ''))
    assert '全部有效交付项目' in page.export_button.accessibleName()
    page.export_button.click()
    assert page.worker is None
    assert '取消' in page.status_note.text()


def test_export_ignores_filters_and_reports_success_without_mutation(page, tmp_path, monkeypatch):
    from delivery_ui import QFileDialog, QMessageBox
    target = tmp_path / 'table.xlsx'
    target.write_bytes(b'old')
    old = identity(target.stat())
    calls = []
    def export(db, path, **kwargs):
        calls.append((db, path, kwargs))
        return {'project_count': 3, 'path': str(path)}
    monkeypatch.setitem(sys.modules, 'delivery_export', types.SimpleNamespace(export_delivered_projects=export))
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **k: (str(target), 'Excel 工作簿 (*.xlsx)'))
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.Yes)
    page.search.setText('无匹配')
    changed = QSignalSpy(page.statistics_changed)
    page.export_button.click()
    wait(page)
    assert len(calls) == 1 and calls[0][0] is page.service.db
    assert calls[0][2]['replace_existing'] is True
    assert calls[0][2]['expected_target_identity'] == old
    assert '3' in page.status_note.text() and str(target) in page.status_note.text()
    assert changed.count() == 0


def test_export_error_and_cancelled_worker_are_safe(page, tmp_path, monkeypatch):
    from concurrent.futures import CancelledError
    from delivery_ui import QFileDialog
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **k: (str(tmp_path / 'out.xlsx'), ''))
    def fail(*a, **k):
        raise ValueError('隔离错误')
    monkeypatch.setitem(sys.modules, 'delivery_export', types.SimpleNamespace(export_delivered_projects=fail))
    page.export_button.click()
    wait(page)
    assert '隔离错误' in page.status_note.text()
    def cancel(*a, **k):
        raise CancelledError()
    monkeypatch.setitem(sys.modules, 'delivery_export', types.SimpleNamespace(export_delivered_projects=cancel))
    page.export_button.click()
    wait(page)
    assert '取消' in page.status_note.text()


def test_existing_file_rejected_without_starting_worker(page, tmp_path, monkeypatch):
    from delivery_ui import QFileDialog, QMessageBox
    target = tmp_path / 'existing.xlsx'
    target.write_bytes(b'original')
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **k: (str(target), ''))
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.No)
    page.export_button.click()
    assert page.worker is None and target.read_bytes() == b'original'


def test_busy_cannot_start_twice_and_shutdown_waits(page, tmp_path, monkeypatch):
    from delivery_ui import QFileDialog
    entered, release = threading.Event(), threading.Event()
    calls = []
    def export(db, path, **kwargs):
        calls.append(path)
        entered.set()
        assert release.wait(4)
        return {'project_count': 0, 'path': str(path)}
    monkeypatch.setitem(sys.modules, 'delivery_export', types.SimpleNamespace(export_delivered_projects=export))
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **k: (str(tmp_path / 'out.xlsx'), ''))
    page.export_button.click()
    assert entered.wait(2)
    assert not page.export_button.isEnabled()
    page.export_projects()
    assert len(calls) == 1
    release.set()
    page.shutdown()
    assert page.worker is None and '已导出' in page.status_note.text()


def test_export_button_moves_with_controls_and_keeps_natural_width(page):
    for width, height, expected in ((1000, 700, 'date'), (680, 460, 'search'), (1000, 700, 'date')):
        page.resize(width, height)
        page._sync_embedded_layout()
        QApplication.processEvents()
        target = page.date_controls if expected == 'date' else page.search_controls
        other = page.search_controls if expected == 'date' else page.date_controls
        assert target.indexOf(page.export_button) >= 0
        assert other.indexOf(page.export_button) == -1
        assert page.export_button.width() >= page.export_button.minimumSizeHint().width()
        assert page.export_button.width() < width // 2
