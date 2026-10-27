"""目录与共享收件走真实后台队列，界面不读取远程路径。"""

import threading
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QApplication

from desktop_controller import FloatingController
from desktop_service import DesktopService
from floating import decode_mime


@pytest.fixture
def controller(tmp_path):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    value = FloatingController(DesktopService(tmp_path / 'data/workbox.db'), tray_enabled=False)
    assert value.start()
    value.monitor_timer.stop()
    yield value
    value.shutdown()
    app.processEvents()


def wait(value):
    end = time.monotonic() + 12
    while value.busy and time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(.01)
    assert not value.busy
    QApplication.processEvents()


def test_decoder_accepts_folder_without_filesystem_access(tmp_path, monkeypatch):
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(tmp_path))])
    def no_io(*args, **kwargs):
        raise AssertionError('隔离测试项目5系统')
    monkeypatch.setattr(Path, 'is_dir', no_io)
    assert decode_mime(mime) == {'kind': 'files', 'paths': [str(tmp_path)]}


def test_folder_drop_copies_nested_document_and_feedback_targets_project(controller, tmp_path):
    root = tmp_path / '隔离检测系统'
    source = root / '材料' / '说明.txt'
    source.parent.mkdir(parents=True)
    source.write_bytes(b'folder fixture')
    controller.service.receive_text('1. 隔离检测系统')
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(root))])
    event = QDropEvent(QPointF(38, 38), Qt.CopyAction | Qt.MoveAction, mime, Qt.LeftButton, Qt.NoModifier)
    controller.orb.dropEvent(event)
    assert event.isAccepted() and event.dropAction() == Qt.CopyAction
    wait(controller)
    project = controller.service.get_project(1)
    assert project['status'] == '待交付'
    assert (Path(project['workspace']['path']) / '材料' / '说明.txt').read_bytes() == b'folder fixture'
    assert source.read_bytes() == b'folder fixture'
    assert controller.bubble.navigation_target == ('projects', (1,))
    assert '文件夹' in controller.panel.notice.text() and '1' in controller.panel.notice.text()


def test_file_routing_never_blocks_gui_thread(controller, tmp_path, monkeypatch):
    source = tmp_path / '隔离检测系统.txt'
    source.write_bytes(b'ordinary fixture')
    main_thread = threading.get_ident()
    original = controller.service.file_routing
    observed = []
    def checked(path):
        observed.append(threading.get_ident())
        assert threading.get_ident() != main_thread, '共享路径校验不能卡住界面'
        return original(path)
    monkeypatch.setattr(controller.service, 'file_routing', checked)
    controller.receive({'kind': 'files', 'paths': [str(source)]})
    wait(controller)
    assert observed and main_thread not in observed
    assert controller.service.get_project(1)['status'] == '待交付'


def test_ambiguous_file_still_requires_choice_on_gui_thread(controller, tmp_path, monkeypatch):
    controller.service.receive_text('1. 隔离同名系统')
    controller.service.capture_message('1. 隔离同名系统', '另一个批次')
    source = tmp_path / '隔离同名系统.txt'
    source.write_bytes(b'ambiguous fixture')
    chosen = []
    main_thread = threading.get_ident()
    def choose(name):
        assert threading.get_ident() == main_thread
        chosen.append(name)
        return 2
    monkeypatch.setattr(controller.panel, 'choose_file_project', choose)
    controller.receive({'kind': 'files', 'paths': [str(source)]})
    wait(controller)
    assert chosen == [source.name]
    with controller.service.db.connect() as connection:
        rows = connection.execute('SELECT project_id FROM work_files').fetchall()
    assert [row[0] for row in rows] == [2]


def test_folder_manual_choice_keeps_subdirectories_and_groups_materials(controller, tmp_path, monkeypatch):
    controller.service.receive_text('1. 隔离同名系统')
    controller.service.capture_message('1. 隔离同名系统', '另一个批次')
    root = tmp_path / '隔离同名系统'
    for name in ('说明.txt', '附表.txt'):
        source = root / '材料' / name
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(name.encode('utf-8'))
    choices = []
    monkeypatch.setattr(controller.panel, 'choose_file_project', lambda title: choices.append(title) or 2)
    controller.receive({'kind': 'files', 'paths': [str(root)]})
    wait(controller)
    assert len(choices) == 1
    target = Path(controller.service.workspaces.get_workspace(2)['path'])
    assert (target / '材料' / '说明.txt').read_bytes() == '说明.txt'.encode('utf-8')
    assert (target / '材料' / '附表.txt').read_bytes() == '附表.txt'.encode('utf-8')
    assert controller.service.get_project(1)['status'] == '待开发'
    assert controller.service.get_project(2)['status'] == '待交付'
