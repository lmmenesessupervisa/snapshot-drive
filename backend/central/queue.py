"""Cola local de heartbeats pendientes de envío al central."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

MAX_ATTEMPTS = 20
BACKOFF_LADDER = [60, 300, 900, 3600, 21600, 86400]   # 1m,5m,15m,1h,6h,24h


def backoff_seconds(attempts: int) -> int:
    """Devuelve segundos de espera para el intento N (1-indexed)."""
    if attempts < 1:
        return 0
    idx = min(attempts - 1, len(BACKOFF_LADDER) - 1)
    return BACKOFF_LADDER[idx]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def enqueue(conn: sqlite3.Connection, payload: dict) -> None:
    """Inserta el payload. Si el event_id ya existía, no hace nada (idempotente)."""
    try:
        conn.execute(
            "INSERT INTO central_queue (event_id, payload_json, enqueued_at, "
            " next_retry_ts, attempts, state) VALUES (?,?,?,?,0,'pending')",
            (payload["event_id"], json.dumps(payload, separators=(",", ":")),
             _now_iso(), _now_iso()),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        pass


def fetch_due(conn: sqlite3.Connection, *, limit: int = 100) -> list[dict]:
    rows = conn.execute(
        "SELECT id, event_id, payload_json, attempts FROM central_queue "
        "WHERE state='pending' AND next_retry_ts <= ? "
        "ORDER BY next_retry_ts LIMIT ?",
        (_now_iso(), limit),
    ).fetchall()
    return [{"id": r[0], "event_id": r[1],
             "payload": json.loads(r[2]), "attempts": r[3]}
            for r in rows]


def mark_done(conn: sqlite3.Connection, event_id: str) -> None:
    conn.execute("DELETE FROM central_queue WHERE event_id=?", (event_id,))
    conn.commit()


def mark_failed(conn: sqlite3.Connection, event_id: str, *, error: str) -> None:
    """Increment attempts, reschedule. Si attempts == MAX → dead."""
    row = conn.execute(
        "SELECT attempts FROM central_queue WHERE event_id=?", (event_id,)
    ).fetchone()
    if not row:
        return
    new_attempts = row[0] + 1
    if new_attempts >= MAX_ATTEMPTS:
        conn.execute(
            "UPDATE central_queue SET attempts=?, last_error=?, state='dead' "
            "WHERE event_id=?", (new_attempts, error[:500], event_id),
        )
    else:
        next_ts = (datetime.now(timezone.utc)
                   + timedelta(seconds=backoff_seconds(new_attempts))).isoformat()
        conn.execute(
            "UPDATE central_queue SET attempts=?, last_error=?, "
            " next_retry_ts=?, state='pending' WHERE event_id=?",
            (new_attempts, error[:500], next_ts, event_id),
        )
    conn.commit()


def mark_dead(conn: sqlite3.Connection, event_id: str, *, error: str) -> None:
    """Marca dead inmediatamente (para 4xx no recuperables)."""
    conn.execute(
        "UPDATE central_queue SET state='dead', last_error=? WHERE event_id=?",
        (error[:500], event_id),
    )
    conn.commit()


def stats(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        "SELECT state, COUNT(*) FROM central_queue GROUP BY state"
    ).fetchall()
    return {r[0]: r[1] for r in rows}
