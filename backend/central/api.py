"""Endpoints máquina-a-máquina: heartbeat y ping."""
from __future__ import annotations

import logging

from flask import Blueprint, current_app, jsonify, request

from . import inventory as inv_mod
from . import models as m
from . import tokens as tok
from . import schema as sch
from ..config import Config

log = logging.getLogger(__name__)

central_api_bp = Blueprint("central_api", __name__, url_prefix="/api/v1")


def _db():
    return current_app.config["DB_CONN"]


def _client_meta():
    return request.remote_addr or ""


@central_api_bp.get("/ping")
def ping():
    return jsonify(ok=True, version=current_app.config.get("VERSION", "dev"))


@central_api_bp.get("/auth-check")
def auth_check():
    """Valida el Bearer token sin crear ningún central_event.

    Útil para que el cliente compruebe vinculación + auth desde su UI
    de Ajustes ("Probar conexión") sin contaminar el audit del central.
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return jsonify(ok=False, error="missing bearer token"), 401
    plaintext = auth[7:].strip()
    db = _db()
    info = tok.verify(db, plaintext)
    if info is None:
        return jsonify(ok=False, error="invalid or revoked token"), 401
    client = m.get_client(db, info.client_id)
    if client is None:
        return jsonify(ok=False, error="client gone"), 410
    return jsonify(
        ok=True,
        client={"id": client["id"], "proyecto": client["proyecto"]},
        token={"label": info.label, "scope": info.scope},
    )


@central_api_bp.post("/heartbeat")
def heartbeat():
    raw = request.get_data() or b""
    try:
        sch.check_size(raw)
    except sch.SchemaError as e:
        return jsonify(ok=False, error=str(e)), 413

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return jsonify(ok=False, error="missing bearer token"), 401
    plaintext = auth[7:].strip()
    db = _db()
    info = tok.verify(db, plaintext)
    if info is None:
        return jsonify(ok=False, error="invalid or revoked token"), 401

    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify(ok=False, error="payload must be JSON"), 400
    try:
        sch.validate_heartbeat(payload)
    except sch.SchemaError as e:
        return jsonify(ok=False, error=f"schema: {e}"), 400

    client = m.get_client(db, info.client_id)
    if client is None:
        # client borrado pero token todavía no revocado en cascada — defensivo
        return jsonify(ok=False, error="client gone"), 410
    if payload["client"]["proyecto"] != client["proyecto"]:
        return jsonify(ok=False, error="proyecto mismatch with token's client"), 409

    res = m.apply_heartbeat(db, payload, token_id=info.id,
                            client_id=info.client_id, src_ip=_client_meta())

    # Best-effort: si el cliente mandó su inventario embebido, lo
    # materializamos en drive_inventory. Fallar acá NO debe romper el
    # ack del heartbeat — el botón "Refrescar" del central siempre puede
    # rehacer el scan completo desde Drive.
    inventory_leaves = 0
    if payload.get("inventory"):
        try:
            inventory_leaves = inv_mod.apply_client_inventory(
                db, payload, shrink_pct=Config.ALERTS_SHRINK_PCT,
            )
        except Exception:
            log.exception("inventory: client_push failed (heartbeat OK)")

    return jsonify(ok=True, event_id=payload["event_id"],
                   target_id=res.target_id,
                   inventory_leaves=inventory_leaves), 200
