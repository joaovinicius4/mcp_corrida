import json
import subprocess
import sys


def test_cli_import_read_export_and_errors(tmp_path, workbook_file):
    base = [sys.executable, "-m", "coach_corrida.cli", "--data-dir", str(tmp_path / "store")]
    imported = subprocess.run(base+["import", str(workbook_file)], capture_output=True, text=True, encoding="utf-8", timeout=15)
    assert imported.returncode == 0, imported.stderr
    assert json.loads(imported.stdout)["new_activities"] == 3
    output = tmp_path / "snapshot.json"
    exported = subprocess.run(base+["export", str(output)], capture_output=True, text=True, encoding="utf-8", timeout=15)
    assert exported.returncode == 0, exported.stderr
    snapshot = json.loads(output.read_text(encoding="utf-8"))
    assert len(snapshot["tables"]["activities"]) == 3
    assert "samples" not in snapshot["tables"]
    missing = subprocess.run(base+["detail", "9999"], capture_output=True, text=True, encoding="utf-8", timeout=15)
    assert missing.returncode == 2
    assert json.loads(missing.stderr)["error"]
