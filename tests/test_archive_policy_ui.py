"""隔离桌面会话中的收录规则说明、真实状态反馈和历史包标识。"""

import time
from zipfile import ZipFile

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QDialog

from compact_ui import CompactPanel, ProjectDeliveryDialog
from desktop_controller import FloatingController
from desktop_service import DesktopService


@pytest.fixture(scope='module')
def qapp():
    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    return application


@pytest.fixture
def controller(qapp, tmp_path):
    value = FloatingController(DesktopService(tmp_path / 'data' / 'workbox.db'), tray_enabled=False)
    assert value.start()
    value.monitor_timer.stop()
    value.service.receive_text('1. 甲系统\n2. 乙系统')
    value.refresh()
    try:
        yield value
    finally:
        value.shutdown()
        qapp.processEvents()


def wait(controller):
    deadline = time.monotonic() + 10
    while controller.busy and time.monotonic() < deadline:
        QApplication.processEvents()
        QTest.qWait(10)
    assert not controller.busy
    QApplication.processEvents()


def archive(controller, path, counts):
    with ZipFile(path, 'w') as stream:
        for project, count in counts.items():
            for index in range(count):
                stream.writestr(f'{project}/资料{index}.txt', f'{project}-{index}')
    return controller.service.import_zip(path)


def select_project(controller, pid):
    controller.show_panel()
    for row in range(controller.panel.project_list.count()):
        if controller.panel.project_list.item(row).data(Qt.UserRole) == pid:
            controller.panel.project_list.setCurrentRow(row)
            return
    pytest.fail('隔离项目未显示')


def project_action(panel, project_id, text):
    menu = panel._test_project_menu = panel.project_context_menu(project_id)
    for item in menu.actions():
        if item.text() == text:
            return item
    pytest.fail(f'项目右键菜单缺少：{text}')


@pytest.mark.parametrize('duplicate', [False, True])
def test_ordinary_file_success_feedback_states_pending_only_after_commit(controller, tmp_path, duplicate):
    source = tmp_path / '甲系统.txt'
    source.write_text('隔离工作正文', encoding='utf-8')
    if duplicate:
        controller.service.receive_file(source)
    controller.receive({'kind': 'files', 'paths': [str(source)]})
    wait(controller)
    assert controller.service.get_project(1)['status'] == '待交付'
    assert '已设为待交付' in controller.bubble.text.text()


def test_rule_write_failure_never_notifies_file_intake_success(controller, tmp_path):
    source = tmp_path / '甲系统.txt'
    source.write_bytes(b'isolated')
    with controller.service.db.connect() as connection:
        connection.execute("CREATE TRIGGER reject_state BEFORE UPDATE OF status ON projects WHEN NEW.status='待交付' BEGIN SELECT RAISE(ABORT,'isolated status failure'); END")
    controller.receive({'kind': 'files', 'paths': [str(source)]})
    wait(controller)
    assert controller.service.get_project(1)['status'] == '待开发'
    assert '失败' in controller.bubble.text.text()
    assert '已设为待交付' not in controller.bubble.text.text()


def test_watching_feedback_explains_threshold_and_original_filename(controller, tmp_path):
    path = tmp_path / '甲系统-2-G.zip'
    with ZipFile(path, 'w') as stream:
        stream.writestr('甲系统/说明.txt', '正文')
        stream.writestr('乙系统/说明.txt', '正文')
    controller.receive({'kind': 'files', 'paths': [str(path)]})
    wait(controller)
    text = controller.bubble.text.text()
    assert '稳定' in text and '保留原名' in text
    assert '超过 3 个文件' in text and '已交付' in text
    assert '不足 4 个文件' in text and '待交付' in text


def test_matched_bundle_feedback_uses_per_project_count_facts(controller, tmp_path):
    result = archive(controller, tmp_path / '甲系统-2-G.zip', {'甲系统': 4, '乙系统': 3})
    controller.enqueue('核对包内项目', lambda **_: result)
    wait(controller)
    text = controller.panel.notice.text()
    assert '甲系统：4 个文件，当前已交付' in text
    assert '乙系统：3 个文件，当前待交付' in text
    assert '统一更新为待交付' not in text
    assert controller.bubble.navigation_target == ('projects', (1, 2))


def test_archived_multiple_versions_report_latest_count_and_current_project_state(controller, tmp_path):
    path = tmp_path / '甲系统.zip'
    old = archive(controller, path, {'甲系统': 4})['zip']
    latest = archive(controller, path, {'甲系统': 3})['zip']
    # 故意逆序传入，旧包的成功判定不能覆盖最后保存的新包状态。
    controller.enqueue('检查 ZIP 归档', lambda **_: {'archived': [latest, old], 'pending': 0, 'errors': []})
    wait(controller)
    text = controller.panel.notice.text()
    assert '甲系统：3 个文件，当前待交付' in text
    assert '甲系统：4 个文件' not in text
    assert '当前已交付' not in text


