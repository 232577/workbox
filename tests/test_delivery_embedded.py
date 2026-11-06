"""交付管理与主工作台共用窗口，切页保留工作位置。"""
import time
import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest, QSignalSpy
from PySide6.QtWidgets import QApplication, QDialogButtonBox

from compact_ui import CompactPanel
from desktop_service import DesktopService
from test_delivery_cleanup_ui import answer_correction


@pytest.fixture
def panel(tmp_path):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    service = DesktopService(tmp_path / 'data' / 'workbox.db')
    service.db.create_capture('管理页切换', '隔离批次', [('页面验证项目', False)])
    with service.db.connect() as connection:
        connection.execute("""INSERT INTO delivery_events
            (project_id,project_name,batch_name,kind,archive_path,occurred_at)
            VALUES (1,'页面验证项目','隔离批次','manual-files','','2026-10-30T03:00:00Z')""")
    value = CompactPanel(service)
    value.show()
    QTest.qWait(80)
    yield value
    if value.today_dialog:
        value.today_dialog.shutdown()
    value.document_pane.shutdown()
    value.hide()
    value.deleteLater()
    app.processEvents()


def test_delivery_entry_embeds_one_page_and_keeps_navigation(panel):
    panel.today_button.click()
    QTest.qWait(80)
    page = panel.today_dialog
    assert not page.isWindow()
    assert page.window() is panel
    assert panel.tabs.isAncestorOf(page)
    assert panel.page_title.text() == '交付记录管理'
    assert panel.today_button.isChecked() and not panel.nav_projects.isChecked()
    assert not panel.search.isVisible()
    assert not any(w.isVisible() and w.windowTitle() == '交付记录管理'
                   for w in QApplication.topLevelWidgets())
    count = panel.tabs.count()
    panel.today_button.click()
    assert panel.today_dialog is page and panel.tabs.count() == count
    for control, activate, index, title in ((panel.nav_zip, panel.advanced_actions['zip'].trigger, 1, 'ZIP 归档'),
                                            (panel.nav_review, panel.advanced_actions['review'].trigger, 2, '待核对'),
                                            (panel.nav_projects, panel.nav_projects.click, 0, '项目')):
        activate()
        assert panel.tabs.currentIndex() == index
        assert panel.page_title.text() == title and control.isChecked()
        assert not page.timer.isActive()
    assert panel.search.isVisible()


def test_delivery_switch_preserves_filters_selection_and_timer(panel):
    panel.search.setText('页面')
    panel.project_list.setCurrentRow(0)
    panel.today_button.click()
    page = panel.today_dialog
    page.all_dates.setChecked(True)
    page.search.setText('页面验证')
    page.table.selectRow(0)
    answer_correction(page.exclude_count_action, QDialogButtonBox.Cancel, draft='尚未提交的说明')
    QTest.qWait(80)
    first = page.table.item(0, 0)
    assert page.timer.isActive()
    page.close_button.click()
    assert panel.tabs.currentIndex() == 0 and panel.isVisible()
    assert panel.search.text() == '页面'
    assert panel.project_list.currentItem().data(Qt.UserRole) == 1
    assert not page.timer.isActive()
    panel.today_button.click()
    assert page.search.text() == '页面验证' and page.all_dates.isChecked()
    assert page.table.item(0, 0) is first
    assert page.table.currentRow() == 0
    assert answer_correction(page.exclude_count_action, QDialogButtonBox.Cancel) == '尚未提交的说明'
    QTest.keyClick(page.search, Qt.Key_Escape)
    assert page.isVisible() and panel.isVisible()
    panel.hide()
    assert not page.timer.isActive()
    panel.show()
    QTest.qWait(80)
    assert page.timer.isActive()


def test_embedded_page_fits_minimum_shell_and_actions_remain_reachable(panel):
    panel.resize(660, 480)
    panel.today_button.click()
    QTest.qWait(180)
    page = panel.today_dialog
    assert panel.width() == 660 and panel.height() == 480
    scroller = panel.delivery_page_container
    assert scroller.horizontalScrollBar().maximum() == 0
    assert scroller.verticalScrollBar().maximum() == 0
    assert page.table.viewport().height() >= 110
    assert page.table.isVisible() and not page.details.isVisible()
    for control in (page.compact_filter_button, page.close_button):
        rect = control.rect()
        assert control.width() >= control.minimumSizeHint().width()
        assert scroller.viewport().rect().contains(control.mapTo(scroller.viewport(), rect.center()))
    page.all_dates.setChecked(True)
    page.table.selectRow(0)
    assert page.project_actions_button.isVisible()
    page.compact_tabs.setCurrentIndex(1)
    QTest.qWait(100)
    assert page.details.isVisible() and not page.table.isVisible()
    assert page.details.viewport().height() >= 80
    assert page.details.rowCount() == 1
    assert scroller.verticalScrollBar().maximum() == 0
    assert page.record_actions_button.isVisible()
    actions = {a.text(): a for a in page.record_action_menu.actions()}
    assert actions['撤销误记'].isEnabled()
    assert not actions['恢复记录'].isEnabled()
    answer_correction(actions['撤销误记'], QDialogButtonBox.Cancel, draft='保留说明')
    panel.resize(1200, 780)
    QTest.qWait(150)
    # 高缩放时 Windows 可把请求的大窗口限制到物理屏幕；按实际可用尺寸验收。
    compact = panel.width()<820 or panel.height()<620
    assert page.details.isVisible()
    assert page.table.isVisible() is not compact
    assert answer_correction(actions['撤销误记'], QDialogButtonBox.Cancel) == '保留说明'
    assert page.table.currentRow() == 0
    assert page.compact_tabs.isVisible() is compact


