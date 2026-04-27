"""HTTP sender que drena la cola local hacia el central."""
from __future__ import annotations

import logging

import requests

from backend.config import Config
from . import queue as q

log = logging.getLogger(__name__)


def _enabled() -> bool:
    return bool(Config.CENTRAL_URL and Config.CENTRAL_TOKEN)


def send_one(payload: dict) -> tuple[int, str]:
    """Devuelve (status_code, body_text). Retorna (0, error) si conn falla."""
    url = Config.CENTRAL_URL.rstrip("/") + "/api/v1/heartbeat"
    headers = {"Authorization": f"Bearer {Config.CENTRAL_TOKEN}",
               "Content-Type": "application/json"}
    try:
        r = requests.post(url, json=payload, headers=headers,
                          timeout=Config.CENTRAL_TIMEOUT_S)
        return r.status_code, (r.text or "")[:500]
    except requests.RequestException as e:
        return 0, str(e)[:500]


def drain(conn, *, limit: int = 100) -> int:
    """Procesa hasta `limit` items pendientes due. Retorna cuántos se intentaron."""
    if not _enabled():
        return 0
    items = q.fetch_due(conn, limit=limit)
    for item in items:
        code, body = send_one(item["payload"])
        if code == 200:
            q.mark_done(conn, item["event_id"])
        elif code in (400, 401, 403, 409, 410, 413):
            log.error("heartbeat %s rejected with %s: %s",
                      item["event_id"], code, body[:200])
            q.mark_dead(conn, item["event_id"], error=f"{code}: {body[:200]}")
        else:
            q.mark_failed(conn, item["event_id"], error=f"{code}: {body[:200]}")
    return len(items)


def send_now(conn, payload: dict) -> int:
    """Encola y dispara un único envío inmediato."""
    q.enqueue(conn, payload)
    if not _enabled():
        return 0
    code, body = send_one(payload)
    if code == 200:
        q.mark_done(conn, payload["event_id"])
    elif code in (400, 401, 403, 409, 410, 413):
        q.mark_dead(conn, payload["event_id"], error=f"{code}: {body[:200]}")
    elif code != 0:
        q.mark_failed(conn, payload["event_id"], error=f"{code}: {body[:200]}")
    return code
