"""安装载荷必须排除个人数据，并在出包前核实既有程序。"""
import hashlib
import json
from pathlib import Path

import pytest

from scripts.build_installer import prepare_payload


@pytest.fixture
def build_inputs(tmp_path):
    project, built = tmp_path/'project', tmp_path/'built'
    built.mkdir()
    (built/'WorkBox.exe').write_bytes(b'isolated frozen executable')
    (built/'WorkBoxUpdater.exe').write_bytes(b'MZ isolated updater executable')
    for name in ('python311.dll', 'PySide6/Qt6Core.dll', 'PySide6/plugins/platforms/qwindows.dll'):
        path = built/'_internal'/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'isolated runtime')
    (project/'licenses').mkdir(parents=True)
    (project/'licenses/LICENSE.txt').write_text('isolated license', encoding='utf-8')
    (project/'packaging').mkdir()
    (project/'packaging/安装说明.txt').write_text('隔离安装说明', encoding='utf-8')
    (project/'THIRD_PARTY_NOTICES.md').write_text('isolated notices', encoding='utf-8')
    return project, built, hashlib.sha256((built/'WorkBox.exe').read_bytes()).hexdigest()


def test_payload_contains_runtime_and_not_project_personal_files(build_inputs, tmp_path):
    project, built, digest = build_inputs
    (project/'data').mkdir()
    (project/'data/private.db').write_bytes(b'personal data must remain outside')
    output = tmp_path/'payload'
    manifest = prepare_payload(project, built, output, digest)
    assert (output/'WorkBox.exe').read_bytes() == (built/'WorkBox.exe').read_bytes()
    assert (output/'WorkBoxUpdater.exe').read_bytes() == (built/'WorkBoxUpdater.exe').read_bytes()
    assert (output/'_internal/PySide6/plugins/platforms/qwindows.dll').is_file()
    assert (output/'安装说明.txt').is_file() and (output/'licenses/LICENSE.txt').is_file()
    assert not (output/'data').exists()
    assert {'WorkBox.exe', 'WorkBoxUpdater.exe'} <= manifest.keys()
    assert all(not name.endswith('.db') for name in manifest)


@pytest.mark.parametrize('extra', ['data/workbox.db', '客户资料.docx', '_internal/private.db'])
def test_contaminated_build_is_rejected_before_output_is_created(build_inputs, tmp_path, extra):
    project, built, digest = build_inputs
    path = built/extra
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'private file')
    output = tmp_path/'payload'
    with pytest.raises(ValueError):
        prepare_payload(project, built, output, digest)
    assert not output.exists()


def test_unverified_build_and_existing_output_are_never_overwritten(build_inputs, tmp_path):
    project, built, digest = build_inputs
    output = tmp_path/'payload'
    with pytest.raises(ValueError):
        prepare_payload(project, built, output, '0'*64)
    assert not output.exists()
    output.mkdir()
    sentinel = output/'keep.txt'
    sentinel.write_text('keep', encoding='utf-8')
    with pytest.raises(FileExistsError):
        prepare_payload(project, built, output, digest)
    assert sentinel.read_text(encoding='utf-8') == 'keep'


def test_full_install_budget_is_enforced_before_compiler_starts(build_inputs, tmp_path, monkeypatch):
    from scripts import build_installer
    from workbox.updates import installer_runtime

    project, built, app_sha = build_inputs
    compiler = tmp_path / 'anonymous-compiler.exe'
    compiler.touch()
    monkeypatch.setattr(installer_runtime, 'MAX_FULL_PAYLOAD_SIZE', 1)

    def compile_fixture(*args, **kwargs):
        pytest.fail('an over-budget payload must never reach Inno Setup')

    monkeypatch.setattr(build_installer.subprocess, 'run', compile_fixture)
    with pytest.raises(ValueError, match='程序载荷总长度'):
        build_installer.build(project, built, tmp_path / 'installer', compiler, app_sha)


@pytest.mark.parametrize('helper_bytes', [b'MZ anonymous first updater', b'MZ anonymous second updater'])
def test_compiler_receives_exact_prepared_manifest_and_updater_digests(build_inputs, tmp_path, monkeypatch, helper_bytes):
    from types import SimpleNamespace
    from scripts import build_installer

    project, built, app_sha = build_inputs
    (built / 'WorkBoxUpdater.exe').write_bytes(helper_bytes)
    compiler = tmp_path / 'anonymous-compiler.exe'
    compiler.write_bytes(b'anonymous compiler placeholder; never executed')
    destination = tmp_path / 'installer'
    calls = []

    def compile_fixture(command, **options):
        calls.append(command)
        output = Path(next(value.removeprefix('/DOutputDir=') for value in command
                           if value.startswith('/DOutputDir=')))
        (output / 'WorkBox-1.0.0-Setup-x64.exe').write_bytes(b'MZ anonymous inert installer')
        return SimpleNamespace(returncode=0, stdout=b'anonymous compiler output')

    monkeypatch.setattr(build_installer.subprocess, 'run', compile_fixture)
    report = build_installer.build(project, built, destination, compiler, app_sha)
    manifest_sha = hashlib.sha256((destination / 'payload/PACKAGE_CONTENTS.sha256').read_bytes()).hexdigest()
    updater_sha = hashlib.sha256(helper_bytes).hexdigest()
    assert len(calls) == 1
    assert calls[0].count('/DPayloadManifestSHA256=' + manifest_sha) == 1
    assert calls[0].count('/DUpdaterSHA256=' + updater_sha) == 1
    assert report['payload_manifest_sha256'] == manifest_sha
    assert report['updater_sha256'] == updater_sha
    assert report['runtime_validation'] == 'pending'


