"""明确新组、只看未交接、分组复制与准确版本确认。"""
import time
from types import SimpleNamespace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox

from task_groups_ui import TaskGroupsPage


class Groups:
    def snapshot(self, **kwargs):
        return [dict(id=1, code='G20261105-001', title='匿名组甲', color='#467CB3',
            counts=dict(total=2, handed=1, ready=2, pending=1, withdrawn=0, needs_review=0),
            tasks=[dict(id=2, code='G20261105-001-01', project_name='匿名甲任务', project_id=1,
                material_ready=True, handed_over=False, binding_id=9, archive_path='D:/anonymous/archive.zip'),
                dict(id=3, code='G20261105-001-02', project_name='匿名完成任务', project_id=2,
                     material_ready=True, handed_over=True)]),
            dict(id=4, code='G20261105-002', title='匿名组乙', tasks=[dict(id=5,
                code='G20261105-002-01', project_name='另一组私有任务', project_id=3)])]
    def messages(self, group_id):
        return []
    def handoff(self, ids, **kwargs):
        self.confirmed = (ids, kwargs)
        return {}


def page():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    value = TaskGroupsPage(Groups())
    value.refresh()
    return value


def test_explicit_new_group_does_not_depend_on_selected_group(monkeypatch):
    value = page()
    emitted = []
    value.task_text_requested.connect(lambda *args: emitted.append(args))
    monkeypatch.setattr(QInputDialog, 'getMultiLineText', lambda *a: ('1. 匿名新任务系统', True))
    value.new_group()
    assert emitted == [('1. 匿名新任务系统', None, False)]
    value.close()


def test_unhanded_filter_keeps_whole_group_counts():
    value = page()
    value.only_unhanded.setChecked(True)
    group = value.tree.topLevelItem(0)
    assert group.childCount() == 1
    assert '总 2' in group.text(0)
    assert '1/2' in group.text(2)
    value.close()


def test_copy_is_selected_group_only_unless_explicitly_all(monkeypatch):
    value = page()
    written = []
    monkeypatch.setattr(QApplication, 'clipboard', lambda: SimpleNamespace(setText=written.append))
    value.select_ids(1, 2)
    value._copy()
    assert len(written) == 1 and '匿名甲任务' in written[0] and '另一组私有任务' not in written[0]
    value.close()


def test_handoff_confirmation_pins_visible_archive(monkeypatch):
    value = page()
    value.select_ids(1, 2)
    shown = []
    def confirm(_parent, _title, body, *args):
        shown.append(body)
        return QMessageBox.Yes
    monkeypatch.setattr(QMessageBox, 'question', confirm)
    value._action('handoff')
    deadline = time.monotonic() + 5
    while value.worker is not None and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(.01)
    assert value.worker is None
    assert 'D:/anonymous/archive.zip' in shown[0]
    assert value.groups.confirmed[1]['expected_bindings'] == {2: 9}
    value.close()


def test_cancelled_group_export_is_not_reported_as_success():
    value = page()
    value.worker = SimpleNamespace(error='', cancelled=True, result=None,
        task_operation_kind='export', deleteLater=lambda: None)
    value._finished()
    assert value.worker is None and '取消' in value.note.text()
    value.close()
