from pathlib import Path
from zipfile import ZipFile

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QDialog, QFrame, QLabel

import pytest

from workbox.desktop.compact_ui import CompactPanel, ProjectDeliveryDialog
from workbox.desktop.desktop_service import DesktopService
from workbox.desktop.toolbox_board import ProjectBoard
from workbox.team_delivery.contracts import Account, Snapshot, Stage, Task


@pytest.fixture
def board_panel(tmp_path):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    service = DesktopService(tmp_path / 'data' / 'workbox.db')
    service.receive_text('1. 匿名批次待开发项目\n2. 匿名开发中项目\n3. 匿名待交付项目\n4. 匿名已交付项目')
    ids = {row['name']: row['id'] for row in service.list_projects()}
    service.db.set_status(ids['匿名开发中项目'], '开发中')
    service.db.set_status(ids['匿名待交付项目'], '待交付')
    for pid in (ids['匿名待交付项目'], ids['匿名已交付项目']):
        root = Path(service.workspaces.get_workspace(pid)['path'])
        (root / '匿名材料.txt').write_text('匿名交付材料', encoding='utf-8')
    service.confirm_delivery(ids['匿名已交付项目'], confirmed=True)
    panel = CompactPanel(service, project_board_default=True)
    panel.resize(660, 480)
    panel.show()
    app.processEvents()
    yield panel, ids
    panel.document_pane.shutdown()
    panel.close()
    panel.deleteLater()
    app.processEvents()


def test_board_keeps_three_columns_in_narrow_window_and_opens_existing_details(board_panel):
    panel, ids = board_panel
    board = panel.project_board
    lanes = list(board.lanes.values())
    assert panel.width() == 660
    assert [lane.accessibleName() for lane in lanes] == ['待开发', '待交付', '已完成']
    assert all(lane.isVisible() and lane.width() >= 185 for lane in lanes)
    assert all(left.geometry().right() < right.geometry().left() for left, right in zip(lanes, lanes[1:]))
    assert set(lanes[0].rows) == {ids['匿名批次待开发项目'], ids['匿名开发中项目']}
    assert set(lanes[1].rows) == {ids['匿名待交付项目']}
    assert set(lanes[2].rows) == {ids['匿名已交付项目']}
    lanes[1].rows[ids['匿名待交付项目']].click()
    assert panel.document_pane.project_id == ids['匿名待交付项目']
    assert panel.document_side.isVisible()
    assert panel.projects_list_button.isChecked()
    panel.show_project_board()
    assert board.isVisible() and all(lane.isVisible() for lane in lanes)
    assert panel.project_board_button.isChecked()
    assert lanes[1].rows[ids['匿名待交付项目']].isChecked()


def test_board_groups_verified_original_batches_and_displays_urgency_only(board_panel):
    panel, ids = board_panel
    account = Account('fixture', '匿名材料员', 'material', 'fixture-session', 'fixture-material')
    first = Task('55', '匿名原站项目', account.id, Stage.RETURNED, '55-1',
                 original_batch_id='batch-55', original_batch_name='匿名原站批次', original_urgency='非常紧急')
    second = Task('56', '匿名同批项目', account.id, Stage.READY, '56-1', original_batch_id='batch-55')
    other = Task('57', '匿名未分批原站项目', account.id, Stage.READY, '57-1', original_batch_name='无编号标签')
    assert panel.service.sync_personal_site_snapshot(Snapshot(account, (first, second, other))) == 3
    project_ids = {row['original_task_id']: row['id'] for row in panel.service.list_projects()}
    panel.refresh()
    lane = panel.project_board.lanes['待开发']
    batches = {batch.accessibleName(): batch for batch in lane.findChildren(QFrame, 'projectBoardBatch')}
    assert set(batches) == {'匿名原站批次', '未分批'}
    assert all(batches['匿名原站批次'].isAncestorOf(lane.rows[project_ids[site]]) for site in ('55', '56'))
    assert batches['未分批'].isAncestorOf(lane.rows[project_ids['57']])
    other_id = ids['匿名开发中项目']
    assert batches['未分批'].isAncestorOf(lane.rows[other_id])
    urgency = lane.rows[project_ids['55']].findChild(QLabel, 'projectBoardUrgency')
    assert urgency.text() == '非常紧急'
    assert not lane.rows[other_id].findChild(QLabel, 'projectBoardUrgency')
    assert not any(label.text() == '预审退回' for label in lane.findChildren(QLabel))


