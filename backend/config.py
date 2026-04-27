"""Configuración del backend snapshot-V3."""
import os
import re
from pathlib import Path


def _read_shell_conf(path: Path) -> dict:
    """Lee un fichero de configuración estilo shell (KEY="value") sin ejecutarlo."""
    out: dict[str, str] = {}
    try:
        for line in Path(path).read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r'^([A-Z_][A-Z0-9_]*)=(?:"([^"]*)"|\'([^\']*)\'|(\S*))\s*(?:#.*)?$', line)
            if not m:
                continue
            out[m.group(1)] = m.group(2) if m.group(2) is not None else (
                m.group(3) if m.group(3) is not None else (m.group(4) or "")
            )
    except OSError:
        pass
    return out


_DEFAULT_CONF = Path(os.getenv("CONF_FILE", "/opt/snapshot-V3/core/etc/snapshot.conf"))
_LOCAL_CONF = Path(os.getenv("LOCAL_CONF", "/etc/snapshot-v3/snapshot.local.conf"))

# Mismo contrato que core/lib/common.sh: el .local.conf sobreescribe al .conf
# global. Aquí vive GOOGLE_CLIENT_ID/SECRET y cualquier override por cliente.
_CONF = _read_shell_conf(_DEFAULT_CONF)
_CONF.update(_read_shell_conf(_LOCAL_CONF))


class Config:
    SNAPSHOT_ROOT = Path(os.getenv("SNAPSHOT_ROOT", "/opt/snapshot-V3"))
    SNAPCTL_BIN = Path(os.getenv("SNAPCTL_BIN", SNAPSHOT_ROOT / "core" / "bin" / "snapctl"))
    CONF_FILE = _DEFAULT_CONF

    DB_PATH = Path(os.getenv("DB_PATH", "/var/lib/snapshot-v3/snapshot.db"))
    LOG_DIR = Path(os.getenv("LOG_DIR", "/var/log/snapshot-v3"))
    LOG_FILE = LOG_DIR / "backend.log"

    HOST = os.getenv("API_HOST", "127.0.0.1")
    PORT = int(os.getenv("API_PORT", "5070"))

    # Timeout para subprocess snapctl (segundos)
    SNAPCTL_TIMEOUT = int(os.getenv("SNAPCTL_TIMEOUT", "3600"))

    # NOTE: this is no longer used by Flask. backend/app.py derives Flask's
    # SECRET_KEY from load_secret_key() via HKDF so there's a single source of
    # truth (the master in /etc/snapshot-v3/snapshot.local.conf or
    # SNAPSHOT_SECRET_KEY env). Kept here only for code that still imports
    # Config.SECRET_KEY directly; do not add new readers.
    SECRET_KEY = os.getenv("SNAPSHOT_SECRET", "")

    # Google OAuth (Device Flow) — leídos de snapshot.conf, overrideables por env
    GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID") or _CONF.get("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET") or _CONF.get("GOOGLE_CLIENT_SECRET", "")
    GOOGLE_OAUTH_SCOPE = (
        os.getenv("GOOGLE_OAUTH_SCOPE")
        or _CONF.get("GOOGLE_OAUTH_SCOPE")
        or "https://www.googleapis.com/auth/drive"
    )

    # Vista /audit — agregación del shared Drive (solo ops, OFF por defecto).
    AUDIT_ENABLED = (_CONF.get("SNAPSHOT_AUDIT_VIEWER") or os.getenv("SNAPSHOT_AUDIT_VIEWER") or "0") == "1"
    AUDIT_PASSWORD = os.getenv("AUDIT_PASSWORD") or _CONF.get("AUDIT_PASSWORD", "")
    AUDIT_REMOTE_PATH = os.getenv("AUDIT_REMOTE_PATH") or _CONF.get("AUDIT_REMOTE_PATH") or "snapshots"
    RCLONE_CONFIG = Path(
        os.getenv("RCLONE_CONFIG")
        or _CONF.get("RCLONE_CONFIG")
        or "/var/lib/snapshot-v3/rclone.conf"
    )
    RCLONE_REMOTE = os.getenv("RCLONE_REMOTE") or _CONF.get("RCLONE_REMOTE") or "gdrive"
    RCLONE_BIN = Path(
        os.getenv("RCLONE_BIN")
        or str(SNAPSHOT_ROOT / "bundle" / "bin" / "rclone")
    )

    # Archive (backup mensual cold-storage) — taxonomía + retention.
    # Password NO se expone via Config (se lee fresh del local.conf en cada
    # operación de escritura para no mantenerlo en memoria del backend).
    LOCAL_CONF_PATH = _LOCAL_CONF
    BACKUP_PROYECTO = _CONF.get("BACKUP_PROYECTO", "")
    BACKUP_ENTORNO  = _CONF.get("BACKUP_ENTORNO", "")
    BACKUP_PAIS     = _CONF.get("BACKUP_PAIS", "")
    BACKUP_NOMBRE   = _CONF.get("BACKUP_NOMBRE", "")
    ARCHIVE_KEEP_MONTHS = int(_CONF.get("ARCHIVE_KEEP_MONTHS", "12") or 12)

    # ─── Sub-proyecto B: despliegue dual ─────────────────────────────────
    # MODE controla qué blueprints carga app.py. Default "client".
    _MODE_RAW = (os.getenv("MODE") or _CONF.get("MODE") or "client").strip().lower()
    MODE = _MODE_RAW if _MODE_RAW in ("client", "central") else "client"

    # Solo relevante en client mode: dónde postear heartbeats.
    CENTRAL_URL = (os.getenv("CENTRAL_URL") or _CONF.get("CENTRAL_URL") or "").rstrip("/")
    CENTRAL_TOKEN = os.getenv("CENTRAL_TOKEN") or _CONF.get("CENTRAL_TOKEN") or ""
    CENTRAL_TIMEOUT_S = int(os.getenv("CENTRAL_TIMEOUT_S") or _CONF.get("CENTRAL_TIMEOUT_S") or "5")
    # Tope duro de payload aceptado por el endpoint POST /heartbeat
    CENTRAL_MAX_PAYLOAD_BYTES = 64 * 1024


