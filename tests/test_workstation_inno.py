"""Compile anonymous installer fixtures and exercise setup initialization without installing."""
from pathlib import Path
import os
import shutil
import struct
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def compiler(tmp_path):
    compiler = shutil.which('ISCC.exe') or str(
        Path(os.environ.get('ProgramFiles(x86)', 'C:/Program Files (x86)')) /
        'Inno Setup 6/ISCC.exe')
    if not Path(compiler).is_file():
        pytest.skip('Inno Setup 6 is required for fixture compilation')
    payload = tmp_path / 'payload'
    payload.mkdir()
    for name in ('WorkBox.exe', 'WorkBoxUpdater.exe', '安装说明.txt'):
        (payload / name).write_bytes(b'anonymous compiler fixture')
    icon = tmp_path / 'fixture.ico'
    image = struct.pack('<IIIHHIIIIII', 40, 1, 2, 1, 32, 0, 4, 0, 0, 0, 0) + b'\x00' * 8
    icon.write_bytes(struct.pack('<HHH', 0, 1, 1) +
                     struct.pack('<BBBBHHII', 1, 1, 0, 0, 1, 32, len(image), 22) + image)

    def compile_variant(*defines, script=ROOT / 'packaging/workbox.iss'):
        return subprocess.run([
            compiler, '/Q', f'/DPayloadDir={payload}', f'/DOutputDir={tmp_path / "out"}',
            '/DPayloadManifestSHA256=' + 'a' * 64, '/DUpdaterSHA256=' + 'b' * 64,
            f'/DAppIcon={icon}', '/DChineseMessagesFile=compiler:Default.isl',
            *defines, str(script),
        ], capture_output=True, text=True, errors='replace', timeout=60)

    return compile_variant, tmp_path / 'out'


@pytest.mark.parametrize(('defines', 'filename'), [
    ((), 'WorkBox-1.0.0-Setup-x64.exe'),
    (('/DWorkstationPayload=1',), 'WorkBox-1.0.0-Workstation-Setup-x64.exe'),
])
def test_installer_variants_compile_to_distinct_delivery_names(compiler, defines, filename):
    compile_variant, output = compiler
    result = compile_variant(*defines)
    assert result.returncode == 0, result.stdout + result.stderr
    assert sorted(path.name for path in output.iterdir()) == [filename]
    assert (output / filename).read_bytes()[:2] == b'MZ'


def test_workstation_rejects_server_payload_at_compile_boundary(compiler, tmp_path):
    compile_variant, output = compiler
    result = compile_variant('/DWorkstationPayload=1', f'/DTeamServerPayloadDir={tmp_path}')
    assert result.returncode != 0
    assert 'WorkstationPayload cannot include TeamServerPayloadDir' in result.stdout + result.stderr
    assert not list(output.glob('*.exe'))


def test_workstation_rejects_standalone_chromium_at_compile_boundary(compiler):
    compile_variant, output = compiler
    result = compile_variant('/DWorkstationPayload=1', '/DChromiumManifestSHA256=' + 'a' * 64)
    assert result.returncode != 0
    assert 'WorkstationPayload cannot include standalone Chromium' in result.stdout + result.stderr
    assert not list(output.glob('*.exe'))


def test_invalid_workstation_role_flag_never_builds_a_generic_installer(compiler):
    compile_variant, output = compiler
    result = compile_variant('/DWorkstationPayload=0')
    assert result.returncode != 0
    assert 'WorkstationPayload must be 1' in result.stdout + result.stderr
    assert not list(output.glob('*.exe'))


def test_legacy_team_component_installer_still_compiles(compiler, tmp_path):
    compile_variant, output = compiler
    server = tmp_path / 'server'
    server.mkdir()
    (server / 'anonymous.txt').write_text('anonymous', encoding='utf-8')
    manifest = tmp_path / 'server.sha256'
    manifest.write_text('a' * 64 + '  anonymous.txt\n', encoding='utf-8')
    result = compile_variant(
        f'/DTeamServerPayloadDir={server}', '/DTeamServerManifestSHA256=' + 'a' * 64,
        '/DTeamServerMaxAppDir=120', '/DTeamServerVersion=anonymous',
        '/DChromiumManifestSHA256=' + 'b' * 64, f'/DTeamServerFilesManifest={manifest}',
        '/DTeamServerFilesManifestSHA256=' + 'c' * 64,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert sorted(path.name for path in output.iterdir()) == ['WorkBox-1.0.0-Team-Setup-x64.exe']


def test_upgrade_and_first_install_trials_select_separate_identities(compiler, tmp_path):
    compile_variant, output = compiler
    appdata = tmp_path / 'anonymous-localappdata'
    source = (ROOT / 'packaging/workbox.iss').read_text(encoding='utf-8-sig')
    source = source.replace('{localappdata}', str(appdata)).replace(
        'function InitializeSetup: Boolean;', 'function FixtureInitializeSetup: Boolean;', 1)
    script = tmp_path / 'inspect-setup.iss'
    script.write_text(source + '''
function InitializeSetup: Boolean;
begin
  if FixtureInitializeSetup then
    Log('Workstation fixture identity: ' + WorkBoxAppId('') + '|' + WorkBoxDefaultDirectory(''))
  else
    Log('Workstation fixture refused');
  { Exit initialization in every case. No program, registry or shortcut is installed. }
  Result := False;
end;
''', encoding='utf-8-sig')
    result = compile_variant('/DWorkstationPayload=1', script=script)
    assert result.returncode == 0, result.stdout + result.stderr
    installer = output / 'WorkBox-1.0.0-Workstation-Setup-x64.exe'

    def initialize_fixture(name, *switches):
        log = tmp_path / (name + '.log')
        result = subprocess.run([str(installer), '/SP-', '/VERYSILENT', '/SUPPRESSMSGBOXES',
                                 '/NORESTART', f'/LOG={log}', *switches], timeout=30)
        assert result.returncode != 0
        return log.read_text(encoding='utf-8-sig')

    for name, flag, id_, folder in [
        ('product', (), '94062298-8991-4FF2-9E56-6B837B20A5FD', 'Programs/WorkBox'),
        ('fresh', ('/WORKBOXTRIAL=1',), 'E806D856-21C2-4D98-BD71-D392DFE76CB8', 'WorkBoxWorkstationTrial/program'),
        ('upgrade', ('/WORKBOXTRIAL=upgrade',), '5189504C-7ED9-4B90-A4AA-0DCA5BBFB196',
         'WorkBoxWorkstationUpgradeTrial/program'),
    ]:
        assert f'Workstation fixture identity: {{{id_}}}|{appdata / folder}' in initialize_fixture(name, *flag)
    assert 'Workstation fixture refused' in initialize_fixture(
        'wrong-dir', '/WORKBOXTRIAL=upgrade', f'/DIR={appdata / "WorkBoxWorkstationTrial/program"}')
    assert 'Workstation fixture refused' in initialize_fixture('typo', '/WORKBOXTRIAL=upgrad')
    assert not appdata.exists()
