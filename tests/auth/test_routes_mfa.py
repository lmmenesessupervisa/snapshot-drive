import pytest
import pyotp
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


def _admin_login(app):
    db = app.config["DB_CONN"]
    create_user(db, email="a@x.com", display_name="A",
                password_hash=hash_password("AdminPass-456-Y"),
                role="admin")
    return app.test_client(), db


def test_enroll_start_returns_secret_and_uri(app):
    c, db = _admin_login(app)
    # Step 1: login → require enroll
    r = c.post("/auth/login", json={
        "email": "a@x.com", "password": "AdminPass-456-Y",
    })
    assert r.json["require_mfa_enroll"] is True

    # Step 2: start enroll (no auth needed yet — we use enroll_token)
    r = c.post("/auth/mfa/enroll/start",
               json={"email": "a@x.com", "password": "AdminPass-456-Y"})
    assert r.status_code == 200
    assert "secret" in r.json
    assert "otpauth_uri" in r.json


def test_enroll_confirm_creates_session(app):
    c, db = _admin_login(app)
    r = c.post("/auth/mfa/enroll/start",
               json={"email": "a@x.com", "password": "AdminPass-456-Y"})
    secret = r.json["secret"]
    code = pyotp.TOTP(secret).now()
    r2 = c.post("/auth/mfa/enroll/confirm", json={
        "email": "a@x.com", "password": "AdminPass-456-Y",
        "secret": secret, "code": code,
    })
    assert r2.status_code == 200
    assert len(r2.json["backup_codes"]) == 10
    cookies = r2.headers.getlist("Set-Cookie")
    assert any("snapshot_session=" in cc for cc in cookies)


def test_enroll_confirm_wrong_code(app):
    c, db = _admin_login(app)
    r = c.post("/auth/mfa/enroll/start",
               json={"email": "a@x.com", "password": "AdminPass-456-Y"})
    r2 = c.post("/auth/mfa/enroll/confirm", json={
        "email": "a@x.com", "password": "AdminPass-456-Y",
        "secret": r.json["secret"], "code": "000000",
    })
    assert r2.status_code == 400


def test_enroll_start_lockout_after_5_fails(app):
    c, db = _admin_login(app)
    for _ in range(5):
        c.post("/auth/mfa/enroll/start", json={
            "email": "a@x.com", "password": "wrong-bad-password",
        })
    # Now even with the correct password, locked
    r = c.post("/auth/mfa/enroll/start", json={
        "email": "a@x.com", "password": "AdminPass-456-Y",
    })
    assert r.status_code == 401


def test_login_after_enroll_requires_mfa(app):
    c, db = _admin_login(app)
    r = c.post("/auth/mfa/enroll/start",
               json={"email": "a@x.com", "password": "AdminPass-456-Y"})
    secret = r.json["secret"]
    code = pyotp.TOTP(secret).now()
    c.post("/auth/mfa/enroll/confirm", json={
        "email": "a@x.com", "password": "AdminPass-456-Y",
        "secret": secret, "code": code,
    })
    # Subsequent login must include mfa_code
    r3 = c.post("/auth/login", json={
        "email": "a@x.com", "password": "AdminPass-456-Y",
    })
    assert r3.json["require_mfa"] is True
    code2 = pyotp.TOTP(secret).now()
    r4 = c.post("/auth/login", json={
        "email": "a@x.com", "password": "AdminPass-456-Y",
        "mfa_code": code2,
    })
    assert r4.status_code == 200
    assert r4.json["ok"] is True