# --- Auth ---
import secrets

SESSION_TTL_HOURS = int(os.environ.get("SESSION_TTL_HOURS", 8))
IDLE_TIMEOUT_MINUTES = int(os.environ.get("IDLE_TIMEOUT_MINUTES", 60))
MFA_REQUIRED_ROLES = ("admin",)


def load_secret_key() -> bytes:
    """Load 32-byte SECRET_KEY from snapshot.local.conf or env.

    Order of precedence:
      1. SNAPSHOT_SECRET_KEY env var (hex, 64 chars).
      2. /etc/snapshot-v3/snapshot.local.conf line `SECRET_KEY="..."`.
      3. /var/lib/snapshot-v3/.secret_key (auto-generated, 0600).
    """
    env_val = os.environ.get("SNAPSHOT_SECRET_KEY")
    if env_val:
        return bytes.fromhex(env_val)

    local_conf = "/etc/snapshot-v3/snapshot.local.conf"
    if os.path.exists(local_conf):
        with open(local_conf) as f:
            for line in f:
                line = line.strip()
                if line.startswith("SECRET_KEY="):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if val:
                        return bytes.fromhex(val)

    fallback = "/var/lib/snapshot-v3/.secret_key"
    if os.path.exists(fallback):
        with open(fallback) as f:
            return bytes.fromhex(f.read().strip())
    # Generate one
    key = secrets.token_hex(32)
    os.makedirs(os.path.dirname(fallback), exist_ok=True)
    with open(fallback, "w") as f:
        f.write(key)
    os.chmod(fallback, 0o600)
    return bytes.fromhex(key)
