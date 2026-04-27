# Central Alerts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add three detection rules + email/webhook notification + UI alerts page to the snapshot-V3 central deploy (depends on sub-B, already merged).

**Architecture:** New `backend/central/alerts/` module with reactive evaluation hooked into `apply_heartbeat` (folder_missing, backup_shrink) plus periodic sweep (no_heartbeat) called from healthcheck timer. State persisted in new `central_alerts` SQLite table. Notification via SMTP + webhook with notified_at idempotency.

**Tech Stack:** Flask, SQLite, smtplib (stdlib), requests (already in deps from sub-B).

**Spec:** `docs/superpowers/specs/2026-04-27-central-alerts-design.md`

---

## File Structure

### Created

| Path | Responsibility |
|------|---------------|
| `backend/central/alerts/__init__.py` | Re-export `alerts_bp`, `evaluate_heartbeat`, `sweep_inactive` |
| `backend/central/alerts/store.py` | DB CRUD: `fire`, `resolve`, `acknowledge`, `list_active`, `list_recent`, `get_by_id` |
| `backend/central/alerts/rules.py` | Pure detection: `evaluate_heartbeat(payload, target_id, prev_size, conn, *, thresholds)` returns list of `(type, severity, detail)` to fire |
| `backend/central/alerts/sweep.py` | Periodic detection: `sweep_inactive(conn, threshold_hours)` raises no_heartbeat for stale targets |
| `backend/central/alerts/dispatch.py` | Notification: `notify(alert, client, target)` — email + webhook, marks `notified_at` |
| `backend/central/alerts/routes.py` | `alerts_bp` with `/api/admin/alerts*` endpoints |
| `frontend/templates/central/alerts.html` | Page listing alerts with filter + acknowledge buttons |
| `tests/central/test_alerts_store.py` | CRUD round-trips, idempotency, resolve flow |
| `tests/central/test_alerts_rules.py` | Detection logic per rule |
| `tests/central/test_alerts_sweep.py` | Sweep finds stale, ignores fresh, no duplicates |
| `tests/central/test_alerts_dispatch.py` | Email skipped if no SMTP, webhook posts JSON |
| `tests/central/test_alerts_routes.py` | API auth + behavior |
| `tests/central/test_alerts_e2e.py` | Heartbeat with missing_paths → alert active in DB → API exposes it |

### Modified

| Path | Change |
|------|--------|
| `backend/models/db.py` | Add `central_alerts` table + 2 indexes (idempotent) |
| `backend/config.py` | `ALERTS_NO_HEARTBEAT_HOURS`, `ALERTS_SHRINK_PCT`, `ALERTS_EMAIL`, `ALERTS_WEBHOOK` + `Config.SMTP_*` already exist |
| `backend/central/models.py` | `apply_heartbeat` reads prev `total_size_bytes` BEFORE upsert, then calls `evaluate_heartbeat` AFTER upsert |
| `backend/app.py` | If `MODE=central`: register `alerts_bp` + context_processor for active critical count |
| `frontend/templates/base.html` | Banner if `central_alerts_critical > 0` |
| `core/bin/snapctl` | New sub-command `central alerts-sweep` |
| `core/lib/central.sh` | Add `MISSING_PATHS_JSON` env support (line in JSON heartbeat for `host_meta.missing_paths`) |
| `core/lib/archive.sh` | Collect missing paths into a variable, pass to `central_send` |
| `systemd/snapshot-healthcheck.service` | Additional `ExecStartPost=` for `alerts-sweep` |
| `core/etc/snapshot.local.conf.example` | Document the four `ALERTS_*` knobs |
| `README.md` | New section "Alertas (modo central)" |

---

## Phase 1: Foundation

### Task 1: Schema migration

**Files:**
- Modify: `backend/models/db.py` (add CREATE TABLE central_alerts to SCHEMA constant)
- Create: `tests/central/test_alerts_schema.py`

- [ ] **Step 1: Write failing test**

```python
# tests/central/test_alerts_schema.py
def test_central_alerts_table_exists(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='central_alerts'"
    ).fetchall()
    assert len(rows) == 1


def test_central_alerts_indexes_exist(conn):
    rows = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='central_alerts'"
    ).fetchall()}
    assert "idx_alerts_active_lookup" in rows
    assert "idx_alerts_triggered" in rows


def test_alert_check_constraints_reject_bad_type(conn):
    import pytest, sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO central_alerts(type, client_id, severity, "
            "triggered_at, last_seen_at) VALUES (?,?,?,?,?)",
            ("bogus", 1, "warning", "2026-01-01", "2026-01-01"),
        )
```

- [ ] **Step 2: Run test, verify failure**

```bash
.venv/bin/pytest tests/central/test_alerts_schema.py -v
```

Expected: 3 failures (table missing).

- [ ] **Step 3: Add SQL to `backend/models/db.py` SCHEMA constant**

Append before the closing `"""` of `SCHEMA`:

```sql

CREATE TABLE IF NOT EXISTS central_alerts (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  type          TEXT NOT NULL CHECK(type IN
                  ('no_heartbeat','folder_missing','backup_shrink')),
  client_id     INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  target_id     INTEGER REFERENCES targets(id) ON DELETE CASCADE,
  severity      TEXT NOT NULL DEFAULT 'warning'
                  CHECK(severity IN ('info','warning','critical')),
  triggered_at  TEXT NOT NULL,
  last_seen_at  TEXT NOT NULL,
  resolved_at   TEXT,
  notified_at   TEXT,
  detail_json   TEXT
);

CREATE INDEX IF NOT EXISTS idx_alerts_active_lookup
  ON central_alerts(client_id, target_id, type, resolved_at);

CREATE INDEX IF NOT EXISTS idx_alerts_triggered
  ON central_alerts(triggered_at DESC);
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/central/test_alerts_schema.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/models/db.py tests/central/test_alerts_schema.py
git commit -m "alerts: add central_alerts table + indexes"
```

---

### Task 2: Config knobs

**Files:**
- Modify: `backend/config.py` (add 4 ALERTS_* attrs to `Config`)
- Create: `tests/central/test_alerts_config.py`

- [ ] **Step 1: Failing test**

```python
# tests/central/test_alerts_config.py
import importlib


def test_default_thresholds(monkeypatch):
    for k in ("ALERTS_NO_HEARTBEAT_HOURS", "ALERTS_SHRINK_PCT",
              "ALERTS_EMAIL", "ALERTS_WEBHOOK"):
        monkeypatch.delenv(k, raising=False)
    import backend.config
    importlib.reload(backend.config)
    from backend.config import Config
    assert Config.ALERTS_NO_HEARTBEAT_HOURS == 48
    assert Config.ALERTS_SHRINK_PCT == 20
    assert Config.ALERTS_EMAIL == ""
    assert Config.ALERTS_WEBHOOK == ""


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("ALERTS_NO_HEARTBEAT_HOURS", "12")
    monkeypatch.setenv("ALERTS_SHRINK_PCT", "30")
    monkeypatch.setenv("ALERTS_EMAIL", "ops@x.com")
    monkeypatch.setenv("ALERTS_WEBHOOK", "https://hook.example/x")
    import backend.config
    importlib.reload(backend.config)
    from backend.config import Config
    assert Config.ALERTS_NO_HEARTBEAT_HOURS == 12
    assert Config.ALERTS_SHRINK_PCT == 30
    assert Config.ALERTS_EMAIL == "ops@x.com"
    assert Config.ALERTS_WEBHOOK == "https://hook.example/x"
```

- [ ] **Step 2: Run, expect failure**

```bash
.venv/bin/pytest tests/central/test_alerts_config.py -v
```

- [ ] **Step 3: Add config knobs**

Append inside `class Config:` in `backend/config.py` (right after `ARCHIVE_KEEP_MONTHS`):

