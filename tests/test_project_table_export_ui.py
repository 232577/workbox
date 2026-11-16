"""Project table export captures the displayed or explicitly checked scope."""
from concurrent.futures import CancelledError
import threading
import time

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

import delivery_export
from desktop_controller import FloatingController
from desktop_service import DesktopService
from native_files import identity


@pytest.fixture
def controller(tmp_path):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    service = DesktopService(tmp_path / 'data/workbox.db')
    receipt = service.receive_text('\n'.join(f'{n}. 匿名范围项目{n}' for n in range(1, 6)))
    with service.db.connect() as connection:
        connection.execute('DELETE FROM project_type_links')
        for pid in receipt['project_ids']:
            connection.execute('UPDATE projects SET created_at=? WHERE id=?',
                ('2026-11-05T16:30:00Z' if pid <= 3 else '2026-11-06T16:30:00Z', pid))
    service.set_status(1, '待交付')
    value = FloatingController(service, tray_enabled=False)
    assert value.start()
    value.monitor_timer.stop()
    value.show_panel()
    app.processEvents()
    yield value
    value.shutdown()
    value.dispose_ui()


def ids(panel):
    return tuple(panel.project_list.item(i).data(Qt.UserRole) for i in range(panel.project_list.count()))


def wait(controller):
    deadline = time.monotonic() + 5
    while controller.busy and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(.01)
    assert not controller.busy
    QApplication.processEvents()


def test_export_menu_is_available_in_every_status_and_single_highlight_is_not_checked(controller):
    panel = controller.panel
    for state in (None, '待开发', '开发中', '待交付', '已交付'):
        panel.status_buttons[state].click()
        QApplication.processEvents()
        assert panel.project_export_button.isVisible()
        assert panel.project_export_filtered_action.text() == f'导出当前筛选结果（{len(ids(panel))}项）'
        assert panel.project_export_filtered_action.isEnabled() == bool(ids(panel))
        assert panel.project_export_checked_action.text() == '导出已勾选项目（0项）'
        assert not panel.project_export_checked_action.isEnabled()
        assert panel.pending_export_action.isVisible() == (state == '待开发')


def test_filtered_export_freezes_date_type_status_and_order_before_save_picker(controller, tmp_path, monkeypatch):
    panel = controller.panel
    panel.added_date_filter.set_day('2026-11-06')
    panel.type_filter.setCurrentIndex(panel.type_filter.findData(0))
    panel.status_buttons['待开发'].click()
    expected = ids(panel)
    assert set(expected) == {2, 3}
    calls = []
    target = tmp_path / 'table.xlsx'
    target.write_bytes(b'previous')
    previous = identity(target.stat())

    def save(*args, **kwargs):
        assert controller.choosing_pending_export and controller.busy
        assert '当前筛选结果' in args[2] and '2项' in args[2]
        panel.search.setText('没有匹配')
        return str(target), ''

    def export(db, destination, **kwargs):
        calls.append((db, destination, kwargs))
        return {'path': str(destination), 'project_count': len(kwargs['project_ids'])}

    monkeypatch.setattr(QFileDialog, 'getSaveFileName', save)
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.Yes)
    monkeypatch.setattr(delivery_export, 'export_project_table', export, raising=False)
    monkeypatch.setattr(controller, 'refresh', lambda: pytest.fail('Read-only export refreshed business data'))
    panel.project_export_filtered_action.trigger()
    wait(controller)
    assert len(calls) == 1 and calls[0][:2] == (panel.service.db, target)
    options = calls[0][2]
    assert options['project_ids'] == expected and isinstance(options['project_ids'], tuple)
    assert options['replace_existing'] and options['expected_target_identity'] == previous
    assert all(text in options['scope_label'] for text in ('当前筛选结果', '2项', '首次新增日期', '2026-11-06', '待开发', '未分类'))
    assert '当前筛选结果' in panel.notice.toolTip() and '2' in panel.notice.toolTip()


def test_checked_export_uses_only_explicit_checks_and_never_falls_back(controller, tmp_path, monkeypatch):
    panel = controller.panel
    calls = []
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **k: (str(tmp_path / 'table.xlsx'), ''))
    monkeypatch.setattr(delivery_export, 'export_project_table',
        lambda db, path, **kw: calls.append(kw) or {'path': str(path), 'project_count': len(kw['project_ids'])}, raising=False)
    panel.project_list.setCurrentRow(0)
    panel.request_project_table_export('checked')
    assert not calls and not controller.busy
    panel.bulk_mode.setChecked(True)
    panel.project_list.clearSelection()
    panel.project_list.item(1).setSelected(True)
    panel.project_list.item(3).setSelected(True)
    expected = tuple(panel.project_list.item(i).data(Qt.UserRole) for i in (1, 3))
    assert panel.project_export_checked_action.text() == '导出已勾选项目（2项）'
    panel.project_export_checked_action.trigger()
    panel.project_list.clearSelection()
    wait(controller)
    assert len(calls) == 1 and calls[0]['project_ids'] == expected
    assert '已勾选项目' in calls[0]['scope_label']
    panel.request_project_table_export('checked')
    assert len(calls) == 1


