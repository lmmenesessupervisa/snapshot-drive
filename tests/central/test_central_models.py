import json
import pytest
from backend.central import models as m


@pytest.fixture
def client_id(conn):
    return m.create_client(conn, proyecto="superaccess-uno",
                           organizacion="superaccess s.a.")


def _payload(eid="11111111-1111-1111-1111-111111111111", op="archive",
             status="ok", size=1000, total=5000):
    return {
        "event_id": eid,
        "ts": "2026-04-27T17:00:00Z",
        "client": {"proyecto": "superaccess-uno", "entorno": "cloud", "pais": "co"},
        "target": {"category": "os", "subkey": "linux", "label": "web01"},
        "operation": {"op": op, "status": status,
                      "started_at": "2026-04-27T17:00:00Z", "duration_s": 5,
                      "error": None},
        "snapshot": {"size_bytes": size, "remote_path": "x", "encrypted": True},
        "totals": {"size_bytes": total, "count_files": 1,
                   "oldest_ts": "2026-04-27T17:00:00Z",
                   "newest_ts": "2026-04-27T17:00:00Z"},
        "host_meta": {"hostname": "web01", "snapctl_version": "0.4",
                      "rclone_version": "v1.68"},
    }


def test_create_client_unique(conn):
    cid = m.create_client(conn, proyecto="alpha")
    assert isinstance(cid, int)
    with pytest.raises(Exception):
        m.create_client(conn, proyecto="alpha")


def test_first_heartbeat_creates_target(conn, client_id):
    res = m.apply_heartbeat(conn, _payload(), token_id=1, client_id=client_id, src_ip="1.2.3.4")
    assert res.target_created is True
    assert res.event_inserted is True
    row = conn.execute("SELECT category, subkey, label FROM targets").fetchone()
    assert tuple(row) == ("os", "linux", "web01")


def test_second_heartbeat_updates_target(conn, client_id):
    m.apply_heartbeat(conn, _payload(eid="aaaa1111-1111-1111-1111-111111111111", size=1000, total=1000),
                      token_id=1, client_id=client_id, src_ip="1.1.1.1")
    m.apply_heartbeat(conn, _payload(eid="bbbb2222-2222-2222-2222-222222222222", size=2000, total=3000),
                      token_id=1, client_id=client_id, src_ip="1.1.1.1")
    row = conn.execute(
        "SELECT last_size_bytes, total_size_bytes FROM targets"
    ).fetchone()
    assert tuple(row) == (2000, 3000)


def test_duplicate_event_id_is_silent(conn, client_id):
    p = _payload(eid="cccc3333-3333-3333-3333-333333333333")
    r1 = m.apply_heartbeat(conn, p, token_id=1, client_id=client_id, src_ip="x")
    r2 = m.apply_heartbeat(conn, p, token_id=1, client_id=client_id, src_ip="x")
    assert r1.event_inserted is True
    assert r2.event_inserted is False
    n = conn.execute("SELECT COUNT(*) FROM central_events").fetchone()[0]
    assert n == 1


def test_dashboard_query_aggregates(conn, client_id):
    cid2 = m.create_client(conn, proyecto="orus")
    p1 = _payload(eid="11111111-1111-1111-1111-111111111111", total=10_000)
    m.apply_heartbeat(conn, p1, token_id=1, client_id=client_id, src_ip="x")
    p2 = _payload(eid="22222222-2222-2222-2222-222222222222", total=20_000)
    p2["target"]["label"] = "web02"
    m.apply_heartbeat(conn, p2, token_id=1, client_id=client_id, src_ip="x")
    rows = m.dashboard_summary(conn)
    by_proj = {r["proyecto"]: r for r in rows}
    assert by_proj["superaccess-uno"]["targets_count"] == 2
    assert by_proj["superaccess-uno"]["total_bytes"] == 30_000
    assert by_proj["orus"]["targets_count"] == 0