```python
    # --- Sub-D: alerts (central only) ---
    ALERTS_NO_HEARTBEAT_HOURS = int(
        os.getenv("ALERTS_NO_HEARTBEAT_HOURS")
        or _CONF.get("ALERTS_NO_HEARTBEAT_HOURS")
        or "48"
    )
    ALERTS_SHRINK_PCT = int(
        os.getenv("ALERTS_SHRINK_PCT")
        or _CONF.get("ALERTS_SHRINK_PCT")
        or "20"
    )
    ALERTS_EMAIL = os.getenv("ALERTS_EMAIL") or _CONF.get("ALERTS_EMAIL", "")
    ALERTS_WEBHOOK = os.getenv("ALERTS_WEBHOOK") or _CONF.get("ALERTS_WEBHOOK", "")
```

- [ ] **Step 4: Run, expect pass**

- [ ] **Step 5: Commit**

```bash
git add backend/config.py tests/central/test_alerts_config.py
git commit -m "alerts: Config knobs (NO_HEARTBEAT_HOURS, SHRINK_PCT, EMAIL, WEBHOOK)"
```

---

### Task 3: Alert store (CRUD)

**Files:**
- Create: `backend/central/alerts/__init__.py` (initially exposes only the store)
- Create: `backend/central/alerts/store.py`
- Create: `tests/central/test_alerts_store.py`

- [ ] **Step 1: Write failing test**

```python
# tests/central/test_alerts_store.py
import pytest
from backend.central import models as m
from backend.central.alerts import store as st


@pytest.fixture
def cid_tid(conn):
    cid = m.create_client(conn, proyecto="alpha")
    tid = m.upsert_target(conn, client_id=cid, category="os",
                          subkey="linux", label="host01") if hasattr(m, "upsert_target") else None
    if tid is None:
        # Fall back to direct insert when no helper exists yet.
        cur = conn.execute(
            "INSERT INTO targets(client_id, category, subkey, label, "
            "last_heartbeat_ts) VALUES(?,?,?,?,?)",
            (cid, "os", "linux", "host01", "2026-04-01T00:00:00Z"),
        )
        tid = cur.lastrowid
    return cid, tid


def test_fire_creates_active_alert(conn, cid_tid):
    cid, tid = cid_tid
    a = st.fire(conn, type_="folder_missing", client_id=cid, target_id=tid,
                severity="warning", detail={"missing_paths": ["/etc"]})
    assert a["id"] > 0
    assert a["resolved_at"] is None
    assert a["notified_at"] is None


def test_fire_idempotent_updates_last_seen(conn, cid_tid):
    cid, tid = cid_tid
    a1 = st.fire(conn, type_="folder_missing", client_id=cid, target_id=tid,
                 severity="warning", detail={"missing_paths": ["/etc"]})
    a2 = st.fire(conn, type_="folder_missing", client_id=cid, target_id=tid,
                 severity="warning", detail={"missing_paths": ["/etc","/var"]})
    assert a1["id"] == a2["id"]
    rows = conn.execute(
        "SELECT COUNT(*) FROM central_alerts WHERE resolved_at IS NULL"
    ).fetchone()
    assert rows[0] == 1
    # last_seen_at advances
    assert a2["last_seen_at"] >= a1["last_seen_at"]


def test_resolve_marks_resolved_at(conn, cid_tid):
    cid, tid = cid_tid
    a = st.fire(conn, type_="folder_missing", client_id=cid, target_id=tid,
                severity="warning", detail={})
    st.resolve(conn, a["id"])
    row = conn.execute(
        "SELECT resolved_at FROM central_alerts WHERE id=?", (a["id"],)
    ).fetchone()
    assert row[0] is not None


def test_resolve_active_by_key_no_op_when_no_active(conn, cid_tid):
    cid, tid = cid_tid
    n = st.resolve_active_by_key(conn, type_="folder_missing",
                                 client_id=cid, target_id=tid)
    assert n == 0


def test_resolve_active_by_key_resolves_match(conn, cid_tid):
    cid, tid = cid_tid
    st.fire(conn, type_="folder_missing", client_id=cid, target_id=tid,
            severity="warning", detail={})
    n = st.resolve_active_by_key(conn, type_="folder_missing",
                                 client_id=cid, target_id=tid)
    assert n == 1


def test_acknowledge_resolves_with_actor(conn, cid_tid):
    cid, tid = cid_tid
    a = st.fire(conn, type_="backup_shrink", client_id=cid, target_id=tid,
                severity="critical", detail={})
    st.acknowledge(conn, a["id"], actor_email="op@x.com")
    row = conn.execute(
        "SELECT resolved_at, detail_json FROM central_alerts WHERE id=?",
        (a["id"],),
    ).fetchone()
    assert row[0] is not None
    import json
    detail = json.loads(row[1])
    assert detail.get("acknowledged_by") == "op@x.com"


def test_list_active_returns_only_unresolved(conn, cid_tid):
    cid, tid = cid_tid
    a = st.fire(conn, type_="folder_missing", client_id=cid, target_id=tid,
                severity="warning", detail={})
    st.fire(conn, type_="backup_shrink", client_id=cid, target_id=tid,
            severity="critical", detail={})
    st.resolve(conn, a["id"])
    rows = st.list_active(conn)
    assert len(rows) == 1
    assert rows[0]["type"] == "backup_shrink"


def test_count_active_critical(conn, cid_tid):
    cid, tid = cid_tid
    st.fire(conn, type_="backup_shrink", client_id=cid, target_id=tid,
            severity="critical", detail={})
    st.fire(conn, type_="folder_missing", client_id=cid, target_id=tid,
            severity="warning", detail={})
    assert st.count_active_critical(conn) == 1
```

- [ ] **Step 2: Run, expect failure (module missing)**

```bash
.venv/bin/pytest tests/central/test_alerts_store.py -v
```

- [ ] **Step 3: Implement `backend/central/alerts/__init__.py`**

```python
"""Central alerts subsystem: detection rules, persistence, dispatch.

Public API re-exported here for convenience; sub-modules are the real home:
- store:    DB CRUD for central_alerts rows
- rules:    pure detection logic invoked at heartbeat time
- sweep:    periodic detection (no_heartbeat)
- dispatch: notification dispatcher (email + webhook)
- routes:   alerts_bp Flask blueprint
"""
from .store import (
    fire, resolve, resolve_active_by_key, acknowledge,
    list_active, list_recent, get_by_id, count_active_critical,
)

__all__ = [
    "fire", "resolve", "resolve_active_by_key", "acknowledge",
    "list_active", "list_recent", "get_by_id", "count_active_critical",
]
```

- [ ] **Step 4: Implement `backend/central/alerts/store.py`**

