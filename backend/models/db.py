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
