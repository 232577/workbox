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


def test_old_installed_payload_is_allowed_but_new_source_requires_helper(tmp_path):
    app = tmp_path / 'WorkBox.exe'; app.write_bytes(b'MZ anonymous application')
    sha = hashlib.sha256(app.read_bytes()).hexdigest()
    manifest = f'{sha}  WorkBox.exe\n'
    assert acceptance().verify_installed_payload(tmp_path, manifest, sha) == 1
    with pytest.raises(ValueError, match='updater'):
        acceptance().verify_installed_payload(tmp_path, manifest, sha, require_updater=True)


@pytest.mark.parametrize('failure', [None, 'hash', 'non-program'])
def test_new_installed_payload_verifies_helper_hash_and_executable_header(tmp_path, failure):
    app = tmp_path / 'WorkBox.exe'; app.write_bytes(b'MZ anonymous application')
    helper = tmp_path / 'WorkBoxUpdater.exe'
    helper.write_bytes(b'not executable' if failure == 'non-program' else b'MZ anonymous helper')
    sha = hashlib.sha256(app.read_bytes()).hexdigest()
    helper_sha = hashlib.sha256(helper.read_bytes()).hexdigest()
    manifest = f'{sha}  WorkBox.exe\n{helper_sha}  WorkBoxUpdater.exe\n'
    if failure == 'hash': helper.write_bytes(b'MZ changed helper')
    if failure:
        with pytest.raises(ValueError):
            acceptance().verify_installed_payload(tmp_path, manifest, sha, require_updater=True)
    else:
        assert acceptance().verify_installed_payload(tmp_path, manifest, sha, require_updater=True) == 2


@pytest.mark.parametrize('output,expected', [('', False),
    ('100644 blob ' + 'b' * 40 + '\tupdate_helper_main.py\n', True)])
def test_source_capability_reads_exact_commit_tree_without_working_tree_guess(monkeypatch, output, expected):
    calls = []
    def git(command, **options):
        calls.append((command, options))
        return output
    monkeypatch.setattr(acceptance().subprocess, 'check_output', git)
    assert acceptance().source_requires_updater('a' * 40) is expected
    assert calls[0][0] == ['git', 'ls-tree', 'a' * 40, '--', 'update_helper_main.py']


@pytest.mark.parametrize('output', ['unparseable', '120000 blob ' + 'b' * 40 + '\tupdate_helper_main.py\n',
    '100644 blob ' + 'b' * 40 + '\tunrelated.py\n'])
def test_ambiguous_source_capability_never_silently_becomes_legacy(monkeypatch, output):
    monkeypatch.setattr(acceptance().subprocess, 'check_output', lambda *a, **k: output)
    with pytest.raises(ValueError):
        acceptance().source_requires_updater('a' * 40)


def test_updater_self_check_uses_explicit_read_only_flag_and_checks_exit(tmp_path, monkeypatch):
    from types import SimpleNamespace
    helper = tmp_path / 'WorkBoxUpdater.exe'; helper.write_bytes(b'MZ anonymous helper, never executed')
    expected = hashlib.sha256(helper.read_bytes()).hexdigest()
    calls = []
    def run(command, **options):
        calls.append((command, options))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(acceptance().subprocess, 'run', run)
    acceptance().verify_updater_self_check(tmp_path, expected)
    assert calls[0][0] == [str(helper), '--self-check', '--noninteractive']
    assert calls[0][1].get('shell', False) is False and calls[0][1]['timeout'] <= 45
    monkeypatch.setattr(acceptance().subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=1))
    with pytest.raises(ValueError):
        acceptance().verify_updater_self_check(tmp_path, expected)


@pytest.mark.parametrize('failure', ['missing-helper', 'not-main'])
def test_candidate_cli_checks_exact_source_before_any_installer_execution(tmp_path, monkeypatch, failure):
    import json
    from types import SimpleNamespace
    tool = acceptance()
    source = 'a' * 40
    tag = 'v1.0.0.20261113.3'
    candidate = tmp_path / 'candidate'; candidate.mkdir()
    previous = tmp_path / 'previous'; previous.mkdir()
    name = 'WorkBox-1.0.0-Setup-x64-20261113-r3.exe'
    installer = candidate / name; installer.write_bytes(b'MZ anonymous inert installer, never executed')
    sha = hashlib.sha256(installer.read_bytes()).hexdigest()
    manifest = candidate / 'PACKAGE_CONTENTS.sha256'
    manifest.write_text('b' * 64 + '  WorkBox.exe\n', encoding='utf-8')
    (candidate / 'BUILD_PROVENANCE.json').write_text(json.dumps(dict(release_tag=tag, source_commit=source,
        installer_sha256=sha, installer_name=name, payload_manifest_sha256=tool.digest(manifest),
        app_sha256='b' * 64, payload_files=1)), encoding='utf-8')
    monkeypatch.setenv('GITHUB_ACTIONS', 'true')
    monkeypatch.setenv('GITHUB_REPOSITORY', '232577/workbox')
    monkeypatch.setattr(tool, 'registration_exists', lambda: False)
    calls = []
    def git(command, **options):
        calls.append(command)
        if command == ['git', 'rev-parse', 'HEAD']: return source
        if command == ['git', 'ls-tree', source, '--', 'update_helper_main.py']:
            return '100644 blob ' + 'c' * 40 + '\tupdate_helper_main.py\n'
        pytest.fail('unexpected read command')
    def run(command, **options):
        calls.append(command)
        assert command == ['git', 'merge-base', '--is-ancestor', source, 'origin/main']
        if failure == 'not-main':
            raise tool.subprocess.CalledProcessError(1, command)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(tool.subprocess, 'check_output', git)
    monkeypatch.setattr(tool.subprocess, 'run', run)
    monkeypatch.setattr(tool, 'install', lambda *a, **k: pytest.fail('must not execute any installer'))
    error = ValueError if failure == 'missing-helper' else tool.subprocess.CalledProcessError
    with pytest.raises(error):
        tool.main(['--candidate-dir', str(candidate), '--previous-dir', str(previous), '--expected-tag', tag,
                   '--previous-tag', 'v1.0.0.20261113.2', '--expected-sha', sha,
                   '--expected-source', source, '--output', str(tmp_path / 'result')])
    assert calls[:2] == [['git', 'rev-parse', 'HEAD'], ['git', 'merge-base', '--is-ancestor', source, 'origin/main']]
    assert len(calls) == (3 if failure == 'missing-helper' else 2)


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


def test_draft_download_permissions_are_isolated_from_installer_execution():
    from pathlib import Path
    import re
    workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/installer-acceptance.yml').read_text(encoding='utf-8')
    jobs = dict(re.findall(r'^  (download|verify):\n(.*?)(?=^  \w+:\n|\Z)', workflow, re.MULTILINE | re.DOTALL))
    assert set(jobs) == {'download', 'verify'}
    assert 'contents: write' in jobs['download']
    assert 'runs-on: ubuntu-latest' in jobs['download']
    assert 'verify_installer_windows' not in jobs['download']
    assert 'contents: read' in jobs['verify'] and 'contents: write' not in jobs['verify']
    assert 'needs: download' in jobs['verify']
    assert 'GH_TOKEN:' not in jobs['verify']
    assert 'actions/download-artifact@' in jobs['verify']
