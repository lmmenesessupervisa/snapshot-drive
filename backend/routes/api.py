"""Endpoints REST del backend.

Todas las respuestas son JSON estructurado:
{
  "ok": bool,
  "data": <obj|null>,
  "error": <str|null>
}
"""
import logging
from flask import Blueprint, current_app, jsonify, request

from ..config import Config
from ..services.drive_oauth import (
    OAuthError,
    OAuthPending,
    build_rclone_token_json,
    poll_device_token,
    start_device_flow,
)
from ..services.snapctl import SnapctlError
from ..services import scheduler as sched
from ..services import sysconfig

log = logging.getLogger("api")
api_bp = Blueprint("api", __name__, url_prefix="/api")


def _ok(data=None, status=200):
    return jsonify(ok=True, data=data, error=None), status


def _err(msg: str, status: int = 400, detail: str = ""):
    return jsonify(ok=False, data=None, error=msg, detail=detail), status


def _svc():
    return current_app.config["SNAPCTL_SVC"]


def _db():
    return current_app.config["DB"]


# ---------- Snapshots ----------
@api_bp.get("/snapshots")
def list_snapshots():
    fresh = request.args.get("fresh") in ("1", "true")
    try:
        return _ok(_svc().list_snapshots(fresh=fresh))
    except SnapctlError as e:
        return _err(str(e), 500, e.stderr)


@api_bp.post("/snapshots")
def create_snapshot():
    payload = request.get_json(silent=True) or {}
    tag = (payload.get("tag") or "manual").strip()
    try:
        res = _svc().create(tag=tag)
        _db().audit("api", "create", f"tag={tag}")
        return _ok(res, 201)
    except SnapctlError as e:
        return _err(str(e), 500, e.stderr)


@api_bp.get("/estimate")
def estimate_snapshot():
    fresh = request.args.get("fresh") in ("1", "true")
    try:
        return _ok(_svc().estimate(fresh=fresh))
    except SnapctlError as e:
        return _err(str(e), 500, e.stderr)


@api_bp.delete("/snapshots/<sid>")
def delete_snapshot(sid: str):
    try:
        res = _svc().delete(sid)
        _db().audit("api", "delete", f"id={sid}")
        return _ok(res)
    except SnapctlError as e:
        return _err(str(e), 400 if e.rc == 2 else 500, e.stderr)


# ---------- Restore ----------
@api_bp.post("/restore")
def restore():
    payload = request.get_json(silent=True) or {}
    sid = payload.get("id", "").strip()
    target = payload.get("target", "").strip()
    include = (payload.get("include") or "").strip() or None
    if not sid or not target:
        return _err("Parámetros 'id' y 'target' son obligatorios", 400)
    try:
        res = _svc().restore(sid, target, include=include)
        _db().audit("api", "restore", f"id={sid} target={target} include={include or ''}")
        return _ok(res)
    except SnapctlError as e:
        return _err(str(e), 400 if e.rc == 2 else 500, e.stderr)


# ---------- Operaciones de mantenimiento ----------
@api_bp.post("/prune")
def prune():
    try:
        res = _svc().prune()
        _db().audit("api", "prune", "")
        return _ok(res)
    except SnapctlError as e:
        return _err(str(e), 500, e.stderr)


@api_bp.post("/cleanup-local")
def cleanup_local():
    try:
        res = _svc().cleanup_local()
        _db().audit("api", "cleanup-local", "")
        return _ok(res)
    except SnapctlError as e:
        return _err(str(e), 500, e.stderr)


@api_bp.post("/check")
def check():
    try:
        res = _svc().check()
        return _ok(res)
    except SnapctlError as e:
        return _err(str(e), 500, e.stderr)


@api_bp.post("/unlock")
def unlock():
    try:
        res = _svc().unlock()
        _db().audit("api", "unlock", "")
        return _ok(res)
    except SnapctlError as e:
        return _err(str(e), 500, e.stderr)


