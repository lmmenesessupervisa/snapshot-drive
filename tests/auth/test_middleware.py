import pytest
import sqlite3
from flask import Flask, g, jsonify

from backend.auth.middleware import install_auth_middleware
from backend.auth.migrations import apply_migrations
from backend.auth.users import create_user
from backend.auth.sessions import create_session


COOKIE_NAME = "snapshot_session"


@pytest.fixture
def db(tmp_path):
    c = sqlite3.connect(str(tmp_path / "t.db"))
    apply_migrations(c)
    return c


@pytest.fixture
def app(db):
    app = Flask(__name__)
    app.config["DB_CONN"] = db

    install_auth_middleware(app)

    @app.route("/whoami")
    def whoami():
        u = getattr(g, "current_user", None)
        return jsonify(user=u.email if u else None)

    @app.route("/post-thing", methods=["POST"])
    def post_thing():
        return jsonify(ok=True)

    return app


def _login(db):
    u = create_user(db, email="a@b.c", display_name="A",
                    password_hash="$x$", role="operator")
    s = create_session(db, user_id=u.id, mfa_verified=True)
    return u, s


def test_no_cookie_anonymous(app):
    r = app.test_client().get("/whoami")
    assert r.json == {"user": None}


def test_valid_cookie_loads_user(app, db):
    u, s = _login(db)
    c = app.test_client()
    c.set_cookie(key=COOKIE_NAME, value=s.id, domain="localhost")
    r = c.get("/whoami")
    assert r.json == {"user": "a@b.c"}


def test_invalid_cookie_treated_as_anon(app):
    c = app.test_client()
    c.set_cookie(key=COOKIE_NAME, value="not_a_real_id", domain="localhost")
    r = c.get("/whoami")
    assert r.json == {"user": None}


def test_post_without_csrf_rejected(app, db):
    u, s = _login(db)
    c = app.test_client()
    c.set_cookie(key=COOKIE_NAME, value=s.id, domain="localhost")
    r = c.post("/post-thing")
    assert r.status_code == 403


def test_post_with_correct_csrf_accepted(app, db):
    u, s = _login(db)
    c = app.test_client()
    c.set_cookie(key=COOKIE_NAME, value=s.id, domain="localhost")
    r = c.post("/post-thing", headers={"X-CSRF-Token": s.csrf_token})
    assert r.status_code == 200
