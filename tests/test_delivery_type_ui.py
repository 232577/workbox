"""交付页面修改当前类型，首次交付事实、资格和文件保持不变。"""
from datetime import date, datetime, timezone
import time

import pytest
from PySide6.QtCore import Qt, QItemSelectionModel, QPointF
from PySide6.QtGui import QTextLayout, QTextOption
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QTextEdit, QStyle, QStyleOptionViewItem, QAbstractItemView

import database
import delivery_ui
from desktop_service import DesktopService
from theme import install_theme, set_theme


@pytest.fixture(scope='module')
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


@pytest.fixture
def dialog(qapp, tmp_path, monkeypatch):
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            moment = datetime(2026, 10, 29, 4, tzinfo=timezone.utc)
            return moment.astimezone(tz) if tz else moment.replace(tzinfo=None)
    monkeypatch.setattr(database, 'datetime', Frozen)
    service = DesktopService(tmp_path / 'data/workbox.db')
    service.set_default_project_type(None)
    service.receive_text('1. 类型甲平台\n2. 类型乙平台\n3. 历史丙平台')
    archive = tmp_path / 'archive'
    archive.mkdir()
    (archive / '说明.txt').write_text('真实隔离档案', encoding='utf-8')
    with service.db.connect() as connection:
        connection.execute("UPDATE projects SET status='已交付',delivered_at='2026-10-29T03:00:00Z'")
        for pid, name, at in ((1, '类型甲平台', '2026-10-29T02:00:00Z'),
                              (1, '类型甲平台', '2026-10-29T03:00:00Z'),
                              (2, '类型乙平台', '2026-10-29T03:00:00Z'),
                              (3, '历史丙平台', '2026-10-28T03:00:00Z'),
                              (3, '历史丙平台', '2026-10-29T03:00:00Z')):
            connection.execute('''INSERT INTO delivery_events(project_id,project_name,batch_name,
                kind,archive_path,occurred_at) VALUES(?,?,?,'manual-files',?,?)''',
                (pid, name, '本批', str(archive), at))
    value = delivery_ui.TodayDeliveryDialog(service)
    value.show()
    qapp.processEvents()
    yield value
    value.shutdown()


def select(dialog, *ids):
    dialog.table.clearSelection()
    for row in range(dialog.table.rowCount()):
        if dialog.table.item(row, 0).data(Qt.UserRole) in ids:
            index = dialog.table.model().index(row, 0)
            dialog.table.selectionModel().select(index, QItemSelectionModel.Select | QItemSelectionModel.Rows)
            dialog.table.selectionModel().setCurrentIndex(index, QItemSelectionModel.NoUpdate)


def finish(dialog):
    until = time.monotonic() + 5
    while dialog.worker is not None and time.monotonic() < until:
        QApplication.processEvents()
        time.sleep(.01)
    QApplication.processEvents()
    assert dialog.worker is None


def facts(service):
    with service.db.connect() as connection:
        return {table: [tuple(row) for row in connection.execute('SELECT * FROM ' + table)]
                for table in ('projects', 'delivery_events', 'delivery_event_actions',
                              'project_delivery_count_actions')}


def test_total_accessibility_tracks_actual_count_and_read_failure(dialog, monkeypatch):
    from PySide6.QtGui import QAccessible
    accessible = QAccessible.queryAccessibleInterface(dialog.type_total)
    assert accessible.text(QAccessible.Name) == dialog.type_total.text()
    assert '合计 2 个项目' in accessible.text(QAccessible.Name)
    dialog.service.set_project_delivery_counting([1], False, confirmed=True)
    dialog.refresh()
    assert '合计 1 个项目' in accessible.text(QAccessible.Name)
    monkeypatch.setattr(dialog.service.db, 'delivery_history',
                        lambda *a, **k: (_ for _ in ()).throw(OSError('隔离读取失败')))
    dialog.refresh()
    assert '暂不可读' in accessible.text(QAccessible.Name)
    assert accessible.text(QAccessible.Name) == dialog.type_total.text()


