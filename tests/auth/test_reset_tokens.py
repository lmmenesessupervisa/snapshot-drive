import pytest
import sqlite3
from datetime import datetime, timedelta, timezone

from backend.auth.migrations import apply_migrations
from backend.auth.users import create_user
from backend.auth.reset_tokens import (
    create_reset_token, consume_reset_token, RESET_TTL_HOURS,
)


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(str(tmp_path / "t.db"))
    apply_migrations(c)
    return c


@pytest.fixture
def uid(conn):
    return create_user(conn, email="a@b.c", display_name="A",
                       password_hash="$x$", role="operator").id


def test_create_returns_token(conn, uid):
    tok = create_reset_token(conn, uid)
    assert isinstance(tok, str) and len(tok) >= 32


def test_consume_valid_token_returns_user(conn, uid):
    tok = create_reset_token(conn, uid)
    assert consume_reset_token(conn, tok) == uid


def test_consume_twice_returns_none(conn, uid):
    tok = create_reset_token(conn, uid)
    assert consume_reset_token(conn, tok) == uid
    assert consume_reset_token(conn, tok) is None


def test_consume_expired_returns_none(conn, uid):
    tok = create_reset_token(conn, uid)
    past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    conn.execute("UPDATE password_resets SET expires_at=?", (past,))
    conn.commit()
    assert consume_reset_token(conn, tok) is None


def test_consume_unknown_returns_none(conn):
    assert consume_reset_token(conn, "nonexistent_token_value") is None
