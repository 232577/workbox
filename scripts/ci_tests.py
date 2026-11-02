"""Run one deterministic shard of the entire test-file collection on Windows."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import subprocess
import sys

def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--shard',type=int,required=True);parser.add_argument('--shards',type=int,default=4)
    args=parser.parse_args()
    if not 1<=args.shard<=args.shards<=16:parser.error('invalid shard')
    root=Path(__file__).resolve().parents[1]
    files=sorted((root/'tests').glob('test_*.py'))
    chosen=[str(p.relative_to(root)) for i,p in enumerate(files) if i%args.shards==args.shard-1]
    if not chosen:parser.error('empty shard')
    out=root/'.local/ci';out.mkdir(parents=True,exist_ok=True)
    (out/f'collection-{args.shard}.json').write_text(json.dumps({'total_files':len(files),'shard':args.shard,'files':chosen},indent=2),encoding='utf-8')
    return subprocess.run([sys.executable,'-m','scripts.run_tests','-q',*chosen,
        '--basetemp='+str(out/f'fixtures-{args.shard}'),'--junitxml='+str(out/f'part-{args.shard}.xml')],cwd=root).returncode

if __name__=='__main__':raise SystemExit(main())
