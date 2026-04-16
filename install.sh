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

ASSUME_YES=0
SKIP_DEPS=0

usage() {
    cat <<EOF
Instalador de snapshot-V3.

Uso:
  sudo ./install.sh              Interactivo. apt pide confirmación antes de instalar.
  sudo ./install.sh -y           Responde sí a todo (apt en modo no-interactivo).
  sudo ./install.sh --skip-deps  No toca apt. Útil si ya tienes restic/rclone/
                                 python3-venv o si tu sistema tiene paquetes
                                 third-party rotos (ej. anydesk) que rompen apt.

Dependencias del sistema: restic rclone python3-venv
(jq y mailutils son opcionales para JSON CLI y notificaciones por email.)
EOF
}

for arg in "$@"; do
    case "$arg" in
        -y|--yes) ASSUME_YES=1 ;;
        --skip-deps) SKIP_DEPS=1 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Flag desconocida: $arg"; usage; exit 1 ;;
    esac
done

# ---------- Helpers ----------
need_root()   { [[ $EUID -eq 0 ]] || { echo "Ejecuta como root (sudo)"; exit 1; }; }
bold() { printf "\033[1m%s\033[0m\n" "$*"; }
info() { printf "  • %s\n" "$*"; }

need_root

bold "[1/7] Dependencias del sistema"

# Binarios que snapctl requiere SÍ o SÍ — si ya están, no tocamos apt.
REQUIRED_BINS=(restic rclone)
# Paquetes apt que instalamos si falta algo. No incluimos python3/rsync/curl/
# openssl/ca-certificates porque vienen de fábrica en Ubuntu Server y listarlos
# solo provoca que apt re-evalúe dependencias y arrastre upgrades no deseados.
REQUIRED_PKGS=(restic rclone python3-venv jq mailutils)

check_missing() {
    local -n _out=$1
    _out=()
    local b
    for b in "${REQUIRED_BINS[@]}"; do
        command -v "$b" >/dev/null 2>&1 || _out+=("$b")
    done
    # En Debian/Ubuntu 'venv' es stdlib pero 'ensurepip' vive en el paquete
    # python3.X-venv — sin él, 'python3 -m venv' muere con
    # "ensurepip is not available". Probamos ensurepip, que es lo que de
    # verdad necesita [4/7] para crear el venv con pip.
    python3 -c 'import ensurepip' 2>/dev/null || _out+=("python3-venv")
}

MISSING=()
check_missing MISSING

if [[ $SKIP_DEPS -eq 1 ]]; then
    if [[ ${#MISSING[@]} -gt 0 ]]; then
        echo "!! --skip-deps, pero faltan: ${MISSING[*]}"
        echo "!! Instálalos manualmente y vuelve a ejecutar, o quita --skip-deps."
        exit 1
    fi
    info "Saltando apt (--skip-deps). Dependencias verificadas."
elif [[ ${#MISSING[@]} -eq 0 ]]; then
    info "Todas las dependencias ya instaladas. Saltando apt."
else
    info "Faltan dependencias: ${MISSING[*]}"
    echo "  Se ejecutará:"
    echo "    apt-get install --no-install-recommends --no-upgrade ${REQUIRED_PKGS[*]}"
    echo "  --no-upgrade → apt NO actualiza paquetes ya instalados."
    echo "  apt te mostrará el resumen completo antes de ejecutar nada."
    if [[ $ASSUME_YES -eq 0 ]]; then
        read -rp "  ¿Continuar? [y/N] " ANS
        [[ "$ANS" =~ ^[Yy]$ ]] || { echo "Abortado."; exit 1; }
    fi
    APT_ARGS=(--no-install-recommends --no-upgrade)
    [[ $ASSUME_YES -eq 1 ]] && APT_ARGS+=(-y)
    apt-get update || info "apt-get update falló; continuando con la cache existente."
    if ! apt-get install "${APT_ARGS[@]}" "${REQUIRED_PKGS[@]}"; then
        info "apt-get install devolvió error — suele ser un paquete ajeno"
        info "(anydesk, postgresql-*, etc.) con post-install roto. Verificando"
        info "si nuestras dependencias quedaron OK igualmente..."
        check_missing MISSING
        if [[ ${#MISSING[@]} -gt 0 ]]; then
            echo "!! Aún faltan dependencias críticas: ${MISSING[*]}"
            echo "!! Repara el sistema con:   sudo apt --fix-broken install"
            echo "!! O instálalas a mano y re-ejecuta:   sudo bash install.sh --skip-deps"
            exit 1
        fi
        info "Nuestras dependencias están presentes; continuamos pese al error de apt."
    fi
fi

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
# --clear: si hay un .venv parcial de un intento previo que falló, se limpia
# y se recrea. Idempotente en installs sanos (re-crea un venv nuevo y pip
# install reinstala las deps; tarda ~30s).
python3 -m venv --clear "$INSTALL_ROOT/.venv"
"$INSTALL_ROOT/.venv/bin/pip" install --upgrade pip wheel
"$INSTALL_ROOT/.venv/bin/pip" install -r "$INSTALL_ROOT/backend/requirements.txt"

bold "[5/7] Inicializando repositorio restic"
# snapctl status es read-only y SIEMPRE retorna 0, así que no sirve como
# guardia ("¿ya está inicializado?"). Chequeamos directamente el password
# file, que es lo que cmd_init crea primero y cmd_create requiere.
# cmd_init es idempotente (no regenera password, no re-init repos ya existentes).
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

  Configuración global:    $INSTALL_ROOT/core/etc/snapshot.conf
  Override local (secrets): /etc/snapshot-v3/snapshot.local.conf
  Logs:                    $LOG_DIR/

  SIGUIENTES PASOS:

  1. Edita /etc/snapshot-v3/snapshot.local.conf y rellena GOOGLE_CLIENT_ID
     y GOOGLE_CLIENT_SECRET del OAuth Client de Google Cloud Console.
       sudo nano /etc/snapshot-v3/snapshot.local.conf
       sudo systemctl restart snapshot-backend

  2. Abre el panel web (por SSH tunnel si estás remoto) y usa
     "Vincular Drive" para autorizar vía Device Flow. El backend escribe
     $STATE_DIR/rclone.conf automáticamente — NO hace falta 'rclone config'
     a mano.

  3. Verifica:
       sudo snapctl status
       systemctl list-timers 'snapshot*'
       sudo snapctl create --tag post-install   # snapshot de prueba

EOF
