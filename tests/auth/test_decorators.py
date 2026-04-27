import pytest
from flask import Flask, g, jsonify

from backend.auth.decorators import (
    require_login, require_role, require_any_role,
)


class _FakeUser:
    def __init__(self, role):
        self.role = role


def _app():
    app = Flask(__name__)

    @app.route("/anon")
    @require_login
    def anon_view():
        return jsonify(ok=True)

    @app.route("/admin")
    @require_role("admin")
    def admin_view():
        return jsonify(ok=True)

    @app.route("/either")
    @require_any_role("admin", "operator")
    def either_view():
        return jsonify(ok=True)

    return app


def _set_user(app, role):
    @app.before_request
    def _inject():
        if role:
            g.current_user = _FakeUser(role)

    return app


def test_require_login_no_user():
    app = _app()
    c = app.test_client()
    r = c.get("/anon")
    assert r.status_code == 401


def test_require_login_with_user():
    app = _set_user(_app(), "auditor")
    r = app.test_client().get("/anon")
    assert r.status_code == 200


def test_require_role_match():
    app = _set_user(_app(), "admin")
    r = app.test_client().get("/admin")
    assert r.status_code == 200


def test_require_role_mismatch():
    app = _set_user(_app(), "operator")
    r = app.test_client().get("/admin")
    assert r.status_code == 403


def test_require_any_role_match_first():
    app = _set_user(_app(), "admin")
    r = app.test_client().get("/either")
    assert r.status_code == 200


def test_require_any_role_match_second():
    app = _set_user(_app(), "operator")
    r = app.test_client().get("/either")
    assert r.status_code == 200


def test_require_any_role_no_match():
    app = _set_user(_app(), "auditor")
    r = app.test_client().get("/either")
    assert r.status_code == 403