def test_empty_results_can_clear_all_filters_and_cannot_export(controller, monkeypatch):
    panel = controller.panel
    panel.added_date_filter.set_day('2026-11-06')
    panel.batch_filter.setCurrentIndex(1)
    panel.type_filter.setCurrentIndex(1)
    panel.status_buttons['已交付'].click()
    panel.search.setText('没有匹配')
    assert not ids(panel) and panel.empty_clear_filters_button.isVisible()
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **k: pytest.fail('Empty scope opened save picker'))
    panel.request_project_table_export('filtered')
    panel.empty_clear_filters_button.click()
    assert len(ids(panel)) == 5
    assert panel.status_buttons[None].isChecked() and panel.search.text() == ''
    assert all(control.currentData() is None for control in (panel.batch_filter, panel.type_filter))
    assert panel.added_date_filter.selection() is None
    assert not panel.empty_clear_filters_button.isVisible()


def test_first_added_day_and_status_counts_stay_direct_with_types_in_more_filters(controller):
    panel = controller.panel
    for width in (660, 1200):
        panel.resize(width, 640)
        QApplication.processEvents()
        assert not panel.project_filter_panel.isVisible()
        assert not panel.type_filter.isVisible() and not panel.batch_filter.isVisible()
        assert panel.added_date_filter.isVisible() and panel.status_segments.isVisible()
        assert panel.project_count_context.text() == '首次记录\n日常项目'
        panel.project_filter_button.click()
        QApplication.processEvents()
        assert panel.type_filter.isVisible() and panel.batch_filter.isVisible()
        assert panel.project_filter_panel.isAncestorOf(panel.type_filter)
        assert panel.project_filter_panel.isAncestorOf(panel.batch_filter)
        assert not panel.type_filter.geometry().intersects(panel.batch_filter.geometry())
        assert panel.type_filter.width() >= panel.type_filter.minimumSizeHint().width()
        assert panel.project_queue_header.rect().contains(panel.project_export_button.geometry())
        panel.project_filter_button.click()
        QApplication.processEvents()
    panel.status_buttons['待交付'].click()
    picker = panel.added_date_filter
    picker.set_day('2026-11-06')
    assert picker.caption() == '2026-11-06'
    assert panel.status_buttons[None].text() == '全部\n3'
    assert panel.status_buttons['待开发'].text() == '待开发\n2'
    assert panel.status_buttons['待交付'].text() == '待交付\n1'
    assert panel.project_list_heading.text() == '项目清单 · 1件'


def test_failed_read_disables_new_export_until_successful_refresh(controller, monkeypatch):
    panel = controller.panel
    original = panel.service.project_workbench_snapshot
    monkeypatch.setattr(panel.service, 'project_workbench_snapshot', lambda *a, **k: (_ for _ in ()).throw(OSError('匿名读取失败')))
    panel.search.setText('新的筛选')
    assert len(ids(panel)) == 5 and panel._project_read_error
    assert not panel.project_export_filtered_action.isEnabled()
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **k: pytest.fail('Stale scope exported'))
    panel.request_project_table_export('filtered')
    monkeypatch.setattr(panel.service, 'project_workbench_snapshot', original)
    panel.search.clear()
    assert panel.project_export_filtered_action.isEnabled()


def test_filter_refresh_deferred_by_scroll_does_not_export_mismatched_scope(controller, monkeypatch):
    panel = controller.panel
    scroll = panel.project_list.verticalScrollBar()
    scroll.setSliderDown(True)
    try:
        panel.search.setText('没有匹配')
        assert len(ids(panel)) == 5 and panel._refresh_after_drag
        assert not panel.project_export_filtered_action.isEnabled()
        monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **k: pytest.fail('Deferred scope exported'))
        panel.request_project_table_export('filtered')
    finally:
        scroll.setSliderDown(False)
    assert not ids(panel)


