"""Reject private payloads and common secret patterns without printing their contents."""
from __future__ import annotations

import argparse
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

PRIVATE_DIRS={'.local','.tmp','.venv','.cache','data','backups','releases','build','dist','verification','软件著作权申请资料'}
PRIVATE_SUFFIXES={'.db','.sqlite','.sqlite3','.db-wal','.db-shm','.exe','.msi','.zip','.7z','.key','.pfx','.p12','.pem'}
SECRET=re.compile(rb'gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----')

def inspect_file(name: str, content: bytes) -> list[str]:
    """Return reasons, not matching content. This is a guardrail, not a full secret audit."""
    path=PurePosixPath(name.replace('\\','/'))
    reasons=[]
    if not path.parts or path.is_absolute() or '..' in path.parts or path.parts[0] in PRIVATE_DIRS:
        reasons.append('private or unsafe path')
    if (path.name=='.env' or path.name.startswith('.env.') and path.name!='.env.example'
            or path.name.startswith('.dev.vars') or path.suffix.lower() in PRIVATE_SUFFIXES):
        reasons.append('credentials, live data or distribution payload')
    if SECRET.search(content):
        reasons.append('possible credential/private key')
    if len(content)>25*1024*1024:
        reasons.append('oversized file; publish artifacts through Releases')
    return reasons

def check_push(lines: str, allow_tag_creation: bool=False) -> list[str]:
    errors=[]
    for line in lines.splitlines():
        fields=line.split()
        if not fields:
            continue
        if len(fields)!=4:
            errors.append('invalid pre-push input');continue
        _,local_sha,remote_ref,remote_sha=fields
        if remote_ref in {'refs/heads/main','refs/heads/master'}:
            errors.append('main is PR-only; push a task branch')
        if remote_ref.startswith('refs/tags/'):
            if set(remote_sha)!={'0'} or set(local_sha)=={'0'}:
                errors.append('published tags cannot be updated or deleted')
            elif not allow_tag_creation:
                errors.append('new tags require the explicit release procedure')
    return errors

def inspect_repository(root: Path, staged: bool=False) -> list[str]:
    args=['git','diff','--cached','--name-only','--diff-filter=ACMR','-z'] if staged else ['git','ls-files','-z']
    names=subprocess.check_output(args,cwd=root).decode('utf-8').split('\0')
    index={}
    for entry in subprocess.check_output(['git','ls-files','--stage','-z'],cwd=root).decode('utf-8').split('\0'):
        if entry:
            metadata,name=entry.split('\t',1)
            mode,_,stage=metadata.split()
            index.setdefault(name,[]).append((mode,stage))
    errors=[]
    for name in filter(None,names):
        if any(mode not in {'100644','100755'} or stage!='0' for mode,stage in index.get(name,[])):
            errors.append(name+': links, submodules or unresolved index entries are not allowed')
            continue
        if staged:
            content=subprocess.check_output(['git','show',':'+name],cwd=root)
        else:
            path=root/name
            if path.is_symlink():
                errors.append(name+': links are not allowed');continue
            if not path.is_file():
                errors.append(name+': tracked file is missing');continue
            content=path.read_bytes()
        errors.extend(name+': '+reason for reason in inspect_file(name,content))
    return errors

def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--all',action='store_true');mode.add_argument('--staged',action='store_true');mode.add_argument('--pre-push',action='store_true')
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    errors=(check_push(sys.stdin.read(),os.environ.get('WORKBOX_RELEASE_TAGS')=='1') if args.pre_push
            else inspect_repository(root,args.staged))
    for error in errors:
        print(error,file=sys.stderr)
    if errors:
        return 1
    print('Repository boundary check passed.')
    return 0

if __name__=='__main__':
    raise SystemExit(main())
