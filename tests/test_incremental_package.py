"""仅从匿名干净程序载荷生成增量包，不执行程序或访问业务目录。"""
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile, ZIP_DEFLATED

import pytest
import delta_format as fmt

FROM, TO = 'v1.0.0.20261113.2', 'v1.0.0.20261113.3'
CONTENTS = 'PACKAGE_CONTENTS.sha256'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def module():
    path = Path(__file__).resolve().parents[1] / 'scripts/build_incremental_update.py'
    assert path.is_file(), '缺少只读增量包生成器'
    return importlib.import_module('scripts.build_incremental_update')


def payload(root, records):
    root.mkdir()
    for name, data in records.items():
        path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
    (root / CONTENTS).write_text(''.join(f'{sha(data)}  {name}\n' for name, data in sorted(records.items())), encoding='utf-8')


@pytest.fixture
def candidate(tmp_path):
    records = {name: ('anonymous ' + name).encode() for name in fmt.REQUIRED_FILES if name != CONTENTS}
    records.update({'_internal/removed.dll': b'removed', 'licenses/python.txt': b'anonymous license'})
    updated = dict(records); del updated['_internal/removed.dll']
    updated['WorkBox.exe'] = b'MZnew anonymous runtime'
    updated['_internal/added.dll'] = b'anonymous new dependency'
    base, target = tmp_path / 'base', tmp_path / 'target'
    payload(base, records); payload(target, updated)
    return base, target, tmp_path / fmt.delta_filename(FROM, TO)


def build(candidate):
    return module().build_delta(*candidate, from_tag=FROM, to_tag=TO)


def snapshot(root):
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob('*') if path.is_file()}


def test_real_changes_additions_deletions_and_manifest_self_digest(candidate):
    base, target, output = candidate
    before = snapshot(base), snapshot(target)
    result = build(candidate)
    assert set(result) == {'name', 'size', 'sha256', 'from_tag', 'to_tag', 'changed_count', 'removed_count'}
    assert result['name'] == output.name and result['size'] == output.stat().st_size
    assert result['sha256'] == sha(output.read_bytes())
    assert (result['from_tag'], result['to_tag'], result['changed_count'], result['removed_count']) == (FROM, TO, 3, 1)
    manifest = fmt.read_manifest(output, expected_sha256=result['sha256'], expected_size=result['size'], from_tag=FROM, to_tag=TO)
    assert set(manifest['base']) == set(before[0]) and set(manifest['target']) == set(before[1])
    for side, values in (('base', before[0]), ('target', before[1])):
        assert manifest[side][CONTENTS] == {'size': len(values[CONTENTS]), 'sha256': sha(values[CONTENTS])}
    with ZipFile(output) as archive:
        assert archive.namelist() == ['delta.json', 'payload/PACKAGE_CONTENTS.sha256', 'payload/WorkBox.exe', 'payload/_internal/added.dll']
        assert all(not info.is_dir() and info.compress_type == ZIP_DEFLATED and info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist())
        assert archive.read('payload/WorkBox.exe') == before[1]['WorkBox.exe']
    assert (snapshot(base), snapshot(target)) == before


def test_identical_inputs_make_identical_zip_bytes_in_different_output_dirs(candidate, tmp_path):
    base, target, output = candidate
    build(candidate)
    other = tmp_path / 'other'; other.mkdir()
    report = module().build_delta(base, target, other / output.name, from_tag=FROM, to_tag=TO)
    assert output.read_bytes() == (other / output.name).read_bytes()
    assert report['sha256'] == sha(output.read_bytes())


@pytest.mark.parametrize('damage', ['tampered', 'missing', 'extra', 'missing-helper', 'self', 'duplicate', 'case', 'bad-digest', 'unsafe', 'private-empty-dir'])
@pytest.mark.parametrize('side', [0, 1])
def test_invalid_input_payload_is_rejected_without_output(candidate, damage, side):
    root = candidate[side]; manifest = root / CONTENTS
    if damage == 'tampered': (root / 'WorkBox.exe').write_bytes(b'changed')
    elif damage == 'missing': (root / '_internal/python311.dll').unlink()
    elif damage == 'extra': (root / '_internal/extra.dll').write_bytes(b'preserve')
    elif damage == 'missing-helper':
        (root / 'WorkBoxUpdater.exe').unlink()
        manifest.write_text('\n'.join(line for line in manifest.read_text(encoding='utf-8').splitlines() if not line.endswith('  WorkBoxUpdater.exe')) + '\n', encoding='utf-8')
    elif damage == 'self': manifest.write_text(manifest.read_text(encoding='utf-8') + '0' * 64 + '  ' + CONTENTS + '\n', encoding='utf-8')
    elif damage in ('duplicate', 'case'):
        text = manifest.read_text(encoding='utf-8'); line = next(line for line in text.splitlines() if line.endswith('  WorkBox.exe'))
        manifest.write_text(text + (line if damage == 'duplicate' else line.replace('WorkBox.exe', 'workbox.exe')) + '\n', encoding='utf-8')
    elif damage == 'bad-digest': manifest.write_text('x' * 64 + '  WorkBox.exe\n', encoding='utf-8')
    elif damage == 'unsafe': manifest.write_text('0' * 64 + '  ../outside.exe\n', encoding='utf-8')
    else: (root / '_internal/data').mkdir()
    before = snapshot(root)
    with pytest.raises((ValueError, OSError)): build(candidate)
    assert not candidate[2].exists() and snapshot(root) == before


