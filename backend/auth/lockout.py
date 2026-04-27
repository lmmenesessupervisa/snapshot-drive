"""Failed-login attempt counter with exponential backoff lockout."""
import sqlite3
from datetime import datetime, timedelta, timezone


MAX_ATTEMPTS = 5
BASE_LOCK_MINUTES = 15
MAX_LOCK_HOURS = 24


def _now() -> datetime:
    return datetime.now(timezone.utc)


def is_locked(conn: sqlite3.Connection, user_id: int) -> bool:
    row = conn.execute(
        "SELECT locked_until FROM users WHERE id=?", (user_id,)
    ).fetchone()
    if not row or not row[0]:
        return False
    return datetime.fromisoformat(row[0]) > _now()


def record_failure(conn: sqlite3.Connection, user_id: int) -> None:
    row = conn.execute(
        "SELECT failed_attempts, lock_count FROM users WHERE id=?",
        (user_id,),
    ).fetchone()
    if not row:
        return
    fa, lc = row
    fa += 1
    if fa >= MAX_ATTEMPTS:
        lc += 1
        # 15min * 2^(lock_count-1), capped at 24h
        minutes = BASE_LOCK_MINUTES * (2 ** (lc - 1))
        minutes = min(minutes, MAX_LOCK_HOURS * 60)
        until = (_now() + timedelta(minutes=minutes)).isoformat()
        conn.execute(
            "UPDATE users SET failed_attempts=?, lock_count=?, "
            "locked_until=?, updated_at=? WHERE id=?",
            (0, lc, until, _now().isoformat(), user_id),
        )
    else:
        conn.execute(
            "UPDATE users SET failed_attempts=?, updated_at=? WHERE id=?",
            (fa, _now().isoformat(), user_id),
        )
    conn.commit()


def record_success(conn: sqlite3.Connection, user_id: int) -> None:
    conn.execute(
        "UPDATE users SET failed_attempts=0, lock_count=0, locked_until=NULL,"
        " last_login_at=?, updated_at=? WHERE id=?",
        (_now().isoformat(), _now().isoformat(), user_id),
    )
    conn.commit()
