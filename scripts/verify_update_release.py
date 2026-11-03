"""Offline, read-only consistency checks; never execute or publish an installer."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys

from native_files import plain_path
from scripts.build_installer import PRIVATE_NAMES, PRIVATE_SUFFIXES, REQUIRED_RUNTIME
from update_client import MAX_INSTALLER, RELEASE_ROOT, build_key

MAX_JSON_BYTES = 1024 * 1024
MAX_PAYLOAD_FILES = 20000
MAX_PAYLOAD_BYTES = 1024 * 1024 * 1024
CONTENTS = 'PACKAGE_CONTENTS.sha256'
RESERVED_NAME = re.compile(r'(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', re.I)


class ReleaseGateError(ValueError):
    """A rejected candidate; messages must not contain private absolute paths."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ReleaseGateError(message)


def regular_file(path: Path) -> Path:
    path = plain_path(path)
    require(stat.S_ISREG(path.stat().st_mode), 'Input must be a regular local file.')
    return path


def bounded_bytes(path: Path, limit: int) -> bytes:
    path = regular_file(path)
    with path.open('rb') as stream:
        content = stream.read(limit + 1)
    require(len(content) <= limit, 'Input exceeds its size limit.')
    return content


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        require(key not in result, 'Duplicate JSON key.')
        result[key] = value
    return result


def _invalid_constant(_value: str):
    raise ReleaseGateError('Non-finite JSON number is not allowed.')


