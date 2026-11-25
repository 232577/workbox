from pathlib import Path
import hashlib
import subprocess

import pytest

from scripts.build_offline_combined import build_combined, compile_wrapper


COMPILER = Path(r'C:\Program Files (x86)\Inno Setup 6\ISCC.exe')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_role(root, label, *, fail=False):
    recipe = root / (label + '.iss')
    marker = root / (label + '.once')
    if fail:
        marker.write_text('fail the first anonymous install', encoding='utf-8')
    code = f'''
[Code]
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and FileExists('{marker}') then begin
    DeleteFile('{marker}');
    RaiseException('anonymous child failure');
  end;
end;
''' if fail else ''
    recipe.write_text(f'''[Setup]
AppName=Anonymous {label}
AppVersion=1.0
CreateAppDir=no
DefaultDirName={{tmp}}\\AnonymousRole
Uninstallable=no
CreateUninstallRegKey=no
PrivilegesRequired=lowest
OutputDir={root}
OutputBaseFilename={label}
{code}
''', encoding='utf-8')
    subprocess.run([str(COMPILER), '/Q', str(recipe)], cwd=root, check=True, timeout=40)
    executable = root / (label + '.exe')
    return {'root': str(executable), 'sha256': digest(executable), 'size': executable.stat().st_size,
            'source_commit': 'a' * 40}


@pytest.fixture
def roles(tmp_path):
    if not COMPILER.is_file():
        pytest.skip('Inno Setup 6 is needed for executable role fixtures')
    return {'workstation': make_role(tmp_path, 'workstation'),
            'office': make_role(tmp_path, 'office'),
            'broken': make_role(tmp_path, 'broken-office', fail=True)}


def run_wrapper(artifact, root, name, components):
    log = root / (name + '.log')
    status = subprocess.run([str(artifact), '/SP-', '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
                             f'/COMPONENTS={components}', f'/LOG={log}'], cwd=root, timeout=70)
    return status.returncode, log.read_text(encoding='utf-8-sig')


def test_selected_roles_are_extracted_and_run_as_one_offline_file(tmp_path, roles):
    output = tmp_path / 'candidate'
    result = build_combined({name: roles[name] for name in ('workstation', 'office')}, output, COMPILER,
                            chinese_messages='compiler:Default.isl')
    artifact = Path(result['installer'])
    assert result['output_sha256'] == digest(artifact)
    assert result['output_size'] == artifact.stat().st_size
    exit_code, log = run_wrapper(artifact, tmp_path, 'both', 'workstation,office')
    assert exit_code == 0
    assert 'Role workstation: success' in log and 'Role office: success' in log

    exit_code, log = run_wrapper(artifact, tmp_path, 'single', 'workstation')
    assert exit_code == 0
    assert 'Dest filename:' in log and '\\workbox-child-installers\\workstation.exe' in log
    assert '\\workbox-child-installers\\office.exe' not in log
    assert 'Role office: skipped' in log


def test_partial_failure_returns_nonzero_and_failed_role_can_be_selected_on_retry(tmp_path, roles):
    output = tmp_path / 'candidate'
    result = build_combined({'workstation': roles['workstation'], 'office': roles['broken']}, output, COMPILER,
                            chinese_messages='compiler:Default.isl')
    artifact = Path(result['installer'])
    exit_code, log = run_wrapper(artifact, tmp_path, 'both', 'workstation,office')
    assert exit_code != 0
    assert 'Role workstation: success' in log
    assert 'Role office: failed (post-installation exception)' in log
    retry, retry_log = run_wrapper(artifact, tmp_path, 'only-office', 'office')
    assert retry == 0
    assert 'Role workstation: skipped' in retry_log
    assert 'Role office: success' in retry_log
    assert '\\workbox-child-installers\\workstation.exe' not in retry_log


def test_two_role_digest_gate_runs_neither_when_one_embedded_child_is_wrong(tmp_path, roles):
    output = tmp_path / 'candidate'
    specified = {'workstation': roles['workstation'], 'office': dict(roles['broken'], sha256=roles['office']['sha256'])}
    with pytest.raises(ValueError, match='与发布收据不一致'):
        build_combined(specified, output, COMPILER, chinese_messages='compiler:Default.isl')
    assert not output.exists()
    output.mkdir()
    artifact = compile_wrapper(specified, output, COMPILER, chinese_messages='compiler:Default.isl')
    exit_code, log = run_wrapper(artifact, tmp_path, 'bad-digest', 'workstation,office')
    assert exit_code != 0
    assert 'Role office: checksum mismatch' in log
    assert 'Launching role' not in log
    assert (tmp_path / 'broken-office.once').is_file()
