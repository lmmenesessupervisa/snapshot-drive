#!/usr/bin/env bash
# ===========================================================
# snapshot-V3 — instalador para Ubuntu Server
#
# Instala en /opt/snapshot-V3, crea venv, despliega unidades systemd,
# inicializa repo restic y activa timers.
# ===========================================================

set -Eeuo pipefail

INSTALL_ROOT="/opt/snapshot-V3"
STATE_DIR="/var/lib/snapshot-v3"
LOG_DIR="/var/log/snapshot-v3"
API_PORT="${API_PORT:-5070}"
FRONTEND_PORT="${FRONTEND_PORT:-5071}"

# ---------- Helpers ----------
need_root()   { [[ $EUID -eq 0 ]] || { echo "Ejecuta como root (sudo)"; exit 1; }; }
bold() { printf "\033[1m%s\033[0m\n" "$*"; }
info() { printf "  • %s\n" "$*"; }

need_root

bold "[1/7] Dependencias del sistema"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip \
    restic rclone \
    curl ca-certificates openssl \
    rsync jq mailutils

bold "[2/7] Usuarios y directorios"
install -d -m 0750 "$INSTALL_ROOT" "$STATE_DIR" "$LOG_DIR"

bold "[3/7] Desplegando archivos en $INSTALL_ROOT"
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
rsync -a --delete \
    --exclude '.venv' --exclude 'logs/*' --exclude '__pycache__' \
    "$SRC_DIR"/ "$INSTALL_ROOT"/
chmod +x "$INSTALL_ROOT/core/bin/snapctl" "$INSTALL_ROOT/core/lib/common.sh"
ln -sf "$INSTALL_ROOT/core/bin/snapctl" /usr/local/bin/snapctl

# Override local con credenciales (OAuth, notificaciones, etc).
# Vive en /etc/snapshot-v3/ — FUERA del árbol de código — para que el
# rsync --delete de arriba no lo borre en los upgrades y para desacoplar
# los secretos del cliente del ciclo de vida del código.
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

bold "[4/7] Virtualenv Python y dependencias backend"
python3 -m venv "$INSTALL_ROOT/.venv"
"$INSTALL_ROOT/.venv/bin/pip" install --upgrade pip wheel
"$INSTALL_ROOT/.venv/bin/pip" install -r "$INSTALL_ROOT/backend/requirements.txt"

bold "[5/7] Inicializando repositorio restic"
if snapctl status >/dev/null 2>&1; then
    info "Estado previo detectado, saltando init."
else
    snapctl init || info "init no-op (posiblemente ya inicializado)"
fi

bold "[6/7] Instalando unidades systemd"
install -m 0644 "$INSTALL_ROOT/systemd/snapshot-backend.service"    /etc/systemd/system/
install -m 0644 "$INSTALL_ROOT/systemd/snapshot@.service"           /etc/systemd/system/
install -m 0644 "$INSTALL_ROOT/systemd/snapshot@.timer"             /etc/systemd/system/
install -m 0644 "$INSTALL_ROOT/systemd/snapshot-healthcheck.service" /etc/systemd/system/
install -m 0644 "$INSTALL_ROOT/systemd/snapshot-healthcheck.timer"   /etc/systemd/system/

# Drop-in para el timer de reconcile (cadencia cada 30 min)
install -d -m 0755 /etc/systemd/system/snapshot@reconcile.timer.d/
install -m 0644 "$INSTALL_ROOT/systemd/snapshot@reconcile.timer.d/override.conf" \
    /etc/systemd/system/snapshot@reconcile.timer.d/override.conf

systemctl daemon-reload
systemctl enable --now snapshot-backend.service
systemctl enable --now snapshot@create.timer
systemctl enable --now snapshot@reconcile.timer
systemctl enable --now snapshot@prune.timer
systemctl enable --now snapshot-healthcheck.timer
# El viejo timer 'sync' queda deprecado en favor de 'reconcile'; desactivar si existe
systemctl disable --now snapshot@sync.timer 2>/dev/null || true

bold "[7/7] Validaciones finales"
sleep 1
if curl -fsS "http://127.0.0.1:${API_PORT}/api/health" >/dev/null; then
    info "API UP en http://127.0.0.1:${API_PORT}"
else
    echo "!! API no responde. Revisa: journalctl -u snapshot-backend -n 80"
    exit 2
fi

bold "Instalación completada."
cat <<EOF

  Panel web:  http://127.0.0.1:${API_PORT}/
  API:        http://127.0.0.1:${API_PORT}/api/health
  CLI:        snapctl status

  Configuración: $INSTALL_ROOT/core/etc/snapshot.conf
  Logs:          $LOG_DIR/

  Rclone Google Drive:
    rclone config --config $STATE_DIR/rclone.conf   # crear remoto 'gdrive'

  Timers activos:
    systemctl list-timers 'snapshot*'

EOF
