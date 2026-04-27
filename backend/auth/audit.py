"""Audit log writer for auth events.

Writes to the audit_auth SQLite table and (when running inside the Flask
app context with logging configured) emits a JSON line on the auth log.
"""
import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Optional


_log = logging.getLogger("auth.audit")


def write_event(
    conn: sqlite3.Connection,
    *,
    actor: str,
    event: str,
    user_id: Optional[int] = None,
    email: Optional[str] = None,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
    detail: Optional[dict] = None,
) -> None:
    if actor not in ("web", "cli", "system"):
        raise ValueError(f"invalid actor: {actor}")
    now = datetime.now(timezone.utc).isoformat()
    detail_json = json.dumps(detail) if detail is not None else None
    conn.execute(
        "INSERT INTO audit_auth"
        "(actor,user_id,email,event,ip,user_agent,detail,created_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (actor, user_id, email, event, ip, user_agent, detail_json, now),
    )
    conn.commit()
    _log.info(json.dumps({
        "ts": now, "actor": actor, "event": event,
        "user_id": user_id, "email": email, "ip": ip,
    }))
