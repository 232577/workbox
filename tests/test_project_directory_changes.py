import importlib.util
import json
import threading
from pathlib import Path

import pytest

from app import WorkBox
from workspace_manager import WorkspaceManager


@pytest.fixture
def context(tmp_path):
    service = WorkBox(tmp_path / 'data' / 'box.db')
    manager = WorkspaceManager(service)
    lock = threading.RLock()
    service.db._operation_lock = lock
    with service.db.connect() as connection:
        connection.executescript('''
            CREATE TABLE IF NOT EXISTS project_merges (
                source_id INTEGER PRIMARY KEY, target_id INTEGER NOT NULL,
                operation_id TEXT NOT NULL UNIQUE, merged_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS project_change_commits (
                operation_id TEXT PRIMARY KEY, kind TEXT NOT NULL, source_id INTEGER NOT NULL,
                target_id INTEGER, new_name TEXT NOT NULL, committed_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS project_change_blocks (
                project_id INTEGER PRIMARY KEY, operation_id TEXT NOT NULL, reason TEXT NOT NULL);
        ''')
    return service, manager, lock


def coordinator(context):
    if importlib.util.find_spec('project_directory_changes') is None:
        pytest.fail('项目目录变更协调器尚未实现')
    from project_directory_changes import ProjectDirectoryChanges
    return ProjectDirectoryChanges(context[0].db, context[1], context[2])


def create(context, names, batch='本批'):
    service, manager, _ = context
    ids = service.db.create_capture('\n'.join(names), batch, [(name, False) for name in names])['project_ids']
    manager.ensure_projects(ids)
    return ids


def commit(context, *, fail=False):
    service, _, _ = context
    def apply(operation_id, mapping, result):
        if fail:
            raise RuntimeError('模拟提交失败')
        with service.db.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            if result['kind'] == 'rename':
                connection.execute('UPDATE projects SET name=? WHERE id=?', (result['new_name'], result['source_id']))
                for old, new in mapping.items():
                    connection.execute('UPDATE project_workspaces SET path=?,group_path=CASE WHEN group_path=? THEN ? ELSE group_path END WHERE path=?',
                                       (new, old, new, old))
            connection.execute('INSERT INTO project_change_commits(operation_id,kind,source_id,target_id,new_name) VALUES (?,?,?,?,?)',
                               (operation_id, result['kind'], result['source_id'], result['target_id'], result['new_name']))
        return {'saved': True}
    return apply


def test_rename_uses_registered_parent_and_preserves_filenames(context, tmp_path):
    pid = create(context, ['旧系统'])[0]
    old = Path(context[1].get_workspace(pid)['path'])
    (old / '旧系统_材料.txt').write_bytes(b'original')
    (old / '旧系统.zip').write_bytes(b'zip bytes retained')
    context[1].configure(tmp_path / '未来工作', tmp_path / '未来归档')
    changes = coordinator(context)
    plan = changes.preview(pid, '新系统')
    assert json.loads(json.dumps(plan, ensure_ascii=False)) == plan
    assert Path(plan['target_path']) == old.parent / '新系统'
    assert old.is_dir() and not Path(plan['target_path']).exists()
    changes.execute(plan, commit(context))
    new = old.parent / '新系统'
    assert not old.exists()
    assert (new / '旧系统.zip').read_bytes() == b'zip bytes retained'
    assert (new / '旧系统_材料.txt').read_bytes() == b'original'
    assert context[1].get_workspace(pid)['group_path'] == str(new)


def test_batch_first_leaf_changes_without_moving_group_or_sibling(context):
    first, second = create(context, ['第一系统', '第二系统'])
    before = context[1].get_workspace(first)
    sibling = Path(context[1].get_workspace(second)['path'])
    changes = coordinator(context)
    plan = changes.preview(first, '新第一系统')
    changes.execute(plan, commit(context))
    assert sibling.is_dir()
    assert Path(before['group_path']).is_dir()
    assert context[1].get_workspace(first)['group_path'] == before['group_path']


