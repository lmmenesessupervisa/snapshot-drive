"""Endpoints REST del backend.

El proyecto se reestructuró para enfocarse exclusivamente en el backup
mensual cold-storage (archives .tar.zst[.enc] subidos a Drive con ruta
taxonómica). Los endpoints legacy del motor restic quedaron retirados.

Formato uniforme:
  { "ok": bool, "data": <obj|null>, "error": <str|null> }
"""
import logging
from flask import Blueprint, current_app, jsonify, request

from ..auth.decorators import require_login, require_role, require_any_role
from ..config import Config
from ..services.drive_oauth import (
    OAuthError,
    OAuthPending,
    build_rclone_token_json,
    poll_device_token,
    start_device_flow,
)
from ..services.snapctl import SnapctlError
from ..services import sysconfig
from ..services import archive_config, archive_ops
from ..services.archive_config import ArchiveConfigError
from ..services.archive_ops import ArchiveOpError

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


# ---------- Health / Logs / Jobs ----------
@api_bp.get("/health")
def health():
    return _ok({"status": "up"})


@api_bp.get("/logs")
@require_any_role("admin", "operator")
def logs():
    n = request.args.get("lines", default=200, type=int)
    return _ok({"lines": _svc().logs(lines=n)})


@api_bp.get("/jobs")
@require_login
def jobs():
    limit = request.args.get("limit", default=50, type=int)
    return _ok(_db().job_list(limit=limit))


@api_bp.get("/jobs/<int:jid>")
@require_login
def job_detail(jid: int):
    j = _db().job_get(jid)
    if not j:
        return _err("job no encontrado", 404)
    return _ok(j)


# ---------- Configuración de paths a respaldar ----------
@api_bp.get("/config")
@require_any_role("admin", "operator")
def config_get():
    return _ok(sysconfig.get_config())


@api_bp.post("/config")
@require_any_role("admin", "operator")
def config_set():
    payload = request.get_json(silent=True) or {}
    try:
        res = sysconfig.set_config(payload)
        _db().audit("api", "config-set", f"paths={res.get('backup_paths','')}"[:500])
        return _ok(res)
    except sysconfig.ConfigError as e:
        return _err(str(e), 400)


# ---------- Google Drive (vinculación rclone) ----------
@api_bp.get("/drive/status")
@require_login
def drive_status():
    try:
        return _ok(_svc().drive_status())
    except SnapctlError as e:
        return _err(str(e), 500, e.stderr)


@api_bp.post("/drive/link")
@require_role("admin")
def drive_link():
    payload = request.get_json(silent=True) or {}
    token = (payload.get("token") or "").strip()
    team_drive = (payload.get("team_drive") or "").strip()
    if not token:
        return _err("Falta el token rclone", 400)
    try:
        _svc().drive_link(token, team_drive=team_drive)
        _db().audit("api", "drive-link", f"team_drive={team_drive or '-'}")
        return _ok({"status": "linked"})
    except SnapctlError as e:
        return _err(str(e), 400 if e.rc == 2 else 500, e.stderr)


@api_bp.post("/drive/unlink")
@require_role("admin")
def drive_unlink():
    try:
        _svc().drive_unlink()
        _db().audit("api", "drive-unlink", "")
        return _ok({"status": "unlinked"})
    except SnapctlError as e:
        return _err(str(e), 500, e.stderr)


@api_bp.get("/drive/shared")
@require_role("admin")
def drive_shared_list():
    try:
        return _ok(_svc().drive_shared_list())
    except SnapctlError as e:
        return _err(str(e), 500, e.stderr)


@api_bp.post("/drive/target")
@require_role("admin")
def drive_set_target():
    payload = request.get_json(silent=True) or {}
    kind = (payload.get("type") or "").strip()
    ident = (payload.get("id") or "").strip()
    if kind not in ("personal", "shared"):
        return _err("type debe ser 'personal' o 'shared'", 400)
    if kind == "shared" and not ident:
        return _err("Falta el id de la unidad compartida", 400)
    try:
        _svc().drive_set_target(kind, ident)
        _db().audit("api", "drive-target", f"type={kind} id={ident or '-'}")
        return _ok({"status": "ok"})
    except SnapctlError as e:
        return _err(str(e), 400 if e.rc == 2 else 500, e.stderr)


# ---------- OAuth Device Flow ----------
@api_bp.post("/drive/oauth/device/start")
@require_role("admin")
def oauth_device_start():
    try:
        dc = start_device_flow(Config.GOOGLE_CLIENT_ID, Config.GOOGLE_OAUTH_SCOPE)
        return _ok(dc.to_public_dict())
    except OAuthError as e:
        return _err(str(e), 400)


