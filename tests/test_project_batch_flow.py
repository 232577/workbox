"""项目收件与批次提示不再依赖任务组页面；只使用匿名夹具。"""
import time

import pytest
from PySide6.QtWidgets import QApplication

from desktop_controller import FloatingController
from desktop_service import DesktopService


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
    from update_client import CURRENT_TAG
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
