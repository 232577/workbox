"""Anonymous regressions for file/text order and explicitly confirmed merges."""
import json
import os
from pathlib import Path

import pytest

from desktop_service import DesktopService
from parser import ParsedProject


@pytest.fixture
def service(tmp_path):
    return DesktopService(tmp_path / 'data' / 'workbox.db')


def rows(service, table):
    with service.db.connect() as db:
        return [dict(r) for r in db.execute('SELECT * FROM ' + table + ' ORDER BY 1')]


def test_file_first_then_text_batch_reuses_project_and_keeps_its_history(service, tmp_path):
    source = tmp_path / '匿名数据归集系统.docx'
    source.write_bytes(b'anonymous-material')
    result = service.receive_path(source)
    pid = result['file']['project_id']
    original = service.get_project(pid)
    workspace = dict(service.workspaces.get_workspace(pid))
    files = rows(service, 'work_files')
    receipt = service.receive_text('1. 另一个示例系统\n2. 匿名数据归集系统')
    assert receipt['state'] == 'committed'
    assert receipt['project_ids'][1] == pid
    assert receipt['reused_project_ids'] == [pid]
    assert 'workspace_error' not in receipt
    assert service.db.counts()['projects'] == 2
    assert service.get_project(pid)['status'] == original['status'] == '待交付'
    assert dict(service.workspaces.get_workspace(pid)) == workspace
    assert rows(service, 'work_files') == files
    assert rows(service, 'delivery_events') == []
    assert source.read_bytes() == b'anonymous-material'


def test_text_first_then_file_and_reworded_message_keep_one_project(service, tmp_path):
    pid = service.receive_text('1. 匿名数据归集系统')['project_ids'][0]
    source = tmp_path / '匿名数据归集系统 (1).docx'
    source.write_bytes(b'material')
    assert service.receive_path(source)['file']['project_id'] == pid
    assert service.receive_text('项目名称：匿名数据归集系统')['project_ids'] == [pid]
    assert service.db.counts()['projects'] == 1


def test_review_confirmation_rechecks_existing_project_created_while_pending(service):
    receipt = service.receive_text('这是一段需要人工核对的文字。')
    pid = service.receive_text('1. 匿名数据归集系统')['project_ids'][0]
    result = service.resolve_text(receipt['id'], [ParsedProject('匿名数据归集系统')])
    assert result['project_ids'] == [pid]
    assert service.db.counts()['projects'] == 1


def test_reuse_preserves_state_urgency_and_type(service):
    pid = service.receive_text('1. ＡＩ 资料系统')['project_ids'][0]
    service.bulk_update_projects([pid], status='开发中', urgent=True)
    before = rows(service, 'projects')
    links = rows(service, 'project_type_links')
    result = service.resolve_text(service.receive_text('需要核对名称。')['id'],
                                  [ParsedProject('ai   资料系统', False)])
    assert result['project_ids'] == [pid]
    assert result['created_project_ids'] == []
    assert rows(service, 'projects') == before
    assert rows(service, 'project_type_links') == links


def test_mixed_batch_does_not_move_existing_project_into_new_batch(service):
    first = service.receive_text('1. 已有示例系统')['project_ids'][0]
    original = dict(service.workspaces.get_workspace(first))
    receipt = service.receive_text('1. 新项目甲系统\n2. 已有示例系统\n3. 新项目乙系统')
    assert receipt['project_ids'][1] == first
    assert dict(service.workspaces.get_workspace(first)) == original
    new = [service.workspaces.get_workspace(pid) for pid in receipt['created_project_ids']]
    assert len(new) == 2 and new[0]['batch_id'] == new[1]['batch_id'] != original['batch_id']
    assert new[0]['group_path'] == new[1]['group_path'] != original['group_path']


def test_merged_old_name_resolves_to_canonical_project_without_recreating(service):
    source = service.receive_text('1. 旧示例系统')['project_ids'][0]
    target = service.receive_text('1. 新示例系统')['project_ids'][0]
    plan = service.preview_project_rename(source, '新示例系统', target)
    service.rename_project(source, '新示例系统', merge_target_id=target,
                           expected_token=plan['token'], confirmed_merge=True)
    before = rows(service, 'projects')
    result = service.receive_text('项目名称：旧示例系统')
    assert result['project_ids'] == [target]
    assert result['reused_project_ids'] == [target]
    assert rows(service, 'projects') == before


def test_review_deduplicates_same_normalized_name_in_one_confirmation(service):
    result = service.resolve_text(service.receive_text('请核对下面内容。')['id'],
                                   [ParsedProject('ＡＩ 示例系统'), ParsedProject('ai 示例系统', True)])
    assert len(result['project_ids']) == 1
    assert service.get_project(result['project_ids'][0])['urgent']


@pytest.mark.parametrize('boundary', ['archived', 'deleted', 'multiple'])
def test_name_boundary_stays_pending_without_creating_partial_batch(service, boundary):
    pid = service.receive_text('1. 匿名数据归集系统')['project_ids'][0]
    if boundary == 'archived':
        service.bulk_update_projects([pid], archived=True)
    elif boundary == 'deleted':
        service.delete_project(pid, confirmed=True)
    else:
        service.capture_message('1. 匿名数据归集系统', '明确建立的另一批')
    before = rows(service, 'projects')
    result = service.receive_text('1. 全新示例系统\n2. 匿名数据归集系统')
    assert result['state'] == 'needs-review'
    assert result['reason']
    assert result['project_ids'] == []
    assert rows(service, 'projects') == before
    with pytest.raises(ValueError, match='同名|归档|删除'):
        service.resolve_text(result['id'], [ParsedProject('全新示例系统'), ParsedProject('匿名数据归集系统')])
    assert rows(service, 'projects') == before


@pytest.mark.skipif(os.name != 'nt', reason='Directory leases require Windows')
@pytest.mark.parametrize('from_file_project', [False, True])
def test_same_named_pending_and_new_project_merge_in_both_directions(service, tmp_path, from_file_project):
    source = tmp_path / '匿名数据归集系统.docx'
    source.write_bytes(b'original')
    file_id = service.receive_path(source)['file']['project_id']
    # Explicit legacy fixture: two same names already exist in different batches.
    batch = service.capture_message('1. 独立同批系统\n2. 匿名数据归集系统', '旧版重名批次')
    new_id = batch['project_ids'][1]
    source_id, target_id = (file_id,new_id) if from_file_project else (new_id,file_id)
    before_events = rows(service, 'delivery_events')
    original_root = Path(service.workspaces.get_workspace(source_id)['path'])
    retained_root = Path(service.workspaces.get_workspace(target_id)['path'])
    plan = service.preview_project_rename(source_id, '匿名数据归集系统', target_id)
    assert plan['kind'] == 'merge', plan.get('warning')
    result = service.rename_project(source_id, '匿名数据归集系统', merge_target_id=target_id,
        expected_token=plan['token'], confirmed_merge=True)
    assert result['project_id'] == target_id
    assert service.get_project(target_id)['status'] == '待交付'
    assert original_root.is_dir()
    assert (retained_root / source.name).read_bytes() == b'original'
    assert rows(service, 'delivery_events') == before_events
