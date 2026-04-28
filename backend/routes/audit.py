"""Blueprint /audit — vista agregada de snapshots por cliente.

Gate dual:
1. Config.AUDIT_ENABLED debe ser True (SNAPSHOT_AUDIT_VIEWER=1 en local.conf).
   Si no, todas las rutas devuelven 404 para no filtrar ni siquiera que
   existe la vista.
2. Auth principal del panel: requiere usuario logueado con rol admin o
   auditor (mismo gate que el resto de la app, vía la session de
   snapctl).

La vista está pensada para correr en UNA instalación separada (la máquina
ops del operador) que tiene rclone.conf apuntando al shared Drive con
permisos para leer /snapshots/ de todos los clientes.
"""
from __future__ import annotations

import logging
import time
from functools import wraps

from flask import Blueprint, abort, g, jsonify, redirect, render_template, request

from ..config import Config
from ..services.audit import AuditError, AuditService, client_to_dict

log = logging.getLogger(__name__)

audit_bp = Blueprint("audit", __name__, url_prefix="/audit")

_service: AuditService | None = None

_ALLOWED_ROLES = ("admin", "auditor")


def _svc() -> AuditService:
    global _service
    if _service is None:
        _service = AuditService(
            rclone_bin=Config.RCLONE_BIN,
            rclone_config=Config.RCLONE_CONFIG,
            remote=Config.RCLONE_REMOTE,
            remote_path=Config.AUDIT_REMOTE_PATH,
        )
    return _service


@audit_bp.before_request
def _enabled_gate():
    # Si la vista no está habilitada en este deploy, fingir que no existe.
    if not Config.AUDIT_ENABLED:
        abort(404)


def require_role(view):
    """Gate: usuario logueado en el panel principal con rol admin/auditor."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        u = getattr(g, "current_user", None)
        is_api = request.path.startswith("/audit/api/") or request.is_json
        if not u:
            if is_api:
                return jsonify(ok=False, error="auth requerida"), 401
            return redirect("/auth/login")
        if u.role not in _ALLOWED_ROLES:
            if is_api:
                return jsonify(ok=False, error="forbidden"), 403
            return redirect("/")
        return view(*args, **kwargs)
    return wrapped


# ----------------- dashboard -----------------
@audit_bp.get("/")
@require_role
def index():
    return render_template("audit.html", page="audit")


@audit_bp.get("/api/status")
@require_role
def api_status():
    force = request.args.get("force") == "1"
    try:
        clients = _svc().get_all(force_refresh=force)
    except AuditError as e:
        return jsonify(ok=False, error=str(e)), 502

    total = len(clients)
    by_health: dict[str, int] = {}
    for c in clients:
        by_health[c.health] = by_health.get(c.health, 0) + 1

    return jsonify(
        ok=True,
        updated_ts=int(time.time()),
        summary={
            "total": total,
            "ok": by_health.get("ok", 0),
            "fail": by_health.get("fail", 0),
            "silent": by_health.get("silent", 0),
            "running": by_health.get("running", 0),
            "unreported": by_health.get("unreported", 0),
            "unknown": by_health.get("unknown", 0),
            "silence_threshold_h": _svc().silence_threshold_h,
        },
        clients=[client_to_dict(c) for c in clients],
    )


@audit_bp.post("/api/refresh")
@require_role
def api_refresh():
    _svc().invalidate_cache()
    return jsonify(ok=True)