```python
"""DB CRUD for central_alerts.

Idempotency contract: fire() returns the same id if an active alert with
the same (client_id, target_id, type) already exists; it bumps last_seen_at
and merges detail_json. resolve() marks resolved_at. acknowledge() resolves
and stores the actor in detail_json under 'acknowledged_by'.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(r) -> dict:
    cols = ("id", "type", "client_id", "target_id", "severity",
            "triggered_at", "last_seen_at", "resolved_at",
            "notified_at", "detail_json")
    out = dict(zip(cols, r))
    out["detail"] = json.loads(out.pop("detail_json") or "{}")
    return out


def fire(conn: sqlite3.Connection, *, type_: str, client_id: int,
         target_id: Optional[int], severity: str, detail: dict) -> dict:
    """Idempotent: 1 active row per (client_id, target_id, type)."""
    now = _now_iso()
    detail_json = json.dumps(detail or {})
    existing = conn.execute(
        "SELECT id, detail_json FROM central_alerts WHERE type=? "
        "AND client_id=? AND ((target_id IS NULL AND ? IS NULL) OR target_id=?) "
        "AND resolved_at IS NULL",
        (type_, client_id, target_id, target_id),
    ).fetchone()
    if existing:
        merged = json.loads(existing[1] or "{}")
        merged.update(detail or {})
        conn.execute(
            "UPDATE central_alerts SET last_seen_at=?, severity=?, "
            "detail_json=? WHERE id=?",
            (now, severity, json.dumps(merged), existing[0]),
        )
        conn.commit()
        return get_by_id(conn, existing[0])
    cur = conn.execute(
        "INSERT INTO central_alerts(type, client_id, target_id, severity,"
        " triggered_at, last_seen_at, detail_json) VALUES(?,?,?,?,?,?,?)",
        (type_, client_id, target_id, severity, now, now, detail_json),
    )
    conn.commit()
    return get_by_id(conn, cur.lastrowid)


def resolve(conn: sqlite3.Connection, alert_id: int) -> None:
    conn.execute(
        "UPDATE central_alerts SET resolved_at=? "
        "WHERE id=? AND resolved_at IS NULL",
        (_now_iso(), alert_id),
    )
    conn.commit()


def resolve_active_by_key(conn: sqlite3.Connection, *, type_: str,
                          client_id: int,
                          target_id: Optional[int]) -> int:
    """Resolve any active alert matching the key. Returns rowcount."""
    cur = conn.execute(
        "UPDATE central_alerts SET resolved_at=? WHERE type=? "
        "AND client_id=? AND ((target_id IS NULL AND ? IS NULL) OR target_id=?) "
        "AND resolved_at IS NULL",
        (_now_iso(), type_, client_id, target_id, target_id),
    )
    conn.commit()
    return cur.rowcount


def acknowledge(conn: sqlite3.Connection, alert_id: int,
                actor_email: str) -> None:
    row = conn.execute(
        "SELECT detail_json FROM central_alerts WHERE id=?", (alert_id,)
    ).fetchone()
    if not row:
        return
    detail = json.loads(row[0] or "{}")
    detail["acknowledged_by"] = actor_email
    conn.execute(
        "UPDATE central_alerts SET resolved_at=?, detail_json=? WHERE id=?",
        (_now_iso(), json.dumps(detail), alert_id),
    )
    conn.commit()


_SELECT_COLS = ("id, type, client_id, target_id, severity, triggered_at, "
                "last_seen_at, resolved_at, notified_at, detail_json")


def get_by_id(conn: sqlite3.Connection, alert_id: int) -> Optional[dict]:
    r = conn.execute(
        f"SELECT {_SELECT_COLS} FROM central_alerts WHERE id=?", (alert_id,)
    ).fetchone()
    return _row_to_dict(r) if r else None


def list_active(conn: sqlite3.Connection, *, limit: int = 200) -> list[dict]:
    rows = conn.execute(
        f"SELECT {_SELECT_COLS} FROM central_alerts "
        "WHERE resolved_at IS NULL ORDER BY triggered_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def list_recent(conn: sqlite3.Connection, *, limit: int = 200) -> list[dict]:
    rows = conn.execute(
        f"SELECT {_SELECT_COLS} FROM central_alerts "
        "ORDER BY triggered_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def mark_notified(conn: sqlite3.Connection, alert_id: int) -> None:
    conn.execute(
        "UPDATE central_alerts SET notified_at=? WHERE id=?",
        (_now_iso(), alert_id),
    )
    conn.commit()


def count_active_critical(conn: sqlite3.Connection) -> int:
    r = conn.execute(
        "SELECT COUNT(*) FROM central_alerts "
        "WHERE resolved_at IS NULL AND severity='critical'"
    ).fetchone()
    return r[0] if r else 0
```

- [ ] **Step 5: Run tests, expect 8 passed**

```bash
.venv/bin/pytest tests/central/test_alerts_store.py -v
```

- [ ] **Step 6: Commit**

```bash
git add backend/central/alerts/ tests/central/test_alerts_store.py
git commit -m "alerts: store CRUD with idempotent fire and acknowledge"
```

---

### Task 4: Detection rules

**Files:**
- Create: `backend/central/alerts/rules.py`
- Create: `tests/central/test_alerts_rules.py`

- [ ] **Step 1: Failing test**

```python
# tests/central/test_alerts_rules.py
from backend.central.alerts.rules import evaluate_heartbeat


def _payload(missing=None, total_bytes=1_000_000_000):
    return {
        "host_meta": {"hostname": "h", "missing_paths": missing or []},
        "totals": {"size_bytes": total_bytes},
    }


def test_no_findings_when_clean():
    out = evaluate_heartbeat(
        _payload(missing=[], total_bytes=1_000_000_000),
        prev_size_bytes=1_000_000_000,
        thresholds={"shrink_pct": 20},
    )
    assert out == []


def test_folder_missing_fired():
    out = evaluate_heartbeat(
        _payload(missing=["/etc/x"], total_bytes=1000),
        prev_size_bytes=1000,
        thresholds={"shrink_pct": 20},
    )
    assert any(f["type"] == "folder_missing" for f in out)
    folder = next(f for f in out if f["type"] == "folder_missing")
    assert folder["severity"] == "warning"
    assert folder["detail"]["missing_paths"] == ["/etc/x"]


def test_backup_shrink_fired_when_pct_above_threshold():
    out = evaluate_heartbeat(
        _payload(missing=[], total_bytes=500),
        prev_size_bytes=1000,
        thresholds={"shrink_pct": 20},
    )
    shrink = next(f for f in out if f["type"] == "backup_shrink")
    assert shrink["severity"] == "critical"  # 50% drop
    assert shrink["detail"]["pct"] == 50
    assert shrink["detail"]["prev_bytes"] == 1000
    assert shrink["detail"]["new_bytes"] == 500


def test_backup_shrink_warning_below_50pct():
    out = evaluate_heartbeat(
        _payload(missing=[], total_bytes=750),
        prev_size_bytes=1000,
        thresholds={"shrink_pct": 20},
    )
    shrink = next(f for f in out if f["type"] == "backup_shrink")
    assert shrink["severity"] == "warning"  # 25% drop


def test_backup_shrink_skipped_when_under_threshold():
    out = evaluate_heartbeat(
        _payload(missing=[], total_bytes=900),
        prev_size_bytes=1000,
        thresholds={"shrink_pct": 20},
    )
    assert not any(f["type"] == "backup_shrink" for f in out)


def test_backup_shrink_skipped_when_no_prev():
    out = evaluate_heartbeat(
        _payload(total_bytes=1),
        prev_size_bytes=None,
        thresholds={"shrink_pct": 20},
    )
    assert not any(f["type"] == "backup_shrink" for f in out)


def test_no_resolves_when_no_findings_for_existing():
    """resolves_keys returns the SET of (client,target,type) tuples that
    should auto-resolve given THIS heartbeat."""
    from backend.central.alerts.rules import resolves_keys
    keys = resolves_keys(_payload(missing=[]))
    assert "folder_missing" in keys
    assert "no_heartbeat" in keys
    assert "backup_shrink" not in keys  # never auto-resolves
```

- [ ] **Step 2: Run, expect failure**

- [ ] **Step 3: Implement `backend/central/alerts/rules.py`**

