import pytest
import sqlite3


def test_central_alerts_table_exists(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='central_alerts'"
    ).fetchall()
    assert len(rows) == 1


def test_central_alerts_indexes_exist(conn):
    rows = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND tbl_name='central_alerts'"
    ).fetchall()}
    assert "idx_alerts_active_lookup" in rows
    assert "idx_alerts_triggered" in rows


def test_alert_check_constraints_reject_bad_type(conn):
    cid = conn.execute(
        "INSERT INTO clients(proyecto, created_at, updated_at) "
        "VALUES('p','2026-01-01','2026-01-01')"
    ).lastrowid
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO central_alerts(type, client_id, severity, "
            "triggered_at, last_seen_at) VALUES (?,?,?,?,?)",
            ("bogus", cid, "warning", "2026-01-01", "2026-01-01"),
        )


def test_alert_check_constraints_reject_bad_severity(conn):
    cid = conn.execute(
        "INSERT INTO clients(proyecto, created_at, updated_at) "
        "VALUES('p2','2026-01-01','2026-01-01')"
    ).lastrowid
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO central_alerts(type, client_id, severity, "
            "triggered_at, last_seen_at) VALUES (?,?,?,?,?)",
            ("folder_missing", cid, "panic", "2026-01-01", "2026-01-01"),
        )
