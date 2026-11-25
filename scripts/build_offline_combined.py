"""Store two verified role installers in a single self-contained Windows EXE."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess

from workbox.storage.native_files import plain_path
from workbox.updates.incremental_apply import _record


ROLES = ('workstation', 'office')
MAX_ROLE_SIZE = 200_000_000
MAX_SHELL_OVERHEAD = 5_000_000
INSTALLER = 'WorkBox-1.0.0-Complete-Offline-x64.exe'
RECIPE = Path(__file__).resolve().parents[1] / 'packaging' / 'workbox-offline-combined.iss'


def compile_wrapper(roles, output, compiler, *, chinese_messages, isolated_validation=False):
    output = plain_path(output)
    installer = output / INSTALLER
    if installer.exists():
        raise FileExistsError('整套安装包已经存在。请使用新的输出目录。')
    workstation, office = (roles[role] for role in ROLES)
    args = [str(plain_path(compiler)), '/Q', f'/DOutputDir={output}', f'/DChineseMessagesFile={chinese_messages}',
            '/DWorkstationInstaller=' + str(plain_path(workstation['root'])),
            '/DWorkstationSHA256=' + workstation['sha256'], '/DOfficeInstaller=' + str(plain_path(office['root'])),
            '/DOfficeSHA256=' + office['sha256']]
    if isolated_validation:
        args.append('/DCombinedTrial=1')
    with (output / 'compiler.log').open('xb') as log:
        subprocess.run([*args, str(RECIPE)], stdout=log, stderr=subprocess.STDOUT, check=True)
    return installer


def _validate_role(name, spec):
    if not (isinstance(spec['source_commit'], str) and re.fullmatch('[a-f0-9]{40}', spec['source_commit'])
            and isinstance(spec['sha256'], str) and re.fullmatch('[a-f0-9]{64}', spec['sha256'])
            and type(spec['size']) is int and 0 < spec['size'] < MAX_ROLE_SIZE):
        raise ValueError(f'{name} 角色的来源、摘要或大小无效。')
    if 'packaging_commit' in spec and (not isinstance(spec['packaging_commit'], str)
                                      or not re.fullmatch('[a-f0-9]{40}', spec['packaging_commit'])):
        raise ValueError(f'{name} 角色的打包提交号无效。')
    source = plain_path(spec['root'])
    if (source.suffix.casefold() != '.exe' or source.stat().st_size != spec['size']
            or _record(source)['sha256'] != spec['sha256']):
        raise ValueError(f'{name} 角色安装器与发布收据不一致。')
    return source


def build_combined(roles, output, compiler, *, chinese_messages, isolated_validation=False):
    if set(roles) != set(ROLES):
        raise ValueError('整套安装包需要独立的工作站和办公室服务器安装器。')
    if bool(roles['office'].get('isolation_trial')) != isolated_validation:
        raise ValueError('验收外壳只使用服务器隔离版，正式外壳只使用服务器生产版。')
    sources = {name: _validate_role(name, roles[name]) for name in ROLES}
    if sources['workstation'].samefile(sources['office']):
        raise ValueError('两个角色安装器必须是不同的文件。')
    output = plain_path(output)
    output.mkdir()
    artifact = compile_wrapper(roles, output, compiler, chinese_messages=chinese_messages,
                               isolated_validation=isolated_validation)
    for name in ROLES:
        _validate_role(name, roles[name])
    facts = _record(artifact)
    embedded_bytes = sum(roles[name]['size'] for name in ROLES)
    if not embedded_bytes <= facts['size'] <= embedded_bytes + MAX_SHELL_OVERHEAD:
        raise ValueError('整套安装包的大小与直接封装两个已核验安装器的预期不符。')
    receipt = {'installer': str(artifact), 'output_size': facts['size'], 'output_sha256': facts['sha256'],
               'shell_bytes': facts['size'] - embedded_bytes, 'isolated_validation': isolated_validation,
               'wrapper_recipe_sha256': _record(RECIPE)['sha256'],
               'roles': {name: {key: roles[name][key] for key in ('source_commit', 'packaging_commit', 'sha256', 'size')
                                if key in roles[name]}
                         for name in ROLES}}
    (output / 'BUILD_INFO.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--compiler', required=True, type=Path)
    parser.add_argument('--chinese-messages', required=True)
    parser.add_argument('--isolated-validation', action='store_true')
    args = parser.parse_args()
    spec = json.loads(args.config.read_text(encoding='utf-8'))
    print(json.dumps(build_combined(spec, args.output, args.compiler, chinese_messages=args.chinese_messages,
                                    isolated_validation=args.isolated_validation)))


if __name__ == '__main__':
    main()