@pytest.fixture
def workstation_inputs(build_inputs, monkeypatch):
    from scripts import stage_webengine_runtime as webengine

    project, built, digest = build_inputs
    qt = built / '_internal/PySide6'
    for relative in (*webengine.WEBENGINE_FILES, 'QtWebEngineCore.pyd', 'QtWebEngineWidgets.pyd'):
        path = qt / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'anonymous Qt file')
    runtimes = {}
    for name in webengine.MSVC_RUNTIME:
        payload = name.encode('ascii')
        (qt / name).write_bytes(payload)
        runtimes[name] = len(payload), hashlib.sha256(payload).hexdigest()
    monkeypatch.setattr(webengine, 'MSVC_RUNTIME', runtimes)
    (project / 'packaging/安装说明.txt').write_text(
        '隔离安装说明\n\n团队交件与办公室服务器\n旧版引导使用后备 Chromium。\n\n项目操作与交付记录\n正文。\n',
        encoding='utf-8',
    )
    return project, built, digest


@pytest.mark.parametrize('relative', [
    '_internal/playwright/driver/node.exe',
    '_internal/Playwright-1.62.0.dist-info/METADATA',
    '_internal/pyee-13.0.0.dist-info/METADATA',
    '_internal/greenlet/_greenlet.pyd',
    '_internal/chromium-1754043/chrome.exe',
    '_internal/driver/node.exe',
    '_internal/TeamServer/Kanboard.cmd',
])
def test_workstation_payload_rejects_extra_engines_and_server_before_copy(workstation_inputs, tmp_path, relative):
    project, built, digest = workstation_inputs
    unexpected = built / relative
    unexpected.parent.mkdir(parents=True, exist_ok=True)
    unexpected.write_bytes(b'anonymous forbidden payload')
    output = tmp_path / 'payload'

    with pytest.raises(ValueError, match='工作站'):
        prepare_payload(project, built, output, digest, workstation=True)

    assert not output.exists()


@pytest.mark.parametrize('problem', ['missing-renderer', 'wrong-vc-bytes'])
def test_workstation_payload_requires_app_local_engine_and_vc_runtime(workstation_inputs, tmp_path, problem):
    project, built, digest = workstation_inputs
    qt = built / '_internal/PySide6'
    if problem == 'missing-renderer':
        (qt / 'QtWebEngineProcess.exe').unlink()
    else:
        (qt / 'msvcp140.dll').write_bytes(b'msvcp141.dll')
    output = tmp_path / 'payload'

    with pytest.raises(ValueError, match='Qt'):
        prepare_payload(project, built, output, digest, workstation=True)

    assert not output.exists()


def test_workstation_guide_preserves_desktop_help_and_explains_separate_office(workstation_inputs, tmp_path):
    project, built, digest = workstation_inputs
    output = tmp_path / 'payload'

    manifest = prepare_payload(project, built, output, digest, workstation=True)

    assert '_internal/PySide6/QtWebEngineProcess.exe' in manifest
    guide = (output / '安装说明.txt').read_text(encoding='utf-8')
    assert '办公室服务器安装包' in guide
    assert '项目操作与交付记录' in guide
    assert '后备 Chromium' not in guide


