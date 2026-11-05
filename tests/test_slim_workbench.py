"""精简工作台入口仍调用原有页面、上下文动作与保护。"""
import pytest
import threading
from types import SimpleNamespace
from zipfile import ZipFile
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QMenu, QTreeWidget

from compact_ui import CompactPanel
from desktop_service import DesktopService


@pytest.fixture
def panel(tmp_path):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    value = CompactPanel(DesktopService(tmp_path / 'data' / 'workbox.db'))
    value.show()
    app.processEvents()
    yield value
    value.document_pane.shutdown()
    value.hide()


def action(panel, caption):
    return next(item for item in panel.advanced_menu.actions() if item.text() == caption)


def test_daily_navigation_and_advanced_tools_remain_reachable(panel):
    assert panel.settings_button.accessibleName() == '高级设置'
    assert isinstance(panel.advanced_menu, QMenu)
    assert panel.settings_button.menu() is panel.advanced_menu
    assert [control.accessibleName() for control, _ in panel.nav_controls
            if control.isVisible()] == ['项目', '今日工作量', '高级设置']
    for control in (panel.nav_zip, panel.appearance_button, panel.help_button,
                    panel.update_button, panel.material_check_button, panel.intake_log_button):
        assert control.isHidden()
    captions = {item.text() for item in panel.advanced_menu.actions() if not item.isSeparator()}
    assert captions == {'工作设置…', '归档分类', 'ZIP 归档库', '待核对', '收录记录',
                        '监视详情', '外观', '使用指南', '检查更新'}
    assert action(panel, '外观').menu() is panel.appearance_button.menu()
    assert set(panel.theme_actions.values()) == set(action(panel, '外观').menu().actions())
    action(panel, 'ZIP 归档库').trigger()
    assert panel.tabs.currentIndex() == 1 and panel.page_title.text() == 'ZIP 归档'
    action(panel, '待核对').trigger()
    assert panel.tabs.currentIndex() == 2 and panel.nav_review.isVisible()
    panel.nav_projects.click()
    assert panel.tabs.currentIndex() == 0 and panel.nav_review.isHidden()


def test_pending_entry_tracks_count_current_page_and_exact_feedback(panel):
    service = panel.service
    assert panel.nav_review.isHidden()
    service.receive_text('这是一条需要人工核对的匿名消息')
    panel.refresh()
    receipts = service.pending_receipts()
    assert receipts
    receipt_id = receipts[0]['id']
    assert panel.nav_review.isVisible()
    assert str(len(receipts)) in panel.nav_review.text()
    assert panel.focus_receipt(receipt_id)
    assert panel.receipt_list.currentItem().data(Qt.UserRole) == receipt_id
    for receipt in receipts:
        service.dismiss_receipt(receipt['id'])
    panel.refresh()
    assert panel.receipt_list.count() == 0 and panel.nav_review.isVisible()
    panel.nav_projects.click()
    assert panel.nav_review.isHidden()
    action(panel, '待核对').trigger()
    assert panel.nav_review.isVisible()


def test_narrow_pending_entry_displays_live_count_across_resize(panel):
    panel.resize(660, 480)
    QApplication.processEvents()
    assert panel.nav_review.isHidden()
    panel.service.receive_text('这是一条等待人工核对的匿名收件消息')
    panel.refresh()
    QApplication.processEvents()
    count = len(panel.service.pending_receipts())
    badge = panel.nav_review.findChild(QLabel, 'navigationCountBadge')
    assert count > 0 and badge is not None and badge.isVisible()
    assert badge.text() == str(count) and panel.nav_review.text() == ''
    assert panel.nav_review.rect().contains(badge.geometry())
    panel.resize(1200, 780)
    QApplication.processEvents()
    assert badge.isHidden() and panel.nav_review.text() == f'待核对 {count}'
    panel.resize(660, 480)
    QApplication.processEvents()
    assert badge.isVisible() and badge.text() == str(count)
    action(panel, '待核对').trigger()
    for receipt in panel.service.pending_receipts():
        panel.service.dismiss_receipt(receipt['id'])
    panel.refresh()
    assert panel.nav_review.isVisible() and badge.isHidden()
    panel.nav_projects.click()
    assert panel.nav_review.isHidden()


