"""只把明确收件成功标为短提示，错误、待核对和等待归档持续可见。"""
import time
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from desktop_controller import FloatingController
from desktop_service import DesktopService


@pytest.fixture
def controller(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    value = FloatingController(DesktopService(tmp_path / 'data/workbox.db'), tray_enabled=False)
    assert value.start()
    value.monitor_timer.stop()
    value.show_panel()
    kinds = []
    original = value.panel.show_notice
    def observe(message, **kwargs):
        kinds.append(kwargs.get('kind', 'persistent'))
        original(message, **kwargs)
    monkeypatch.setattr(value.panel, 'show_notice', observe)
    value.observed_notice_kinds = kinds
    yield value
    value.shutdown()
    app.processEvents()


def finish(controller):
    deadline = time.monotonic() + 8
    while controller.busy and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(.01)
    assert not controller.busy
    QApplication.processEvents()


def test_successful_text_and_file_use_scoped_short_notice(controller, tmp_path):
    controller.receive({'kind':'text', 'text':'1. 文件归集系统'})
    finish(controller)
    assert controller.observed_notice_kinds[-1] == 'intake-success'
    path = tmp_path / '文件归集系统.txt'
    path.write_text('anonymous', encoding='utf-8')
    controller.receive({'kind':'files', 'paths':[str(path)]})
    finish(controller)
    assert controller.observed_notice_kinds[-1] == 'intake-success'
    assert controller.service.get_project(1)['status'] == '待交付'
    controller.panel.tabs.setCurrentIndex(2)
    QApplication.processEvents()
    assert not controller.panel.notice.isVisible()


@pytest.mark.parametrize('result', [
    {'state':'needs-review','project_ids':[], 'receipt_id':1, 'reason':'需要核对'},
    {'state':'committed','project_ids':[1], 'workspace_error':'目录创建失败'},
    {'watching':True, 'project_id':1, 'source_path':'anonymous.zip'},
])
def test_attention_results_remain_persistent(controller, result):
    controller.service.receive_text('1. 文件归集系统')
    controller.caption = '收件'
    controller.silent_current = False
    controller.worker = SimpleNamespace(result=result,error='',cancelled=False,deleteLater=lambda:None)
    controller._completed()
    assert controller.observed_notice_kinds[-1] == 'persistent'
    controller.panel.tabs.setCurrentIndex(2)
    QApplication.processEvents()
    assert controller.panel.notice.isVisible()


def test_failure_after_success_is_not_treated_as_transient(controller):
    controller.receive({'kind':'text','text':'1. 文件归集系统'})
    finish(controller)
    def fail(**_):
        raise OSError('anonymous read failure')
    controller.enqueue('收件失败', fail)
    finish(controller)
    assert controller.observed_notice_kinds[-1] == 'persistent'
    assert 'anonymous read failure' in controller.panel.notice.toolTip()


def test_real_text_directory_failure_is_visible_on_first_line(controller, monkeypatch):
    def unavailable(*args, **kwargs):
        raise OSError('anonymous workspace unavailable')
    monkeypatch.setattr(controller.service.workspaces, 'ensure_projects', unavailable)
    controller.receive({'kind':'text','text':'1. 新建目录检查系统'})
    finish(controller)
    assert len(controller.service.list_projects()) == 1
    assert controller.observed_notice_kinds[-1] == 'persistent'
    assert '工作目录创建失败' in controller.panel.notice.text()
    assert '及工作目录' not in controller.panel.notice.text()
    assert 'anonymous workspace unavailable' in controller.panel.notice.toolTip()
    controller.panel.tabs.setCurrentIndex(2)
    QApplication.processEvents()
    assert controller.panel.notice.isVisible()