```python
"""Pure detection rules — no DB, no IO. Returns descriptors that
store.fire() will persist."""
from __future__ import annotations

from typing import Optional


def evaluate_heartbeat(payload: dict, *, prev_size_bytes: Optional[int],
                       thresholds: dict) -> list[dict]:
    """Returns a list of {type, severity, detail} dicts to fire.
    Side-effect free: caller (apply_heartbeat) persists via store.fire().
    """
    out: list[dict] = []
    host_meta = payload.get("host_meta") or {}
    missing = host_meta.get("missing_paths") or []
    if missing:
        out.append({
            "type": "folder_missing",
            "severity": "warning",
            "detail": {"missing_paths": list(missing)},
        })

    new_size = (payload.get("totals") or {}).get("size_bytes") or 0
    if prev_size_bytes and prev_size_bytes > 0:
        pct = round(100 * (prev_size_bytes - new_size) / prev_size_bytes, 1)
        if pct >= thresholds.get("shrink_pct", 20):
            sev = "critical" if pct >= 50 else "warning"
            out.append({
                "type": "backup_shrink",
                "severity": sev,
                "detail": {
                    "prev_bytes": prev_size_bytes,
                    "new_bytes": new_size,
                    "pct": pct,
                },
            })
    return out


def resolves_keys(payload: dict) -> set[str]:
    """Which alert types should auto-resolve given this heartbeat.

    backup_shrink never auto-resolves (admin must acknowledge).
    """
    out = {"no_heartbeat"}                  # any heartbeat clears no_heartbeat
    host_meta = payload.get("host_meta") or {}
    if not (host_meta.get("missing_paths") or []):
        out.add("folder_missing")
    return out
```

- [ ] **Step 4: Run, expect pass**

- [ ] **Step 5: Commit**

```bash
git add backend/central/alerts/rules.py tests/central/test_alerts_rules.py
git commit -m "alerts: detection rules (folder_missing, backup_shrink) + resolves_keys"
```

---

### Task 5: Sweep job (no_heartbeat)

**Files:**
- Create: `backend/central/alerts/sweep.py`
- Create: `tests/central/test_alerts_sweep.py`

- [ ] **Step 1: Failing test**

```python
# tests/central/test_alerts_sweep.py
from datetime import datetime, timedelta, timezone
from backend.central.alerts.sweep import sweep_inactive
from backend.central.alerts import store as st


def _seed_target(conn, hours_ago: int):
    cid = conn.execute(
        "INSERT INTO clients(proyecto, created_at, updated_at) "
        "VALUES('alpha','2026-04-01','2026-04-01') RETURNING id"
    ).fetchone()[0]
    last_ts = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    tid = conn.execute(
        "INSERT INTO targets(client_id, category, subkey, label, "
        "last_heartbeat_ts) VALUES(?,?,?,?,?) RETURNING id",
        (cid, "os", "linux", "host01", last_ts),
    ).fetchone()[0]
    return cid, tid


def test_sweep_fires_when_stale(conn):
    cid, tid = _seed_target(conn, hours_ago=72)
    n = sweep_inactive(conn, threshold_hours=48)
    assert n == 1
    assert len(st.list_active(conn)) == 1


def test_sweep_skips_fresh(conn):
    _seed_target(conn, hours_ago=5)
    n = sweep_inactive(conn, threshold_hours=48)
    assert n == 0


def test_sweep_idempotent_on_already_active(conn):
    cid, tid = _seed_target(conn, hours_ago=72)
    sweep_inactive(conn, threshold_hours=48)
    sweep_inactive(conn, threshold_hours=48)
    rows = conn.execute(
        "SELECT COUNT(*) FROM central_alerts WHERE resolved_at IS NULL"
    ).fetchone()
    assert rows[0] == 1


def test_sweep_severity_critical_after_7_days(conn):
    cid, tid = _seed_target(conn, hours_ago=24*8)
    sweep_inactive(conn, threshold_hours=48)
    rows = st.list_active(conn)
    assert rows[0]["severity"] == "critical"


def test_sweep_severity_warning_below_7_days(conn):
    cid, tid = _seed_target(conn, hours_ago=72)
    sweep_inactive(conn, threshold_hours=48)
    rows = st.list_active(conn)
    assert rows[0]["severity"] == "warning"
```

- [ ] **Step 2: Run, expect failure**

- [ ] **Step 3: Implement `backend/central/alerts/sweep.py`**

```python
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
        # hours_since: int division of seconds since last_ts
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
```

- [ ] **Step 4: Run, expect pass**

- [ ] **Step 5: Commit**

```bash
git add backend/central/alerts/sweep.py tests/central/test_alerts_sweep.py
git commit -m "alerts: sweep_inactive job for no_heartbeat detection"
```

---

## Phase 2: Hooks + Notification

### Task 6: Hook into apply_heartbeat

**Files:**
- Modify: `backend/central/models.py` (`apply_heartbeat`)

- [ ] **Step 1: Failing test**

```python
# tests/central/test_alerts_models_hook.py
from backend.central import models as m
from backend.central.alerts import store as st


def _payload(eid, *, total_bytes=1000, missing=None):
    return {
        "event_id": eid,
        "ts": "2026-04-27T17:00:00Z",
        "client": {"proyecto": "alpha", "entorno": "cloud", "pais": "co"},
        "target": {"category": "os", "subkey": "linux", "label": "host01"},
        "operation": {"op": "archive", "status": "ok",
                      "started_at": "2026-04-27T17:00:00Z",
                      "duration_s": 1, "error": None},
        "snapshot": {"size_bytes": total_bytes, "remote_path": "x",
                     "encrypted": False},
        "totals": {"size_bytes": total_bytes, "count_files": 1,
                   "oldest_ts": None, "newest_ts": "2026-04-27T17:00:00Z"},
        "host_meta": {"hostname": "h", "snapctl_version": "0",
                      "rclone_version": "0",
                      "missing_paths": missing or []},
    }


def test_heartbeat_with_missing_paths_fires_alert(conn):
    cid = m.create_client(conn, proyecto="alpha")
    m.apply_heartbeat(conn, _payload("e1", missing=["/etc/x"]),
                      token_id=1, client_id=cid, src_ip="1.1.1.1")
    rows = st.list_active(conn)
    types = [r["type"] for r in rows]
    assert "folder_missing" in types


def test_heartbeat_clean_resolves_prior_folder_missing(conn):
    cid = m.create_client(conn, proyecto="alpha")
    m.apply_heartbeat(conn, _payload("e1", missing=["/etc/x"]),
                      token_id=1, client_id=cid, src_ip="1.1.1.1")
    m.apply_heartbeat(conn, _payload("e2", missing=[]),
                      token_id=1, client_id=cid, src_ip="1.1.1.1")
    rows = [r for r in st.list_active(conn) if r["type"] == "folder_missing"]
    assert rows == []


def test_shrink_alert_fires_after_two_heartbeats(conn):
    cid = m.create_client(conn, proyecto="alpha")
    m.apply_heartbeat(conn, _payload("e1", total_bytes=1_000_000),
                      token_id=1, client_id=cid, src_ip="1.1.1.1")
    m.apply_heartbeat(conn, _payload("e2", total_bytes=400_000),
                      token_id=1, client_id=cid, src_ip="1.1.1.1")
    rows = [r for r in st.list_active(conn) if r["type"] == "backup_shrink"]
    assert len(rows) == 1
    assert rows[0]["detail"]["pct"] == 60.0
```

- [ ] **Step 2: Run, expect failure**

- [ ] **Step 3: Modify `backend/central/models.py::apply_heartbeat`**

Read the file first. Identify the line just before the final return where the target row has been upserted. Capture the prev `total_size_bytes` BEFORE the upsert; after upsert call into rules + store:

```python
# Near top of file:
from backend.config import Config
from .alerts import store as alerts_store
from .alerts import rules as alerts_rules
```

Inside `apply_heartbeat`, BEFORE the target upsert query (find the `INSERT ... ON CONFLICT(client_id, category, subkey, label) DO UPDATE` line), capture:

```python
prev_row = conn.execute(
    "SELECT id, total_size_bytes FROM targets WHERE client_id=? AND "
    "category=? AND subkey=? AND label=?",
    (client_id, payload["target"]["category"],
     payload["target"]["subkey"], payload["target"]["label"]),
).fetchone()
prev_size = prev_row[1] if prev_row else None
```

AFTER the upsert (right before constructing `HeartbeatResult`), get the target_id (already in scope) and call:

