"""Blueprint /audit — vista agregada de snapshots por cliente.

Gate dual:
1. Config.AUDIT_ENABLED debe ser True (SNAPSHOT_AUDIT_VIEWER=1 en local.conf).
   Si no, todas las rutas devuelven 404 para no filtrar ni siquiera que
   existe la vista.
2. Login con una password (Config.AUDIT_PASSWORD) — constante, constant-time
   compare, en cookie firmada por Flask (SECRET_KEY).

La vista está pensada para correr en UNA instalación separada (la máquina
ops del operador) que tiene rclone.conf apuntando al shared Drive con
permisos para leer /snapshots/ de todos los clientes.
"""
from __future__ import annotations

import hmac
import logging
import time
from functools import wraps

from flask import (
    Blueprint,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from ..config import Config
from ..services.audit import AuditError, AuditService, client_to_dict

log = logging.getLogger(__name__)

audit_bp = Blueprint("audit", __name__, url_prefix="/audit")

_service: AuditService | None = None


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
    # Debe haber una password configurada; si no, la vista es inalcanzable.
    if not Config.AUDIT_PASSWORD:
        log.warning("AUDIT_ENABLED=1 pero AUDIT_PASSWORD vacía — bloqueando acceso.")
        abort(404)


def require_auth(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("audit_auth"):
            if request.path.startswith("/audit/api/") or request.is_json:
                return jsonify(ok=False, error="auth requerida"), 401
            return redirect(url_for("audit.login"))
        return view(*args, **kwargs)
    return wrapped


# ----------------- login flow -----------------
@audit_bp.get("/login")
def login():
    if session.get("audit_auth"):
        return redirect(url_for("audit.index"))
    return render_template("audit_login.html", error=None)


@audit_bp.post("/login")
def login_submit():
    password = (request.form.get("password") or "").strip()
    expected = (Config.AUDIT_PASSWORD or "").strip()
    # constant-time compare para evitar timing attacks
    if expected and hmac.compare_digest(password, expected):
        session.permanent = True
        session["audit_auth"] = True
        session["audit_login_ts"] = int(time.time())
        return redirect(url_for("audit.index"))
    # sleep artificial para frenar bruteforce (9 dígitos ~ débil; cada intento tarda ≥1s)
    time.sleep(1.0)
    return render_template(
        "audit_login.html",
        error="Contraseña incorrecta.",
    ), 401


@audit_bp.get("/logout")
def logout():
    session.pop("audit_auth", None)
    session.pop("audit_login_ts", None)
    return redirect(url_for("audit.login"))


# ----------------- dashboard -----------------
@audit_bp.get("/")
@require_auth
def index():
    return render_template("audit.html", page="audit")


@audit_bp.get("/api/status")
@require_auth
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
@require_auth
def api_refresh():
    _svc().invalidate_cache()
    return jsonify(ok=True)
