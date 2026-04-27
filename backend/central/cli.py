"""CLI entry para el bash wrapper. Uso:
   python -m backend.central.cli send <json_file>
   python -m backend.central.cli drain
   python -m backend.central.cli status
"""
import json
import os
import sqlite3
import sys
from pathlib import Path

from backend.config import Config
from backend.models.db import DB
from . import sender, queue as q


def _resolve_db_path() -> Path:
    # SNAPSHOT_DB_PATH (env por backend/app.py + tests) tiene prioridad sobre
    # Config.DB_PATH (que lee DB_PATH directamente).
    override = os.getenv("SNAPSHOT_DB_PATH")
    return Path(override) if override else Config.DB_PATH


def _open_conn() -> sqlite3.Connection:
    """Inicializa schema (DB) y devuelve sqlite3.Connection raw."""
    path = _resolve_db_path()
    DB(path)  # ensure schema
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


def main():
    if len(sys.argv) < 2:
        print("usage: cli.py {send <file>|drain|status}", file=sys.stderr)
        sys.exit(2)
    conn = _open_conn()
    cmd = sys.argv[1]
    if cmd == "send":
        payload = json.loads(open(sys.argv[2]).read())
        code = sender.send_now(conn, payload)
        print(json.dumps({"ok": code == 200, "code": code}))
        sys.exit(0 if code == 200 else 1)
    elif cmd in ("drain", "drain-queue"):
        n = sender.drain(conn)
        print(json.dumps({"ok": True, "drained": n}))
    elif cmd == "status":
        st = q.stats(conn)
        print(json.dumps({"ok": True, "queue": st,
                          "central_url": Config.CENTRAL_URL,
                          "mode": Config.MODE}))
    elif cmd == "alerts-sweep":
        from . import alerts as _alerts
        n = _alerts.sweep.sweep_inactive(
            conn, threshold_hours=Config.ALERTS_NO_HEARTBEAT_HOURS,
        )
        print(json.dumps({"ok": True, "fired": n}))
    else:
        print(f"unknown: {cmd}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