```python
findings = alerts_rules.evaluate_heartbeat(
    payload,
    prev_size_bytes=prev_size,
    thresholds={"shrink_pct": Config.ALERTS_SHRINK_PCT},
)
for f in findings:
    alerts_store.fire(conn, type_=f["type"], client_id=client_id,
                      target_id=target_id, severity=f["severity"],
                      detail=f["detail"])
for type_to_resolve in alerts_rules.resolves_keys(payload):
    alerts_store.resolve_active_by_key(
        conn, type_=type_to_resolve, client_id=client_id, target_id=target_id,
    )
```

Note: dispatch.notify() is NOT called yet — Task 7 wires it.

- [ ] **Step 4: Run, expect pass**

```bash
.venv/bin/pytest tests/central/test_alerts_models_hook.py tests/central/test_central_models.py -v
```

Run the existing models tests too — they should still pass.

- [ ] **Step 5: Commit**

```bash
git add backend/central/models.py tests/central/test_alerts_models_hook.py
git commit -m "alerts: hook evaluate_heartbeat into apply_heartbeat"
```

---

### Task 7: Notification dispatcher

**Files:**
- Create: `backend/central/alerts/dispatch.py`
- Create: `tests/central/test_alerts_dispatch.py`
- Modify: `backend/central/models.py` (call dispatch after fire)

- [ ] **Step 1: Failing test**

```python
# tests/central/test_alerts_dispatch.py
import pytest
from unittest.mock import MagicMock
from backend.central.alerts import dispatch as d


def test_notify_no_op_when_no_smtp_no_webhook(conn, monkeypatch):
    monkeypatch.setattr("backend.central.alerts.dispatch.Config.SMTP_HOST", "")
    monkeypatch.setattr("backend.central.alerts.dispatch.Config.ALERTS_WEBHOOK", "")
    sent = d.notify(conn, alert={"id": 1, "type": "x", "severity": "warning",
                                  "detail": {}, "triggered_at": "now"},
                    client={"proyecto": "p"},
                    target={"label": "t", "category": "os", "subkey": "x"})
    assert sent == {"email": False, "webhook": False}


def test_notify_webhook_posts_json(conn, monkeypatch):
    monkeypatch.setattr("backend.central.alerts.dispatch.Config.ALERTS_WEBHOOK",
                        "https://hook.example/x")
    fake = MagicMock(); fake.status_code = 200
    posted = {}
    def _post(url, json=None, timeout=None):
        posted["url"] = url
        posted["json"] = json
        return fake
    monkeypatch.setattr("backend.central.alerts.dispatch.requests.post", _post)
    d.notify(conn, alert={"id": 7, "type": "no_heartbeat",
                          "severity": "critical", "detail": {}, "triggered_at": "t"},
             client={"proyecto": "alpha"}, target={"label": "host01",
                                                   "category": "os",
                                                   "subkey": "linux"})
    assert posted["url"] == "https://hook.example/x"
    assert posted["json"]["event"] == "alert.fired"
    assert posted["json"]["alert"]["id"] == 7


def test_notify_marks_notified_at(conn, monkeypatch):
    cid = conn.execute(
        "INSERT INTO clients(proyecto, created_at, updated_at) "
        "VALUES('z','2026-01-01','2026-01-01') RETURNING id"
    ).fetchone()[0]
    a = conn.execute(
        "INSERT INTO central_alerts(type, client_id, severity, "
        "triggered_at, last_seen_at) VALUES(?,?,?,?,?) RETURNING id",
        ("folder_missing", cid, "warning", "now", "now"),
    ).fetchone()[0]
    monkeypatch.setattr("backend.central.alerts.dispatch.Config.ALERTS_WEBHOOK", "")
    monkeypatch.setattr("backend.central.alerts.dispatch.Config.SMTP_HOST", "")
    d.notify(conn, alert={"id": a, "type": "folder_missing",
                          "severity": "warning", "detail": {},
                          "triggered_at": "now"},
             client={"proyecto": "z"},
             target={"label": "t", "category": "os", "subkey": "x"})
    row = conn.execute(
        "SELECT notified_at FROM central_alerts WHERE id=?", (a,)
    ).fetchone()
    assert row[0] is not None
```

- [ ] **Step 2: Run, expect failure**

- [ ] **Step 3: Implement `backend/central/alerts/dispatch.py`**

```python
"""Notification dispatcher for fired alerts.

Best-effort: failure to send email / webhook is logged but does not
prevent the alert from being recorded. notified_at is bumped regardless
to prevent retry storms (next scheduled fire on resolve+re-fire).
"""
from __future__ import annotations

import logging
import smtplib
import sqlite3
from email.message import EmailMessage

import requests

from backend.config import Config
from . import store

log = logging.getLogger(__name__)


def _send_email(alert: dict, client: dict, target: dict) -> bool:
    if not Config.SMTP_HOST:
        return False
    to_email = Config.ALERTS_EMAIL or ""
    if not to_email:
        return False
    msg = EmailMessage()
    msg["Subject"] = (f"[snapshot-V3] {alert['severity'].upper()}: "
                      f"{alert['type']} en {client.get('proyecto', '?')}")
    msg["From"] = Config.SMTP_FROM if hasattr(Config, "SMTP_FROM") and Config.SMTP_FROM else f"snapshot-v3@{Config.SMTP_HOST}"
    msg["To"] = to_email
    target_label = (f"{target.get('category')}/{target.get('subkey')}/"
                    f"{target.get('label')}")
    msg.set_content(
        f"Alerta tipo: {alert['type']}\n"
        f"Severidad: {alert['severity']}\n"
        f"Cliente: {client.get('proyecto')}\n"
        f"Target: {target_label}\n"
        f"Triggered at: {alert['triggered_at']}\n\n"
        f"Detalle: {alert.get('detail')}\n"
    )
    try:
        port = int(getattr(Config, "SMTP_PORT", 587) or 587)
        with smtplib.SMTP(Config.SMTP_HOST, port, timeout=10) as s:
            try: s.starttls()
            except Exception: pass
            user = getattr(Config, "SMTP_USER", "") or ""
            pwd = getattr(Config, "SMTP_PASSWORD", "") or ""
            if user:
                s.login(user, pwd)
            s.send_message(msg)
        return True
    except Exception as e:
        log.warning("alerts: email send failed: %s", e)
        return False


def _post_webhook(alert: dict, client: dict, target: dict,
                  *, event: str = "alert.fired") -> bool:
    url = Config.ALERTS_WEBHOOK
    if not url:
        return False
    body = {
        "event": event,
        "alert": {
            "id": alert["id"], "type": alert["type"],
            "severity": alert["severity"],
            "triggered_at": alert["triggered_at"],
            "detail": alert.get("detail") or {},
        },
        "client": {"proyecto": client.get("proyecto"),
                   "organizacion": client.get("organizacion")},
        "target": {"label": target.get("label"),
                   "category": target.get("category"),
                   "subkey": target.get("subkey")},
    }
    try:
        r = requests.post(url, json=body, timeout=5)
        return 200 <= r.status_code < 300
    except Exception as e:
        log.warning("alerts: webhook failed: %s", e)
        return False


def notify(conn: sqlite3.Connection, *, alert: dict, client: dict,
           target: dict, event: str = "alert.fired") -> dict:
    sent_email = _send_email(alert, client, target)
    sent_hook = _post_webhook(alert, client, target, event=event)
    store.mark_notified(conn, alert["id"])
    return {"email": sent_email, "webhook": sent_hook}
```

- [ ] **Step 4: Wire dispatch into `apply_heartbeat`**

After the `for f in findings: alerts_store.fire(...)` block in `apply_heartbeat`, append:

