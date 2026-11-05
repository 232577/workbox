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
    for control in (page.add_button, page.new_button, page.handoff_button, page.more_button, page.export_button):
        assert control.width() >= control.fontMetrics().horizontalAdvance(control.text()) + 12
        assert control.geometry().right() <= page.width()
    page.close()


def test_slim_actions_and_context_menus(monkeypatch):
    from task_groups_ui import TaskGroupsPage
    app = QApplication.instance() or QApplication([])
    class Groups:
        def snapshot(self, **kwargs):
            return [{'id': 1, 'code': 'G1', 'tasks': [{'id': 2, 'project_name': '甲'}]}]
        def messages(self, group_id): return []
    page = TaskGroupsPage(Groups())
    page.select_ids(1, 2)
    menu = page.more_button.menu()
    captions = [action.text() for action in menu.actions()]
    assert captions == ['新一轮任务', '查看项目文件', '复制本组未交', '撤销交接',
                        '关联交付记录', '核对本组收件消息', '高级任务操作']
    advanced = menu.actions()[-1].menu()
    assert [action.text() for action in advanced.actions()] == [
        '撤回任务', '重开任务', '移动任务到其他组', '重命名任务组', '设置组颜色', '复制当前筛选的全部未交清单']
    calls = []
    monkeypatch.setattr(page, '_run', lambda operation, **kwargs: calls.append(operation))
    monkeypatch.setattr('PySide6.QtWidgets.QMessageBox.question', lambda *args: 16384)
    advanced.actions()[0].trigger()
    assert calls and page._selected()[0]['id'] == 2
    assert page.export_button.text() == '导出本组表格'
    page.close()


def test_public_history_entry_respects_busy_and_shutdown(monkeypatch):
    from task_groups_ui import TaskGroupsPage
    app = QApplication.instance() or QApplication([])
    page = TaskGroupsPage(object())
    calls = []
    monkeypatch.setattr(page, '_history', lambda: calls.append('open'))
    page.import_history()
    page.set_busy(True)
    page.import_history()
    page.set_busy(False)
    page.worker = object()
    page.import_history()
    page.worker = None
    page._shutting_down = True
    page.import_history()
    assert calls == ['open']
    page.close()


