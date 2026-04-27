import sqlite3
from backend.auth.migrations import apply_migrations, CURRENT_VERSION


def test_fresh_db_applies_all(tmp_path):
    db_path = str(tmp_path / "t.db")
    conn = sqlite3.connect(db_path)
    apply_migrations(conn)
    cur = conn.execute("PRAGMA user_version")
    assert cur.fetchone()[0] == CURRENT_VERSION
    # Tables exist
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert {"users", "sessions", "password_history",
            "password_resets", "mfa_backup_codes", "audit_auth"} <= names


def test_idempotent(tmp_path):
    db_path = str(tmp_path / "t.db")
    conn = sqlite3.connect(db_path)
    apply_migrations(conn)
    apply_migrations(conn)  # second call no-op
    cur = conn.execute("PRAGMA user_version")
    assert cur.fetchone()[0] == CURRENT_VERSION