def test_old_package_count_feedback_names_historical_decision_when_current_state_changed(controller, tmp_path):
    result = archive(controller, tmp_path / '甲系统.zip', {'甲系统': 4})
    source = tmp_path / '甲系统.txt'
    source.write_bytes(b'new work')
    controller.service.receive_file(source)
    controller.enqueue('查看已归档包', lambda **_: result)
    wait(controller)
    text = controller.panel.notice.text()
    assert '归档时已交付' in text and '当前待交付' in text


def test_needs_review_feedback_does_not_guess_counts_or_states(controller, tmp_path):
    result = archive(controller, tmp_path / '甲系统-2-G.zip', {'甲系统': 4})
    assert controller.service.db.get_zip_bundle(result['zip']['id'])['state'] == 'needs-review'
    controller.enqueue('核对包内项目', lambda **_: result)
    wait(controller)
    text = controller.panel.notice.text()
    assert '项目状态未修改' in text
    assert '当前已交付' not in text and '当前待交付' not in text


def test_zip_caption_marks_count_decisions_as_historical(controller, tmp_path):
    result = archive(controller, tmp_path / '甲系统-2-G.zip', {'甲系统': 4, '乙系统': 3})
    record = controller.service.list_zips()[0]
    version = controller.service.snapshots.list_versions()[0]
    text = CompactPanel.zip_caption(record, version, 1)
    assert '归档时' in text and '甲系统 4 个文件 · 已交付' in text
    assert '乙系统 3 个文件 · 待交付' in text
    assert '当前已交付' not in text


def test_project_zip_tooltip_preserves_historical_count_after_new_work(controller, tmp_path):
    result = archive(controller, tmp_path / '甲系统.zip', {'甲系统': 4})
    source = tmp_path / '甲系统.txt'
    source.write_bytes(b'new')
    controller.service.receive_file(source)
    controller.refresh()
    select_project(controller, 1)
    panel = controller.panel
    dialog = ProjectDeliveryDialog(controller.service.get_project(1), panel)
    tip = dialog.version_box.itemData(dialog.version_box.findData(result['zip']['id']), Qt.ToolTipRole)
    assert '归档时' in tip and '4 个文件' in tip and '已交付' in tip
    assert panel.current_project['status'] == '待交付'
    assert project_action(panel, 1, '确认交付…').isEnabled()
    assert '自动' in panel.status_buttons['已交付'].toolTip()
    assert '普通文件' in panel.status_buttons['待交付'].toolTip()


def test_manual_exception_confirmation_explains_override_and_defaults_no(controller, tmp_path):
    zip_id = archive(controller, tmp_path / '甲系统.zip', {'甲系统': 3})['zip']['id']
    controller.refresh()
    select_project(controller, 1)
    seen = []
    def decline():
        for dialog in QApplication.topLevelWidgets():
            if isinstance(dialog, ProjectDeliveryDialog) and dialog.isVisible():
                dialog.version_box.setCurrentIndex(dialog.version_box.findData(zip_id))
                seen.append((dialog.windowTitle(), dialog.explanation.text(), dialog.cancel_button.isDefault()))
                dialog.cancel_button.click()
    QTimer.singleShot(40, decline)
    project_action(controller.panel, 1, '确认交付…').trigger()
    title, text, default = seen[0]
    assert '确认交付' in title
    assert '文件数量' in text and '首次交付计入当天统计' in text and '补交只保留记录' in text
    assert default
    assert controller.service.get_project(1)['status'] == '待交付'


def test_delivered_button_reflects_current_state_and_cannot_repeat_confirmation(controller, tmp_path):
    archive(controller, tmp_path / '甲系统.zip', {'甲系统': 4})
    controller.refresh()
    select_project(controller, 1)
    assert controller.panel.current_project['status'] == '已交付'
    assert not project_action(controller.panel, 1, '确认交付…').isEnabled()


def test_recognition_dialog_explains_per_project_rule(controller, tmp_path):
    result = archive(controller, tmp_path / '甲系统-2-G.zip', {'甲系统': 4})
    controller.refresh()
    panel = controller.panel
    panel.zip_list.setCurrentRow(0)
    found = []
    def inspect_dialog():
        for dialog in QApplication.topLevelWidgets():
            if isinstance(dialog, QDialog) and dialog.windowTitle() == '核对多项目 ZIP':
                found.append('\n'.join(label.text() for label in dialog.findChildren(QLabel)))
                dialog.reject()
    QTimer.singleShot(40, inspect_dialog)
    panel.recognize_package()
    assert found and '超过 3 个文件' in found[0] and '不足 4 个文件' in found[0]
    assert '统一标记待交付' not in found[0]
