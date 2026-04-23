"""Operaciones sobre archives (listar/crear/restaurar/borrar) expuestas a la UI.

Todas las operaciones orquestan rclone + snapctl como subprocess, igual que
el resto del backend. Mantiene la responsabilidad del motor en el CLI y
el backend solo formatea JSON para el frontend.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import Config
from . import archive_config

log = logging.getLogger(__name__)


class ArchiveOpError(Exception):
    pass


# ---------------- TTL cache (in-process, gunicorn 1 worker) ----------------
# rclone lsjson contra Drive no es gratis: cada listado es varias llamadas a
# la API y tarda 1-3s. El Dashboard/Snapshots/Audit consultan estos endpoints
# en cada page load. Un cache de ~60s evita que navegar entre vistas dispare
# rclone cada vez. Se invalida explícitamente tras create/delete/restore.
_CACHE: dict[str, tuple[float, Any]] = {}
_CACHE_TTL_S = 60.0


def _cache_get(key: str):
    hit = _CACHE.get(key)
    if hit and (time.time() - hit[0]) < _CACHE_TTL_S:
        return hit[1]
    return None


def _cache_set(key: str, value):
    _CACHE[key] = (time.time(), value)


def invalidate_cache() -> None:
    _CACHE.clear()


# ---------------- parsing helpers ----------------
_NAME_RE = re.compile(r"^servidor_(?P<host>[A-Za-z0-9_.\-]+)_(?P<ts>\d{8}_\d{6})\.(?P<ext>tar\.zst(?:\.enc)?)$")


def _parse_archive_name(name: str) -> dict | None:
    m = _NAME_RE.match(name)
    if not m:
        return None
    return {
        "host": m.group("host"),
        "timestamp": m.group("ts"),
        "encrypted": m.group("ext").endswith(".enc"),
    }


def _rclone(*args: str, timeout: int = 30) -> str:
    if not Config.RCLONE_BIN.exists():
        raise ArchiveOpError(f"rclone no encontrado en {Config.RCLONE_BIN}")
    if not Config.RCLONE_CONFIG.exists():
        raise ArchiveOpError("rclone.conf no existe — vincula Drive primero")
    cmd = [str(Config.RCLONE_BIN), "--config", str(Config.RCLONE_CONFIG), *args]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise ArchiveOpError(f"rclone timeout {timeout}s: {' '.join(args[:2])}")
    if r.returncode != 0:
        raise ArchiveOpError(f"rclone rc={r.returncode}: {r.stderr.strip()[:300]}")
    return r.stdout


def _archive_root_for(cfg: dict, host_scope: str | None = None) -> str:
    """Raíz en Drive donde viven los archives de este host.
    Si host_scope está definido, devuelve esa carpeta; si no, usa el nombre
    configurado. Formato: PROY/ENT/PAIS/os/linux/NOMBRE
    """
    host = host_scope or cfg["nombre"]
    if not cfg["proyecto"] or not cfg["entorno"] or not cfg["pais"] or not host:
        raise ArchiveOpError("Taxonomía incompleta. Configúrala en Ajustes.")
    return f"{cfg['proyecto']}/{cfg['entorno']}/{cfg['pais']}/os/linux/{host}"


# ---------------- list ----------------
def list_archives(force: bool = False) -> list[dict]:
    """Lista archives del host local (taxonomía del propio cliente)."""
    if not force:
        cached = _cache_get("list")
        if cached is not None:
            return cached

    cfg = archive_config.get_config()
    root = _archive_root_for(cfg)
    remote = f"{Config.RCLONE_REMOTE}:{root}"
    try:
        raw = _rclone("lsjson", "-R", "--files-only", remote, timeout=60)
    except ArchiveOpError as e:
        if "directory not found" in str(e).lower() or "not found" in str(e).lower():
            _cache_set("list", [])
            return []
        raise
    try:
        items = json.loads(raw or "[]")
    except json.JSONDecodeError:
        _cache_set("list", [])
        return []

    out: list[dict] = []
    for it in items:
        name = it.get("Name", "")
        parsed = _parse_archive_name(name)
        if not parsed:
            continue
        out.append({
            "name": name,
            "path": f"{root}/{it.get('Path', name)}",
            "size_bytes": it.get("Size", 0),
            "modified_ts": it.get("ModTime", ""),
            "timestamp": parsed["timestamp"],
            "encrypted": parsed["encrypted"],
            "host": parsed["host"],
        })
    out.sort(key=lambda x: x["modified_ts"], reverse=True)
    _cache_set("list", out)
    return out


# ---------------- summary (Dashboard) ----------------
def summary(force: bool = False) -> dict:
    """Datos agregados para el Dashboard."""
    if not force:
        cached = _cache_get("summary")
        if cached is not None:
            return cached

    cfg = archive_config.get_config()
    result = {
        "taxonomy_ok": bool(cfg["proyecto"] and cfg["entorno"] and cfg["pais"]),
        "password_set": cfg["password_set"],
        "host": cfg["nombre"],
        "archives_count": 0,
        "total_size_bytes": 0,
        "last": None,
        "next_scheduled": None,
        "drive_path_root": None,
    }
    if not result["taxonomy_ok"]:
        return result

    result["drive_path_root"] = _archive_root_for(cfg)
    try:
        archives = list_archives()
    except ArchiveOpError as e:
        log.warning("summary: list_archives falló: %s", e)
        archives = []

    result["archives_count"] = len(archives)
    result["total_size_bytes"] = sum(a.get("size_bytes", 0) for a in archives)
    if archives:
        last = archives[0]
        result["last"] = {
            "name": last["name"],
            "path": last["path"],
            "size_bytes": last["size_bytes"],
            "modified_ts": last["modified_ts"],
            "encrypted": last["encrypted"],
        }

    # Próxima ejecución del timer: lo leemos de systemd (rápido, no requiere root).
    try:
        r = subprocess.run(
            ["systemctl", "show", "snapshot@archive.timer",
             "-p", "NextElapseUSecRealtime", "--value"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        val = (r.stdout or "").strip()
        if val and val != "0" and val != "n/a":
            result["next_scheduled"] = val
    except Exception:
        pass
    _cache_set("summary", result)
    return result


# ---------------- create ----------------
def create_archive() -> dict:
    """Dispara snapctl archive en sincrónico. Puede tardar varios minutos.
    El endpoint /api/archive/create DEBE usar un timeout grande (horas)."""
    bin_path = str(Config.SNAPCTL_BIN)
    start = time.time()
    log.info("archive_ops: invocando %s archive", bin_path)
    r = subprocess.run(
        [bin_path, "archive"],
        capture_output=True, text=True,
        timeout=Config.SNAPCTL_TIMEOUT, check=False,
    )
    dur = int(time.time() - start)
    if r.returncode != 0:
        raise ArchiveOpError(
            f"snapctl archive rc={r.returncode} (dur={dur}s): "
            f"{r.stderr.strip()[:500] or r.stdout.strip()[:500]}"
        )
    # Acabamos de subir un archivo nuevo: forzar refresh para que aparezca.
    invalidate_cache()
    try:
        archives = list_archives(force=True)
    except ArchiveOpError:
        archives = []
    last = archives[0] if archives else None
    return {"duration_s": dur, "last": last}


# ---------------- restore ----------------
_SAFE_TARGET_RE = re.compile(r"^/[A-Za-z0-9_./\-]+$")


def restore_archive(remote_path: str, target: str) -> dict:
    """Descarga + descifra + extrae un archive al directorio `target`."""
    remote_path = (remote_path or "").strip().lstrip("/")
    target = (target or "").strip()
    if not remote_path:
        raise ArchiveOpError("Falta la ruta del archive remoto")
    if not target:
        raise ArchiveOpError("Falta el directorio de destino")
    if not _SAFE_TARGET_RE.match(target):
        raise ArchiveOpError(
            "Destino inválido: debe ser absoluto, sin espacios ni caracteres especiales."
        )
    if any(seg == ".." for seg in target.split("/")):
        raise ArchiveOpError("Destino no puede contener '..'")

    bin_path = str(Config.SNAPCTL_BIN)
    start = time.time()
    log.info("archive_ops: restore %s → %s", remote_path, target)
    r = subprocess.run(
        [bin_path, "archive-restore", remote_path, "--target", target],
        capture_output=True, text=True,
        timeout=Config.SNAPCTL_TIMEOUT, check=False,
    )
    dur = int(time.time() - start)
    if r.returncode != 0:
        msg = (r.stderr.strip() or r.stdout.strip() or f"rc={r.returncode}")[:500]
        if "bad decrypt" in msg.lower():
            raise ArchiveOpError(
                "Contraseña incorrecta. Este archive se encriptó con una contraseña distinta a la actual."
            )
        raise ArchiveOpError(f"Restore falló (dur={dur}s): {msg}")
    return {"duration_s": dur, "target": target, "path": remote_path}


# ---------------- delete ----------------
def delete_archive(remote_path: str) -> dict:
    remote_path = (remote_path or "").strip().lstrip("/")
    if not remote_path:
        raise ArchiveOpError("Falta la ruta del archive")
    # Sanity: debe ser un path bajo la taxonomía configurada.
    cfg = archive_config.get_config()
    root = _archive_root_for(cfg)
    if not remote_path.startswith(root + "/"):
        raise ArchiveOpError(
            f"La ruta no pertenece a este cliente. Debe empezar por {root}/"
        )

    remote = f"{Config.RCLONE_REMOTE}:{remote_path}"
    _rclone("deletefile", remote, timeout=30)
    invalidate_cache()   # el listado cambió
    return {"path": remote_path}
