"""Downloaded draft delta identity is checked before installer processes run."""
import hashlib
import json

import pytest

from scripts.build_incremental_update import build_delta
from scripts.verify_incremental_windows import validate_candidate_delta, verify_incremental_upgrade
from workbox.updates.delta_format import REQUIRED_FILES, delta_filename


PREVIOUS = 'v1.0.0.20261123.1'
TARGET = 'v1.0.0.20261124.2'
SOURCE = 'a' * 40


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write_payload(root, executable):
    facts = {}
    for name in sorted(REQUIRED_FILES - {'PACKAGE_CONTENTS.sha256'}):
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        content = executable if name == 'WorkBox.exe' else b'anonymous unchanged runtime'
        target.write_bytes(content)
        facts[name] = sha(content)
    (root / 'PACKAGE_CONTENTS.sha256').write_text(
        ''.join(f'{digest}  {name}\n' for name, digest in facts.items()), encoding='utf-8',
    )
    return facts


@pytest.fixture
def candidate_delta(tmp_path):
    candidate = tmp_path / 'candidate'
    base = tmp_path / 'base'
    target = tmp_path / 'target'
    write_payload(base, b'MZ old')
    records = write_payload(target, b'MZ target')
    package = candidate / delta_filename(PREVIOUS, TARGET)
    result = build_delta(base, target, package, from_tag=PREVIOUS, to_tag=TARGET)
    info = {
        'release_tag': TARGET,
        'source_commit': SOURCE,
        'installer_name': 'WorkBox-1.0.0-Setup-x64-20261124-r2.exe',
        'installer_sha256': '1' * 64,
        'installer_size': result['size'] + 500,
        'payload_manifest_sha256': sha((target / 'PACKAGE_CONTENTS.sha256').read_bytes()),
    }
    assets = [
        {'name': info['installer_name'], 'size': info['installer_size'],
         'digest': 'sha256:' + info['installer_sha256'], 'state': 'uploaded'},
        {'name': package.name, 'size': result['size'],
         'digest': 'sha256:' + result['sha256'], 'state': 'uploaded'},
    ]
    draft = dict(tag_name=TARGET, target_commitish=SOURCE, draft=True, prerelease=False, assets=assets)
    return package, result, info, records, draft


def verify(candidate_delta, *, expected=None):
    package, result, info, records, draft = candidate_delta
    (package.parent / 'release.json').write_text(json.dumps(draft), encoding='utf-8')
    return validate_candidate_delta(
        package.parent, info, records, previous_tag=PREVIOUS,
        expected_sha=expected if expected is not None else result['sha256'],
    )


def test_draft_metadata_selects_exact_previous_asset(candidate_delta):
    package, _, _, _, draft = candidate_delta
    unrelated = delta_filename('v1.0.0.20261124.1', TARGET)
    draft['assets'].append(dict(draft['assets'][1], name=unrelated))
    selected, release, manifest = verify(candidate_delta)
    assert selected == package
    assert release.delta.filename == package.name
    assert manifest['from_tag'] == PREVIOUS and manifest['to_tag'] == TARGET


@pytest.mark.parametrize('change', ['wrong-pin', 'duplicate', 'pending', 'wrong-size',
                                    'unexpected-tag', 'unexpected-source', 'not-draft', 'no-full-fallback',
                                    'wrong-target-manifest', 'wrong-target-byte'])
def test_draft_delta_checks_fail_closed(candidate_delta, change):
    _, _, info, records, draft = candidate_delta
    expected = None
    if change == 'wrong-pin':
        expected = '2' * 64
    elif change == 'duplicate':
        draft['assets'].append(draft['assets'][1].copy())
    elif change == 'pending':
        draft['assets'][1]['state'] = 'new'
    elif change == 'wrong-size':
        draft['assets'][1]['size'] += 1
    elif change == 'unexpected-tag':
        draft['tag_name'] = PREVIOUS
    elif change == 'unexpected-source':
        draft['target_commitish'] = 'b' * 40
    elif change == 'not-draft':
        draft['draft'] = False
    elif change == 'no-full-fallback':
        draft['assets'].pop(0)
    elif change == 'wrong-target-manifest':
        info['payload_manifest_sha256'] = '3' * 64
    else:
        records['WorkBox.exe'] = '4' * 64
    with pytest.raises(ValueError):
        verify(candidate_delta, expected=expected)


def test_incremental_runtime_fixture_will_not_run_on_workstation(tmp_path, monkeypatch):
    monkeypatch.delenv('GITHUB_ACTIONS', raising=False)
    with pytest.raises(RuntimeError, match='disposable runner'):
        verify_incremental_upgrade(tmp_path / 'installed', tmp_path, None, None, None)
    assert not list(tmp_path.iterdir())
