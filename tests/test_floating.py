import importlib
import importlib.util
import time
from pathlib import Path
from threading import Event
from zipfile import ZipFile

import pytest
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QTimer, QUrl
from PySide6.QtGui import QDropEvent
from PySide6.QtTest import QTest, QSignalSpy
from PySide6.QtWidgets import QApplication

from desktop_service import DesktopService
from compact_ui import ProjectDeliveryDialog


@pytest.fixture(scope='module')
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def module():
    assert importlib.util.find_spec('floating'), 'Floating ball is not implemented'
    return importlib.import_module('floating')


def controller(tmp_path):
    assert importlib.util.find_spec('desktop_controller'), 'Floating lifecycle is not implemented'
    cls = importlib.import_module('desktop_controller').FloatingController
    value = cls(DesktopService(tmp_path / 'workbox.db'), tray_enabled=False)
    assert value.start()
    return value


def wait(predicate, seconds=8):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        QApplication.processEvents()
        if predicate():
            return
        # 释放 GIL，让真实 FileJob 的 Python 复制和数据库逻辑继续执行。
        time.sleep(0.01)
    assert predicate(), 'Native operation timed out'


def zip_at(path):
    with ZipFile(path, 'w') as stream:
        stream.writestr('fixture.txt', 'isolated not executable')
    return path


def drop(widget, mime):
    event = QDropEvent(QPointF(38, 38), Qt.CopyAction | Qt.MoveAction, mime, Qt.LeftButton, Qt.NoModifier)
    widget.dropEvent(event)
    return event


def project_action(panel, project_id, text):
    menu = panel._test_project_menu = panel.project_context_menu(project_id)
    for item in menu.actions():
        if item.text() == text:
            return item
        submenu = item.menu()
        if submenu:
            for child in submenu.actions():
                if child.text() == text:
                    return child
    pytest.fail(f'项目右键菜单缺少：{text}')


def test_decoder_prioritizes_real_zip_urls_over_filename_text(qapp, tmp_path):
    mime = QMimeData()
    mime.setText('not a project')
    path = zip_at(tmp_path / '成品.zip')
    mime.setUrls([QUrl.fromLocalFile(str(path))])
    assert module().decode_mime(mime) == {'kind': 'files', 'paths': [str(path)]}
    mime = QMimeData()
    mime.setText('成品.zip')
    assert module().decode_mime(mime)['kind'] == 'text'


def test_remote_and_virtual_files_are_not_downloaded_or_invented(qapp):
    mime = QMimeData()
    mime.setUrls([QUrl('https://example.invalid/file.zip')])
    assert module().decode_mime(mime)['kind'] == 'unsupported'
    mime = QMimeData()
    mime.setData('application/x-qt-windows-mime;value="FileGroupDescriptorW"', b'virtual')
    assert module().decode_mime(mime)['kind'] == 'unsupported'


def test_html_is_plain_text_not_executed_or_fetched(qapp):
    mime = QMimeData()
    mime.setHtml('<div>1. 客户工具</div><script>malicious()</script><img src="https://example.invalid/x"><div>2. 合同工具</div>')
    data = module().decode_mime(mime)
    assert data['kind'] == 'text' and data['text'].splitlines() == ['1. 客户工具', '2. 合同工具']


def test_ball_is_small_round_surface_and_click_opens_not_moves(qapp):
    orb = module().FloatingBall()
    try:
        orb.show()
        assert orb.width() == orb.height() and 64 <= orb.width() <= 88
        assert orb.testAttribute(Qt.WA_TranslucentBackground)
        opened = QSignalSpy(orb.open_requested)
        QTest.mouseClick(orb, Qt.LeftButton, pos=QPoint(38, 38))
        assert opened.count() == 1
        QTest.mousePress(orb, Qt.LeftButton, pos=QPoint(38, 38))
        QTest.mouseMove(orb, QPoint(68, 48), delay=30)
        QTest.mouseRelease(orb, Qt.LeftButton, pos=QPoint(68, 48))
        assert opened.count() == 1
    finally:
        orb.hide()
        orb.deleteLater()


def test_startup_only_shows_ball_close_manager_returns_to_ball(qapp, tmp_path):
    app = controller(tmp_path)
    try:
        assert app.orb.isVisible() and not app.panel.isVisible()
        QTest.mouseClick(app.orb, Qt.LeftButton, pos=QPoint(38, 38))
        assert app.panel.isVisible()
        app.panel.close()
        assert not app.panel.isVisible() and app.orb.isVisible()
        assert app.service.db.counts() == {'projects': 0, 'batches': 0, 'zips': 0}
    finally:
        app.shutdown()


