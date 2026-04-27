"""Auth decorators applied to Flask view functions.

Reads `g.current_user` populated by the auth middleware. Returns 401
if no user is logged in, 403 if the role does not match.
"""
from functools import wraps
from typing import Callable

from flask import g, jsonify


def _unauth():
    return jsonify(ok=False, error="unauthenticated"), 401


def _forbidden():
    return jsonify(ok=False, error="forbidden"), 403


def require_login(view: Callable) -> Callable:
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not getattr(g, "current_user", None):
            return _unauth()
        return view(*args, **kwargs)
    return wrapped


def require_role(role: str) -> Callable:
    def deco(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = getattr(g, "current_user", None)
            if not user:
                return _unauth()
            if user.role != role:
                return _forbidden()
            return view(*args, **kwargs)
        return wrapped
    return deco


def require_any_role(*roles: str) -> Callable:
    def deco(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = getattr(g, "current_user", None)
            if not user:
                return _unauth()
            if user.role not in roles:
                return _forbidden()
            return view(*args, **kwargs)
        return wrapped
    return deco
