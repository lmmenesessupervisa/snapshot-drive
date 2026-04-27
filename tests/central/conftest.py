# tests/central/conftest.py
import sqlite3
import pytest


@pytest.fixture
def conn(tmp_path):
    """sqlite3.Connection con schema completo (auth + central tables).

    Replica el setup de produccion: backend/app.py crea DB(path) que
    ejecuta SCHEMA, y luego abre auth_conn sobre el mismo archivo y
    aplica apply_migrations. Tests hacen lo mismo en orden.
    """
    path = tmp_path / "t.db"
    # 1) Inicializar schema central via DB (igual que app.py linea 70)
    from backend.models.db import DB
    DB(path)
    # 2) Abrir conn raw (igual que app.py linea 85)
    c = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
    c.row_factory = sqlite3.Row
    # 3) Aplicar migrations de auth (crea users, sessions, audit_auth, etc.)
    from backend.auth.migrations import apply_migrations
    apply_migrations(c)
    return c