@pytest.mark.parametrize('cancel_at', ['save', 'overwrite', 'quit'])
def test_cancel_picker_or_quit_during_picker_never_enqueues_export(controller, tmp_path, monkeypatch, cancel_at):
    panel = controller.panel
    target = tmp_path / 'table.xlsx'
    target.write_bytes(b'previous')
    def save(*a, **kw):
        if cancel_at == 'quit':
            controller.request_quit()
        return ('', '') if cancel_at == 'save' else (str(target), '')
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', save)
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.No if cancel_at == 'overwrite' else QMessageBox.Yes)
    monkeypatch.setattr(delivery_export, 'export_project_table', lambda *a, **k: pytest.fail('Cancelled picker started export'), raising=False)
    panel.project_export_filtered_action.trigger()
    wait(controller)
    assert controller.worker is None and not controller.queue and target.read_bytes() == b'previous'
    assert controller.closed == (cancel_at == 'quit')


def test_snapshot_validation_error_is_persistent_and_retry_keeps_frozen_scope(controller, tmp_path, monkeypatch):
    panel = controller.panel
    target = tmp_path / 'table.xlsx'
    expected = ids(panel)
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **k: (str(target), ''))
    def fail(*a, **kw):
        assert kw['project_ids'] == expected
        raise ValueError('项目范围已变化，请刷新清单')
    monkeypatch.setattr(delivery_export, 'export_project_table', fail, raising=False)
    panel.project_export_filtered_action.trigger()
    wait(controller)
    assert '项目范围已变化' in panel.notice.toolTip() and not panel._notice_timer.isActive()
    assert not target.exists() and ids(panel) == expected
    monkeypatch.setattr(delivery_export, 'export_project_table',
        lambda db, path, **kw: {'path': str(path), 'project_count': len(kw['project_ids'])})
    panel.project_export_filtered_action.trigger()
    wait(controller)
    assert '已导出 5 个项目' in panel.notice.toolTip()


def test_real_ui_export_reads_back_exact_ids_date_state_type_and_scope(controller, tmp_path, monkeypatch):
    from openpyxl import load_workbook
    panel = controller.panel
    panel.added_date_filter.set_day('2026-11-06')
    panel.type_filter.setCurrentIndex(panel.type_filter.findData(0))
    panel.status_buttons['待开发'].click()
    expected = ids(panel)
    assert set(expected) == {2, 3}
    target = tmp_path / 'real-ui-project-table.xlsx'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **k: (str(target), ''))
    panel.project_export_filtered_action.trigger()
    wait(controller)
    with target.open('rb') as source:
        book = load_workbook(source)
        assert book.sheetnames == ['项目清单', '导出说明']
        rows = list(book['项目清单'].values)
        records = [dict(zip(rows[0], row)) for row in rows[1:]]
        assert tuple(row['项目编号'] for row in records) == tuple(map(str, expected))
        assert all(row['首次新增日期'] == '2026-11-06' and row['当前状态'] == '待开发'
                   and row['项目类型'] == '未分类' for row in records)
        notes = dict(list(book['导出说明'].values)[1:])
        assert notes['项目数'] == 2
        assert all(text in notes['导出范围'] for text in ('当前筛选结果（2项）', '2026-11-06', '待开发', '未分类'))
        book.close()
    assert '已导出 2 个项目' in panel.notice.toolTip()


@pytest.mark.parametrize('quit_app', [False, True])
def test_new_export_cancel_and_quit_keep_worker_lifecycle(controller, tmp_path, monkeypatch, quit_app):
    panel = controller.panel
    entered = threading.Event()
    def export(db, path, *, cancel_event, **kw):
        entered.set()
        assert cancel_event.wait(3)
        raise CancelledError()
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **k: (str(tmp_path / 'table.xlsx'), ''))
    monkeypatch.setattr(delivery_export, 'export_project_table', export, raising=False)
    panel.project_export_filtered_action.trigger()
    assert entered.wait(2) and controller.busy
    assert not panel.project_export_button.isEnabled()
    if quit_app:
        controller.request_quit()
    else:
        panel.cancel_button.click()
    wait(controller)
    assert controller.worker is None and not controller.queue
    assert controller.closed == quit_app
    if not quit_app:
        assert '已取消导出项目表' in panel.notice.toolTip()


def test_queued_new_export_cancel_preserves_running_silent_scan(controller, tmp_path, monkeypatch):
    panel = controller.panel
    release = threading.Event()
    def scan(*, cancel_event, **kw):
        assert release.wait(3)
        assert not cancel_event.is_set()
        return {'archived': [], 'pending': 0, 'errors': []}
    monkeypatch.setattr(controller.service, 'scan_archives', scan)
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **k: (str(tmp_path / 'table.xlsx'), ''))
    monkeypatch.setattr(delivery_export, 'export_project_table', lambda *a, **k: pytest.fail('Cancelled export started'), raising=False)
    controller.check_archives()
    try:
        panel.project_export_filtered_action.trigger()
        assert len(controller.queue) == 1
        panel.cancel_button.click()
        assert not controller.queue and not controller.worker.cancel_event.is_set()
    finally:
        release.set()
        wait(controller)
