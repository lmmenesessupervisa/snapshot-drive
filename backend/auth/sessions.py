"""Server-side session storage in SQLite.

Sessions identified by 256-bit random hex id stored in an HttpOnly,
Secure, SameSite=Strict cookie. CSRF token bound to each session.
Sliding refresh extends ttl when within 2h of expiry; idle timeout
revokes after 1h without activity.
"""
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional


SESSION_TTL_HOURS = 8
IDLE_TIMEOUT_MINUTES = 480
SLIDING_THRESHOLD_HOURS = 2


@dataclass
class Session:
    id: str
    user_id: int
    created_at: str
    last_seen_at: str
    expires_at: str
    ip: Optional[str]
    user_agent: Optional[str]
    csrf_token: str
    mfa_verified: bool


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_session(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
    mfa_verified: bool = False,
) -> Session:
    sid = secrets.token_hex(32)
    csrf = secrets.token_hex(32)
    now = _now()
    expires = now + timedelta(hours=SESSION_TTL_HOURS)
    conn.execute(
        "INSERT INTO sessions(id,user_id,created_at,last_seen_at,"
        "expires_at,ip,user_agent,csrf_token,mfa_verified)"
        " VALUES(?,?,?,?,?,?,?,?,?)",
        (sid, user_id, now.isoformat(), now.isoformat(),
         expires.isoformat(), ip, user_agent, csrf, 1 if mfa_verified else 0),
    )
    conn.commit()
    return Session(sid, user_id, now.isoformat(), now.isoformat(),
                   expires.isoformat(), ip, user_agent, csrf, mfa_verified)


def load_session(conn: sqlite3.Connection, sid: str) -> Optional[Session]:
    row = conn.execute(
        "SELECT id,user_id,created_at,last_seen_at,expires_at,ip,"
        "user_agent,csrf_token,mfa_verified FROM sessions WHERE id=?",
        (sid,),
    ).fetchone()
    if not row:
        return None
    s = Session(row[0], row[1], row[2], row[3], row[4], row[5],
                row[6], row[7], bool(row[8]))
    now = _now()
    if datetime.fromisoformat(s.expires_at) <= now:
        revoke_session(conn, sid)
        return None
    last = datetime.fromisoformat(s.last_seen_at)
    if (now - last) > timedelta(minutes=IDLE_TIMEOUT_MINUTES):
        revoke_session(conn, sid)
        return None
    return s


def refresh_session(conn: sqlite3.Connection, sid: str) -> None:
    """Update last_seen_at; extend expires_at if within sliding threshold."""
    now = _now()
    row = conn.execute(
        "SELECT expires_at FROM sessions WHERE id=?", (sid,)
    ).fetchone()
    if not row:
        return
    expires = datetime.fromisoformat(row[0])
    if (expires - now) < timedelta(hours=SLIDING_THRESHOLD_HOURS):
        new_exp = (now + timedelta(hours=SESSION_TTL_HOURS)).isoformat()
        conn.execute(
            "UPDATE sessions SET last_seen_at=?, expires_at=? WHERE id=?",
            (now.isoformat(), new_exp, sid),
        )
    else:
        conn.execute(
            "UPDATE sessions SET last_seen_at=? WHERE id=?",
            (now.isoformat(), sid),
        )
    conn.commit()


def revoke_session(conn: sqlite3.Connection, sid: str) -> None:
    conn.execute("DELETE FROM sessions WHERE id=?", (sid,))
    conn.commit()


def revoke_user_sessions(conn: sqlite3.Connection, user_id: int) -> None:
    conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
    conn.commit()


def mark_mfa_verified(conn: sqlite3.Connection, sid: str) -> None:
    conn.execute("UPDATE sessions SET mfa_verified=1 WHERE id=?", (sid,))
    conn.commit()


def cleanup_expired(conn: sqlite3.Connection) -> int:
    """Remove expired sessions. Call periodically (cron / startup)."""
    cur = conn.execute(
        "DELETE FROM sessions WHERE expires_at <= ?", (_now().isoformat(),)
    )
    conn.commit()
    return cur.rowcount
