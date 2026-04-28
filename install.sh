#!/usr/bin/env bash
# ===========================================================
# snapshot-V3 — instalador standalone para Linux
#
# NO toca apt ni el Python del sistema. Descarga Python 3 standalone
# (de python-build-standalone), restic y rclone desde sus releases
# oficiales a /opt/snapshot-V3/bundle/, y monta el venv contra el
# Python bundled.
#
# Requiere del sistema solo: curl, tar, python3, rsync (core utils
# presentes por defecto en Ubuntu Server y derivados).
# ===========================================================

set -Eeuo pipefail

INSTALL_ROOT="/opt/snapshot-V3"
STATE_DIR="/var/lib/snapshot-v3"
LOG_DIR="/var/log/snapshot-v3"
BUNDLE_DIR="$INSTALL_ROOT/bundle"
API_PORT="${API_PORT:-5070}"
FRONTEND_PORT="${FRONTEND_PORT:-5071}"

# Versiones pinneadas (sobreescribibles via env antes de ejecutar).
: "${PYTHON_VERSION:=3.12.8}"
: "${PYTHON_PBS_DATE:=20241219}"
: "${RESTIC_VERSION:=0.17.3}"
: "${RCLONE_VERSION:=v1.68.2}"
: "${AGE_VERSION:=v1.2.1}"

ASSUME_YES=0
CENTRAL_MODE=0

usage() {
    cat <<EOF
Instalador standalone de snapshot-V3.

Descarga Python, restic y rclone a /opt/snapshot-V3/bundle/ desde sus
releases oficiales — NO toca apt ni el Python del sistema.

Uso:
  sudo ./install.sh        Instalación.
  sudo ./install.sh -y     No-interactivo (pip silencioso).
  sudo ./install.sh -h     Esta ayuda.

Variables de entorno (override de versiones):
  PYTHON_VERSION=${PYTHON_VERSION}
  PYTHON_PBS_DATE=${PYTHON_PBS_DATE}
  RESTIC_VERSION=${RESTIC_VERSION}
  RCLONE_VERSION=${RCLONE_VERSION}

Requiere instalados en el sistema: curl, tar, python3, rsync.
EOF
}

for arg in "$@"; do
    case "$arg" in
        -y|--yes) ASSUME_YES=1 ;;
        --central) CENTRAL_MODE=1 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Flag desconocida: $arg"; usage; exit 1 ;;
    esac
done

# ---------- Helpers ----------
need_root()   { [[ $EUID -eq 0 ]] || { echo "Ejecuta como root (sudo)"; exit 1; }; }
bold() { printf "\033[1m%s\033[0m\n" "$*"; }
info() { printf "  • %s\n" "$*"; }

need_root

# ---------- Arquitectura ----------
ARCH="$(uname -m)"
case "$ARCH" in
    x86_64)
        PYTHON_PLATFORM="x86_64-unknown-linux-gnu"
        RESTIC_PLATFORM="linux_amd64"
        RCLONE_PLATFORM="linux-amd64"
        AGE_PLATFORM="linux-amd64"
        ;;
    aarch64|arm64)
        PYTHON_PLATFORM="aarch64-unknown-linux-gnu"
        RESTIC_PLATFORM="linux_arm64"
        RCLONE_PLATFORM="linux-arm64"
        AGE_PLATFORM="linux-arm64"
        ;;
    *)
        echo "!! Arquitectura no soportada: $ARCH"
        echo "!! Compatibles: x86_64, aarch64"
        exit 1
        ;;
esac

bold "[1/9] Verificando tooling mínimo del sistema"
for t in curl tar python3 rsync; do
    if ! command -v "$t" >/dev/null 2>&1; then
        echo "!! Falta '$t'. Instálalo con:   sudo apt-get install -y $t"
        echo "!! (solo ese paquete; nada más del snapshot-V3 requiere apt)"
        exit 1
    fi
done
info "curl, tar, python3, rsync presentes."

bold "[2/9] Directorios del proyecto"
install -d -m 0750 "$INSTALL_ROOT" "$STATE_DIR" "$LOG_DIR"
install -d -m 0755 "$BUNDLE_DIR/bin"

bold "[3/9] Desplegando archivos en $INSTALL_ROOT"
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
# --exclude 'bundle': los binarios bundled viven DENTRO de INSTALL_ROOT
# pero NO están en git. Sin el exclude, rsync --delete los borraría.
rsync -a --delete \
    --exclude '.venv' --exclude 'logs/*' --exclude '__pycache__' \
    --exclude 'bundle' \
    "$SRC_DIR"/ "$INSTALL_ROOT"/
