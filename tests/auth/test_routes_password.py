import pytest
from flask import Flask
import sqlite3

from backend.auth.migrations import apply_migrations
from backend.auth.users import create_user
from backend.auth.passwords import hash_password
from backend.auth.sessions import create_session
from backend.auth import auth_bp
from backend.auth.middleware import install_auth_middleware


@pytest.fixture
def app_with_user(tmp_path):
    app = Flask(__name__)
    app.config["SECRET_KEY_BYTES"] = bytes.fromhex("0" * 64)
    conn = sqlite3.connect(str(tmp_path / "t.db"), check_same_thread=False)
    apply_migrations(conn)
    app.config["DB_CONN"] = conn
    install_auth_middleware(app)
    app.register_blueprint(auth_bp, url_prefix="/auth")
    u = create_user(conn, email="op@x.com", display_name="Op",
                    password_hash=hash_password("Original-Pass-1234-A"),
                    role="operator")
    s = create_session(conn, user_id=u.id, mfa_verified=True)
    return app, u, s


def test_change_password_happy_path(app_with_user):
    app, u, s = app_with_user
    c = app.test_client()
    c.set_cookie(key="snapshot_session", value=s.id, domain="localhost")
    r = c.post("/auth/password",
               headers={"X-CSRF-Token": s.csrf_token},
               json={"current": "Original-Pass-1234-A",
                     "new": "Brand-New-Pass-5678-B"})
    assert r.status_code == 200, r.json


def test_change_wrong_current(app_with_user):
    app, u, s = app_with_user
    c = app.test_client()
    c.set_cookie(key="snapshot_session", value=s.id, domain="localhost")
    r = c.post("/auth/password",
               headers={"X-CSRF-Token": s.csrf_token},
               json={"current": "wrong", "new": "Brand-New-Pass-5678-B"})
    assert r.status_code == 400


def test_change_violates_policy(app_with_user):
    app, u, s = app_with_user
    c = app.test_client()
    c.set_cookie(key="snapshot_session", value=s.id, domain="localhost")
    r = c.post("/auth/password",
               headers={"X-CSRF-Token": s.csrf_token},
               json={"current": "Original-Pass-1234-A", "new": "short"})
    assert r.status_code == 400


def test_change_reuse_rejected(app_with_user):
    app, u, s = app_with_user
    c = app.test_client()
    c.set_cookie(key="snapshot_session", value=s.id, domain="localhost")
    r = c.post("/auth/password",
               headers={"X-CSRF-Token": s.csrf_token},
               json={"current": "Original-Pass-1234-A",
                     "new": "Original-Pass-1234-A"})
    # Same as current → policy may reject as "no change" or history match
    assert r.status_code == 400
