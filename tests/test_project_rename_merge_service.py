"""显式改名与合并的业务、目录、归档和重复收件闭环。"""
import json
import os
from pathlib import Path
from zipfile import ZipFile

import pytest

from desktop_service import DesktopService

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='目录身份租约使用 Windows API')


@pytest.fixture
def service(tmp_path):
    return DesktopService(tmp_path/'data/workbox.db')


def receive(service, root, name, content='独立工作资料'):
    source = root/(name+'_操作手册.txt')
    source.write_text(content, encoding='utf-8')
    service.receive_path(source)
    project = next(row for row in service.list_projects() if row['name'] == name)
    return project['id'], source, Path(service.workspaces.get_workspace(project['id'])['path'])


def rows(service, table):
    with service.db.connect() as connection:
        return [dict(row) for row in connection.execute('SELECT * FROM '+table+' ORDER BY 1')]


def apply(service, pid, name, target=None):
    plan = service.preview_project_rename(pid, name, target)
    result = service.rename_project(pid, name, expected_token=plan['token'],
        merge_target_id=(plan.get('target') or {}).get('id'), confirmed_merge=plan['kind']=='merge')
    return plan, result


def test_rename_changes_registered_folder_and_paths_but_preserves_basename_source_and_archives(service,tmp_path):
    pid, source, original = receive(service,tmp_path,'旧项目')
    service.confirm_delivery(pid, confirmed=True)
    events = rows(service,'delivery_events')
    archives = rows(service,'project_delivery_archives')
    before = rows(service,'work_files')[0]
    plan = service.preview_project_rename(pid,'正确项目')
    assert plan['kind']=='rename' and original.exists() and service.get_project(pid)['name']=='旧项目'
    result = service.rename_project(pid,'正确项目',expected_token=plan['token'])
    new = Path(service.workspaces.get_workspace(pid)['path'])
    assert result['project_id']==pid and new == original.parent/'正确项目'
    assert not original.exists() and (new/source.name).read_bytes()==source.read_bytes()
    after = rows(service,'work_files')[0]
    assert after['filename']==before['filename'] and after['source_path']==before['source_path']
    assert after['path']==str(new/source.name) and after['sha256']==before['sha256']
    assert rows(service,'delivery_events')==events and rows(service,'project_delivery_archives')==archives
    assert all(Path(row['path']).exists() for row in archives)
    service.receive_path(source)
    assert service.db.counts()['projects']==1 and service.list_projects()[0]['id']==pid


def test_same_name_requires_confirmation_and_merges_pending_progress_with_preserved_originals(service,tmp_path):
    target = service.receive_text('1. 正确项目')['project_ids'][0]
    source_id, source, old_folder = receive(service,tmp_path,'错误名称')
    initial = rows(service,'projects')
    plan = service.preview_project_rename(source_id,'正确项目')
    assert plan['kind']=='merge' and plan['target']['id']==target and plan['result_status']=='待交付'
    assert rows(service,'projects')==initial and not Path(plan['copy_path']).exists()
    with pytest.raises(ValueError,match='合并|确认'):
        service.rename_project(source_id,'正确项目')
    assert rows(service,'projects')==initial
    result=service.rename_project(source_id,'正确项目',expected_token=plan['token'],merge_target_id=target,confirmed_merge=True)
    assert result['project_ids']==[target] and service.get_project(target)['status']=='待交付'
    assert [row['id'] for row in service.list_projects()]==[target]
    assert service.get_project(source_id)['merged_into_id']==target
    assert old_folder.exists() and (old_folder/source.name).read_bytes()==source.read_bytes()
    record=rows(service,'work_files')[0]
    assert record['project_id']==target and Path(record['path']).is_relative_to(Path(plan['folder_to']))
    assert Path(record['path']).name==source.name and Path(record['path']).read_bytes()==source.read_bytes()
    service.receive_path(source)
    assert service.db.counts()['projects']==1
    with pytest.raises(ValueError,match='合并'):
        service.workspaces.import_file(source,source_id)


def test_existing_same_name_rows_can_merge_without_changing_display_name(service,tmp_path):
    first=service.receive_text('1. 同名项目')['project_ids'][0]
    second=service.capture_message('另一条同名项目','第二批')[ 'project_ids'][0]
    # 模拟旧版仅改数据库名称造成的既有同名，不调用新接口制造错误。
    with service.db.connect() as connection:
        connection.execute('UPDATE projects SET name=? WHERE id=?',('同名项目',second))
    plan,result=apply(service,second,'同名项目')
    assert plan['kind']=='merge' and result['project_id']==first
    assert len(service.list_projects())==1


