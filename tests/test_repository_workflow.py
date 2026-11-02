"""Public collaboration tooling uses isolated Git fixtures, never production data."""
import importlib.util
import subprocess
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]

def load(name):
    path=ROOT/'scripts'/f'{name}.py'
    assert path.is_file(), f'missing collaboration tool: {name}'
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

@pytest.mark.parametrize('name',['.env','.local/owner.json','data/workbox.db','backups/copy.zip','releases/Setup.exe','private.key'])
def test_private_payloads_are_rejected(name):
    guard=load('repo_guard')
    assert guard.inspect_file(name,b'ordinary content')

def test_public_code_and_templates_are_allowed_but_secret_content_is_not():
    guard=load('repo_guard')
    assert guard.inspect_file('scripts/example.py',b'print("hello")\n')==[]
    assert guard.inspect_file('.env.example',b'EXAMPLE=replace_me\n')==[]
    secret=('gh'+'p_'+'A'*36).encode()
    assert guard.inspect_file('website/settings.js',secret)

def git(root,*args):
    return subprocess.run(['git',*args],cwd=root,check=True,capture_output=True,text=True).stdout.strip()

@pytest.fixture
def repository(tmp_path):
    git(tmp_path,'init','-b','main');git(tmp_path,'config','user.name','Test')
    git(tmp_path,'config','user.email','test@example.invalid')
    (tmp_path/'file.txt').write_text('fixture',encoding='utf-8')
    git(tmp_path,'add','file.txt');git(tmp_path,'commit','-m','fixture')
    return tmp_path

def test_safe_start_rejects_missing_or_wrong_remote_without_modifying_tree(repository):
    dev=load('dev')
    git(repository,'remote','add','origin','https://example.invalid/other.git')
    before=git(repository,'rev-parse','HEAD')
    with pytest.raises(RuntimeError,match='remote|远端'):
        dev.start_branch(repository,'feat/test')
    assert git(repository,'rev-parse','HEAD')==before
    assert git(repository,'branch','--show-current')=='main'

def test_safe_start_rejects_dirty_tree_before_network(repository):
    dev=load('dev')
    git(repository,'remote','add','origin','https://github.com/232577/workbox.git')
    (repository/'file.txt').write_text('uncommitted',encoding='utf-8')
    with pytest.raises(RuntimeError,match='dirty|未提交'):
        dev.start_branch(repository,'feat/test')
    assert (repository/'file.txt').read_text(encoding='utf-8')=='uncommitted'

def test_pre_push_blocks_main_and_all_tag_updates():
    guard=load('repo_guard')
    old='1'*40;new='2'*40
    assert guard.check_push(f'refs/heads/main {new} refs/heads/main {old}\n')
    assert guard.check_push(f'refs/tags/v1 {new} refs/tags/v1 {old}\n')
    assert guard.check_push(f'refs/heads/feat/demo {new} refs/heads/feat/demo {old}\n')==[]

def test_staged_checks_read_index_instead_of_safe_unstaged_replacement(repository):
    guard=load('repo_guard')
    path=repository/'config.txt'
    path.write_text('gh'+'p_'+'A'*36,encoding='utf-8')
    git(repository,'add','config.txt')
    path.write_text('safe unstaged content',encoding='utf-8')
    assert guard.inspect_repository(repository,staged=True)
    git(repository,'add','config.txt')
    assert guard.inspect_repository(repository,staged=True)==[]

def test_index_symlink_is_rejected_without_needing_windows_link_permission(repository):
    guard=load('repo_guard')
    oid=subprocess.run(['git','hash-object','-w','--stdin'],cwd=repository,input='file.txt',
                       text=True,capture_output=True,check=True).stdout.strip()
    git(repository,'update-index','--add','--cacheinfo',f'120000,{oid},pointer.txt')
    assert guard.inspect_repository(repository,staged=True)

def remote_fixture(repository,monkeypatch,dev):
    remote=repository.parent/(repository.name+'-remote.git')
    git(repository,'init','--bare',str(remote))
    git(repository,'remote','add','origin',str(remote))
    git(repository,'push','-u','origin','main')
    other=repository.parent/(repository.name+'-other')
    git(repository,'clone','--branch','main',str(remote),str(other))
    git(other,'config','user.name','Other');git(other,'config','user.email','other@example.invalid')
    monkeypatch.setattr(dev,'CANONICAL',{str(remote).replace('\\','/').removesuffix('.git')})
    # Git retains the URL spelling, so use one normalized fixture URL.
    git(repository,'remote','set-url','origin',str(remote).replace('\\','/'))
    return other

def test_new_task_uses_remote_tip_and_preserves_existing_local_main(repository,monkeypatch):
    dev=load('dev');other=remote_fixture(repository,monkeypatch,dev)
    old=git(repository,'rev-parse','main')
    (other/'remote.txt').write_text('new work from another place',encoding='utf-8')
    git(other,'add','remote.txt');git(other,'commit','-m','other place');git(other,'push','origin','main')
    dev.start_branch(repository,'feat/new-place')
    assert git(repository,'branch','--show-current')=='feat/new-place'
    assert git(repository,'rev-parse','HEAD')==git(other,'rev-parse','HEAD')
    assert git(repository,'rev-parse','main')==old
    assert (repository/'remote.txt').is_file()

