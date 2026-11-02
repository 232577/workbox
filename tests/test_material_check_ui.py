"""Real Qt and isolated SQLite checks; never open production data."""
import importlib
import json
import time
from pathlib import Path
from threading import Event

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from desktop_service import DesktopService
from material_matching import Requirement


@pytest.fixture(scope='module')
def qapp():
    app=QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


@pytest.fixture
def service(tmp_path):
    value=DesktopService(tmp_path/'data'/'workbox.db')
    value.receive_text('1. 示例系统\n2. 另一系统')
    return value


def load(name):
    assert (Path(__file__).resolve().parents[1]/(name+'.py')).is_file(), 'missing '+name
    return importlib.import_module(name)


def wait(predicate):
    until=time.monotonic()+10
    while time.monotonic()<until:
        QApplication.processEvents()
        if predicate():return
        time.sleep(.01)
    assert predicate(), 'Qt condition did not become true'


def snapshots(service):
    with service.db.connect() as db:
        return '\n'.join(db.iterdump())


def make_dialog(service):
    return load('material_check_ui').MaterialCheckDialog(service,1)


def test_open_edit_and_cancel_do_not_save_or_change_business_data(qapp,service):
    before=snapshots(service)
    d=make_dialog(service)
    try:
        d.set_rules([Requirement('a','说明书','word',2,'说明书')])
        assert d.worker is None and d.report is None
        d.reject()
    finally:d.shutdown();d.deleteLater()
    assert snapshots(service)==before


def test_saved_rules_are_project_scoped_and_restored(qapp,service):
    store=load('material_check_store')
    rules=(Requirement('a','手册','word',2,'软件名称 手册'),)
    assert store.load_rules(service,1) is None
    d=make_dialog(service)
    try:
        d.set_rules(rules);d.save_rules()
        assert store.load_rules(service,1)==rules
        assert store.load_rules(service,2) is None
        assert service.get_project(1)['status']=='待开发'
    finally:d.shutdown();d.deleteLater()
    other=make_dialog(service)
    try:assert other.rules()==rules
    finally:other.shutdown();other.deleteLater()


def test_store_rejects_invalid_project_and_bad_rule_without_changes(service):
    store=load('material_check_store');before=snapshots(service)
    for pid,rules in [(999,[]),(True,[]),(1,[Requirement('a','a','word',0,'')])]:
        with pytest.raises((ValueError,KeyError)):store.save_rules(service,pid,rules)
    assert snapshots(service)==before


def test_corrupt_saved_rules_are_visible_and_not_rewritten(qapp,service):
    store=load('material_check_store')
    with service.db.connect() as db:
        db.execute('INSERT INTO desktop_settings VALUES (?,?)',(store.setting_key(1),'not json'))
    before=snapshots(service);d=make_dialog(service)
    try:
        assert d.report is None and '规则' in d.status_label.text()
        assert d.rules()==()
    finally:d.shutdown();d.deleteLater()
    assert snapshots(service)==before


def test_refresh_uses_current_work_files_and_leaves_state_and_bytes_unchanged(qapp,service):
    root=Path(service.workspaces.get_workspace(1)['path'])
    for name in ('示例系统_说明书.doc','示例系统_代码.docx'):(root/name).write_text('anonymous',encoding='utf-8')
    before=snapshots(service);files={p.name:p.read_bytes() for p in root.iterdir() if p.is_file()}
    d=make_dialog(service)
    try:
        d.set_rules([Requirement('a','说明书','word',1,'说明书'),Requirement('b','代码','word',1,'代码')])
        d.refresh();wait(lambda:d.worker is None)
        assert d.report.complete and '收齐' in d.status_label.text()
        assert d.result_table.rowCount()==2
    finally:d.shutdown();d.deleteLater()
    assert snapshots(service)==before
    assert {p.name:p.read_bytes() for p in root.iterdir() if p.is_file()}==files


def test_edit_invalidates_previous_complete_result(qapp,service):
    root=Path(service.workspaces.get_workspace(1)['path']);(root/'说明书.docx').write_text('fixture')
    d=make_dialog(service)
    try:
        d.set_rules([Requirement('a','手册','word',1,'说明书')]);d.refresh();wait(lambda:d.worker is None)
        assert d.report.complete
        d.rule_table.item(0,3).setText('不存在')
        assert d.report is None and '重新检查' in d.status_label.text()
    finally:d.shutdown();d.deleteLater()


def test_ambiguity_requires_choice_and_is_never_double_counted(qapp,service):
    root=Path(service.workspaces.get_workspace(1)['path']);(root/'说明书.docx').write_text('fixture')
    d=make_dialog(service)
    try:
        d.set_rules([Requirement('a','所有Word','word'),Requirement('b','说明书','word',1,'说明书')])
        d.refresh();wait(lambda:d.worker is None)
        assert d.report.ambiguous and d.ambiguity_table.rowCount()==1
        combo=d.ambiguity_table.cellWidget(0,1)
        assert combo.currentData() is None
        combo.setCurrentIndex(combo.findData('b'));QApplication.processEvents()
        assert not d.report.ambiguous and sum(r.matched for r in d.report.rows)==1
        assert not d.report.complete
    finally:d.shutdown();d.deleteLater()


def test_partial_read_cannot_show_all_collected(qapp,service,monkeypatch):
    module=load('material_check_ui')
    monkeypatch.setattr(module,'list_project_documents',lambda *a,**k:dict(project_id=1,workspace_path='registered',
        errors=['部分目录不可读'],documents=[dict(id='a',name='说明书.docx',kind='工作文件',exists=True,is_directory=False)]))
    d=make_dialog(service)
    try:
        d.set_rules([Requirement('a','说明书','word',1,'说明书')]);d.refresh();wait(lambda:d.worker is None)
        assert not d.report.complete and '不完整' in d.status_label.text()
    finally:d.shutdown();d.deleteLater()


