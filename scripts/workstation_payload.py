"""核对工作站冻结产物只带应用自用的 Qt 浏览器运行时。"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from scripts import stage_webengine_runtime as webengine
from scripts.build_installer import checked_files
from workbox.storage.native_files import plain_path


EXTRA_COMPONENT = re.compile(
    r'^(?:playwright|ms-playwright|pyee|greenlet|chromium|chrome|msedge|node|nodejs|teamserver)(?:$|[-_.])'
)
SHA256 = re.compile('[0-9a-f]{64}')


def validate_workstation_runtime(frozen: Path) -> None:
    frozen = plain_path(frozen)
    for path in checked_files(frozen):
        relative = path.relative_to(frozen)
        if any(EXTRA_COMPONENT.match(part.casefold()) for part in relative.parts):
            raise ValueError(f'工作站载荷包含额外的浏览器、驱动或服务器组件：{relative.as_posix()}')

    qt = frozen / '_internal/PySide6'
    for name in (*webengine.WEBENGINE_FILES, 'QtWebEngineCore.pyd', 'QtWebEngineWidgets.pyd'):
        path = plain_path(qt / name)
        if not path.is_file() or not path.stat().st_size:
            raise ValueError(f'工作站缺少 QtWebEngine 文件：{name}')
    for name, identity in webengine.MSVC_RUNTIME.items():
        webengine.verified_bytes(qt / name, identity)


def _read_record(path: Path) -> bytes:
    with plain_path(path).open('rb') as file:
        raw = file.read(8193)
    if len(raw) > 8192:
        raise ValueError(f'工作站冻结凭据或 Qt 清单过大：{path.name}')
    return raw


def read_workstation_freeze(path: Path, project: Path, frozen: Path, source_commit: str,
                            executable_sha256: str) -> tuple[dict[str, str], str]:
    path = plain_path(path)
    record = json.loads(_read_record(path))
    if (not isinstance(record, dict) or record.get('variant') != 'workstation'
            or record.get('source_commit') != source_commit
            or record.get('executable_sha256') != executable_sha256.casefold()
            or not isinstance(record.get('frozen_path'), str) or not record['frozen_path']
            or plain_path(project / record['frozen_path']) != frozen):
        raise ValueError('工作站冻结凭据与源码提交、主程序或冻结目录不一致。')
    updater_sha256 = record.get('updater_sha256')
    if not isinstance(updater_sha256, str) or SHA256.fullmatch(updater_sha256) is None:
        raise ValueError('工作站冻结凭据缺少有效的更新助手 SHA-256。')

    raw_qt_manifest = _read_record(path.with_name('qt-engine-files.json'))
    qt_manifest_sha256 = hashlib.sha256(raw_qt_manifest).hexdigest()
    if record.get('qt_manifest_sha256') != qt_manifest_sha256:
        raise ValueError('QtWebEngine 清单与工作站冻结凭据不一致。')
    qt_manifest = json.loads(raw_qt_manifest)
    if (not isinstance(qt_manifest, dict) or qt_manifest.keys() != {*webengine.WEBENGINE_FILES, *webengine.MSVC_RUNTIME}
            or any(not isinstance(digest, str) or SHA256.fullmatch(digest) is None for digest in qt_manifest.values())):
        raise ValueError('QtWebEngine 冻结清单的文件或摘要无效。')
    hashes = {'_internal/PySide6/' + name: digest for name, digest in qt_manifest.items()}
    hashes['WorkBoxUpdater.exe'] = updater_sha256
    return hashes, qt_manifest_sha256


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frozen', type=Path, required=True)
    args = parser.parse_args()
    validate_workstation_runtime(args.frozen)


if __name__ == '__main__':
    main()