def test_diverged_main_refuses_fast_forward_without_losing_local_work(repository,monkeypatch):
    dev=load('dev');other=remote_fixture(repository,monkeypatch,dev)
    (repository/'local.txt').write_text('keep local',encoding='utf-8')
    git(repository,'add','local.txt');git(repository,'commit','-m','local work')
    before=git(repository,'rev-parse','HEAD')
    (other/'remote.txt').write_text('keep remote',encoding='utf-8')
    git(other,'add','remote.txt');git(other,'commit','-m','remote work');git(other,'push','origin','main')
    with pytest.raises(RuntimeError):dev.sync_main(repository)
    assert git(repository,'rev-parse','HEAD')==before
    assert git(repository,'rev-parse','origin/main')==git(other,'rev-parse','HEAD')
    assert (repository/'local.txt').read_text(encoding='utf-8')=='keep local'
    assert git(repository,'status','--porcelain')==''


def shared_task(repository, monkeypatch, dev):
    other = remote_fixture(repository, monkeypatch, dev)
    git(other, 'switch', '-c', 'feat/shared-task')
    (other / 'task.txt').write_text('first location', encoding='utf-8')
    git(other, 'add', 'task.txt')
    git(other, 'commit', '-m', 'task at first location')
    git(other, 'push', '-u', 'origin', 'feat/shared-task')
    return other


def test_resume_creates_tracking_branch_from_remote_task(repository, monkeypatch):
    dev = load('dev')
    other = shared_task(repository, monkeypatch, dev)
    main = git(repository, 'rev-parse', 'main')
    dev.resume_branch(repository, 'feat/shared-task')
    assert git(repository, 'branch', '--show-current') == 'feat/shared-task'
    assert git(repository, 'rev-parse', 'HEAD') == git(other, 'rev-parse', 'HEAD')
    assert git(repository, 'rev-parse', '--abbrev-ref', '@{upstream}') == 'origin/feat/shared-task'
    assert git(repository, 'rev-parse', 'main') == main


def test_resume_fast_forwards_existing_task_without_reset(repository, monkeypatch):
    dev = load('dev')
    other = shared_task(repository, monkeypatch, dev)
    dev.resume_branch(repository, 'feat/shared-task')
    git(repository, 'switch', 'main')
    (other / 'task.txt').write_text('second location update', encoding='utf-8')
    git(other, 'add', 'task.txt')
    git(other, 'commit', '-m', 'advance shared task')
    git(other, 'push')
    dev.resume_branch(repository, 'feat/shared-task')
    assert git(repository, 'rev-parse', 'HEAD') == git(other, 'rev-parse', 'HEAD')
    assert (repository / 'task.txt').read_text(encoding='utf-8') == 'second location update'


@pytest.mark.parametrize('remote_advances', [False, True])
def test_resume_rejects_unpushed_or_diverged_task_without_switching(repository, monkeypatch, remote_advances):
    dev = load('dev')
    other = shared_task(repository, monkeypatch, dev)
    dev.resume_branch(repository, 'feat/shared-task')
    (repository / 'local.txt').write_text('preserve unpushed work', encoding='utf-8')
    git(repository, 'add', 'local.txt')
    git(repository, 'commit', '-m', 'local unpushed work')
    local_tip = git(repository, 'rev-parse', 'HEAD')
    git(repository, 'switch', 'main')
    original = git(repository, 'rev-parse', 'HEAD')
    if remote_advances:
        (other / 'remote.txt').write_text('preserve remote work', encoding='utf-8')
        git(other, 'add', 'remote.txt')
        git(other, 'commit', '-m', 'other location work')
        git(other, 'push')
    with pytest.raises(RuntimeError, match='未推送|分歧|fast-forward'):
        dev.resume_branch(repository, 'feat/shared-task')
    assert git(repository, 'branch', '--show-current') == 'main'
    assert git(repository, 'rev-parse', 'HEAD') == original
    assert git(repository, 'rev-parse', 'feat/shared-task') == local_tip
    assert git(repository, 'show', 'feat/shared-task:local.txt') == 'preserve unpushed work'


def test_resume_rejects_missing_remote_task_without_creating_branch(repository, monkeypatch):
    dev = load('dev')
    remote_fixture(repository, monkeypatch, dev)
    before = git(repository, 'rev-parse', 'HEAD')
    with pytest.raises(RuntimeError, match='远端|remote'):
        dev.resume_branch(repository, 'feat/missing')
    assert git(repository, 'rev-parse', 'HEAD') == before
    assert git(repository, 'branch', '--list', 'feat/missing') == ''


def test_resume_rejects_dirty_work_before_network(repository):
    dev = load('dev')
    git(repository, 'remote', 'add', 'origin', 'https://example.invalid/must-not-contact.git')
    (repository / 'file.txt').write_text('unsaved work', encoding='utf-8')
    with pytest.raises(RuntimeError, match='dirty|未提交'):
        dev.resume_branch(repository, 'feat/shared-task')
    assert (repository / 'file.txt').read_text(encoding='utf-8') == 'unsaved work'


def test_resume_refuses_wrong_upstream_without_switching(repository, monkeypatch):
    dev = load('dev')
    shared_task(repository, monkeypatch, dev)
    dev.resume_branch(repository, 'feat/shared-task')
    git(repository, 'branch', '--set-upstream-to=origin/main', 'feat/shared-task')
    git(repository, 'switch', 'main')
    with pytest.raises(RuntimeError, match='upstream|上游'):
        dev.resume_branch(repository, 'feat/shared-task')
    assert git(repository, 'branch', '--show-current') == 'main'


def test_resume_rejects_main_and_wrong_remote(repository):
    dev = load('dev')
    git(repository, 'remote', 'add', 'origin', 'https://example.invalid/other.git')
    before = git(repository, 'rev-parse', 'HEAD')
    with pytest.raises(RuntimeError):
        dev.resume_branch(repository, 'main')
    with pytest.raises(RuntimeError, match='remote|远端'):
        dev.resume_branch(repository, 'feat/task')
    assert git(repository, 'rev-parse', 'HEAD') == before
