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

from scripts.verify_update_release import CONTENTS, payload_name
from workbox.storage.native_files import plain_path
from workbox.updates.update_client import build_key

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


def _manifest_records(manifest: str) -> dict:
    records, seen = {}, set()
    for line in manifest.splitlines():
        match = re.fullmatch(r'([0-9a-f]{64})  (.+)', line)
        if match is None:
            raise ValueError('Malformed payload manifest')
        expected, name = match.groups()
        name = payload_name(name)
        if name.casefold() in seen:
            raise ValueError('Duplicate payload path')
        seen.add(name.casefold())
        records[name] = expected
    return records


def verify_installed_payload(root: Path, manifest: str, app_sha: str, *, require_updater=False) -> int:
    from workbox.updates.incremental_apply import _inventory

    root = plain_path(root)
    records = _manifest_records(manifest)
    # 与后续增量基线使用相同程序边界；清单自身不列入清单，data/卸载项/用户根文件不参与。
    if _inventory(root) - {CONTENTS} != set(records):
        raise ValueError('Installed program inventory differs from payload manifest')
    for name, expected in records.items():
        path = plain_path(root / name)
        if not path.is_relative_to(root) or digest(path) != expected:
            raise ValueError('Installed payload digest mismatch')
    if 'WorkBox.exe' not in records or digest(root / 'WorkBox.exe') != app_sha:
        raise ValueError('Installed application digest mismatch')
    helpers = [name for name in records if name.casefold() == 'workboxupdater.exe']
    if require_updater or helpers:
        if helpers != ['WorkBoxUpdater.exe']:
            raise ValueError('Installed updater is missing or has an ambiguous name')
        with plain_path(root / 'WorkBoxUpdater.exe').open('rb') as stream:
            if stream.read(2) != b'MZ':
                raise ValueError('Installed updater is not a Windows executable')
    return len(records)


def verify_runtime_history(root: Path, obsolete: dict, manifest_sha256: str) -> dict[Path, str]:
    """核对旧、新清单差集的可恢复保留，并返回卸载后必须保留的记录及文件摘要。"""
    from workbox.updates import installer_runtime as runtime

    root = plain_path(root)
    if runtime._histories(root, manifest_sha256):
        raise ValueError('Unfinished obsolete runtime history')
    protected, recovered = {}, {}
    history = plain_path(root / runtime.HISTORY)
    if history.exists():
        for transaction in history.iterdir():
            if re.fullmatch(r'\.prepare-[a-f0-9]{32}', transaction.name):
                # 未发布目录只保留有限普通元数据的字节证据，不解析为有效恢复计划。
                for count, path in enumerate(transaction.iterdir(), 1):
                    if count > runtime.MAX_HISTORY + 3:
                        raise ValueError('Unpublished runtime metadata exceeds acceptance limit')
                    protected[path] = runtime._bounded_record(path, runtime.fmt.MAX_MANIFEST_SIZE)['sha256']
                continue
            plan = runtime._read_json(transaction / 'plan.json')
            for name, entry in plan['files'].items():
                protected[transaction / 'files' / name] = entry['sha256']
                if plan['manifest_sha256'] == manifest_sha256:
                    if name in recovered:
                        raise ValueError('Duplicate obsolete runtime history record')
                    recovered[name] = entry['sha256']
            for name in ('plan.json', 'receipt.json'):
                path = transaction / name
                protected[path] = digest(path)
            for path in transaction.glob('.pending-receipt-*'):
                protected[path] = runtime._bounded_record(path, runtime.fmt.MAX_MANIFEST_SIZE)['sha256']
    if recovered != obsolete:
        raise ValueError('Obsolete runtime history differs from previous payload')
    return protected


def source_requires_updater(source: str) -> bool:
    """仅以已核验提交的树判断能力，不读取工作树的新增或缺失文件。"""
    if not isinstance(source, str) or not re.fullmatch(r'[0-9a-f]{40}', source):
        raise ValueError('Invalid source commit identity')
    paths = ('update_helper_main.py', 'workbox/updates/update_helper_main.py')
    output = subprocess.check_output(['git', 'ls-tree', source, '--', *paths], text=True, encoding='utf-8')
    if not output.strip():
        return False
    match = re.fullmatch(r'100(?:644|755) blob [0-9a-f]{40}\t([^\n]+)\n?', output)
    if match is None or match[1] not in paths:
        raise ValueError('Ambiguous updater capability in exact source tree')
    return True


