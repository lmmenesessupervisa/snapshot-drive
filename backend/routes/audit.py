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

from flask import Blueprint, abort, current_app, g, jsonify, redirect, render_template, request

from ..config import Config
from ..services.audit import AuditError, AuditService, client_to_dict
from ..services.audit_tree import AuditTreeError, AuditTreeService

log = logging.getLogger(__name__)

audit_bp = Blueprint("audit", __name__, url_prefix="/audit")

_service: AuditService | None = None
_tree_service: AuditTreeService | None = None

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


def _tree_svc() -> AuditTreeService:
    global _tree_service
    if _tree_service is None:
        _tree_service = AuditTreeService(
            conn=current_app.config["DB_CONN"],
            rclone_bin=Config.RCLONE_BIN,
            rclone_config=Config.RCLONE_CONFIG,
            remote=Config.RCLONE_REMOTE,
            remote_path=Config.AUDIT_REMOTE_PATH,
            shrink_pct=Config.ALERTS_SHRINK_PCT,
        )
    else:
        # Mantén el threshold sincronizado con cualquier cambio en runtime
        # (vía /api/central/config o /api/alerts/config). Es un int, así que
        # la asignación es atómica.
        _tree_service.shrink_pct = Config.ALERTS_SHRINK_PCT
    return _tree_service


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
    # Pasamos contexto suficiente para que la UI elija qué tab mostrar:
    # central → solo "Por proyecto"; client → solo "Mis backups".
    local_filter = {
        "proyecto": Config.BACKUP_PROYECTO or "",
        "entorno":  Config.BACKUP_ENTORNO  or "",
        "pais":     Config.BACKUP_PAIS     or "",
        "label":    Config.BACKUP_NOMBRE   or _local_hostname(),
    }
    return render_template(
        "audit.html",
        page="audit",
        deploy_mode=Config.MODE,
        local_filter=local_filter,
    )


def _local_hostname() -> str:
    import socket
    return socket.gethostname().split(".", 1)[0]


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
        last_scan=_tree_svc().last_scan(),
    )


@audit_bp.post("/api/refresh")
@require_role
def api_refresh():
    """Gatilla un scan completo de Drive → reescribe drive_inventory.

    Antes era un no-op (solo invalidaba el caché en memoria); ahora hace
    el trabajo. Devuelve metadata del scan para que la UI muestre la
    "Última actualización" real.
    """
    _svc().invalidate_cache()
    try:
        scan = _tree_svc().refresh(triggered_by="manual")
    except AuditTreeError as e:
        return jsonify(ok=False, error=str(e)), 502
    return jsonify(ok=True, scan=scan)


@audit_bp.get("/api/local")
@require_role
def api_local():
    """Vista de los backups del cliente LOCAL (este host).

    Filtra por (BACKUP_PROYECTO, BACKUP_ENTORNO, BACKUP_PAIS, BACKUP_NOMBRE).
    Solo válido en MODE=client — un central no respalda nada propio. Si la
    taxonomía no está configurada todavía, devolvemos 200 con `summary`
    vacío para que la UI muestre un estado "configura primero" en vez de un
    error rojo.
    """
    if Config.MODE != "client":
        return jsonify(ok=False, error="solo disponible en MODE=client"), 404

    proyecto = Config.BACKUP_PROYECTO or ""
    entorno  = Config.BACKUP_ENTORNO  or ""
    pais     = Config.BACKUP_PAIS     or ""
    label    = Config.BACKUP_NOMBRE   or _local_hostname()

    # Si falta cualquiera de los 3 ejes de la taxonomía, no podemos filtrar:
    # devolvemos un payload "incompleto" para que la UI prompte al usuario.
    if not (proyecto and entorno and pais and label):
        return jsonify(ok=True,
                       configured=False,
                       filter={"proyecto": proyecto, "entorno": entorno,
                               "pais": pais, "label": label},
                       summary={"files": 0, "size_bytes": 0, "shrunk": 0,
                                "system_files": 0, "system_size": 0,
                                "db_count": 0, "db_files": 0, "db_size": 0,
                                "encrypted_files": 0, "last_backup_ts": None,
                                "shrink_pct_threshold": _tree_svc().shrink_pct},
                       system=None, databases=[])

    force = request.args.get("force") == "1"
    try:
        view = _tree_svc().build_local_view(
            proyecto=proyecto, entorno=entorno, pais=pais,
            label=label, force=force,
        )
    except AuditTreeError as e:
        return jsonify(ok=False, error=str(e)), 502
    return jsonify(ok=True, configured=True, **view)


@audit_bp.get("/api/tree")
@require_role
def api_tree():
    """Vista jerárquica por proyecto/entorno/país/cliente/tipo-de-backup.

    Además del árbol, intenta enriquecer cada cliente con su estado de
    servicio (`service`) leído del shadow `_status/<host>.json` que cada
    snapctl escribe al terminar una operación. Esto permite distinguir un
    cliente que sigue reportando heartbeats pero cuyo último backup quedó
    "viejo" en Drive (alive + stale_backup) de uno que se cayó por completo
    (silent / unreported).
    """
    force = request.args.get("force") == "1"
    try:
        tree = _tree_svc().build_tree(force=force)
    except AuditTreeError as e:
        return jsonify(ok=False, error=str(e)), 502

    # Mejor esfuerzo: si fallar la lectura de _status/, devolvemos el árbol
    # tal cual y el frontend se queda sin chip de servicio.
    service_by_host: dict[str, dict] = {}
    try:
        clients = _svc().get_all(force_refresh=force)
    except AuditError as e:
        log.warning("audit status read falló (tree sigue): %s", e)
        clients = []
    for c in clients:
        service_by_host[c.host] = {
            "health": c.health,            # ok|fail|silent|running|unreported|unknown
            "silent_hours": (round(c.silent_hours, 2)
                             if c.silent_hours is not None else None),
            "updated_ts": c.updated_ts,
            "last_op": (c.last or {}).get("op"),
            "last_status": (c.last or {}).get("status"),
        }

    # Inyecta el campo `service` en cada cliente del árbol.
    for p in tree.get("proyectos", []):
        for r in p.get("regions", []):
            for cli in r.get("clients", []):
                svc_info = service_by_host.get(cli["label"])
                if svc_info is None:
                    cli["service"] = {"health": "unreported"}
                else:
                    cli["service"] = svc_info

    tree["summary"]["service_silence_threshold_h"] = _svc().silence_threshold_h
    return jsonify(ok=True, **tree)