def test_merge_copies_complete_tree_without_overwriting_or_removing_source(context):
    source, target = create(context, ['来源系统', '目标系统'])
    root = Path(context[1].get_workspace(source)['path'])
    target_root = Path(context[1].get_workspace(target)['path'])
    (root / '资料').mkdir()
    (root / '空目录').mkdir()
    (root / '资料' / '来源系统.zip').write_bytes(b'source')
    (target_root / '来源系统.zip').write_bytes(b'target')
    changes = coordinator(context)
    plan = changes.preview(source, '目标系统', target)
    assert Path(plan['target_path']) == target_root
    changes.execute(plan, commit(context))
    assert (target_root / '资料' / '来源系统.zip').read_bytes() == b'source'
    assert (target_root / '空目录').is_dir()
    assert (root / '资料' / '来源系统.zip').read_bytes() == b'source'
    assert (target_root / '来源系统.zip').read_bytes() == b'target'
    assert not (target_root / '合并资料').exists()
    assert not list(target_root.parent.glob('.workbox-merge-backup-*'))


def test_recover_interrupted_flat_merge_restores_original_target(context):
    import uuid
    from project_directory_changes import _directory_identity
    source, target = create(context, ['来源系统', '目标系统'])
    old = Path(context[1].get_workspace(source)['path'])
    retained = Path(context[1].get_workspace(target)['path'])
    (old / '并入.txt').write_bytes(b'source')
    (retained / '原有.txt').write_bytes(b'target')
    changes = coordinator(context)
    plan = changes.preview(source, '目标系统', target)
    operation_id = uuid.uuid4().hex
    stage = changes._stage_path(plan, operation_id)
    backup = retained.parent / ('.workbox-merge-backup-' + operation_id)
    stage.mkdir()
    changes._copy_merge_stage(old, retained, stage, plan)
    record = dict(operation_id=operation_id, plan=plan, state='published',
                  stage_path=str(stage), stage_identity=_directory_identity(stage),
                  backup_path=str(backup), backup_identity=plan['target_identity'], error='')
    changes._write(record)
    retained.rename(backup)
    stage.rename(retained)
    assert (retained / '并入.txt').read_bytes() == b'source'
    assert changes.recover() == []
    assert (retained / '原有.txt').read_bytes() == b'target'
    assert not (retained / '并入.txt').exists()
    assert (old / '并入.txt').read_bytes() == b'source'
    assert (stage / '并入.txt').read_bytes() == b'source'


def test_merge_reuses_target_directory_spelling_and_maps_nested_files(context):
    source, target = create(context, ['SourceProject', 'TargetProject'])
    old = Path(context[1].get_workspace(source)['path'])
    retained = Path(context[1].get_workspace(target)['path'])
    (old / 'docs').mkdir()
    (old / 'docs' / 'new.txt').write_bytes(b'source')
    (retained / 'Docs').mkdir()
    (retained / 'Docs' / 'existing.txt').write_bytes(b'target')
    changes = coordinator(context)
    plan = changes.preview(source, 'TargetProject', target)
    changes.execute(plan, commit(context))
    assert (retained / 'Docs' / 'new.txt').read_bytes() == b'source'
    assert (retained / 'Docs' / 'existing.txt').read_bytes() == b'target'
    assert plan['path_mapping'][str(old / 'docs' / 'new.txt')] == str(retained / 'Docs' / 'new.txt')
    assert 'Docs' in [path.name for path in retained.iterdir()]


def test_merge_cleanup_preserves_material_added_after_backup_inventory(context, monkeypatch):
    source, target = create(context, ['SourceProject', 'TargetProject'])
    old = Path(context[1].get_workspace(source)['path'])
    retained = Path(context[1].get_workspace(target)['path'])
    (old / 'source.txt').write_bytes(b'source')
    (retained / 'target.txt').write_bytes(b'target')
    changes = coordinator(context)
    original = changes._inventory
    added = []
    def save_after_inventory(root, **kwargs):
        inventory = original(root, **kwargs)
        if Path(root).name.startswith('.workbox-merge-backup-') and not kwargs and not added:
            latest = Path(root) / 'latest.txt'
            latest.write_bytes(b'only copy of newly saved material')
            added.append(latest)
        return inventory
    monkeypatch.setattr(changes, '_inventory', save_after_inventory)
    plan = changes.preview(source, 'TargetProject', target)
    result = changes.execute(plan, commit(context))
    assert added and added[0].read_bytes() == b'only copy of newly saved material'
    assert result.get('cleanup_error')
    assert (retained / 'target.txt').read_bytes() == b'target'
    assert (retained / 'source.txt').read_bytes() == b'source'


