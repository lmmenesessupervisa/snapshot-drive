"""Test helpers compartidos. Patrón: insertar user vía users module y
abrir sesión vía POST /auth/login. Para tests de central que NO necesitan
MFA, usamos roles operator/auditor (admin requiere MFA enrollment)."""
from backend.auth import users as users_mod
from backend.auth.passwords import hash_password


def create_user_and_login(test_client, conn, *, role: str = "operator",
                          email: str | None = None,
                          display_name: str | None = None,
                          password: str = "TestPassword-123-XYZ!"):
    """Crea un user en `users` con role solicitado y abre sesión.
    Retorna (email, password). conn debe ser sqlite3.Connection."""
    email = email or f"{role}@test.local"
    display_name = display_name or role.capitalize()
    users_mod.create_user(
        conn, email=email, display_name=display_name,
        password_hash=hash_password(password), role=role,
    )
    r = test_client.post("/auth/login",
                         json={"email": email, "password": password})
    assert r.status_code == 200, f"login failed: {r.get_data(as_text=True)}"
    data = r.get_json() or {}
    csrf = data.get("csrf_token")
    return email, password, csrf
