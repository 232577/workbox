"""Read-only checklist integration uses canonical identities and actual rules."""
from concurrent.futures import CancelledError
from importlib import import_module, util
from pathlib import Path
from threading import Event

import pytest

from desktop_service import DesktopService
from material_check_store import save_rules
from material_matching import Requirement


def review():
    assert util.find_spec('checklist_review') is not None, 'Checklist integration not implemented'
    return import_module('checklist_review')


def entry(name, row=2):
    return dict(name=name, date='2026-11-09', source_date='2026-11-09', row=row, sheet='示例名单', duplicate=False)


def dump(service):
    with service.db.connect() as db:
        return '\n'.join(db.iterdump())


def test_comparison_keeps_duplicates_and_never_creates_projects(tmp_path):
    s = DesktopService(tmp_path/'data/db.sqlite')
    pid = s.receive_text('1. 示例甲系统')['project_ids'][0]
    before = dump(s)
    rows = review().review_entries(s, [entry('示例甲系统'), entry('未收到系统'), entry('示例甲系统',3)])
    assert [r['project_id'] for r in rows] == [pid, None, pid]
    assert [r['state'] for r in rows] == ['已关联','尚未收录','已关联']
    assert rows[0]['material_state'] == '未检查'
    assert dump(s) == before


@pytest.mark.parametrize('boundary', ['multiple', 'archived', 'deleted'])
def test_protected_and_same_name_projects_are_not_silently_selected(tmp_path, boundary):
    s = DesktopService(tmp_path/'data/db.sqlite')
    pid = s.receive_text('1. 示例甲系统')['project_ids'][0]
    if boundary == 'multiple': s.capture_message('1. 示例甲系统', '独立同名批次')
    elif boundary == 'archived': s.bulk_update_projects([pid], archived=True)
    else: s.delete_project(pid, confirmed=True)
    before = dump(s)
    row = review().review_entries(s, [entry('示例甲系统')], check_materials=True)[0]
    assert row['state'] == {'multiple':'同名待确认','archived':'已归档','deleted':'已删除'}[boundary]
    assert row['project_id'] is None
    assert dump(s) == before


def test_material_checks_use_saved_rules_without_setting_delivery(tmp_path):
    s = DesktopService(tmp_path/'data/db.sqlite')
    pid = s.receive_text('1. 示例甲系统')['project_ids'][0]
    root = Path(s.workspaces.get_workspace(pid)['path'])
    (root/'示例甲系统说明书.docx').write_bytes(b'anonymous')
    save_rules(s,pid,[Requirement('doc','说明书','word',1,'说明书'),Requirement('pdf','说明书PDF','pdf',1,'说明书')])
    before = dump(s)
    row = review().review_entries(s, [entry('示例甲系统')], check_materials=True)[0]
    assert row['material_state'] == '材料不齐'
    assert '说明书PDF' in row['missing']
    assert row['matched'] == 1 and row['required'] == 2
    assert row['project_state'] == '待开发'
    assert dump(s) == before
    (root/'示例甲系统说明书.pdf').write_bytes(b'pdf')
    row = review().review_entries(s, [entry('示例甲系统')], check_materials=True)[0]
    assert row['material_state'] == '已收齐'
    assert dump(s) == before


def test_missing_directory_is_unknown_not_complete(tmp_path):
    s = DesktopService(tmp_path/'data/db.sqlite'); pid=s.receive_text('1. 示例甲系统')['project_ids'][0]
    save_rules(s,pid,[Requirement('word','说明书','word')])
    Path(s.workspaces.get_workspace(pid)['path']).rmdir()
    row=review().review_entries(s,[entry('示例甲系统')],check_materials=True)[0]
    assert row['material_state'] == '读取不完整'


def test_no_saved_rules_never_assumes_default_five_materials(tmp_path):
    s=DesktopService(tmp_path/'data/db.sqlite'); s.receive_text('1. 示例甲系统')
    before=dump(s)
    row=review().review_entries(s,[entry('示例甲系统')],check_materials=True)[0]
    assert row['material_state'] == '未配置规则'
    assert dump(s)==before


def test_full_name_normalization_is_not_fuzzy_matching(tmp_path):
    s=DesktopService(tmp_path/'data/db.sqlite'); pid=s.receive_text('1. ＡＩ 资料系统')['project_ids'][0]
    rows=review().review_entries(s,[entry('ai   资料系统'),entry('资料系统')])
    assert rows[0]['project_id']==pid and rows[1]['state']=='尚未收录'


def test_cancel_before_read_and_batch_limit(tmp_path):
    s=DesktopService(tmp_path/'data/db.sqlite'); event=Event();event.set();before=dump(s)
    with pytest.raises(CancelledError): review().review_entries(s,[entry('甲系统')],cancel_event=event)
    with pytest.raises(ValueError): review().review_entries(s,[entry(str(i)) for i in range(2001)])
    assert dump(s)==before
