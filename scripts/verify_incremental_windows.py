"""Validate a draft delta and exercise the authentic previous helper in CI."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
from unittest.mock import patch

from scripts.verify_update_release import CONTENTS, load_json
from workbox.storage.native_files import plain_path
from workbox.updates import update_client
from workbox.updates.delta_format import SHA256, delta_filename, read_manifest
from workbox.updates.incremental_apply import _copy_checked, verify_tree
from workbox.updates.update_install import delta_handoff_command


def validate_candidate_delta(candidate, info, records, *, previous_tag, expected_sha):
    """Check pinned draft asset facts, then project its stable metadata for the client."""
    target = info['release_tag']
    name = delta_filename(previous_tag, target)
    draft = load_json(candidate / 'release.json')
    assets = draft.get('assets')
    if (draft.get('tag_name') != target or draft.get('draft') is not True
            or draft.get('prerelease') is not False or not isinstance(assets, list)):
        raise ValueError('Candidate release is not the expected draft')
    stable_assets = [
        dict(asset, browser_download_url=update_client.RELEASE_ROOT + f'download/{target}/{asset["name"]}')
        for asset in assets
        if isinstance(asset, dict) and asset.get('name') in (info['installer_name'], name)
    ]
    stable = dict(tag_name=target, draft=False, prerelease=False,
                  html_url=update_client.RELEASE_ROOT + 'tag/' + target, assets=stable_assets)
    release = update_client.parse_release(stable, current_tag=previous_tag)
    if (not isinstance(expected_sha, str) or SHA256.fullmatch(expected_sha) is None
            or release is None or release.size != info['installer_size']
            or release.sha256 != info['installer_sha256'] or release.delta is None
            or release.delta.sha256 != expected_sha):
        raise ValueError('Candidate full or incremental asset identity mismatch')
    package = plain_path(candidate / name)
    manifest = read_manifest(
        package, expected_sha256=expected_sha, expected_size=release.delta.size,
        from_tag=previous_tag, to_tag=target,
    )
    target_digests = {path: fact['sha256'] for path, fact in manifest['target'].items()}
    if target_digests != {**records, CONTENTS: info['payload_manifest_sha256']}:
        raise ValueError('Delta target does not match the candidate installer manifest')
    return package, release, manifest


def _digest(path):
    with plain_path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _stop_owned_relaunch(receipt, exe):
    """Keep the exact Windows process handle while checking the fixture path."""
    pid = receipt.get('pid')
    if type(pid) is not int or not 0 < pid < 2**32:
        return
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
    ]
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    rights = 0x00100000 | 0x1000 | 1  # SYNCHRONIZE, QUERY_LIMITED_INFORMATION, TERMINATE.
    handle = kernel.OpenProcess(rights, False, pid)
    if not handle:
        error = ctypes.get_last_error()
        if error == 87:  # The owned fixture already exited.
            return
        raise ctypes.WinError(error)
    try:
        length = wintypes.DWORD(32768)
        path = ctypes.create_unicode_buffer(length.value)
        if not kernel.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(length)):
            if kernel.WaitForSingleObject(handle, 0) == 0:
                return
            raise ctypes.WinError(ctypes.get_last_error())
        if Path(path.value).resolve() != exe.resolve():
            raise ValueError('Refusing to terminate a process outside the private fixture')
        if not kernel.TerminateProcess(handle, 0) and kernel.WaitForSingleObject(handle, 0) != 0:
            raise ctypes.WinError(ctypes.get_last_error())
        if kernel.WaitForSingleObject(handle, 30_000) != 0:
            raise TimeoutError('Owned incremental fixture did not exit')
    finally:
        kernel.CloseHandle(handle)


def verify_incremental_upgrade(previous_install, output, package, release, manifest):
    """Exercise only an anonymous copy of the exact previous installed program."""
    previous_install, output = plain_path(previous_install), plain_path(output)
    if (os.name != 'nt' or os.environ.get('GITHUB_ACTIONS') != 'true'
            or os.environ.get('GITHUB_REPOSITORY') != '232577/workbox'
            or previous_install != output / 'installed'):
        raise RuntimeError('Incremental execution is restricted to the disposable runner fixture')
    verify_tree(previous_install, manifest['base'])
    app = plain_path(output / 'incremental-app')
    app.mkdir()
    for name, record in manifest['base'].items():
        _copy_checked(previous_install / name, app / name, record)
    verify_tree(app, manifest['base'])
    exe = app / 'WorkBox.exe'
    data = plain_path(output / 'incremental-data')
    data.mkdir()
    environment = dict(os.environ, QT_QPA_PLATFORM='offscreen', PYINSTALLER_RESET_ENVIRONMENT='1')
    options = dict(cwd=app, env=environment, creationflags=subprocess.CREATE_NO_WINDOW)
    subprocess.run([str(exe), '--check', '--data-dir', str(data)], check=True, timeout=45, **options)
    database = data / 'workbox.db'
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE TABLE delta_acceptance_sentinel (value TEXT NOT NULL)')
        connection.execute('INSERT INTO delta_acceptance_sentinel VALUES (?)', ('anonymous-data',))
    database_sha = _digest(database)
    material = data / 'anonymous-material.txt'
    material.write_bytes(b'Anonymous material protected through incremental update.\n')
    material_sha = _digest(material)
    cache = plain_path(output / 'incremental-cache')
    cache.mkdir()
    copied_asset = cache / package.name
    shutil.copyfile(package, copied_asset)
    parent = subprocess.Popen([str(exe), '--version'], **options)
    try:
        # The separate verifier runs from candidate source, while the installed copy
        # and selected asset have already proved the previous runtime's exact tag.
        with patch.object(update_client, 'CURRENT_TAG', release.delta.from_tag):
            command = delta_handoff_command(copied_asset, release, app, parent.pid, data)
    finally:
        try:
            if parent.wait(timeout=45) != 0:
                raise ValueError('Previous frozen fixture could not start')
        except subprocess.TimeoutExpired:
            parent.terminate()
            parent.wait(timeout=30)
            raise
    previous_helper_sha = manifest['base']['WorkBoxUpdater.exe']['sha256']
    if _digest(Path(command[0])) != previous_helper_sha:
        raise ValueError('Copied helper does not match the previous official runtime')
    completed = subprocess.run([*command, '--noninteractive'], timeout=200, **options)
    receipt = load_json(cache / 'install-result.json')
    try:
        if (completed.returncode != 0 or receipt.get('status') != 'verified-relaunch-requested'
                or receipt.get('release_tag') != release.tag
                or receipt.get('delta_sha256') != release.delta.sha256):
            raise ValueError('Previous frozen helper did not report a verified incremental restart')
        verify_tree(app, manifest['target'])
        probe = cache / 'incremental-probe.json'
        subprocess.run([str(exe), '--build-info', '--build-info-output', str(probe), '--data-dir', str(data)],
                       check=True, timeout=45, **options)
        if load_json(probe) != {'version': '1.0.0', 'release_tag': release.tag, 'data_dir': str(data)}:
            raise ValueError('Incrementally updated program identity or original data path differs')
        if _digest(material) != material_sha:
            raise ValueError('Incremental update changed anonymous material bytes')
        with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as connection:
            if (connection.execute('PRAGMA quick_check').fetchone() != ('ok',)
                    or connection.execute('SELECT value FROM delta_acceptance_sentinel').fetchall()
                    != [('anonymous-data',)]):
                raise ValueError('Incremental update did not preserve anonymous database evidence')
    finally:
        _stop_owned_relaunch(receipt, exe)
    return dict(incremental_update_verified=True, incremental_delta_sha256=release.delta.sha256,
                incremental_receipt_status=receipt['status'],
                incremental_delta_size=release.delta.size, incremental_target_inventory_verified=True,
                incremental_previous_inventory_verified=True, incremental_official_previous_helper_verified=True,
                incremental_previous_helper_sha256=previous_helper_sha,
                incremental_runtime_identity_verified=True,
                incremental_anonymous_database_and_material_preserved=True,
                incremental_anonymous_database_bytes_unchanged=_digest(database) == database_sha)
