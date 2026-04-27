import pytest
from datetime import datetime, timedelta, timezone
from backend.central import queue as q


def _payload(eid="aaaaaaaa-1111-2222-3333-444444444444"):
    return {"event_id": eid, "ts": "2026-01-01T00:00:00Z"}


def test_enqueue_creates_pending_row(conn):
    p = _payload()
    q.enqueue(conn, p)
    rows = conn.execute(
        "SELECT event_id, state, attempts FROM central_queue"
    ).fetchall()
    assert [tuple(r) for r in rows] == [(p["event_id"], "pending", 0)]


def test_enqueue_idempotent_by_event_id(conn):
    p = _payload()
    q.enqueue(conn, p)
    q.enqueue(conn, p)
    n = conn.execute("SELECT COUNT(*) FROM central_queue").fetchone()[0]
    assert n == 1


def test_fetch_due_returns_only_pending_past_deadline(conn):
    q.enqueue(conn, _payload(eid="11111111-1111-1111-1111-111111111111"))
    q.enqueue(conn, _payload(eid="22222222-2222-2222-2222-222222222222"))
    conn.execute(
        "UPDATE central_queue SET next_retry_ts=? WHERE event_id=?",
        ((datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
         "22222222-2222-2222-2222-222222222222"),
    )
    conn.commit()
    due = q.fetch_due(conn, limit=10)
    assert len(due) == 1
    assert due[0]["event_id"] == "11111111-1111-1111-1111-111111111111"


def test_mark_done_deletes_row(conn):
    q.enqueue(conn, _payload())
    q.mark_done(conn, _payload()["event_id"])
    n = conn.execute("SELECT COUNT(*) FROM central_queue").fetchone()[0]
    assert n == 0


def test_mark_failed_increments_attempts_and_schedules_retry(conn):
    p = _payload()
    q.enqueue(conn, p)
    q.mark_failed(conn, p["event_id"], error="500 Internal")
    row = conn.execute(
        "SELECT attempts, last_error, state, next_retry_ts FROM central_queue"
    ).fetchone()
    assert row[0] == 1
    assert row[1] == "500 Internal"
    assert row[2] == "pending"
    assert row[3] > p["ts"]


def test_backoff_dead_after_threshold(conn):
    p = _payload()
    q.enqueue(conn, p)
    for _ in range(20):
        q.mark_failed(conn, p["event_id"], error="boom")
    row = conn.execute("SELECT state FROM central_queue").fetchone()
    assert row[0] == "dead"


def test_mark_dead_explicit(conn):
    p = _payload()
    q.enqueue(conn, p)
    q.mark_dead(conn, p["event_id"], error="401 unauthorized")
    row = conn.execute("SELECT state, last_error FROM central_queue").fetchone()
    assert row[0] == "dead" and row[1] == "401 unauthorized"


def test_backoff_schedule_progression():
    seq = [q.backoff_seconds(n) for n in range(1, 7)]
    assert seq == [60, 300, 900, 3600, 21600, 86400]
    assert q.backoff_seconds(7) == 86400
    assert q.backoff_seconds(20) == 86400