@pytest.mark.parametrize('has_target',[True,False])
def test_legacy_display_only_rename_recovers_old_name_from_successful_intake_history(service,tmp_path,has_target):
    target=service.receive_text('1. 正确项目')['project_ids'][0] if has_target else None
    source,original,old=receive(service,tmp_path,'原文件中的旧名称')
    with service.db.connect() as connection:
        connection.execute('UPDATE projects SET name=? WHERE id=?',('正确项目',source))
    assert not rows(service,'project_name_aliases')
    before=rows(service,'file_intake_events')
    plan,result=apply(service,source,'正确项目')
    kept=target or source
    assert result['project_id']==kept
    assert rows(service,'file_intake_events')==before
    assert any(alias['alias_name']=='原文件中的旧名称' and alias['project_id']==kept
               for alias in service.db.list_project_aliases())
    service.receive_path(original)
    assert service.db.counts()['projects']==1 and service.list_projects()[0]['id']==kept


def test_failed_receipt_name_is_not_treated_as_confirmed_historical_alias(service,tmp_path):
    pid,source,_=receive(service,tmp_path,'真实旧名称')
    service.intake_log.append(source,'failed',project_id=pid,project_name='未采用的猜测名称',message='隔离失败')
    apply(service,pid,'正确名称')
    aliases={row['alias_name'] for row in service.db.list_project_aliases()}
    assert '真实旧名称' in aliases and '未采用的猜测名称' not in aliases


def test_multiple_candidates_require_exact_target_id(service,tmp_path):
    a=service.receive_text('1. 正确项目')['project_ids'][0]
    b=service.capture_message('正确项目','另一批')['project_ids'][0]
    pid,_,_=receive(service,tmp_path,'误建项目')
    plan=service.preview_project_rename(pid,'正确项目')
    assert plan['kind']=='choose_target' and not plan.get('token')
    assert {row['id'] for row in plan['candidates']}=={a,b}
    chosen=service.preview_project_rename(pid,'正确项目',b)
    assert chosen['target']['id']==b and chosen['kind']=='merge'


def test_normalized_same_name_still_uses_existing_target_display_name(service,tmp_path):
    target=service.receive_text('1. ＡＩ 资料系统')['project_ids'][0]
    pid,_,_=receive(service,tmp_path,'误建项目')
    plan,result=apply(service,pid,'ai   资料系统')
    assert plan['kind']=='merge' and result['project_id']==target
    assert service.get_project(target)['name']=='ＡＩ 资料系统'


def test_duplicate_text_receipt_navigates_to_merged_target_without_rewriting_receipt(service,tmp_path):
    target=service.receive_text('1. 正确项目')['project_ids'][0]
    receipt=service.receive_text('1. 错误项目')
    source_id=receipt['project_ids'][0]
    original=rows(service,'intake_receipts')
    apply(service,source_id,'正确项目')
    duplicate=service.receive_text('1. 错误项目')
    assert duplicate['duplicate'] and duplicate['project_ids']==[target]
    assert 'workspace_error' not in duplicate and rows(service,'intake_receipts')==original


def test_ensure_workspace_after_same_batch_merge_ignores_retired_member(service):
    receipt=service.receive_text('1. 保留项目\n2. 错误项目')
    target,source_id=receipt['project_ids']
    apply(service,source_id,'保留项目')
    result=service.ensure_workspaces(target)
    assert [row['project_id'] for row in result]==[target]


def test_alias_ending_in_bundle_number_is_not_a_new_bundle(service,tmp_path):
    pid,_,_=receive(service,tmp_path,'资料系统-2')
    apply(service,pid,'正确资料系统')
    assert not service.is_bundle_candidate('资料系统-2.zip')


def test_zip_with_explicit_merged_id_uses_retained_project_and_never_creates_stub(service,tmp_path):
    target=service.receive_text('1. 正确项目')['project_ids'][0]
    source,_,_=receive(service,tmp_path,'错误项目')
    apply(service,source,'正确项目')
    package=tmp_path/f'WB-{source:06d}_错误项目.zip'
    with ZipFile(package,'w') as archive:
        for index in range(4):archive.writestr(f'{index}.txt','独立资料')
    assert [row['id'] for row in service.file_candidates(package)]==[target]
    service.receive_path(package)
    assert service.db.counts()['projects']==1
    assert service.snapshots.list_sources()[0]['project_id']==target