def test_type_change_counts_current_type_without_recounting_delivery(dialog, monkeypatch):
    assert hasattr(dialog, 'change_type_button')
    kind = dialog.service.create_project_type('独立业务')
    before = facts(dialog.service)
    file = next(dialog.service.db.path.parent.parent.rglob('说明.txt'))
    source = file.read_bytes()
    select(dialog, 1, 2)
    received = []
    def choose(service, parent, projects):
        received.append(projects)
        return True, kind['id']
    monkeypatch.setattr(delivery_ui, 'choose_project_type', choose)
    changed, completed = [], []
    dialog.statistics_changed.connect(lambda: changed.append(True))
    dialog.operation_completed.connect(completed.append)
    dialog.change_type_button.click()
    finish(dialog)
    assert {row['id'] for row in received[0]} == {1, 2}
    assert all('workspace' in row and 'zips' in row for row in received[0])
    assert dialog.service.get_project(1)['project_type_id'] == kind['id']
    assert dialog.service.get_project(2)['project_type_id'] == kind['id']
    assert dialog.table.columnCount() == 5
    assert {dialog.table.item(i, 4).text() for i in range(dialog.table.rowCount())
            if dialog.table.item(i, 0).data(Qt.UserRole) in (1, 2)} == {'独立业务'}
    assert '独立业务：2' in dialog.type_counts.toPlainText()
    assert '合计 2 个项目' in dialog.type_total.text()
    assert len(changed) == len(completed) == 1
    assert facts(dialog.service) == before and file.read_bytes() == source
    dialog.change_type_button.click()
    finish(dialog)
    assert facts(dialog.service) == before
    assert dialog.service.db.delivery_history(date(2026, 10, 29))['project_count'] == 2


def test_cancel_no_selection_and_failure_preserve_selection_and_note(dialog, monkeypatch):
    assert hasattr(dialog, 'change_type_button')
    calls = []
    monkeypatch.setattr(delivery_ui, 'choose_project_type', lambda *args: calls.append(args) or (False, None))
    assert not dialog.change_type_button.isEnabled()
    dialog.manage_project_types()
    assert not calls
    select(dialog, 1)
    dialog.reason.setText('保留说明')
    before = facts(dialog.service)
    dialog.change_type_button.click()
    assert len(calls) == 1 and facts(dialog.service) == before
    assert dialog._selected_projects()[0]['project_id'] == 1
    kind = dialog.service.create_project_type('失败隔离类型')
    monkeypatch.setattr(delivery_ui, 'choose_project_type', lambda *args: (True, kind['id']))
    def fail(*args):
        raise OSError('隔离写入失败')
    monkeypatch.setattr(dialog.service, 'set_project_types', fail)
    dialog.change_type_button.click()
    finish(dialog)
    assert '隔离写入失败' in dialog.status_note.text()
    assert dialog.reason.text() == '保留说明'
    assert dialog._selected_projects()[0]['project_id'] == 1
    assert dialog.change_type_button.isEnabled() and facts(dialog.service) == before


@pytest.mark.parametrize('protected', ['archived', 'deleted'])
def test_mixed_selection_with_protected_project_cannot_change(dialog, monkeypatch, protected):
    assert hasattr(dialog, 'change_type_button')
    with dialog.service.db.connect() as connection:
        if protected == 'archived':
            connection.execute("INSERT INTO project_details(project_id,archived_at) VALUES(1,'2026-10-29') ON CONFLICT(project_id) DO UPDATE SET archived_at='2026-10-29'")
        else:
            connection.execute("INSERT INTO project_deletions(project_id,deleted_at) VALUES(1,'2026-10-29')")
    dialog.refresh()
    calls = []
    monkeypatch.setattr(delivery_ui, 'choose_project_type', lambda *args: calls.append(args) or (True, None))
    select(dialog, 1, 2)
    assert not dialog.change_type_button.isEnabled()
    dialog.manage_project_types()
    assert not calls
    select(dialog, 2)
    assert dialog.change_type_button.isEnabled()


