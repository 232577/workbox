"""Repository checks keep Python sources and build scripts in their own directories."""

import subprocess

import pytest

from scripts.repo_guard import inspect_file, inspect_repository


@pytest.mark.parametrize('path', ['app.py', 'main.pyw', 'build.ps1', 'LOCAL.PS1'])
def test_root_source_files_are_rejected(path):
    assert any('repository root' in reason for reason in inspect_file(path, b''))


@pytest.mark.parametrize(
    'path',
    ['workbox/__main__.py', 'workbox/storage/native_files.py', 'scripts/build.ps1',
     'tests/test_entry.py', 'README.md', 'pytest.ini'],
)
def test_managed_paths_and_root_configuration_are_allowed(path):
    assert inspect_file(path, b'') == []


def test_staged_source_layout_is_validated(tmp_path):
    subprocess.run(['git', 'init', '-q'], cwd=tmp_path, check=True)
    (tmp_path / 'helpers.py').touch()
    (tmp_path / 'scripts').mkdir()
    (tmp_path / 'scripts' / 'build.ps1').touch()
    subprocess.run(['git', 'add', '--', 'helpers.py', 'scripts/build.ps1'], cwd=tmp_path, check=True)

    errors = inspect_repository(tmp_path, staged=True)

    assert len(errors) == 1
    assert errors[0].startswith('helpers.py: ')
    assert 'repository root' in errors[0]