def test_retired_merge_backup_never_auto_creates_project_from_generic_filename(service,tmp_path):
    target=service.receive_text('1. 保留项目')['project_ids'][0]
    source,_,old=receive(service,tmp_path,'来源项目')
    apply(service,source,'保留项目')
    late=old/'附件.txt'
    late.write_text('保留目录内的新材料',encoding='utf-8')
    result=service.receive_path(late)
    assert result.get('intake_choice')==str(late)
    assert result['routing']['requires_choice'] and '备份' in result['routing']['basis']
    assert service.db.counts()['projects']==1
    explicit=service.receive_path(late,target,manual=True)
    assert explicit['file']['project_id']==target and late.exists()


def test_pending_archive_is_blocked_in_preview_before_copying(service,tmp_path):
    target=service.receive_text('1. 保留项目')['project_ids'][0]
    source,_,_=receive(service,tmp_path,'来源项目')
    package=tmp_path/'来源项目.zip'
    with ZipFile(package,'w') as archive:archive.writestr('a.txt','资料')
    service.snapshots.watch_zip(package,source)
    with service.db.connect() as connection:
        sid=connection.execute('SELECT id FROM zip_sources WHERE project_id=?',(source,)).fetchone()[0]
        connection.execute('INSERT INTO zip_snapshot_attempts(id,source_id,project_id,target,metadata,state) VALUES (?,?,?,?,?,?)',
            ('isolated-pending',sid,source,str(tmp_path/'pending.zip'),'{}','copied'))
    plan=service.preview_project_rename(source,'保留项目')
    assert plan['kind']=='blocked' and not plan['token']
    assert '归档' in plan['warning'] or '操作' in plan['warning']
    assert not service.directory_changes.journal_root.exists()


@pytest.mark.parametrize('same_batch',[True,False])
def test_merge_preserves_bundle_reintake_and_approval_or_blocks_cross_batch(service,tmp_path,same_batch):
    ids=service.receive_text('1. 来源系统\n2. 另一个系统\n3. 保留系统')['project_ids']
    source,other,target=ids
    if not same_batch:target=service.receive_text('1. 外批系统')['project_ids'][0]
    target_name=service.get_project(target)['name']
    package=tmp_path/'来源系统-2.zip'
    with ZipFile(package,'w') as archive:
        for name in ('来源系统','另一个系统'):
            for index in range(4):archive.writestr(f'{name}/{index}.txt','资料')
    service.receive_path(package)
    service.scan_archives(now=0);service.scan_archives(now=20)
    zid=service.db.list_zips()[0]['id']
    service.snapshots.approve_version(zid)
    plan=service.preview_project_rename(source,target_name)
    if not same_batch:
        assert plan['kind']=='blocked' and '批次' in plan['warning']
        assert not service.directory_changes.journal_root.exists()
        return
    before=rows(service,'delivery_events')
    service.rename_project(source,target_name,expected_token=plan['token'],merge_target_id=target,confirmed_merge=True)
    version=next(row for row in service.snapshots.list_versions() if row['zip_id']==zid)
    assert version['approved_count']==version['project_count']==2
    assert service.db.get_zip_bundle(zid)['state']=='matched'
    service.receive_path(package)
    service.scan_archives(now=40);service.scan_archives(now=60)
    assert service.db.get_zip_bundle(zid)['state']=='matched'
    assert rows(service,'delivery_events')==before


@pytest.mark.parametrize('change',['source-status','target-type','folder-content'])
def test_stale_confirmation_never_changes_name_or_folder(service,tmp_path,change):
    target=service.receive_text('1. 正确项目')['project_ids'][0]
    pid,_,old=receive(service,tmp_path,'误建项目')
    plan=service.preview_project_rename(pid,'正确项目')
    if change=='source-status': service.set_status(pid,'开发中')
    elif change=='target-type': service.set_project_types([target],service.create_project_type('特殊业务')['id'])
    else: (old/'新添加.txt').write_text('预览后新增',encoding='utf-8')
    with pytest.raises(ValueError,match='变化|重新'):
        service.rename_project(pid,'正确项目',expected_token=plan['token'],merge_target_id=target,confirmed_merge=True)
    assert service.get_project(pid)['name']=='误建项目' and old.exists()
    assert not rows(service,'project_merges') and not Path(plan['copy_path']).exists()


