import importlib
import importlib.util
import time
import zipfile

import pytest
from PySide6.QtCore import QMimeData, QPointF, Qt, QTimer, QUrl
from PySide6.QtGui import QDropEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox, QComboBox, QDialog, QPushButton, QScrollArea
from PySide6.QtCore import QThread
import threading
from concurrent.futures import CancelledError

from app import WorkBox


def ui_module():
    assert importlib.util.find_spec("ui") is not None, "Task 4: native UI missing"
    return importlib.import_module("ui")


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


@pytest.fixture
def service(tmp_path):
    return WorkBox(tmp_path / "workbox.db")


def wait_until(condition, timeout=8):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QApplication.processEvents()
        if condition():
            return
        QTest.qWait(10)
    assert condition(), "UI operation did not finish within the test deadline"


def make_zip(path):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("fixture.txt", "isolated UI test")
    return path


def test_first_window_is_empty_and_has_no_seeded_data(qapp, service):
    window = ui_module().MainWindow(service)
    try:
        window.show()
        qapp.processEvents()
        assert "WorkBox" in window.windowTitle() and "v1.0" in window.windowTitle()
        assert {key: label.text() for key, label in window.count_labels.items()} == {
            "projects": "0", "batches": "0", "zips": "0"
        }
        assert window.project_table.rowCount() == 0
        assert window.empty_label.isVisible()
        assert service.db.counts() == {"projects": 0, "batches": 0, "zips": 0}
    finally:
        window.close()


def test_capture_dialog_preview_can_be_edited_before_save(qapp, service):
    dialog = ui_module().CaptureDialog(service)
    try:
        dialog.show()
        dialog.batch_edit.setCurrentText("UI测试批次")
        dialog.message_edit.setPlainText("1. 项目甲\n2. 项目乙（加急）")
        assert not dialog.save_button.isEnabled()
        QTest.mouseClick(dialog.preview_button, Qt.LeftButton)
        assert dialog.preview_table.rowCount() == 2
        dialog.preview_table.item(0, 0).setText("人工修正的项目甲")
        QTest.mouseClick(dialog.save_button, Qt.LeftButton)
        assert service.db.counts()["projects"] == 2
        assert service.get_project(1)["name"] == "人工修正的项目甲"
        assert service.get_project(2)["urgent"] == 1
        assert service.get_project(1)["raw_text"] == "1. 项目甲\n2. 项目乙（加急）"
    finally:
        dialog.close()


def test_editing_raw_message_invalidates_old_preview(qapp, service):
    dialog = ui_module().CaptureDialog(service)
    try:
        dialog.message_edit.setPlainText("项目甲")
        dialog.preview_button.click()
        assert dialog.save_button.isEnabled()
        dialog.message_edit.setPlainText("项目乙")
        assert not dialog.save_button.isEnabled()
        assert service.db.counts()["projects"] == 0
    finally:
        dialog.close()


def test_empty_capture_shows_inline_error_without_saving(qapp, service):
    dialog = ui_module().CaptureDialog(service)
    try:
        dialog.preview_button.click()
        assert dialog.error_label.text()
        assert not dialog.save_button.isEnabled()
        assert service.db.counts()["batches"] == 0
    finally:
        dialog.close()


def test_project_search_status_urgency_and_batch_views(qapp, service):
    service.capture_message("1. 客户工具\n2. 合同工具", "UI批次")
    window = ui_module().MainWindow(service)
    try:
        window.show()
        window.search_edit.setText("客户")
        wait_until(lambda: window.project_table.rowCount() == 1)
        window.project_table.selectRow(0)
        window.status_edit.setCurrentText("开发中")
        window.urgent_edit.click()
        assert service.get_project(1)["status"] == "开发中"
        assert service.get_project(1)["urgent"] == 1
        window.search_edit.clear()
        window.status_buttons["开发中"].click()
        wait_until(lambda: window.project_table.rowCount() == 1)
        window.nav_buttons[1].click()
        assert window.batch_table.rowCount() == 1
        assert window.pages.currentIndex() == 1
    finally:
        window.close()


def test_zip_import_runs_in_worker_and_refreshes_window(qapp, service, tmp_path):
    service.capture_message("客户工具", "UI批次")
    path = make_zip(tmp_path / "客户工具.zip")
    window = ui_module().MainWindow(service)
    try:
        window.show()
        window.import_paths([str(path)])
        wait_until(lambda: not window.busy)
        assert service.get_project(1)["status"] == "待交付"
        assert window.count_labels["zips"].text() == "1"
        assert window.zip_table.rowCount() == 1
        assert service.get_project(1)["delivered_at"] is None
    finally:
        window.close()


