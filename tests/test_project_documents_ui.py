import time
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QDragEnterEvent, QDropEvent
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QHeaderView, QMessageBox

from workbox.desktop.compact_ui import CompactPanel
from workbox.desktop.desktop_service import DesktopService


@pytest.fixture(scope='module')
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def wait(predicate):
    end = time.monotonic()+8
    while time.monotonic()<end:
        QApplication.processEvents()
        if predicate():
            return
        time.sleep(.01)
    assert predicate()


@pytest.fixture
def panel(qapp,tmp_path):
    service=DesktopService(tmp_path/'data/workbox.db')
    service.receive_text('1. 文档甲系统\n2. 文档乙系统')
    for pid,name in ((1,'甲说明.txt'),(2,'乙说明.docx')):
        (Path(service.workspaces.get_workspace(pid)['path'])/name).write_text('隔离文档',encoding='utf-8')
    value=CompactPanel(service)
    value.show()
    yield value
    if hasattr(value,'document_pane'):
        value.document_pane.shutdown()
    value.hide()


def choose(panel,pid):
    item=next(panel.project_list.item(i) for i in range(panel.project_list.count())
              if panel.project_list.item(i).data(Qt.UserRole)==pid)
    panel.project_list.setCurrentItem(item)


def history_reader(monkeypatch,panel,tmp_path):
    from workbox.desktop import project_documents_ui
    original=project_documents_ui.list_project_documents
    history=tmp_path/'history'/'甲说明.txt';history.parent.mkdir();history.write_text('旧稿',encoding='utf-8')
    def read(service,pid,**kwargs):
        include=kwargs.pop('include_history',False)
        result=original(service,pid,**kwargs)
        if include and pid==1:
            row=dict(result['documents'][0],id='history:1',name=history.name,path=str(history),
                     relative_path=history.name,kind='历史文稿',work_file_id=1,
                     superseded_at='2026-11-09T01:00:00Z')
            result['documents'].append(row)
        return result
    monkeypatch.setattr(project_documents_ui,'list_project_documents',read)
    return history,read


def test_history_uses_native_menu_and_keeps_current_selection(panel,tmp_path,monkeypatch):
    history,_=history_reader(monkeypatch,panel,tmp_path)
    choose(panel,1);pane=panel.document_pane
    wait(lambda:pane.worker is None and pane.table.rowCount()==1)
    pane.table.selectRow(0);selected=pane.current_document()['id']
    action=next(action for action in pane.file_menu.actions() if action.text()=='查看历史文稿')
    assert action.isCheckable() and not action.isChecked()
    action.trigger()
    wait(lambda:pane.worker is None and pane.documents and pane.documents[0]['kind']=='历史文稿')
    assert pane.title.text()=='历史文稿' and pane.table.rowCount()==1
    assert '2026' in pane.table.item(0,0).text() and '保留于' in pane.table.item(0,0).text()
    opened=[];located=[]
    monkeypatch.setattr(QDesktopServices,'openUrl',lambda url:opened.append(url.toLocalFile()) or True)
    pane.folder_requested.connect(located.append)
    pane.table.selectRow(0);pane.open_selected();pane.open_selected_folder()
    assert not pane.archive_history_action.isEnabled()
    assert list(map(Path,opened))==[history] and list(map(Path,located))==[history.parent]
    action.trigger()
    wait(lambda:pane.worker is None and pane.documents and pane.documents[0]['kind']=='工作文件')
    assert pane.title.text()=='当前文件' and pane.current_document()['id']==selected


def test_history_mode_rejects_old_current_reader_and_resets_for_new_project(panel,tmp_path,monkeypatch):
    from threading import Event
    from workbox.desktop import project_documents_ui
    _,read=history_reader(monkeypatch,panel,tmp_path)
    choose(panel,1);pane=panel.document_pane
    wait(lambda:pane.worker is None and pane.table.rowCount()==1)
    entered,release=Event(),Event()
    def delayed(service,pid,**kwargs):
        if not kwargs.get('include_history'):
            entered.set();release.wait(2)
        return read(service,pid,**kwargs)
    monkeypatch.setattr(project_documents_ui,'list_project_documents',delayed)
    pane.request_refresh();wait(entered.is_set)
    pane.history_action.trigger();release.set()
    wait(lambda:pane.worker is None and pane.documents and pane.documents[0]['kind']=='历史文稿')
    assert all(row['kind']=='历史文稿' for row in pane.documents)
    choose(panel,2)
    wait(lambda:pane.worker is None and pane.documents and pane.documents[0]['name']=='乙说明.docx')
    assert not pane.history_action.isChecked() and pane.title.text()=='当前文件'