@pytest.mark.parametrize('location', ['existing', 'base', 'target', 'wrong-name'])
def test_output_never_overwrites_or_enters_input(candidate, tmp_path, location):
    base, target, output = candidate
    if location == 'existing': output.write_bytes(b'preserve output')
    elif location == 'base': output = base / output.name
    elif location == 'target': output = target / 'subdir' / output.name
    else: output = tmp_path / 'wrong.wbdelta'
    before = snapshot(base), snapshot(target)
    with pytest.raises((ValueError, OSError)):
        module().build_delta(base, target, output, from_tag=FROM, to_tag=TO)
    assert (snapshot(base), snapshot(target)) == before
    if location == 'existing': assert output.read_bytes() == b'preserve output'
    else: assert not output.exists()


@pytest.mark.parametrize('kind', ['hardlink', 'junction', 'stream'])
@pytest.mark.skipif(os.name != 'nt', reason='真实 Windows 链接及命名流边界')
def test_real_links_and_streams_in_payload_are_rejected(candidate, tmp_path, kind):
    base, _, output = candidate; program = base / 'WorkBox.exe'
    if kind == 'hardlink': os.link(program, tmp_path / 'user-copy.exe')
    elif kind == 'junction':
        import _winapi
        external = tmp_path / 'external'; external.mkdir(); (external / 'safe.dll').write_bytes(b'preserve')
        _winapi.CreateJunction(str(external), str(base / '_internal/link'))
    else: Path(str(program) + ':unlisted').write_bytes(b'preserve stream')
    with pytest.raises((ValueError, OSError)): build(candidate)
    assert not output.exists() and program.exists()
    if kind == 'stream': assert Path(str(program) + ':unlisted').read_bytes() == b'preserve stream'


def test_failed_self_check_does_not_publish_and_preserves_inputs(candidate, monkeypatch):
    builder = module(); before = snapshot(candidate[0]), snapshot(candidate[1])
    monkeypatch.setattr(builder, 'read_manifest', lambda *a, **k: (_ for _ in ()).throw(ValueError('anonymous self-check failure')))
    with pytest.raises(ValueError): build(candidate)
    assert not candidate[2].exists() and (snapshot(candidate[0]), snapshot(candidate[1])) == before


def test_publish_race_never_replaces_the_other_output(candidate, monkeypatch):
    builder = module(); original = builder._publish_new
    def collide(source, output, **kwargs):
        output.write_bytes(b'preserve concurrently created output')
        return original(source, output, **kwargs)
    monkeypatch.setattr(builder, '_publish_new', collide)
    with pytest.raises((ValueError, OSError)): build(candidate)
    assert candidate[2].read_bytes() == b'preserve concurrently created output'


def test_source_change_after_package_self_check_prevents_publish(candidate, monkeypatch):
    builder = module(); original = builder.read_manifest
    def change_source(*args, **kwargs):
        result = original(*args, **kwargs)
        (candidate[0] / 'WorkBox.exe').write_bytes(b'external source edit')
        return result
    monkeypatch.setattr(builder, 'read_manifest', change_source)
    with pytest.raises(ValueError): build(candidate)
    assert not candidate[2].exists()
    assert (candidate[0] / 'WorkBox.exe').read_bytes() == b'external source edit'


def test_temporary_change_at_publish_is_not_mistaken_for_verified_package(candidate, monkeypatch):
    builder = module(); original = builder._publish_new
    def change_temporary(source, output, **kwargs):
        source.write_bytes(b'external replacement of uncommitted package')
        return original(source, output, **kwargs)
    monkeypatch.setattr(builder, '_publish_new', change_temporary)
    with pytest.raises((ValueError, OSError)): build(candidate)
    assert not candidate[2].exists()


def test_cli_outputs_only_public_asset_facts(candidate):
    base, target, output = candidate; builder = module()
    result = subprocess.run([sys.executable, str(Path(builder.__file__).resolve()), '--base', str(base),
        '--target', str(target), '--output', str(output), '--from-tag', FROM, '--to-tag', TO], capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stderr
    facts = json.loads(result.stdout)
    assert facts['name'] == output.name and facts['sha256'] == sha(output.read_bytes())
    assert str(base) not in result.stdout and str(target) not in result.stdout
