"""Run one deterministic shard of the entire test-file collection on Windows."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--shard', type=int, required=True)
    parser.add_argument('--shards', type=int, default=4)
    parser.add_argument(
        '--output-dir',
        type=Path,
        default=Path('.local/ci'),
        help='Directory for reports and fixtures. Relative to the repository root. Defaults to .local/ci.',
    )
    args = parser.parse_args()
    if not 1 <= args.shard <= args.shards <= 16:
        parser.error('invalid shard')

    root = Path(__file__).resolve().parents[1]
    files = sorted((root / 'tests').rglob('test_*.py'))
    chosen = [str(path.relative_to(root)) for path in files[args.shard - 1::args.shards]]
    if not chosen:
        parser.error('empty shard')

    output = root / args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    manifest = {'total_files': len(files), 'shard': args.shard, 'files': chosen}
    (output / f'collection-{args.shard}.json').write_text(
        json.dumps(manifest, indent=2), encoding='utf-8',
    )

    fixtures = output / f'fixtures-{args.shard}'
    report = output / f'part-{args.shard}.xml'
    command = [
        sys.executable, '-m', 'scripts.run_tests', '-q', *chosen,
        f'--basetemp={fixtures}', f'--junitxml={report}',
    ]
    return subprocess.run(command, cwd=root).returncode


if __name__ == '__main__':
    raise SystemExit(main())