def test_board_delivery_click_emits_once_without_a_dialog(board_panel, monkeypatch):
    panel, ids = board_panel
    pending = ids['匿名待交付项目']
    dispatch = QSignalSpy(panel.delivery_requested)
    monkeypatch.setattr(ProjectDeliveryDialog, 'exec', lambda *_: pytest.fail('Unexpected delivery dialog'))
    action = panel.project_board.lanes['待交付'].actions[pending]
    action.click()
    assert dispatch.count() == 1 and dispatch.at(0) == [pending, None]
    panel.service.confirm_delivery(pending, confirmed=True)
    action.click()
    assert dispatch.count() == 1
    panel.refresh()
    assert pending not in panel.project_board.lanes['待交付'].rows
    assert pending in panel.project_board.lanes['已完成'].rows


def test_board_uses_single_archive_and_reports_alternatives_in_the_page(board_panel, tmp_path):
    panel, ids = board_panel
    pending = ids['匿名待交付项目']
    dispatch = QSignalSpy(panel.delivery_requested)
    path = tmp_path / '匿名待交付项目.zip'

    def import_version(text):
        with ZipFile(path, 'w') as archive:
            archive.writestr('匿名材料.txt', text)
        record = panel.service.import_zip(path)['zip']
        assert record['project_id'] == pending
        return record['id']

    first_zip = import_version('匿名归档材料一')
    action = panel.project_board.lanes['待交付'].actions[pending]
    action.click()
    assert not dispatch.count()
    assert panel.notice.isVisible()
    assert '全部项目' in panel.notice.text()
    assert panel.project_board.isVisible()
    workspace = Path(panel.service.workspaces.get_workspace(pending)['path'])
    (workspace / '匿名材料.txt').unlink()
    action.click()
    assert dispatch.count() == 1 and dispatch.at(0) == [pending, first_zip]
    assert import_version('匿名归档材料二') != first_zip
    action.click()
    assert dispatch.count() == 1
    assert '全部项目' in panel.notice.text()


def test_advanced_delivery_linkage_opens_as_a_workbench_page(board_panel):
    panel, _ = board_panel
    panel.advanced_actions['delivery_linkage'].trigger()
    assert panel.tabs.currentWidget() is panel.delivery_linkage_page
    assert panel.delivery_linkage_page.isVisible() and not panel.project_board.isVisible()
    assert not any(widget.isVisible() and isinstance(widget, QDialog) for widget in QApplication.topLevelWidgets())
    panel.nav_projects.click()
    assert panel.project_board.isVisible() and not panel.delivery_linkage_page.isVisible()


def test_dragging_title_requests_project_files_without_opening_details(board_panel):
    panel, ids = board_panel
    pending = ids['匿名待交付项目']
    spy = QSignalSpy(panel.board_file_drag_requested)
    title = panel.project_board.lanes['待交付'].rows[pending]
    position = title.rect().center()
    target = position + QPoint(QApplication.startDragDistance() + 1, 0)
    QTest.mousePress(title, Qt.LeftButton, pos=position)
    move = QMouseEvent(QEvent.MouseMove, QPointF(target), QPointF(title.mapToGlobal(target)),
                       Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(title, move)
    QTest.mouseRelease(title, Qt.LeftButton, pos=target)
    assert spy.count() == 1 and spy.at(0) == [pending, title]
    assert panel.project_board.isVisible()


def test_board_keeps_selection_and_reading_position_when_lane_changes_above_it():
    app = QApplication.instance() or QApplication([])
    board = ProjectBoard()
    board.resize(900, 340)
    board.show()
    projects = [{'id': pid, 'status': '待开发', 'name': f'匿名产品部署任务管理和资料审核统计平台 {pid}'}
                for pid in range(1, 28)]
    board.update_projects(projects)
    app.processEvents()
    lane = board.lanes['待开发']
    bar = lane.scroll.verticalScrollBar()
    board.select(11)
    bar.setValue(lane.rows[11].mapTo(lane.scroll.widget(), QPoint()).y() + 4)
    assert lane.rows[11].mapTo(lane.scroll.viewport(), QPoint()).y() == -4
    board.update_projects(projects[1:])
    app.processEvents()
    assert lane.rows[11].mapTo(lane.scroll.viewport(), QPoint()).y() == -4
    assert lane.rows[11].isChecked()
    board.resize(660, 340)
    app.processEvents()
    assert lane.rows[11].mapTo(lane.scroll.viewport(), QPoint()).y() == -4
    board.close()
