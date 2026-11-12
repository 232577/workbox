"""Verify exact draft installer bytes on a clean, disposable Windows CI runner.

Never invoke installation on a workstation: the CLI requires GitHub Actions,
the intended repository and absence of the stable app registration in both hives.
Only anonymous fixtures are created. This is not a production updater.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import time

from native_files import plain_path
from scripts.verify_update_release import payload_name
from update_client import build_key

APP_KEY = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\{94062298-8991-4FF2-9E56-6B837B20A5FD}_is1'


def digest(path: Path) -> str:
    with plain_path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def validate_identity(tag: str, installer_sha: str, source: str, info: dict) -> None:
    build_key(tag)
    if (not re.fullmatch(r'[0-9a-f]{64}', installer_sha)
            or not re.fullmatch(r'[0-9a-f]{40}', source)
            or info.get('release_tag') != tag or info.get('installer_sha256') != installer_sha
            or info.get('source_commit') != source):
        raise ValueError('Candidate identity mismatch')


def verify_installed_payload(root: Path, manifest: str, app_sha: str) -> int:
    root = plain_path(root)
    seen = set()
    for line in manifest.splitlines():
        match = re.fullmatch(r'([0-9a-f]{64})  (.+)', line)
        if match is None:
            raise ValueError('Malformed payload manifest')
        expected, name = match.groups()
        name = payload_name(name)
        if name.casefold() in seen:
            raise ValueError('Duplicate payload path')
        seen.add(name.casefold())
        path = plain_path(root / name)
        if not path.is_relative_to(root) or digest(path) != expected:
            raise ValueError('Installed payload digest mismatch')
    if 'workbox.exe' not in seen or digest(root / 'WorkBox.exe') != app_sha:
        raise ValueError('Installed application digest mismatch')
    return len(seen)


def registration_exists() -> bool:
    import winreg
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, APP_KEY, 0, winreg.KEY_READ | view):
                    return True
            except FileNotFoundError:
                pass
    return False


def probe(exe: Path, destination: Path) -> dict:
    subprocess.run([str(exe), '--build-info', '--build-info-output', str(destination)],
                   check=True, timeout=45)
    return json.loads(destination.read_text(encoding='utf-8'))


def install(installer: Path, target: Path, log: Path, *, upgrade=False) -> None:
    command = [str(installer), '/SP-', '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
               '/NOCLOSEAPPLICATIONS', '/NORESTARTAPPLICATIONS', '/NOICONS', '/TASKS=',
               '/DIR=' + str(target), '/LOG=' + str(log)]
    if upgrade:
        command.append('/WORKBOXUPDATE=1')
    subprocess.run(command, check=True, timeout=240)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate-dir', type=Path, required=True)
    parser.add_argument('--previous-dir', type=Path, required=True)
    parser.add_argument('--expected-tag', required=True)
    parser.add_argument('--previous-tag', required=True)
    parser.add_argument('--expected-sha', required=True)
    parser.add_argument('--expected-source', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    if (os.name != 'nt' or os.environ.get('GITHUB_ACTIONS') != 'true'
            or os.environ.get('GITHUB_REPOSITORY') != '232577/workbox'):
        raise RuntimeError('Installer execution is restricted to disposable repository CI runners')
    if registration_exists():
        raise RuntimeError('Existing WorkBox installation registration; refusing to change it')
    candidate = plain_path(args.candidate_dir.resolve())
    previous = plain_path(args.previous_dir.resolve())
    out = plain_path(args.output.resolve())
    out.mkdir(parents=True, exist_ok=False)
    source = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    if source != args.expected_source:
        raise ValueError('Checked-out source identity mismatch')
    info = json.loads((candidate / 'BUILD_PROVENANCE.json').read_text(encoding='utf-8'))
    validate_identity(args.expected_tag, args.expected_sha, source, info)
    date, revision = build_key(args.expected_tag)
    filename = f'WorkBox-1.0.0-Setup-x64-{date}' + (f'-r{revision}' if revision else '') + '.exe'
    installer = plain_path(candidate / filename)
    if info.get('installer_name') != filename or digest(installer) != args.expected_sha:
        raise ValueError('Candidate installer digest mismatch')
    manifest_file = candidate / 'PACKAGE_CONTENTS.sha256'
    if digest(manifest_file) != info['payload_manifest_sha256']:
        raise ValueError('Candidate manifest digest mismatch')
    manifest = manifest_file.read_text(encoding='utf-8')
    old = json.loads((previous / 'release.json').read_text(encoding='utf-8-sig'))
    if (old['tag_name'] != args.previous_tag or old['draft'] or old['prerelease']
            or build_key(args.previous_tag) >= build_key(args.expected_tag)):
        raise ValueError('Invalid previous stable release')
    old_date, old_revision = build_key(args.previous_tag)
    old_name = f'WorkBox-1.0.0-Setup-x64-{old_date}' + (f'-r{old_revision}' if old_revision else '') + '.exe'
    old_asset = next(a for a in old['assets'] if a['name'] == old_name)
    old_installer = plain_path(previous / old_name)
    if (old_asset['digest'] != 'sha256:' + digest(old_installer)
            or old_asset['size'] != old_installer.stat().st_size):
        raise ValueError('Previous installer digest mismatch')
    target = out / 'installed'
    install(old_installer, target, out / 'previous-install.log')
    if probe(target / 'WorkBox.exe', out / 'previous-probe.json')['release_tag'] != args.previous_tag:
        raise ValueError('Previous runtime identity mismatch')
    data = target / 'data'
    subprocess.run([str(target / 'WorkBox.exe'), '--check', '--data-dir', str(data)], check=True, timeout=45)
    database = data / 'workbox.db'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE release_acceptance_sentinel (key TEXT PRIMARY KEY, value BLOB NOT NULL)')
        db.execute('INSERT INTO release_acceptance_sentinel VALUES (?, ?)', ('anonymous', b'preserve-exactly'))
        assert db.execute('PRAGMA quick_check').fetchone() == ('ok',)
    sentinel = data / 'anonymous-material.txt'
    sentinel.write_bytes(b'Anonymous material must survive installer upgrade and uninstall.\n')
    protected = {database: digest(database), sentinel: digest(sentinel)}
    install(installer, target, out / 'candidate-install.log', upgrade=True)
    assert all(digest(path) == value for path, value in protected.items()), 'Installer changed anonymous data'
    count = verify_installed_payload(target, manifest, info['app_sha256'])
    assert count == info['payload_files']
    runtime = probe(target / 'WorkBox.exe', out / 'candidate-probe.json')
    assert runtime['release_tag'] == args.expected_tag and runtime['version'] == '1.0.0'
    assert Path(runtime['data_dir']).resolve() == data.resolve()
    uninstaller = target / 'unins000.exe'
    subprocess.run([str(uninstaller), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
                    '/LOG=' + str(out / 'uninstall.log')], check=True, timeout=120)
    deadline = time.monotonic() + 30
    while registration_exists() and time.monotonic() < deadline:
        time.sleep(.25)
    assert not registration_exists(), 'Anonymous uninstall registration remains'
    assert all(digest(path) == value for path, value in protected.items()), 'Uninstaller changed anonymous data'
    report = {'status': 'passed', 'scope': 'exact installer on disposable Windows runner',
        'source_commit': source, 'release_tag': args.expected_tag, 'previous_tag': args.previous_tag,
        'installer_sha256': args.expected_sha, 'app_sha256': info['app_sha256'], 'payload_files': count,
        'fresh_previous_install': True, 'candidate_upgrade': True, 'runtime_identity_verified': True,
        'uninstall_verified': True, 'anonymous_database_and_material_preserved': True,
        'initial_and_final_app_registration_absent': True, 'production_data_accessed': False,
        'physical_device_claimed': False}
    (out / 'acceptance.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