def test_empty_history_has_clear_scope_and_return_action(panel):
    choose(panel,1);pane=panel.document_pane
    wait(lambda:pane.worker is None and pane.table.rowCount()==1)
    pane.history_action.trigger()
    wait(lambda:pane.worker is None and not pane.documents)
    assert pane.title.text()=='历史文稿' and '没有历史文稿' in pane.hint.text()
    assert pane.file_menu_button.isVisible() and pane.history_action.isChecked()
    pane.history_action.trigger()
    wait(lambda:pane.worker is None and pane.table.rowCount()==1)


def test_manual_history_action_only_emits_for_registered_current_documents(panel,monkeypatch):
    root=Path(panel.service.workspaces.get_workspace(1)['path'])
    target=root/'甲说明.txt'
    with panel.service.db.connect() as connection:
        file_id=connection.execute('INSERT INTO work_files(project_id,source_path,path,filename,size,sha256,mtime_ns) '
            'VALUES(?,?,?,?,?,?,?)',(1,str(target),str(target),target.name,target.stat().st_size,'a'*64,100)).lastrowid
    choose(panel,1);pane=panel.document_pane
    wait(lambda:pane.worker is None and pane.table.rowCount()==1)
    action=next(action for action in pane.file_menu.actions() if action.text()=='移入历史文稿…')
    assert not action.isEnabled()
    pane.table.selectRow(0)
    assert action.isEnabled()
    received=[];pane.history_archive_requested.connect(received.append)
    prompts=[]
    def decline_history(parent,title,text,buttons,default):
        prompts.append((title,text,buttons,default))
        return QMessageBox.No
    monkeypatch.setattr(QMessageBox,'question',decline_history)
    before=target.read_bytes()
    action.trigger()
    assert received==[dict(project_id=1,work_file_id=file_id,name=target.name,path=str(target))]
    assert len(prompts)==1 and prompts[0][0]=='移入历史文稿' and target.name in prompts[0][1]
    assert prompts[0][2]==QMessageBox.Yes|QMessageBox.No and prompts[0][3]==QMessageBox.No
    unknown=root/'未知.txt';unknown.write_bytes(b'unknown')
    package=root/'包.zip';package.write_bytes(b'not executed')
    with panel.service.db.connect() as connection:
        connection.execute('INSERT INTO work_files(project_id,source_path,path,filename,size,sha256,mtime_ns) '
            'VALUES(?,?,?,?,?,?,?)',(1,str(package),str(package),package.name,package.stat().st_size,'b'*64,100))
    pane.request_refresh();wait(lambda:pane.worker is None and pane.table.rowCount()==3)
    for name in ('未知.txt','包.zip'):
        row=next(i for i,record in enumerate(pane.documents) if record['name']==name)
        pane.table.selectRow(row)
        assert not action.isEnabled()
    assert target.read_bytes()==before and len(received)==1 and len(prompts)==1


def test_projects_are_left_and_selected_documents_are_right(panel,qapp):
    assert hasattr(panel,'project_splitter'), '缺少项目与文档左右分栏'
    choose(panel,2)
    pane=panel.document_pane
    wait(lambda: pane.table.rowCount()==1 and pane.worker is None)
    assert pane.table.item(0,0).text()=='乙说明.docx'
    assert panel.project_splitter.orientation()==Qt.Horizontal
    panel.show_notice('文件已经收进项目，原件保留。')
    left=panel.project_list.mapTo(panel,panel.project_list.rect().topRight())
    right=pane.table.mapTo(panel,pane.table.rect().topLeft())
    assert left.x()<right.x()
    for width,height in ((840,670),(660,492)):
        panel.resize(width,height)
        qapp.processEvents()
        panel.show_project_detail()
        qapp.processEvents()
        assert not panel.project_side.isVisible() and panel.document_side.isVisible()
        assert panel.project_back_button.isVisible()
        assert panel.width()==width and panel.height()==height and pane.table.viewport().height()>=40
        pane.table.selectRow(0)
        assert panel.rect().contains(pane.open_button.mapTo(panel,pane.open_button.rect().bottomRight()))
        assert pane.open_button.mapTo(pane,pane.open_button.rect().bottomLeft()).y() < pane.table.geometry().top(), '文件操作应靠近表头且不遮挡数据'


