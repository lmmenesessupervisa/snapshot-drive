import json
import os
import subprocess

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PYBIN = os.path.join(PROJECT_ROOT, ".venv", "bin", "python")


def test_alerts_sweep_runs_with_no_targets(tmp_path):
    env = {**os.environ,
           "SNAPSHOT_DB_PATH": str(tmp_path / "t.db"),
           "MODE": "central",
           "ALERTS_NO_HEARTBEAT_HOURS": "48"}
    out = subprocess.check_output(
        [PYBIN, "-m", "backend.central.cli", "alerts-sweep"],
        env=env, cwd=PROJECT_ROOT,
    )
    j = json.loads(out)
    assert j["ok"] is True
    assert j["fired"] == 0
