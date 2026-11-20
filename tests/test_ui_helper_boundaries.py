import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize('module_name', [
    'archive_classification_ui',
    'delivery_ui',
    'onboarding_ui',
    'project_documents_ui',
])
def test_standalone_ui_import_does_not_load_workbench(module_name):
    probe = (
        'import importlib, sys\n'
        'importlib.import_module(sys.argv[1])\n'
        'assert "compact_ui" not in sys.modules, "standalone UI loaded the workbench"\n'
    )
    result = subprocess.run(
        [sys.executable, '-c', probe, module_name],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