def test_merge_cleanup_detects_backup_edits_after_inventory(context, monkeypatch):
    source, target = create(context, ['SourceProject', 'TargetProject'])
    retained = Path(context[1].get_workspace(target)['path'])
    (retained / 'target.txt').write_bytes(b'target')
    changes = coordinator(context)
    original = changes._inventory
    edited = []
    def edit_after_inventory(root, **kwargs):
        inventory = original(root, **kwargs)
        if Path(root).name.startswith('.workbox-merge-backup-') and not kwargs and not edited:
            latest = Path(root) / 'target.txt'
            latest.write_bytes(b'latest external edit')
            edited.append(latest)
        return inventory
    monkeypatch.setattr(changes, '_inventory', edit_after_inventory)
    plan = changes.preview(source, 'TargetProject', target)
    result = changes.execute(plan, commit(context))
    assert edited and edited[0].read_bytes() == b'latest external edit'
    assert result.get('cleanup_error')
    assert (retained / 'target.txt').read_bytes() == b'target'


def test_merge_cleanup_locks_backup_identity_and_published_bytes(context, monkeypatch):
    source, target = create(context, ['SourceProject', 'TargetProject'])
    retained = Path(context[1].get_workspace(target)['path'])
    (retained / 'target.txt').write_bytes(b'target')
    changes = coordinator(context)
    import project_directory_changes as module
    original_inventory = changes._inventory
    original_remove = module.ZipLease.remove_after_verified_commit
    denied = []
    def replace_after_inventory(root, **kwargs):
        inventory = original_inventory(root, **kwargs)
        if Path(root).name.startswith('.workbox-merge-backup-') and not kwargs and not denied:
            try:
                Path(root).rename(Path(root).with_name('externally-moved-backup'))
            except PermissionError:
                denied.append('backup')
        return inventory
    def edit_published_before_remove(lease):
        try:
            (retained / 'target.txt').write_bytes(b'external edit')
        except PermissionError:
            denied.append('published')
        return original_remove(lease)
    monkeypatch.setattr(changes, '_inventory', replace_after_inventory)
    monkeypatch.setattr(module.ZipLease, 'remove_after_verified_commit', edit_published_before_remove)
    plan = changes.preview(source, 'TargetProject', target)
    result = changes.execute(plan, commit(context))
    assert denied == ['backup', 'published']
    assert not result.get('cleanup_error')
    assert (retained / 'target.txt').read_bytes() == b'target'


def test_merge_cleanup_resumes_after_partial_file_deletion(context, monkeypatch):
    source, target = create(context, ['SourceProject', 'TargetProject'])
    retained = Path(context[1].get_workspace(target)['path'])
    (retained / 'nested').mkdir()
    (retained / 'nested' / 'first.txt').write_bytes(b'first')
    (retained / 'nested' / 'second.txt').write_bytes(b'second')
    changes = coordinator(context)
    import project_directory_changes as module
    original = module.ZipLease.remove_after_verified_commit
    removed = []
    def interrupt_after_removal(lease):
        original(lease)
        removed.append(lease.path)
        raise OSError('simulated cleanup interruption')
    monkeypatch.setattr(module.ZipLease, 'remove_after_verified_commit', interrupt_after_removal)
    plan = changes.preview(source, 'TargetProject', target)
    result = changes.execute(plan, commit(context))
    assert result.get('cleanup_error') and len(removed) == 1
    assert not removed[0].exists()
    assert list(retained.parent.glob('.workbox-merge-backup-*'))
    monkeypatch.setattr(module.ZipLease, 'remove_after_verified_commit', original)
    assert changes.recover() == []
    assert not list(retained.parent.glob('.workbox-merge-backup-*'))
    assert (retained / 'nested' / 'first.txt').read_bytes() == b'first'
    assert (retained / 'nested' / 'second.txt').read_bytes() == b'second'


