from datetime import datetime, timedelta, timezone
from backend.central.alerts.sweep import sweep_inactive
from backend.central.alerts import store as st


def _seed_target(conn, hours_ago: int, label: str = "host01"):
    cur = conn.execute(
        "INSERT INTO clients(proyecto, created_at, updated_at) "
        "VALUES(?,?,?)",
        (f"p-{label}-{hours_ago}", "2026-04-01", "2026-04-01"),
    )
    cid = cur.lastrowid
    last_ts = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    cur2 = conn.execute(
        "INSERT INTO targets(client_id, category, subkey, label, "
        "last_heartbeat_ts) VALUES(?,?,?,?,?)",
        (cid, "os", "linux", label, last_ts),
    )
    return cid, cur2.lastrowid


def test_sweep_fires_when_stale(conn):
    _seed_target(conn, hours_ago=72)
    n = sweep_inactive(conn, threshold_hours=48)
    assert n == 1
    assert len(st.list_active(conn)) == 1


def test_sweep_skips_fresh(conn):
    _seed_target(conn, hours_ago=5)
    n = sweep_inactive(conn, threshold_hours=48)
    assert n == 0


def test_sweep_idempotent_on_already_active(conn):
    _seed_target(conn, hours_ago=72)
    sweep_inactive(conn, threshold_hours=48)
    sweep_inactive(conn, threshold_hours=48)
    rows = conn.execute(
        "SELECT COUNT(*) FROM central_alerts WHERE resolved_at IS NULL"
    ).fetchone()
    assert rows[0] == 1


def test_sweep_severity_critical_after_7_days(conn):
    _seed_target(conn, hours_ago=24*8)
    sweep_inactive(conn, threshold_hours=48)
    rows = st.list_active(conn)
    assert rows[0]["severity"] == "critical"


def test_sweep_severity_warning_below_7_days(conn):
    _seed_target(conn, hours_ago=72)
    sweep_inactive(conn, threshold_hours=48)
    rows = st.list_active(conn)
    assert rows[0]["severity"] == "warning"
