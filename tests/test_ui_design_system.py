"""Presentation contracts; only anonymous projects, no network or installation."""
import time

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QHeaderView


@pytest.fixture(scope='module')
def qapp():
    app=QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


@pytest.fixture
def service(tmp_path):
    from desktop_service import DesktopService
    s=DesktopService(tmp_path/'data/workbox.db')
    s.receive_text('1. 匿名检查系统')
    return s


def wait(done):
    end=time.monotonic()+5
    while time.monotonic()<end:
        QApplication.processEvents()
        if done():return
        time.sleep(.01)
    assert done()


def test_shared_design_tokens_and_semantic_styles(qapp):
    from theme import tokens, stylesheet
    for mode in ('light','dark'):
        c=tokens(mode)
        assert {'info_bg','success_bg','warning_bg','error_bg','subtle_surface'} <= set(c)
        qss=stylesheet(mode)
        for marker in ('statusBanner','metricStrip','dialogHeader','actionBar'):
            assert marker in qss


def test_status_banner_has_explicit_text_tone_and_no_rich_text(qapp):
    import ui_components as ui
    assert hasattr(ui,'StatusBanner'), 'missing shared status component'
    w=ui.StatusBanner('<b>匿名提示</b>')
    try:
        assert w.textFormat()==Qt.PlainText
        w.setTone('warning')
        assert w.property('tone')=='warning' and w.text()=='<b>匿名提示</b>'
        with pytest.raises(ValueError):w.setTone('not-a-tone')
        assert w.property('tone')=='warning'
    finally:w.deleteLater()


def test_shared_button_accessible_name_tracks_dynamic_caption(qapp):
    from PySide6.QtGui import QAccessible
    from ui_components import button
    b=button('检查更新')
    try:
        b.setText('正在检查')
        assert QAccessible.queryAccessibleInterface(b).text(QAccessible.Name)=='正在检查'
    finally:b.deleteLater()


def test_danger_button_keeps_semantic_color_with_local_fluent_styles(qapp):
    from PySide6.QtGui import QPalette
    from PySide6.QtWidgets import QDialog, QVBoxLayout
    from ui_components import button
    from theme import apply_to, set_theme, tokens
    w=QDialog();apply_to(w);layout=QVBoxLayout(w)
    b=button('移除所选','danger');layout.addWidget(b);w.show()
    try:
        for mode in ('light','dark'):
            set_theme(mode,persist=False)
            for _ in range(4):QApplication.processEvents()
            assert b.palette().color(QPalette.ButtonText).name()==tokens(mode)['error'].lower()
        b.setEnabled(False)
        QApplication.processEvents()
        assert not b.isEnabled() and b.text()=='移除所选'
    finally:w.close();w.deleteLater();set_theme('light',persist=False)


def test_contextual_material_menu_is_reachable_and_safe(qapp,service,monkeypatch):
    from compact_ui import CompactPanel
    p=CompactPanel(service)
    try:
        p.show();QApplication.processEvents()
        p.project_list.setCurrentRow(0);p.select_project()
        assert p.material_check_button.isHidden()
        assert p.project_actions_button.isVisible() and p.project_actions_button.isEnabled()
        calls=[]
        monkeypatch.setattr(p,'open_material_check',calls.append)
        menu=p.project_context_menu(1)
        material=next(action for action in menu.actions() if action.text()=='材料检查…')
        assert material.isEnabled()
        material.trigger()
        assert calls==[1]
        p.set_busy(True)
        menu=p.project_context_menu(1)
        assert not next(action for action in menu.actions() if action.text()=='材料检查…').isEnabled()
    finally:
        p.document_pane.shutdown();p.hide();p.deleteLater()


def test_material_missing_filter_summary_and_invalidation(qapp,service,monkeypatch):
    import material_check_ui as ui
    from material_matching import Requirement
    d=ui.MaterialCheckDialog(service,1)
    before=service.get_project(1)
    data=dict(project_id=1,workspace_path='registered',errors=[],documents=[dict(id='one',name='说明书.docx',relative_path='说明书.docx',kind='工作文件',exists=True,is_directory=False)])
    monkeypatch.setattr(ui,'list_project_documents',lambda *a,**k:data)
    try:
        assert hasattr(d,'missing_only'), 'missing actionable results filter'
        assert hasattr(d,'copy_summary_button'), 'missing explicit copy summary'
        d.set_rules([Requirement('a','说明书','word',1,'说明书'),Requirement('b','代码','word',1,'代码')])
        d.refresh();wait(lambda:d.worker is None)
        assert d.result_table.rowCount()==2 and not d.report.complete
        d.missing_only.setChecked(True)
        assert d.result_table.rowCount()==1 and d.result_table.item(0,0).text()=='代码'
        assert len(d.report.rows)==2, 'filter must not mutate the report'
        text=d.summary_text()
        assert '尚缺' in text and '代码' in text and '内容' in text
        assert d.copy_summary_button.isEnabled()
        d.rule_table.item(0,3).setText('新关键词')
        assert d.report is None and not d.copy_summary_button.isEnabled()
        assert d.summary_text()==''
        assert service.get_project(1)==before
        assert service.get_setting('material_check_v1:1') is None
    finally:d.shutdown();d.deleteLater()


