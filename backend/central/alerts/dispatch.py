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
    if not getattr(Config, "SMTP_HOST", "") or not Config.ALERTS_EMAIL:
        return False
    msg = EmailMessage()
    msg["Subject"] = (f"[snapshot-V3] {alert['severity'].upper()}: "
                      f"{alert['type']} en {client.get('proyecto', '?')}")
    msg["From"] = (getattr(Config, "SMTP_FROM", "")
                   or f"snapshot-v3@{Config.SMTP_HOST}")
    msg["To"] = Config.ALERTS_EMAIL
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
            try:
                s.starttls()
            except Exception:
                pass
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