def verify_updater_self_check(root: Path, expected_sha256: str) -> None:
    helper = plain_path(root / 'WorkBoxUpdater.exe')
    if (not isinstance(expected_sha256, str) or not re.fullmatch(r'[0-9a-f]{64}', expected_sha256)
            or digest(helper) != expected_sha256):
        raise ValueError('Updater digest mismatch before self-check')
    with helper.open('rb') as stream:
        if stream.read(2) != b'MZ':
            raise ValueError('Updater is not a Windows executable')
    result = subprocess.run([str(helper), '--self-check', '--noninteractive'],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        timeout=45, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    if result.returncode != 0 or digest(helper) != expected_sha256:
        raise ValueError('Updater self-check failed or executable changed')


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


def _verify_legacy_busy_upgrade(installer: Path, target: Path, manifest: str,
                                app_sha: str, output: Path) -> dict:
    """仅供已验证的隔离 runner 主流程调用；不用于真实安装或原生 UI 验收。"""
    target, output = plain_path(target), plain_path(output)
    if target != output / 'installed':
        raise ValueError('Legacy busy fixture must belong to this disposable output')
    verify_installed_payload(target, manifest, app_sha)
    manifest_sha = digest(target / CONTENTS)
    data = output / 'legacy-busy-data'
    data.mkdir(exist_ok=False)
    environment = dict(os.environ, QT_QPA_PLATFORM='offscreen', PYINSTALLER_RESET_ENVIRONMENT='1')
    terminated = False
    with (output / 'legacy-busy-process.log').open('xb') as process_log:
        process = subprocess.Popen([str(target / 'WorkBox.exe'), '--panel', '--no-tray', '--data-dir', str(data)],
            cwd=target, env=environment, shell=False, close_fds=True,
            stdin=subprocess.DEVNULL, stdout=process_log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            if process.poll() is not None:
                raise ValueError('Legacy busy fixture exited before the upgrade attempt')
            exit_code = 0
            try:
                install(installer, target, output / 'legacy-busy-install.log', upgrade=True)
            except subprocess.CalledProcessError as error:
                exit_code = error.returncode
            # Inno Setup: 7 means Preparing to Install refused to proceed.
            # https://jrsoftware.org/inno-setup/documentation/topic_setupexitcodes.htm
            if exit_code != 7:
                raise ValueError('Busy legacy upgrade did not stop at Preparing to Install: ' + str(exit_code))
            verify_installed_payload(target, manifest, app_sha)
            if digest(target / CONTENTS) != manifest_sha:
                raise ValueError('Busy legacy upgrade changed the installed manifest')
            if process.poll() is not None:
                raise ValueError('Busy upgrade stopped the legacy fixture process')
        finally:
            # Only this Popen-owned anonymous fixture is terminated, never a name/PID search.
            if process.poll() is None:
                process.terminate()
                terminated = True
            process.wait(timeout=30)
    return dict(legacy_busy_upgrade_refused=True, legacy_busy_installer_exit_code=exit_code,
        legacy_busy_program_bytes_preserved=True, legacy_busy_fixture_ui_verified=False,
        legacy_busy_fixture_cleanup='owned-process-terminate-and-wait' if terminated else 'owned-process-wait')


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
    subprocess.run(['git', 'merge-base', '--is-ancestor', source, 'origin/main'], check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    require_updater = source_requires_updater(source)
    date, revision = build_key(args.expected_tag)
    filename = f'WorkBox-1.0.0-Setup-x64-{date}' + (f'-r{revision}' if revision else '') + '.exe'
    installer = plain_path(candidate / filename)
    if info.get('installer_name') != filename or digest(installer) != args.expected_sha:
        raise ValueError('Candidate installer digest mismatch')
    manifest_file = candidate / 'PACKAGE_CONTENTS.sha256'
    if digest(manifest_file) != info['payload_manifest_sha256']:
        raise ValueError('Candidate manifest digest mismatch')
    manifest = manifest_file.read_text(encoding='utf-8')
    records = _manifest_records(manifest)
    if require_updater and 'WorkBoxUpdater.exe' not in records:
        raise ValueError('New source requires an updater in the candidate manifest')
    if ('updater_sha256' in info and (not isinstance(info['updater_sha256'], str)
            or not re.fullmatch(r'[0-9a-f]{64}', info['updater_sha256'])
            or info['updater_sha256'] != records.get('WorkBoxUpdater.exe'))):
        raise ValueError('Candidate updater digest evidence mismatch')
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
    old_manifest = (target / CONTENTS).read_text(encoding='utf-8')
    old_records = _manifest_records(old_manifest)
    verify_installed_payload(target, old_manifest, old_records.get('WorkBox.exe', ''))
    obsolete = {name: value for name, value in old_records.items() if name not in records}
    legacy_busy = _verify_legacy_busy_upgrade(installer, target, old_manifest, old_records['WorkBox.exe'], out)
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
    count = verify_installed_payload(target, manifest, info['app_sha256'], require_updater=require_updater)
    assert count == info['payload_files']
    runtime_history = verify_runtime_history(target, obsolete, info['payload_manifest_sha256'])
    protected.update(runtime_history)
    if require_updater:
        verify_updater_self_check(target, records['WorkBoxUpdater.exe'])
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
    assert all(digest(path) == value for path, value in protected.items()), 'Uninstaller changed anonymous data or runtime history'
    assert verify_runtime_history(target, obsolete, info['payload_manifest_sha256']) == runtime_history, 'Runtime history changed on uninstall'
    report = {'status': 'passed', 'scope': 'exact installer on disposable Windows runner',
        'source_commit': source, 'release_tag': args.expected_tag, 'previous_tag': args.previous_tag,
        'installer_sha256': args.expected_sha, 'app_sha256': info['app_sha256'], 'payload_files': count,
        'fresh_previous_install': True, 'candidate_upgrade': True, 'runtime_identity_verified': True,
        'uninstall_verified': True, 'anonymous_database_and_material_preserved': True,
        'exact_program_inventory_verified': True, 'obsolete_runtime_recoverably_preserved': True,
        'retired_runtime_files': len(obsolete), 'runtime_history_preserved_on_uninstall': True,
        'initial_and_final_app_registration_absent': True, 'production_data_accessed': False,
        'updater_required_by_source': require_updater, 'updater_self_check_verified': require_updater,
        'physical_device_claimed': False}
    report.update(legacy_busy)
    (out / 'acceptance.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
