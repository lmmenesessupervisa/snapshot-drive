"""Matriz central.* → roles que la conceden. Aliases para UI:
admin = webmaster, operator = técnico, auditor = gerente."""
from __future__ import annotations

from functools import wraps
from typing import Callable

from flask import g, jsonify, redirect, request

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


def _is_api_request() -> bool:
    """True si el caller espera JSON (XHR/API), False si es navegación HTML."""
    return request.path.startswith("/api/") or request.is_json


def require_central_perm(perm: str) -> Callable:
    """Decorator equivalente a require_role pero contra la matriz.

    Páginas HTML → redirect al login si no hay sesión, o a "/" si rol
    insuficiente. APIs/XHR → JSON 401/403 (consistente con
    `auth.decorators.require_login` y con `audit.require_role`).
    """
    def deco(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = getattr(g, "current_user", None)
            if not user:
                if _is_api_request():
                    return jsonify(ok=False, error="unauthenticated"), 401
                return redirect("/auth/login")
            if not role_has(user.role, perm):
                if _is_api_request():
                    return jsonify(ok=False, error="forbidden",
                                   required_perm=perm), 403
                return redirect("/")
            return view(*args, **kwargs)
        return wrapped
    return deco
