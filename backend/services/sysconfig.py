"""Leer/escribir ajustes de snapshot.conf desde el panel web.

Maneja valores que el usuario puede tocar sin entrar al servidor:
- BACKUP_PATHS        rutas que se respaldan (la lista para `restic backup`)
- RCLONE_REMOTE_PATH  carpeta dentro de Drive donde se guarda el repo

La escritura es atómica (tmp + rename) con copia a .bak, igual que
`scheduler.set_retention`, para no corromper el conf si algo falla.
"""
from __future__ import annotations

import os
import re
import shutil
import socket
import tempfile
from pathlib import Path


class ConfigError(Exception):
    pass


# Solo caracteres seguros para rutas POSIX de backup: nada de espacios
# (rompería el word-splitting de BACKUP_PATHS en snapctl) ni metacaracteres
# de shell. Los segmentos `..` se rechazan aparte.
_PATH_RE = re.compile(r"^/[A-Za-z0-9_./\-]+$")

# Carpeta remota: sin slash inicial/final, caracteres seguros para Drive.
_FOLDER_RE = re.compile(r"^[A-Za-z0-9_.\-]+(?:/[A-Za-z0-9_.\-]+)*$")

_KEYS = ("BACKUP_PATHS", "RCLONE_REMOTE_PATH")


def _conf_path() -> Path:
    return Path(os.getenv("CONF_FILE", "/opt/snapshot-V3/core/etc/snapshot.conf"))


def _hostname_short() -> str:
    return socket.gethostname().split(".", 1)[0]


def _read_value(text: str, key: str) -> str:
    for line in text.splitlines():
        m = re.match(
            rf'^\s*{re.escape(key)}\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|(\S*))\s*(?:#.*)?$',
            line,
        )
        if m:
            return m.group(1) or m.group(2) or m.group(3) or ""
    return ""


def _expand(value: str) -> str:
    """Expande ${HOSTNAME} para que la UI muestre la ruta efectiva."""
    return value.replace("${HOSTNAME}", _hostname_short())


def get_config() -> dict:
    p = _conf_path()
    text = p.read_text() if p.exists() else ""
    backup_paths = _read_value(text, "BACKUP_PATHS")
    remote_path = _read_value(text, "RCLONE_REMOTE_PATH")
    return {
        "backup_paths": backup_paths,
        "backup_paths_list": [x for x in re.split(r"\s+", backup_paths.strip()) if x],
        "rclone_remote_path": remote_path,
        "rclone_remote_path_effective": _expand(remote_path),
        "hostname": _hostname_short(),
    }


def _validate_paths(paths: list[str]) -> list[str]:
    cleaned: list[str] = []
    for raw in paths:
        p = raw.strip().rstrip("/")
        if not p:
            continue
        if not _PATH_RE.match(p):
            raise ConfigError(
                f"Ruta inválida: {p!r}. Debe empezar por '/' y usar solo "
                "letras, dígitos, '_', '.', '-' y '/' (sin espacios)."
            )
        if any(seg == ".." for seg in p.split("/")):
            raise ConfigError(f"Ruta no puede contener '..': {p!r}")
        cleaned.append(p)
    if not cleaned:
        raise ConfigError("Debes especificar al menos una ruta a respaldar.")
    # Quitar duplicados preservando orden
    seen: set[str] = set()
    out: list[str] = []
    for p in cleaned:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _validate_remote_path(value: str) -> str:
    v = value.strip().strip("/")
    if not v:
        raise ConfigError("La carpeta de Drive no puede estar vacía.")
    if len(v) > 256:
        raise ConfigError("La carpeta de Drive es demasiado larga (máx 256).")
    if ".." in v.split("/"):
        raise ConfigError("La carpeta de Drive no puede contener '..'")
    # ${HOSTNAME} es un placeholder válido — common.sh lo expande en bash al
    # sourcear snapshot.conf. Lo sustituimos por un token dummy solo para
    # validar con el regex; guardamos el valor original.
    v_check = v.replace("${HOSTNAME}", "H")
    if not _FOLDER_RE.match(v_check):
        raise ConfigError(
            f"Carpeta inválida: {v!r}. Solo letras, dígitos, '_', '.', '-' "
            "y '/' como separador (ej. 'snapshots/zfsantander'). "
            "Puedes usar '${HOSTNAME}' como comodín para el nombre del host."
        )
    return v


def _write_back(new_vals: dict[str, str]) -> None:
    p = _conf_path()
    if not p.exists():
        raise ConfigError(f"snapshot.conf no encontrado: {p}")
    text = p.read_text()
    lines = text.splitlines()
    seen: set[str] = set()
    for i, line in enumerate(lines):
        for k, v in new_vals.items():
            if re.match(rf'^\s*{re.escape(k)}\s*=', line):
                lines[i] = f'{k}="{v}"'
                seen.add(k)
                break
    for k, v in new_vals.items():
        if k not in seen:
            lines.append(f'{k}="{v}"')
    new_text = "\n".join(lines)
    if not new_text.endswith("\n"):
        new_text += "\n"

    bak = p.with_suffix(p.suffix + ".bak")
    try:
        shutil.copy2(p, bak)
    except OSError:
        pass

    tmp = tempfile.NamedTemporaryFile(
        "w", delete=False, dir=str(p.parent), prefix=".snapshot.conf."
    )
    try:
        tmp.write(new_text)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp.close()
        # Preservar permisos originales
        try:
            st = p.stat()
            os.chmod(tmp.name, st.st_mode & 0o777)
        except OSError:
            os.chmod(tmp.name, 0o644)
        os.replace(tmp.name, p)
    except Exception:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        raise


def set_config(values: dict) -> dict:
    new_vals: dict[str, str] = {}

    if "backup_paths" in values:
        raw = values["backup_paths"]
        if isinstance(raw, list):
            paths = raw
        else:
            # acepta separación por líneas, tabs o espacios
            paths = re.split(r"\s+", str(raw).strip())
        paths = _validate_paths(paths)
        new_vals["BACKUP_PATHS"] = " ".join(paths)

    if "rclone_remote_path" in values:
        new_vals["RCLONE_REMOTE_PATH"] = _validate_remote_path(
            str(values["rclone_remote_path"])
        )

    if not new_vals:
        raise ConfigError("No se enviaron valores a guardar.")

    _write_back(new_vals)
    return get_config()
