import pytest
from backend.central import models as m
from backend.central.alerts import store as st


@pytest.fixture
def cid_tid(conn):
    cid = m.create_client(conn, proyecto="alpha")
    cur = conn.execute(
        "INSERT INTO targets(client_id, category, subkey, label, "
        "last_heartbeat_ts) VALUES(?,?,?,?,?)",
        (cid, "os", "linux", "host01", "2026-04-01T00:00:00Z"),
    )
    return cid, cur.lastrowid


def test_fire_creates_active_alert(conn, cid_tid):
    cid, tid = cid_tid
    a = st.fire(conn, type_="folder_missing", client_id=cid, target_id=tid,
                severity="warning", detail={"missing_paths": ["/etc"]})
    assert a["id"] > 0
    assert a["resolved_at"] is None
    assert a["notified_at"] is None


def test_fire_idempotent_updates_last_seen(conn, cid_tid):
    cid, tid = cid_tid
    a1 = st.fire(conn, type_="folder_missing", client_id=cid, target_id=tid,
                 severity="warning", detail={"missing_paths": ["/etc"]})
    a2 = st.fire(conn, type_="folder_missing", client_id=cid, target_id=tid,
                 severity="warning", detail={"missing_paths": ["/etc","/var"]})
    assert a1["id"] == a2["id"]
    rows = conn.execute(
        "SELECT COUNT(*) FROM central_alerts WHERE resolved_at IS NULL"
    ).fetchone()
    assert rows[0] == 1
    assert a2["last_seen_at"] >= a1["last_seen_at"]


def test_resolve_marks_resolved_at(conn, cid_tid):
    cid, tid = cid_tid
    a = st.fire(conn, type_="folder_missing", client_id=cid, target_id=tid,
                severity="warning", detail={})
    st.resolve(conn, a["id"])
    row = conn.execute(
        "SELECT resolved_at FROM central_alerts WHERE id=?", (a["id"],)
    ).fetchone()
    assert row[0] is not None


def test_resolve_active_by_key_no_op_when_no_active(conn, cid_tid):
    cid, tid = cid_tid
    n = st.resolve_active_by_key(conn, type_="folder_missing",
                                 client_id=cid, target_id=tid)
    assert n == 0


def test_resolve_active_by_key_resolves_match(conn, cid_tid):
    cid, tid = cid_tid
    st.fire(conn, type_="folder_missing", client_id=cid, target_id=tid,
            severity="warning", detail={})
    n = st.resolve_active_by_key(conn, type_="folder_missing",
                                 client_id=cid, target_id=tid)
    assert n == 1


def test_acknowledge_resolves_with_actor(conn, cid_tid):
    cid, tid = cid_tid
    a = st.fire(conn, type_="backup_shrink", client_id=cid, target_id=tid,
                severity="critical", detail={})
    st.acknowledge(conn, a["id"], actor_email="op@x.com")
    row = conn.execute(
        "SELECT resolved_at, detail_json FROM central_alerts WHERE id=?",
        (a["id"],),
    ).fetchone()
    assert row[0] is not None
    import json
    detail = json.loads(row[1])
    assert detail.get("acknowledged_by") == "op@x.com"


def test_list_active_returns_only_unresolved(conn, cid_tid):
    cid, tid = cid_tid
    a = st.fire(conn, type_="folder_missing", client_id=cid, target_id=tid,
                severity="warning", detail={})
    st.fire(conn, type_="backup_shrink", client_id=cid, target_id=tid,
            severity="critical", detail={})
    st.resolve(conn, a["id"])
    rows = st.list_active(conn)
    assert len(rows) == 1
    assert rows[0]["type"] == "backup_shrink"


def test_count_active_critical(conn, cid_tid):
    cid, tid = cid_tid
    st.fire(conn, type_="backup_shrink", client_id=cid, target_id=tid,
            severity="critical", detail={})
    st.fire(conn, type_="folder_missing", client_id=cid, target_id=tid,
            severity="warning", detail={})
    assert st.count_active_critical(conn) == 1
