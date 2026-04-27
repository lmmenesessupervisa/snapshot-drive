import pytest
from unittest.mock import MagicMock
from backend.central.alerts import dispatch as d


def test_notify_no_op_when_no_smtp_no_webhook(conn, monkeypatch):
    monkeypatch.setattr(d.Config, "SMTP_HOST", "", raising=False)
    monkeypatch.setattr(d.Config, "ALERTS_WEBHOOK", "", raising=False)
    cid = conn.execute(
        "INSERT INTO clients(proyecto, created_at, updated_at) "
        "VALUES('p','x','x')"
    ).lastrowid
    aid = conn.execute(
        "INSERT INTO central_alerts(type, client_id, severity, "
        "triggered_at, last_seen_at) VALUES (?,?,?,?,?)",
        ("folder_missing", cid, "warning", "now", "now"),
    ).lastrowid
    sent = d.notify(conn,
                    alert={"id": aid, "type": "folder_missing",
                           "severity": "warning", "detail": {},
                           "triggered_at": "now"},
                    client={"proyecto": "p"},
                    target={"label": "t", "category": "os", "subkey": "x"})
    assert sent == {"email": False, "webhook": False}
    # notified_at marked even when no channel sent
    row = conn.execute(
        "SELECT notified_at FROM central_alerts WHERE id=?", (aid,)
    ).fetchone()
    assert row[0] is not None


def test_notify_webhook_posts_json(conn, monkeypatch):
    monkeypatch.setattr(d.Config, "ALERTS_WEBHOOK",
                        "https://hook.example/x", raising=False)
    monkeypatch.setattr(d.Config, "SMTP_HOST", "", raising=False)
    cid = conn.execute(
        "INSERT INTO clients(proyecto, created_at, updated_at) "
        "VALUES('p2','x','x')"
    ).lastrowid
    aid = conn.execute(
        "INSERT INTO central_alerts(type, client_id, severity, "
        "triggered_at, last_seen_at) VALUES (?,?,?,?,?)",
        ("no_heartbeat", cid, "critical", "t", "t"),
    ).lastrowid
    fake = MagicMock(); fake.status_code = 200
    posted = {}
    def _post(url, json=None, timeout=None):
        posted["url"] = url
        posted["json"] = json
        return fake
    monkeypatch.setattr(d.requests, "post", _post)
    d.notify(conn,
             alert={"id": aid, "type": "no_heartbeat",
                    "severity": "critical", "detail": {}, "triggered_at": "t"},
             client={"proyecto": "alpha"},
             target={"label": "host01", "category": "os", "subkey": "linux"})
    assert posted["url"] == "https://hook.example/x"
    assert posted["json"]["event"] == "alert.fired"
    assert posted["json"]["alert"]["id"] == aid
