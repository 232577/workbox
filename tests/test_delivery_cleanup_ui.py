"""交付页精简后，筛选、分类工作量与纠正从实际入口操作。"""
import pytest
from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QLineEdit, QTextBrowser

from delivery_ui import TodayDeliveryDialog
from desktop_service import DesktopService


@pytest.fixture(params=[(660, 480), (980, 680)], ids=['compact', 'wide'])
def page(tmp_path, request):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    service = DesktopService(tmp_path / 'data' / 'isolated.db')
    service.receive_text('1. 匿名项目')
    kind = service.create_project_type('专项材料')
    service.set_project_types([1], kind['id'])
    with service.db.connect() as connection:
        connection.execute("UPDATE projects SET status='已交付' WHERE id=1")
        connection.execute("""INSERT INTO delivery_events
            (project_id,project_name,batch_name,kind,archive_path,occurred_at)
            VALUES (1,'匿名项目','匿名批次','manual-files','','2026-10-30T03:00:00Z')""")
    value = TodayDeliveryDialog(service, embedded=True)
    value.resize(*request.param)
    value.show()
    QTest.qWait(80)
    yield value
    value.shutdown()
    value.deleteLater()
    app.processEvents()


def menu_action(button, title):
    assert button.isVisible() and button.isEnabled()
    return next(action for action in button.menu().actions() if action.text() == title)


def wait_for_write(page):
    for _ in range(200):
        if page.worker is None:
            return
        QTest.qWait(20)
    pytest.fail('交付纠正未结束')


def answer_correction(action, answer, *, draft=None, contains=()):
    observed = []

    def enter_reason():
        dialog = QApplication.activeModalWidget()
        if dialog is None:
            return
        editor = dialog.findChild(QLineEdit, 'deliveryCorrectionReason')
        buttons = dialog.findChild(QDialogButtonBox)
        if editor is None or buttons is None:
            dialog.reject()
            return
        observed.append((editor.text(), dialog.findChild(QTextBrowser).toPlainText(),
                         buttons.button(QDialogButtonBox.Cancel).isDefault()))
        if draft is not None:
            editor.setText(draft)
        buttons.button(answer).click()

    assert action.isEnabled()
    QTimer.singleShot(0, enter_reason)
    action.trigger()
    assert len(observed) == 1, '可见的纠正操作必须提供备注与确认入口'
    previous, impact, cancel_is_default = observed[0]
    assert cancel_is_default
    assert all(part in impact for part in contains)
    return previous


def test_wide_and_compact_share_filters_and_show_type_counts(page):
    assert page.date_edit.isVisible() and page.search.isVisible() and page.export_button.isVisible()
    filter_button = page.compact_filter_button
    assert menu_action(filter_button, '今天').isEnabled()
    assert menu_action(filter_button, '刷新').isEnabled()
    all_dates = menu_action(filter_button, '全部日期')
    all_dates.trigger()
    assert all_dates.isChecked() and page.table.rowCount() == 1
    assert '1' in filter_button.text()
    assert page.type_total.isVisible() and '合计 1 个项目' in page.type_total.text()
    assert page.type_counts.isVisible() and '专项材料：1' in page.type_counts.toPlainText()
    assert not page.refresh_button.isVisible()


def test_read_failure_exposes_retry_without_hiding_export(page, monkeypatch):
    read_history = page.service.db.delivery_history

    def unavailable(*args, **kwargs):
        raise OSError('隔离读取失败')

    monkeypatch.setattr(page.service.db, 'delivery_history', unavailable)
    menu_action(page.compact_filter_button, '刷新').trigger()
    assert page.summary.isVisible() and '隔离读取失败' in page.summary.text()
    assert page.refresh_button.isVisible() and page.export_button.isEnabled()
    monkeypatch.setattr(page.service.db, 'delivery_history', read_history)
    page.refresh_button.click()
    assert not page.refresh_button.isVisible()


def test_details_only_use_record_space_when_needed(page):
    page.all_dates.setChecked(True)
    assert page.table.isVisible() and not page.details.isVisible()
    page.table.selectRow(0)
    page.compact_tabs.setCurrentIndex(1)
    assert page.details.isVisible() and page.details.rowCount() == 1
    page.table.clearSelection()
    assert not page.details.isVisible()
    if not page._compact_embedded:
        assert page.table.isVisible()


def test_corrections_keep_draft_and_separate_project_workload_from_event_validity(page, monkeypatch):
    menu_action(page.compact_filter_button, '全部日期').trigger()
    page.table.selectRow(0)
    exclude = menu_action(page.project_actions_button, '不计工作量')
    before = page.service.db.delivery_history()
    assert answer_correction(exclude, QDialogButtonBox.Cancel, draft='代交说明') == ''
    assert page.worker is None and page.service.db.delivery_history() == before

    update = page.service.set_project_delivery_counting

    def fail(*args, **kwargs):
        raise OSError('隔离写入失败')

    monkeypatch.setattr(page.service, 'set_project_delivery_counting', fail)
    assert answer_correction(exclude, QDialogButtonBox.Ok) == '代交说明'
    wait_for_write(page)
    assert '可重试' in page.status_note.text() and exclude.isEnabled()
    assert page.service.db.delivery_history() == before

    monkeypatch.setattr(page.service, 'set_project_delivery_counting', update)
    assert answer_correction(exclude, QDialogButtonBox.Ok) == '代交说明'
    wait_for_write(page)
    history = page.service.db.delivery_history()
    assert history['project_count'] == 0 and history['voided_count'] == 0
    assert history['events'][0]['count_exclusion_reason'] == '代交说明'
    assert page.service.db.get_project(1)['status'] == '已交付'
    include = menu_action(page.project_actions_button, '恢复计入')
    assert answer_correction(include, QDialogButtonBox.Ok) == ''
    wait_for_write(page)
    assert page.service.db.delivery_history()['project_count'] == 1
    assert answer_correction(exclude, QDialogButtonBox.Cancel) == ''

    page.compact_tabs.setCurrentIndex(1)
    void = menu_action(page.record_actions_button, '撤销误记')
    assert answer_correction(void, QDialogButtonBox.Ok, draft='误记说明') == ''
    wait_for_write(page)
    assert page.service.db.get_project(1)['status'] == '待交付'
    history = page.service.db.delivery_history()
    assert history['voided_count'] == 1 and history['events'][0]['management_reason'] == '误记说明'
    menu_action(page.compact_filter_button, '显示已撤销').trigger()
    page.table.selectRow(0)
    restore = menu_action(page.record_actions_button, '恢复记录')
    assert answer_correction(restore, QDialogButtonBox.Ok) == ''
    wait_for_write(page)
    assert page.service.db.get_project(1)['status'] == '待交付'
    assert page.service.db.delivery_history()['voided_count'] == 0