```python
from .alerts import dispatch as alerts_dispatch
client_row = get_client(conn, client_id)
target_row = conn.execute(
    "SELECT id, category, subkey, label FROM targets WHERE id=?",
    (target_id,),
).fetchone()
target_dict = {"id": target_row[0], "category": target_row[1],
               "subkey": target_row[2], "label": target_row[3]} if target_row else {}
for f in findings:
    fired_alert = alerts_store.fire(  # already called above; but get the row again to dispatch
        conn, type_=f["type"], client_id=client_id, target_id=target_id,
        severity=f["severity"], detail=f["detail"],
    )
    if not fired_alert.get("notified_at"):
        alerts_dispatch.notify(conn, alert=fired_alert, client=client_row,
                                target=target_dict)
```

NOTE: this calls fire twice on the same finding — once in the original loop (Task 6) and once here. Refactor: replace the original Task 6 loop with this combined version. The combined version becomes:

```python
for f in findings:
    fired_alert = alerts_store.fire(
        conn, type_=f["type"], client_id=client_id, target_id=target_id,
        severity=f["severity"], detail=f["detail"],
    )
    if not fired_alert.get("notified_at"):
        alerts_dispatch.notify(conn, alert=fired_alert, client=client_row,
                                target=target_dict)
for type_to_resolve in alerts_rules.resolves_keys(payload):
    alerts_store.resolve_active_by_key(
        conn, type_=type_to_resolve, client_id=client_id, target_id=target_id,
    )
```

- [ ] **Step 5: Run, expect pass**

```bash
.venv/bin/pytest tests/central/test_alerts_dispatch.py tests/central/test_alerts_models_hook.py -v
```

- [ ] **Step 6: Commit**

```bash
git add backend/central/alerts/dispatch.py backend/central/models.py \
       tests/central/test_alerts_dispatch.py
git commit -m "alerts: notification dispatcher (email + webhook) wired to apply_heartbeat"
```

---

## Phase 3: API + UI

### Task 8: API endpoints

**Files:**
- Create: `backend/central/alerts/routes.py`
- Modify: `backend/central/alerts/__init__.py` (add `alerts_bp`)
- Modify: `backend/app.py` (register alerts_bp under MODE=central)
- Create: `tests/central/test_alerts_routes.py`

- [ ] **Step 1: Failing test**

```python
# tests/central/test_alerts_routes.py
import pytest


@pytest.fixture
def central_app(monkeypatch, tmp_path):
    monkeypatch.setenv("MODE", "central")
    monkeypatch.setenv("SNAPSHOT_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("SNAPSHOT_SECRET_KEY", "0" * 64)
    monkeypatch.setenv("SNAPSHOT_TEST_MODE", "1")
    import importlib, backend.config, backend.app
    importlib.reload(backend.config)
    importlib.reload(backend.app)
    from backend.app import create_app
    app = create_app()
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(central_app):
    return central_app.test_client()


@pytest.fixture
def conn(central_app):
    return central_app.config["DB_CONN"]


def test_list_alerts_requires_auth(client):
    r = client.get("/api/admin/alerts")
    assert r.status_code in (401, 403)


def test_list_alerts_for_auditor_returns_active(client, conn):
    from tests.auth.helpers import create_user_and_login
    from backend.central import models as m
    from backend.central.alerts import store as st
    cid = m.create_client(conn, proyecto="alpha")
    st.fire(conn, type_="folder_missing", client_id=cid, target_id=None,
            severity="warning", detail={})
    create_user_and_login(client, conn, role="auditor")
    r = client.get("/api/admin/alerts?active=1")
    assert r.status_code == 200
    data = r.get_json()["data"]
    assert len(data) == 1
    assert data[0]["type"] == "folder_missing"


def test_acknowledge_requires_alerts_configure(client, conn):
    from tests.auth.helpers import create_user_and_login
    from backend.central import models as m
    from backend.central.alerts import store as st
    cid = m.create_client(conn, proyecto="alpha")
    a = st.fire(conn, type_="backup_shrink", client_id=cid, target_id=None,
                severity="critical", detail={})
    _, _, csrf = create_user_and_login(client, conn, role="auditor")
    r = client.post(f"/api/admin/alerts/{a['id']}/acknowledge",
                    headers={"X-CSRF-Token": csrf})
    assert r.status_code == 403


def test_operator_can_acknowledge(client, conn):
    from tests.auth.helpers import create_user_and_login
    from backend.central import models as m
    from backend.central.alerts import store as st
    cid = m.create_client(conn, proyecto="alpha")
    a = st.fire(conn, type_="backup_shrink", client_id=cid, target_id=None,
                severity="critical", detail={})
    _, _, csrf = create_user_and_login(client, conn, role="operator")
    r = client.post(f"/api/admin/alerts/{a['id']}/acknowledge",
                    headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200
    row = conn.execute(
        "SELECT resolved_at FROM central_alerts WHERE id=?", (a["id"],)
    ).fetchone()
    assert row[0] is not None


def test_get_config_returns_thresholds(client, conn):
    from tests.auth.helpers import create_user_and_login
    create_user_and_login(client, conn, role="auditor")
    r = client.get("/api/admin/alerts/config")
    assert r.status_code == 200
    cfg = r.get_json()["data"]
    assert "no_heartbeat_hours" in cfg
    assert "shrink_pct" in cfg
```

- [ ] **Step 2: Run, expect failure**

- [ ] **Step 3: Implement `backend/central/alerts/routes.py`**

```python
"""HTTP API for alerts."""
from __future__ import annotations

from flask import Blueprint, current_app, g, jsonify, request

from backend.config import Config
from . import store
from ..permissions import require_central_perm

alerts_bp = Blueprint("central_alerts", __name__, url_prefix="/api/admin/alerts")


def _db():
    return current_app.config["DB_CONN"]


def _ok(data=None, status=200):
    return jsonify(ok=True, data=data, error=None), status


def _err(msg, status=400):
    return jsonify(ok=False, data=None, error=msg), status


@alerts_bp.get("")
@require_central_perm("central.dashboard:view")
def list_alerts():
    active = request.args.get("active") not in ("0", "false", "no", None)
    limit = min(request.args.get("limit", default=200, type=int), 1000)
    if active:
        return _ok(store.list_active(_db(), limit=limit))
    return _ok(store.list_recent(_db(), limit=limit))


@alerts_bp.get("/<int:alert_id>")
@require_central_perm("central.dashboard:view")
def get_alert(alert_id):
    a = store.get_by_id(_db(), alert_id)
    if not a:
        return _err("not found", 404)
    return _ok(a)


@alerts_bp.post("/<int:alert_id>/acknowledge")
@require_central_perm("central.alerts:configure")
def acknowledge_alert(alert_id):
    a = store.get_by_id(_db(), alert_id)
    if not a:
        return _err("not found", 404)
    actor = g.current_user.email if getattr(g, "current_user", None) else "unknown"
    store.acknowledge(_db(), alert_id, actor_email=actor)
    return _ok({"id": alert_id, "resolved": True})


@alerts_bp.get("/config")
@require_central_perm("central.dashboard:view")
def get_config():
    return _ok({
        "no_heartbeat_hours": Config.ALERTS_NO_HEARTBEAT_HOURS,
        "shrink_pct": Config.ALERTS_SHRINK_PCT,
        "email": Config.ALERTS_EMAIL,
        "webhook_set": bool(Config.ALERTS_WEBHOOK),
    })
```

- [ ] **Step 4: Update `backend/central/alerts/__init__.py`**

Append:

```python
from .routes import alerts_bp  # noqa: E402, F401
```

- [ ] **Step 5: Register in `backend/app.py`**

In the `if Config.MODE == "central":` block, add:

```python
        from .central.alerts import alerts_bp as central_alerts_bp
        app.register_blueprint(central_alerts_bp)
```

Also add CSRF exemption for the alerts endpoints if needed (not needed — they use snapshot_session CSRF like other admin endpoints).

- [ ] **Step 6: Run, expect pass**

```bash
.venv/bin/pytest tests/central/test_alerts_routes.py -v
.venv/bin/pytest tests/ -q
```

