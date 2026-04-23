"""Leer/escribir la configuración de archive (taxonomía + password) en
/etc/snapshot-v3/snapshot.local.conf.

Contrato igual que backend/services/sysconfig.py: escritura atómica con
tmp+rename, backup en .bak, preserva permisos (600 en local.conf — el
archivo contiene la password).

El valor de ARCHIVE_PASSWORD NUNCA se devuelve al frontend — solo una
bandera `password_set: bool`. El frontend puede SETEARLA vía POST pero
nunca LEERLA. Si el operador pierde la password no hay forma de
recuperarla desde la UI — es responsabilidad suya guardarla.
"""
from __future__ import annotations

import os
import re
import shutil
import socket
import tempfile
from pathlib import Path

from ..config import Config


class ArchiveConfigError(Exception):
    pass


PROYECTOS = ["superaccess-uno", "superaccess-dos", "basculas",
             "proyectos-especiales", "orus"]
ENTORNOS  = ["cloud", "local"]
PAISES    = ["colombia", "peru", "costa-rica", "panama"]

_KEY_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")
_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")


def _local_conf_path() -> Path:
    return Path(os.getenv("LOCAL_CONF", str(Config.LOCAL_CONF_PATH)))


def _read(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(
            r'^([A-Z_][A-Z0-9_]*)=(?:"([^"]*)"|\'([^\']*)\'|(\S*))\s*(?:#.*)?$',
            line,
        )
        if m:
            out[m.group(1)] = m.group(2) if m.group(2) is not None else (
                m.group(3) if m.group(3) is not None else (m.group(4) or "")
            )
    return out


def _hostname_short() -> str:
    return socket.gethostname().split(".", 1)[0]


def get_config() -> dict:
    vals = _read(_local_conf_path())
    nombre = vals.get("BACKUP_NOMBRE", "") or _hostname_short()
    return {
        "proyecto": vals.get("BACKUP_PROYECTO", ""),
        "entorno":  vals.get("BACKUP_ENTORNO",  ""),
        "pais":     vals.get("BACKUP_PAIS",     ""),
        "nombre":   nombre,
        "nombre_effective": nombre,
        "hostname": _hostname_short(),
        "keep_months": int(vals.get("ARCHIVE_KEEP_MONTHS", "12") or 12),
        "password_set": bool(vals.get("ARCHIVE_PASSWORD", "")),
        "valid_proyectos": PROYECTOS,
        "valid_entornos":  ENTORNOS,
        "valid_paises":    PAISES,
    }


def _validate_enum(key: str, value: str, allowed: list[str]) -> str:
    v = (value or "").strip()
    if not v:
        raise ArchiveConfigError(f"{key}: campo obligatorio.")
    if v not in allowed:
        raise ArchiveConfigError(
            f"{key} inválido: {v!r}. Válidos: {', '.join(allowed)}"
        )
    return v


def _validate_nombre(value: str) -> str:
    v = (value or "").strip()
    if not v:
        raise ArchiveConfigError("Nombre del servidor obligatorio.")
    if len(v) > 64:
        raise ArchiveConfigError("Nombre demasiado largo (máx 64).")
    if not _NAME_RE.match(v):
        raise ArchiveConfigError(
            "Nombre solo puede contener letras, dígitos, guiones, puntos y guiones bajos."
        )
    return v


def _validate_password(pw: str, confirm: str | None) -> str:
    if pw != (confirm if confirm is not None else pw):
        raise ArchiveConfigError("La contraseña y su confirmación no coinciden.")
    if pw == "":
        return ""   # vacío = desactivar encriptación
    if len(pw) < 8:
        raise ArchiveConfigError("La contraseña debe tener al menos 8 caracteres.")
    if len(pw) > 256:
        raise ArchiveConfigError("La contraseña es demasiado larga.")
    # Permite cualquier carácter imprimible; rechaza comillas dobles porque
    # romperían el formato shell al escribir a local.conf.
    if '"' in pw or "\n" in pw or "\r" in pw:
        raise ArchiveConfigError('La contraseña no puede contener comillas dobles ni saltos de línea.')
    return pw


def _write_back(updates: dict[str, str]) -> None:
    """Escribe atómicamente `updates` al local.conf. Preserva líneas no
    mencionadas. Crea el archivo si no existe (mode 0600)."""
    p = _local_conf_path()
    p.parent.mkdir(parents=True, exist_ok=True)

    existing_text = p.read_text() if p.exists() else ""
    lines = existing_text.splitlines() if existing_text else []
    seen: set[str] = set()

    # Re-escribe valores existentes en su línea original (preserva orden y comentarios).
    # Python regex: usar \s, NO [[:space:]] (eso es POSIX y se interpreta como set literal).
    for i, line in enumerate(lines):
        for k, v in updates.items():
            if re.match(rf'^\s*{re.escape(k)}\s*=', line):
                lines[i] = f'{k}="{v}"'
                seen.add(k)
                break

    # Añade valores nuevos al final.
    for k, v in updates.items():
        if k not in seen:
            lines.append(f'{k}="{v}"')

    new_text = "\n".join(lines).rstrip() + "\n"

    # Backup del original.
    if p.exists():
        try:
            shutil.copy2(p, p.with_suffix(p.suffix + ".bak"))
        except OSError:
            pass

    # Escritura atómica + preserva permisos (o crea como 600).
    tmp = tempfile.NamedTemporaryFile(
        "w", delete=False, dir=str(p.parent), prefix=".snapshot.local.conf."
    )
    try:
        tmp.write(new_text)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp.close()
        if p.exists():
            st = p.stat()
            os.chmod(tmp.name, st.st_mode & 0o777)
        else:
            os.chmod(tmp.name, 0o600)
        os.replace(tmp.name, p)
    except Exception:
        try: os.unlink(tmp.name)
        except OSError: pass
        raise


def set_taxonomy(values: dict) -> dict:
    updates = {
        "BACKUP_PROYECTO": _validate_enum("proyecto", values.get("proyecto", ""), PROYECTOS),
        "BACKUP_ENTORNO":  _validate_enum("entorno",  values.get("entorno",  ""), ENTORNOS),
        "BACKUP_PAIS":     _validate_enum("pais",     values.get("pais",     ""), PAISES),
        "BACKUP_NOMBRE":   _validate_nombre(values.get("nombre", "") or _hostname_short()),
    }
    if "keep_months" in values:
        try:
            km = int(values["keep_months"])
        except (TypeError, ValueError):
            raise ArchiveConfigError("keep_months debe ser entero.")
        if not (1 <= km <= 120):
            raise ArchiveConfigError("keep_months debe estar entre 1 y 120.")
        updates["ARCHIVE_KEEP_MONTHS"] = str(km)
    _write_back(updates)
    return get_config()


def set_password(new: str, confirm: str) -> dict:
    pw = _validate_password(new or "", confirm or "")
    _write_back({"ARCHIVE_PASSWORD": pw})
    return get_config()


def clear_password() -> dict:
    _write_back({"ARCHIVE_PASSWORD": ""})
    return get_config()
