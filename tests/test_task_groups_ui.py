import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt


def test_group_page_preserves_selection_and_shows_manual_handoff():
    from task_groups_ui import TaskGroupsPage
    app = QApplication.instance() or QApplication([])

    class Groups:
        def snapshot(self, **kwargs):
            return [{'id': 1, 'code': 'G20261105-001', 'name': '任务组', 'tasks': [
                {'id': 2, 'code': 'G20261105-001-01', 'project_id': 3,
                 'project_name': '项目', 'material_ready': True, 'handed_over': False}]}]
        def messages(self, group_id):
            return [{'raw_text': '原消息'}]

    page = TaskGroupsPage(Groups())
    page.refresh()
    group = page.tree.topLevelItem(0)
    group.setExpanded(True)
    page.tree.setCurrentItem(group.child(0))
    page.refresh()
    assert page.tree.currentItem().data(0, 256) == ('task', 2)
    assert page.tree.topLevelItem(0).isExpanded()
    assert '原消息' in page.details.toPlainText()
    assert page.tree.currentItem().text(2) == '未交接'
    page.close()


def test_scope_selection_and_message_signal(monkeypatch):
    from task_groups_ui import TaskGroupsPage
    from PySide6.QtWidgets import QInputDialog
    app = QApplication.instance() or QApplication([])

    class Groups:
        calls = []
        def snapshot(self, **kwargs):
            self.calls.append(kwargs)
            return [{'id': 1, 'code': 'G1', 'tasks': [{'id': 2, 'code': 'G1-01',
                     'group_id': 1, 'project_id': 3, 'project_name': '项目'}]}]
        def messages(self, group_id): return []
    service = Groups()
    page = TaskGroupsPage(service)
    assert page.select_ids(1, 2)
    assert service.calls[-1]['open_only'] is False
    emitted = []
    page.task_text_requested.connect(lambda *args: emitted.append(args))
    monkeypatch.setattr(QInputDialog, 'getMultiLineText', lambda *args: ('原始消息', True))
    page._text(False)
    page.new_round('原始消息')
    assert emitted == [('原始消息', 1, False), ('原始消息', None, True)]
    page.close()


def test_narrow_layout_keeps_actions_visible():
    from task_groups_ui import TaskGroupsPage
    app = QApplication.instance() or QApplication([])
    class Groups:
        def snapshot(self, **kwargs): return []
    page = TaskGroupsPage(Groups())
    page.resize(580, 480)
    page.show()
    app.processEvents()
    for control in (page.add_button, page.new_button, page.handoff_button, page.more_button, page.copy_button):
        assert control.width() >= control.fontMetrics().horizontalAdvance(control.text()) + 12
        assert control.geometry().right() <= page.width()
    page.close()


def test_real_service_group_page_and_manual_handoff(tmp_path, monkeypatch):
    from desktop_service import DesktopService
    from task_groups_ui import TaskGroupsPage
    from PySide6.QtWidgets import QMessageBox
    app = QApplication.instance() or QApplication([])
    service = DesktopService(tmp_path / 'anonymous' / 'workbox.db')
    received = service.task_groups.receive('1. 匿名客户系统')
    with service.db.connect() as connection:
        event = connection.execute("INSERT INTO delivery_events(project_id,project_name,batch_name,kind,archive_path) VALUES (1,'匿名客户系统','匿名批次','manual-files','anonymous/archive')").lastrowid
    service.task_groups.bind_delivery(received['task_ids'], event, confirmed=True)
    page = TaskGroupsPage(service.task_groups)
    assert page.select_ids(received['group_id'], received['task_ids'][0])
    assert page.tree.currentItem().text(1) == '已就绪'
    assert page.tree.currentItem().text(2) == '未交接'
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.Yes)
    page._action('handoff')
    assert page.worker.wait(10000)
    app.processEvents()
    assert page.tree.currentItem().text(2) == '已交接'
    page.close()


def test_today_filter_and_unchanged_refresh_preserves_items():
    from datetime import datetime
    from database import DELIVERY_TIMEZONE
    from task_groups_ui import TaskGroupsPage
    app = QApplication.instance() or QApplication([])
    class Groups:
        calls = []
        def snapshot(self, **kwargs):
            self.calls.append(kwargs)
            return [{'id': 1, 'code': 'G1', 'tasks': []}]
        def messages(self, group_id): return []
    groups = Groups()
    page = TaskGroupsPage(groups)
    page.scope.setCurrentIndex(page.scope.findData('today'))
    assert groups.calls[-1]['received_day'] == datetime.now(DELIVERY_TIMEZONE).date().isoformat()
    item = page.tree.topLevelItem(0)
    page.refresh()
    assert page.tree.topLevelItem(0) is item
    page.close()


def test_long_task_name_is_visible_and_external_busy():
    from task_groups_ui import TaskGroupsPage
    app = QApplication.instance() or QApplication([])
    name = '超长项目名称' * 18
    class Groups:
        def snapshot(self, **kwargs):
            return [{'id': 1, 'code': 'G1', 'tasks': [{'id': 2, 'code': 'G1-01',
                'project_id': 3, 'project_name': name}]}]
        def messages(self, group_id): return []
    page = TaskGroupsPage(Groups())
    page.resize(580, 480); page.show(); page.refresh()
    group = page.tree.topLevelItem(0); group.setExpanded(True)
    page.tree.setCurrentItem(group.child(0)); app.processEvents()
    assert name in page.details.toPlainText()
    assert page.tree.wordWrap()
    assert page.tree.textElideMode() == Qt.ElideNone
    assert page.tree.visualItemRect(group.child(0)).height() > page.tree.fontMetrics().height() * 2
    page.set_busy(True)
    assert not page.add_button.isEnabled()
    page.set_busy(False)
    assert page.add_button.isEnabled()
    assert page.shutdown()
    page.close()


def test_moved_task_retains_its_original_message():
    from task_groups_ui import TaskGroupsPage
    app = QApplication.instance() or QApplication([])
    class Groups:
        def snapshot(self, **kwargs):
            return [{'id': 2, 'code': 'G2', 'tasks': [{'id': 3, 'code': 'G1-01',
                'group_id': 2, 'original_group_id': 1, 'project_name': '项目'}]}]
        def messages(self, group_id):
            if group_id == 1:
                return [{'raw_text': '原任务消息', 'task_ids': [3]},
                        {'raw_text': '其他任务消息', 'task_ids': [9]}]
            return [{'raw_text': '当前组消息', 'task_ids': []}]
    page = TaskGroupsPage(Groups()); page.select_ids(2, 3)
    assert '原任务消息' in page.details.toPlainText()
    assert '其他任务消息' not in page.details.toPlainText()
    page.close()
