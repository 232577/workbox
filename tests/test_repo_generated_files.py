"""Exercise the repository boundary with anonymous generated paths."""

import pytest

from scripts.repo_guard import inspect_file


@pytest.mark.parametrize(
    'path',
    [
        '.worktrees/task/notes.md',
        '.pytest-tmp/fixture/report.txt',
        '.playwright-mcp/session.yml',
        'website/.wrangler/state.json',
        'tests/.pytest_cache/README.md',
        'scripts/__pycache__/runner.pyc',
    ],
)
def test_generated_paths_are_rejected_even_when_force_added(path):
    assert inspect_file(path, b'test output')


def test_versioned_technical_documentation_and_sample_reports_remain_allowed():
    assert inspect_file('docs/releases/notes.md', b'release notes') == []
    assert inspect_file('tests/fixtures/report.json', b'{}') == []
