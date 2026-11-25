"""从已核实的冻结产物生成干净安装载荷及 Inno Setup 安装包。"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path, PurePosixPath

from scripts.chromium_payload import stage_chromium

from workbox.storage.native_files import plain_path
from workbox.updates.installer_runtime import verify_program_manifest


PRIVATE_SUFFIXES = {'.db', '.db-wal', '.db-shm', '.sqlite', '.sqlite3', '.csv',
                    '.json', '.log', '.ini', '.doc', '.docx', '.xls', '.xlsx', '.pdf'}
PRIVATE_NAMES = {'data', 'state', 'logs', '.local', '.git', '.env', '.codex', 'backups'}
REQUIRED_RUNTIME = ('WorkBox.exe', 'WorkBoxUpdater.exe', '_internal/python311.dll', '_internal/PySide6/Qt6Core.dll',
                    '_internal/PySide6/plugins/platforms/qwindows.dll')
PLAYWRIGHT_CATALOGS = frozenset({
    '_internal/playwright/driver/package/browsers.json',
    '_internal/playwright/driver/package/package.json',
    '_internal/playwright/driver/package/lib/tools/cli-client/help.json',
})
MAX_WORKSTATION_INSTALLER_SIZE = 200_000_000
WORKSTATION_GUIDE = (
    '团队交件与办公室服务器\n'
    '工作站可以离线安装，自带用于「这是家」窗口和「团队办理」的 Qt 浏览器运行时。\n'
    '首次安装和更新已有工作箱都使用此安装包。更新前请退出工作箱，在原程序目录安装。\n'
    '原地更新保留个人资料和已安装的办公室服务器程序。旧版独立 Chromium 如已安装，也保留供回退使用。\n'
    '办理业务时需能访问「这是家」和已部署的办公室服务。请按页面提示登录各自账号。\n'
    '办公室服务器由管理员使用单独的办公室服务器安装包部署，负责保存材料、生成五件包和统计。\n\n'
)
SERVER_MANIFEST = 'install-bundle.json'
SERVER_REQUIRED = frozenset({
    'python312/python.exe', 'scripts/kanboard_local_manager.py', 'scripts/kanboard_runtime.py',
    'source/kanboard/index.php', 'source.tar', 'Kanboard.cmd', 'INSTALL-README.txt',
    'executor/WorkBoxDocumentExecutor.exe', 'filee/workbox-filee-worker.exe',
})


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


def prepare_payload(project, built, output, expected_exe_sha256, *, team_guide=False, workstation=False,
                    frozen_hashes=None):
    project, built, output = map(plain_path, (project, built, output))
    if output.exists():
        raise FileExistsError('安装载荷目录已存在，不覆盖既有内容。')
    if output.is_relative_to(built):
        raise ValueError('安装载荷不能放入构建源目录内。')
    if not re.fullmatch('[0-9a-fA-F]{64}', expected_exe_sha256):
        raise ValueError('必须提供经过验证的 EXE SHA256。')
    if {path.name for path in built.iterdir()} != {'WorkBox.exe', 'WorkBoxUpdater.exe', '_internal'}:
        raise ValueError('构建目录必须仅含 WorkBox.exe、WorkBoxUpdater.exe 和 _internal，不得带入用户数据。')
    for name in REQUIRED_RUNTIME:
        if not plain_path(built/name).is_file():
            raise ValueError('缺少必要运行时：' + name)
    if sha256(built/'WorkBox.exe') != expected_exe_sha256.casefold():
        raise ValueError('EXE 与已验证版本不一致。')
    if workstation:
        from scripts.workstation_payload import validate_workstation_runtime

        validate_workstation_runtime(built)
    helper = plain_path(built/'WorkBoxUpdater.exe')
    with helper.open('rb') as stream:
        if stream.read(2) != b'MZ':
            raise ValueError('独立更新助手不是 Windows 程序。')
    verified_hashes = {'WorkBox.exe': expected_exe_sha256.casefold(), 'WorkBoxUpdater.exe': sha256(helper)}
    for name, expected in (frozen_hashes or {}).items():
        digest = verified_hashes.get(name)
        if digest is None:
            digest = sha256(plain_path(built / name))
        if digest != expected:
            raise ValueError(f'工作站更新助手或 Qt 运行库与冻结凭据不一致：{name}')
        verified_hashes[name] = digest
    sources = []
    for path in checked_files(built):
        relative = path.relative_to(built)
        if (any(part.casefold() in PRIVATE_NAMES for part in relative.parts)
                or (path.suffix.casefold() in PRIVATE_SUFFIXES and relative.as_posix() not in PLAYWRIGHT_CATALOGS)):
            raise ValueError('构建目录含可能的用户数据或配置：' + str(relative))
        sources.append((path, relative))
    guide_source = plain_path(project/'packaging/安装说明.txt')
    guide = None
    if workstation:
        guide, sections = re.subn(r'(?ms)^团队交件与办公室服务器\n.*?(?=^项目操作与交付记录\n)',
                                 WORKSTATION_GUIDE, guide_source.read_text(encoding='utf-8'), count=1)
        if sections != 1:
            raise ValueError('安装说明缺少可替换的团队交件章节。')
    elif team_guide:
        team_usage = plain_path(project/'docs/TEAM-DELIVERY.md').read_text(encoding='utf-8')
        guide = guide_source.read_text(encoding='utf-8').rstrip() + '\n\n' + team_usage
    for source, target in ((guide_source, Path('安装说明.txt')),
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
        expected = verified_hashes.get(relative.as_posix())
        if expected is None:
            expected = sha256(source)
        elif sha256(source) != expected:
            raise RuntimeError('已核实的程序在复制前发生变化。')
        target = output/relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if guide is not None and relative == Path('安装说明.txt'):
            target.write_text(guide, encoding='utf-8', newline='\n')
            expected = hashlib.sha256(guide.encode('utf-8')).hexdigest()
        else:
            shutil.copy2(source, target)
        if sha256(target) != expected:
            raise RuntimeError('安装载荷复制校验失败：' + str(relative))
        manifest[relative.as_posix()] = expected
    (output/'PACKAGE_CONTENTS.sha256').write_text(''.join(
        f'{digest}  {name}\n' for name, digest in sorted(manifest.items())), encoding='utf-8')
    return manifest


def inspect_team_server(root, expected_commit, expected_manifest_sha256):
    """Verify the exact stand-alone server layout before it enters the installer."""
    root = plain_path(root)
    if not re.fullmatch('[0-9a-fA-F]{40}', expected_commit):
        raise ValueError('必须提供经过核验的服务器完整提交 SHA。')
    if not re.fullmatch('[0-9a-fA-F]{64}', expected_manifest_sha256):
        raise ValueError('必须提供经过核验的服务器清单 SHA-256。')
    receipt = plain_path(root / SERVER_MANIFEST)
    if not receipt.is_file() or not 0 < receipt.stat().st_size <= 16 * 1024 * 1024:
        raise ValueError('缺少有效的服务器安装清单。')
    raw = receipt.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != expected_manifest_sha256.casefold():
        raise ValueError('服务器安装清单摘要不一致。')
    bundle = json.loads(raw)
    expected_header = ('workbox-kanboard-install/1', 'WorkBoxKanboard', 'access-intake-worker-v11', expected_commit.casefold())
    if not isinstance(bundle, dict) or tuple(bundle.get(key) for key in ('format', 'product', 'profile', 'commit')) != expected_header:
        raise ValueError('服务器清单的产品、配置或源码提交不一致。')
    records = bundle.get('files')
    if not isinstance(records, list):
        raise ValueError('服务器文件清单无效。')
    files = {}
    folded = {SERVER_MANIFEST.casefold()}
    for row in records:
        if not isinstance(row, dict) or row.keys() != {'path', 'size', 'sha256'}:
            raise ValueError('服务器文件记录无效。')
        name = row['path']
        if (not isinstance(name, str) or re.search(r'[\\:\x00-\x1f]', name)
                or any(part in ('', '.', '..') or part.endswith((' ', '.')) for part in name.split('/'))
                or name.casefold() in folded or type(row['size']) is not int or row['size'] < 0
                or not isinstance(row['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', row['sha256'])):
            raise ValueError('服务器文件路径或摘要无效。')
        files[name] = row['size'], row['sha256']
        folded.add(name.casefold())
    if not SERVER_REQUIRED <= files.keys() or files['source.tar'][1] != bundle.get('source_archive_sha256'):
        raise ValueError('服务器缺少必需组件或源码归档。')
    files[SERVER_MANIFEST] = len(raw), digest
    paths = {path.relative_to(root).as_posix(): path for path in checked_files(root)}
    if paths.keys() != files.keys():
        raise ValueError('服务器程序目录有缺失或额外文件。')
    for name, (size, expected) in files.items():
        path = paths[name]
        if path.stat().st_size != size or sha256(path) != expected:
            raise ValueError('服务器文件摘要不一致：' + name)
    return bundle, files


def team_server_path_limit(files, version):
    # Keep ordinary Windows paths below 260 characters and directories below 248.
    subtree = PurePosixPath('TeamServer') / version

    def path_units(relative):
        return len(('/' + relative.as_posix()).encode('utf-16-le')) // 2

    return min(min(259 - path_units(subtree / name), 247 - path_units((subtree / name).parent))
               for name in files)


def build(project, built, destination, compiler, expected_exe_sha256, *,
          team_server_payload=None, team_server_commit=None, team_server_manifest_sha256=None,
          chromium_archive=None, desktop_source_commit=None,
          chinese_messages=None, chinese_messages_sha256=None, workstation=False, freeze_provenance=None,
          release_tag=None):
    project, built, destination, compiler = map(plain_path, (project, built, destination, compiler))
    if destination.exists():
        raise FileExistsError('安装包输出目录已存在，请使用新的目录。')
    if not compiler.is_file():
        raise FileNotFoundError('找不到已安装的 Inno Setup 编译器。')
    if (chinese_messages is None) != (chinese_messages_sha256 is None):
        raise ValueError('外部 Inno 简体中文消息文件和 SHA-256 必须同时提供。')
    if chinese_messages is not None:
        chinese_messages = plain_path(chinese_messages)
        if not chinese_messages.is_file() or sha256(chinese_messages) != chinese_messages_sha256.casefold():
            raise ValueError('Inno 简体中文消息文件摘要不一致。')
    server_args = (team_server_payload, team_server_commit, team_server_manifest_sha256)
    release_filename = None
    if release_tag is not None:
        from workbox.updates.update_client import CURRENT_TAG, build_key
        if not workstation or release_tag != CURRENT_TAG:
            raise ValueError('正式安装包需要工作站模式及与当前源码一致的发布标签。')
        date, revision = build_key(release_tag)
        suffix = str(date) + (f'-r{revision}' if revision else '')
        release_filename = f'WorkBox-1.0.0-Setup-x64-{suffix}.exe'
    if workstation:
        if any(value is not None for value in server_args):
            raise ValueError('工作站包不包含办公室服务器。')
        if chromium_archive is not None:
            raise ValueError('工作站包不允许携带独立 Chromium。')
        if freeze_provenance is None:
            raise ValueError('工作站包需要本次构建生成的冻结凭据。')
    elif freeze_provenance is not None:
        raise ValueError('指定工作站冻结凭据需要使用工作站打包模式。')
    if any(value is not None for value in server_args) and not all(server_args):
        raise ValueError('服务器载荷、完整源码提交和清单摘要必须同时提供。')
    if workstation or team_server_payload is not None:
        if (not isinstance(desktop_source_commit, str) or not re.fullmatch('[0-9a-f]{40}', desktop_source_commit)
                or subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=project, text=True).strip() != desktop_source_commit
                or subprocess.check_output(['git', 'status', '--porcelain'], cwd=project, text=True).strip()):
            raise ValueError('需要与冻结程序对应的干净桌面源码提交。')
    frozen_hashes = None
    if workstation:
        from scripts.workstation_payload import read_workstation_freeze

        frozen_hashes, qt_manifest_sha256 = read_workstation_freeze(
            freeze_provenance, project, built, desktop_source_commit, expected_exe_sha256)
    if team_server_payload is not None:
        team_server_payload = plain_path(team_server_payload)
        if destination.is_relative_to(team_server_payload):
            raise ValueError('安装包输出目录不能位于服务器载荷内。')
        if chromium_archive is None:
            raise ValueError('统一安装包需要固定的离线 Chromium 载荷。')
        server, server_files = inspect_team_server(team_server_payload, team_server_commit, team_server_manifest_sha256)
    payload = destination/'payload'
    manifest = prepare_payload(project, built, payload, expected_exe_sha256,
                               team_guide=team_server_payload is not None, workstation=workstation,
                               frozen_hashes=frozen_hashes)
    manifest_sha256 = sha256(payload/'PACKAGE_CONTENTS.sha256')
    verify_program_manifest(payload, manifest_sha256)
    output = destination/'output'
    output.mkdir()
    command = [str(compiler), '/Qp', '/DPayloadDir='+str(payload), '/DOutputDir='+str(output),
               '/DPayloadManifestSHA256='+manifest_sha256,
               '/DUpdaterSHA256='+manifest['WorkBoxUpdater.exe'],
               '/DAppIcon='+str(project/'build/WorkBox.ico')]
    if chinese_messages is not None:
        command.append('/DChineseMessagesFile=' + str(chinese_messages))
    if chromium_archive is not None:
        browser = stage_chromium(project / 'packaging', plain_path(chromium_archive), payload / 'Chromium')
        command.append('/DChromiumManifestSHA256=' + browser['payload_manifest_sha256'])
    installer_name = 'WorkBox-1.0.0-Setup-x64.exe'
    if workstation:
        command.append('/DWorkstationPayload=1')
        installer_name = 'WorkBox-1.0.0-Workstation-Setup-x64.exe'
        if release_filename:
            installer_name = release_filename
            command.append('/F' + Path(installer_name).stem)
    if team_server_payload is not None:
        staged_server = destination / 'team-server-payload'
        for name, (size, expected) in server_files.items():
            target = staged_server / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(plain_path(team_server_payload / name), target)
            if target.stat().st_size != size or sha256(target) != expected:
                raise RuntimeError('服务器程序复制校验失败：' + name)
        server_digest = server_files[SERVER_MANIFEST][1]
        server_version = server_digest[:16]
        server_inventory = destination / 'TEAM_SERVER_CONTENTS.sha256'
        server_inventory.write_text(''.join(f'{digest}  {name}\n' for name, (_, digest) in sorted(server_files.items())),
                                    encoding='utf-8-sig', newline='\n')
        server_inventory_digest = sha256(server_inventory)
        command.extend(('/DTeamServerPayloadDir=' + str(staged_server),
                        '/DTeamServerManifestSHA256=' + server_digest,
                        '/DTeamServerFilesManifest=' + str(server_inventory),
                        '/DTeamServerFilesManifestSHA256=' + server_inventory_digest,
                        '/DTeamServerVersion=' + server_version,
                        '/DTeamServerMaxAppDir=' + str(team_server_path_limit(server_files, server_version))))
        installer_name = 'WorkBox-1.0.0-Team-Setup-x64.exe'
    command.append(str(project/'packaging/workbox.iss'))
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    (destination/'compiler.log').write_bytes(result.stdout)
    if result.returncode:
        raise RuntimeError('安装包编译失败，请查看 compiler.log。')
    installer = plain_path(output/installer_name)
    if not installer.is_file():
        raise RuntimeError('编译器未生成预期安装包。')
    installer_size = installer.stat().st_size
    if workstation and installer_size > MAX_WORKSTATION_INSTALLER_SIZE:
        installer.rename(installer.with_suffix('.exe.oversize'))
        raise RuntimeError(f'工作站安装包为 {installer_size} 字节，超过 {MAX_WORKSTATION_INSTALLER_SIZE} 字节上限。')
    report = dict(installer=str(installer), installer_sha256=sha256(installer),
                  installer_size=installer_size, app_sha256=manifest['WorkBox.exe'],
                  updater_sha256=manifest['WorkBoxUpdater.exe'],
                  payload_manifest_sha256=manifest_sha256,
                  payload_files=len(manifest), contains_user_data=False,
                  target='Windows 10 1809+ / Windows 11 x64', install_mode='current-user',
                  data_location='安装目录/data', runtime_validation='pending')
    if chromium_archive is not None:
        report['chromium'] = browser
    if chinese_messages is not None:
        report['chinese_messages_sha256'] = chinese_messages_sha256.casefold()
    if workstation:
        report.update(package_role='workstation', desktop_source_commit=desktop_source_commit,
                      installer_max_bytes=MAX_WORKSTATION_INSTALLER_SIZE, includes_office_server=False,
                      qt_manifest_sha256=qt_manifest_sha256)
    if release_tag:
        report.update(installer_name=installer_name, release_tag=release_tag)
    if team_server_payload is not None:
        report['desktop_source_commit'] = desktop_source_commit
        report['team_server'] = dict(source_commit=server['commit'], profile=server['profile'],
                                     manifest_sha256=server_digest, payload_files=len(server_files),
                                     files_manifest_sha256=server_inventory_digest,
                                     install_subdirectory=f'TeamServer/{server_version}', selected_by_default=False)
    (destination/'BUILD_INFO.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--built', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--expected-exe-sha256', required=True)
    parser.add_argument('--compiler', type=Path, default=Path('C:/Program Files (x86)/Inno Setup 6/ISCC.exe'))
    parser.add_argument('--workstation', action='store_true', help='构建单引擎离线工作站包')
    parser.add_argument('--release-tag', help='使用正式更新入口要求的安装文件名，须与当前程序构建标签一致')
    parser.add_argument('--freeze-provenance', type=Path, help='工作站冻结时生成的 FREEZE_PROVENANCE.json')
    parser.add_argument('--team-server-payload', type=Path)
    parser.add_argument('--team-server-commit')
    parser.add_argument('--team-server-manifest-sha256')
    parser.add_argument('--chromium-archive', type=Path)
    parser.add_argument('--desktop-source-commit')
    parser.add_argument('--chinese-messages', type=Path)
    parser.add_argument('--chinese-messages-sha256')
    args = parser.parse_args()
    build(Path(__file__).resolve().parents[1], args.built, args.destination, args.compiler, args.expected_exe_sha256,
          team_server_payload=args.team_server_payload, team_server_commit=args.team_server_commit,
          team_server_manifest_sha256=args.team_server_manifest_sha256, chromium_archive=args.chromium_archive,
          desktop_source_commit=args.desktop_source_commit, chinese_messages=args.chinese_messages,
          chinese_messages_sha256=args.chinese_messages_sha256, workstation=args.workstation,
          freeze_provenance=args.freeze_provenance, release_tag=args.release_tag)