- [ ] **Step 7: Commit**

```bash
git add backend/central/alerts/routes.py backend/central/alerts/__init__.py \
       backend/app.py tests/central/test_alerts_routes.py
git commit -m "alerts: REST API (/api/admin/alerts*) with permission gating"
```

---

### Task 9: UI page + banner

**Files:**
- Create: `frontend/templates/central/alerts.html`
- Create: `frontend/static/js/central/alerts.js`
- Modify: `frontend/templates/base.html` (banner)
- Modify: `backend/app.py` (context_processor for alert count)
- Modify: `backend/central/dashboard.py` (route GET /dashboard-central/alerts)

- [ ] **Step 1: Add route**

In `backend/central/dashboard.py`, after `tokens_page`, add:

```python
from .alerts import store as alerts_store


@central_dashboard_bp.get("/dashboard-central/alerts")
@require_central_perm("central.dashboard:view")
def alerts_page():
    db = current_app.config["DB_CONN"]
    status_filter = request.args.get("status", "active")
    if status_filter == "active":
        rows = alerts_store.list_active(db)
    else:
        rows = alerts_store.list_recent(db, limit=500)
    return render_template("central/alerts.html",
                           rows=rows, status_filter=status_filter)
```

Add `request` to the existing imports at the top of `backend/central/dashboard.py`:

```python
from flask import Blueprint, current_app, render_template, request
```

- [ ] **Step 2: Create `frontend/templates/central/alerts.html`**

```html
{% extends "base.html" %}
{% block title %}Alertas — Central{% endblock %}
{% block header %}Alertas{% endblock %}
{% block content %}
<section class="p-6">
  <div class="mb-4 flex items-center gap-3 text-sm">
    <span>Filtro:</span>
    <a href="?status=active"
       class="px-3 py-1 rounded border border-[var(--border)] {% if status_filter == 'active' %}bg-brand-600 text-white{% endif %}">
      Activas
    </a>
    <a href="?status=all"
       class="px-3 py-1 rounded border border-[var(--border)] {% if status_filter == 'all' %}bg-brand-600 text-white{% endif %}">
      Todas
    </a>
    <span class="text-[var(--muted)]">{{ rows|length }} resultado(s)</span>
  </div>

  {% if not rows %}
  <div class="text-sm text-[var(--muted)] p-8 text-center border border-dashed rounded">
    Sin alertas {% if status_filter == 'active' %}activas{% endif %}.
  </div>
  {% else %}
  <table class="min-w-full text-sm rounded border border-[var(--border)]">
    <thead class="bg-[var(--surface)]">
      <tr>
        <th class="text-left px-4 py-2">Tipo</th>
        <th class="text-left px-4 py-2">Severidad</th>
        <th class="text-left px-4 py-2">Cliente</th>
        <th class="text-left px-4 py-2">Target</th>
        <th class="text-left px-4 py-2">Disparada</th>
        <th class="text-left px-4 py-2">Estado</th>
        <th class="text-right px-4 py-2">Acciones</th>
      </tr>
    </thead>
    <tbody>
    {% for a in rows %}
      <tr class="border-t border-[var(--border)]" data-alert-id="{{ a.id }}">
        <td class="px-4 py-2 font-mono">{{ a.type }}</td>
        <td class="px-4 py-2">
          <span class="px-2 py-0.5 rounded text-xs
                       {% if a.severity == 'critical' %}bg-rose-500/20 text-rose-300
                       {% elif a.severity == 'warning' %}bg-amber-500/20 text-amber-300
                       {% else %}bg-slate-500/20 text-slate-300{% endif %}">
            {{ a.severity }}
          </span>
        </td>
        <td class="px-4 py-2">{{ a.client_id }}</td>
        <td class="px-4 py-2">{{ a.target_id or "—" }}</td>
        <td class="px-4 py-2 font-mono text-xs">{{ a.triggered_at }}</td>
        <td class="px-4 py-2">
          {% if a.resolved_at %}
            <span class="text-emerald-300">resuelto</span>
          {% else %}
            <span class="text-amber-300">activa</span>
          {% endif %}
        </td>
        <td class="px-4 py-2 text-right">
          {% if not a.resolved_at and g.current_user and g.current_user.role in ('admin', 'operator') %}
          <button class="ack-btn rounded border border-[var(--border)] px-2 py-1 text-xs hover:bg-black/5">
            Acknowledge
          </button>
          {% endif %}
        </td>
      </tr>
      <tr class="border-t border-[var(--border)] bg-black/5">
        <td colspan="7" class="px-4 py-1 text-xs font-mono text-[var(--muted)]">
          {{ a.detail | tojson }}
        </td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
  {% endif %}
</section>
<script src="{{ url_for('static', filename='js/central/alerts.js') }}"></script>
{% endblock %}
```

- [ ] **Step 3: Create `frontend/static/js/central/alerts.js`**

```javascript
document.querySelectorAll(".ack-btn").forEach(btn => {
  btn.addEventListener("click", async () => {
    const aid = btn.closest("tr").dataset.alertId;
    if (!confirm("¿Marcar esta alerta como resuelta? El admin debe haber verificado la causa raíz.")) return;
    const r = await apiFetch(`/api/admin/alerts/${aid}/acknowledge`, {method: "POST"});
    if (r.ok) location.reload();
    else alert("Error al hacer acknowledge");
  });
});
```

- [ ] **Step 4: Add context_processor in `backend/app.py`**

Inside `create_app()`, after the existing `_inject_flags` context_processor, add:

```python
    @app.context_processor
    def _inject_alerts_count():
        if Config.MODE != "central":
            return {"central_alerts_critical": 0}
        try:
            from .central.alerts import store as alerts_store
            return {
                "central_alerts_critical":
                    alerts_store.count_active_critical(app.config["DB_CONN"])
            }
        except Exception:
            return {"central_alerts_critical": 0}
```

- [ ] **Step 5: Add banner to `frontend/templates/base.html`**

Inside `<body>`, BEFORE the existing `<div class="flex h-full">` div, add:

```html
{% if central_alerts_critical and central_alerts_critical > 0 %}
<div class="bg-rose-600 text-white text-sm px-4 py-2 text-center">
  🚨 {{ central_alerts_critical }} alerta(s) crítica(s) activa(s) —
  <a href="/dashboard-central/alerts" class="underline">ver alertas</a>
</div>
{% endif %}
```

Also add a nav link to alerts in the central section of the sidebar (between "Dashboard" and "Auditoría"):

```html
{% if current_user and current_user.role in ('admin', 'operator', 'auditor') %}
<a href="/dashboard-central/alerts" class="nav-item">
  <svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
  <span>Alertas</span>
</a>
{% endif %}
```

- [ ] **Step 6: Smoke test**

```bash
.venv/bin/pytest tests/ -q
```

Manual: visit `/dashboard-central/alerts` after creating a fake alert via `store.fire`.

- [ ] **Step 7: Commit**

```bash
git add backend/central/dashboard.py backend/app.py \
       frontend/templates/central/alerts.html \
       frontend/templates/base.html \
       frontend/static/js/central/alerts.js
git commit -m "alerts: UI page + critical banner + nav link"
```

---

## Phase 4: Sweep timer + heartbeat enrichment

### Task 10: snapctl alerts-sweep CLI + healthcheck timer

**Files:**
- Modify: `backend/central/cli.py` (add `alerts-sweep` subcommand)
- Modify: `core/bin/snapctl` (extend the `central` dispatcher)
- Modify: `systemd/snapshot-healthcheck.service` (additional ExecStartPost)
- Create: `tests/central/test_alerts_cli.py`

- [ ] **Step 1: Failing test**