def zip_attention_fixture(panel, tmp_path, kind):
    service = panel.service
    service.workspaces.configure(str(tmp_path / 'work'), str(tmp_path / 'archives'), 10)
    if kind == 'bundle':
        for batch in ('匿名批次一', '匿名批次二'):
            service.db.create_capture('1. 甲检测平台\n2. 乙调度平台', batch,
                                      [('甲检测平台', False), ('乙调度平台', False)])
        source = tmp_path / '甲检测平台-2-G.zip'
        with ZipFile(source, 'w') as archive:
            archive.writestr('甲检测平台/说明.txt', '甲')
            archive.writestr('乙调度平台/说明.txt', '乙')
        package = service.import_zip(source)['zip']
        assert service.db.get_zip_bundle(package['id'])['state'] == 'needs-review'
        return lambda: service.snapshots.recognize_bundle(package['id'], batch_id=1)
    service.db.create_capture('1. 匿名归档系统', '匿名批次', [('匿名归档系统', False)])
    source = tmp_path / '匿名文件.zip'
    with ZipFile(source, 'w') as archive:
        archive.writestr('说明.txt', '匿名内容')
    valid = source.read_bytes()
    service.snapshots.watch_zip(source, 1)
    service.snapshots.scan(0)
    assert service.snapshots.scan(10)['archived']
    source.write_bytes(b'PK\x03\x04' + b'broken central directory' * 5)
    service.snapshots.scan(20)
    assert service.snapshots.scan(30)['errors']
    assert service.snapshots.list_sources()[0]['state'] == 'error'
    def recover():
        source.write_bytes(valid)
        service.snapshots.scan(40)
    return recover


@pytest.mark.parametrize('kind,caption', [('bundle', '待核对包 1'), ('source', '归档异常 1')])
def test_exception_zip_entry_uses_full_snapshot_counts_and_stays_on_current_page(panel, tmp_path, kind, caption):
    assert panel.nav_zip.isHidden()
    recover = zip_attention_fixture(panel, tmp_path, kind)
    assert panel.service.pending_receipts() == []
    panel.search.setText('完全不匹配的匿名搜索')
    panel.refresh()
    assert panel.zip_list.count() == 0
    assert panel.nav_review.isHidden() and panel.nav_zip.isVisible()
    assert caption in panel.nav_zip.text() and caption in panel.nav_zip.toolTip()
    assert any(control is panel.nav_zip for control, _ in panel.nav_controls)
    panel.resize(660, 480)
    QApplication.processEvents()
    badge = panel.nav_zip.findChild(QLabel, 'navigationCountBadge')
    assert badge.isVisible() and badge.text() == '1' and panel.nav_zip.text() == ''
    assert panel.nav_zip.rect().contains(badge.geometry())
    panel.nav_zip.click()
    assert panel.tabs.currentIndex() == 1 and panel.page_title.text() == 'ZIP 归档'
    recover()
    panel.refresh()
    assert panel.nav_zip.isVisible() and badge.isHidden()
    panel.nav_projects.click()
    assert panel.nav_zip.isHidden()
    panel.resize(1200, 780)
    QApplication.processEvents()
    assert panel.nav_zip.isHidden()


@pytest.mark.parametrize('reader', ['list_versions', 'list_sources'])
def test_unreadable_zip_attention_preserves_count_and_reports_error(panel, tmp_path, monkeypatch, reader):
    zip_attention_fixture(panel, tmp_path, 'source')
    panel.resize(660, 480)
    panel.refresh()
    badge = panel.nav_zip.findChild(QLabel, 'navigationCountBadge')
    assert badge.isVisible() and badge.text() == '1'
    def unavailable():
        raise OSError('匿名归档状态暂不可读')
    monkeypatch.setattr(panel.service.snapshots, reader, unavailable)
    panel.refresh()
    QApplication.processEvents()
    assert panel.nav_zip.isVisible() and badge.text() == '1'
    assert '不可读' in panel.nav_zip.toolTip()
    assert panel.notice.isVisible() and '匿名归档状态暂不可读' in panel.notice.text()


