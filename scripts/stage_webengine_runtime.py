"""核对冻结后的 QtWebEngine 载荷并补齐子进程的本地运行库。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import PySide6

from scripts.build_installer import sha256
from workbox.storage.native_files import plain_path


PYSIDE_VERSION = '6.8.3'
# 来自 requirements-lock.txt 锁定的 Windows x64 PySide6_Essentials wheel。
MSVC_RUNTIME = {
    'msvcp140.dll': (565384, '2a2aef1d8ccb312edb33554e3db0ac8c158957dc3e31faf5de02b3eab340afcc'),
    'vcruntime140.dll': (111240, '7516a369770fce4c993c0aa6a91f872e8e97873e85866851ae7351eb1dfed528'),
    'vcruntime140_1.dll': (41608, 'ac1a338047211cc6def2330b7e9f866260ce4ab50c43b858f156960b39846ca5'),
}
WEBENGINE_FILES = (
    'QtWebEngineProcess.exe',
    'Qt6WebEngineCore.dll',
    'Qt6WebEngineWidgets.dll',
    'resources/icudtl.dat',
    'resources/v8_context_snapshot.bin',
    'resources/qtwebengine_resources.pak',
    'resources/qtwebengine_devtools_resources.pak',
    'resources/qtwebengine_resources_100p.pak',
    'resources/qtwebengine_resources_200p.pak',
    'translations/qtwebengine_locales/en-US.pak',
    'translations/qtwebengine_locales/zh-CN.pak',
)
DESIGNER_METADATA = 'qml/QtQuick3D/designer/propertyGroups.json'
DESIGNER_IDENTITY = (5043, '2aec9312eaedf4dc63ab3695ccfc8d77e0cd473e86fcb310e070bb13717c81fc')


def verified_bytes(path: Path, identity: tuple[int, str]) -> bytes:
    path = plain_path(path)
    size, digest = identity
    if not path.is_file() or path.stat().st_size != size:
        raise ValueError(f'Qt 文件缺失或大小不匹配：{path}')
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != digest:
        raise ValueError(f'Qt 文件摘要不匹配：{path}')
    return payload


def stage_webengine_runtime(frozen: Path, qt_wheel: Path) -> dict[str, str]:
    qt_dist = plain_path(frozen / '_internal' / 'PySide6')
    manifest = {}
    for name in WEBENGINE_FILES:
        path = plain_path(qt_dist / name)
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f'冻结产物缺少 QtWebEngine 文件：{path}')
        manifest[name] = sha256(path)

    pending = {}
    for name, identity in MSVC_RUNTIME.items():
        payload = verified_bytes(qt_wheel / name, identity)
        destination = plain_path(qt_dist / name)
        if destination.exists():
            verified_bytes(destination, identity)
        else:
            pending[destination] = payload
        manifest[name] = identity[1]

    designer = plain_path(qt_dist / DESIGNER_METADATA)
    if designer.exists():
        verified_bytes(designer, DESIGNER_IDENTITY)
        # 排除 Qt Designer 的开发元数据，保留安装包私有 JSON 拦截规则。
        designer.unlink()
    for destination, payload in pending.items():
        with destination.open('xb') as file:
            file.write(payload)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frozen', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    args = parser.parse_args()
    if PySide6.__version__ != PYSIDE_VERSION:
        raise ValueError(f'请使用锁定的 Windows x64 PySide6 {PYSIDE_VERSION} 构建。')
    output = plain_path(args.manifest)
    if output.is_relative_to(plain_path(args.frozen)):
        raise ValueError('QtWebEngine 文件清单需要保存在安装载荷外。')
    manifest = stage_webengine_runtime(args.frozen, Path(PySide6.__file__).parent)
    output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
