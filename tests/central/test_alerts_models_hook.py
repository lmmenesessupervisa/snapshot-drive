from backend.central import models as m
from backend.central.alerts import store as st


def _payload(eid, *, total_bytes=1000, missing=None):
    return {
        "event_id": eid,
        "ts": "2026-04-27T17:00:00Z",
        "client": {"proyecto": "alpha", "entorno": "cloud", "pais": "co"},
        "target": {"category": "os", "subkey": "linux", "label": "host01"},
        "operation": {"op": "archive", "status": "ok",
                      "started_at": "2026-04-27T17:00:00Z",
                      "duration_s": 1, "error": None},
        "snapshot": {"size_bytes": total_bytes, "remote_path": "x",
                     "encrypted": False},
        "totals": {"size_bytes": total_bytes, "count_files": 1,
                   "oldest_ts": None, "newest_ts": "2026-04-27T17:00:00Z"},
        "host_meta": {"hostname": "h", "snapctl_version": "0",
                      "rclone_version": "0",
                      "missing_paths": missing or []},
    }


def test_heartbeat_with_missing_paths_fires_alert(conn):
    cid = m.create_client(conn, proyecto="alpha")
    m.apply_heartbeat(conn, _payload("e1", missing=["/etc/x"]),
                      token_id=1, client_id=cid, src_ip="1.1.1.1")
    types = [r["type"] for r in st.list_active(conn)]
    assert "folder_missing" in types


def test_heartbeat_clean_resolves_prior_folder_missing(conn):
    cid = m.create_client(conn, proyecto="alpha")
    m.apply_heartbeat(conn, _payload("e1", missing=["/etc/x"]),
                      token_id=1, client_id=cid, src_ip="1.1.1.1")
    m.apply_heartbeat(conn, _payload("e2", missing=[]),
                      token_id=1, client_id=cid, src_ip="1.1.1.1")
    rows = [r for r in st.list_active(conn) if r["type"] == "folder_missing"]
    assert rows == []


def test_shrink_alert_fires_after_two_heartbeats(conn):
    cid = m.create_client(conn, proyecto="alpha")
    m.apply_heartbeat(conn, _payload("e1", total_bytes=1_000_000),
                      token_id=1, client_id=cid, src_ip="1.1.1.1")
    m.apply_heartbeat(conn, _payload("e2", total_bytes=400_000),
                      token_id=1, client_id=cid, src_ip="1.1.1.1")
    rows = [r for r in st.list_active(conn) if r["type"] == "backup_shrink"]
    assert len(rows) == 1
    assert rows[0]["detail"]["pct"] == 60.0
