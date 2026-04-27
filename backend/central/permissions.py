"""Matriz central.* → roles que la conceden. Aliases para UI:
admin = webmaster, operator = técnico, auditor = gerente."""
from __future__ import annotations

from functools import wraps
from typing import Callable

from flask import g, jsonify

# perm → set de roles que la tienen
PERMISSIONS: dict[str, set[str]] = {
    "central.dashboard:view":   {"admin", "operator", "auditor"},
    "central.audit:view":       {"admin", "operator", "auditor"},
    "central.clients:read":     {"admin", "operator", "auditor"},
    "central.clients:write":    {"admin", "operator"},
    "central.tokens:issue":     {"admin", "operator"},
    "central.tokens:revoke":    {"admin", "operator"},
    "central.alerts:configure": {"admin", "operator"},
    "central.users:manage":     {"admin"},
    "central.settings:edit":    {"admin"},
}

ROLE_ALIASES_ES = {"admin": "webmaster", "operator": "técnico", "auditor": "gerente"}


def role_has(role: str, perm: str) -> bool:
    return role in PERMISSIONS.get(perm, set())


def require_central_perm(perm: str) -> Callable:
    """Decorator equivalente a require_role pero contra la matriz."""
    def deco(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = getattr(g, "current_user", None)
            if not user:
                return jsonify(ok=False, error="unauthenticated"), 401
            if not role_has(user.role, perm):
                return jsonify(ok=False, error="forbidden",
                               required_perm=perm), 403
            return view(*args, **kwargs)
        return wrapped
    return deco
