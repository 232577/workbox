"""Reject untrusted identities and altered payloads before installer acceptance."""
import hashlib
import importlib.util
import pytest


def acceptance():
    assert importlib.util.find_spec('scripts.verify_installer_windows'), 'Installer acceptance helper is missing'
    from scripts import verify_installer_windows
    return verify_installer_windows


def test_installer_acceptance_rejects_identity_mismatch():
    with pytest.raises(ValueError, match='identity'):
        acceptance().validate_identity('v1.0.0.20261112.3', 'a' * 64, 'b' * 40,
            {'release_tag': 'v1.0.0.20261112.2', 'installer_sha256': 'a' * 64, 'source_commit': 'b' * 40})


def test_installer_acceptance_checks_actual_installed_payload_bytes(tmp_path):
    app = tmp_path / 'WorkBox.exe'
    app.write_bytes(b'MZ anonymous fixture')
    digest = hashlib.sha256(app.read_bytes()).hexdigest()
    manifest = f'{digest}  WorkBox.exe\n'
    assert acceptance().verify_installed_payload(tmp_path, manifest, digest) == 1
    app.write_bytes(b'MZ altered fixture')
    with pytest.raises(ValueError, match='digest'):
        acceptance().verify_installed_payload(tmp_path, manifest, digest)


@pytest.mark.parametrize('name', ['../outside', 'C:/outside', 'data/workbox.db', 'WorkBox.exe/../outside'])
def test_installer_acceptance_rejects_unsafe_manifest_paths(tmp_path, name):
    with pytest.raises(ValueError):
        acceptance().verify_installed_payload(tmp_path, 'a' * 64 + '  ' + name + '\n', 'a' * 64)


def test_installer_cli_refuses_a_workstation_without_creating_output(tmp_path, monkeypatch):
    monkeypatch.delenv('GITHUB_ACTIONS', raising=False)
    output = tmp_path / 'must-not-exist'
    with pytest.raises(RuntimeError, match='disposable'):
        acceptance().main(['--candidate-dir', str(tmp_path), '--previous-dir', str(tmp_path),
            '--expected-tag', 'v1.0.0.20261112.3', '--previous-tag', 'v1.0.0.20261112.2',
            '--expected-sha', 'a' * 64, '--expected-source', 'b' * 40, '--output', str(output)])
    assert not output.exists()
