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

ASSUME_YES=0

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
        ;;
    aarch64|arm64)
        PYTHON_PLATFORM="aarch64-unknown-linux-gnu"
        RESTIC_PLATFORM="linux_arm64"
        RCLONE_PLATFORM="linux-arm64"
        ;;
    *)
        echo "!! Arquitectura no soportada: $ARCH"
        echo "!! Compatibles: x86_64, aarch64"
        exit 1
        ;;
esac

bold "[1/8] Verificando tooling mínimo del sistema"
for t in curl tar python3 rsync; do
    if ! command -v "$t" >/dev/null 2>&1; then
        echo "!! Falta '$t'. Instálalo con:   sudo apt-get install -y $t"
        echo "!! (solo ese paquete; nada más del snapshot-V3 requiere apt)"
        exit 1
    fi
done
info "curl, tar, python3, rsync presentes."

bold "[2/8] Directorios del proyecto"
install -d -m 0750 "$INSTALL_ROOT" "$STATE_DIR" "$LOG_DIR"
install -d -m 0755 "$BUNDLE_DIR/bin"

bold "[3/8] Desplegando archivos en $INSTALL_ROOT"
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

bold "[4/8] Descargando binarios standalone a $BUNDLE_DIR"

PYTHON_TARBALL="cpython-${PYTHON_VERSION}+${PYTHON_PBS_DATE}-${PYTHON_PLATFORM}-install_only.tar.gz"
PYTHON_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PYTHON_PBS_DATE}/${PYTHON_TARBALL}"
RESTIC_URL="https://github.com/restic/restic/releases/download/v${RESTIC_VERSION}/restic_${RESTIC_VERSION}_${RESTIC_PLATFORM}.bz2"
RCLONE_URL="https://downloads.rclone.org/${RCLONE_VERSION}/rclone-${RCLONE_VERSION}-${RCLONE_PLATFORM}.zip"

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

bold "[5/8] Virtualenv Python (contra el Python bundled) y deps backend"
"$BUNDLE_DIR/python/bin/python3" -m venv --clear "$INSTALL_ROOT/.venv"
PIP_QUIET=""
[[ $ASSUME_YES -eq 1 ]] && PIP_QUIET="--quiet"
"$INSTALL_ROOT/.venv/bin/pip" install $PIP_QUIET --upgrade pip wheel
"$INSTALL_ROOT/.venv/bin/pip" install $PIP_QUIET -r "$INSTALL_ROOT/backend/requirements.txt"
info "Venv OK ($("$INSTALL_ROOT/.venv/bin/python3" -V))"

bold "[6/8] Inicializando repositorio restic"
RESTIC_PASS="$STATE_DIR/.restic-pass"
if [[ -f "$RESTIC_PASS" ]]; then
    info "Password file ya existe; saltando init."
else
    if ! snapctl init; then
        echo "!! snapctl init falló. Revisa $LOG_DIR/snapctl.log y ejecútalo"
        echo "!! manualmente: sudo snapctl init"
        exit 1
    fi
fi

bold "[7/8] Instalando unidades systemd"
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
systemctl disable --now snapshot@sync.timer      2>/dev/null || true

bold "[8/8] Validaciones finales"
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