def test_ctrl_selection_previews_the_focused_project_and_blocks_single_project_actions(panel, qapp):
    panel.resize(1100, 700)
    panel.show_project_queue()
    workspace = Path(panel.service.workspaces.get_workspace(2)['path'])
    panel.service.receive_file(workspace / '乙说明.docx', project_id=2)
    for pid in (1, 2):
        panel.service.set_status(pid, '待交付')
    panel.refresh()
    view, pane = panel.project_list, panel.document_pane
    items = {view.item(i).data(Qt.UserRole): view.item(i) for i in range(view.count())}
    choose(panel, 1)
    wait(lambda: pane.worker is None and pane.table.rowCount() == 1)
    qapp.processEvents()

    QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.ControlModifier,
                     view.visualItemRect(items[2]).center())
    assert {item.data(Qt.UserRole) for item in view.selectedItems()} == {1, 2}
    assert panel.current_project is None
    assert pane.project_id == 2
    assert panel.project_title.text() == '文档乙系统' and '已选 2 项' in panel.project_metadata.text()
    wait(lambda: pane.worker is None and pane.table.rowCount() == 1)
    assert pane.table.item(0, 0).text() == '乙说明.docx'
    assert not panel.confirm_delivery_button.isEnabled() and not panel.project_actions_button.isVisible()
    pane.table.selectRow(0)
    assert pane.open_button.isEnabled() and pane.folder_action.isEnabled() and pane.history_action.isEnabled()
    assert not pane.team_action.isEnabled() and not pane.archive_history_action.isEnabled()
    transfers, archived = QSignalSpy(pane.team_requested), QSignalSpy(pane.history_archive_requested)
    pane.request_team_delivery()
    pane.request_archive_history()
    assert transfers.count() == archived.count() == 0

    QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.ControlModifier,
                     view.visualItemRect(items[1]).center())
    assert view.currentItem() is items[1] and not items[1].isSelected()
    assert panel.current_project['id'] == pane.project_id == 2
    assert panel.project_title.text() == '文档乙系统' and '已选' not in panel.project_metadata.text()
    assert panel.confirm_delivery_button.isEnabled() and panel.project_actions_button.isVisible()
    wait(lambda: pane.worker is None and pane.table.rowCount() == 1)
    pane.table.selectRow(0)
    assert pane.team_action.isEnabled() and pane.archive_history_action.isEnabled(), (
        pane.current_document(), pane._preview_only, pane.table.currentRow())


def test_keyboard_focus_changes_preview_without_changing_batch_selection(panel):
    panel.resize(1100, 700)
    panel.show_project_queue()
    view, pane = panel.project_list, panel.document_pane
    view.setCurrentRow(0)
    view.selectAll()
    view.setFocus()
    QTest.keyClick(view, Qt.Key_Down, Qt.ControlModifier)
    assert len(view.selectedItems()) == 2
    assert view.currentRow() == 1
    assert pane.project_id == view.currentItem().data(Qt.UserRole)
    title = panel.project_title.text()
    panel.refresh()
    assert panel.project_title.text() == title
    assert pane.project_id == view.currentItem().data(Qt.UserRole)


def test_batch_preview_uses_only_selected_files_on_a_narrow_window(panel):
    panel.resize(660, 492)
    panel.show_project_queue()
    view, pane = panel.project_list, panel.document_pane
    QTest.qWait(60)
    choose(panel, 1)
    view.selectAll()
    assert panel.project_side.isVisible() and not panel.document_side.isVisible()
    assert panel.project_detail_button.isVisible()
    panel.project_detail_button.click()
    assert panel.document_side.isVisible() and panel.project_back_button.isVisible()
    wait(lambda: pane.worker is None and pane.table.rowCount() == 1)
    assert pane.project_id == 1 and pane.table.item(0, 0).text() == '甲说明.txt'
    assert not panel.project_actions_button.isVisible()
    panel.project_back_button.click()
    assert panel.project_side.isVisible() and len(view.selectedItems()) == 2