def test_idle_archive_status_uses_lightweight_count_and_full_refresh_reuses_versions(panel, tmp_path, monkeypatch):
    zip_attention_fixture(panel, tmp_path, 'bundle')
    panel.refresh()
    versions = panel.service.snapshots.list_versions()
    assert panel._zip_review_count == 1
    monkeypatch.setattr(panel.service.snapshots, 'list_versions',
                        lambda: pytest.fail('后台状态刷新不得加载全量版本详情'))
    monkeypatch.setattr(panel.service.db, 'get_zip_file_counts',
                        lambda *a, **k: pytest.fail('后台状态刷新不得逐 ZIP 查询文件数'))
    panel.refresh_archive_status()
    assert panel._zip_review_count == 1 and '待核对包 1' in panel.nav_zip.toolTip()
    monkeypatch.setattr(panel.service.snapshots, 'review_count',
                        lambda: pytest.fail('已有版本数据时不得重复查询计数'), raising=False)
    panel.refresh_archive_status(versions=versions)
    assert panel._zip_review_count == 1


def test_idle_review_count_error_keeps_last_attention_visible(panel, tmp_path, monkeypatch):
    zip_attention_fixture(panel, tmp_path, 'bundle')
    panel.resize(660, 480)
    panel.refresh()
    def unavailable():
        raise OSError('匿名轻量统计读取失败')
    monkeypatch.setattr(panel.service.snapshots, 'review_count', unavailable, raising=False)
    panel.refresh_archive_status()
    QApplication.processEvents()
    badge = panel.nav_zip.findChild(QLabel, 'navigationCountBadge')
    assert panel._zip_review_count == 1 and badge.isVisible() and badge.text() == '1'
    assert '不可读' in panel.nav_zip.toolTip()
    assert panel.notice.isVisible() and '匿名轻量统计读取失败' in panel.notice.text()


def test_advanced_callbacks_reuse_existing_actions(panel, monkeypatch):
    calls = []
    monkeypatch.setattr(panel, 'edit_directories', lambda: calls.append('settings'))
    classification = QSignalSpy(panel.classification_requested)
    update = QSignalSpy(panel.update_requested)
    action(panel, '工作设置…').trigger()
    action(panel, '归档分类').trigger()
    action(panel, '检查更新').trigger()
    assert calls == ['settings']
    assert classification.count() == update.count() == 1


@pytest.mark.parametrize('existing_history', [False, True])
def test_workbench_has_no_retired_task_entries(panel, existing_history):
    if existing_history:
        panel.service.task_groups.receive('1. 匿名历史项目')
    panel.refresh()
    panel.nav_projects.click()
    assert panel.tabs.currentIndex() == 0 and panel.task_groups_page is None
    assert not hasattr(panel, 'nav_task_groups')
    assert all(type(panel.tabs.widget(i)).__name__ != 'TaskGroupsPage'
               for i in range(panel.tabs.count()))
    captions = [item.text() for item in panel.advanced_menu.actions()]
    assert not any(text in captions for text in ('历史消息补录', '手工交接', '本组导出'))


def test_refresh_and_navigation_preserve_existing_task_history(panel):
    service = panel.service
    service.task_groups.receive('1. 匿名保留系统')
    tables = ('task_groups', 'task_items', 'task_group_messages', 'task_actions')
    def history_rows():
        with service.db.connect() as connection:
            return {table: [tuple(row) for row in connection.execute(f'SELECT * FROM {table} ORDER BY id')]
                    for table in tables}
    before = history_rows()
    panel.refresh()
    action(panel, 'ZIP 归档库').trigger()
    action(panel, '待核对').trigger()
    panel.nav_projects.click()
    panel.refresh()
    assert history_rows() == before
    assert panel.project_list.count() == 1


def test_busy_and_resize_do_not_restore_hidden_tools(panel):
    for busy in (True, False):
        panel.set_busy(busy)
        assert panel.settings_button.isEnabled()
        assert action(panel, '工作设置…').isEnabled() is not busy
        assert panel.cancel_button.isVisible() is busy
        for width, height in ((660, 480), (1200, 780), (660, 492)):
            panel.resize(width, height)
            panel.refresh()
            QApplication.processEvents()
            for control in (panel.nav_zip, panel.appearance_button, panel.help_button,
                            panel.update_button, panel.material_check_button, panel.intake_log_button):
                assert control.isHidden()
            assert panel.background_button.isVisible() and panel.exit_button.isVisible()
            assert panel.settings_button.accessibleName() == '高级设置'


