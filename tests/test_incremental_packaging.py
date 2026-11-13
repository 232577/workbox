"""独立标准库助手和普通安装载荷的打包契约；不执行安装器。"""
import hashlib
from pathlib import Path

import pytest

from scripts.build_installer import prepare_payload

ROOT = Path(__file__).resolve().parents[1]


def fixture(tmp_path, *, helper=b'MZ anonymous updater fixture'):
    project, built = tmp_path / 'project', tmp_path / 'built'
    files = {'WorkBox.exe': b'MZ anonymous application',
             '_internal/python311.dll': b'anonymous python',
             '_internal/PySide6/Qt6Core.dll': b'anonymous Qt',
             '_internal/PySide6/plugins/platforms/qwindows.dll': b'anonymous platform'}
    if helper is not None:
        files['WorkBoxUpdater.exe'] = helper
    for name, data in files.items():
        path = built / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
    for name in ('packaging/安装说明.txt', 'THIRD_PARTY_NOTICES.md', 'licenses/LICENSE.txt'):
        path = project / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text('anonymous', encoding='utf-8')
    return project, built, hashlib.sha256(files['WorkBox.exe']).hexdigest()


def test_current_payload_requires_and_measures_independent_helper(tmp_path):
    project, built, digest = fixture(tmp_path)
    destination = tmp_path / 'payload'
    manifest = prepare_payload(project, built, destination, digest)
    assert (destination / 'WorkBoxUpdater.exe').read_bytes() == (built / 'WorkBoxUpdater.exe').read_bytes()
    assert manifest['WorkBoxUpdater.exe'] == hashlib.sha256((built / 'WorkBoxUpdater.exe').read_bytes()).hexdigest()
    assert manifest['WorkBoxUpdater.exe'] + '  WorkBoxUpdater.exe\n' in (destination / 'PACKAGE_CONTENTS.sha256').read_text(encoding='utf-8')


@pytest.mark.parametrize('helper', [None, b'', b'not a Windows executable'])
def test_missing_or_non_program_helper_rejected_before_output(tmp_path, helper):
    project, built, digest = fixture(tmp_path, helper=helper)
    destination = tmp_path / 'payload'
    with pytest.raises(ValueError):
        prepare_payload(project, built, destination, digest)
    assert not destination.exists()


def test_build_script_builds_helper_before_main_and_checks_same_byte_copy():
    script = (ROOT / 'build.ps1').read_text(encoding='utf-8')
    invocations = script.split('& $Python -m PyInstaller ')
    assert len(invocations) == 3
    helper, main = invocations[1], invocations[2]
    command = helper.split('if ($LASTEXITCODE')[0]
    assert '--onefile' in command and '--windowed' in command and '--noupx' in command
    assert '--name WorkBoxUpdater' in command and 'update_helper_main.py' in command
    assert '--exclude-module PySide6' in command and '--collect-data' not in command
    assert '--onedir' in main and '--name WorkBox ' in main
    assert '--self-check' in helper and '--noninteractive' in helper
    assert 'Start-Process' in helper and '-Wait' in helper and '-WindowStyle Hidden' in helper
    assert 'ExitCode' in helper
    assert 'Get-FileHash' in script and 'WorkBoxUpdater.exe' in main
    assert '.venv\u005cScripts\u005cpython.exe' in script


def test_installer_preprocessor_requires_helper_without_user_data_deletion():
    script = (ROOT / 'packaging/workbox.iss').read_text(encoding='utf-8')
    assert '#if !FileExists(PayloadDir + "\\WorkBoxUpdater.exe")' in script
    assert 'Source: "{#PayloadDir}\\*"' in script
    assert '[InstallDelete]' not in script and '[UninstallDelete]' not in script