chmod +x "$INSTALL_ROOT/core/bin/snapctl" "$INSTALL_ROOT/core/lib/common.sh"
ln -sf "$INSTALL_ROOT/core/bin/snapctl" /usr/local/bin/snapctl

# Override local con credenciales (OAuth, notificaciones, etc).
# Vive en /etc/snapshot-v3/ — FUERA del árbol de código — para que el
# rsync --delete de arriba no lo borre en los upgrades.
LOCAL_CONF_DIR="/etc/snapshot-v3"
LOCAL_CONF="$LOCAL_CONF_DIR/snapshot.local.conf"
install -d -m 0700 "$LOCAL_CONF_DIR"
if [[ ! -f "$LOCAL_CONF" ]]; then
    install -m 0600 "$INSTALL_ROOT/core/etc/snapshot.local.conf.example" "$LOCAL_CONF"
    info "Creado $LOCAL_CONF (edítalo con GOOGLE_CLIENT_ID/SECRET)"
else
    chmod 600 "$LOCAL_CONF" || true
    info "Override local conservado: $LOCAL_CONF"
fi

# Generate SECRET_KEY if empty
if ! grep -qE '^SECRET_KEY="[a-f0-9]{64}"' "$LOCAL_CONF"; then
    NEW_KEY="$(openssl rand -hex 32)"
    if grep -qE '^SECRET_KEY=' "$LOCAL_CONF"; then
        sed -i "s|^SECRET_KEY=.*|SECRET_KEY=\"$NEW_KEY\"|" "$LOCAL_CONF"
    else
        echo "SECRET_KEY=\"$NEW_KEY\"" >> "$LOCAL_CONF"
    fi
    info "SECRET_KEY generado en $LOCAL_CONF"
fi
chmod 600 "$LOCAL_CONF"

bold "[4/9] Descargando binarios standalone a $BUNDLE_DIR"

PYTHON_TARBALL="cpython-${PYTHON_VERSION}+${PYTHON_PBS_DATE}-${PYTHON_PLATFORM}-install_only.tar.gz"
PYTHON_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PYTHON_PBS_DATE}/${PYTHON_TARBALL}"
RESTIC_URL="https://github.com/restic/restic/releases/download/v${RESTIC_VERSION}/restic_${RESTIC_VERSION}_${RESTIC_PLATFORM}.bz2"
RCLONE_URL="https://downloads.rclone.org/${RCLONE_VERSION}/rclone-${RCLONE_VERSION}-${RCLONE_PLATFORM}.zip"
AGE_URL="https://github.com/FiloSottile/age/releases/download/${AGE_VERSION}/age-${AGE_VERSION}-${AGE_PLATFORM}.tar.gz"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

fetch() {
    local url="$1" dest="$2"
    info "↓ $(basename "$dest")  ($(echo "$url" | awk -F/ '{print $3}'))"
    curl -fSL --retry 3 --retry-delay 5 --progress-bar -o "$dest" "$url"
}

_py_bundled_ver() {
    "$BUNDLE_DIR/python/bin/python3" -V 2>/dev/null | awk '{print $2}' || true
}
_restic_bundled_ver() {
    "$BUNDLE_DIR/bin/restic" version 2>/dev/null | awk 'NR==1{print $2}' || true
}
_rclone_bundled_ver() {
    "$BUNDLE_DIR/bin/rclone" version 2>/dev/null | awk 'NR==1{sub(/^v/, "", $2); print $2}' || true
}
_age_bundled_ver() {
    # age --version emite "v1.2.1" — quitamos la 'v' y devolvemos los digits.
    "$BUNDLE_DIR/bin/age" --version 2>/dev/null | head -1 | sed 's/^v//' || true
}

# --- Python standalone ---
if [[ ! -x "$BUNDLE_DIR/python/bin/python3" ]] || [[ "$(_py_bundled_ver)" != "$PYTHON_VERSION" ]]; then
    rm -rf "$BUNDLE_DIR/python"
    install -d "$BUNDLE_DIR/python"
    fetch "$PYTHON_URL" "$TMP_DIR/python.tar.gz"
    tar -xzf "$TMP_DIR/python.tar.gz" -C "$BUNDLE_DIR/python" --strip-components=1
    info "Python $(_py_bundled_ver) instalado."