def test_text_drop_creates_folders_and_zip_drop_copies_after_stable_wait(qapp, tmp_path):
    app = controller(tmp_path)
    try:
        mime = QMimeData()
        mime.setText('1. 客户档案系统\n2. 合同工具，加急')
        event = drop(app.orb, mime)
        assert event.isAccepted() and event.dropAction() == Qt.CopyAction
        wait(lambda: not app.busy)
        assert app.service.db.counts()['projects'] == 2
        assert not app.panel.isVisible()
        path = zip_at(tmp_path / '客户档案系统.zip')
        before = path.read_bytes()
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(path))])
        event = drop(app.orb, mime)
        # Qt source must not independently delete: intake keeps the original and verifies a copy.
        assert event.dropAction() == Qt.CopyAction
        wait(lambda: not app.busy)
        assert path.exists() and app.service.db.counts()['zips'] == 0
        start = time.time()
        app.service.scan_archives(now=start)
        app.enqueue('检查归档', lambda **kw: app.service.scan_archives(now=start+11, **kw))
        wait(lambda: not app.busy)
        assert path.exists()
        project = app.service.get_project(1)
        assert project['status'] == '待交付'
        assert Path(project['zips'][0]['path']).read_bytes() == before
        assert not app.panel.isVisible()
    finally:
        app.shutdown()


def test_manager_selection_changes_state_and_urgency_without_a_different_page(qapp, tmp_path):
    app = controller(tmp_path)
    try:
        app.service.receive_text('客户档案系统')
        app.show_panel()
        app.panel.project_list.setCurrentRow(0)
        project_action(app.panel, 1, '开发中').trigger()
        wait(lambda: not app.busy)
        project_action(app.panel, 1, '加急').trigger()
        wait(lambda: not app.busy)
        assert app.service.get_project(1)['status'] == '开发中'
        assert app.service.get_project(1)['urgent'] == 1
    finally:
        app.shutdown()


def test_manager_delivery_confirmation_defaults_no_then_explicit_yes(qapp, tmp_path):
    app = controller(tmp_path)
    defaults = []
    zip_id = None
    def answer(accept):
        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, ProjectDeliveryDialog) and widget.isVisible():
                widget.version_box.setCurrentIndex(widget.version_box.findData(zip_id))
                defaults.append(widget.cancel_button.isDefault())
                (widget.confirm_button if accept else widget.cancel_button).click()
                return
    try:
        app.service.receive_text('客户档案系统')
        zip_id = app.service.import_zip(zip_at(tmp_path / '客户档案系统.zip'))['zip']['id']
        app.show_panel()
        app.panel.project_list.setCurrentRow(0)
        assert project_action(app.panel, 1, '确认交付…').isEnabled()
        QTimer.singleShot(40, lambda: answer(False))
        project_action(app.panel, 1, '确认交付…').trigger()
        assert app.service.get_project(1)['status'] == '待交付'
        QTimer.singleShot(40, lambda: answer(True))
        project_action(app.panel, 1, '确认交付…').trigger()
        wait(lambda: not app.busy)
        assert defaults == [True, True]
        assert app.service.get_project(1)['status'] == '已交付'
        assert app.service.get_project(1)['delivered_zip_id'] == zip_id
    finally:
        app.shutdown()


def test_position_is_persisted_and_offscreen_position_is_clamped(qapp, tmp_path):
    app = controller(tmp_path)
    app.orb.move(140, 150)
    app.shutdown()
    app = controller(tmp_path)
    try:
        assert app.orb.pos() == QPoint(140, 150)
    finally:
        app.shutdown()
    service = DesktopService(tmp_path / 'workbox.db')
    service.set_setting('orb_position', [99999, 99999])
    app = controller(tmp_path)
    try:
        assert any(screen.availableGeometry().contains(app.orb.geometry()) for screen in qapp.screens())
    finally:
        app.shutdown()


def test_same_database_second_instance_wakes_first_without_a_second_ball(qapp, tmp_path):
    first = controller(tmp_path)
    try:
        second = importlib.import_module('desktop_controller').FloatingController(first.service, tray_enabled=False)
        try:
            assert second.start() is False
            wait(lambda: first.panel.isVisible())
        finally:
            second.shutdown()
        assert first.orb.isVisible()
    finally:
        first.shutdown()


def test_async_failure_does_not_discard_next_dropped_zip(qapp, tmp_path):
    app = controller(tmp_path)
    try:
        bad = tmp_path / 'bad.zip'
        bad.write_bytes(b'not ZIP')
        good = zip_at(tmp_path / 'good.zip')
        app.receive({'kind': 'files', 'paths': [str(bad), str(good)]})
        wait(lambda: not app.busy)
        start = time.time()
        app.service.scan_archives(now=start)
        app.enqueue('检查归档', lambda **kw: app.service.scan_archives(now=start+11, **kw))
        wait(lambda: not app.busy)
        assert bad.exists() and good.exists()
        assert app.service.db.counts()['zips'] == 1
        assert any(row['filename'] == bad.name and row['status'] == 'failed'
                   for row in app.service.intake_log.list_recent())
        assert app.service.list_projects()[0]['status'] == '待交付'
    finally:
        app.shutdown()


