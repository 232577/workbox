import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def test_source_data_directory_stays_at_repository_root():
    from workbox.__main__ import default_data_dir

    assert default_data_dir() == ROOT / "data"


def test_entry_point_initializes_only_requested_empty_data_directory(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "workbox", "--data-dir", str(tmp_path / "personal-data"), "--check"],
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", timeout=15,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["version"] == "1.0.0"
    assert report["counts"] == {"projects": 0, "batches": 0, "zips": 0}
    assert Path(report["database"]) == tmp_path / "personal-data" / "workbox.db"
    assert not (tmp_path / "data").exists()


def test_cli_version_stays_one_point_zero():
    result = subprocess.run(
        [sys.executable, "-m", "workbox", "--version"],
        cwd=ROOT,
        capture_output=True, text=True, encoding="utf-8", timeout=15,
    )
    assert result.returncode == 0
    assert "1.0.0" in result.stdout