def test_type_refresh_updates_rename_disable_and_keeps_existing_items(dialog):
    assert hasattr(dialog, 'type_counts')
    kind = dialog.service.create_project_type('原类型')
    dialog.service.set_project_types([1], kind['id'])
    dialog.timer.timeout.emit()
    select(dialog, 1)
    item, detail = dialog.table.item(dialog.table.currentRow(), 0), dialog.details.item(0, 0)
    counts_document = dialog.type_counts.document()
    dialog.service.rename_project_type(kind['id'], '改名后的类型')
    dialog.service.set_project_type_active(kind['id'], False)
    dialog.timer.timeout.emit()
    assert dialog.table.item(dialog.table.currentRow(), 0) is item
    assert dialog.details.item(0, 0) is detail
    assert '改名后的类型' in dialog.table.item(dialog.table.currentRow(), 4).text()
    assert '改名后的类型（已停用）：1' in dialog.type_counts.toPlainText()
    assert '合计 2 个项目' in dialog.type_total.text()
    text = dialog.type_counts.toPlainText()
    dialog.timer.timeout.emit()
    assert dialog.type_counts.document() is counts_document and dialog.type_counts.toPlainText() == text


def test_type_counts_follow_date_keyword_and_counting_eligibility(dialog):
    assert hasattr(dialog, 'type_counts')
    kind = dialog.service.create_project_type('交付分类')
    dialog.service.set_project_types([1, 2, 3], kind['id'])
    dialog.service.set_project_delivery_counting([2], False, confirmed=True)
    dialog.refresh()
    assert '交付分类：1' in dialog.type_counts.toPlainText()
    assert '合计 1 个项目' in dialog.type_total.text()
    dialog.search.setText('历史丙')
    assert '交付分类：0' in dialog.type_counts.toPlainText()
    assert '合计 0 个项目' in dialog.type_total.text()
    dialog.all_dates.setChecked(True)
    assert '交付分类：1' in dialog.type_counts.toPlainText()
    assert '合计 1 个项目' in dialog.type_total.text()


def test_empty_and_many_long_types_wrap_scroll_at_minimum_size(dialog, qapp):
    assert hasattr(dialog, 'type_counts')
    long_name = 'W' * 40
    dialog.service.create_project_type(long_name)
    for index in range(25):
        dialog.service.create_project_type(f'附加类别{index}')
    dialog.search.setText('不存在的项目')
    dialog.resize(680, 460)
    qapp.processEvents()
    assert dialog.width() == 680 and dialog.height() == 460
    assert '生成材料：0' in dialog.type_counts.toPlainText()
    assert '可运行：0' in dialog.type_counts.toPlainText()
    assert '未分类：0' in dialog.type_counts.toPlainText()
    assert long_name + '：0' in dialog.type_counts.toPlainText()
    assert '合计 0 个项目' in dialog.type_total.text()
    assert dialog.type_counts.lineWrapMode() == QTextEdit.WidgetWidth
    assert dialog.type_counts.horizontalScrollBar().maximum() == 0
    assert dialog.type_counts.verticalScrollBar().maximum() > 0
    dialog.type_counts.verticalScrollBar().setValue(20)
    position = dialog.type_counts.verticalScrollBar().value()
    dialog.refresh()
    assert dialog.type_counts.verticalScrollBar().value() == position
    for widget in (dialog.change_type_button, dialog.close_button, dialog.void_button,
                   dialog.restore_button, dialog.type_total, dialog.table, dialog.details):
        assert dialog.rect().contains(widget.mapTo(dialog, widget.rect().topLeft()))
        assert dialog.rect().contains(widget.mapTo(dialog, widget.rect().bottomRight()))
    dialog.resize(980, 680)
    qapp.processEvents()
    assert dialog.width() == 980 and dialog.height() == 680