def test_close_panel_does_not_cancel_but_quit_drains_background_task(qapp, tmp_path):
    app = controller(tmp_path)
    entered = Event()
    from concurrent.futures import CancelledError
    def slow(*, cancel_event, progress):
        entered.set()
        cancel_event.wait(5)
        raise CancelledError()
    try:
        app.enqueue('测试后台取消', slow)
        wait(entered.is_set)
        app.show_panel()
        app.panel.close()
        assert app.busy and app.orb.isVisible()
        app.request_quit()
        wait(lambda: app.closed)
        assert not app.orb.isVisible() and not app.busy
    finally:
        app.shutdown()


def test_direct_shutdown_with_queued_finished_signal_has_no_late_exception(qapp, tmp_path, monkeypatch):
    import sys
    from concurrent.futures import CancelledError
    errors = []
    monkeypatch.setattr(sys, 'excepthook', lambda *args: errors.append(args))
    app = controller(tmp_path)
    entered = Event()
    def operation(*, cancel_event, progress):
        entered.set()
        cancel_event.wait(5)
        raise CancelledError()
    app.enqueue('关闭测试', operation)
    wait(entered.is_set)
    app.shutdown()
    QTest.qWait(50)
    assert errors == []


def test_review_dialog_saves_and_updates_orb_count(qapp, tmp_path):
    from PySide6.QtWidgets import QDialog
    app = controller(tmp_path)
    try:
        app.service.receive_text('描述内容需核对。')
        app.show_panel()
        app.panel.tabs.setCurrentIndex(2)
        app.panel.receipt_list.setCurrentRow(0)
        def confirm():
            for dialog in QApplication.topLevelWidgets():
                if isinstance(dialog, QDialog) and dialog.windowTitle() == '核对项目消息':
                    if not dialog.table.rowCount():
                        dialog.add_row('核对后的项目', False)
                    else:
                        dialog.table.item(0, 0).setText('核对后的项目')
                    dialog.save_button.click()
        QTimer.singleShot(70, confirm)
        app.panel.review_button.click()
        assert app.service.db.counts()['projects'] == 1
        assert app.orb.count == 1
    finally:
        app.shutdown()


def test_long_project_name_does_not_expand_panel_beyond_screen(qapp, tmp_path):
    app = controller(tmp_path)
    try:
        app.service.capture_message('原文', '长名称测试', [importlib.import_module('parser').ParsedProject('超长项目名称' * 30)])
        app.show_panel()
        app.panel.project_list.setCurrentRow(0)
        qapp.processEvents()
        assert app.panel.width() <= app.orb.screen().availableGeometry().width()-24
    finally:
        app.shutdown()


def test_panel_native_frame_does_not_fall_below_screen(qapp, tmp_path):
    app = controller(tmp_path)
    try:
        area = app.orb.screen().availableGeometry()
        app.orb.move(area.right()-90, area.center().y())
        app.show_panel()
        qapp.processEvents()
        assert area.contains(app.panel.frameGeometry()), (area, app.panel.frameGeometry())
    finally:
        app.shutdown()


def test_status_and_package_text_not_clipped_in_compact_height(qapp, tmp_path):
    app = controller(tmp_path)
    try:
        app.service.receive_text('客户工具')
        app.show_panel()
        app.panel.resize(840, 492)
        app.panel.project_list.setCurrentRow(0)
        app.panel.show_notice('ZIP 已归档，原件保留。\n详细处理位置只在完整提示中显示。')
        qapp.processEvents()
        assert app.panel.document_pane.parentWidget() is app.panel.document_side
        assert app.panel.rect().contains(app.panel.document_pane.mapTo(app.panel, app.panel.document_pane.rect().bottomRight()))
        menu = app.panel.project_context_menu(1)
        menu.popup(app.panel.project_list.mapToGlobal(QPoint(20, 20)))
        qapp.processEvents()
        assert all(menu.rect().contains(menu.actionGeometry(item)) for item in menu.actions() if not item.isSeparator())
        menu.close()
        dialog = ProjectDeliveryDialog(app.service.get_project(1), app.panel)
        dialog.show()
        qapp.processEvents()
        for control in (dialog.version_box, dialog.confirm_button, dialog.cancel_button):
            assert control.height() >= control.minimumSizeHint().height(), (control.height(), control.minimumSizeHint())
            assert dialog.rect().contains(control.mapTo(dialog, control.rect().bottomRight()))
        dialog.cancel_button.click()
    finally:
        app.shutdown()


def test_paste_message_button_does_not_move_clipboard_files(qapp, tmp_path, monkeypatch):
    app = controller(tmp_path)
    path = zip_at(tmp_path / 'clipboard.zip')
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path))])
    class IsolatedClipboard:
        def mimeData(self):
            return mime
    monkeypatch.setattr(QApplication, 'clipboard', lambda: IsolatedClipboard())
    try:
        app.paste()
        wait(lambda: not app.busy)
        assert path.exists(), 'Pasting messages must not silently move copied file URLs'
        assert app.service.db.counts()['zips'] == 0
    finally:
        app.shutdown()
