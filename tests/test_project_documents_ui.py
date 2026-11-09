import time
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QHeaderView, QMessageBox

from compact_ui import CompactPanel
from desktop_service import DesktopService


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
    import project_documents_ui
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
    import project_documents_ui
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


def test_late_document_load_cannot_replace_the_new_project(panel,monkeypatch):
    from threading import Event
    import project_documents_ui
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
    choose(panel,2)
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
    from project_documents import list_project_documents
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
    import project_documents_ui
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
    import project_documents_ui

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
    import project_documents_ui
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
