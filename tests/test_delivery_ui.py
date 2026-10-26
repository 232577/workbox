"""今日交付窗口只读取隔离数据库，打开动作仅验证目录信号。"""

import importlib
from datetime import datetime, timezone

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QAbstractItemView

import database
from desktop_service import DesktopService


@pytest.fixture(scope='module')
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


@pytest.fixture
def service(tmp_path, monkeypatch):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            fixed = datetime(2026, 10, 23, 4, tzinfo=timezone.utc)
            return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)
    monkeypatch.setattr(database, 'datetime', FrozenDateTime)
    value = DesktopService(tmp_path / 'data' / 'workbox.db')
    value.db.create_capture('甲系统\n乙系统', '本批', [('甲系统', False), ('乙系统', False)])
    return value


def make_dialog(service):
    try:
        module = importlib.import_module('delivery_ui')
    except ImportError:
        pytest.fail('缺少今日交付窗口模块')
    return module.TodayDeliveryDialog(service)


def event(service, pid=1, *, kind='manual-files', at='2026-10-23T03:04:05.000Z', path=''):
    with service.db.connect() as connection:
        return connection.execute('''INSERT INTO delivery_events(
            project_id,project_name,batch_name,kind,zip_id,archive_path,occurred_at)
            VALUES (?,?,?, ?,NULL,?,?)''',
            (pid, '甲系统' if pid == 1 else '乙系统', '本批', kind, str(path), at)).lastrowid


def selected_pid(dialog):
    row = dialog.table.currentRow()
    return dialog.table.item(row, 0).data(Qt.UserRole) if row >= 0 else None


def test_empty_dialog_is_read_only_and_explains_beijing_day(qapp, service):
    dialog = make_dialog(service)
    try:
        assert dialog.table.rowCount() == dialog.details.rowCount() == 0
        assert '首次交付 0 个项目' in dialog.summary.text() and '补交 0 条' in dialog.summary.text()
        assert '暂无交付记录' in dialog.summary.text() and '北京时间' in dialog.scope_note.text()
        assert dialog.table.editTriggers() == dialog.details.editTriggers() == QAbstractItemView.NoEditTriggers
        assert not dialog.open_folder_button.isEnabled()
        assert dialog.table.accessibleName() and dialog.details.accessibleName()
        assert not dialog.isModal()
    finally:
        dialog.close()


def test_summary_lists_unique_projects_and_selected_project_all_events(qapp, service):
    event(service, at='2026-10-23T03:04:05.000Z')
    event(service, at='2026-10-23T04:05:06.000Z', kind='manual-zip')
    event(service, 2, at='2026-10-23T02:00:00.000Z', kind='auto-zip')
    dialog = make_dialog(service)
    try:
        assert '首次交付 2 个项目' in dialog.summary.text() and '补交 1 条' in dialog.summary.text()
        assert dialog.table.rowCount() == 2
        assert [dialog.table.item(0, column).text() for column in range(4)] == ['甲系统', '本批', '2026-10-23 11:04:05', '计数 1 项']
        dialog.table.selectRow(0)
        assert dialog.details.rowCount() == 2
        assert dialog.details.item(0, 0).text() == '2026-10-23 12:05:06'
        assert dialog.details.item(1, 0).text() == '2026-10-23 11:04:05'
        assert dialog.details.item(0, 2).text() == '手动 ZIP 交付'
        assert dialog.details.item(1, 2).text() == '手动材料交付'
        assert dialog.details.item(0, 1).text() == '补交不计'
    finally:
        dialog.close()


def test_kinds_include_automatic_and_explicit_legacy_fact(qapp, service):
    event(service, kind='auto-zip', at='2026-10-23T04:00:00.000Z')
    event(service, kind='legacy', at='2026-10-23T03:00:00.000Z')
    dialog = make_dialog(service)
    try:
        dialog.table.selectRow(0)
        assert dialog.details.item(0, 2).text() == 'ZIP 自动交付'
        assert '旧交付事实' in dialog.details.item(1, 2).text()
        assert '升级前' in dialog.details.item(1, 2).toolTip()
    finally:
        dialog.close()


def test_open_archive_action_emits_folder_for_selected_event_without_opening_file(qapp, service, tmp_path):
    folder = tmp_path / '项目材料'
    folder.mkdir()
    zipped = tmp_path / 'ZIP归档' / '成品.zip'
    zipped.parent.mkdir()
    zipped.write_bytes(b'isolated archive receipt')
    event(service, path=folder, at='2026-10-23T04:00:00.000Z')
    event(service, path=zipped, kind='auto-zip', at='2026-10-23T03:00:00.000Z')
    dialog = make_dialog(service)
    received = []
    dialog.folder_requested.connect(received.append)
    try:
        dialog.table.selectRow(0)
        dialog.details.selectRow(0)
        dialog.open_folder_button.click()
        dialog.details.selectRow(1)
        dialog.open_folder_button.click()
        assert received == [str(folder), str(zipped.parent)]
    finally:
        dialog.close()