else
    info "Python bundled ya presente: $(_py_bundled_ver)"
fi

# --- restic ---
if [[ ! -x "$BUNDLE_DIR/bin/restic" ]] || [[ "$(_restic_bundled_ver)" != "$RESTIC_VERSION" ]]; then
    fetch "$RESTIC_URL" "$TMP_DIR/restic.bz2"
    python3 - "$TMP_DIR/restic.bz2" "$BUNDLE_DIR/bin/restic" <<'PY'
import bz2, shutil, sys
src, dst = sys.argv[1], sys.argv[2]
with bz2.open(src, "rb") as s, open(dst, "wb") as d:
    shutil.copyfileobj(s, d)
PY
    chmod +x "$BUNDLE_DIR/bin/restic"
    info "restic $(_restic_bundled_ver) instalado."
else
    info "restic bundled ya presente: $(_restic_bundled_ver)"
fi

# --- rclone ---
RCLONE_EXPECT="${RCLONE_VERSION#v}"
if [[ ! -x "$BUNDLE_DIR/bin/rclone" ]] || [[ "$(_rclone_bundled_ver)" != "$RCLONE_EXPECT" ]]; then
    fetch "$RCLONE_URL" "$TMP_DIR/rclone.zip"
    python3 - "$TMP_DIR/rclone.zip" "$BUNDLE_DIR/bin/rclone" <<'PY'
import zipfile, shutil, sys
src, dst = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(src) as z:
    for m in z.namelist():
        if m.endswith("/rclone"):
            with z.open(m) as s, open(dst, "wb") as d:
                shutil.copyfileobj(s, d)
            break
    else:
        raise SystemExit("rclone binary not found inside zip")
PY
    chmod +x "$BUNDLE_DIR/bin/rclone"
    info "rclone $(_rclone_bundled_ver) instalado."
else
    info "rclone bundled ya presente: $(_rclone_bundled_ver)"
fi

# --- age (opt-in encryption alternative to openssl) ---
AGE_EXPECT="${AGE_VERSION#v}"
if [[ ! -x "$BUNDLE_DIR/bin/age" ]] || [[ "$(_age_bundled_ver)" != "$AGE_EXPECT" ]]; then
    fetch "$AGE_URL" "$TMP_DIR/age.tar.gz"
    tar -xzf "$TMP_DIR/age.tar.gz" -C "$TMP_DIR"
    install -m 0755 "$TMP_DIR/age/age" "$BUNDLE_DIR/bin/age"
    install -m 0755 "$TMP_DIR/age/age-keygen" "$BUNDLE_DIR/bin/age-keygen"
    info "age $(_age_bundled_ver) instalado."
else
    info "age bundled ya presente: $(_age_bundled_ver)"
fi

bold "[5/9] Virtualenv Python (contra el Python bundled) y deps backend"
"$BUNDLE_DIR/python/bin/python3" -m venv --clear "$INSTALL_ROOT/.venv"
PIP_QUIET=""
[[ $ASSUME_YES -eq 1 ]] && PIP_QUIET="--quiet"
"$INSTALL_ROOT/.venv/bin/pip" install $PIP_QUIET --upgrade pip wheel
"$INSTALL_ROOT/.venv/bin/pip" install $PIP_QUIET -r "$INSTALL_ROOT/backend/requirements.txt"
info "Venv OK ($("$INSTALL_ROOT/.venv/bin/python3" -V))"

bold "[6/9] Preparando estado local"
# El motor actual (archive mensual) no usa restic — los archivos .tar.zst
# se crean en streaming y van directo a Drive. Si algún operador decide
# re-habilitar el flujo restic legacy, 'snapctl init' sigue disponible
# y generará /var/lib/snapshot-v3/.restic-pass bajo demanda.
info "Estado local OK. Archive usa streaming directo a Drive."

bold "[7/9] Instalando unidades systemd"
install -m 0644 "$INSTALL_ROOT/systemd/snapshot-backend.service"    /etc/systemd/system/
install -m 0644 "$INSTALL_ROOT/systemd/snapshot@.service"           /etc/systemd/system/
install -m 0644 "$INSTALL_ROOT/systemd/snapshot@.timer"             /etc/systemd/system/
install -m 0644 "$INSTALL_ROOT/systemd/snapshot-healthcheck.service" /etc/systemd/system/
install -m 0644 "$INSTALL_ROOT/systemd/snapshot-healthcheck.timer"   /etc/systemd/system/

