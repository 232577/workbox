"""Anonymous boundaries for the opt-in server and packaged browser."""

import hashlib
import json
from pathlib import Path

import pytest

from scripts import build_installer as builder
from scripts import chromium_payload


SERVER_COMMIT = 'b' * 40


@pytest.fixture
def server_package(tmp_path):
    root = tmp_path / 'server'
    records = []
    for name in sorted(builder.SERVER_REQUIRED):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = ('anonymous ' + name + '\n').encode('utf-8')
        path.write_bytes(payload)
        records.append(dict(path=name, size=len(payload), sha256=hashlib.sha256(payload).hexdigest()))
    bundle = dict(format='workbox-kanboard-install/1', product='WorkBoxKanboard',
                  commit=SERVER_COMMIT, profile='access-intake-worker-v11', files=records,
                  source_archive_sha256=builder.sha256(root / 'source.tar'))
    receipt = root / builder.SERVER_MANIFEST
    receipt.write_text(json.dumps(bundle), encoding='utf-8')
    return root, builder.sha256(receipt)


def test_server_inventory_reuses_only_the_exact_verified_program(server_package):
    root, digest = server_package
    bundle, files = builder.inspect_team_server(root, SERVER_COMMIT, digest)
    assert bundle['commit'] == SERVER_COMMIT
    assert files[builder.SERVER_MANIFEST][1] == digest
    assert builder.SERVER_REQUIRED <= files.keys()
    assert 100 <= builder.team_server_path_limit(files, digest[:16]) < 240


@pytest.mark.parametrize('change', ['commit', 'manifest', 'missing', 'changed', 'extra'])
def test_server_input_rejects_mismatched_provenance_and_programs(server_package, change):
    root, digest = server_package
    commit = SERVER_COMMIT
    if change == 'commit':
        commit = 'a' * 40
    elif change == 'manifest':
        digest = 'c' * 64
    elif change == 'missing':
        (root / 'filee/workbox-filee-worker.exe').unlink()
    elif change == 'changed':
        (root / 'filee/workbox-filee-worker.exe').write_bytes(b'changed')
    else:
        (root / 'anonymous-private.db').write_bytes(b'not program data')
    with pytest.raises(ValueError):
        builder.inspect_team_server(root, commit, digest)


def test_chromium_staging_refuses_unpinned_archive_without_output(tmp_path, monkeypatch):
    license_file = tmp_path / 'Chromium-LICENSE.txt'
    license_file.write_bytes(b'anonymous license fixture')
    monkeypatch.setattr(chromium_payload, 'LICENSE_SHA256', builder.sha256(license_file))
    archive = tmp_path / 'browser.zip'
    archive.write_bytes(b'anonymous unpinned archive')
    output = tmp_path / 'Chromium'
    with pytest.raises(ValueError, match='Chromium ZIP'):
        chromium_payload.stage_chromium(tmp_path, archive, output)
    assert not output.exists()
