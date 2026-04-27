"""Schema migrations for the auth subsystem.

Versioning via SQLite's PRAGMA user_version. The runner applies all
migrations whose version is greater than the current database version,
in order. Each migration is a function that takes a sqlite3.Connection.
"""
import sqlite3
from typing import Callable, List, Tuple


def _v1_create_auth_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users (
      id              INTEGER PRIMARY KEY,
      email           TEXT NOT NULL UNIQUE COLLATE NOCASE,
      display_name    TEXT NOT NULL,
      password_hash   TEXT NOT NULL,
      role            TEXT NOT NULL CHECK(role IN ('admin','operator','auditor')),
      mfa_secret      TEXT,
      mfa_enrolled_at TEXT,
      status          TEXT NOT NULL DEFAULT 'active'
                        CHECK(status IN ('active','disabled','locked')),
      failed_attempts INTEGER NOT NULL DEFAULT 0,
      lock_count      INTEGER NOT NULL DEFAULT 0,
      locked_until    TEXT,
      created_at      TEXT NOT NULL,
      updated_at      TEXT NOT NULL,
      last_login_at   TEXT
    );

    CREATE TABLE IF NOT EXISTS sessions (
      id            TEXT PRIMARY KEY,
      user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      created_at    TEXT NOT NULL,
      last_seen_at  TEXT NOT NULL,
      expires_at    TEXT NOT NULL,
      ip            TEXT,
      user_agent    TEXT,
      csrf_token    TEXT NOT NULL,
      mfa_verified  INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS password_history (
      user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      password_hash TEXT NOT NULL,
      changed_at    TEXT NOT NULL,
      PRIMARY KEY (user_id, changed_at)
    );

    CREATE TABLE IF NOT EXISTS password_resets (
      token_hash    TEXT PRIMARY KEY,
      user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      created_at    TEXT NOT NULL,
      expires_at    TEXT NOT NULL,
      consumed_at   TEXT
    );

    CREATE TABLE IF NOT EXISTS mfa_backup_codes (
      user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      code_hash   TEXT NOT NULL,
      consumed_at TEXT,
      PRIMARY KEY (user_id, code_hash)
    );

    CREATE TABLE IF NOT EXISTS audit_auth (
      id          INTEGER PRIMARY KEY,
      actor       TEXT NOT NULL CHECK(actor IN ('web','cli','system')),
      user_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
      email       TEXT,
      event       TEXT NOT NULL,
      ip          TEXT,
      user_agent  TEXT,
      detail      TEXT,
      created_at  TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
    CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);
    CREATE INDEX IF NOT EXISTS idx_audit_auth_created ON audit_auth(created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_audit_auth_user ON audit_auth(user_id, created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_password_resets_user ON password_resets(user_id);
    """)


def _v2_jobs_actor(conn: sqlite3.Connection) -> None:
    cur = conn.execute("PRAGMA table_info(jobs)")
    cols = {r[1] for r in cur.fetchall()}
    if cols and "actor_user_id" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN actor_user_id INTEGER")


MIGRATIONS: List[Tuple[int, Callable[[sqlite3.Connection], None]]] = [
    (1, _v1_create_auth_tables),
    (2, _v2_jobs_actor),
]
CURRENT_VERSION = MIGRATIONS[-1][0]


def apply_migrations(conn: sqlite3.Connection) -> None:
    cur = conn.execute("PRAGMA user_version")
    current = cur.fetchone()[0]
    for version, fn in MIGRATIONS:
        if version > current:
            fn(conn)
            conn.execute(f"PRAGMA user_version = {version}")
            conn.commit()