```python
# tests/central/test_alerts_cli.py
import json
import os
import subprocess

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PYBIN = os.path.join(PROJECT_ROOT, ".venv", "bin", "python")


def test_alerts_sweep_runs(tmp_path):
    env = {**os.environ,
           "SNAPSHOT_DB_PATH": str(tmp_path / "t.db"),
           "MODE": "central",
           "ALERTS_NO_HEARTBEAT_HOURS": "48"}
    out = subprocess.check_output(
        [PYBIN, "-m", "backend.central.cli", "alerts-sweep"],
        env=env, cwd=PROJECT_ROOT,
    )
    j = json.loads(out)
    assert j["ok"] is True
    assert "fired" in j
```

- [ ] **Step 2: Run, expect failure**

- [ ] **Step 3: Add `alerts-sweep` to `backend/central/cli.py`**

In the dispatch logic of `main()`, add a new branch:

```python
    elif cmd == "alerts-sweep":
        from . import alerts
        from backend.config import Config
        n = alerts.sweep.sweep_inactive(
            conn, threshold_hours=Config.ALERTS_NO_HEARTBEAT_HOURS,
        )
        print(json.dumps({"ok": True, "fired": n}))
```

Also expose `sweep` in `alerts/__init__.py`:

```python
from . import sweep  # noqa
```

- [ ] **Step 4: Add to `core/bin/snapctl` central dispatcher**

In the `central)` case in main, extend the inner case:

```bash
                alerts-sweep)    "$pybin" -m backend.central.cli alerts-sweep ;;
```

- [ ] **Step 5: Add `ExecStartPost` to `systemd/snapshot-healthcheck.service`**

Append:

```
ExecStartPost=-/opt/snapshot-V3/core/bin/snapctl central alerts-sweep
```

- [ ] **Step 6: Run, expect pass**

```bash
.venv/bin/pytest tests/central/test_alerts_cli.py -v
```

- [ ] **Step 7: Commit**

```bash
git add backend/central/cli.py backend/central/alerts/__init__.py \
       core/bin/snapctl systemd/snapshot-healthcheck.service \
       tests/central/test_alerts_cli.py
git commit -m "alerts: snapctl central alerts-sweep + healthcheck integration"
```

---

### Task 11: archive.sh emits missing_paths

**Files:**
- Modify: `core/lib/archive.sh` (collect missing paths into a variable, pass to `central_send` via env)
- Modify: `core/lib/central.sh` (use `MISSING_PATHS_JSON` env if set)

- [ ] **Step 1: Modify `core/lib/archive.sh`**

In `cmd_archive()`, find the loop that collects existing paths:

```bash
local -a paths=()
local -a missing_paths=()
for p in $BACKUP_PATHS; do
    if [[ -e "$p" ]]; then
        paths+=("$p")
    else
        log_warn "Ruta no existe, se omite del archive: $p"
        missing_paths+=("$p")
    fi
done
```

(Add the `missing_paths+=` line — the `paths+=` and `log_warn` already exist.)

Build a JSON array of missing paths just before calling `central_send`:

```bash
# Build JSON: ["/etc","/var"] from bash array (or [] when empty).
local missing_json="[]"
if [[ ${#missing_paths[@]} -gt 0 ]]; then
    missing_json="$(printf '%s\n' "${missing_paths[@]}" \
        | python3 -c 'import json,sys; print(json.dumps([l.strip() for l in sys.stdin if l.strip()]))')"
fi
```

In both the OK branch and the FAIL branch's `central_send` call, prepend the env:

```bash
MISSING_PATHS_JSON="$missing_json" ENCRYPTED="${encrypted}" central_send "ok" ...
```

- [ ] **Step 2: Modify `core/lib/central.sh`**

In the `central_send()` function, replace the `host_meta` JSON line:

```bash
"host_meta": {"hostname": "$(hostname)", "snapctl_version": "${SNAPCTL_VERSION:-dev}",
              "rclone_version": "$(rclone version 2>/dev/null | head -1 | awk '{print $2}')",
              "missing_paths": ${MISSING_PATHS_JSON:-[]}}
```

(The `${MISSING_PATHS_JSON:-[]}` defaults to `[]` if not set, so old callers still work.)

- [ ] **Step 3: Manual smoke test (optional, since archive.sh requires Drive)**

```bash
bash -n core/lib/archive.sh
bash -n core/lib/central.sh
```

Both should exit 0.

- [ ] **Step 4: Commit**

```bash
git add core/lib/archive.sh core/lib/central.sh
git commit -m "alerts: archive.sh emits missing_paths in heartbeat host_meta"
```

---

### Task 12: README + final tests

**Files:**
- Modify: `README.md` (add "Alertas (modo central)" subsection under "Modo central")
- Modify: `core/etc/snapshot.local.conf.example` (document the four ALERTS_* knobs)

- [ ] **Step 1: Append to `README.md` after the "Modo central" section, before "## CLI"**

```markdown
### Alertas (modo central)

El central detecta automáticamente tres condiciones y notifica:

| Tipo | Disparo | Resolución |
|---|---|---|
| `no_heartbeat` | target sin reportar > `ALERTS_NO_HEARTBEAT_HOURS` (default 48h) | auto al siguiente heartbeat OK |
| `folder_missing` | heartbeat reporta `host_meta.missing_paths` no vacío | auto al siguiente heartbeat sin paths faltantes |
| `backup_shrink` | totals cae > `ALERTS_SHRINK_PCT`% (default 20%) entre heartbeats | manual (admin clickea "Acknowledge") |

**Configuración** en `/etc/snapshot-v3/snapshot.local.conf`:

```bash
ALERTS_NO_HEARTBEAT_HOURS="48"
ALERTS_SHRINK_PCT="20"
ALERTS_EMAIL=""              # vacío = a los users con role=admin
ALERTS_WEBHOOK=""            # POST JSON para Slack/Discord/etc
```

**UI:** `/dashboard-central/alerts` muestra activas + histórico. Banner
rojo en el header cuando hay alertas críticas activas.

**Notificación:** email (vía SMTP de `snapshot.local.conf`) + webhook
opcional. Falla silenciosa si SMTP/webhook no configurado o caído.

**Sweep `no_heartbeat`:** ejecuta cada 15 min vía
`snapshot-healthcheck.timer` → `snapctl central alerts-sweep`.
```

- [ ] **Step 2: Append to `core/etc/snapshot.local.conf.example`**

```bash

# --- Sub-D: Alertas (solo aplica en MODE=central) -------------------------
# Threshold para "cliente sin reportar":
ALERTS_NO_HEARTBEAT_HOURS="48"
# Threshold % shrink en totals para "backup borrado":
ALERTS_SHRINK_PCT="20"
# Destinatario de notificaciones — vacío = a todos los admins:
ALERTS_EMAIL=""
# Webhook POST JSON opcional (Slack/Discord/PagerDuty webhook URL):
ALERTS_WEBHOOK=""
```

- [ ] **Step 3: Run full suite**

```bash
.venv/bin/pytest tests/ -q
```

Expected: all green (170 baseline + ~25 new from sub-D).

- [ ] **Step 4: Commit**

```bash
git add README.md core/etc/snapshot.local.conf.example
git commit -m "docs: README + local.conf example for central alerts"
```

---

### Task 13: Push + open PR

- [ ] **Step 1: Push branch**

```bash
git push origin feature/central-alerts
```

- [ ] **Step 2: Print PR URL**

```
https://github.com/lmmenesessupervisa/snapshot-drive/pull/new/feature/central-alerts
```

User opens PR with body summarizing the 3 detection rules + email/webhook + UI.

---

## Plan complete

13 tasks covering full sub-project D:
- Phase 1 (T1-T5): foundation — schema, config, store, rules, sweep
- Phase 2 (T6-T7): hooks — apply_heartbeat integration, dispatch
- Phase 3 (T8-T9): API + UI
- Phase 4 (T10-T11): timer + archive.sh enrichment
- Final (T12-T13): docs + PR
