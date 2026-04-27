"""Endpoints administrativos del central (humanos, sesión + RBAC)."""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from . import models as m
from . import tokens as tok
from .permissions import require_central_perm

central_admin_bp = Blueprint("central_admin", __name__, url_prefix="/api/admin")


def _db():
    return current_app.config["DB_CONN"]


def _ok(data=None):
    return jsonify(ok=True, data=data, error=None)


def _err(msg, code=400):
    return jsonify(ok=False, data=None, error=msg), code


# --- clients ---

@central_admin_bp.get("/clients")
@require_central_perm("central.clients:read")
def list_clients():
    return _ok(m.list_clients(_db()))


@central_admin_bp.get("/clients/<int:cid>")
@require_central_perm("central.clients:read")
def get_client_route(cid):
    c = m.get_client(_db(), cid)
    if not c:
        return _err("not found", 404)
    c["targets"] = m.list_targets_by_client(_db(), cid)
    c["recent_events"] = m.list_events(_db(), client_id=cid, limit=50)
    return _ok(c)


@central_admin_bp.post("/clients")
@require_central_perm("central.clients:write")
def create_client_route():
    body = request.get_json(silent=True) or {}
    proyecto = (body.get("proyecto") or "").strip()
    if not proyecto:
        return _err("proyecto required")
    try:
        cid = m.create_client(_db(),
                              proyecto=proyecto,
                              organizacion=body.get("organizacion"),
                              contacto=body.get("contacto"),
                              retencion_meses=body.get("retencion_meses"),
                              notas=body.get("notas"))
    except Exception as e:
        return _err(f"create failed: {e}", 409)
    return _ok({"id": cid, "proyecto": proyecto})


@central_admin_bp.patch("/clients/<int:cid>")
@require_central_perm("central.clients:write")
def update_client_route(cid):
    body = request.get_json(silent=True) or {}
    allowed = {k: v for k, v in body.items()
               if k in ("organizacion", "contacto", "retencion_meses", "notas")}
    m.update_client(_db(), cid, **allowed)
    return _ok({"id": cid})


@central_admin_bp.delete("/clients/<int:cid>")
@require_central_perm("central.clients:write")
def delete_client_route(cid):
    m.delete_client(_db(), cid)
    return _ok({"id": cid, "deleted": True})


# --- tokens ---

@central_admin_bp.post("/clients/<int:cid>/tokens")
@require_central_perm("central.tokens:issue")
def issue_token_route(cid):
    body = request.get_json(silent=True) or {}
    label = (body.get("label") or "").strip()
    if not label:
        return _err("label required")
    if m.get_client(_db(), cid) is None:
        return _err("client not found", 404)
    plaintext, token_id = tok.issue(_db(), cid, label=label,
                                    expires_at=body.get("expires_at"))
    return _ok({"plaintext": plaintext, "token_id": token_id, "label": label})


@central_admin_bp.delete("/tokens/<int:tid>")
@require_central_perm("central.tokens:revoke")
def revoke_token_route(tid):
    db = _db()
    row = db.execute(
        "SELECT id FROM central_tokens WHERE id=? AND revoked_at IS NULL", (tid,)
    ).fetchone()
    if not row:
        return _err("not found", 404)
    tok.revoke(db, tid)
    return _ok({"id": tid, "revoked": True})


@central_admin_bp.get("/tokens")
@require_central_perm("central.clients:read")
def list_tokens_route():
    cid = request.args.get("client_id", type=int)
    if not cid:
        return _err("client_id query param required")
    return _ok(tok.list_active(_db(), cid))


# --- events ---

@central_admin_bp.get("/events")
@require_central_perm("central.audit:view")
def list_events_route():
    target_id = request.args.get("target_id", type=int)
    client_id = request.args.get("client_id", type=int)
    since = request.args.get("since")
    limit = min(request.args.get("limit", default=50, type=int), 500)
    return _ok(m.list_events(_db(), target_id=target_id,
                             client_id=client_id, since=since, limit=limit))
