"""窄侧栏内的工作量必须可读，不能依赖跑到按钮外的浮动标签。"""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from compact_ui import CompactPanel
from desktop_service import DesktopService

@pytest.fixture(scope='module')
def app():
    value=QApplication.instance() or QApplication([])
    value.setQuitOnLastWindowClosed(False)
    return value

@pytest.mark.parametrize('count,caption',[(0,'0'),(13,'13'),(107,'99+')])
def test_compact_workload_number_belongs_to_its_button(app,tmp_path,monkeypatch,count,caption):
    service=DesktopService(tmp_path/'workbox.db')
    monkeypatch.setattr(service.db,'today_delivery_summary',lambda:{'day':'2026-10-29','project_count':count,'event_count':count})
    panel=CompactPanel(service)
    panel.resize(660,480)
    panel.show();app.processEvents()
    try:
        assert panel.today_button.text()==caption
        assert str(count) in panel.today_button.toolTip()
        assert not hasattr(panel,'today_count_badge'), '工作量数字不应使用越出按钮的浮动徽章'
        for control,_ in panel.nav_controls:
            rect=control.rect();rect.moveTopLeft(control.mapTo(panel.navigation,rect.topLeft()))
            assert panel.navigation.rect().contains(rect)
        panel.resize(1200,780);app.processEvents()
        assert panel.today_button.text()==f'今日工作量 {count}'
        panel.resize(660,480);app.processEvents()
        assert panel.today_button.text()==caption
        assert panel.width()==660
    finally:
        panel.document_pane.shutdown();panel.hide()