def test_recover_preupgrade_nested_merge_journal(context):
    import hashlib
    import uuid
    from project_directory_changes import _directory_identity, _json
    source, target = create(context, ['来源系统', '目标系统'])
    old = Path(context[1].get_workspace(source)['path'])
    retained = Path(context[1].get_workspace(target)['path'])
    (old / '并入.txt').write_bytes(b'source')
    changes = coordinator(context)
    plan = changes.preview(source, '目标系统', target)
    nested = retained / '合并资料' / old.name
    nested.parent.mkdir()
    plan['target_path'] = str(nested)
    plan['path_mapping'] = {str(old): str(nested)}
    plan['parent_identity'] = dict(path=str(nested.parent), identity=_directory_identity(nested.parent))
    for key in ('target_inventory', 'merge_inventory', 'merge_files', 'fingerprint'):
        plan.pop(key)
    plan['fingerprint'] = hashlib.sha256(_json(plan).encode('utf-8')).hexdigest()
    operation_id = uuid.uuid4().hex
    stage = changes._stage_path(plan, operation_id)
    stage.mkdir()
    changes._copy_tree(old, stage, plan['inventory'])
    record = dict(operation_id=operation_id, plan=plan, state='published',
                  stage_path=str(stage), stage_identity=_directory_identity(stage), error='')
    changes._write(record)
    stage.rename(nested)
    assert changes.recover() == []
    assert (old / '并入.txt').read_bytes() == b'source'
    assert not nested.exists()
    assert retained.is_dir()
    assert (stage / '并入.txt').read_bytes() == b'source'


@pytest.mark.parametrize('kind', ['rename', 'merge'])
def test_database_failure_restores_original_and_preserves_owned_stage(context, kind):
    source, target = create(context, ['来源系统', '目标系统'])
    old = Path(context[1].get_workspace(source)['path'])
    (old / '保留.txt').write_bytes(b'keep')
    changes = coordinator(context)
    plan = changes.preview(source, '新系统' if kind == 'rename' else '目标系统', None if kind == 'rename' else target)
    with pytest.raises(RuntimeError, match='模拟提交失败'):
        changes.execute(plan, commit(context, fail=True))
    assert (old / '保留.txt').read_bytes() == b'keep'
    if kind == 'rename':
        assert not Path(plan['target_path']).exists()
    else:
        assert Path(plan['target_path']).is_dir()
    assert changes.recover() == []
    if kind == 'merge':
        assert any(path.name == '保留.txt' for path in old.parent.rglob('保留.txt'))


@pytest.mark.parametrize('mutation', ['file', 'project', 'target'])
def test_stale_plan_rejected_before_file_or_database_change(context, mutation):
    source = create(context, ['来源系统'])[0]
    old = Path(context[1].get_workspace(source)['path'])
    (old / '材料.txt').write_bytes(b'initial')
    changes = coordinator(context)
    plan = changes.preview(source, '新系统')
    if mutation == 'file':
        (old / '材料.txt').write_bytes(b'changed after preview')
    elif mutation == 'project':
        with context[0].db.connect() as connection:
            connection.execute('UPDATE projects SET name=? WHERE id=?', ('另一个名', source))
    else:
        Path(plan['target_path']).mkdir()
    called = []
    with pytest.raises((ValueError, RuntimeError, FileExistsError)):
        changes.execute(plan, lambda *args: called.append(args))
    assert old.is_dir() and called == []


def test_rename_rejects_registered_archive_inside_working_directory(context):
    source = create(context, ['来源系统'])[0]
    root = Path(context[1].get_workspace(source)['path'])
    archive = root / '历史归档.zip'
    archive.write_bytes(b'history')
    with context[0].db.connect() as connection:
        connection.execute('INSERT INTO zips(project_id,path,filename,size,sha256,mtime_ns) VALUES (?,?,?,?,?,?)',
                           (source, str(archive), archive.name, archive.stat().st_size, 'a' * 64, archive.stat().st_mtime_ns))
    with pytest.raises(ValueError, match='归档'):
        coordinator(context).preview(source, '新系统')
    assert archive.read_bytes() == b'history'


def test_nested_other_workspace_rejected(context):
    source, other = create(context, ['来源系统', '另一个系统'])
    root = Path(context[1].get_workspace(source)['path'])
    nested = root / '嵌套项目'
    nested.mkdir()
    with context[0].db.connect() as connection:
        connection.execute('UPDATE project_workspaces SET path=? WHERE project_id=?', (str(nested), other))
    with pytest.raises(ValueError, match='目录'):
        coordinator(context).preview(source, '新系统')


def test_cancelled_plan_does_not_call_commit(context):
    source = create(context, ['来源系统'])[0]
    changes = coordinator(context)
    plan = changes.preview(source, '新系统')
    event = threading.Event()
    event.set()
    from concurrent.futures import CancelledError
    with pytest.raises(CancelledError):
        changes.execute(plan, lambda *args: pytest.fail('取消后不应提交'), cancel_event=event)
    assert Path(plan['source_path']).is_dir()