install -d -m 0755 /etc/systemd/system/snapshot@reconcile.timer.d/
install -m 0644 "$INSTALL_ROOT/systemd/snapshot@reconcile.timer.d/override.conf" \
    /etc/systemd/system/snapshot@reconcile.timer.d/override.conf

# Drop-ins para el backup mensual cold-storage ('archive').
install -d -m 0755 /etc/systemd/system/snapshot@archive.timer.d/
install -m 0644 "$INSTALL_ROOT/systemd/snapshot@archive.timer.d/override.conf" \
    /etc/systemd/system/snapshot@archive.timer.d/override.conf
install -d -m 0755 /etc/systemd/system/snapshot@archive.service.d/
install -m 0644 "$INSTALL_ROOT/systemd/snapshot@archive.service.d/override.conf" \
    /etc/systemd/system/snapshot@archive.service.d/override.conf

# Drop-ins para el backup de bases de datos ('db-archive').
install -d -m 0755 /etc/systemd/system/snapshot@db-archive.timer.d/
install -m 0644 "$INSTALL_ROOT/systemd/snapshot@db-archive.timer.d/override.conf" \
    /etc/systemd/system/snapshot@db-archive.timer.d/override.conf
install -d -m 0755 /etc/systemd/system/snapshot@db-archive.service.d/
install -m 0644 "$INSTALL_ROOT/systemd/snapshot@db-archive.service.d/override.conf" \
    /etc/systemd/system/snapshot@db-archive.service.d/override.conf

systemctl daemon-reload
systemctl enable --now snapshot-backend.service
# Backup mensual cold-storage (único timer de backup activo).
systemctl enable --now snapshot@archive.timer
systemctl enable --now snapshot-healthcheck.timer
# Timers restic legacy: desactivados por default. Se pueden re-habilitar
# manualmente si algún operador quiere usar la rama restic para incremental:
#   sudo systemctl enable --now snapshot@create.timer
systemctl disable --now snapshot@create.timer    2>/dev/null || true
systemctl disable --now snapshot@reconcile.timer 2>/dev/null || true
systemctl disable --now snapshot@prune.timer     2>/dev/null || true

# DB archive timer: solo activar si DB_BACKUP_TARGETS está configurado.
if grep -qE '^DB_BACKUP_TARGETS="[^"]+"' "$LOCAL_CONF" 2>/dev/null; then
    systemctl enable --now snapshot@db-archive.timer
    info "snapshot@db-archive.timer activado (DB_BACKUP_TARGETS configurado)."
else
    systemctl disable --now snapshot@db-archive.timer 2>/dev/null || true
    info "snapshot@db-archive.timer NO activado (DB_BACKUP_TARGETS vacío)."
fi
systemctl disable --now snapshot@sync.timer      2>/dev/null || true

bold "[8/9] Crear primer usuario admin"
ADMIN_CRED_FILE="/root/.snapshot-v3-admin-credentials"

# Detect if at least one admin exists already.
EXISTING_ADMINS="$(
    cd "$INSTALL_ROOT" && \
    "$INSTALL_ROOT/.venv/bin/python" -m backend.auth.admin_cli list 2>/dev/null \
        | awk 'NR>1 && $3=="admin"{print $2}'
)" || true

if [[ -z "${EXISTING_ADMINS:-}" ]]; then
    if [[ $ASSUME_YES -eq 1 ]]; then
        ADMIN_EMAIL="admin@$(hostname -s)"
        ADMIN_PWD="$(openssl rand -base64 18 | tr -d '=+/' | cut -c1-24)"
    else
        read -rp "  Email del admin: " ADMIN_EMAIL
        ADMIN_PWD="$(openssl rand -base64 18 | tr -d '=+/' | cut -c1-24)"
    fi
    (cd "$INSTALL_ROOT" && \
     "$INSTALL_ROOT/.venv/bin/python" -m backend.auth.admin_cli create \
        --email "$ADMIN_EMAIL" --display "Admin" --role admin \
        --password "$ADMIN_PWD" >/dev/null)
    if [[ $ASSUME_YES -eq 1 ]]; then
        umask 077
        cat > "$ADMIN_CRED_FILE" <<EOF
email: $ADMIN_EMAIL
password: $ADMIN_PWD
EOF
        chmod 0600 "$ADMIN_CRED_FILE"
        info "Admin creado. Credenciales en $ADMIN_CRED_FILE (0600)."
    else
        cat <<EOF

  ✔  Admin creado: $ADMIN_EMAIL
  ✔  Password (24 chars): $ADMIN_PWD
  ⚠  ANOTALA — no se vuelve a mostrar.
  ⚠  Tu primer login te pedirá configurar MFA (obligatorio para admin).

