"""Release consistency uses small anonymous local fixtures, never live installers."""
from copy import deepcopy
import hashlib
import importlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
TAG = 'v1.0.0.20261103.1'
FILENAME = 'WorkBox-1.0.0-Setup-x64-20261103-r1.exe'


def gate():
    assert (ROOT / 'scripts/verify_update_release.py').is_file(), 'Missing release consistency gate'
    return importlib.import_module('scripts.verify_update_release')


def digest(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def candidate(tmp_path):
    root = tmp_path / 'payload'
    records = {
        'WorkBox.exe': b'MZanonymous runtime',
        '_internal/python311.dll': b'python fixture',
        '_internal/PySide6/Qt6Core.dll': b'Qt fixture',
        '_internal/PySide6/plugins/platforms/qwindows.dll': b'platform fixture',
        '安装说明.txt': '匿名安装说明'.encode(),
        'THIRD_PARTY_NOTICES.md': b'anonymous notices',
        'licenses/python.txt': b'anonymous license',
    }
    for name, data in records.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    lines = ''.join(f'{digest(data)}  {name}\n' for name, data in sorted(records.items()))
    (root / 'PACKAGE_CONTENTS.sha256').write_text(lines, encoding='utf-8')
    installer = tmp_path / FILENAME
    installer.write_bytes(b'MZanonymous installer; never run')
    sha = digest(installer.read_bytes())
    manifest = dict(tag_name=TAG, draft=False, prerelease=False, body='匿名发行说明',
        html_url=f'https://github.com/232577/workbox/releases/tag/{TAG}',
        assets=[dict(name=FILENAME, state='uploaded', size=installer.stat().st_size,
            digest='sha256:' + sha,
            browser_download_url=f'https://github.com/232577/workbox/releases/download/{TAG}/{FILENAME}')])
    build_info = dict(installer_sha256=sha, installer_size=installer.stat().st_size,
        app_sha256=digest(records['WorkBox.exe']), payload_files=len(records),
        contains_user_data=False, runtime_validation='pending')
    runtime_info = dict(version='1.0.0', release_tag=TAG, app_sha256=build_info['app_sha256'])
    return dict(manifest=manifest, build_info=build_info, runtime_info=runtime_info,
                installer=installer, payload=root, expected_tag=TAG)


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def add_updater(candidate, data=b'MZ anonymous standalone updater'):
    root = candidate['payload']
    (root / 'WorkBoxUpdater.exe').write_bytes(data)
    contents = root / 'PACKAGE_CONTENTS.sha256'
    contents.write_text(contents.read_text(encoding='utf-8') + digest(data) + '  WorkBoxUpdater.exe\n', encoding='utf-8')
    candidate['build_info']['payload_files'] += 1
    candidate['build_info']['updater_sha256'] = digest(data)


def test_historical_payload_does_not_require_new_updater(candidate):
    assert not (candidate['payload'] / 'WorkBoxUpdater.exe').exists()
    result = gate().verify_release(**candidate)
    assert result['incremental_ready'] is False


def test_new_payload_reports_verified_helper_without_executing_it(candidate):
    add_updater(candidate)
    before = snapshot(candidate['payload'].parent)
    result = gate().verify_release(**candidate)
    assert result['incremental_ready'] is True
    assert result['runtime_executed'] is False
    assert snapshot(candidate['payload'].parent) == before


@pytest.mark.parametrize('failure', ['missing-file', 'missing-report', 'wrong-hash', 'non-program'])
def test_updater_advertisement_is_complete_and_matches_actual_bytes(candidate, failure):
    if failure == 'missing-file':
        candidate['build_info']['updater_sha256'] = 'a' * 64
    else:
        add_updater(candidate, b'not executable' if failure == 'non-program' else b'MZ anonymous helper')
        if failure == 'missing-report': candidate['build_info'].pop('updater_sha256')
        elif failure == 'wrong-hash': candidate['build_info']['updater_sha256'] = '0' * 64
    with pytest.raises(ValueError):
        gate().verify_release(**candidate)


def test_valid_candidate_is_read_only_and_does_not_claim_deployment(candidate):
    tool = gate()
    root = candidate['payload'].parent
    before = snapshot(root)
    result = tool.verify_release(**candidate)
    assert result['status'] == 'passed'
    assert result['scope'] == 'offline-artifact-consistency'
    assert result['release_tag'] == TAG and result['installer_filename'] == FILENAME
    assert result['payload_files'] == 7
    assert result['runtime_executed'] is False and result['installation_verified'] is False
    assert result['source_provenance_verified'] is False
    assert str(root) not in json.dumps(result)
    assert snapshot(root) == before


@pytest.mark.parametrize('field,value', [
    ('expected_tag', 'v1.0.0.20261103.2'), ('expected_tag', 'v1.0.0.20260230'),
    ('expected_tag', 'v2.0.0.20261103'), ('expected_tag', True),
])
def test_invalid_or_mismatched_expected_build_is_rejected(candidate, field, value):
    tool = gate()
    candidate[field] = value
    with pytest.raises(ValueError): tool.verify_release(**candidate)


@pytest.mark.parametrize('field,value', [
    ('draft', True), ('draft', 0), ('prerelease', True),
    ('html_url', 'https://github.com/other/workbox/releases/tag/' + TAG),
    ('tag_name', 'v1.0.0.20261102'), ('assets', []), ('assets', 'not-an-array'),
])
def test_metadata_must_be_one_official_stable_release(candidate, field, value):
    tool = gate()
    candidate['manifest'][field] = value
    with pytest.raises(ValueError): tool.verify_release(**candidate)


@pytest.mark.parametrize('body', [True, 1, 1.5, ['notes'], {'notes': 'text'}])
def test_release_notes_rejected_by_updater_are_rejected_by_gate(candidate, body):
    from update_client import parse_release

    candidate['manifest']['body'] = body
    with pytest.raises(ValueError):
        parse_release(candidate['manifest'], current_tag='v1.0.0.20261102')
    with pytest.raises(ValueError):
        gate().verify_release(**candidate)


@pytest.mark.parametrize('body', ['release notes', '', None, False, 0, [], {}])
def test_release_notes_accepted_by_updater_are_accepted_by_gate(candidate, body):
    from update_client import parse_release

    candidate['manifest']['body'] = body
    assert parse_release(candidate['manifest'], current_tag='v1.0.0.20261102').tag == TAG
    assert gate().verify_release(**candidate)['status'] == 'passed'


def test_missing_release_notes_keep_the_updater_fallback(candidate):
    from update_client import parse_release

    candidate['manifest'].pop('body')
    assert parse_release(candidate['manifest'], current_tag='v1.0.0.20261102').tag == TAG
    assert gate().verify_release(**candidate)['status'] == 'passed'


@pytest.mark.parametrize('field,value', [
    ('size', True), ('size', 2), ('digest', 'sha256:' + '0' * 64),
    ('state', 'new'), ('name', 'WorkBox.exe'),
    ('browser_download_url', 'http://github.com/232577/workbox/download/setup.exe'),
])
def test_asset_mismatches_fail_closed(candidate, field, value):
    tool = gate()
    candidate['manifest']['assets'][0][field] = value
    with pytest.raises(ValueError): tool.verify_release(**candidate)


def test_duplicate_release_assets_are_rejected(candidate):
    tool = gate()
    candidate['manifest']['assets'].append(deepcopy(candidate['manifest']['assets'][0]))
    with pytest.raises(ValueError): tool.verify_release(**candidate)


def test_builder_compatible_empty_non_executable_file_is_allowed(candidate):
    tool = gate()
    name = '_internal/empty.marker'
    (candidate['payload'] / name).write_bytes(b'')
    with (candidate['payload'] / 'PACKAGE_CONTENTS.sha256').open('a', encoding='utf-8') as stream:
        stream.write(digest(b'') + '  ' + name + '\n')
    candidate['build_info']['payload_files'] += 1
    assert tool.verify_release(**candidate)['payload_files'] == 8


@pytest.mark.parametrize('field,value', [
    ('installer_size', True), ('installer_size', 2), ('installer_sha256', '0' * 64),
    ('app_sha256', '0' * 64), ('payload_files', True), ('payload_files', 8),
    ('contains_user_data', True), ('contains_user_data', 0),
])
def test_build_report_must_match_actual_bytes(candidate, field, value):
    tool = gate()
    candidate['build_info'][field] = value
    with pytest.raises(ValueError): tool.verify_release(**candidate)


@pytest.mark.parametrize('field,value', [
    ('version', '1.1.0'), ('release_tag', 'v1.0.0.20261102'), ('app_sha256', '0' * 64),
])
def test_stale_runtime_evidence_cannot_validate_new_release(candidate, field, value):
    tool = gate()
    candidate['runtime_info'][field] = value
    with pytest.raises(ValueError): tool.verify_release(**candidate)


@pytest.mark.parametrize('key', ['manifest', 'build_info', 'runtime_info'])
def test_non_object_inputs_are_rejected(candidate, key):
    tool = gate()
    candidate[key] = []
    with pytest.raises(ValueError): tool.verify_release(**candidate)


@pytest.mark.parametrize('action', ['tamper-installer', 'rename-installer', 'tamper-runtime',
    'missing-runtime', 'unlisted-file', 'missing-manifest', 'empty-manifest'])
def test_missing_or_changed_artifacts_are_rejected(candidate, action):
    tool = gate()
    root = candidate['payload']
    if action == 'tamper-installer': candidate['installer'].write_bytes(b'MZtampered')
    elif action == 'rename-installer':
        candidate['installer'] = candidate['installer'].rename(root.parent / 'wrong.exe')
    elif action == 'tamper-runtime': (root / 'WorkBox.exe').write_bytes(b'MZtampered')
    elif action == 'missing-runtime': (root / '_internal/python311.dll').unlink()
    elif action == 'unlisted-file': (root / 'unlisted.txt').write_text('fixture')
    elif action == 'missing-manifest': (root / 'PACKAGE_CONTENTS.sha256').unlink()
    else: (root / 'PACKAGE_CONTENTS.sha256').write_text('')
    with pytest.raises((ValueError, OSError)): tool.verify_release(**candidate)


@pytest.mark.parametrize('name', ['../outside.txt', '/absolute.txt', 'C:/outside.txt',
    'a\\b.txt', 'a//b.txt', './WorkBox.exe', 'WorkBox.exe:extra', 'workbox.exe',
    'data/workbox.db', '_internal/.env', 'licenses/trailing.txt ', 'licenses/CON.txt'])
def test_unsafe_duplicate_or_private_payload_entries_rejected(candidate, name):
    tool = gate()
    manifest = candidate['payload'] / 'PACKAGE_CONTENTS.sha256'
    with manifest.open('a', encoding='utf-8') as stream: stream.write('0' * 64 + '  ' + name + '\n')
    candidate['build_info']['payload_files'] += 1
    with pytest.raises(ValueError): tool.verify_release(**candidate)


def test_declared_hash_cannot_turn_non_executable_bytes_into_installer(candidate):
    tool = gate()
    data = b'not executable'
    candidate['installer'].write_bytes(data)
    asset = candidate['manifest']['assets'][0]
    asset.update(size=len(data), digest='sha256:' + digest(data))
    candidate['build_info'].update(installer_size=len(data), installer_sha256=digest(data))
    with pytest.raises(ValueError): tool.verify_release(**candidate)


@pytest.mark.parametrize('text', ['{"a":1,"a":2}', '[]', '{invalid', '{"value":NaN}'])
def test_strict_json_rejects_ambiguous_or_invalid_input(tmp_path, text):
    tool = gate()
    path = tmp_path / 'input.json'; path.write_text(text, encoding='utf-8')
    with pytest.raises(ValueError): tool.load_json(path)


def test_metadata_read_is_bounded(tmp_path):
    tool = gate()
    path = tmp_path / 'input.json'
    path.write_bytes(b' ' * (tool.MAX_JSON_BYTES + 1))
    with pytest.raises(ValueError): tool.load_json(path)


def test_cli_failure_is_nonzero_does_not_execute_or_change_input(candidate):
    gate()
    root = candidate['payload'].parent
    paths = {}
    for name in ('manifest', 'build_info', 'runtime_info'):
        paths[name] = root / (name + '.json')
        paths[name].write_text(json.dumps(candidate[name]), encoding='utf-8')
    command = [sys.executable, '-m', 'scripts.verify_update_release',
        '--manifest', str(paths['manifest']), '--build-info', str(paths['build_info']),
        '--runtime-info', str(paths['runtime_info']), '--installer', str(candidate['installer']),
        '--payload', str(candidate['payload']), '--expected-tag', TAG]
    before = snapshot(root)
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['status'] == 'passed'
    assert snapshot(root) == before
    candidate['installer'].write_bytes(b'MZmodified')
    before = snapshot(root)
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode != 0
    assert json.loads(result.stdout)['status'] == 'failed'
    assert str(root) not in result.stdout + result.stderr
    assert snapshot(root) == before