def test_preview_without_registered_directory_is_noop(context):
    source = context[0].db.create_capture('来源系统', '未登记', [('来源系统', False)])['project_ids'][0]
    changes = coordinator(context)
    plan = changes.preview(source, '新系统')
    assert plan['path_mapping'] == {}
    changes.execute(plan, commit(context))
    assert context[0].get_project(source)['name'] == '新系统'


def test_source_identity_replacement_rejected_even_when_content_equal(context):
    source = create(context, ['来源系统'])[0]
    root = Path(context[1].get_workspace(source)['path'])
    (root / '材料.txt').write_bytes(b'same')
    changes = coordinator(context)
    plan = changes.preview(source, '新系统')
    root.rename(root.with_name('外部挪走'))
    root.mkdir()
    (root / '材料.txt').write_bytes(b'same')
    with pytest.raises((ValueError, RuntimeError)):
        changes.execute(plan, lambda *args: pytest.fail('替换目录不能提交'))
    assert root.is_dir()


def test_merged_project_workspace_is_not_discovered_or_recreated(context):
    source, target = create(context, ['来源系统', '目标系统'])
    with context[0].db.connect() as connection:
        connection.execute('INSERT INTO project_merges(source_id,target_id,operation_id,snapshot_json) VALUES (?,?,?,?)', (source, target, 'merged-test', '{}'))
    assert source not in [row['project_id'] for row in context[1].list_workspaces(include_archived=True)]
    with pytest.raises(ValueError):
        context[1].ensure_projects([source])


def test_workspace_mutations_wait_for_shared_operation_lock(context, tmp_path):
    source = create(context, ['来源系统'])[0]
    file = tmp_path / '材料.txt'
    file.write_bytes(b'payload')
    started, completed = threading.Event(), threading.Event()
    errors = []
    def work():
        started.set()
        try:
            context[1].import_file(file, source)
        except BaseException as error:
            errors.append(error)
        finally:
            completed.set()
    with context[2]:
        worker = threading.Thread(target=work)
        worker.start()
        assert started.wait(2)
        assert not completed.wait(.15)
    worker.join(5)
    assert completed.is_set() and errors == []


def interrupted(changes, plan, *, published=False):
    import uuid
    record = dict(operation_id=uuid.uuid4().hex, plan=plan, state='prepared',
                  stage_path='', stage_identity=None, error='')
    changes._write(record)
    if published:
        Path(plan['source_path']).rename(plan['target_path'])
    return record


def test_recover_interrupted_rename_returns_owned_directory_to_original(context):
    source = create(context, ['来源系统'])[0]
    changes = coordinator(context)
    plan = changes.preview(source, '新系统')
    interrupted(changes, plan, published=True)
    assert changes.recover() == []
    assert Path(plan['source_path']).is_dir() and not Path(plan['target_path']).exists()


def test_recovery_never_moves_directory_back_after_database_commit(context):
    source = create(context, ['来源系统'])[0]
    changes = coordinator(context)
    plan = changes.preview(source, '新系统')
    record = interrupted(changes, plan, published=True)
    commit(context)(record['operation_id'], plan['path_mapping'], plan)
    assert changes.recover() == []
    assert Path(plan['target_path']).is_dir() and not Path(plan['source_path']).exists()


def test_recovery_blocks_projects_when_new_path_was_replaced(context):
    source = create(context, ['来源系统'])[0]
    changes = coordinator(context)
    plan = changes.preview(source, '新系统')
    interrupted(changes, plan, published=True)
    new = Path(plan['target_path'])
    new.rename(new.with_name('被外部移动'))
    new.mkdir()
    (new / '未知文件.txt').write_bytes(b'unknown')
    assert changes.recover()
    with context[0].db.connect() as connection:
        assert connection.execute('SELECT 1 FROM project_change_blocks WHERE project_id=?', (source,)).fetchone()
    assert (new / '未知文件.txt').read_bytes() == b'unknown'
    with pytest.raises(ValueError):
        context[1].ensure_projects([source])


def test_committed_missing_directory_is_blocked_without_rollback(context):
    source = create(context, ['来源系统'])[0]
    changes = coordinator(context)
    plan = changes.preview(source, '新系统')
    record = interrupted(changes, plan, published=True)
    commit(context)(record['operation_id'], plan['path_mapping'], plan)
    new = Path(plan['target_path'])
    retained = new.with_name('外部移动的已提交目录')
    new.rename(retained)
    assert changes.recover()
    assert retained.is_dir() and not Path(plan['source_path']).exists()


