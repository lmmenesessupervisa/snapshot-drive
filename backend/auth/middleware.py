"""Flask before_request middleware for session + CSRF.

Reads the session cookie, loads the user into g.current_user, refreshes
the session, and enforces a per-session CSRF token on state-changing
requests.
"""
from flask import Flask, g, request, jsonify

from . import sessions as sess
from . import users as users_mod


COOKIE_NAME = "snapshot_session"
CSRF_HEADER = "X-CSRF-Token"
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Endpoints that don't require CSRF (no session yet, or login flow).
CSRF_EXEMPT_ENDPOINTS = {
    "auth.login",
    "auth.reset_request",
    "auth.reset_consume",
    "auth.mfa_enroll_start",
    "auth.mfa_enroll_confirm",
}


def install_auth_middleware(app: Flask) -> None:
    @app.before_request
    def _load_session():
        g.current_user = None
        g.session = None
        sid = request.cookies.get(COOKIE_NAME)
        if not sid:
            return _maybe_csrf_block()
        conn = app.config.get("DB_CONN")
        if conn is None:
            return _maybe_csrf_block()
        s = sess.load_session(conn, sid)
        if s is None:
            return _maybe_csrf_block()
        u = users_mod.get_user_by_id(conn, s.user_id)
        if u is None or u.status != "active":
            sess.revoke_session(conn, sid)
            return _maybe_csrf_block()
        g.session = s
        g.current_user = u
        sess.refresh_session(conn, sid)
        return _maybe_csrf_block()

    def _maybe_csrf_block():
        if request.method not in UNSAFE_METHODS:
            return None
        if request.endpoint in CSRF_EXEMPT_ENDPOINTS:
            return None
        s = getattr(g, "session", None)
        token = request.headers.get(CSRF_HEADER, "")
        if not s or not token or token != s.csrf_token:
            return jsonify(ok=False, error="csrf"), 403
        return None
