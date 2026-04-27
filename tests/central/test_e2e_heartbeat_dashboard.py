"""E2E: cliente envía heartbeat → dashboard agregado lo refleja."""
import pytest
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


def _good_payload(eid, proyecto, total_bytes):
    return {
        "event_id": eid,
        "ts": "2026-04-27T17:00:00Z",
        "client": {"proyecto": proyecto, "entorno": "cloud", "pais": "co"},
        "target": {"category": "os", "subkey": "linux", "label": "host01"},
        "operation": {"op": "archive", "status": "ok",
                      "started_at": "2026-04-27T17:00:00Z",
                      "duration_s": 10, "error": None},
        "snapshot": {"size_bytes": 100_000_000, "remote_path": "x", "encrypted": True},
        "totals": {"size_bytes": total_bytes, "count_files": 5,
                   "oldest_ts": "2026-01-01T00:00:00Z",
                   "newest_ts": "2026-04-27T17:00:00Z"},
        "host_meta": {"hostname": "h", "snapctl_version": "0", "rclone_version": "0"},
    }


def test_heartbeat_then_dashboard_shows_client(client, conn):
    cid = m.create_client(conn, proyecto="alpha")
    plain, _ = tok.issue(conn, cid, label="x")
    payload = _good_payload(
        "abcdef00-0000-0000-0000-000000000001", "alpha", 500_000_000,
    )
    r = client.post("/api/v1/heartbeat", json=payload,
                    headers={"Authorization": f"Bearer {plain}"})
    assert r.status_code == 200, r.get_data(as_text=True)
    from tests.auth.helpers import create_user_and_login
    create_user_and_login(client, conn, role="auditor")
    r = client.get("/dashboard-central")
    assert r.status_code == 200
    assert b"alpha" in r.data


def test_heartbeat_replay_is_idempotent(client, conn):
    """N envíos del mismo event_id dejan 1 evento aplicado."""
    cid = m.create_client(conn, proyecto="beta")
    plain, _ = tok.issue(conn, cid, label="y")
    payload = _good_payload(
        "abcdef00-0000-0000-0000-000000000002", "beta", 200_000_000,
    )
    h = {"Authorization": f"Bearer {plain}"}
    for _ in range(5):
        r = client.post("/api/v1/heartbeat", json=payload, headers=h)
        assert r.status_code == 200
    n = conn.execute(
        "SELECT COUNT(*) FROM central_events WHERE client_id=?", (cid,)
    ).fetchone()[0]
    assert n == 1
