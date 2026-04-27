"""Validacion del payload del heartbeat. Sin pydantic — chequeos manuales
con mensajes de error explicitos para devolver al cliente en 400."""
from __future__ import annotations

import re
import uuid
from typing import Any

MAX_PAYLOAD_BYTES = 64 * 1024
_OP_VALUES = {"archive", "create", "reconcile", "prune", "delete", "db_dump"}
_STATUS_VALUES = {"ok", "fail", "running"}
_CATEGORY_VALUES = {"os", "db"}
# Caracteres permitidos en proyecto/entorno/pais/subkey/label: alfanum, dash, underscore, dot
_PATH_SAFE = re.compile(r"^[a-zA-Z0-9._-]{1,64}$")


class SchemaError(ValueError):
    pass


def check_size(raw: bytes) -> None:
    if len(raw) > MAX_PAYLOAD_BYTES:
        raise SchemaError(f"payload too large: {len(raw)} > {MAX_PAYLOAD_BYTES}")


def _require(d: dict, key: str, types: tuple) -> Any:
    if key not in d:
        raise SchemaError(f"missing field: {key}")
    if not isinstance(d[key], types):
        raise SchemaError(f"wrong type for {key}: {type(d[key]).__name__}")
    return d[key]


def _require_path_safe(value: str, fieldname: str) -> str:
    if not _PATH_SAFE.match(value):
        raise SchemaError(f"{fieldname} has invalid chars (must match {_PATH_SAFE.pattern})")
    return value


def validate_heartbeat(p: dict) -> dict:
    if not isinstance(p, dict):
        raise SchemaError("payload must be a JSON object")

    eid = _require(p, "event_id", (str,))
    try:
        uuid.UUID(eid)
    except ValueError:
        raise SchemaError("event_id is not a valid uuid")

    _require(p, "ts", (str,))

    client = _require(p, "client", (dict,))
    _require_path_safe(_require(client, "proyecto", (str,)), "client.proyecto")
    if "entorno" in client and client["entorno"] is not None:
        _require_path_safe(client["entorno"], "client.entorno")
    if "pais" in client and client["pais"] is not None:
        _require_path_safe(client["pais"], "client.pais")

    target = _require(p, "target", (dict,))
    cat = _require(target, "category", (str,))
    if cat not in _CATEGORY_VALUES:
        raise SchemaError(f"invalid category: {cat}")
    _require_path_safe(_require(target, "subkey", (str,)), "target.subkey")
    _require_path_safe(_require(target, "label", (str,)), "target.label")

    op = _require(p, "operation", (dict,))
    if op["op"] not in _OP_VALUES:
        raise SchemaError(f"invalid operation.op: {op['op']}")
    if op["status"] not in _STATUS_VALUES:
        raise SchemaError(f"invalid operation.status: {op['status']}")
    if "duration_s" in op and op["duration_s"] is not None:
        if not isinstance(op["duration_s"], int) or op["duration_s"] < 0:
            raise SchemaError("operation.duration_s must be non-negative int")
    if op.get("error") is not None and len(str(op["error"])) > 500:
        raise SchemaError("operation.error too long (>500 chars)")

    snap = p.get("snapshot")
    if snap is not None:
        if not isinstance(snap, dict):
            raise SchemaError("snapshot must be object")
        if "size_bytes" in snap:
            if not isinstance(snap["size_bytes"], int) or snap["size_bytes"] < 0:
                raise SchemaError("snapshot.size_bytes must be non-negative int")

    totals = p.get("totals")
    if totals is not None:
        if not isinstance(totals, dict):
            raise SchemaError("totals must be object")
        for k in ("size_bytes", "count_files"):
            if k in totals and (not isinstance(totals[k], int) or totals[k] < 0):
                raise SchemaError(f"totals.{k} must be non-negative int")

    return p