def test_busy_keeps_native_advanced_menu_and_read_only_tools_openable(panel):
    panel.set_busy(True)
    observed = []
    def close_menu():
        observed.append(panel.advanced_menu.isVisible())
        panel.advanced_menu.close()
    QTimer.singleShot(0, close_menu)
    panel.settings_button.click()
    QTest.qWait(30)
    assert observed == [True]
    for caption in ('工作设置…', '归档分类'):
        assert not action(panel, caption).isEnabled()
    for caption in ('ZIP 归档库', '待核对', '收录记录', '监视详情', '使用指南', '检查更新', '外观'):
        assert action(panel, caption).isEnabled()


def test_all_delivery_export_completion_restores_scan_without_reopening_menu(panel, tmp_path, monkeypatch):
    import export_ui
    import delivery_export
    panel.show_today_deliveries()
    page = panel.today_dialog
    started, release = threading.Event(), threading.Event()
    with panel.service.db.connect() as connection:
        before = list(connection.iterdump())
    def operation(db, target, **_):
        started.set()
        if not release.wait(5):
            raise RuntimeError('匿名任务未及时释放')
        return {'project_count': 0, 'path': str(target)}
    monkeypatch.setattr(export_ui, 'choose_workbook_destination', lambda _: (tmp_path / '交付.xlsx', None))
    monkeypatch.setattr(delivery_export, 'export_delivered_projects', operation)
    assert page.export_button.isEnabled()
    assert page.export_button.accessibleName() == '导出全部有效交付项目'
    page.export_button.click()
    try:
        for _ in range(100):
            if started.is_set():
                break
            QTest.qWait(10)
        assert started.is_set() and page.worker is not None
        panel.advanced_menu.aboutToShow.emit()
        assert not panel.scan_button.isEnabled()
        action(panel, 'ZIP 归档库').trigger()
        release.set()
        for _ in range(200):
            QTest.qWait(10)
            if page.worker is None and panel.scan_button.isEnabled():
                break
        assert page.worker is None and not panel.business_page_busy()
        assert panel.scan_button.isEnabled()
        assert panel.tabs.currentIndex() == 1
        assert '已导出 0 个项目' in page.status_note.text()
        with panel.service.db.connect() as connection:
            assert list(connection.iterdump()) == before
    finally:
        release.set()
        for _ in range(200):
            if page.worker is None:
                break
            QTest.qWait(10)


@pytest.mark.parametrize('page_name', ['archive_classification_page', 'today_dialog'])
def test_advanced_mutations_recheck_independent_page_worker(panel, monkeypatch, page_name):
    calls = []
    monkeypatch.setattr(panel, 'edit_directories', lambda: calls.append('settings'))
    classification = QSignalSpy(panel.classification_requested)
    scans = QSignalSpy(panel.scan_requested)
    # 菜单已启用后，业务页可能自行启动 worker；触发时也必须重新检查。
    page = SimpleNamespace(worker=None)
    monkeypatch.setattr(panel, page_name, page, raising=False)
    previous = page.worker
    original_page = panel.tabs.currentWidget()
    page.worker = object()
    try:
        assert not panel.busy
        panel.scan_button.click()
        for caption in ('工作设置…', '归档分类'):
            action(panel, caption).trigger()
        assert calls == [] and classification.count() == 0 and scans.count() == 0
        assert panel.tabs.currentWidget() is original_page
        panel.advanced_menu.aboutToShow.emit()
        assert not action(panel, '工作设置…').isEnabled()
        assert not action(panel, '归档分类').isEnabled()
        assert not panel.scan_button.isEnabled()
        assert action(panel, '使用指南').isEnabled()
        assert action(panel, '外观').menu().isEnabled()
    finally:
        page.worker = previous
    panel.advanced_menu.aboutToShow.emit()
    assert action(panel, '工作设置…').isEnabled() and panel.scan_button.isEnabled()
