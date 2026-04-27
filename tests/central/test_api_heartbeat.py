import json
import pytest
from backend.models.db import DB
from backend.central import models as m
from backend.central import tokens as tok


@pytest.fixture
def central_app(monkeypatch, tmp_path):
    monkeypatch.setenv("MODE", "central")
    monkeypatch.setenv("SNAPSHOT_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("SNAPSHOT_SECRET_KEY", "0" * 64)
    monkeypatch.setenv("SNAPSHOT_TEST_MODE", "1")
    import importlib, backend.config, backend.app
    importlib.reload(backend.config)
    importlib.reload(backend.app)
    from backend.app import create_app
    app = create_app()
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(central_app):
    return central_app.test_client()


@pytest.fixture
def conn(central_app):
    return central_app.config["DB_CONN"]


@pytest.fixture
def setup(conn):
    cid = m.create_client(conn, proyecto="superaccess-uno")
    plaintext, token_id = tok.issue(conn, cid, label="web01")
    return {"client_id": cid, "token_id": token_id, "token": plaintext}


def _good_payload():
    return {
        "event_id": "11111111-1111-1111-1111-111111111111",
        "ts": "2026-04-27T17:00:00Z",
        "client": {"proyecto": "superaccess-uno", "entorno": "cloud", "pais": "co"},
        "target": {"category": "os", "subkey": "linux", "label": "web01"},
        "operation": {"op": "archive", "status": "ok",
                      "started_at": "2026-04-27T17:00:00Z", "duration_s": 5,
                      "error": None},
        "snapshot": {"size_bytes": 1000, "remote_path": "x", "encrypted": True},
        "totals": {"size_bytes": 5000, "count_files": 1,
                   "oldest_ts": "2026-04-27T17:00:00Z",
                   "newest_ts": "2026-04-27T17:00:00Z"},
        "host_meta": {"hostname": "web01", "snapctl_version": "0.4",
                      "rclone_version": "v1.68"},
    }


def test_heartbeat_no_token_returns_401(client):
    r = client.post("/api/v1/heartbeat", json=_good_payload())
    assert r.status_code == 401


def test_heartbeat_bad_token_returns_401(client):
    r = client.post("/api/v1/heartbeat", json=_good_payload(),
                    headers={"Authorization": "Bearer wrongtoken"})
    assert r.status_code == 401


def test_heartbeat_good_token_accepted(client, setup):
    r = client.post("/api/v1/heartbeat", json=_good_payload(),
                    headers={"Authorization": f"Bearer {setup['token']}"})
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body["ok"] is True
    assert body["event_id"] == "11111111-1111-1111-1111-111111111111"


def test_heartbeat_invalid_schema_returns_400(client, setup):
    p = _good_payload(); p["target"]["category"] = "windows"
    r = client.post("/api/v1/heartbeat", json=p,
                    headers={"Authorization": f"Bearer {setup['token']}"})
    assert r.status_code == 400


def test_heartbeat_proyecto_mismatch_returns_409(client, setup):
    p = _good_payload(); p["client"]["proyecto"] = "otro"
    r = client.post("/api/v1/heartbeat", json=p,
                    headers={"Authorization": f"Bearer {setup['token']}"})
    assert r.status_code == 409


def test_heartbeat_idempotent_replay(client, setup):
    p = _good_payload()
    h = {"Authorization": f"Bearer {setup['token']}"}
    r1 = client.post("/api/v1/heartbeat", json=p, headers=h)
    r2 = client.post("/api/v1/heartbeat", json=p, headers=h)
    assert r1.status_code == 200 and r2.status_code == 200


def test_heartbeat_too_large_returns_413(client, setup):
    p = _good_payload()
    p["operation"]["error"] = "x" * 70_000  # rompe schema (error >500) o tamaño total
    r = client.post("/api/v1/heartbeat", json=p,
                    headers={"Authorization": f"Bearer {setup['token']}"})
    assert r.status_code in (400, 413)


def test_ping_no_auth_required(client):
    r = client.get("/api/v1/ping")
    assert r.status_code == 200
    assert r.get_json()["ok"] is True
