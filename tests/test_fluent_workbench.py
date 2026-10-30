"""正式工作台使用真实库组件，视觉筛选与实际数据范围保持一致。"""
import pytest
from PySide6.QtWidgets import QApplication
from qfluentwidgets import CardWidget, ListWidget, SegmentedWidget, TableWidget
from compact_ui import CompactPanel, ReviewDialog
from desktop_service import DesktopService
from theme import install_theme

@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])

def test_workbench_uses_library_views_cards_and_status_segment(app, tmp_path):
    service=DesktopService(tmp_path/'workbox.db')
    install_theme(service)
    panel=CompactPanel(service)
    try:
        assert isinstance(panel.project_list,ListWidget)
        assert isinstance(panel.zip_list,ListWidget) and isinstance(panel.receipt_list,ListWidget)
        assert isinstance(panel.document_pane.table,TableWidget)
        assert isinstance(panel.document_side,CardWidget)
        assert isinstance(panel.status_segments,SegmentedWidget)
        assert panel.status_segments.currentRouteKey()=='all'
        panel.status_buttons['开发中'].click()
        assert panel.status_segments.currentRouteKey()=='开发中'
        assert panel.status_buttons['开发中'].isChecked()
    finally:
        panel.document_pane.shutdown();panel.hide()

def test_open_intake_project_resets_visible_segment_with_filter(app, tmp_path):
    service=DesktopService(tmp_path/'workbox.db')
    result=service.db.create_capture('独立导航验证','资料',[('工作台验证系统',False)])
    panel=CompactPanel(service)
    try:
        panel.status_buttons['已交付'].click()
        assert panel.project_list.count()==0
        assert panel.focus_projects(result['project_ids'])
        assert panel.status_buttons[None].isChecked()
        assert panel.status_segments.currentRouteKey()=='all'
        assert panel.project_list.count()==1
    finally:
        panel.document_pane.shutdown();panel.hide()

def test_fluent_list_selection_paint_tracks_model_rebuild(app, tmp_path):
    service=DesktopService(tmp_path/'workbox.db')
    service.db.create_capture('列表状态验证','资料',[('待开发项目系统',False),('开发中项目系统',False)])
    pid=next(p['id'] for p in service.list_projects() if p['name']=='开发中项目系统')
    service.set_status(pid,'开发中')
    panel=CompactPanel(service)
    try:
        panel.project_list.setCurrentRow(0)
        assert panel.project_list.delegate.selectedRows
        panel.status_buttons['待开发'].click()
        assert panel.project_list.count()==1
        expected={index.row() for index in panel.project_list.selectedIndexes()}
        assert panel.project_list.delegate.selectedRows==expected
    finally:
        panel.document_pane.shutdown();panel.hide()


def test_review_row_reflows_after_window_width_changes(app, tmp_path):
    from PySide6.QtTest import QTest
    service = DesktopService(tmp_path/'workbox.db')
    dialog = ReviewDialog(service, {'id': 1, 'reason': '需要核对', 'raw_text': '独立界面验证',
                                   'candidates': [{'name': '跨部门项目文档收录整理与版本交付协同管理系统' * 3,
                                                   'urgent': False}]})
    dialog.resize(980, 520)
    dialog.show()
    QTest.qWait(150)
    wide_height = dialog.table.rowHeight(0)
    dialog.resize(540, 520)
    QTest.qWait(150)
    narrow_height = dialog.table.rowHeight(0)
    assert narrow_height > wide_height
    dialog.resize(980, 520)
    QTest.qWait(150)
    assert dialog.table.rowHeight(0) == wide_height
