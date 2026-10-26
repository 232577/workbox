"""从已核实的冻结产物生成干净安装载荷及 Inno Setup 安装包。"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

from native_files import plain_path


PRIVATE_SUFFIXES = {'.db', '.db-wal', '.db-shm', '.sqlite', '.sqlite3', '.csv',
                    '.json', '.log', '.ini', '.doc', '.docx', '.xls', '.xlsx', '.pdf'}
PRIVATE_NAMES = {'data', 'state', 'logs', '.local', '.git', '.env', '.codex', 'backups'}
REQUIRED_RUNTIME = ('WorkBox.exe', '_internal/python311.dll', '_internal/PySide6/Qt6Core.dll',
                    '_internal/PySide6/plugins/platforms/qwindows.dll')


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def checked_files(root):
    root = plain_path(root)
    pending = [root]
    while pending:
        parent = pending.pop()
        for path in sorted(parent.iterdir()):
            path = plain_path(path)
            if not path.is_relative_to(root):
                raise ValueError('安装载荷路径越界。')
            if path.is_dir():
                pending.append(path)
            elif path.is_file():
                yield path
            else:
                raise ValueError('安装载荷存在特殊文件。')


def prepare_payload(project, built, output, expected_exe_sha256):
    project, built, output = map(plain_path, (project, built, output))
    if output.exists():
        raise FileExistsError('安装载荷目录已存在，不覆盖既有内容。')
    if output.is_relative_to(built):
        raise ValueError('安装载荷不能放入构建源目录内。')
    if not re.fullmatch('[0-9a-fA-F]{64}', expected_exe_sha256):
        raise ValueError('必须提供经过验证的 EXE SHA256。')
    if {path.name for path in built.iterdir()} != {'WorkBox.exe', '_internal'}:
        raise ValueError('构建目录必须仅含 WorkBox.exe 和 _internal，不得带入用户数据。')
    for name in REQUIRED_RUNTIME:
        if not plain_path(built/name).is_file():
            raise ValueError('缺少必要运行时：' + name)
    if sha256(built/'WorkBox.exe') != expected_exe_sha256.casefold():
        raise ValueError('EXE 与已验证版本不一致。')
    sources = []
    for path in checked_files(built):
        relative = path.relative_to(built)
        if (any(part.casefold() in PRIVATE_NAMES for part in relative.parts)
                or path.suffix.casefold() in PRIVATE_SUFFIXES):
            raise ValueError('构建目录含可能的用户数据或配置：' + str(relative))
        sources.append((path, relative))
    for source, target in ((project/'packaging/安装说明.txt', Path('安装说明.txt')),
                           (project/'THIRD_PARTY_NOTICES.md', Path('THIRD_PARTY_NOTICES.md'))):
        if not plain_path(source).is_file():
            raise FileNotFoundError(source)
        sources.append((source, target))
    licenses = list(checked_files(project/'licenses'))
    if not licenses:
        raise ValueError('缺少第三方许可证。')
    for path in licenses:
        if path.suffix.casefold() != '.txt':
            raise ValueError('许可证目录包含额外文件。')
        sources.append((path, Path('licenses')/path.relative_to(project/'licenses')))
    # 所有边界验证完成后才创建输出目录；每个副本核对字节摘要。
    output.mkdir(parents=True)
    manifest = {}
    for source, relative in sources:
        expected = sha256(source)
        target = output/relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        if sha256(target) != expected:
            raise RuntimeError('安装载荷复制校验失败：' + str(relative))
        manifest[relative.as_posix()] = expected
    (output/'PACKAGE_CONTENTS.sha256').write_text(''.join(
        f'{digest}  {name}\n' for name, digest in sorted(manifest.items())), encoding='utf-8')
    return manifest


def build(project, built, destination, compiler, expected_exe_sha256):
    project, built, destination, compiler = map(plain_path, (project, built, destination, compiler))
    if destination.exists():
        raise FileExistsError('安装包输出目录已存在，请使用新的目录。')
    if not compiler.is_file():
        raise FileNotFoundError('找不到已安装的 Inno Setup 编译器。')
    payload = destination/'payload'
    manifest = prepare_payload(project, built, payload, expected_exe_sha256)
    output = destination/'output'
    output.mkdir()
    command = [str(compiler), '/Qp', '/DPayloadDir='+str(payload), '/DOutputDir='+str(output),
               '/DAppIcon='+str(project/'build/WorkBox.ico'), str(project/'packaging/workbox.iss')]
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    (destination/'compiler.log').write_bytes(result.stdout)
    if result.returncode:
        raise RuntimeError('安装包编译失败，请查看 compiler.log。')
    installer = output/'WorkBox-1.0.0-Setup-x64.exe'
    if not installer.is_file():
        raise RuntimeError('编译器未生成预期安装包。')
    report = dict(installer=str(installer), installer_sha256=sha256(installer),
                  installer_size=installer.stat().st_size, app_sha256=manifest['WorkBox.exe'],
                  payload_files=len(manifest), contains_user_data=False,
                  target='Windows 10 1809+ / Windows 11 x64', install_mode='current-user',
                  data_location='安装目录/data', runtime_validation='pending')
    (destination/'BUILD_INFO.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--built', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--expected-exe-sha256', required=True)
    parser.add_argument('--compiler', type=Path, default=Path('C:/Program Files (x86)/Inno Setup 6/ISCC.exe'))
    args = parser.parse_args()
    build(Path(__file__).resolve().parents[1], args.built, args.destination, args.compiler, args.expected_exe_sha256)