def test_native_drop_routes_local_zip_to_read_only_import(qapp, service, tmp_path):
    path = make_zip(tmp_path / "unmatched.zip")
    before = path.read_bytes()
    window = ui_module().MainWindow(service)
    try:
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(path))])
        event = QDropEvent(QPointF(25, 25), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        window.dropEvent(event)
        assert event.isAccepted()
        wait_until(lambda: not window.busy)
        assert service.db.counts()["zips"] == 1
        assert path.read_bytes() == before
        assert service.list_zips()[0]["project_id"] is None
    finally:
        window.close()


def test_import_batch_continues_after_invalid_file_and_reports_error(qapp, service, tmp_path):
    window = ui_module().MainWindow(service)
    try:
        window.import_paths([str(tmp_path / "missing.zip"), str(make_zip(tmp_path / "valid.zip"))])
        wait_until(lambda: not window.busy)
        assert service.db.counts()["zips"] == 1
        assert "失败 1" in window.notice_label.text()
    finally:
        window.close()


def test_delivery_dialog_defaults_to_no_and_requires_positive_click(qapp, service, tmp_path):
    service.capture_message("客户工具", "UI批次")
    service.import_zip(make_zip(tmp_path / "客户工具.zip"))
    window = ui_module().MainWindow(service)
    answers = []

    def answer(button):
        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, QMessageBox) and widget.isVisible():
                answers.append(widget.standardButton(widget.defaultButton()))
                widget.button(button).click()
                return

    try:
        window.show()
        window.project_table.selectRow(0)
        assert window.confirm_button.isEnabled()
        QTimer.singleShot(40, lambda: answer(QMessageBox.No))
        window.confirm_button.click()
        assert service.get_project(1)["status"] == "待交付"
        QTimer.singleShot(40, lambda: answer(QMessageBox.Yes))
        window.confirm_button.click()
        wait_until(lambda: not window.busy)
        assert answers == [QMessageBox.No, QMessageBox.No]
        assert service.get_project(1)["status"] == "已交付"
        assert not window.confirm_button.isEnabled()
    finally:
        window.close()


def test_changed_raw_text_cannot_resave_stale_projects_via_add_row(qapp, service):
    dialog = ui_module().CaptureDialog(service)
    try:
        dialog.message_edit.setPlainText("原项目")
        dialog.preview_button.click()
        dialog.message_edit.setPlainText("新项目消息")
        dialog.add_button.click()
        assert dialog.preview_table.rowCount() == 1
        assert dialog.preview_table.item(0, 0).text() == ""
    finally:
        dialog.close()


def test_primary_delivery_action_fits_without_scrolling_at_default_size(qapp, service, tmp_path):
    service.capture_message("客户工具", "UI批次")
    service.import_zip(make_zip(tmp_path / "客户工具.zip"))
    window = ui_module().MainWindow(service)
    try:
        window.resize(1280, 800)
        window.show()
        window.project_table.selectRow(0)
        window.notify("ZIP 已登记，等待人工确认交付。")
        qapp.processEvents()
        scroll = window.findChild(QScrollArea)
        bottom = window.confirm_button.mapTo(scroll.viewport(), window.confirm_button.rect().bottomRight())
        assert scroll.viewport().rect().contains(bottom), "Primary delivery action is clipped below detail viewport"
    finally:
        window.close()


def test_background_job_does_not_block_ui_and_close_cancels_safely(qapp, service):
    window = ui_module().MainWindow(service)
    entered = threading.Event()
    thread_identity = []

    def operation(*, cancel_event, progress):
        thread_identity.append(QThread.currentThread() is not qapp.thread())
        entered.set()
        cancel_event.wait(3)
        if cancel_event.is_set():
            raise CancelledError()
        raise RuntimeError("close did not cancel the worker")

    try:
        window.show()
        window._start_job("delivery", "isolated close test", operation)
        wait_until(entered.is_set)
        assert thread_identity == [True]
        assert window.busy
        QTimer.singleShot(30, window.close)
        wait_until(lambda: not window.isVisible())
        assert not window.busy
        assert service.db.counts() == {"projects": 0, "batches": 0, "zips": 0}
    finally:
        window.cancel_job()
        wait_until(lambda: not window.busy)
        window.close()


def test_manual_association_dialog_applies_selected_project_only(qapp, service, tmp_path):
    service.capture_message("1. 客户工具\n2. 合同工具", "UI批次")
    service.import_zip(make_zip(tmp_path / "unknown.zip"))
    window = ui_module().MainWindow(service)

    def choose():
        for dialog in QApplication.topLevelWidgets():
            if isinstance(dialog, QDialog) and dialog.windowTitle() == "将 ZIP 关联到项目":
                choice = dialog.findChild(QComboBox)
                choice.setCurrentIndex(choice.findData(2))
                for control in dialog.findChildren(QPushButton):
                    if control.text() == "确认关联":
                        control.click()

    try:
        window.show()
        window.show_page(2)
        window.zip_table.selectRow(0)
        QTimer.singleShot(40, choose)
        window.link_button.click()
        assert service.list_zips()[0]["project_id"] == 2
        assert service.get_project(1)["status"] == "待开发"
        assert service.get_project(2)["status"] == "待交付"
    finally:
        window.close()
