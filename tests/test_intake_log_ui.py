"""收录记录仅从隔离数据库读取事实，不访问真实文件。"""

import pytest
from PySide6.QtCore import QDateTime, Qt, QTimer
from PySide6.QtWidgets import QApplication, QAbstractItemView

import compact_ui
from desktop_service import DesktopService
from intake_log import FileIntakeLog


@pytest.fixture(scope='module')
def qapp():
    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    return application


@pytest.fixture
def service(tmp_path):
    result = DesktopService(tmp_path / 'workbox.db')
    if not hasattr(result, 'intake_log'):
        result.intake_log = FileIntakeLog(result.db)
    return result


def make_dialog(service):
    assert hasattr(compact_ui, 'FileIntakeLogDialog'), '缺少普通文件收录记录界面'
    return compact_ui.FileIntakeLogDialog(service)


def append(log, name='隔离需求说明.txt', project='甲系统', **kwargs):
    return log.append('D:/隔离收件/' + name, 'copied', project_id=1,
                      project_name=project, target_path='D:/隔离工作/' + name,
                      basis='文件名精确匹配', candidate_ids=[1, 2], manual=True,
                      sha256='a' * 64, message='已保留原件', **kwargs)


def test_empty_dialog_explains_scope_and_is_read_only(qapp, service):
    dialog = make_dialog(service)
    try:
        assert dialog.table.rowCount() == 0
        assert '还没有普通文件收录记录' in dialog.summary.text()
        assert 'ZIP' in dialog.scope_note.text() and 'ZIP 版本记录' in dialog.scope_note.text()
        assert dialog.details.isReadOnly()
        assert dialog.table.editTriggers() == QAbstractItemView.NoEditTriggers
        assert dialog.search.accessibleName()
    finally:
        dialog.close()


def test_event_selection_shows_local_time_and_copyable_factual_details(qapp, service):
    event_id = append(service.intake_log)
    with service.db.connect() as connection:
        connection.execute("UPDATE file_intake_events SET created_at='2026-10-23T01:02:03.000Z' WHERE id=?", (event_id,))
    dialog = make_dialog(service)
    try:
        assert dialog.table.rowCount() == 1
        expected = QDateTime.fromString('2026-10-23T01:02:03.000Z', Qt.ISODateWithMs).toLocalTime().toString('yyyy-MM-dd HH:mm:ss')
        assert dialog.table.item(0, 0).text() == expected
        assert dialog.table.item(0, 1).text() == '已收录'
        assert dialog.table.item(0, 2).text() == '隔离需求说明.txt'
        assert dialog.table.item(0, 3).text() == '甲系统'
        dialog.table.selectRow(0)
        text = dialog.details.toPlainText()
        for value in ('D:/隔离收件/隔离需求说明.txt', 'D:/隔离工作/隔离需求说明.txt',
                      '文件名精确匹配', '人工选择：是', '1, 2', 'a' * 64, '已保留原件'):
            assert value in text
        dialog.details.selectAll()
        assert dialog.details.textCursor().hasSelection()
    finally:
        dialog.close()


def test_search_and_refresh_replace_results_and_clear_stale_details(qapp, service):
    append(service.intake_log)
    dialog = make_dialog(service)
    try:
        dialog.table.selectRow(0)
        append(service.intake_log, '新增说明.txt', '乙系统')
        dialog.refresh_button.click()
        assert dialog.table.rowCount() == 2
        dialog.search.setText('乙系统')
        assert dialog.table.rowCount() == 1
        assert dialog.table.item(0, 3).text() == '乙系统'
        assert not dialog.details.toPlainText()
        dialog.search.setText('不存在的项目')
        assert dialog.table.rowCount() == 0
        assert '没有匹配' in dialog.summary.text()
        assert not dialog.details.toPlainText()
        dialog.search.clear()
        assert dialog.table.rowCount() == 2
    finally:
        dialog.close()


def test_legacy_record_never_invents_manual_choice_or_matching_evidence(qapp, service):
    project_id = service.receive_text('1. 隔离旧项目')['project_ids'][0]
    with service.db.connect() as connection:
        connection.execute('INSERT INTO work_files(project_id,source_path,path,filename,size,sha256,mtime_ns) VALUES (?,?,?,?,?,?,?)',
                           (project_id, 'D:/隔离旧收件.txt', 'D:/隔离旧工作.txt', '隔离旧收件.txt', 1, 'b' * 64, 0))
    before = service.intake_log.list_recent()
    dialog = make_dialog(service)
    try:
        dialog.table.selectRow(0)
        assert dialog.table.item(0, 1).text() == '旧记录'
        text = dialog.details.toPlainText()
        assert '旧记录未记录判断依据' in text
        assert '人工选择：未记录' in text
        assert '候选项目：未记录' in text
        dialog.refresh_button.click()
        assert service.intake_log.list_recent() == before
    finally:
        dialog.close()


def test_refresh_error_is_visible_and_retry_recovers(qapp, service, monkeypatch):
    append(service.intake_log)
    dialog = make_dialog(service)
    original = service.intake_log.list_recent
    try:
        def fail(*args, **kwargs):
            raise OSError('隔离数据库暂时不可读')
        monkeypatch.setattr(service.intake_log, 'list_recent', fail)
        dialog.refresh_button.click()
        assert '暂时不可读' in dialog.summary.text()
        assert dialog.table.rowCount() == 0
        assert not dialog.details.toPlainText()
        assert dialog.refresh_button.isEnabled()
        monkeypatch.setattr(service.intake_log, 'list_recent', original)
        dialog.refresh_button.click()
        assert dialog.table.rowCount() == 1
    finally:
        dialog.close()


def test_panel_entry_opens_records_without_expanding_compact_header(qapp, service):
    panel = compact_ui.CompactPanel(service)
    try:
        assert hasattr(panel, 'intake_log_button'), '筛选栏缺少收录记录入口'
        panel.resize(660, 492)
        panel.show()
        qapp.processEvents()
        observed = []
        def close_records():
            for widget in QApplication.topLevelWidgets():
                if type(widget).__name__ == 'FileIntakeLogDialog' and widget.isVisible():
                    observed.append(widget.windowTitle())
                    widget.reject()
        QTimer.singleShot(40, close_records)
        panel.intake_log_button.click()
        assert observed == ['普通文件收录记录']
        assert panel.width() <= 660
        assert panel.rect().contains(panel.intake_log_button.mapTo(panel, panel.intake_log_button.rect().bottomRight()))
        assert panel.intake_log_button.mapTo(panel, panel.intake_log_button.rect().topLeft()).y() > panel.search.y()
    finally:
        panel.hide()