EOF
    fi
else
    info "Admin existente: $EXISTING_ADMINS — no se crea uno nuevo."
fi

if [[ $CENTRAL_MODE -eq 1 ]]; then
    bold "[8.5/9] Configurando modo CENTRAL"
    # Setear MODE=central en local.conf si no está ya
    if ! grep -q '^MODE=' "$LOCAL_CONF" 2>/dev/null; then
        echo 'MODE="central"' >> "$LOCAL_CONF"
        info "MODE=central agregado a $LOCAL_CONF"
    else
        sed -i 's/^MODE=.*/MODE="central"/' "$LOCAL_CONF"
        info "MODE actualizado a central en $LOCAL_CONF"
    fi
    # En central no corre el archive timer del cliente
    systemctl disable --now snapshot@archive.timer 2>/dev/null || true
    systemctl restart snapshot-backend
    sleep 1
    # Crear cliente demo + token la primera vez
    if "$INSTALL_ROOT/.venv/bin/python" -c "
from backend.config import Config
from backend.models.db import DB
from backend.central import models
db = DB(Config.DB_PATH)
import sqlite3
c = sqlite3.connect(str(Config.DB_PATH))
n = c.execute('SELECT COUNT(*) FROM clients').fetchone()[0]
exit(0 if n == 0 else 1)
" 2>/dev/null; then
        info "Creando cliente demo + token inicial..."
        "$INSTALL_ROOT/.venv/bin/python" -c "
import sqlite3, sys
from backend.config import Config
from backend.models.db import DB
from backend.central import models, tokens
DB(Config.DB_PATH)
c = sqlite3.connect(str(Config.DB_PATH), isolation_level=None)
cid = models.create_client(c, proyecto='demo')
plain, _ = tokens.issue(c, cid, label='demo-host')
print(f'CLIENT demo creado (id={cid}).')
print(f'TOKEN (guardar AHORA — no se vuelve a mostrar):')
print(f'  {plain}')
"
    else
        info "Ya existen clientes — se conserva la lista actual."
    fi
    cat <<EOF
─────────────────────────────────────────────────
Snippet de Caddy para central.tu-dominio.com:

central.tu-dominio.com {
  reverse_proxy 127.0.0.1:5070
}
─────────────────────────────────────────────────
EOF
fi

bold "[9/9] Validaciones finales"
sleep 1
if curl -fsS "http://127.0.0.1:${API_PORT}/api/health" >/dev/null; then
    info "API UP en http://127.0.0.1:${API_PORT}"
else
    echo "!! API no responde. Revisa: journalctl -u snapshot-backend -n 80"
    exit 2
fi

bold "Instalación completada."
cat <<EOF

  Panel web:   http://127.0.0.1:${API_PORT}/
  API:         http://127.0.0.1:${API_PORT}/api/health
  CLI:         snapctl status

  Código:                    $INSTALL_ROOT/
  Binarios standalone:       $BUNDLE_DIR/
    · Python $(_py_bundled_ver)
    · restic $(_restic_bundled_ver)
    · rclone $(_rclone_bundled_ver)
  Config global:             $INSTALL_ROOT/core/etc/snapshot.conf
  Override local (secrets):  $LOCAL_CONF
  Estado + backups locales:  $STATE_DIR/
  Logs:                      $LOG_DIR/

  NADA se ha tocado del sistema: ni apt, ni el Python del host, ni otros
  servicios. Todo lo que snapshot-V3 necesita vive en /opt/snapshot-V3/
  y /etc/snapshot-v3/ (y datos en /var/lib/snapshot-v3/, logs en
  /var/log/snapshot-v3/). common.sh antepone el bundle/bin al PATH, así
  que snapctl usa los binarios aislados y no el restic/rclone del host
  si existieran.

  SIGUIENTES PASOS:

  1. Edita el override local con las credenciales OAuth:
       sudo nano $LOCAL_CONF
       sudo systemctl restart snapshot-backend

  2. Abre el panel web (por SSH tunnel si estás remoto) y usa
     "Vincular Drive" para autorizar vía Device Flow.

  3. Verifica:
       sudo snapctl status
       systemctl list-timers 'snapshot*'
       sudo snapctl create --tag post-install

EOF
