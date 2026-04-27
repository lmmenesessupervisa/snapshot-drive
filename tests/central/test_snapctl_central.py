import json
import os
import subprocess
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PYBIN = os.path.join(PROJECT_ROOT, ".venv", "bin", "python")


def test_central_status_runs(tmp_path, monkeypatch):
    env = {**os.environ,
           "SNAPSHOT_DB_PATH": str(tmp_path / "t.db"),
           "MODE": "client",
           "CENTRAL_URL": "",
           "CENTRAL_TOKEN": ""}
    out = subprocess.check_output(
        [PYBIN, "-m", "backend.central.cli", "status"],
        env=env, cwd=PROJECT_ROOT,
    )
    j = json.loads(out)
    assert j["ok"] is True
    assert j["mode"] == "client"


def test_central_drain_noop_when_disabled(tmp_path):
    env = {**os.environ,
           "SNAPSHOT_DB_PATH": str(tmp_path / "t.db"),
           "MODE": "client",
           "CENTRAL_URL": "",
           "CENTRAL_TOKEN": ""}
    out = subprocess.check_output(
        [PYBIN, "-m", "backend.central.cli", "drain"],
        env=env, cwd=PROJECT_ROOT,
    )
    j = json.loads(out)
    assert j["ok"] is True
    assert j["drained"] == 0