@api_bp.post("/drive/oauth/device/poll")
@require_role("admin")
def oauth_device_poll():
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

    try:
        _svc().drive_link(rclone_token, team_drive=team_drive)
        _db().audit("api", "drive-link", f"via=oauth team_drive={team_drive or '-'}")
    except SnapctlError as e:
        return _err(str(e), 400 if e.rc == 2 else 500, e.stderr)
    return _ok({"status": "linked"})


# ---------- Archive: configuración de taxonomía + password ----------
@api_bp.get("/archive/config")
@require_login
def archive_config_get():
    return _ok(archive_config.get_config())


@api_bp.post("/archive/config")
@require_role("admin")
def archive_config_set():
    payload = request.get_json(silent=True) or {}
    try:
        res = archive_config.set_taxonomy(payload)
        _db().audit(
            "api", "archive-config",
            f"proyecto={res['proyecto']} entorno={res['entorno']} "
            f"pais={res['pais']} nombre={res['nombre']}",
        )
        return _ok(res)
    except ArchiveConfigError as e:
        return _err(str(e), 400)


@api_bp.post("/archive/password")
@require_role("admin")
def archive_password_set():
    payload = request.get_json(silent=True) or {}
    pw      = payload.get("password") or ""
    confirm = payload.get("confirm") or ""
    try:
        res = archive_config.set_password(pw, confirm)
        _db().audit("api", "archive-password", "set" if pw else "cleared")
        return _ok(res)
    except ArchiveConfigError as e:
        return _err(str(e), 400)


@api_bp.delete("/archive/password")
@require_role("admin")
def archive_password_clear():
    try:
        res = archive_config.clear_password()
        _db().audit("api", "archive-password", "cleared")
        return _ok(res)
    except ArchiveConfigError as e:
        return _err(str(e), 400)


# ---------- DB backups (sub-E) ----------
@api_bp.get("/db-archive/config")
@require_role("admin")
def db_archive_config_get():
    return _ok(archive_config.get_db_config())


@api_bp.post("/db-archive/config")
@require_role("admin")
def db_archive_config_set():
    payload = request.get_json(silent=True) or {}
    try:
        res = archive_config.set_db_config(payload)
        _db().audit("api", "db-archive-config", f"targets={res['targets']!r}")
        return _ok(res)
    except ArchiveConfigError as e:
        return _err(str(e), 400)


# ---------- Crypto / age recipients (sub-F) ----------
@api_bp.get("/crypto/config")
@require_role("admin")
def crypto_config_get():
    return _ok(archive_config.get_crypto_config())


@api_bp.post("/crypto/config")
@require_role("admin")
def crypto_config_set():
    payload = request.get_json(silent=True) or {}
    try:
        res = archive_config.set_recipients(payload.get("recipients") or "")
        _db().audit("api", "crypto-recipients", f"count={res['recipients_count']}")
        return _ok(res)
    except ArchiveConfigError as e:
        return _err(str(e), 400)


@api_bp.post("/crypto/keygen")
@require_role("admin")
def crypto_keygen():
    try:
        res = archive_config.generate_keypair()
        _db().audit("api", "crypto-keygen", "generated (not persisted)")
        return _ok(res)
    except ArchiveConfigError as e:
        return _err(str(e), 500)


# ---------- Archive: operaciones ----------
@api_bp.get("/archive/list")
@require_any_role("admin", "operator")
def archive_list():
    force = request.args.get("force") == "1"
    try:
        return _ok(archive_ops.list_archives(force=force))
    except ArchiveOpError as e:
        return _err(str(e), 400)


@api_bp.get("/archive/summary")
@require_login
def archive_summary():
    force = request.args.get("force") == "1"
    try:
        return _ok(archive_ops.summary(force=force))
    except ArchiveOpError as e:
        return _err(str(e), 400)


@api_bp.post("/archive/create")
@require_any_role("admin", "operator")
def archive_create():
    try:
        res = archive_ops.create_archive()
        last = res.get("last") or {}
        _db().audit(
            "api", "archive-create",
            f"dur={res['duration_s']}s path={last.get('path','-')}",
        )
        return _ok(res)
    except ArchiveOpError as e:
        return _err(str(e), 500)


@api_bp.post("/archive/restore")
@require_any_role("admin", "operator")
def archive_restore():
    payload = request.get_json(silent=True) or {}
    try:
        res = archive_ops.restore_archive(
            payload.get("path") or "",
            payload.get("target") or "",
        )
        _db().audit(
            "api", "archive-restore",
            f"path={res['path']} target={res['target']} dur={res['duration_s']}s",
        )
        return _ok(res)
    except ArchiveOpError as e:
        return _err(str(e), 400)


@api_bp.post("/archive/delete")
@require_role("admin")
def archive_delete():
    payload = request.get_json(silent=True) or {}
    try:
        res = archive_ops.delete_archive(payload.get("path") or "")
        _db().audit("api", "archive-delete", f"path={res['path']}")
        return _ok(res)
    except ArchiveOpError as e:
        return _err(str(e), 400)