def test_document_columns_drag_and_keep_user_width_when_window_changes(panel, qapp):
    choose(panel, 1)
    pane = panel.document_pane
    wait(lambda: pane.worker is None and pane.table.rowCount() == 1)
    header = pane.table.horizontalHeader()
    assert all(header.sectionResizeMode(column) == QHeaderView.Interactive for column in range(3))
    before = pane.table.columnWidth(0)
    boundary = header.sectionViewportPosition(0) + before
    QTest.mousePress(header.viewport(), Qt.LeftButton, pos=QPoint(boundary - 2, header.height() // 2))
    QTest.mouseMove(header.viewport(), QPoint(boundary - 42, header.height() // 2))
    QTest.mouseRelease(header.viewport(), Qt.LeftButton, pos=QPoint(boundary - 42, header.height() // 2))
    qapp.processEvents()
    assert pane.table.columnWidth(0) < before - 20
    chosen = pane.table.columnWidth(0)
    panel.resize(1000, 700)
    qapp.processEvents()
    assert pane.table.columnWidth(0) == chosen
    pane.request_refresh()
    wait(lambda: pane.worker is None)
    assert pane.table.columnWidth(0) == chosen
    header.resizeSection(0, pane.table.viewport().width() + 100)
    qapp.processEvents()
    assert pane.table.horizontalScrollBar().maximum() > 0
    pane.table.horizontalScrollBar().setValue(pane.table.horizontalScrollBar().maximum())
    assert header.sectionViewportPosition(2) < pane.table.viewport().width()


def test_document_refresh_preserves_selection_and_handles_external_new_files(panel,qapp):
    assert hasattr(panel,'document_pane'), '缺少对应文档列表'
    choose(panel,1)
    pane=panel.document_pane
    wait(lambda: pane.table.rowCount()==1 and pane.worker is None)
    pane.table.selectRow(0)
    item=pane.table.item(0,0)
    resets=QSignalSpy(pane.table.model().modelReset)
    pane.request_refresh()
    wait(lambda: pane.worker is None)
    assert pane.table.item(0,0) is item and resets.count()==0
    (Path(panel.service.workspaces.get_workspace(1)['path'])/'新增.txt').write_text('直接在目录添加',encoding='utf-8')
    pane.request_refresh()
    wait(lambda: pane.table.rowCount()==2 and pane.worker is None)
    assert pane.current_document()['name']=='甲说明.txt'


def test_document_copy_uses_actual_selected_file_for_clipboard_and_menu(panel, qapp, monkeypatch):
    choose(panel, 1)
    pane = panel.document_pane
    wait(lambda: pane.table.rowCount() == 1 and pane.worker is None)
    path = Path(panel.service.workspaces.get_workspace(1)['path']) / '甲说明.txt'
    copied = []

    class Clipboard:
        def setMimeData(self, mime):
            copied.append(([url.toLocalFile() for url in mime.urls()], {
                name: bytes(mime.data(name)) for name in mime.formats()
            }))

    monkeypatch.setattr(QApplication, 'clipboard', lambda: Clipboard())
    pane.table.selectRow(0)
    pane.table.setFocus()
    QTest.keyClick(pane.table, Qt.Key_C, Qt.ControlModifier)
    action = next(action for action in pane.file_menu.actions() if action.text() == '复制文件')
    assert action.isEnabled()
    action.trigger()
    assert [list(map(Path, urls)) for urls, _ in copied] == [[path], [path]]
    assert all(formats['application/x-qt-windows-mime;value="Preferred DropEffect"']
               == b'\x01\x00\x00\x00' for _, formats in copied)
    assert path.read_text(encoding='utf-8') == '隔离文档'

    path.unlink()
    QTest.keyClick(pane.table, Qt.Key_C, Qt.ControlModifier)
    assert len(copied) == 2
    pane.table.clearSelection()
    assert not action.isEnabled()
    QTest.keyClick(pane.table, Qt.Key_C, Qt.ControlModifier)
    assert len(copied) == 2


def test_document_drag_from_selected_row_only_offers_copy(panel, monkeypatch):
    from workbox.desktop import project_documents_ui

    choose(panel, 2)
    pane = panel.document_pane
    wait(lambda: pane.table.rowCount() == 1 and pane.worker is None)
    path = Path(panel.service.workspaces.get_workspace(2)['path']) / '乙说明.docx'
    drags = []

    class Drag:
        def __init__(self, source):
            assert source is pane.table

        def setMimeData(self, mime):
            self.mime = mime

        def exec(self, actions, default_action):
            drags.append(([url.toLocalFile() for url in self.mime.urls()], actions, default_action))
            enter = QDragEnterEvent(QPoint(5, 5), Qt.CopyAction, self.mime, Qt.LeftButton, Qt.NoModifier)
            panel.dragEnterEvent(enter)
            assert not enter.isAccepted()
            drop = QDropEvent(QPointF(5, 5), Qt.CopyAction, self.mime, Qt.LeftButton, Qt.NoModifier)
            panel.dropEvent(drop)
            assert not drop.isAccepted()
            return Qt.CopyAction

    monkeypatch.setattr(project_documents_ui, 'QDrag', Drag)
    intake = QSignalSpy(panel.intake)
    pane.table.selectRow(0)
    assert pane.table.dragEnabled()
    for column in range(3):
        assert pane.table.item(0, column).flags() & Qt.ItemIsDragEnabled
    start = pane.table.visualItemRect(pane.table.item(0, 0)).center()
    QTest.mousePress(pane.table.viewport(), Qt.LeftButton, pos=start)
    QTest.mouseMove(pane.table.viewport(), start + QPoint(32, 0))
    QTest.mouseMove(pane.table.viewport(), start + QPoint(50, 0))
    QTest.mouseRelease(pane.table.viewport(), Qt.LeftButton, pos=start + QPoint(50, 0))
    assert len(drags) == 1
    urls, actions, default_action = drags[0]
    assert list(map(Path, urls)) == [path]
    assert actions == default_action == Qt.CopyAction
    assert intake.count() == 0
    assert path.exists() and pane.table.rowCount() == 1


def test_archive_directory_row_is_not_a_file_transfer_source(panel, tmp_path):
    choose(panel, 1)
    pane = panel.document_pane
    wait(lambda: pane.table.rowCount() == 1 and pane.worker is None)
    archive = tmp_path / 'archive'
    archive.mkdir()
    row = dict(pane.documents[0], id='archive:1', path=str(archive), name=archive.name,
               relative_path=archive.name, kind='材料归档', is_directory=True, size=None)
    pane.apply_result(dict(project_id=1, workspace_path=None, errors=[], documents=[row]))
    pane.table.selectRow(0)
    assert not pane.copy_action.isEnabled()
    assert not pane.table.item(0, 0).flags() & Qt.ItemIsDragEnabled
    assert pane.open_button.isEnabled()


def test_open_document_requires_explicit_action_and_never_runs_program_files(panel,monkeypatch):
    assert hasattr(panel,'document_pane'), '缺少文档打开入口'
    opened=[]
    monkeypatch.setattr(QDesktopServices,'openUrl',lambda url:opened.append(url.toLocalFile()) or True)
    choose(panel,1)
    pane=panel.document_pane
    wait(lambda:pane.table.rowCount()==1 and pane.worker is None)
    assert not opened
    pane.table.selectRow(0)
    pane.open_button.click()
    assert list(map(Path,opened))==[Path(panel.service.workspaces.get_workspace(1)['path'])/'甲说明.txt']
    executable=Path(panel.service.workspaces.get_workspace(1)['path'])/'工具.exe'
    executable.write_bytes(b'isolated file, never execute')
    pane.request_refresh()
    wait(lambda:pane.worker is None and pane.table.rowCount()==2)
    row=next(i for i in range(pane.table.rowCount()) if pane.table.item(i,0).text()=='工具.exe')
    pane.table.selectRow(row)
    located=[]
    pane.folder_requested.connect(located.append)
    pane.open_button.click()
    assert len(opened)==1 and located==[str(executable.parent)]


@pytest.mark.parametrize('batch_preview', [False, True])
def test_late_document_load_cannot_replace_the_new_project(panel, monkeypatch, batch_preview):
    from threading import Event
    from workbox.desktop import project_documents_ui
    original=project_documents_ui.list_project_documents
    entered,release=Event(),Event()
    def delayed(service,pid,**kwargs):
        if pid==1:
            entered.set()
            release.wait(3)
            return original(service,pid)
        return original(service,pid,**kwargs)
    monkeypatch.setattr(project_documents_ui,'list_project_documents',delayed)
    choose(panel,1)
    wait(entered.is_set)
    if batch_preview:
        view = panel.project_list
        second = next(view.item(row) for row in range(view.count())
                      if view.item(row).data(Qt.UserRole) == 2)
        QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.ControlModifier, view.visualItemRect(second).center())
        assert len(view.selectedItems()) == 2
    else:
        choose(panel, 2)
    release.set()
    pane=panel.document_pane
    wait(lambda:pane.worker is None and pane.table.rowCount()==1)
    assert pane.table.item(0,0).text()=='乙说明.docx'
    assert pane.project_id==2


def test_document_view_preserves_anchor_and_defers_changes_while_dragging(panel,qapp):
    root=Path(panel.service.workspaces.get_workspace(1)['path'])
    for index in range(60):
        (root/f'文件{index:03d}.txt').write_text('隔离资料',encoding='utf-8')
    choose(panel,1)
    pane=panel.document_pane
    wait(lambda:pane.worker is None and pane.table.rowCount()==61)
    pane.table.verticalScrollBar().setValue(450)
    qapp.processEvents()
    row=pane.table.rowAt(0)
    name=pane.table.item(row,0).text()
    top=pane.table.rowViewportPosition(row)
    pane.table.verticalScrollBar().setSliderDown(True)
    (root/'000新增.txt').write_text('新增',encoding='utf-8')
    pane.request_refresh()
    wait(lambda:pane.worker is None)
    assert pane.table.rowCount()==61
    pane.table.verticalScrollBar().setSliderDown(False)
    qapp.processEvents()
    assert pane.table.rowCount()==62
    row=pane.table.rowAt(0)
    assert pane.table.item(row,0).text()==name and pane.table.rowViewportPosition(row)==top


def test_reverted_update_during_drag_does_not_apply_obsolete_files(panel,qapp):
    choose(panel,1)
    pane=panel.document_pane
    wait(lambda:pane.worker is None and pane.table.rowCount()==1)
    pane.table.verticalScrollBar().setSliderDown(True)
    temporary=Path(panel.service.workspaces.get_workspace(1)['path'])/'短暂文件.txt'
    temporary.write_text('随后删除',encoding='utf-8')
    pane.request_refresh()
    wait(lambda:pane.worker is None)
    temporary.unlink()
    pane.request_refresh()
    wait(lambda:pane.worker is None)
    pane.table.verticalScrollBar().setSliderDown(False)
    qapp.processEvents()
    assert pane.table.rowCount()==1 and pane.table.item(0,0).text()=='甲说明.txt'


def test_document_refresh_consumes_stale_queued_layout_anchor(panel, qapp):
    """An earlier width-layout callback cannot undo a later explicit scroll/refresh."""
    from workbox.storage.project_documents import list_project_documents
    root = Path(panel.service.workspaces.get_workspace(1)['path'])
    for index in range(60):
        (root / f'文件{index:03d}.txt').write_text('隔离资料', encoding='utf-8')
    choose(panel, 1)
    pane = panel.document_pane
    wait(lambda: pane.worker is None and pane.table.rowCount() == 61)
    pane.timer.stop()
    pane._row_layout_timer.stop()
    pane._row_layout_anchor = None
    pane._resize_rows_preserving_anchor()
    bar = pane.table.verticalScrollBar()
    bar.setValue(0)
    pane._queue_row_layout()
    # Deliver the pending timer explicitly after the refresh, without timing luck.
    pane._row_layout_timer.stop()
    stale_anchor = pane._row_layout_anchor
    bar.setValue(450)
    expected = pane._document_anchor()
    assert expected[0] != stale_anchor[0]
    bar.setSliderDown(True)
    (root / '000新增.txt').write_text('新增', encoding='utf-8')
    pane.apply_result(list_project_documents(panel.service, 1))
    assert pane.table.rowCount() == 61 and pane._deferred is not None
    bar.setSliderDown(False)
    assert pane.table.rowCount() == 62
    pane._row_layout_timer.stop()
    pane._resize_rows_preserving_anchor()
    assert pane._document_anchor() == expected
    qapp.processEvents()
    assert pane._document_anchor() == expected


def test_successful_same_content_refresh_clears_old_read_error(panel,monkeypatch):
    from workbox.desktop import project_documents_ui
    choose(panel,1)
    pane=panel.document_pane
    wait(lambda:pane.worker is None and pane.table.rowCount()==1)
    pane.table.selectRow(0)
    original=project_documents_ui.list_project_documents
    def failed(*args,**kwargs):
        raise OSError('隔离读取失败')
    monkeypatch.setattr(project_documents_ui,'list_project_documents',failed)
    pane.request_refresh()
    wait(lambda:pane.worker is None)
    assert '失败' in pane.hint.text() and '上次' in pane.hint.text()
    assert '上次' in pane.file_count.text() and '1 项' in pane.file_count.text()
    assert '上次' in pane.table.accessibleDescription()
    assert pane.table.isVisible() and pane.table.currentItem().text() == '甲说明.txt'
    assert pane.refresh_button.isVisible()
    monkeypatch.setattr(project_documents_ui,'list_project_documents',original)
    pane.refresh_button.click()
    wait(lambda:pane.worker is None)
    assert '失败' not in pane.hint.text() and '1 项' in pane.hint.text()
    assert pane.file_count.text() == '1 项' and not pane.refresh_button.isVisible()
    assert pane.table.currentItem().text() == '甲说明.txt'


@pytest.mark.parametrize('readable', [False, True])
def test_incomplete_document_read_marks_count_and_can_retry(panel, monkeypatch, readable):
    from workbox.desktop import project_documents_ui

    original = project_documents_ui.list_project_documents

    def incomplete(*args, **kwargs):
        result = original(*args, **kwargs)
        result['errors'].append('匿名子目录暂不可读')
        if not readable:
            result['documents'].clear()
        return result

    monkeypatch.setattr(project_documents_ui, 'list_project_documents', incomplete)
    choose(panel, 1)
    pane = panel.document_pane
    wait(lambda: pane.worker is None and pane.refresh_button.isVisible())
    assert '不完整' in pane.hint.text()
    assert pane.file_count.isVisible()
    assert pane.file_count.text() == ('已读取 1 项' if readable else '总数未知')
    assert '总数未知' in pane.table.accessibleDescription()
    assert pane.table.isVisible() is readable
    if readable:
        assert pane.table.item(0, 0).text() == '甲说明.txt'
    monkeypatch.setattr(project_documents_ui, 'list_project_documents', original)
    pane.refresh_button.click()
    wait(lambda: pane.worker is None and not pane.refresh_button.isVisible())
    assert pane.file_count.text() == '1 项'
    assert pane.table.isVisible() and pane.table.item(0, 0).text() == '甲说明.txt'


def test_hidden_reader_finished_callback_cannot_consume_new_reader(panel,monkeypatch,qapp):
    from threading import Event
    from workbox.desktop import project_documents_ui
    choose(panel,1)
    pane=panel.document_pane
    old=pane.worker
    assert old.wait(5000)
    pane.hide()
    entered,release=Event(),Event()
    original=project_documents_ui.list_project_documents
    def delayed(*args,**kwargs):
        entered.set()
        release.wait(2)
        return original(*args,**kwargs)
    monkeypatch.setattr(project_documents_ui,'list_project_documents',delayed)
    pane.show()
    current=pane.worker
    wait(entered.is_set)
    qapp.processEvents()
    assert pane.worker is current and current.isRunning()
    release.set()
    wait(lambda:pane.worker is None)
    assert pane.table.rowCount()==1 and pane.table.item(0,0).text()=='甲说明.txt'


def test_failed_document_open_is_visible_even_in_compact_layout(panel,monkeypatch,qapp):
    panel.resize(660,492)
    choose(panel,1)
    panel.show_project_detail()
    pane=panel.document_pane
    wait(lambda:pane.worker is None and pane.table.rowCount()==1)
    pane.table.selectRow(0)
    monkeypatch.setattr(QDesktopServices,'openUrl',lambda _:False)
    messages=[]
    monkeypatch.setattr(QMessageBox,'warning',lambda parent,title,message:messages.append((title,message)))
    pane.open_button.click()
    assert messages and '无法打开文件' in messages[0][1]