def test_busy_blocks_type_work_and_open_archive_action(dialog, monkeypatch):
    assert hasattr(dialog, 'change_type_button')
    select(dialog, 1)
    assert dialog.open_folder_button.isEnabled()
    calls = []
    monkeypatch.setattr(delivery_ui, 'choose_project_type', lambda *args: calls.append(args) or (False, None))
    dialog.worker = object()
    try:
        dialog._update_open_action()
        assert not dialog.change_type_button.isEnabled()
        assert not dialog.open_folder_button.isEnabled()
        dialog.manage_project_types()
        assert not calls
    finally:
        dialog.worker = None
        dialog._update_open_action()


def test_success_feedback_and_old_actions_fit_minimum_window(dialog, qapp, monkeypatch):
    kind = dialog.service.create_project_type('材料整理')
    monkeypatch.setattr(delivery_ui, 'choose_project_type', lambda *args: (True, kind['id']))
    dialog.resize(680, 460)
    qapp.processEvents()
    select(dialog, 1)
    dialog.change_type_button.click()
    finish(dialog)
    assert dialog.width() == 680 and dialog.height() == 460
    assert dialog.status_note.isVisible()
    assert dialog.void_button.isEnabled()
    assert dialog.exclude_count_button.isEnabled()
    for widget in (dialog.change_type_button, dialog.close_button, dialog.status_note, dialog.details):
        assert dialog.rect().contains(widget.mapTo(dialog, widget.rect().bottomRight()))


def test_cancelling_picker_still_refreshes_immediate_dictionary_edits(dialog, monkeypatch):
    kind = dialog.service.create_project_type('可调整类型')
    dialog.service.set_project_types([1], kind['id'])
    dialog.refresh()
    select(dialog, 1)
    def edit_dictionary(service, parent, projects):
        service.rename_project_type(kind['id'], '取消选择也保留改名')
        return False, None
    monkeypatch.setattr(delivery_ui, 'choose_project_type', edit_dictionary)
    dialog.change_type_button.click()
    assert '取消选择也保留改名：1' in dialog.type_counts.toPlainText()
    assert dialog.service.get_project(1)['project_type_id'] == kind['id']
    assert dialog._selected_projects()[0]['project_id'] == 1


def settle(qapp):
    QTest.qWait(120)
    qapp.processEvents()


def assert_complete_cell_text_fits(table, item):
    index = table.indexFromItem(item)
    option = QStyleOptionViewItem()
    table.initViewItemOption(option)
    table.itemDelegateForIndex(index).initStyleOption(option, index)
    option.state = option.state | QStyle.State_Selected if item.isSelected() else option.state & ~QStyle.State_Selected
    option.rect = table.visualItemRect(item)
    # Fluent 1.11.3 item横向16px加Qt标准文本边距3px，垂直由库delegate.margin控制。
    content = option.rect.adjusted(19, table.itemDelegate().margin, -19, -table.itemDelegate().margin)
    text_option = QTextOption()
    text_option.setWrapMode(QTextOption.WordWrap)
    layout = QTextLayout(option.text, option.font)
    layout.setTextOption(text_option)
    height, covered = 0., 0
    layout.beginLayout()
    while True:
        line = layout.createLine()
        if not line.isValid():
            break
        line.setLineWidth(content.width())
        line.setPosition(QPointF(0, height))
        height += line.height()
        covered += line.textLength()
        assert line.naturalTextWidth() <= content.width()
    layout.endLayout()
    assert option.textElideMode == Qt.ElideNone
    assert covered == len(option.text)
    assert height <= content.height(), (item.text(), height, content)


