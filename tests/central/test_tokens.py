import pytest
from backend.central import tokens as tok


@pytest.fixture
def client_id(conn):
    cur = conn.execute("INSERT INTO clients (proyecto) VALUES ('demo')")
    conn.commit()
    return cur.lastrowid


def test_issue_returns_plaintext_token(conn, client_id):
    plaintext, token_id = tok.issue(conn, client_id, label="web01")
    assert isinstance(plaintext, str) and len(plaintext) >= 32
    assert isinstance(token_id, int) and token_id > 0


def test_issued_token_is_argon2_hashed_in_db(conn, client_id):
    plaintext, token_id = tok.issue(conn, client_id, label="web01")
    row = conn.execute(
        "SELECT token_hash FROM central_tokens WHERE id=?", (token_id,)
    ).fetchone()
    assert row[0].startswith("$argon2"), "token must be argon2-hashed"
    assert plaintext not in row[0], "plaintext must not appear in db"


def test_verify_accepts_correct_token(conn, client_id):
    plaintext, _ = tok.issue(conn, client_id, label="web01")
    result = tok.verify(conn, plaintext)
    assert result is not None
    assert result.client_id == client_id
    assert result.scope == "heartbeat:write"


def test_verify_rejects_wrong_token(conn, client_id):
    tok.issue(conn, client_id, label="web01")
    assert tok.verify(conn, "wrongtoken123") is None


def test_verify_rejects_revoked_token(conn, client_id):
    plaintext, token_id = tok.issue(conn, client_id, label="web01")
    tok.revoke(conn, token_id)
    assert tok.verify(conn, plaintext) is None


def test_verify_rejects_expired_token(conn, client_id):
    plaintext, token_id = tok.issue(conn, client_id, label="web01",
                                    expires_at="2020-01-01T00:00:00Z")
    assert tok.verify(conn, plaintext) is None


def test_verify_updates_last_used_at(conn, client_id):
    plaintext, token_id = tok.issue(conn, client_id, label="web01")
    tok.verify(conn, plaintext)
    row = conn.execute(
        "SELECT last_used_at FROM central_tokens WHERE id=?", (token_id,)
    ).fetchone()
    assert row[0] is not None


def test_revoke_sets_revoked_at(conn, client_id):
    _, token_id = tok.issue(conn, client_id, label="x")
    tok.revoke(conn, token_id)
    row = conn.execute(
        "SELECT revoked_at FROM central_tokens WHERE id=?", (token_id,)
    ).fetchone()
    assert row[0] is not None
