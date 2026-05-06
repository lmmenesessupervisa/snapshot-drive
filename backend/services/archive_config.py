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
    try:
        text = path.read_text()
    except OSError:
        # No existe, o no podemos leerlo (proceso no-root con local.conf 0600).
        # Mismo trade-off que backend/config.py:_read_shell_conf — degradamos a
        # dict vacío en lugar de hacer crash al import time.
        return out
    for line in text.splitlines():
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


# ---------------------------------------------------------------------------
# Sub-E: DB backups
# ---------------------------------------------------------------------------

DB_ENGINES = ("postgres", "mysql", "mongo")
_DB_NAME_RE = re.compile(r"^[A-Za-z0-9._\-]+$")
_DB_TOKEN_RE = re.compile(r"^(postgres|mysql|mongo):[A-Za-z0-9._\-]+$")


def _validate_targets(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    tokens = raw.split()
    seen: set[str] = set()
    for t in tokens:
        if not _DB_TOKEN_RE.match(t):
            raise ArchiveConfigError(
                f"Target inválido: {t!r}. Formato: engine:dbname "
                f"(engine: {', '.join(DB_ENGINES)})."
            )
        if t in seen:
            raise ArchiveConfigError(f"Target duplicado: {t}")
        seen.add(t)
    return " ".join(tokens)


def _validate_simple(key: str, value: str, *, max_len: int = 256) -> str:
    v = (value or "").strip()
    if len(v) > max_len:
        raise ArchiveConfigError(f"{key}: demasiado largo (máx {max_len}).")
    if '"' in v or "\n" in v or "\r" in v:
        raise ArchiveConfigError(f"{key}: no puede contener comillas dobles ni saltos de línea.")
    return v


def _validate_port(key: str, value: str) -> str:
    v = (value or "").strip()
    if not v:
        return ""
    if not v.isdigit() or not (1 <= int(v) <= 65535):
        raise ArchiveConfigError(f"{key}: puerto inválido.")
    return v


def get_db_config() -> dict:
    vals = _read(_local_conf_path())
    return {
        "targets":     vals.get("DB_BACKUP_TARGETS", ""),
        "pg_host":     vals.get("DB_PG_HOST", ""),
        "pg_port":     vals.get("DB_PG_PORT", "5432"),
        "pg_user":     vals.get("DB_PG_USER", ""),
        "pg_password_set":   bool(vals.get("DB_PG_PASSWORD", "")),
        "mysql_host":  vals.get("DB_MYSQL_HOST", ""),
        "mysql_port":  vals.get("DB_MYSQL_PORT", "3306"),
        "mysql_user":  vals.get("DB_MYSQL_USER", ""),
        "mysql_password_set": bool(vals.get("DB_MYSQL_PASSWORD", "")),
        "mongo_uri_set":      bool(vals.get("DB_MONGO_URI", "")),
        "valid_engines": list(DB_ENGINES),
    }


def set_db_config(values: dict) -> dict:
    updates: dict[str, str] = {
        "DB_BACKUP_TARGETS": _validate_targets(values.get("targets", "")),
        "DB_PG_HOST":  _validate_simple("DB_PG_HOST",  values.get("pg_host", "")),
        "DB_PG_PORT":  _validate_port  ("DB_PG_PORT",  values.get("pg_port", "")) or "5432",
        "DB_PG_USER":  _validate_simple("DB_PG_USER",  values.get("pg_user", "")),
        "DB_MYSQL_HOST": _validate_simple("DB_MYSQL_HOST", values.get("mysql_host", "")),
        "DB_MYSQL_PORT": _validate_port  ("DB_MYSQL_PORT", values.get("mysql_port", "")) or "3306",
        "DB_MYSQL_USER": _validate_simple("DB_MYSQL_USER", values.get("mysql_user", "")),
    }
    # Passwords y URI: solo se actualizan si vienen NO vacías. Para borrar,
    # el frontend pasa explícitamente clear_pg_password=true (etc.).
    if values.get("pg_password"):
        updates["DB_PG_PASSWORD"] = _validate_simple("DB_PG_PASSWORD", values["pg_password"])
    elif values.get("clear_pg_password"):
        updates["DB_PG_PASSWORD"] = ""
    if values.get("mysql_password"):
        updates["DB_MYSQL_PASSWORD"] = _validate_simple("DB_MYSQL_PASSWORD", values["mysql_password"])
    elif values.get("clear_mysql_password"):
        updates["DB_MYSQL_PASSWORD"] = ""
    if values.get("mongo_uri"):
        updates["DB_MONGO_URI"] = _validate_simple("DB_MONGO_URI", values["mongo_uri"], max_len=1024)
    elif values.get("clear_mongo_uri"):
        updates["DB_MONGO_URI"] = ""
    _write_back(updates)
    return get_db_config()


# ---------------------------------------------------------------------------
# Sub-F: age crypto recipients
# ---------------------------------------------------------------------------

_AGE_PUB_RE = re.compile(r"^age1[0-9a-z]{10,}$")


def _validate_recipients(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    parts = raw.split()
    for p in parts:
        if not _AGE_PUB_RE.match(p):
            raise ArchiveConfigError(
                f"Recipient inválido: {p!r}. Debe empezar con 'age1' "
                "(generá con 'snapctl crypto-keygen' o desde la UI)."
            )
    return " ".join(parts)


def get_crypto_config() -> dict:
    vals = _read(_local_conf_path())
    recipients = vals.get("ARCHIVE_AGE_RECIPIENTS", "").strip()
    return {
        "recipients": recipients,
        "recipients_count": len([r for r in recipients.split() if r]),
        "openssl_password_set": bool(vals.get("ARCHIVE_PASSWORD", "")),
        "active_mode": (
            "age" if recipients
            else ("openssl" if vals.get("ARCHIVE_PASSWORD") else "none")
        ),
    }


def set_recipients(raw: str) -> dict:
    _write_back({"ARCHIVE_AGE_RECIPIENTS": _validate_recipients(raw)})
    return get_crypto_config()


# ---------------------------------------------------------------------------
# Sub-D: alerts (only used when MODE=central)
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_URL_RE   = re.compile(r"^https?://[^\s\"']+$")


def get_alerts_config() -> dict:
    vals = _read(_local_conf_path())
    return {
        "no_heartbeat_hours": int(vals.get("ALERTS_NO_HEARTBEAT_HOURS", "48") or 48),
        "shrink_pct": int(vals.get("ALERTS_SHRINK_PCT", "20") or 20),
        "email": vals.get("ALERTS_EMAIL", ""),
        "webhook_set": bool(vals.get("ALERTS_WEBHOOK", "")),
    }


def set_alerts_config(values: dict) -> dict:
    updates: dict[str, str] = {}

    if "no_heartbeat_hours" in values:
        try:
            n = int(values["no_heartbeat_hours"])
        except (TypeError, ValueError):
            raise ArchiveConfigError("no_heartbeat_hours debe ser entero.")
        if not (1 <= n <= 720):
            raise ArchiveConfigError("no_heartbeat_hours debe estar entre 1 y 720.")
        updates["ALERTS_NO_HEARTBEAT_HOURS"] = str(n)

    if "shrink_pct" in values:
        try:
            p = int(values["shrink_pct"])
        except (TypeError, ValueError):
            raise ArchiveConfigError("shrink_pct debe ser entero.")
        if not (1 <= p <= 99):
            raise ArchiveConfigError("shrink_pct debe estar entre 1 y 99.")
        updates["ALERTS_SHRINK_PCT"] = str(p)

    email = (values.get("email") or "").strip()
    if email:
        if not _EMAIL_RE.match(email) or len(email) > 256:
            raise ArchiveConfigError("Email inválido.")
        updates["ALERTS_EMAIL"] = email
    elif values.get("clear_email"):
        updates["ALERTS_EMAIL"] = ""

    if values.get("webhook"):
        url = values["webhook"].strip()
        if not _URL_RE.match(url) or len(url) > 1024:
            raise ArchiveConfigError("Webhook debe ser https?://… (máx 1024 chars).")
        updates["ALERTS_WEBHOOK"] = url
    elif values.get("clear_webhook"):
        updates["ALERTS_WEBHOOK"] = ""

    if updates:
        _write_back(updates)
        # Apply to in-memory Config so cambios surten efecto en el dispatch
        # de alertas sin esperar al próximo restart del backend.
        if "ALERTS_NO_HEARTBEAT_HOURS" in updates:
            Config.ALERTS_NO_HEARTBEAT_HOURS = int(updates["ALERTS_NO_HEARTBEAT_HOURS"])
        if "ALERTS_SHRINK_PCT" in updates:
            Config.ALERTS_SHRINK_PCT = int(updates["ALERTS_SHRINK_PCT"])
        if "ALERTS_EMAIL" in updates:
            Config.ALERTS_EMAIL = updates["ALERTS_EMAIL"]
        if "ALERTS_WEBHOOK" in updates:
            Config.ALERTS_WEBHOOK = updates["ALERTS_WEBHOOK"]

    return get_alerts_config()


# ---------------------------------------------------------------------------
# Central-only: AUDIT_REMOTE_PATH + SNAPSHOT_AUDIT_VIEWER
# ---------------------------------------------------------------------------

# Permitimos slashes y espacios escapados para nombres como "Mi unidad/backups".
_REMOTE_PATH_RE = re.compile(r"^[A-Za-z0-9 _./\-]{1,256}$")


def get_central_config() -> dict:
    vals = _read(_local_conf_path())
    # Devolvemos el valor real (incluido string vacío si el operador lo
    # limpió a propósito = scan desde la raíz del Drive). El default
    # "snapshots" del modelo legacy ya no se inyecta acá.
    raw = (vals.get("AUDIT_REMOTE_PATH") or "").strip().strip("/")
    return {
        "audit_remote_path": raw,
        "audit_viewer_enabled": (vals.get("SNAPSHOT_AUDIT_VIEWER") or "0") == "1",
        "rclone_remote": vals.get("RCLONE_REMOTE", "gdrive"),
    }


def set_central_config(values: dict) -> dict:
    updates: dict[str, str] = {}

    if "audit_remote_path" in values:
        # Vacío o "/" = raíz del Drive (consistente con lo que el UI
        # promete en el placeholder del Paso 3). Solo se valida cuando
        # tiene contenido real.
        raw = (values.get("audit_remote_path") or "").strip().strip("/")
        if raw and not _REMOTE_PATH_RE.match(raw):
            raise ArchiveConfigError(
                "audit_remote_path inválido. Permitido: letras, dígitos, '_', '.', '-', '/'"
            )
        updates["AUDIT_REMOTE_PATH"] = raw

    if "audit_viewer_enabled" in values:
        updates["SNAPSHOT_AUDIT_VIEWER"] = "1" if values["audit_viewer_enabled"] else "0"

    if updates:
        _write_back(updates)
        # Refleja en memoria para que el siguiente request use el valor nuevo
        # sin esperar al restart de gunicorn.
        if "AUDIT_REMOTE_PATH" in updates:
            Config.AUDIT_REMOTE_PATH = updates["AUDIT_REMOTE_PATH"]
        if "SNAPSHOT_AUDIT_VIEWER" in updates:
            Config.AUDIT_ENABLED = updates["SNAPSHOT_AUDIT_VIEWER"] == "1"

    return get_central_config()


# ---------------------------------------------------------------------------
# Client-side: vinculación con un servidor central (CENTRAL_URL/CENTRAL_TOKEN)
# ---------------------------------------------------------------------------

_CENTRAL_URL_RE = re.compile(r"^https?://[A-Za-z0-9._:\-/]+$")
_CENTRAL_TOKEN_RE = re.compile(r"^[A-Za-z0-9_\-]{16,256}$")


def get_client_central_link() -> dict:
    """Estado de la vinculación cliente→central. NUNCA devuelve el token
    en plaintext; solo si está seteado o no."""
    vals = _read(_local_conf_path())
    url = (vals.get("CENTRAL_URL") or "").strip()
    tok = (vals.get("CENTRAL_TOKEN") or "").strip()
    return {
        "central_url": url,
        "token_set": bool(tok),
        "configured": bool(url and tok),
    }


def set_client_central_link(values: dict) -> dict:
    """Setea CENTRAL_URL y/o CENTRAL_TOKEN en local.conf. Pasar
    `central_token=""` deja el token actual intacto (no lo borra) — para
    borrarlo, pasa `clear_token=True`. Igual con `central_url`."""
    updates: dict[str, str] = {}

    if "central_url" in values:
        url = (values.get("central_url") or "").strip().rstrip("/")
        if url:
            if not _CENTRAL_URL_RE.match(url):
                raise ArchiveConfigError(
                    "CENTRAL_URL inválida. Esperado http:// o https:// + host."
                )
            updates["CENTRAL_URL"] = url
        elif values.get("clear_url"):
            updates["CENTRAL_URL"] = ""

    if "central_token" in values:
        tok = (values.get("central_token") or "").strip()
        if tok:
            if not _CENTRAL_TOKEN_RE.match(tok):
                raise ArchiveConfigError(
                    "Token inválido. Esperado 16-256 chars alfanuméricos, '_' o '-'."
                )
            updates["CENTRAL_TOKEN"] = tok
        elif values.get("clear_token"):
            updates["CENTRAL_TOKEN"] = ""

    if updates:
        _write_back(updates)
        if "CENTRAL_URL" in updates:
            Config.CENTRAL_URL = updates["CENTRAL_URL"].rstrip("/")
        if "CENTRAL_TOKEN" in updates:
            Config.CENTRAL_TOKEN = updates["CENTRAL_TOKEN"]

    return get_client_central_link()


def generate_keypair() -> dict:
    """Run age-keygen and return public + private. Does NOT persist the
    private key — it's the caller's responsibility to copy it now.
    """
    import subprocess
    age_keygen = Config.SNAPSHOT_ROOT / "bundle" / "bin" / "age-keygen"
    if not age_keygen.is_file() or not os.access(age_keygen, os.X_OK):
        raise ArchiveConfigError(
            "age-keygen no instalado en bundle/bin/. Re-ejecutá install.sh."
        )
    try:
        out = subprocess.run(
            [str(age_keygen)], capture_output=True, text=True, timeout=10, check=True
        )
    except subprocess.CalledProcessError as e:
        raise ArchiveConfigError(f"age-keygen falló: {e.stderr or e.stdout}") from e
    pub = ""
    priv = ""
    for line in (out.stdout or "").splitlines():
        line = line.rstrip()
        m = re.match(r"^# public key:\s*(\S+)$", line)
        if m:
            pub = m.group(1)
            continue
        if line.startswith("AGE-SECRET-KEY-"):
            priv = line
    if not pub or not priv:
        raise ArchiveConfigError("salida de age-keygen no parseable")
    return {"public": pub, "private": priv}
