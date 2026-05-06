"""DB CRUD for central_alerts.

Idempotency contract: fire() returns the same id if an active alert with
the same (client_id, target_id, type) already exists; it bumps last_seen_at
and merges detail_json. resolve() marks resolved_at. acknowledge() resolves
and stores the actor in detail_json under 'acknowledged_by'.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(r) -> dict:
    cols = ("id", "type", "client_id", "target_id", "severity",
            "triggered_at", "last_seen_at", "resolved_at",
            "notified_at", "detail_json")
    out = dict(zip(cols, r))
    out["detail"] = json.loads(out.pop("detail_json") or "{}")
    return out


def fire(conn: sqlite3.Connection, *, type_: str, client_id: int,
         target_id: Optional[int], severity: str, detail: dict) -> dict:
    """Idempotent: 1 active row per (client_id, target_id, type)."""
    now = _now_iso()
    detail_json = json.dumps(detail or {})
    existing = conn.execute(
        "SELECT id, detail_json FROM central_alerts WHERE type=? "
        "AND client_id=? AND ((target_id IS NULL AND ? IS NULL) OR target_id=?) "
        "AND resolved_at IS NULL",
        (type_, client_id, target_id, target_id),
    ).fetchone()
    if existing:
        merged = json.loads(existing[1] or "{}")
        merged.update(detail or {})
        conn.execute(
            "UPDATE central_alerts SET last_seen_at=?, severity=?, "
            "detail_json=? WHERE id=?",
            (now, severity, json.dumps(merged), existing[0]),
        )
        conn.commit()
        return get_by_id(conn, existing[0])
    cur = conn.execute(
        "INSERT INTO central_alerts(type, client_id, target_id, severity,"
        " triggered_at, last_seen_at, detail_json) VALUES(?,?,?,?,?,?,?)",
        (type_, client_id, target_id, severity, now, now, detail_json),
    )
    conn.commit()
    return get_by_id(conn, cur.lastrowid)


def resolve(conn: sqlite3.Connection, alert_id: int) -> None:
    conn.execute(
        "UPDATE central_alerts SET resolved_at=? "
        "WHERE id=? AND resolved_at IS NULL",
        (_now_iso(), alert_id),
    )
    conn.commit()


def resolve_active_by_key(conn: sqlite3.Connection, *, type_: str,
                          client_id: int,
                          target_id: Optional[int]) -> int:
    """Resolve any active alert matching the key. Returns rowcount."""
    cur = conn.execute(
        "UPDATE central_alerts SET resolved_at=? WHERE type=? "
        "AND client_id=? AND ((target_id IS NULL AND ? IS NULL) OR target_id=?) "
        "AND resolved_at IS NULL",
        (_now_iso(), type_, client_id, target_id, target_id),
    )
    conn.commit()
    return cur.rowcount


def acknowledge(conn: sqlite3.Connection, alert_id: int,
                actor_email: str) -> None:
    row = conn.execute(
        "SELECT detail_json FROM central_alerts WHERE id=?", (alert_id,)
    ).fetchone()
    if not row:
        return
    detail = json.loads(row[0] or "{}")
    detail["acknowledged_by"] = actor_email
    conn.execute(
        "UPDATE central_alerts SET resolved_at=?, detail_json=? WHERE id=?",
        (_now_iso(), json.dumps(detail), alert_id),
    )
    conn.commit()


_SELECT_COLS = ("id, type, client_id, target_id, severity, triggered_at, "
                "last_seen_at, resolved_at, notified_at, detail_json")


def get_by_id(conn: sqlite3.Connection, alert_id: int) -> Optional[dict]:
    r = conn.execute(
        f"SELECT {_SELECT_COLS} FROM central_alerts WHERE id=?", (alert_id,)
    ).fetchone()
    return _row_to_dict(r) if r else None


def list_active(conn: sqlite3.Connection, *, limit: int = 200) -> list[dict]:
    return _list_with_join(conn, where="WHERE a.resolved_at IS NULL", limit=limit)


def list_recent(conn: sqlite3.Connection, *, limit: int = 200) -> list[dict]:
    return _list_with_join(conn, where="", limit=limit)


def _list_with_join(conn: sqlite3.Connection, *, where: str, limit: int) -> list[dict]:
    """Lista alertas con JOIN a clients/targets para que la UI pueda
    mostrar el nombre del proyecto y el target en human-readable, en
    vez de los IDs crudos.
    """
    sql = f"""
        SELECT a.id, a.type, a.client_id, a.target_id, a.severity,
               a.triggered_at, a.last_seen_at, a.resolved_at,
               a.notified_at, a.detail_json,
               c.proyecto AS client_proyecto,
               t.category AS target_category,
               t.subkey   AS target_subkey,
               t.label    AS target_label
        FROM central_alerts a
        LEFT JOIN clients c ON c.id = a.client_id
        LEFT JOIN targets t ON t.id = a.target_id
        {where}
        ORDER BY a.triggered_at DESC
        LIMIT ?
    """
    rows = conn.execute(sql, (limit,)).fetchall()
    cols = ("id", "type", "client_id", "target_id", "severity",
            "triggered_at", "last_seen_at", "resolved_at",
            "notified_at", "detail_json",
            "client_proyecto", "target_category", "target_subkey", "target_label")
    out = []
    for r in rows:
        d = dict(zip(cols, r))
        d["detail"] = json.loads(d.pop("detail_json") or "{}")
        out.append(d)
    return out


def mark_notified(conn: sqlite3.Connection, alert_id: int) -> None:
    conn.execute(
        "UPDATE central_alerts SET notified_at=? WHERE id=?",
        (_now_iso(), alert_id),
    )
    conn.commit()


def count_active_critical(conn: sqlite3.Connection) -> int:
    r = conn.execute(
        "SELECT COUNT(*) FROM central_alerts "
        "WHERE resolved_at IS NULL AND severity='critical'"
    ).fetchone()
    return r[0] if r else 0
