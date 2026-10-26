import time
from pathlib import Path

import pytest
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication, QMessageBox

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


def test_projects_are_left_and_selected_documents_are_right(panel,qapp):
    assert hasattr(panel,'project_splitter'), '缺少项目与文档左右分栏'
    choose(panel,2)
    pane=panel.document_pane
    wait(lambda: pane.table.rowCount()==1 and pane.worker is None)
    assert pane.table.item(0,0).text()=='乙说明.docx'
    assert panel.project_splitter.orientation()==Qt.Horizontal
    panel.show_notice('文件已经收进项目，原件保留。')
    for width,height in ((840,670),(660,492)):
        panel.resize(width,height)
        qapp.processEvents()
        left=panel.project_list.mapTo(panel,panel.project_list.rect().topRight())
        right=pane.table.mapTo(panel,pane.table.rect().topLeft())
        assert left.x()<right.x()
        assert panel.width()==width and panel.height()==height and pane.table.viewport().height()>=40
        assert panel.rect().contains(pane.open_button.mapTo(panel,pane.open_button.rect().bottomRight()))
        assert pane.table.geometry().bottom() < pane.open_button.geometry().top(), '文件表不能被底部按钮遮挡'


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


def test_successful_same_content_refresh_clears_old_read_error(panel,monkeypatch):
    import project_documents_ui
    choose(panel,1)
    pane=panel.document_pane
    wait(lambda:pane.worker is None and pane.table.rowCount()==1)
    original=project_documents_ui.list_project_documents
    def failed(*args,**kwargs):
        raise OSError('隔离读取失败')
    monkeypatch.setattr(project_documents_ui,'list_project_documents',failed)
    pane.request_refresh()
    wait(lambda:pane.worker is None)
    assert '失败' in pane.hint.text()
    monkeypatch.setattr(project_documents_ui,'list_project_documents',original)
    pane.request_refresh()
    wait(lambda:pane.worker is None)
    assert '失败' not in pane.hint.text() and '1 项' in pane.hint.text()


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
    pane=panel.document_pane
    wait(lambda:pane.worker is None and pane.table.rowCount()==1)
    pane.table.selectRow(0)
    monkeypatch.setattr(QDesktopServices,'openUrl',lambda _:False)
    messages=[]
    monkeypatch.setattr(QMessageBox,'warning',lambda parent,title,message:messages.append((title,message)))
    pane.open_button.click()
    assert messages and '无法打开文件' in messages[0][1]