def test_normal_window_shows_delivery_actions_without_outer_scroll(panel):
    panel.resize(1200, 780)
    panel.today_button.click()
    QTest.qWait(180)
    scroller = panel.delivery_page_container
    assert scroller.verticalScrollBar().maximum() == 0
    assert scroller.viewport().rect().contains(panel.today_dialog.close_button.mapTo(
        scroller.viewport(), panel.today_dialog.close_button.rect().center()))
    page = panel.today_dialog
    page.all_dates.setChecked(True)
    page.table.selectRow(0)
    panel.show_notice('已恢复记录，统计已更新。')
    page.status_note.setText('已恢复记录，统计已更新。')
    page.status_note.show()
    QTest.qWait(180)
    assert page.table.viewport().height() >= 80
    if page._compact_embedded:
        page.compact_tabs.setCurrentIndex(1)
        QTest.qWait(120)
    assert page.details.viewport().height() >= 40
    assert scroller.verticalScrollBar().maximum()==0


def test_compact_menus_use_existing_actions_and_keep_confirmation(panel):
    panel.resize(660,480)
    panel.today_button.click()
    page = panel.today_dialog
    page._populate_compact_filters()
    filters = {action.text():action for action in page.compact_filter_menu.actions()}
    filters['全部日期'].trigger()
    assert page.all_dates.isChecked() and page.table.rowCount()==1
    page.table.selectRow(0)
    assert page.project_actions_button.isVisible()
    actions = {action.text():action for action in page.project_action_menu.actions()}
    answer_correction(actions['不计工作量'], QDialogButtonBox.Cancel)
    assert page.worker is None
    assert page.rows[0]['included_in_workload']
    answer_correction(actions['不计工作量'], QDialogButtonBox.Ok)
    for _ in range(100):
        if page.worker is None:
            break
        QTest.qWait(20)
    assert page.worker is None and not page.rows[0]['included_in_workload']
    assert panel.delivery_page_container.verticalScrollBar().maximum()==0
    page.compact_tabs.setCurrentIndex(1)
    assert page.record_actions_button.isVisible()
    actions = {action.text():action for action in page.record_action_menu.actions()}
    answer_correction(actions['撤销误记'], QDialogButtonBox.Ok)
    for _ in range(100):
        if page.worker is None:
            break
        QTest.qWait(20)
    assert page.worker is None
    assert page.service.db.get_project(1)['status']=='待交付'
    assert page.service.db.delivery_history()['voided_count']==1


def test_window_size_does_not_enlarge_control_fonts_or_shell_spacing(panel):
    panel.today_button.click()
    page = panel.today_dialog
    controls = (panel.files_button,panel.paste_button,panel.search,panel.project_list,
                page.date_edit,page.search,page.table,page.details,page.compact_filter_button)
    for width,height in ((660,480),(1200,780),(1800,1000)):
        panel.resize(width,height)
        QTest.qWait(180)
        assert [control.font().pixelSize() for control in controls] == [13]*len(controls)
        # 改版标题在紧凑/常规布局使用 18/23px；宽屏不会继续放大。
        assert panel.page_title.font().pixelSize() == (18 if height < 560 else 23)
        margins = panel.workbench_layout.contentsMargins()
        assert margins.top()<=12 and margins.bottom()<=8
        assert panel.workbench_layout.spacing()<=8


def test_compact_menu_standard_activation_opens_menu(panel):
    panel.resize(660,480)
    panel.today_button.click()
    page = panel.today_dialog
    deadline = time.monotonic() + 2
    while not page.compact_filter_button.isVisible() and time.monotonic() < deadline:
        QTest.qWait(10)
    assert page.compact_filter_button.isVisible() and page.compact_filter_button.isEnabled()
    seen=[]
    shown=QSignalSpy(page.compact_filter_menu.aboutToShow)
    observer=QTimer(page.compact_filter_menu)
    observer.setSingleShot(True)
    observer.setInterval(0)
    def close_menu():
        seen.append(page.compact_filter_menu.isVisible())
        page.compact_filter_menu.close()
    observer.timeout.connect(close_menu)
    # Observe after the menu's actual show event, not a wall-clock race between
    # a 60ms timer and a 90ms sleep on a loaded Windows CI host. A missing or
    # invisible menu still fails; the visibility assertion is not weakened.
    page.compact_filter_menu.aboutToShow.connect(observer.start)
    try:
        page.compact_filter_button.click()
        deadline=time.monotonic()+2
        while not seen and time.monotonic()<deadline:
            QTest.qWait(10)
        assert shown.count()==1
        assert seen==[True]
    finally:
        observer.stop()
        page.compact_filter_menu.aboutToShow.disconnect(observer.start)
        page.compact_filter_menu.close()
