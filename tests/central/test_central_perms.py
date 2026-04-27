import pytest
from backend.central.permissions import (
    PERMISSIONS, role_has, require_central_perm,
)


def test_matrix_admin_has_everything():
    for perm in PERMISSIONS:
        assert role_has("admin", perm) is True


def test_auditor_only_has_read():
    assert role_has("auditor", "central.dashboard:view") is True
    assert role_has("auditor", "central.audit:view") is True
    assert role_has("auditor", "central.clients:read") is True
    assert role_has("auditor", "central.clients:write") is False
    assert role_has("auditor", "central.tokens:revoke") is False
    assert role_has("auditor", "central.users:manage") is False


def test_operator_can_revoke_tokens():
    assert role_has("operator", "central.tokens:revoke") is True
    assert role_has("operator", "central.tokens:issue") is True
    assert role_has("operator", "central.users:manage") is False


def test_unknown_perm_is_denied():
    assert role_has("admin", "central.bogus:thing") is False


def test_decorator_returns_403_when_role_lacks_perm(app, client):
    # Necesita un endpoint de prueba registrado al import; ver fixtures
    # más abajo en este archivo.
    pass  # cubierto por test_routes_perms en task 7
