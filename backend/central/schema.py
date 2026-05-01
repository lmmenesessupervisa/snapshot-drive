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

    inv = p.get("inventory")
    if inv is not None:
        _validate_inventory(inv)

    return p


# Caps al inventario opcional embebido en heartbeats. Valores conservadores
# para que un payload con inventory aún quepa cómodo en MAX_PAYLOAD_BYTES.
_INV_MAX_LEAVES = 64
_INV_MAX_FILES_TOTAL = 200
_INV_FNAME_MAX = 256
_INV_PATH_MAX = 1024


def _validate_inventory(inv: Any) -> None:
    if not isinstance(inv, dict):
        raise SchemaError("inventory must be object")
    leaves = inv.get("leaves")
    if not isinstance(leaves, list):
        raise SchemaError("inventory.leaves must be a list")
    if len(leaves) > _INV_MAX_LEAVES:
        raise SchemaError(
            f"inventory.leaves too many: {len(leaves)} > {_INV_MAX_LEAVES}"
        )
    files_total = 0
    for i, leaf in enumerate(leaves):
        if not isinstance(leaf, dict):
            raise SchemaError(f"inventory.leaves[{i}] must be object")
        cat = leaf.get("category")
        if cat not in _CATEGORY_VALUES:
            raise SchemaError(f"inventory.leaves[{i}].category invalid: {cat}")
        sub = leaf.get("subkey")
        if not isinstance(sub, str):
            raise SchemaError(f"inventory.leaves[{i}].subkey must be string")
        _require_path_safe(sub, f"inventory.leaves[{i}].subkey")
        files = leaf.get("files")
        if not isinstance(files, list):
            raise SchemaError(f"inventory.leaves[{i}].files must be a list")
        files_total += len(files)
        if files_total > _INV_MAX_FILES_TOTAL:
            raise SchemaError(
                f"inventory: too many files total ({files_total} > {_INV_MAX_FILES_TOTAL})"
            )
        for j, f in enumerate(files):
            if not isinstance(f, dict):
                raise SchemaError(f"inventory.leaves[{i}].files[{j}] must be object")
            name = f.get("name")
            if not isinstance(name, str) or len(name) > _INV_FNAME_MAX:
                raise SchemaError(
                    f"inventory.leaves[{i}].files[{j}].name invalid"
                )
            path_v = f.get("path")
            if not isinstance(path_v, str) or len(path_v) > _INV_PATH_MAX:
                raise SchemaError(
                    f"inventory.leaves[{i}].files[{j}].path invalid"
                )
            size = f.get("size")
            if not isinstance(size, int) or size < 0:
                raise SchemaError(
                    f"inventory.leaves[{i}].files[{j}].size must be non-negative int"
                )
            ts = f.get("ts")
            if not isinstance(ts, str) or len(ts) != 15:
                raise SchemaError(
                    f"inventory.leaves[{i}].files[{j}].ts invalid (expected YYYYMMDD_HHMMSS)"
                )
            enc = f.get("encrypted", False)
            if not isinstance(enc, bool):
                raise SchemaError(
                    f"inventory.leaves[{i}].files[{j}].encrypted must be bool"
                )
