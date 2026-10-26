import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def test_entry_point_initializes_only_requested_empty_data_directory(tmp_path):
    main = ROOT / "main.py"
    assert main.exists(), "Task 4: main.py missing"
    result = subprocess.run(
        [sys.executable, str(main), "--data-dir", str(tmp_path / "personal-data"), "--check"],
        cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", timeout=15,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["version"] == "1.0.0"
    assert report["counts"] == {"projects": 0, "batches": 0, "zips": 0}
    assert Path(report["database"]) == tmp_path / "personal-data" / "workbox.db"


def test_cli_version_stays_one_point_zero():
    assert (ROOT / "main.py").exists(), "Task 4: main.py missing"
    result = subprocess.run(
        [sys.executable, str(ROOT / "main.py"), "--version"],
        capture_output=True, text=True, encoding="utf-8", timeout=15,
    )
    assert result.returncode == 0
    assert "1.0.0" in result.stdout
