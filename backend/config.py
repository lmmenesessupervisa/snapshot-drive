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

    SECRET_KEY = os.getenv("SNAPSHOT_SECRET", "change-me-in-production")

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
