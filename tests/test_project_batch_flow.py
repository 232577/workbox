"""项目收件与批次提示不再依赖任务组页面；只使用匿名夹具。"""
import time
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from workbox.desktop.desktop_controller import FloatingController
from workbox.desktop.desktop_service import DesktopService


@pytest.fixture
def controller(tmp_path):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    service = DesktopService(tmp_path / 'data/workbox.db')
    value = FloatingController(service, tray_enabled=False)
    assert value.start()
    value.monitor_timer.stop()
    yield value
    value.shutdown()
    value.dispose_ui()
    app.processEvents()


def finish(value):
    until = time.monotonic() + 8
    while value.busy and time.monotonic() < until:
        QApplication.processEvents()
        time.sleep(.01)
    assert not value.busy
    QApplication.processEvents()


def task_history(service):
    with service.db.connect() as connection:
        return {name: [tuple(row) for row in connection.execute('SELECT * FROM ' + name)]
                for name in ('task_groups', 'task_items', 'task_group_messages', 'task_actions')}


def test_main_title_exposes_current_build_without_opening_update_dialog(controller):
    from workbox.updates.update_client import CURRENT_TAG
    assert CURRENT_TAG in controller.panel.windowTitle()
    assert '项目管理' in controller.panel.windowTitle()


def test_normal_intake_keeps_original_project_workflow_and_task_history(controller):
    before = task_history(controller.service)
    text = '1. 匿名电机控制系统\n2. 匿名资料管理工具'
    controller.receive({'kind': 'text', 'text': text})
    finish(controller)
    rows = controller.service.list_projects()
    assert len(rows) == 2 and {row['status'] for row in rows} == {'待开发'}
    assert task_history(controller.service) == before
    assert controller.panel.tabs.currentIndex() == 0
    assert all(controller.panel.tabs.tabText(i) != '任务组' for i in range(controller.panel.tabs.count()))
    assert '任务组' not in controller.bubble.text.text()
    assert controller.bubble.navigation_target == ('projects', tuple(sorted(row['id'] for row in rows)))
    controller.receive({'kind': 'text', 'text': text})
    finish(controller)
    assert len(controller.service.list_projects()) == 2
    assert task_history(controller.service) == before


def test_view_intake_targets_reused_and_new_projects(controller, tmp_path):
    source = tmp_path / '匿名电机控制系统.txt'
    source.write_text('fixture', encoding='utf-8')
    first = controller.service.receive_path(source)['file']['project_id']
    controller.receive({'kind': 'text', 'text': '1. 匿名电机控制系统\n2. 匿名新系统'})
    finish(controller)
    ids = {row['id'] for row in controller.service.list_projects()}
    assert first in ids and len(ids) == 2
    assert controller.bubble.navigation_target[0] == 'projects'
    assert set(controller.bubble.navigation_target[1]) == ids
    controller.show_feedback()
    assert controller.panel.tabs.currentIndex() == 0
    # 保持原来的单项目查看：首项选中并显示文件，其他本次项目也在当前结果中。
    assert {controller.panel.project_list.item(i).data(256)
            for i in range(controller.panel.project_list.count())} >= ids
    assert {item.data(256) for item in controller.panel.project_list.selectedItems()} == {first}
    assert controller.panel.current_project['id'] == first
    assert controller.service.get_project(first)['status'] == '待交付'
    assert source.read_text(encoding='utf-8') == 'fixture'


def test_rename_with_period_keeps_all_eight_projects_in_current_batch_view(controller):
    service, panel = controller.service, controller.panel
    names = [f'匿名资料系统{number}' for number in range(1, 9)]
    names[2] = '匿名资料.V2系统'
    receipt = service.receive_text('\n'.join(f'{i}. {name}' for i, name in enumerate(names, 1)))
    ids = receipt['project_ids']
    assert len(ids) == 8
    service.receive_text('匿名资料批次外系统')
    controller.show_panel()
    panel.resize(660, 480)
    controller.refresh()
    panel.search.setText('匿名资料')
    batch_key = f"receipt:{receipt['receipt_id']}"
    batch_index = panel.batch_filter.findData(batch_key)
    assert batch_index > 0
    panel.batch_filter.setCurrentIndex(batch_index)

    def visible_ids():
        return {panel.project_list.item(i).data(Qt.UserRole) for i in range(panel.project_list.count())}

    assert visible_ids() == set(ids)
    project_id = ids[2]
    before = service.get_project(project_id)
    old_workspace = Path(before['workspace']['path'])
    (old_workspace / '材料.txt').write_bytes(b'original work file')
    name = names[2].replace('.', '')
    plan = service.preview_project_rename(project_id, name)
    assert plan['kind'] == 'rename'
    panel.project_change_requested.emit(dict(project_id=project_id, name=name, expected_token=plan['token'],
        merge_target_id=None, confirmed_merge=False))
    finish(controller)

    after = service.get_project(project_id)
    assert after['name'] == name and after['batch_id'] == before['batch_id']
    workspace = Path(after['workspace']['path'])
    assert workspace.parent == old_workspace.parent
    assert (workspace / '材料.txt').read_bytes() == b'original work file'
    assert panel.batch_filter.currentData() == batch_key
    assert panel.search.text() == '匿名资料'
    assert visible_ids() == set(ids)
    assert panel.current_project['id'] == project_id and panel.current_project['name'] == name
    assert panel.project_list.isVisible()


def test_confirmed_merge_keeps_narrow_project_details_open_in_current_batch(controller):
    service, panel = controller.service, controller.panel
    receipt = service.receive_text('1. 匿名来源项目\n2. 匿名同批项目')
    source, sibling = receipt['project_ids']
    target_name = '匿名保留项目系统'
    target = service.receive_text(target_name)['project_ids'][0]
    controller.show_panel()
    panel.resize(660, 480)
    controller.refresh()
    batch_key = f"receipt:{receipt['receipt_id']}"
    batch_index = panel.batch_filter.findData(batch_key)
    assert batch_index > 0
    panel.batch_filter.setCurrentIndex(batch_index)
    assert panel.focus_visible_project(source)
    panel.show_project_detail()
    assert panel.project_back_button.isVisible()

    plan = service.preview_project_rename(source, target_name)
    assert plan['kind'] == 'merge' and plan['target']['id'] == target
    panel.project_change_requested.emit(dict(project_id=source, name=target_name, expected_token=plan['token'],
        merge_target_id=target, confirmed_merge=True))
    finish(controller)

    assert service.get_project(source)['merged_into_id'] == target
    assert panel.batch_filter.currentData() == batch_key
    assert {panel.project_list.item(i).data(Qt.UserRole) for i in range(panel.project_list.count())} == {target, sibling}
    assert panel.current_project['id'] == target
    assert panel.project_back_button.isVisible()


def test_retired_task_payload_does_not_create_hidden_tasks(controller):
    before = task_history(controller.service)
    controller.receive({'kind': 'text', 'text': '1. 匿名系统', 'group_id': 7, 'new_round': True})
    finish(controller)
    assert controller.service.db.counts()['projects'] == 0
    assert task_history(controller.service) == before
    assert '项目' in controller.panel.notice.text()


def test_committed_legacy_group_result_still_targets_projects(controller):
    result = {'state': 'committed', 'group_id': 8, 'project_ids': [1, 2], 'created_project_ids': [2]}
    assert controller._result_target(result) == ('projects', (1, 2))
