import pytest
import sqlite3
import time
from datetime import datetime, timedelta, timezone

from backend.auth.migrations import apply_migrations
from backend.auth.users import create_user
from backend.auth.sessions import (
    create_session, load_session, refresh_session, revoke_session,
    revoke_user_sessions, SESSION_TTL_HOURS, IDLE_TIMEOUT_MINUTES,
)


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(str(tmp_path / "t.db"))
    apply_migrations(c)
    return c


@pytest.fixture
def uid(conn):
    u = create_user(conn, email="a@b.c", display_name="A",
                    password_hash="$x$", role="operator")
    return u.id


def test_create_returns_id_and_csrf(conn, uid):
    s = create_session(conn, user_id=uid, ip="1.1.1.1",
                       user_agent="ua", mfa_verified=True)
    assert len(s.id) == 64  # 32 bytes hex
    assert len(s.csrf_token) == 64
    assert s.user_id == uid
    assert s.mfa_verified is True


def test_load_valid(conn, uid):
    s = create_session(conn, user_id=uid)
    loaded = load_session(conn, s.id)
    assert loaded is not None
    assert loaded.user_id == uid


def test_load_missing_returns_none(conn):
    assert load_session(conn, "nonexistent" * 8) is None


def test_load_expired_returns_none(conn, uid):
    s = create_session(conn, user_id=uid)
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    conn.execute("UPDATE sessions SET expires_at=? WHERE id=?", (past, s.id))
    conn.commit()
    assert load_session(conn, s.id) is None


def test_load_idle_returns_none(conn, uid):
    s = create_session(conn, user_id=uid)
    past = (datetime.now(timezone.utc)
            - timedelta(minutes=IDLE_TIMEOUT_MINUTES + 5)).isoformat()
    conn.execute("UPDATE sessions SET last_seen_at=? WHERE id=?", (past, s.id))
    conn.commit()
    assert load_session(conn, s.id) is None


def test_refresh_extends_when_close_to_expiry(conn, uid):
    s = create_session(conn, user_id=uid)
    # Set expires_at to 30 min from now (less than 2h threshold)
    soon = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    conn.execute("UPDATE sessions SET expires_at=? WHERE id=?", (soon, s.id))
    conn.commit()
    refresh_session(conn, s.id)
    row = conn.execute(
        "SELECT expires_at FROM sessions WHERE id=?", (s.id,)
    ).fetchone()
    new_expiry = datetime.fromisoformat(row[0])
    assert new_expiry > datetime.now(timezone.utc) + timedelta(hours=SESSION_TTL_HOURS - 1)


def test_revoke(conn, uid):
    s = create_session(conn, user_id=uid)
    revoke_session(conn, s.id)
    assert load_session(conn, s.id) is None


def test_revoke_user(conn, uid):
    s1 = create_session(conn, user_id=uid)
    s2 = create_session(conn, user_id=uid)
    revoke_user_sessions(conn, uid)
    assert load_session(conn, s1.id) is None
    assert load_session(conn, s2.id) is None
