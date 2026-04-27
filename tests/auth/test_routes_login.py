import pytest
from flask import Flask
import sqlite3

from backend.auth.migrations import apply_migrations
from backend.auth.users import create_user
from backend.auth.passwords import hash_password
from backend.auth import auth_bp
from backend.auth.middleware import install_auth_middleware


@pytest.fixture
def app(tmp_path):
    app = Flask(__name__)
    app.config["SECRET_KEY_BYTES"] = bytes.fromhex("0" * 64)
    conn = sqlite3.connect(str(tmp_path / "t.db"), check_same_thread=False)
    apply_migrations(conn)
    app.config["DB_CONN"] = conn
    install_auth_middleware(app)
    app.register_blueprint(auth_bp, url_prefix="/auth")
    return app


def test_login_no_mfa_sets_cookie(app):
    db = app.config["DB_CONN"]
    create_user(db, email="op@x.com", display_name="Op",
                password_hash=hash_password("StrongPass-123-X"),
                role="operator")
    r = app.test_client().post(
        "/auth/login",
        json={"email": "op@x.com", "password": "StrongPass-123-X"},
    )
    assert r.status_code == 200
    assert r.json["ok"] is True
    assert r.json["role"] == "operator"
    cookies = r.headers.getlist("Set-Cookie")
    assert any("snapshot_session=" in c for c in cookies)


def test_login_wrong_password(app):
    db = app.config["DB_CONN"]
    create_user(db, email="op@x.com", display_name="Op",
                password_hash=hash_password("StrongPass-123-X"),
                role="operator")
    r = app.test_client().post(
        "/auth/login",
        json={"email": "op@x.com", "password": "wrong-pass"},
    )
    assert r.status_code == 401


def test_login_nonexistent_user_same_error(app):
    r = app.test_client().post(
        "/auth/login",
        json={"email": "nope@x.com", "password": "anything-anything"},
    )
    assert r.status_code == 401
    assert "invalid" in r.json.get("error", "").lower() or \
           "credenciales" in r.json.get("error", "").lower()


def test_login_admin_without_mfa_requires_enrollment(app):
    db = app.config["DB_CONN"]
    create_user(db, email="ad@x.com", display_name="Ad",
                password_hash=hash_password("AdminPass-456-Y"),
                role="admin")
    r = app.test_client().post(
        "/auth/login",
        json={"email": "ad@x.com", "password": "AdminPass-456-Y"},
    )
    assert r.status_code == 200
    assert r.json.get("require_mfa_enroll") is True
    cookies = r.headers.getlist("Set-Cookie")
    # No session cookie set
    assert not any("snapshot_session=" in c and "Max-Age=0" not in c
                   for c in cookies)