@pytest.mark.parametrize('boundary',['archived','deleted'])
def test_existing_boundary_name_is_reported_not_bypassed(service,tmp_path,boundary):
    target=service.receive_text('1. 正确项目')['project_ids'][0]
    pid,_,old=receive(service,tmp_path,'误建项目')
    if boundary=='archived':service.bulk_update_projects([target],archived=True)
    else:service.delete_project(target,confirmed=True)
    plan=service.preview_project_rename(pid,'正确项目')
    assert plan['kind']=='blocked' and not plan.get('token')
    with pytest.raises(ValueError):service.rename_project(pid,'正确项目')
    assert old.exists() and service.get_project(pid)['name']=='误建项目'


def test_sql_failure_rolls_folder_back_and_preserves_business_rows(service,tmp_path,monkeypatch):
    pid,source,old=receive(service,tmp_path,'旧项目')
    plan=service.preview_project_rename(pid,'新项目')
    before=rows(service,'projects')
    def fail(*args,**kwargs):raise RuntimeError('隔离提交失败')
    monkeypatch.setattr(service,'_commit_project_change',fail)
    with pytest.raises(RuntimeError,match='隔离提交失败'):
        service.rename_project(pid,'新项目',expected_token=plan['token'])
    assert old.exists() and (old/source.name).read_bytes()==source.read_bytes()
    assert not Path(plan['folder_to']).exists() and rows(service,'projects')==before
    assert not rows(service,'project_change_commits')


def test_new_file_before_sql_commit_rolls_back_merge_without_hiding_latest_material(service,tmp_path,monkeypatch):
    target=service.receive_text('1. 保留项目')['project_ids'][0]
    source_id,source,old=receive(service,tmp_path,'来源项目')
    plan=service.preview_project_rename(source_id,'保留项目')
    commit=service._commit_project_change
    def add_file_then_commit(*args,**kwargs):
        (old/'刚保存的材料.txt').write_text('应留在未合并的来源项目',encoding='utf-8')
        return commit(*args,**kwargs)
    monkeypatch.setattr(service,'_commit_project_change',add_file_then_commit)
    with pytest.raises(RuntimeError,match='变化|清单|材料'):
        service.rename_project(source_id,'保留项目',expected_token=plan['token'],merge_target_id=target,confirmed_merge=True)
    assert service.get_project(source_id)['merged_into_id'] is None
    assert not rows(service,'project_merges') and not rows(service,'project_change_commits')
    assert (old/'刚保存的材料.txt').read_text(encoding='utf-8')=='应留在未合并的来源项目'
    assert (old/source.name).exists() and not Path(plan['copy_path']).exists()


def test_merge_keeps_delivery_facts_and_uses_first_valid_count(service,tmp_path):
    a,_,_=receive(service,tmp_path,'保留项目')
    b,_,_=receive(service,tmp_path,'旧名称')
    service.confirm_delivery(a,confirmed=True);service.confirm_delivery(b,confirmed=True)
    before=rows(service,'delivery_events')
    apply(service,b,'保留项目')
    history=service.db.delivery_history()
    assert rows(service,'delivery_events')==before and history['project_count']==1 and history['event_count']==2
    assert {row['project_id'] for row in history['events']}=={a}
    service.db.void_delivery_events([before[-1]['id']],confirmed=True)
    assert service.get_project(a)['status']=='待交付'


def test_rename_and_merge_zip_watches_keep_archives_and_do_not_redeliver(service,tmp_path):
    pid,_,old=receive(service,tmp_path,'旧项目')
    package=tmp_path/'旧项目.zip'
    with ZipFile(package,'w') as archive:
        for index in range(4):archive.writestr(f'{index}.txt','资料')
    service.receive_path(package)
    service.scan_archives(now=0);service.scan_archives(now=20)
    archives=rows(service,'zips');events=rows(service,'delivery_events')
    apply(service,pid,'新项目')
    service.scan_archives(now=40);service.scan_archives(now=60)
    assert rows(service,'zips')==archives and rows(service,'delivery_events')==events
    sources=rows(service,'zip_sources')
    assert all(not Path(row['source_path']).is_relative_to(old) for row in sources)
    service.receive_path(package)
    assert service.db.counts()['projects']==1
