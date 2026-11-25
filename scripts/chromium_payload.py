"""Stage the fixed open-source Windows Chromium fallback for an offline install."""

from hashlib import file_digest, sha256
from pathlib import Path, PurePosixPath
import re
import stat
from zipfile import ZipFile


REVISION = '1754043'
VERSION = '160.0.8183.0'
SOURCE_COMMIT = 'c7c5cb2b8811116e20cd636c7b0514f01ae276bb'
ARCHIVE_URL = f'https://storage.googleapis.com/chromium-browser-snapshots/Win_x64/{REVISION}/chrome-win.zip'
ARCHIVE_SHA256 = '0363eaf409db8478a95653f63115615f8ca89cebb5ffc8d503f963f6587da2e9'
LICENSE_SHA256 = '368cca1106be99d39ecd32a38d8305585d802a475effb66380b91ffc9bcf709b'
MANIFEST = 'CHROMIUM_CONTENTS.sha256'
EXCLUDED = frozenset({'interactive_ui_tests.exe', 'setup.exe'})


def stage_chromium(packaging, archive, output):
    license_name = 'Chromium-LICENSE.txt'
    license_bytes = (Path(packaging) / license_name).read_bytes()
    if sha256(license_bytes).hexdigest() != LICENSE_SHA256:
        raise ValueError('Chromium 许可证与固定上游源码不一致。')
    output = Path(output)
    if output.exists():
        raise FileExistsError('Chromium 输出目录已存在。')
    with Path(archive).open('rb') as locked:
        if file_digest(locked, 'sha256').hexdigest() != ARCHIVE_SHA256:
            raise ValueError('Chromium ZIP 与固定上游摘要不一致。')
        locked.seek(0)
        with ZipFile(locked) as source:
            names = {license_name.casefold(), MANIFEST.casefold()}
            files = {}
            for item in source.infolist():
                if item.is_dir():
                    continue
                head, _, name = item.filename.partition('/')
                path = PurePosixPath(name)
                if (head != 'chrome-win' or path.as_posix() != name
                        or any(not re.fullmatch(r'[A-Za-z0-9_. -]+', part) or part in ('.', '..')
                               or part.endswith((' ', '.')) for part in name.split('/'))
                        or stat.S_ISLNK(item.external_attr >> 16) or name.casefold() in names):
                    raise ValueError('Chromium ZIP 包含无效程序路径。')
                names.add(name.casefold())
                if name not in EXCLUDED:
                    files[name] = item
            if not {'chrome.exe', 'chrome.dll', 'resources.pak', VERSION + '.manifest'} <= files.keys():
                raise ValueError('Chromium ZIP 缺少固定版本的浏览器程序。')
            output.mkdir(parents=True)
            (output / license_name).write_bytes(license_bytes)
            hashes = {license_name: LICENSE_SHA256}
            for name, item in files.items():
                target = output / name
                target.parent.mkdir(parents=True, exist_ok=True)
                digest = sha256()
                with source.open(item) as incoming, target.open('xb') as outgoing:
                    while block := incoming.read(1024 * 1024):
                        digest.update(block)
                        outgoing.write(block)
                hashes[name] = digest.hexdigest()
            locked.seek(0)
            if file_digest(locked, 'sha256').hexdigest() != ARCHIVE_SHA256:
                raise ValueError('准备期间 Chromium ZIP 发生变化。')
    receipt = ''.join(f'{digest}  {name}\n' for name, digest in sorted(hashes.items())).encode('utf-8')
    (output / MANIFEST).write_bytes(receipt)
    return dict(version=VERSION, revision=REVISION, source_commit=SOURCE_COMMIT, archive_url=ARCHIVE_URL,
                archive_sha256=ARCHIVE_SHA256, license_sha256=LICENSE_SHA256,
                payload_manifest_sha256=sha256(receipt).hexdigest(), payload_files=len(hashes))