@pytest.mark.parametrize('case', ['at-limit', 'over-limit', 'stale-updater', 'changed-qt', 'release'])
def test_workstation_compiles_with_provenance_and_only_exposes_in_budget_output(
        workstation_inputs, tmp_path, monkeypatch, case):
    import subprocess
    from types import SimpleNamespace

    from scripts import build_installer, stage_webengine_runtime as webengine

    project, built, digest = workstation_inputs
    subprocess.run(['git', 'init', '-q', project], check=True)
    subprocess.run(['git', '-C', project, 'add', '.'], check=True)
    subprocess.run(['git', '-C', project, '-c', 'user.name=Anonymous',
                    '-c', 'user.email=fixture@example.invalid', '-c', 'commit.gpgsign=false',
                    'commit', '-qm', 'Anonymous source fixture'], check=True)
    commit = subprocess.check_output(['git', '-C', project, 'rev-parse', 'HEAD'], text=True).strip()
    qt = built / '_internal/PySide6'
    qt_manifest = tmp_path / 'qt-engine-files.json'
    qt_manifest.write_text(json.dumps({name: hashlib.sha256((qt / name).read_bytes()).hexdigest()
                                      for name in (*webengine.WEBENGINE_FILES, *webengine.MSVC_RUNTIME)}),
                           encoding='utf-8')
    updater_sha = hashlib.sha256((built / 'WorkBoxUpdater.exe').read_bytes()).hexdigest()
    receipt = tmp_path / 'FREEZE_PROVENANCE.json'
    receipt.write_text(json.dumps({'variant': 'workstation', 'source_commit': commit,
                                  'updater_sha256': updater_sha, 'executable_sha256': digest,
                                  'qt_manifest_sha256': hashlib.sha256(qt_manifest.read_bytes()).hexdigest(),
                                  'frozen_path': str(built)}), encoding='utf-8')
    if case == 'stale-updater':
        (built / 'WorkBoxUpdater.exe').write_bytes(b'MZ anonymous stale updater')
    if case == 'changed-qt':
        (qt / 'Qt6WebEngineCore.dll').write_bytes(b'anonymous replacement Qt')
    compiler = tmp_path / 'anonymous-compiler.exe'
    compiler.touch()
    destination = tmp_path / 'installer'
    real_run = subprocess.run
    calls = []

    def compile_fixture(command, **kwargs):
        if command[0] != str(compiler):
            return real_run(command, **kwargs)
        calls.append(command)
        output = Path(next(argument for argument in command if argument.startswith('/DOutputDir=')).split('=', 1)[1])
        basename = next((arg[2:] for arg in command if arg.startswith('/F')), 'WorkBox-1.0.0-Workstation-Setup-x64')
        (output / (basename + '.exe')).write_bytes(
            b'MZ' + bytes(99 if case == 'over-limit' else 98))
        return SimpleNamespace(returncode=0, stdout=b'anonymous compiler output')

    monkeypatch.setattr(build_installer.subprocess, 'run', compile_fixture)
    monkeypatch.setattr(build_installer, 'MAX_WORKSTATION_INSTALLER_SIZE', 100, raising=False)
    options = dict(workstation=True, desktop_source_commit=commit, freeze_provenance=receipt)
    installer = destination / 'output/WorkBox-1.0.0-Workstation-Setup-x64.exe'
    if case == 'release':
        from workbox.updates.update_client import CURRENT_TAG, build_key
        date, revision = build_key(CURRENT_TAG)
        suffix = str(date) + (f'-r{revision}' if revision else '')
        expected = f'WorkBox-1.0.0-Setup-x64-{suffix}.exe'
        report = build_installer.build(project, built, destination, compiler, digest, release_tag=CURRENT_TAG, **options)
        assert report['installer_name'] == Path(report['installer']).name == expected
        assert report['release_tag'] == CURRENT_TAG
        assert (destination / 'output' / expected).is_file()
        return
    if case in {'stale-updater', 'changed-qt'}:
        with pytest.raises(ValueError, match='更新助手|Qt'):
            build_installer.build(project, built, destination, compiler, digest, **options)
        assert not calls
        assert not destination.exists()
        return
    if case == 'over-limit':
        with pytest.raises(RuntimeError, match='101.*100'):
            build_installer.build(project, built, destination, compiler, digest, **options)
        assert not installer.exists()
        assert not (destination / 'BUILD_INFO.json').exists()
        assert len(list(destination.glob('**/*.oversize'))) == 1
    else:
        report = build_installer.build(project, built, destination, compiler, digest, **options)
        assert installer.stat().st_size == report['installer_size'] == 100
        assert report['desktop_source_commit'] == commit
        assert report['updater_sha256'] == updater_sha
        assert report['package_role'] == 'workstation'

    assert len(calls) == 1
    assert '/DWorkstationPayload=1' in calls[0]
    assert not any('TeamServerPayloadDir' in value or 'ChromiumManifest' in value for value in calls[0])


@pytest.mark.parametrize('options, message', [
    ({'chromium_archive': Path('browser.zip')}, '独立 Chromium'),
    ({'team_server_payload': Path('server')}, '办公室服务器'),
    ({}, '冻结凭据'),
])
def test_workstation_rejects_incompatible_or_unverified_inputs(workstation_inputs, tmp_path, options, message):
    from scripts import build_installer

    project, built, digest = workstation_inputs
    compiler = tmp_path / 'anonymous-compiler.exe'
    compiler.touch()
    destination = tmp_path / 'installer'
    with pytest.raises(ValueError, match=message):
        build_installer.build(project, built, destination, compiler, digest, workstation=True, **options)

    assert not destination.exists()