def test_workbench_wide_to_compact_resize_keeps_native_minimum(qapp,service):
    from compact_ui import CompactPanel
    p=CompactPanel(service)
    try:
        p.show();p.project_list.setCurrentRow(0)
        p.show_notice('匿名布局验证，不修改任何用户文件。')
        for width,height in ((1160,780),(840,670),(660,492),(1160,780),(660,492)):
            p.resize(width,height)
            for _ in range(6):QApplication.processEvents()
            assert (p.width(),p.height())==(width,height)
            assert p.document_pane.table.viewport().height()>=40
            assert p.material_check_button.isHidden()
            for control in (p.project_actions_button,p.background_button,p.exit_button,p.document_pane.file_menu_button):
                assert p.rect().contains(control.mapTo(p,control.rect().bottomRight()))
                assert control.isVisible()
    finally:
        p.document_pane.shutdown();p.hide();p.deleteLater()


def test_material_tables_are_resizable_and_show_gridless_rows(qapp,service):
    from material_check_ui import MaterialCheckDialog
    d=MaterialCheckDialog(service,1)
    try:
        for t in (d.rule_table,d.result_table,d.ambiguity_table):
            assert not t.showGrid()
            assert t.horizontalScrollBarPolicy()==Qt.ScrollBarAsNeeded
            assert t.horizontalHeader().sectionResizeMode(0)==QHeaderView.Interactive
    finally:d.shutdown();d.deleteLater()


def test_material_first_show_respects_default_size(qapp,service):
    from material_check_ui import MaterialCheckDialog
    d=MaterialCheckDialog(service,1)
    try:
        d.show()
        for _ in range(8):QApplication.processEvents()
        assert (d.width(),d.height())==(900,680)
        assert not d.ambiguity_table.isVisible()
        assert d.rect().contains(d.close_button.mapTo(d,d.close_button.rect().bottomRight()))
    finally:d.shutdown();d.deleteLater()


def test_update_dialog_is_inert_until_requested(qapp,tmp_path,monkeypatch):
    import update_ui
    network=[]
    monkeypatch.setattr(update_ui,'check_for_update',lambda **k:network.append(True))
    d=update_ui.UpdateDialog(cache_root=tmp_path/'cache')
    try:
        assert network==[] and not (tmp_path/'cache').exists()
        assert d.status_label.property('tone')=='neutral'
        assert not d.update_button.isEnabled()
    finally:d.shutdown();d.deleteLater()


def test_dialog_header_icon_follows_live_theme_without_losing_context(qapp):
    import theme
    import ui_components as ui
    from qfluentwidgets import FluentIcon
    theme.set_theme('light',persist=False)
    w=ui.DialogHeader('匿名窗口','<b>很长的匿名项目名称</b>',icon=FluentIcon.DOCUMENT)
    try:
        before=w.icon_label.pixmap().toImage()
        theme.set_theme('dark',persist=False)
        QApplication.processEvents()
        assert w.icon_label.pixmap().toImage()!=before, 'header icon kept its light theme pixels'
        assert w.subtitle.toolTip()=='<b>很长的匿名项目名称</b>'
        assert w.subtitle.textFormat()==Qt.PlainText
    finally:w.deleteLater();theme.set_theme('light',persist=False)


def test_incomplete_material_summary_is_not_a_success_and_copy_is_explicit(qapp,service,monkeypatch):
    import material_check_ui as ui
    from material_matching import Requirement
    d=ui.MaterialCheckDialog(service,1)
    copied=[]
    class Clipboard:
        def setText(self,text):copied.append(text)
    monkeypatch.setattr(ui.QApplication,'clipboard',lambda:Clipboard())
    data=dict(project_id=1,workspace_path='registered',errors=['隔离模拟：只读到部分目录'],
              documents=[dict(id='a',name='说明书.docx',kind='工作文件',exists=True,is_directory=False)])
    monkeypatch.setattr(ui,'list_project_documents',lambda *a,**k:data)
    try:
        d.set_rules([Requirement('a','说明书','word',1,'说明书')])
        d.refresh();wait(lambda:d.worker is None)
        assert not d.report.complete and d.status_label.property('tone')=='warning'
        assert '不能确认收齐' in d.summary_text() and copied==[]
        d.copy_summary_button.click()
        assert copied==[d.summary_text()]
        d._invalidate();d.copy_summary()
        assert len(copied)==1 and not d.copy_summary_button.isEnabled()
    finally:d.shutdown();d.deleteLater()
