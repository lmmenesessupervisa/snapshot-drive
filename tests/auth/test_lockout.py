import pytest
import sqlite3
from datetime import datetime, timedelta, timezone

from backend.auth.migrations import apply_migrations
from backend.auth.users import create_user, get_user_by_id
from backend.auth.lockout import (
    record_failure, record_success, is_locked, MAX_ATTEMPTS,
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


def test_under_threshold_no_lock(conn, uid):
    for _ in range(MAX_ATTEMPTS - 1):
        record_failure(conn, uid)
    assert is_locked(conn, uid) is False


def test_lock_on_threshold(conn, uid):
    for _ in range(MAX_ATTEMPTS):
        record_failure(conn, uid)
    assert is_locked(conn, uid) is True
    u = get_user_by_id(conn, uid)
    assert u.locked_until is not None


def test_record_success_resets(conn, uid):
    for _ in range(MAX_ATTEMPTS - 1):
        record_failure(conn, uid)
    record_success(conn, uid)
    u = get_user_by_id(conn, uid)
    assert u.failed_attempts == 0
    assert u.locked_until is None


def test_backoff_grows(conn, uid):
    # First lockout
    for _ in range(MAX_ATTEMPTS):
        record_failure(conn, uid)
    u1 = get_user_by_id(conn, uid)
    locked_until_1 = datetime.fromisoformat(u1.locked_until)
    # Manually unlock and lock again
    conn.execute("UPDATE users SET failed_attempts=0, locked_until=NULL WHERE id=?", (uid,))
    conn.commit()
    for _ in range(MAX_ATTEMPTS):
        record_failure(conn, uid)
    u2 = get_user_by_id(conn, uid)
    locked_until_2 = datetime.fromisoformat(u2.locked_until)
    # Second lockout window must be longer
    delta1 = (locked_until_1 - datetime.now(timezone.utc)).total_seconds()
    delta2 = (locked_until_2 - datetime.now(timezone.utc)).total_seconds()
    assert delta2 > delta1
