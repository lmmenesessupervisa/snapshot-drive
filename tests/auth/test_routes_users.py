import pytest
from flask import Flask
import sqlite3

from backend.auth.migrations import apply_migrations
from backend.auth.users import create_user, get_user_by_email
from backend.auth.passwords import hash_password
from backend.auth.sessions import create_session
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


def _admin_session(app, db):
    u = create_user(db, email="admin@x.com", display_name="Admin",
                    password_hash=hash_password("AdminPass-456-Y"),
                    role="admin")
    s = create_session(db, user_id=u.id, mfa_verified=True)
    return u, s


def _operator_session(app, db):
    u = create_user(db, email="op@x.com", display_name="Op",
                    password_hash=hash_password("OpPass-456-Y"),
                    role="operator")
    s = create_session(db, user_id=u.id, mfa_verified=True)
    return u, s


def _hdr(s):
    return {"X-CSRF-Token": s.csrf_token}


def _set_cookie(c, s):
    c.set_cookie(key="snapshot_session", value=s.id, domain="localhost")


def test_list_users_admin(app):
    db = app.config["DB_CONN"]
    a, s = _admin_session(app, db)
    c = app.test_client()
    _set_cookie(c, s)
    r = c.get("/auth/users")
    assert r.status_code == 200
    emails = [u["email"] for u in r.json["users"]]
    assert "admin@x.com" in emails


def test_list_users_operator_forbidden(app):
    db = app.config["DB_CONN"]
    u, s = _operator_session(app, db)
    c = app.test_client()
    _set_cookie(c, s)
    r = c.get("/auth/users")
    assert r.status_code == 403


def test_create_user_admin(app):
    db = app.config["DB_CONN"]
    a, s = _admin_session(app, db)
    c = app.test_client()
    _set_cookie(c, s)
    r = c.post("/auth/users", headers=_hdr(s), json={
        "email": "new@x.com", "display_name": "New",
        "role": "operator", "password": "Initial-Pass-9999-K",
    })
    assert r.status_code == 200
    assert get_user_by_email(db, "new@x.com") is not None


def test_create_user_operator_forbidden(app):
    db = app.config["DB_CONN"]
    u, s = _operator_session(app, db)
    c = app.test_client()
    _set_cookie(c, s)
    r = c.post("/auth/users", headers=_hdr(s), json={
        "email": "new@x.com", "display_name": "New",
        "role": "operator", "password": "Initial-Pass-9999-K",
    })
    assert r.status_code == 403


def test_admin_reset_password(app):
    db = app.config["DB_CONN"]
    a, s = _admin_session(app, db)
    target = create_user(db, email="t@x.com", display_name="T",
                         password_hash=hash_password("Old-Pass-1234-Y"),
                         role="operator")
    c = app.test_client()
    _set_cookie(c, s)
    r = c.post(f"/auth/users/{target.id}/reset-password", headers=_hdr(s))
    assert r.status_code == 200
    assert "temp_password" in r.json
    assert len(r.json["temp_password"]) >= 12


def test_admin_disable(app):
    db = app.config["DB_CONN"]
    a, s = _admin_session(app, db)
    target = create_user(db, email="t@x.com", display_name="T",
                         password_hash=hash_password("Old-Pass-1234-Y"),
                         role="operator")
    c = app.test_client()
    _set_cookie(c, s)
    r = c.post(f"/auth/users/{target.id}/disable", headers=_hdr(s))
    assert r.status_code == 200


def test_admin_revoke_sessions(app):
    db = app.config["DB_CONN"]
    a, s = _admin_session(app, db)
    target = create_user(db, email="t@x.com", display_name="T",
                         password_hash=hash_password("Old-Pass-1234-Y"),
                         role="operator")
    create_session(db, user_id=target.id)
    c = app.test_client()
    _set_cookie(c, s)
    r = c.post(f"/auth/users/{target.id}/revoke-sessions", headers=_hdr(s))
    assert r.status_code == 200
    rows = db.execute("SELECT COUNT(*) FROM sessions WHERE user_id=?",
                      (target.id,)).fetchone()
    assert rows[0] == 0
