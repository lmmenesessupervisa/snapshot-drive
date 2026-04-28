"""Data access layer del modo central. Patron: funciones puras que toman
una sqlite3.Connection raw, sin ORM. Queries cortas, parametros bound,
transaccion explicita en apply_heartbeat para mantener events <-> targets
coherentes."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class HeartbeatResult:
    event_inserted: bool        # False si ya existia (duplicado idempotente)
    target_created: bool         # True si se creo un target nuevo
    target_id: int


def create_client(conn, *, proyecto: str, organizacion: str | None = None,
                  contacto: str | None = None,
                  retencion_meses: int | None = None,
                  notas: str | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO clients (proyecto, organizacion, contacto, retencion_meses, notas, "
        "created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
        (proyecto, organizacion, contacto, retencion_meses, notas, _now_iso(), _now_iso()),
    )
    conn.commit()
    return cur.lastrowid


def get_client(conn, client_id: int) -> dict | None:
    row = conn.execute(
        "SELECT id, proyecto, organizacion, contacto, retencion_meses, notas "
        "FROM clients WHERE id=?", (client_id,)
    ).fetchone()
    return None if not row else {
        "id": row[0], "proyecto": row[1], "organizacion": row[2],
        "contacto": row[3], "retencion_meses": row[4], "notas": row[5],
    }


def list_clients(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT id, proyecto, organizacion, contacto FROM clients ORDER BY proyecto"
    ).fetchall()
    return [{"id": r[0], "proyecto": r[1], "organizacion": r[2], "contacto": r[3]}
            for r in rows]


def update_client(conn, client_id: int, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    vals = list(fields.values()) + [_now_iso(), client_id]
    conn.execute(
        f"UPDATE clients SET {cols}, updated_at=? WHERE id=?", vals
    )
    conn.commit()


def delete_client(conn, client_id: int) -> None:
    """Cascade borra targets + tokens. central_events quedan huerfanos
    (preservamos audit)."""
    conn.execute("DELETE FROM clients WHERE id=?", (client_id,))
    conn.commit()


def apply_heartbeat(conn, payload: dict, *, token_id: int, client_id: int,
                    src_ip: str | None) -> HeartbeatResult:
    """Inserta event + upsert target. Una sola transaccion.
    Idempotente por event_id. Llamar solo despues de validar el payload."""
    eid = payload["event_id"]
    op = payload["operation"]["op"]
    status = payload["operation"]["status"]
    target = payload["target"]
    snap = payload.get("snapshot") or {}
    totals = payload.get("totals") or {}
    host_meta = payload.get("host_meta") or {}
    now = _now_iso()
    payload_blob = json.dumps(payload, separators=(",", ":"))

    try:
        conn.execute("BEGIN")

        # 1) Upsert target
        cat, sub, label = target["category"], target["subkey"], target["label"]
        cur = conn.execute(
            "SELECT id, total_size_bytes, count_files FROM targets "
            "WHERE client_id=? AND category=? AND subkey=? AND label=?",
            (client_id, cat, sub, label),
        )
        row = cur.fetchone()
        if row is None:
            cur = conn.execute(
                "INSERT INTO targets "
                "(client_id, category, subkey, label, entorno, pais, "
                " last_exec_ts, last_exec_status, last_size_bytes, "
                " total_size_bytes, count_files, oldest_backup_ts, newest_backup_ts, "
                " last_heartbeat_ts, snapctl_version, rclone_version, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (client_id, cat, sub, label,
                 (payload.get("client") or {}).get("entorno"),
                 (payload.get("client") or {}).get("pais"),
                 payload["operation"].get("started_at"), status,
                 snap.get("size_bytes"),
                 totals.get("size_bytes"), totals.get("count_files"),
                 totals.get("oldest_ts"), totals.get("newest_ts"),
                 now, host_meta.get("snapctl_version"),
                 host_meta.get("rclone_version"), now),
            )
            target_id = cur.lastrowid
            target_created = True
        else:
            target_id = row[0]
            conn.execute(
                "UPDATE targets SET "
                " last_exec_ts=COALESCE(?,last_exec_ts), "
                " last_exec_status=?, "
                " last_size_bytes=COALESCE(?,last_size_bytes), "
                " total_size_bytes=COALESCE(?,total_size_bytes), "
                " count_files=COALESCE(?,count_files), "
                " oldest_backup_ts=COALESCE(?,oldest_backup_ts), "
                " newest_backup_ts=COALESCE(?,newest_backup_ts), "
                " last_heartbeat_ts=?, "
                " snapctl_version=COALESCE(?,snapctl_version), "
                " rclone_version=COALESCE(?,rclone_version) "
                "WHERE id=?",
                (payload["operation"].get("started_at"), status,
                 snap.get("size_bytes"),
                 totals.get("size_bytes"), totals.get("count_files"),
                 totals.get("oldest_ts"), totals.get("newest_ts"),
                 now, host_meta.get("snapctl_version"),
                 host_meta.get("rclone_version"), target_id),
            )
            target_created = False

        # 2) Insert event (UNIQUE event_id -> silent on conflict)
        try:
            conn.execute(
                "INSERT INTO central_events "
                "(event_id, received_at, token_id, client_id, target_id, "
                " op, status, payload_json, src_ip) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (eid, now, token_id, client_id, target_id, op, status,
                 payload_blob, src_ip),
            )
            event_inserted = True
        except sqlite3.IntegrityError:
            event_inserted = False

        # Capture prev total_size_bytes BEFORE commit for shrink detection
        prev_total = row[1] if row is not None else None

        conn.commit()
    except Exception:
        conn.rollback()
        raise

    # Sub-D alerts: evaluate + fire + dispatch + auto-resolve.
    # Best-effort: alert failure must not break the heartbeat ack.
    try:
        from backend.config import Config as _Cfg
        from .alerts import rules as _rules
        from .alerts import store as _astore
        from .alerts import dispatch as _adispatch

        findings = _rules.evaluate_heartbeat(
            payload, prev_size_bytes=prev_total,
            thresholds={"shrink_pct": _Cfg.ALERTS_SHRINK_PCT},
        )
        if findings:
            client_row = get_client(conn, client_id) or {}
            target_dict = {"id": target_id, "category": cat,
                           "subkey": sub, "label": label}
            for f in findings:
                fired_alert = _astore.fire(
                    conn, type_=f["type"], client_id=client_id,
                    target_id=target_id, severity=f["severity"],
                    detail=f["detail"],
                )
                if not fired_alert.get("notified_at"):
                    _adispatch.notify(
                        conn, alert=fired_alert, client=client_row,
                        target=target_dict,
                    )
        for type_to_resolve in _rules.resolves_keys(payload):
            _astore.resolve_active_by_key(
                conn, type_=type_to_resolve, client_id=client_id,
                target_id=target_id,
            )
    except Exception:
        log = __import__("logging").getLogger(__name__)
        log.exception("alerts: evaluation failed (heartbeat still applied)")

    return HeartbeatResult(
        event_inserted=event_inserted,
        target_created=target_created,
        target_id=target_id,
    )


def list_targets_by_client(conn, client_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT id, category, subkey, label, last_exec_ts, last_exec_status, "
        "       last_size_bytes, total_size_bytes, count_files, last_heartbeat_ts "
        "FROM targets WHERE client_id=? ORDER BY category, subkey, label",
        (client_id,),
    ).fetchall()
    keys = ("id", "category", "subkey", "label", "last_exec_ts", "last_exec_status",
            "last_size_bytes", "total_size_bytes", "count_files", "last_heartbeat_ts")
    return [dict(zip(keys, r)) for r in rows]


def list_events(conn, *, target_id: int | None = None, client_id: int | None = None,
                since: str | None = None, limit: int = 50) -> list[dict]:
    sql = ("SELECT id, event_id, received_at, op, status, src_ip "
           "FROM central_events WHERE 1=1 ")
    params: list = []
    if target_id is not None:
        sql += "AND target_id=? "
        params.append(target_id)
    if client_id is not None:
        sql += "AND client_id=? "
        params.append(client_id)
    if since is not None:
        sql += "AND received_at >= ? "
        params.append(since)
    sql += "ORDER BY received_at DESC LIMIT ?"
    params.append(int(limit))
    rows = conn.execute(sql, params).fetchall()
    keys = ("id", "event_id", "received_at", "op", "status", "src_ip")
    return [dict(zip(keys, r)) for r in rows]


def dashboard_summary(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT c.id, c.proyecto, c.organizacion, "
        "       COUNT(t.id) AS targets_count, "
        "       COALESCE(SUM(t.total_size_bytes),0) AS total_bytes, "
        "       MAX(t.last_exec_ts) AS last_exec_ts, "
        "       SUM(CASE t.last_exec_status WHEN 'fail' THEN 1 ELSE 0 END) AS failed_targets, "
        "       MIN(t.last_heartbeat_ts) AS oldest_heartbeat "
        "FROM clients c LEFT JOIN targets t ON t.client_id = c.id "
        "GROUP BY c.id ORDER BY c.proyecto"
    ).fetchall()
    keys = ("client_id", "proyecto", "organizacion", "targets_count",
            "total_bytes", "last_exec_ts", "failed_targets", "oldest_heartbeat")
    return [dict(zip(keys, r)) for r in rows]
