"""Capa SQLite: histórico de operaciones, jobs y auditoría."""
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

_LOCK = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kind         TEXT NOT NULL,          -- create|restore|prune|check|sync|delete
    status       TEXT NOT NULL,          -- pending|running|ok|error
    snapshot_id  TEXT,
    args_json    TEXT,
    stdout       TEXT,
    stderr       TEXT,
    rc           INTEGER,
    started_at   TEXT DEFAULT CURRENT_TIMESTAMP,
    finished_at  TEXT
);

CREATE TABLE IF NOT EXISTS audit (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    actor      TEXT NOT NULL,            -- cli|api
    action     TEXT NOT NULL,
    detail     TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_kind   ON jobs(kind);

-- Sub-proyecto B: despliegue dual + agregación central
-- Estas tablas se crean en ambos modos (client y central). En client mode
-- las 5 centrales quedan vacías; en central mode central_queue queda vacía.
-- Trade-off deliberado: un solo path de inicialización.

CREATE TABLE IF NOT EXISTS clients (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    proyecto        TEXT NOT NULL UNIQUE,
    organizacion    TEXT,
    contacto        TEXT,
    retencion_meses INTEGER,
    notas           TEXT,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS targets (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id         INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    category          TEXT NOT NULL CHECK (category IN ('os','db')),
    subkey            TEXT NOT NULL,
    label             TEXT NOT NULL,
    entorno           TEXT,
    pais              TEXT,
    last_exec_ts      TEXT,
    last_exec_status  TEXT,
    last_size_bytes   INTEGER,
    total_size_bytes  INTEGER,
    count_files       INTEGER,
    oldest_backup_ts  TEXT,
    newest_backup_ts  TEXT,
    last_heartbeat_ts TEXT NOT NULL,
    snapctl_version   TEXT,
    rclone_version    TEXT,
    created_at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(client_id, category, subkey, label)
);
CREATE INDEX IF NOT EXISTS idx_targets_client ON targets(client_id);
CREATE INDEX IF NOT EXISTS idx_targets_silent ON targets(last_heartbeat_ts);

CREATE TABLE IF NOT EXISTS central_tokens (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash   TEXT NOT NULL UNIQUE,
    client_id    INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    label        TEXT NOT NULL,
    scope        TEXT NOT NULL DEFAULT 'heartbeat:write',
    created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at   TEXT,
    last_used_at TEXT,
    revoked_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_tokens_client
    ON central_tokens(client_id) WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS central_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id     TEXT NOT NULL UNIQUE,
    received_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    token_id     INTEGER NOT NULL REFERENCES central_tokens(id),
    client_id    INTEGER NOT NULL REFERENCES clients(id),
    target_id    INTEGER REFERENCES targets(id),
    op           TEXT NOT NULL,
    status       TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    src_ip       TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_target_ts
    ON central_events(target_id, received_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_client_ts
    ON central_events(client_id, received_at DESC);

CREATE TABLE IF NOT EXISTS central_user_perms (
    user_id          INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    can_manage_users INTEGER NOT NULL DEFAULT 0,
    notes            TEXT
);

CREATE TABLE IF NOT EXISTS central_queue (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id      TEXT NOT NULL UNIQUE,
    payload_json  TEXT NOT NULL,
    enqueued_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    next_retry_ts TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    attempts      INTEGER NOT NULL DEFAULT 0,
    last_error    TEXT,
    state         TEXT NOT NULL DEFAULT 'pending'
);
CREATE INDEX IF NOT EXISTS idx_queue_due ON central_queue(state, next_retry_ts);
"""


class DB:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self):
        with self.connect() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def connect(self):
        with _LOCK:
            conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            try:
                yield conn
            finally:
                conn.close()

    # ---------- jobs ----------
    def job_start(self, kind: str, args_json: str = "", snapshot_id: str | None = None) -> int:
        with self.connect() as c:
            cur = c.execute(
                "INSERT INTO jobs (kind, status, snapshot_id, args_json) VALUES (?, 'running', ?, ?)",
                (kind, snapshot_id, args_json),
            )
            return cur.lastrowid

    def job_finish(self, job_id: int, rc: int, stdout: str, stderr: str):
        status = "ok" if rc == 0 else "error"
        with self.connect() as c:
            c.execute(
                "UPDATE jobs SET status=?, rc=?, stdout=?, stderr=?, finished_at=CURRENT_TIMESTAMP WHERE id=?",
                (status, rc, stdout[-32000:], stderr[-32000:], job_id),
            )

    def job_list(self, limit: int = 50):
        with self.connect() as c:
            return [dict(r) for r in c.execute(
                "SELECT id,kind,status,snapshot_id,rc,started_at,finished_at FROM jobs ORDER BY id DESC LIMIT ?",
                (limit,),
            )]

    def job_get(self, job_id: int):
        with self.connect() as c:
            r = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            return dict(r) if r else None

    # ---------- audit ----------
    def audit(self, actor: str, action: str, detail: str = ""):
        with self.connect() as c:
            c.execute(
                "INSERT INTO audit (actor, action, detail) VALUES (?,?,?)",
                (actor, action, detail),
            )
