import pytest
from flask import Flask
import sqlite3

from backend.auth.migrations import apply_migrations
from backend.auth.users import create_user
from backend.auth.passwords import hash_password, verify_password
from backend.auth import auth_bp
from backend.auth.middleware import install_auth_middleware


@pytest.fixture
def app(tmp_path):
    app = Flask(__name__)
    app.config["SECRET_KEY_BYTES"] = bytes.fromhex("0" * 64)
    app.config["SMTP_HOST"] = ""  # No SMTP — we just validate flow
    conn = sqlite3.connect(str(tmp_path / "t.db"), check_same_thread=False)
    apply_migrations(conn)
    app.config["DB_CONN"] = conn
    install_auth_middleware(app)
    app.register_blueprint(auth_bp, url_prefix="/auth")
    return app


def test_reset_request_unknown_user_returns_ok(app):
    r = app.test_client().post(
        "/auth/reset-request", json={"email": "missing@x.com"}
    )
    assert r.status_code == 200
    assert r.json["ok"] is True


def test_reset_request_existing_user_creates_token(app):
    db = app.config["DB_CONN"]
    u = create_user(db, email="op@x.com", display_name="Op",
                    password_hash=hash_password("Original-Pass-1234"),
                    role="operator")
    r = app.test_client().post(
        "/auth/reset-request", json={"email": "op@x.com"}
    )
    assert r.status_code == 200
    row = db.execute("SELECT user_id FROM password_resets").fetchone()
    assert row[0] == u.id


def test_reset_consume_changes_password(app):
    from backend.auth.reset_tokens import create_reset_token
    db = app.config["DB_CONN"]
    u = create_user(db, email="op@x.com", display_name="Op",
                    password_hash=hash_password("Original-Pass-1234"),
                    role="operator")
    tok = create_reset_token(db, u.id)
    r = app.test_client().post(
        "/auth/reset-consume",
        json={"token": tok, "new_password": "Reset-Pass-9999-Z"},
    )
    assert r.status_code == 200
    row = db.execute(
        "SELECT password_hash FROM users WHERE id=?", (u.id,)
    ).fetchone()
    assert verify_password("Reset-Pass-9999-Z", row[0])


def test_reset_consume_invalid_token(app):
    r = app.test_client().post(
        "/auth/reset-consume",
        json={"token": "bogus", "new_password": "Reset-Pass-9999-Z"},
    )
    assert r.status_code == 400


def test_reset_consume_revokes_sessions(app):
    from backend.auth.reset_tokens import create_reset_token
    from backend.auth.sessions import create_session, load_session
    db = app.config["DB_CONN"]
    u = create_user(db, email="op@x.com", display_name="Op",
                    password_hash=hash_password("Original-Pass-1234"),
                    role="operator")
    s = create_session(db, user_id=u.id)
    tok = create_reset_token(db, u.id)
    app.test_client().post(
        "/auth/reset-consume",
        json={"token": tok, "new_password": "Reset-Pass-9999-Z"},
    )
    assert load_session(db, s.id) is None