def test_merge_detects_corrupted_published_copy_before_commit(context, monkeypatch):
    source, target = create(context, ['来源系统', '目标系统'])
    old = Path(context[1].get_workspace(source)['path'])
    (old / '材料.txt').write_bytes(b'expected')
    changes = coordinator(context)
    plan = changes.preview(source, '目标系统', target)
    import project_directory_changes as module
    original = module.copy_verified
    def altered(*args, **kwargs):
        metadata = original(*args, **kwargs)
        Path(metadata['path']).write_bytes(b'corrupted')
        return metadata
    monkeypatch.setattr(module, 'copy_verified', altered)
    called = []
    with pytest.raises(RuntimeError, match='副本'):
        changes.execute(plan, lambda *args: called.append(args))
    assert called == [] and (old / '材料.txt').read_bytes() == b'expected'


def test_junction_is_rejected_before_any_move(context, tmp_path):
    import subprocess
    source = create(context, ['来源系统'])[0]
    root = Path(context[1].get_workspace(source)['path'])
    external = tmp_path / '外部目录'
    external.mkdir()
    link = root / '链接目录'
    created = subprocess.run(['cmd', '/d', '/c', 'mklink', '/J', str(link), str(external)], capture_output=True)
    if created.returncode:
        pytest.skip('本测试卷不支持创建 junction：' + created.stderr.decode('mbcs', errors='replace'))
    try:
        with pytest.raises(ValueError):
            coordinator(context).preview(source, '新系统')
        assert external.is_dir()
    finally:
        link.rmdir()


def test_case_only_rename_updates_directory_spelling(context):
    source = create(context, ['CaseProject'])[0]
    changes = coordinator(context)
    plan = changes.preview(source, 'caseProject')
    changes.execute(plan, commit(context))
    root = Path(plan['target_path'])
    assert 'caseProject' in [path.name for path in root.parent.iterdir()]


def test_case_only_database_failure_restores_original_spelling(context):
    source = create(context, ['CaseProject'])[0]
    changes = coordinator(context)
    plan = changes.preview(source, 'caseProject')
    with pytest.raises(RuntimeError, match='模拟提交失败'):
        changes.execute(plan, commit(context, fail=True))
    root = Path(plan['source_path'])
    assert 'CaseProject' in [path.name for path in root.parent.iterdir()]


def test_corrupted_journal_plan_is_blocked_without_moving_any_directory(context):
    source = create(context, ['来源系统'])[0]
    changes = coordinator(context)
    plan = changes.preview(source, '新系统')
    record = interrupted(changes, plan)
    record['plan']['target_path'] = str(Path(plan['target_path']).with_name('未知目录'))
    changes._write(record)
    assert changes.recover()
    assert Path(plan['source_path']).is_dir()
    with context[0].db.connect() as connection:
        assert connection.execute('SELECT 1 FROM project_change_blocks WHERE project_id=?', (source,)).fetchone()


def test_same_process_session_lock_is_borrowed_and_never_released(context):
    from PySide6.QtCore import QLockFile
    source = create(context, ['来源系统'])[0]
    changes = coordinator(context)
    lock = QLockFile(str(context[0].db.path) + '.session.lock')
    lock.setStaleLockTime(0)
    assert lock.tryLock(0)
    try:
        plan = changes.preview(source, '新系统')
        changes.execute(plan, commit(context))
        assert changes.recover() == []
        probe = QLockFile(str(context[0].db.path) + '.session.lock')
        probe.setStaleLockTime(0)
        assert not probe.tryLock(0)
        assert lock.isLocked()
    finally:
        lock.unlock()


def test_preview_does_not_create_session_lock_and_backend_releases_owned_lock(context):
    source = create(context, ['来源系统'])[0]
    changes = coordinator(context)
    lock_path = Path(str(context[0].db.path) + '.session.lock')
    assert not lock_path.exists()
    plan = changes.preview(source, '新系统')
    assert not lock_path.exists()
    changes.execute(plan, commit(context))
    assert not lock_path.exists()


