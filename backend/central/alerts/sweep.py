"""Sweep no_heartbeat: scans targets, fires alerts for stale ones."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from . import store


def sweep_inactive(conn: sqlite3.Connection, threshold_hours: int) -> int:
    """Returns count of alerts fired (or refreshed) this sweep."""
    cutoff = (datetime.now(timezone.utc)
              - timedelta(hours=threshold_hours)).isoformat()
    crit_cutoff = (datetime.now(timezone.utc)
                   - timedelta(days=7)).isoformat()
    rows = conn.execute(
        "SELECT id, client_id, last_heartbeat_ts FROM targets "
        "WHERE last_heartbeat_ts < ?",
        (cutoff,),
    ).fetchall()
    fired = 0
    for tid, cid, last_ts in rows:
        sev = "critical" if last_ts < crit_cutoff else "warning"
        try:
            last_dt = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
            hours = int((datetime.now(timezone.utc) - last_dt).total_seconds() // 3600)
        except Exception:
            hours = -1
        store.fire(
            conn, type_="no_heartbeat", client_id=cid, target_id=tid,
            severity=sev,
            detail={"hours_since": hours, "last_heartbeat_ts": last_ts},
        )
        fired += 1
    return fired