def load_json(path: Path) -> dict:
    try:
        data = json.loads(bounded_bytes(path, MAX_JSON_BYTES).decode('utf-8-sig'),
                          object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise ReleaseGateError('Invalid JSON evidence.') from error
    require(isinstance(data, dict), 'Evidence must be a JSON object.')
    return data


def _identity(info: os.stat_result) -> tuple:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def file_facts(path: Path, limit: int = MAX_INSTALLER) -> tuple[int, str, bytes]:
    """Measure bytes and reject a file changing during its own bounded read."""
    path = regular_file(path)
    before = path.stat()
    require(0 <= before.st_size <= limit, 'Artifact size is outside the allowed range.')
    digest, count, first = hashlib.sha256(), 0, b''
    with path.open('rb') as stream:
        opened = os.fstat(stream.fileno())
        require(_identity(before) == _identity(opened), 'Artifact changed while opening.')
        while block := stream.read(1024 * 1024):
            if not count:
                first = block[:2]
            count += len(block)
            require(count <= limit, 'Artifact grew beyond the allowed size.')
            digest.update(block)
        after = os.fstat(stream.fileno())
    require(count == before.st_size and _identity(before) == _identity(after)
            and _identity(before) == _identity(path.stat()), 'Artifact changed while reading.')
    return count, digest.hexdigest(), first


def payload_name(name: str) -> str:
    require(isinstance(name, str) and 0 < len(name) <= 1024, 'Invalid payload entry.')
    require(not re.search(r'[\\:\x00-\x1f<>"|?*]', name), 'Unsafe payload entry.')
    path = PurePosixPath(name)
    require(not path.is_absolute() and path.as_posix() == name, 'Non-canonical payload entry.')
    require(all(p not in {'.', '..'} and p.strip(' .') == p
                and RESERVED_NAME.fullmatch(p) is None for p in path.parts),
            'Unsafe Windows payload entry.')
    require(not any(p.casefold() in PRIVATE_NAMES | {'.tmp', '.venv'} for p in path.parts)
            and path.suffix.casefold() not in PRIVATE_SUFFIXES,
            'Payload contains a private-data or configuration path.')
    require(name.casefold() != CONTENTS.casefold(), 'Manifest cannot list itself.')
    return name


def payload_records(root: Path) -> dict[str, str]:
    try:
        text = bounded_bytes(root / CONTENTS, MAX_JSON_BYTES).decode('utf-8-sig')
    except UnicodeError as error:
        raise ReleaseGateError('Payload manifest is not UTF-8.') from error
    records, folded = {}, set()
    for line in text.splitlines():
        match = re.fullmatch(r'([0-9a-f]{64})  (.+)', line)
        require(match is not None, 'Malformed payload digest line.')
        digest, name = match.groups()
        name = payload_name(name)
        require(name.casefold() not in folded, 'Duplicate Windows payload path.')
        folded.add(name.casefold())
        records[name] = digest
        require(len(records) <= MAX_PAYLOAD_FILES, 'Too many payload entries.')
    require(bool(records), 'Payload manifest is empty.')
    return records


def _payload_inventory(root: Path) -> set[str]:
    pending, names, visited = [root], set(), 0
    while pending:
        parent = pending.pop()
        for child in parent.iterdir():
            visited += 1
            require(visited <= MAX_PAYLOAD_FILES * 2, 'Payload tree is too large.')
            child = plain_path(child)  # Reject Windows reparse points as well as symlinks.
            require(child.is_relative_to(root), 'Payload path escaped its root.')
            mode = child.stat().st_mode
            if stat.S_ISDIR(mode):
                pending.append(child)
            else:
                require(stat.S_ISREG(mode), 'Special payload file is not allowed.')
                names.add(child.relative_to(root).as_posix())
    return names


def verify_release(manifest: dict, build_info: dict, runtime_info: dict,
                   installer: Path, payload: Path, expected_tag: str) -> dict:
    """Check consistency of supplied evidence and bytes, not release authorization."""
    require(all(isinstance(x, dict) for x in (manifest, build_info, runtime_info)),
            'Release, build and runtime evidence must be objects.')
    date, revision = build_key(expected_tag)
    suffix = str(date) + (f'-r{revision}' if revision else '')
    filename = f'WorkBox-1.0.0-Setup-x64-{suffix}.exe'
    require(manifest.get('tag_name') == expected_tag and manifest.get('draft') is False
            and manifest.get('prerelease') is False, 'Release identity or stable state mismatch.')
    require(manifest.get('html_url') == RELEASE_ROOT + 'tag/' + expected_tag,
            'Release page is not the expected official URL.')
    require(isinstance(manifest.get('body') or '', str),
            'Release notes must be text when nonempty.')
    assets = manifest.get('assets')
    require(isinstance(assets, list), 'Release assets must be a list.')
    matches = [a for a in assets if isinstance(a, dict) and a.get('name') == filename]
    require(len(matches) == 1, 'Expected exactly one matching installer asset.')
    asset = matches[0]
    url = RELEASE_ROOT + f'download/{expected_tag}/{filename}'
    require(asset.get('browser_download_url') == url and asset.get('state') == 'uploaded',
            'Installer asset source or state mismatch.')
    require(type(asset.get('size')) is int and 2 <= asset['size'] <= MAX_INSTALLER,
            'Invalid installer size in release metadata.')
    require(isinstance(asset.get('digest'), str)
            and re.fullmatch(r'sha256:[0-9a-f]{64}', asset['digest']) is not None,
            'Invalid installer digest in release metadata.')

    installer = regular_file(installer)
    require(installer.name == filename, 'Local installer must have its final release filename.')
    size, sha, first = file_facts(installer)
    require(first == b'MZ', 'Installer is not a Windows executable.')
    require(size == asset['size'] and 'sha256:' + sha == asset['digest'],
            'Installer bytes do not match release metadata.')
    require(type(build_info.get('installer_size')) is int and build_info['installer_size'] == size
            and build_info.get('installer_sha256') == sha, 'Build report installer mismatch.')
    require(build_info.get('contains_user_data') is False, 'Build report lacks no-user-data boundary.')
    require(type(build_info.get('payload_files')) is int
            and 0 < build_info['payload_files'] <= MAX_PAYLOAD_FILES, 'Invalid payload count.')

    payload = plain_path(payload)
    require(payload.is_dir(), 'Payload root is not a directory.')
    records = payload_records(payload)
    require(len(records) == build_info['payload_files'], 'Payload count does not match build report.')
    require(set(REQUIRED_RUNTIME) | {'安装说明.txt', 'THIRD_PARTY_NOTICES.md'} <= records.keys()
            and any(n.startswith('licenses/') and n.endswith('.txt') for n in records),
            'Required runtime or license/installation documents are missing.')
    require(_payload_inventory(payload) == set(records) | {CONTENTS},
            'Payload has missing or unlisted files.')
    total = 0
    for name, expected in records.items():
        file_size, actual, prefix = file_facts(payload / name)
        total += file_size
        require(total <= MAX_PAYLOAD_BYTES, 'Total payload size exceeds the limit.')
        require(actual == expected, 'Payload file digest mismatch.')
        if name == 'WorkBox.exe':
            require(prefix == b'MZ', 'Application is not a Windows executable.')
    require(_payload_inventory(payload) == set(records) | {CONTENTS},
            'Payload inventory changed during verification.')
    require(build_info.get('app_sha256') == records['WorkBox.exe'], 'Build report application mismatch.')
    require(runtime_info.get('version') == '1.0.0' and runtime_info.get('release_tag') == expected_tag
            and runtime_info.get('app_sha256') == records['WorkBox.exe'],
            'Runtime build-info evidence does not match this candidate.')
    return dict(status='passed', scope='offline-artifact-consistency', release_tag=expected_tag,
                installer_filename=filename, installer_size=size, installer_sha256=sha,
                app_sha256=records['WorkBox.exe'], payload_files=len(records),
                runtime_executed=False, installation_verified=False, source_provenance_verified=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ('manifest', 'build-info', 'runtime-info', 'installer', 'payload'):
        parser.add_argument('--' + option, type=Path, required=True)
    parser.add_argument('--expected-tag', required=True)
    args = parser.parse_args(argv)
    try:
        result = verify_release(load_json(args.manifest), load_json(args.build_info),
                                load_json(args.runtime_info), args.installer, args.payload, args.expected_tag)
    except (ValueError, OSError, RecursionError) as error:
        # Do not reflect arbitrary input values or local paths into shareable reports.
        message = str(error) if isinstance(error, ReleaseGateError) else 'Input could not be safely validated.'
        print(json.dumps(dict(status='failed', scope='offline-artifact-consistency', error=message)))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
