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


@alerts_bp.get("/config")
@require_central_perm("central.dashboard:view")
def get_config():
    return _ok({
        "no_heartbeat_hours": Config.ALERTS_NO_HEARTBEAT_HOURS,
        "shrink_pct": Config.ALERTS_SHRINK_PCT,
        "email": Config.ALERTS_EMAIL,
        "webhook_set": bool(Config.ALERTS_WEBHOOK),
    })


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
    actor = (g.current_user.email if getattr(g, "current_user", None)
             else "unknown")
    store.acknowledge(_db(), alert_id, actor_email=actor)
    return _ok({"id": alert_id, "resolved": True})
