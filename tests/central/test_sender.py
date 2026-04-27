import pytest
from unittest.mock import MagicMock
from backend.central import sender, queue as q


@pytest.fixture(autouse=True)
def _config(monkeypatch):
    monkeypatch.setenv("CENTRAL_URL", "https://central.example.com")
    monkeypatch.setenv("CENTRAL_TOKEN", "tok123")
    monkeypatch.setenv("CENTRAL_TIMEOUT_S", "1")
    import importlib, backend.config
    importlib.reload(backend.config)
    # Re-import sender so it picks up the reloaded Config
    importlib.reload(sender)


def _payload():
    return {"event_id": "11111111-1111-1111-1111-111111111111", "x": 1}


def test_send_200_marks_done(conn, monkeypatch):
    q.enqueue(conn, _payload())
    fake = MagicMock(); fake.status_code = 200; fake.text = "ok"
    monkeypatch.setattr("backend.central.sender.requests.post",
                        lambda *a, **k: fake)
    n = sender.drain(conn)
    assert n == 1
    assert conn.execute("SELECT COUNT(*) FROM central_queue").fetchone()[0] == 0


def test_send_500_increments_attempts(conn, monkeypatch):
    q.enqueue(conn, _payload())
    fake = MagicMock(); fake.status_code = 500; fake.text = "boom"
    monkeypatch.setattr("backend.central.sender.requests.post",
                        lambda *a, **k: fake)
    sender.drain(conn)
    row = conn.execute("SELECT attempts, state FROM central_queue").fetchone()
    assert tuple(row) == (1, "pending")


def test_send_401_marks_dead_immediately(conn, monkeypatch):
    q.enqueue(conn, _payload())
    fake = MagicMock(); fake.status_code = 401; fake.text = "no"
    monkeypatch.setattr("backend.central.sender.requests.post",
                        lambda *a, **k: fake)
    sender.drain(conn)
    row = conn.execute("SELECT state FROM central_queue").fetchone()
    assert row[0] == "dead"


def test_send_no_central_url_is_noop(conn, monkeypatch):
    monkeypatch.setenv("CENTRAL_URL", "")
    import importlib, backend.config
    importlib.reload(backend.config)
    importlib.reload(sender)
    q.enqueue(conn, _payload())
    n = sender.drain(conn)
    assert n == 0