def test_reader_error_and_wrong_project_result_are_visible(qapp,service,monkeypatch):
    module=load('material_check_ui')
    for response in ('error','wrong'):
        def query(*a,**k):
            if response=='error':raise OSError('isolated failure')
            return dict(project_id=2,workspace_path='registered',errors=[],documents=[])
        monkeypatch.setattr(module,'list_project_documents',query)
        d=make_dialog(service)
        try:
            d.refresh();wait(lambda:d.worker is None)
            assert d.report is None and '未完成' in d.status_label.text()
        finally:d.shutdown();d.deleteLater()


def test_cancel_waits_for_reader_without_forcing_thread(qapp,service,monkeypatch):
    module=load('material_check_ui');entered=Event()
    def query(*a,cancel_event=None,**kw):
        entered.set();assert cancel_event.wait(5)
        from concurrent.futures import CancelledError
        raise CancelledError()
    monkeypatch.setattr(module,'list_project_documents',query)
    before=snapshots(service);d=make_dialog(service)
    try:
        d.show();d.refresh();wait(entered.is_set);d.reject()
        wait(lambda:d.worker is None and not d.isVisible())
        assert d.report is None
    finally:d.shutdown();d.deleteLater()
    assert snapshots(service)==before


def test_project_menu_has_read_only_material_entry(qapp,service,monkeypatch):
    from compact_ui import CompactPanel
    panel=CompactPanel(service)
    try:
        called=[]
        monkeypatch.setattr(panel,'open_material_check',lambda pid:called.append(pid),raising=False)
        menu=panel.project_context_menu(1)
        actions=[a for a in menu.actions() if a.text()=='材料检查…']
        assert len(actions)==1, 'missing material check menu action'
        actions[0].trigger();assert called==[1]
        menu.deleteLater()
    finally:panel.document_pane.shutdown();panel.hide();panel.deleteLater()


def test_controller_stops_material_reader_before_disposal(qapp,service):
    from desktop_controller import FloatingController
    controller=FloatingController(service,tray_enabled=False)
    assert controller.start()
    calls=[]
    class Dialog:
        def shutdown(self):calls.append('stopped')
    controller.panel.material_dialog=Dialog()
    try:
        controller.shutdown()
        assert calls==['stopped'], 'controller ignored material reader during exit'
    finally:
        controller.panel.material_dialog=None
        controller.shutdown();controller.dispose_ui()


def test_json_null_is_corrupt_setting_not_an_unconfigured_project(service):
    store=load('material_check_store')
    with service.db.connect() as db:
        db.execute('INSERT INTO desktop_settings VALUES (?,?)',(store.setting_key(1),'null'))
    with pytest.raises(ValueError):store.load_rules(service,1)


def test_rules_changed_during_read_do_not_display_old_success(qapp,service,monkeypatch):
    module=load('material_check_ui');entered,release=Event(),Event()
    def query(*a,**kw):
        entered.set();assert release.wait(5)
        return dict(project_id=1,workspace_path='registered',errors=[],documents=[])
    monkeypatch.setattr(module,'list_project_documents',query)
    d=make_dialog(service)
    try:
        d.refresh();wait(entered.is_set)
        d.set_rules([Requirement('a','新要求','pdf',1,'报告')]);release.set()
        wait(lambda:d.worker is None)
        assert d.report is None and '重新检查' in d.status_label.text()
    finally:release.set();d.shutdown();d.deleteLater()


def test_failed_material_shutdown_keeps_controller_and_lock(qapp,service):
    from desktop_controller import FloatingController
    controller=FloatingController(service,tray_enabled=False)
    assert controller.start()
    class Pending:
        def shutdown(self):raise RuntimeError('reader pending')
    controller.panel.material_dialog=Pending()
    try:
        with pytest.raises(RuntimeError,match='pending'):controller.shutdown()
        assert not controller.closed and controller.owns_lock
    finally:
        controller.panel.material_dialog=None
        controller.shutdown();controller.dispose_ui()


def test_merged_project_rejects_rule_save_without_mutation(service,monkeypatch):
    store=load('material_check_store');before=snapshots(service)
    project=service.get_project(1);project['merged_into_id']=2
    monkeypatch.setattr(service,'get_project',lambda _:project)
    with pytest.raises(ValueError):store.save_rules(service,1,[Requirement('a','文档','word')])
    assert snapshots(service)==before


def test_narrow_rule_controls_show_type_and_two_digit_count(qapp,service):
    from PySide6.QtWidgets import QStyle, QStyleOptionComboBox
    d=make_dialog(service)
    try:
        d.set_rules([Requirement('a','说明书','word',20,'说明书')])
        d.show();d.tabs.setCurrentIndex(0);QApplication.processEvents()
        d.resize(600,540);QApplication.processEvents()
        family=d.rule_table.cellWidget(0,1)
        opt=QStyleOptionComboBox();family.initStyleOption(opt)
        rect=family.style().subControlRect(QStyle.CC_ComboBox,opt,QStyle.SC_ComboBoxEditField,family)
        assert rect.width()>=family.fontMetrics().horizontalAdvance(family.currentText()), 'file family text clipped'
        count=d.rule_table.cellWidget(0,2)
        edit=count.lineEdit()
        assert edit.contentsRect().width()>=edit.fontMetrics().horizontalAdvance('20')+16, 'count text clipped by nested padding'
        assert d.rect().contains(d.close_button.mapTo(d,d.close_button.rect().bottomRight()))
    finally:d.shutdown();d.deleteLater()