def test_export_button_uses_current_group_and_honors_cancel_and_busy(monkeypatch, tmp_path):
    from task_groups_ui import TaskGroupsPage
    import export_ui
    import delivery_export
    app = QApplication.instance() or QApplication([])
    class Groups:
        db = object()
        def snapshot(self, **kwargs):
            return [{'id': 1, 'code': 'G1', 'tasks': []}, {'id': 4, 'code': 'G4', 'tasks': []}]
        def messages(self, group_id): return []
    page = TaskGroupsPage(Groups())
    page.refresh()
    page.export_button.click()
    assert '选择' in page.note.text()
    page.select_ids(4)
    chosen = []
    monkeypatch.setattr(export_ui, 'choose_workbook_destination', lambda *args: chosen.append(args) or None)
    page.export_button.click()
    assert '取消' in page.note.text()
    assert chosen[-1][1].endswith('G4')
    page.set_busy(True)
    page.export_group()
    assert len(chosen) == 1
    page.set_busy(False)
    target = tmp_path / 'out.xlsx'
    monkeypatch.setattr(export_ui, 'choose_workbook_destination', lambda *args: (target, None))
    calls = []
    monkeypatch.setattr(delivery_export, 'export_delivered_projects', lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr(page, '_run', lambda operation, **kwargs: operation())
    page.export_button.click()
    assert calls[0][0] == (page.groups.db, target)
    assert calls[0][1]['group_id'] == 4
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


def test_long_name_reflows_on_width_changes_without_losing_selection():
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QFontMetrics
    from PySide6.QtWidgets import QStyleOptionViewItem
    from task_groups_ui import TaskGroupsPage
    app = QApplication.instance() or QApplication([])
    name = '匿名新项目长名称用于核验完整显示和自动换行' * 3
    class Groups:
        def snapshot(self, **kwargs):
            return [{'id': 1, 'code': 'G20261105-001', 'tasks': [
                {'id': 2, 'code': 'G20261105-001-01', 'project_name': name}]}]
        def messages(self, group_id): return [{'raw_text': name}]
    page = TaskGroupsPage(Groups()); page.resize(1050, 650); page.show(); page.refresh()
    page.splitter.setSizes([850, 170])
    group = page.tree.topLevelItem(0); group.setExpanded(True)
    task = group.child(0); page.tree.setCurrentItem(task)
    app.processEvents()
    heights = []
    for width in (1050, 580, 1050):
        page.resize(width, 650)
        for _ in range(3): app.processEvents()
        index = page.tree.indexFromItem(task)
        rect = page.tree.visualItemRect(task)
        option = QStyleOptionViewItem()
        page.tree.itemDelegate().initStyleOption(option, index)
        # Actual text area lies inside the first column after the tree indentation.
        text_width = max(1, rect.width() - 40)
        required = QFontMetrics(option.font).boundingRect(QRect(0, 0, text_width, 100000),
                                                           Qt.TextWordWrap, option.text).height()
        heights.append(rect.height())
        assert rect.height() >= required + 4, (width, rect.height(), required)
        assert page.tree.currentItem() is task and task.isSelected()
        assert task.data(0, Qt.DisplayRole).endswith(name)
        assert name in page.details.toPlainText()
    assert heights[1] > heights[0] and heights[2] < heights[1]
    page.close()


def test_reflow_keeps_reading_anchor_and_selected_task():
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QAbstractItemView
    from task_groups_ui import TaskGroupsPage
    app = QApplication.instance() or QApplication([])
    class Groups:
        def snapshot(self, **kwargs):
            return [{'id': 1, 'code': 'G1', 'tasks': [
                {'id': i, 'code': f'G1-{i:02}', 'project_name': ('匿名长项目' * 8) + str(i)}
                for i in range(1, 19)]}]
        def messages(self, group_id): return []
    page = TaskGroupsPage(Groups()); page.resize(1050, 650); page.show(); page.refresh()
    page.splitter.setSizes([850, 170]); app.processEvents()
    group = page.tree.topLevelItem(0); group.setExpanded(True)
    selected = group.child(9); page.tree.setCurrentItem(selected)
    page.tree.scrollToItem(selected, QAbstractItemView.PositionAtTop)
    app.processEvents()
    top = page.tree.itemAt(QPoint(80, 2))
    assert top is not None
    page.resize(580, 650)
    for _ in range(4): app.processEvents()
    assert page.tree.currentItem() is selected and selected.isSelected()
    assert page.tree.itemAt(QPoint(80, 2)) is top
    page.resize(1050, 650)
    for _ in range(4): app.processEvents()
    assert page.tree.currentItem() is selected and selected.isSelected()
    assert page.tree.itemAt(QPoint(80, 2)) is top
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


def test_current_task_detail_tracks_current_without_selection_change():
    from PySide6.QtCore import QItemSelectionModel
    from task_groups_ui import TaskGroupsPage
    app = QApplication.instance() or QApplication([])
    class Groups:
        def snapshot(self, **kwargs):
            return [{'id': 1, 'code': 'G1', 'tasks': [
                {'id': 2, 'code': 'G1-01', 'project_name': '甲项目'},
                {'id': 3, 'code': 'G1-02', 'project_name': '乙项目'}]}]
        def messages(self, group_id): return [{'raw_text': '原消息'}]
    page = TaskGroupsPage(Groups()); page.refresh()
    group = page.tree.topLevelItem(0); group.setExpanded(True)
    page.tree.setCurrentItem(group.child(0))
    page.tree.setCurrentItem(group.child(1), 0, QItemSelectionModel.NoUpdate)
    assert 'G1-02' in page.details.toPlainText()
    assert '乙项目' in page.details.toPlainText()
    page.close()


def test_selected_group_and_task_text_contrast_in_both_themes():
    from task_groups_ui import TaskGroupsPage
    from theme import apply_to, set_theme, tokens
    app = QApplication.instance() or QApplication([])
    class Groups:
        def snapshot(self, **kwargs):
            return [{'id': 1, 'code': 'G20261105-001', 'color': '#507080', 'tasks': [
                {'id': 2, 'code': 'G20261105-001-01', 'project_name': '匿名项目'}]}]
        def messages(self, group_id): return [{'raw_text': '原消息'}]
    try:
        for mode in ('light', 'dark'):
            set_theme(mode, persist=False)
            page = TaskGroupsPage(Groups()); apply_to(page); page.resize(900, 550)
            page.show(); page.refresh(); app.processEvents()
            assert ('selection-color: ' + tokens(mode)['ink']).lower() in page.tree.styleSheet().lower()
            group = page.tree.topLevelItem(0); group.setExpanded(True)
            for item in (group, group.child(0)):
                page.tree.setCurrentItem(item); page.tree.setFocus(); app.processEvents()
                rect = page.tree.visualItemRect(item)
                image = page.tree.viewport().grab().toImage()
                # Ignore the indicator and cell edge; enough foreground pixels must
                # contrast with the selected row surface in both themes.
                matches = 0
                for y in range(max(0, rect.top() + 6), min(image.height(), rect.bottom() - 5)):
                    for x in range(max(0, rect.left() + 22), min(image.width(), rect.right() - 10)):
                        color = image.pixelColor(x, y)
                        if (mode == 'light' and max(color.red(), color.green(), color.blue()) < 110) or (
                            mode == 'dark' and min(color.red(), color.green(), color.blue()) > 180):
                            matches += 1
                assert matches > 25, (mode, item.text(0), matches)
            page.close()
    finally:
        set_theme('light', persist=False)