@api_bp.post("/sync")
def sync():
    try:
        res = _svc().sync()
        _db().audit("api", "sync", "")
        return _ok(res)
    except SnapctlError as e:
        return _err(str(e), 500, e.stderr)


@api_bp.post("/reconcile")
def reconcile():
    try:
        res = _svc().reconcile()
        _db().audit("api", "reconcile", "")
        return _ok(res)
    except SnapctlError as e:
        return _err(str(e), 500, e.stderr)


# ---------- Estado / Logs / Jobs ----------
@api_bp.get("/status")
def status():
    # Query string:
    #   ?deep=1   → consulta Drive (costoso, ~15s primera vez)
    #   ?fresh=1  → bypass cache
    fast  = request.args.get("deep") not in ("1", "true")
    fresh = request.args.get("fresh") in ("1", "true")
    try:
        return _ok(_svc().status(fast=fast, fresh=fresh))
    except SnapctlError as e:
        return _err(str(e), 500, e.stderr)


@api_bp.get("/logs")
def logs():
    n = request.args.get("lines", default=200, type=int)
    return _ok({"lines": _svc().logs(lines=n)})


@api_bp.get("/jobs")
def jobs():
    limit = request.args.get("limit", default=50, type=int)
    return _ok(_db().job_list(limit=limit))


@api_bp.get("/jobs/<int:jid>")
def job_detail(jid: int):
    j = _db().job_get(jid)
    if not j:
        return _err("job no encontrado", 404)
    return _ok(j)


@api_bp.get("/health")
def health():
    return _ok({"status": "up"})


# ---------- Programación (timers systemd) ----------
@api_bp.get("/schedule")
def schedule_list():
    try:
        return _ok({"units": sched.list_schedules()})
    except sched.ScheduleError as e:
        return _err(str(e), 500)


@api_bp.get("/schedule/<unit>")
def schedule_get(unit: str):
    try:
        return _ok(sched.get_schedule(unit).to_dict())
    except sched.ScheduleError as e:
        return _err(str(e), 400)


@api_bp.post("/schedule/<unit>")
def schedule_set(unit: str):
    payload = request.get_json(silent=True) or {}
    try:
        result = sched.set_schedule(
            unit,
            kind=str(payload.get("kind", "daily")),
            time=str(payload.get("time", "")),
            weekday=str(payload.get("weekday", "Mon")),
            day=int(payload.get("day", 1) or 1),
            oncalendar=str(payload.get("oncalendar", "")),
            delay=str(payload.get("delay", "")),
            enabled=bool(payload.get("enabled", True)),
        )
        _db().audit("api", "schedule-set",
                    f"unit={unit} oncal={result.oncalendar} enabled={result.enabled}")
        return _ok(result.to_dict())
    except (sched.ScheduleError, ValueError) as e:
        return _err(str(e), 400)


# ---------- Retención ----------
@api_bp.get("/retention")
def retention_get():
    return _ok(sched.get_retention())


@api_bp.post("/retention")
def retention_set():
    payload = request.get_json(silent=True) or {}
    try:
        result = sched.set_retention(payload)
        _db().audit("api", "retention-set",
                    " ".join(f"{k}={v}" for k, v in result.items()))
        return _ok(result)
    except sched.ScheduleError as e:
        return _err(str(e), 400)


# ---------- Configuración editable (rutas + destino Drive) ----------
@api_bp.get("/config")
def config_get():
    return _ok(sysconfig.get_config())


@api_bp.post("/config")
def config_set():
    payload = request.get_json(silent=True) or {}
    try:
        res = sysconfig.set_config(payload)
        summary = []
        if "backup_paths" in payload:
            summary.append(f"paths={res['backup_paths']}")
        if "rclone_remote_path" in payload:
            summary.append(f"remote_path={res['rclone_remote_path']}")
        _db().audit("api", "config-set", " ".join(summary)[:500])
        return _ok(res)
    except sysconfig.ConfigError as e:
        return _err(str(e), 400)


