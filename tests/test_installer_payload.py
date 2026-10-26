"""安装载荷必须排除个人数据，并在出包前核实既有程序。"""
import hashlib
from pathlib import Path

import pytest

from scripts.build_installer import prepare_payload


@pytest.fixture
def build_inputs(tmp_path):
    project, built = tmp_path/'project', tmp_path/'built'
    built.mkdir()
    (built/'WorkBox.exe').write_bytes(b'isolated frozen executable')
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
    assert (output/'_internal/PySide6/plugins/platforms/qwindows.dll').is_file()
    assert (output/'安装说明.txt').is_file() and (output/'licenses/LICENSE.txt').is_file()
    assert not (output/'data').exists()
    assert 'WorkBox.exe' in manifest and all(not name.endswith('.db') for name in manifest)


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