def test_unchanged_timer_refresh_keeps_existing_items_and_selection(qapp, service):
    event(service)
    dialog = make_dialog(service)
    try:
        dialog.table.selectRow(0)
        top = dialog.table.item(0, 0)
        detail = dialog.details.item(0, 0)
        dialog.timer.timeout.emit()
        assert dialog.table.item(0, 0) is top
        assert dialog.details.item(0, 0) is detail
        assert selected_pid(dialog) == 1
    finally:
        dialog.close()


def test_new_event_refresh_preserves_selected_project_and_detail_selection(qapp, service):
    first_id = event(service)
    dialog = make_dialog(service)
    try:
        dialog.table.selectRow(0)
        dialog.details.selectRow(0)
        event(service, at='2026-10-23T05:00:00.000Z')
        dialog.timer.timeout.emit()
        assert '首次交付 1 个项目' in dialog.summary.text() and '补交 1 条' in dialog.summary.text()
        assert selected_pid(dialog) == 1
        assert dialog.details.rowCount() == 2
        assert dialog.details.item(dialog.details.currentRow(), 0).data(Qt.UserRole) == first_id
    finally:
        dialog.close()


def test_change_in_other_project_does_not_rebuild_selected_project_details(qapp, service):
    event(service)
    dialog = make_dialog(service)
    try:
        dialog.table.selectRow(0)
        detail = dialog.details.item(0, 0)
        event(service, 2, at='2026-10-23T05:00:00.000Z')
        dialog.refresh()
        assert selected_pid(dialog) == 1
        assert dialog.details.item(0, 0) is detail
    finally:
        dialog.close()


def test_changed_project_table_keeps_selection_and_scroll_position(qapp, service):
    names = [f'滚动项目{index}' for index in range(35)]
    ids = service.db.create_capture('\n'.join(names), '滚动批', [(name, False) for name in names])['project_ids']
    for pid in ids:
        event(service, pid)
    dialog = make_dialog(service)
    try:
        dialog.resize(700, 460)
        dialog.show()
        qapp.processEvents()
        dialog.table.selectRow(20)
        dialog.table.scrollToItem(dialog.table.item(20, 0), QAbstractItemView.PositionAtTop)
        pid = selected_pid(dialog)
        position = dialog.table.verticalScrollBar().value()
        assert position > 0
        event(service, ids[0], at='2026-10-23T05:00:00.000Z')
        dialog.refresh()
        assert selected_pid(dialog) == pid
        assert dialog.table.verticalScrollBar().value() == position
    finally:
        dialog.close()


def test_main_read_error_clears_stale_actions_and_retry_recovers(qapp, service, monkeypatch):
    event(service)
    dialog = make_dialog(service)
    original = service.db.today_delivery_summary
    try:
        dialog.table.selectRow(0)
        def fail(*args, **kwargs):
            raise OSError('隔离统计暂时不可读')
        monkeypatch.setattr(service.db, 'today_delivery_summary', fail)
        dialog.refresh_button.click()
        assert '读取交付记录失败' in dialog.summary.text()
        assert '暂时不可读' in dialog.summary.text()
        assert dialog.table.rowCount() == dialog.details.rowCount() == 0
        assert not dialog.open_folder_button.isEnabled() and dialog.refresh_button.isEnabled()
        monkeypatch.setattr(service.db, 'today_delivery_summary', original)
        dialog.refresh_button.click()
        assert dialog.table.rowCount() == 1
    finally:
        dialog.close()


def test_history_read_error_can_retry_without_stale_actions(qapp, service, monkeypatch):
    event(service)
    dialog = make_dialog(service)
    original = service.db.delivery_history
    try:
        def fail(*args, **kwargs):
            raise OSError('隔离明细暂时不可读')
        dialog.table.selectRow(0)
        monkeypatch.setattr(service.db, 'delivery_history', fail)
        dialog.refresh()
        assert '暂时不可读' in dialog.summary.text()
        assert dialog.details.rowCount() == 0 and not dialog.open_folder_button.isEnabled()
        monkeypatch.setattr(service.db, 'delivery_history', original)
        dialog.refresh_button.click()
        dialog.table.selectRow(0)
        assert dialog.details.rowCount() == 1
        assert '失败' not in dialog.details_hint.text()
    finally:
        dialog.close()


def test_modeless_dialog_timer_stops_on_close_and_restarts_on_reopen(qapp, service):
    dialog = make_dialog(service)
    assert not dialog.timer.isActive()
    try:
        dialog.show()
        qapp.processEvents()
        assert dialog.isVisible() and not dialog.isModal()
        assert dialog.timer.isActive() and dialog.timer.interval() == 2000
        dialog.close()
        assert not dialog.timer.isActive()
        dialog.show()
        qapp.processEvents()
        assert dialog.timer.isActive()
    finally:
        dialog.close()