# ---------- Google Drive (rclone) ----------
@api_bp.get("/drive/status")
def drive_status():
    try:
        return _ok(_svc().drive_status())
    except SnapctlError as e:
        return _err(str(e), 500, e.stderr)


@api_bp.post("/drive/link")
def drive_link():
    payload = request.get_json(silent=True) or {}
    token = (payload.get("token") or "").strip()
    team_drive = (payload.get("team_drive") or "").strip()
    if not token:
        return _err("Campo 'token' requerido", 400)
    try:
        res = _svc().drive_link(token, team_drive=team_drive)
        _db().audit("api", "drive-link", f"team_drive={team_drive or '-'}")
        return _ok(res)
    except SnapctlError as e:
        return _err(str(e), 400 if e.rc == 2 else 500, e.stderr)


@api_bp.post("/drive/unlink")
def drive_unlink():
    try:
        res = _svc().drive_unlink()
        _db().audit("api", "drive-unlink", "")
        return _ok(res)
    except SnapctlError as e:
        return _err(str(e), 500, e.stderr)


@api_bp.get("/drive/shared")
def drive_shared():
    try:
        return _ok(_svc().drive_shared_list())
    except SnapctlError as e:
        return _err(str(e), 500, e.stderr)


@api_bp.post("/drive/target")
def drive_target():
    payload = request.get_json(silent=True) or {}
    kind = (payload.get("type") or "").strip()
    shared_id = (payload.get("id") or "").strip() or None
    try:
        res = _svc().drive_set_target(kind, shared_id=shared_id)
        _db().audit("api", "drive-target", f"type={kind} id={shared_id or '-'}")
        return _ok(res)
    except SnapctlError as e:
        return _err(str(e), 400 if e.rc == 2 else 500, e.stderr)


# ---------- Google Drive · OAuth Device Flow ----------
@api_bp.post("/drive/oauth/device/start")
def drive_oauth_device_start():
    """Inicia el Device Flow: pide a Google un user_code + verification_url.

    El frontend muestra ese código al cliente para que lo introduzca en
    `google.com/device` desde cualquier navegador (móvil, laptop, etc).
    """
    try:
        dc = start_device_flow(Config.GOOGLE_CLIENT_ID, Config.GOOGLE_OAUTH_SCOPE)
        _db().audit("api", "drive-oauth-start", "")
        return _ok(dc.to_public_dict())
    except OAuthError as e:
        return _err(str(e), 400)


@api_bp.post("/drive/oauth/device/poll")
def drive_oauth_device_poll():
    """Consulta si el usuario ya autorizó. Al éxito, vincula rclone."""
    payload = request.get_json(silent=True) or {}
    device_code = (payload.get("device_code") or "").strip()
    team_drive = (payload.get("team_drive") or "").strip()
    if not device_code:
        return _err("Falta device_code", 400)
    try:
        token_payload = poll_device_token(
            Config.GOOGLE_CLIENT_ID, Config.GOOGLE_CLIENT_SECRET, device_code
        )
    except OAuthPending as e:
        # 202 Accepted: operación en curso, el cliente debe seguir haciendo polling.
        return jsonify(
            ok=True,
            data={"status": "pending", "slow_down": e.slow_down},
            error=None,
        ), 202
    except OAuthError as e:
        return _err(str(e), 400)

    try:
        rclone_token = build_rclone_token_json(token_payload)
    except OAuthError as e:
        return _err(str(e), 400)

    # Reutilizamos el mismo camino que el flujo viejo (paste de token).
    try:
        _svc().drive_link(rclone_token, team_drive=team_drive)
        _db().audit("api", "drive-link", f"via=oauth team_drive={team_drive or '-'}")
    except SnapctlError as e:
        return _err(str(e), 400 if e.rc == 2 else 500, e.stderr)
    return _ok({"status": "linked"})