def test_other_process_session_lock_refuses_recovery_and_execute(context):
    import subprocess
    import sys
    source = create(context, ['来源系统'])[0]
    changes = coordinator(context)
    plan = changes.preview(source, '新系统')
    interrupted(changes, plan, published=True)
    program = '''import sys
from PySide6.QtCore import QLockFile
lock = QLockFile(sys.argv[1])
lock.setStaleLockTime(0)
assert lock.tryLock(0)
print('LOCKED', flush=True)
sys.stdin.readline()
lock.unlock()
'''
    child = subprocess.Popen([sys.executable, '-c', program, str(context[0].db.path) + '.session.lock'],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == 'LOCKED'
        with pytest.raises(RuntimeError, match='进程|运行'):
            changes.recover()
        with pytest.raises(RuntimeError, match='进程|运行'):
            changes.execute(plan, lambda *args: pytest.fail('其他进程持锁时不能提交'))
        assert Path(plan['target_path']).is_dir() and not Path(plan['source_path']).exists()
    finally:
        child.communicate('\n', timeout=10)
        if child.poll() is None:
            child.kill()
    assert changes.recover() == []
    assert Path(plan['source_path']).is_dir()


def test_unreadable_journal_returns_global_block_and_known_failure_returns_ids(context):
    source = create(context, ['来源系统'])[0]
    changes = coordinator(context)
    changes.journal_root.mkdir()
    (changes.journal_root / 'invalid.json').write_text('{', encoding='utf-8')
    errors = changes.recover()
    assert errors[0]['global_block'] is True and errors[0]['project_ids'] == []
    (changes.journal_root / 'invalid.json').unlink()
    plan = changes.preview(source, '新系统')
    interrupted(changes, plan, published=True)
    target = Path(plan['target_path'])
    target.rename(target.with_name('外部移动'))
    target.mkdir()
    errors = changes.recover()
    assert errors[0]['global_block'] is False and errors[0]['project_ids'] == [source]


@pytest.mark.parametrize('location', ['source', 'published'])
def test_merge_file_bytes_are_locked_through_database_commit(context, location):
    source, target = create(context, ['来源系统', '目标系统'])
    old = Path(context[1].get_workspace(source)['path'])
    (old / '材料.txt').write_bytes(b'previewed')
    changes = coordinator(context)
    plan = changes.preview(source, '目标系统', target)
    denied = []
    def apply(operation_id, mapping, result):
        path = (old if location == 'source' else Path(plan['target_path'])) / '材料.txt'
        try:
            path.write_bytes(b'edited at commit boundary')
        except PermissionError:
            denied.append(True)
        else:
            denied.append(False)
        return commit(context)(operation_id, mapping, result)
    changes.execute(plan, apply)
    assert denied == [True]
    assert (old / '材料.txt').read_bytes() == b'previewed'
    assert (Path(plan['target_path']) / '材料.txt').read_bytes() == b'previewed'


def test_merge_staging_and_rollback_copy_are_outside_registered_workspaces(context, monkeypatch):
    source, target = create(context, ['来源系统', '目标系统'])
    old = Path(context[1].get_workspace(source)['path'])
    (old / '材料.txt').write_bytes(b'payload')
    changes = coordinator(context)
    plan = changes.preview(source, '目标系统', target)
    seen = []
    original = changes._copy_tree
    def inspect_stage(source_root, stage, *args, **kwargs):
        registered = [Path(row['path']) for row in context[1].list_workspaces(include_archived=True)]
        seen.append((stage, all(not stage.is_relative_to(root) for root in registered)))
        return original(source_root, stage, *args, **kwargs)
    monkeypatch.setattr(changes, '_copy_tree', inspect_stage)
    with pytest.raises(RuntimeError, match='模拟提交失败'):
        changes.execute(plan, commit(context, fail=True))
    assert seen and seen[0][1]
    assert (seen[0][0] / '材料.txt').read_bytes() == b'payload'
    assert Path(plan['target_path']).is_dir()
    assert not (Path(plan['target_path']) / '材料.txt').exists()


def test_edit_in_publish_to_lease_gap_is_detected_and_retained_on_rollback(context, monkeypatch):
    source, target = create(context, ['来源系统', '目标系统'])
    old = Path(context[1].get_workspace(source)['path'])
    (old / '材料.txt').write_bytes(b'previewed')
    changes = coordinator(context)
    plan = changes.preview(source, '目标系统', target)
    import project_directory_changes as module
    original = module._DirectoryLease.rename
    def edited(lease, destination):
        publishing = (lease.path.name.startswith('.workbox-merge-')
                      and not lease.path.name.startswith('.workbox-merge-backup-'))
        original(lease, destination)
        if publishing and Path(destination) == Path(plan['target_path']):
            (Path(destination) / '材料.txt').write_bytes(b'external latest edit')
    monkeypatch.setattr(module._DirectoryLease, 'rename', edited)
    called = []
    with pytest.raises(RuntimeError, match='副本'):
        changes.execute(plan, lambda *args: called.append(args))
    assert called == []
    assert (old / '材料.txt').read_bytes() == b'previewed'
    assert Path(plan['target_path']).is_dir()
    assert not (Path(plan['target_path']) / '材料.txt').exists()
    journals = [json.loads(path.read_text(encoding='utf-8')) for path in changes.journal_root.glob('*.json')]
    assert (Path(journals[0]['stage_path']) / '材料.txt').read_bytes() == b'external latest edit'


def test_cancelled_preview_is_read_only_and_does_not_create_locks_or_journal(context):
    source = create(context, ['来源系统'])[0]
    changes = coordinator(context)
    before = sorted(str(path) for path in context[0].db.path.parent.rglob('*'))
    event = threading.Event()
    event.set()
    from concurrent.futures import CancelledError
    with pytest.raises(CancelledError):
        changes.preview(source, '新系统', cancel_event=event)
    assert sorted(str(path) for path in context[0].db.path.parent.rglob('*')) == before
    assert context[0].get_project(source)['name'] == '来源系统'


def test_large_file_preview_hash_stops_at_cancelled_read_boundary(context, monkeypatch):
    source = create(context, ['来源系统'])[0]
    root = Path(context[1].get_workspace(source)['path'])
    payload = b'large-preview-file-' * 200000
    file = root / '大材料.bin'
    file.write_bytes(payload)
    changes = coordinator(context)
    event = threading.Event()
    import project_directory_changes as module
    original = module._hash_stream
    reads = []
    class StopAfterRead:
        def __init__(self, stream):
            self.stream = stream
        def __getattr__(self, name):
            return getattr(self.stream, name)
        def read(self, count):
            data = self.stream.read(count)
            reads.append(len(data))
            event.set()
            return data
    def cancelling_hash(stream, **kwargs):
        assert kwargs['cancel_event'] is event
        return original(StopAfterRead(stream), **kwargs)
    monkeypatch.setattr(module, '_hash_stream', cancelling_hash)
    from concurrent.futures import CancelledError
    with pytest.raises(CancelledError):
        changes.preview(source, '新系统', cancel_event=event)
    assert reads == [1024 * 1024]
    assert file.read_bytes() == payload
    assert not changes.journal_root.exists()
    assert not Path(str(context[0].db.path) + '.session.lock').exists()


@pytest.mark.parametrize('location', ['source', 'published'])
def test_commit_boundary_rejects_new_material_entries_and_retains_latest(context, location):
    source, target = create(context, ['来源系统', '目标系统'])
    old = Path(context[1].get_workspace(source)['path'])
    (old / '原材料.txt').write_bytes(b'confirmed inventory')
    changes = coordinator(context)
    plan = changes.preview(source, '目标系统', target)
    def apply(operation_id, mapping, result):
        root = old if location == 'source' else Path(plan['target_path'])
        (root / '新材料.txt').write_bytes(b'latest newly added material')
        changes.verify_commit_boundary(plan, result)
        return commit(context)(operation_id, mapping, result)
    with pytest.raises(RuntimeError, match='材料|副本'):
        changes.execute(plan, apply)
    assert Path(plan['target_path']).is_dir()
    assert not (Path(plan['target_path']) / '新材料.txt').exists()
    with context[0].db.connect() as connection:
        assert connection.execute('SELECT count(*) FROM project_change_commits').fetchone()[0] == 0
    if location == 'source':
        assert (old / '新材料.txt').read_bytes() == b'latest newly added material'
    else:
        journal = next(changes.journal_root.glob('*.json'))
        record = json.loads(journal.read_text(encoding='utf-8'))
        assert (Path(record['stage_path']) / '新材料.txt').read_bytes() == b'latest newly added material'


def test_normal_rename_boundary_keeps_new_material_without_freezing_inventory(context):
    source = create(context, ['来源系统'])[0]
    changes = coordinator(context)
    plan = changes.preview(source, '新系统')
    def apply(operation_id, mapping, result):
        (Path(plan['target_path']) / '新材料.txt').write_bytes(b'latest')
        changes.verify_commit_boundary(plan, result)
        return commit(context)(operation_id, mapping, result)
    changes.execute(plan, apply)
    assert (Path(plan['target_path']) / '新材料.txt').read_bytes() == b'latest'