@pytest.mark.parametrize('width,height', [(680, 460), (980, 680)])
@pytest.mark.parametrize('mode', ['light', 'dark'])
@pytest.mark.parametrize('selected', [False, True])
def test_long_project_and_type_names_wrap_completely(dialog, qapp, width, height, mode, selected):
    name = '跨部门数据治理与业务档案材料协同管理平台及版本交付系统（业务中心）'
    kind_name = '复杂业务分类名称' * 5
    pid = dialog.service.db.create_capture(name, '完整批次说明', [(name, False)])['project_ids'][0]
    kind = dialog.service.create_project_type(kind_name)
    dialog.service.set_project_types([pid], kind['id'])
    with dialog.service.db.connect() as connection:
        connection.execute('''INSERT INTO delivery_events(project_id,project_name,batch_name,kind,archive_path,
            occurred_at) VALUES(?,?,?,'manual-files','','2026-10-29T04:00:00Z')''', (pid, name, '完整批次说明'))
    dialog.refresh()
    install_theme(dialog.service)
    set_theme(mode, persist=False)
    dialog.resize(width, height)
    settle(qapp)
    if selected:
        select(dialog, pid)
    else:
        dialog.table.clearSelection()
    row = next(i for i in range(dialog.table.rowCount()) if dialog.table.item(i, 0).data(Qt.UserRole) == pid)
    dialog.table.scrollToItem(dialog.table.item(row, 0), QAbstractItemView.PositionAtTop)
    settle(qapp)
    assert dialog.table.isColumnHidden(1) is (width < 820)
    for column, text in ((0, name), (4, kind_name)):
        item = dialog.table.item(row, column)
        assert item.text() == text and text in item.toolTip()
        assert_complete_cell_text_fits(dialog.table, item)
    assert dialog.table.rowHeight(row) <= dialog.table.viewport().height()
    assert '完整批次说明' in dialog.table.item(row, 0).toolTip()
    assert dialog.width() == width and dialog.height() == height
    for widget in (dialog.change_type_button, dialog.close_button, dialog.details):
        assert dialog.rect().contains(widget.mapTo(dialog, widget.rect().bottomRight()))


def test_project_row_reflow_preserves_reading_anchor_selection_and_coalesces(dialog, qapp, monkeypatch):
    names = [f'同名前缀跨部门业务数据治理与项目文档协同交付系统第{index:03d}项' for index in range(60)]
    ids = dialog.service.db.create_capture('\n'.join(names), '长项目批次', [(name, False) for name in names])['project_ids']
    with dialog.service.db.connect() as connection:
        for pid, name in zip(ids, names):
            connection.execute('''INSERT INTO delivery_events(project_id,project_name,batch_name,kind,archive_path,
                occurred_at) VALUES(?,?,?,'manual-files','','2026-10-29T04:00:00Z')''', (pid, name, '长项目批次'))
    dialog.refresh()
    settle(qapp)
    select(dialog, ids[0])
    dialog.table.scrollToItem(dialog.table.item(35, 0), QAbstractItemView.PositionAtTop)
    dialog.table.verticalScrollBar().setValue(dialog.table.verticalScrollBar().value() + 7)
    row = dialog.table.rowAt(0)
    anchor = dialog.table.item(row, 0).data(Qt.UserRole), dialog.table.rowViewportPosition(row)
    calls = []
    resize_rows = dialog.table.resizeRowsToContents
    def measured():
        calls.append(True)
        resize_rows()
    monkeypatch.setattr(dialog.table, 'resizeRowsToContents', measured)
    for width in (850, 730, 680):
        dialog.resize(width, 460)
        QTest.qWait(10)
    settle(qapp)
    row = dialog.table.rowAt(0)
    assert (dialog.table.item(row, 0).data(Qt.UserRole), dialog.table.rowViewportPosition(row)) == anchor
    assert {row['project_id'] for row in dialog._selected_projects()} == {ids[0]}
    assert len(calls) == 1


def test_default_type_counts_fit_one_line_at_minimum_width(dialog, qapp):
    dialog.resize(680, 460)
    settle(qapp)
    assert dialog.type_counts.toPlainText().startswith('生成材料：0  ·  可运行：0  ·  未分类：2')
    assert dialog.type_counts.document().firstBlock().layout().lineCount() == 1
    assert dialog.type_counts.verticalScrollBar().maximum() == 0
