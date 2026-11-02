"""Portable, non-destructive setup and branch helpers for every WorkBox checkout."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

CANONICAL={'https://github.com/232577/workbox','git@github.com:232577/workbox','ssh://git@github.com/232577/workbox'}

def git(root: Path,*args: str,check: bool=True) -> subprocess.CompletedProcess:
    result=subprocess.run(['git',*args],cwd=root,capture_output=True,text=True,encoding='utf-8',timeout=90)
    if check and result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or 'git failed')
    return result

def require_clean(root: Path) -> None:
    if git(root,'status','--porcelain').stdout.strip():
        raise RuntimeError('工作目录存在未提交内容 (dirty)。请提交到自己的任务分支；工具不会 stash、reset 或丢弃文件。')

def require_remote(root: Path) -> None:
    for flags in (('--all',),('--push','--all')):
        result=git(root,'remote','get-url',*flags,'origin',check=False)
        urls=result.stdout.strip().splitlines()
        if result.returncode or len(urls)!=1 or urls[0].rstrip('/').removesuffix('.git') not in CANONICAL:
            raise RuntimeError('origin remote 不是唯一协作远端；停止操作，未修改文件。')

def validate_task_branch(root: Path, name: str) -> None:
    if not re.fullmatch(r'(feat|fix|docs|chore|test|release)/[a-z0-9][a-z0-9._/-]*',name):
        raise RuntimeError('使用 feat/、fix/、docs/、chore/、test/ 或 release/ 开头的任务分支。')
    git(root,'check-ref-format','--branch',name)

def start_branch(root: Path,name: str) -> None:
    validate_task_branch(root, name)
    require_clean(root);require_remote(root)
    git(root,'fetch','--prune','origin')
    for ref in ('refs/heads/'+name,'refs/remotes/origin/'+name):
        if git(root,'show-ref','--verify','--quiet',ref,check=False).returncode==0:
            raise RuntimeError('任务分支已存在；请核实归属后显式检出，不创建覆盖副本。')
    git(root,'switch','-c',name,'origin/main')

def resume_branch(root: Path, name: str) -> None:
    """Resume only a published task, without reset, stash or implicit conflict resolution."""
    validate_task_branch(root, name)
    require_clean(root)
    require_remote(root)
    git(root, 'fetch', '--prune', 'origin')
    remote = 'refs/remotes/origin/' + name
    local = 'refs/heads/' + name
    if git(root, 'show-ref', '--verify', '--quiet', remote, check=False).returncode:
        raise RuntimeError('远端任务分支不存在；请核实是否已推送或合并，未创建或切换分支。')
    if git(root, 'show-ref', '--verify', '--quiet', local, check=False).returncode:
        git(root, 'switch', '-c', name, '--track', 'origin/' + name)
        return
    upstream = git(root, 'for-each-ref', '--format=%(upstream)', local).stdout.strip()
    if upstream and upstream != remote:
        raise RuntimeError('本地任务分支的上游 upstream 不匹配；未修改关联或切换分支。')
    ancestry = git(root, 'merge-base', '--is-ancestor', local, remote, check=False)
    if ancestry.returncode:
        raise RuntimeError('本地任务有未推送提交、分歧或无法确认 fast-forward；请先核对，未切换或覆盖。')
    git(root, 'switch', name)
    git(root, 'merge', '--ff-only', remote)
    if not upstream:
        git(root, 'branch', '--set-upstream-to=origin/' + name, name)

def sync_main(root: Path) -> None:
    require_clean(root);require_remote(root)
    if git(root,'branch','--show-current').stdout.strip()!='main':
        raise RuntimeError('sync-main 仅用于 main；功能分支请显式合并 origin/main 并解决冲突。')
    git(root,'fetch','--prune','origin')
    git(root,'merge','--ff-only','origin/main')

def setup(root: Path) -> None:
    require_remote(root)
    for key,value in {'core.hooksPath':'.githooks','pull.ff':'only','push.default':'simple','fetch.prune':'true'}.items():
        git(root,'config','--local',key,value)
    print('仅配置当前克隆：安全 hooks、仅快速前进拉取、简单推送和远端分支清理。')

def doctor(root: Path) -> None:
    require_remote(root)
    print(json.dumps({'branch':git(root,'branch','--show-current').stdout.strip(),
        'head':git(root,'rev-parse','HEAD').stdout.strip(),
        'clean':not git(root,'status','--porcelain').stdout.strip(),
        'hooks':git(root,'config','--get','core.hooksPath',check=False).stdout.strip(),
        'remote':'232577/workbox','network_checked':False},ensure_ascii=False,indent=2))

def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['setup','doctor','start','resume','sync-main','check'])
    parser.add_argument('branch',nargs='?')
    args=parser.parse_args();root=Path(__file__).resolve().parents[1]
    try:
        if args.action in {'start', 'resume'}:
            if not args.branch:
                parser.error(args.action + ' requires a task branch name')
            (start_branch if args.action == 'start' else resume_branch)(root,args.branch)
        elif args.action=='setup':setup(root)
        elif args.action=='doctor':doctor(root)
        elif args.action=='sync-main':sync_main(root)
        else:
            commands=[[sys.executable,'scripts/repo_guard.py','--all'],['node','--test',*[str(p.relative_to(root)) for p in sorted((root/'website').glob('*.test.mjs'))]],
                      [sys.executable,'-m','scripts.run_tests','-q']]
            for command in commands:
                result=subprocess.run(command,cwd=root)
                if result.returncode:return result.returncode
        return 0
    except (RuntimeError,subprocess.TimeoutExpired) as error:
        print(str(error),file=sys.stderr);return 1

if __name__=='__main__':
    raise SystemExit(main())
