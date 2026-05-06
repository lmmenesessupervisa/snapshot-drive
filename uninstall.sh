#!/usr/bin/env bash
# ===========================================================
# snapshot-V3 — desinstalador
#
# Quita servicios systemd, binarios y código en /opt/snapshot-V3.
# Por defecto CONSERVA los datos del cliente en /var/lib/snapshot-v3
# (repo restic + SQLite + rclone.conf) y los logs en /var/log/snapshot-v3.
# Usa --purge para eliminarlos también (IRREVERSIBLE).
# ===========================================================

set -Eeuo pipefail

INSTALL_ROOT="/opt/snapshot-V3"
STATE_DIR="/var/lib/snapshot-v3"
LOG_DIR="/var/log/snapshot-v3"
LOCAL_CONF_DIR="/etc/snapshot-v3"
SNAPCTL_LINK="/usr/local/bin/snapctl"

PURGE=0
DRY=0
ASSUME_YES=0

usage() {
    cat <<EOF
Desinstalador de snapshot-V3.

Uso:
  sudo ./uninstall.sh              Quita código + servicios systemd.
                                   CONSERVA datos en $STATE_DIR
                                   (repo restic, SQLite, rclone.conf) y logs.
  sudo ./uninstall.sh --purge      TAMBIÉN elimina $STATE_DIR y
                                   $LOG_DIR. IRREVERSIBLE.
  sudo ./uninstall.sh --dry-run    Muestra qué haría sin ejecutar nada.
  sudo ./uninstall.sh -y           No pide confirmación interactiva.

Paquetes apt (restic, rclone, python3-venv, mailutils...) NO se tocan —
pueden estar siendo usados por otros servicios del host.
Los snapshots ya subidos a Google Drive NO se tocan.
EOF
}

for arg in "$@"; do
    case "$arg" in
        --purge) PURGE=1 ;;
        --dry-run) DRY=1 ;;
        -y|--yes) ASSUME_YES=1 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Flag desconocida: $arg"; usage; exit 1 ;;
    esac
done

[[ $EUID -eq 0 ]] || { echo "Ejecuta como root (sudo)"; exit 1; }

bold() { printf "\033[1m%s\033[0m\n" "$*"; }
info() { printf "  • %s\n" "$*"; }

run() {
    if [[ $DRY -eq 1 ]]; then
        printf "  [dry-run] %s\n" "$*"
    else
        "$@"
    fi
}

# systemctl tolerante a units inexistentes
sd() { run systemctl "$@" || true; }

bold "Desinstalador de snapshot-V3"
if [[ $PURGE -eq 1 ]]; then
    cat <<EOF

  !! --purge eliminará TODO lo siguiente de forma irreversible:
     - $STATE_DIR
         · repo restic con los backups locales
         · SQLite del backend (historial de jobs/audit)
         · rclone.conf con el token OAuth de Google Drive
     - $LOG_DIR
     - $LOCAL_CONF_DIR (snapshot.local.conf con SECRET_KEY, CENTRAL_TOKEN, etc.)

     Los archivos ya subidos a Google Drive no se tocan — siguen en
     tu cuenta y puedes restaurarlos con rclone/restic más adelante.

EOF
    if [[ $ASSUME_YES -eq 0 && $DRY -eq 0 ]]; then
        read -rp "  Escribe 'PURGE' para confirmar: " ANS
        [[ "$ANS" == "PURGE" ]] || { echo "Abortado."; exit 1; }
    fi
fi

bold "[1/5] Deteniendo instancias en curso del template"
# snapshot@create.timer disparado hace rato deja un snapshot@create.service
# corriendo; `disable --now` al timer no lo mata. Si hay un backup en
# vuelo, lo paramos explícitamente antes de borrar el código.
mapfile -t RUNNING < <(
    systemctl list-units --type=service --state=active --no-legend 'snapshot@*.service' 2>/dev/null \
        | awk '{print $1}'
)
if [[ ${#RUNNING[@]} -gt 0 ]]; then
    info "Instancias activas: ${RUNNING[*]}"
    for u in "${RUNNING[@]}"; do
        sd stop "$u"
    done
else
    info "Sin instancias activas."
fi

bold "[2/5] Deteniendo y deshabilitando timers y servicios"
UNITS=(
    snapshot-backend.service
    snapshot-healthcheck.timer
    snapshot-healthcheck.service
    snapshot@create.timer
    snapshot@reconcile.timer
    snapshot@prune.timer
    snapshot@archive.timer
    snapshot@sync.timer
    snapshot@check.timer
)
for u in "${UNITS[@]}"; do
    sd disable --now "$u"
done

bold "[3/5] Eliminando unit files de systemd"
UNIT_FILES=(
    /etc/systemd/system/snapshot-backend.service
    /etc/systemd/system/snapshot@.service
    /etc/systemd/system/snapshot@.timer
    /etc/systemd/system/snapshot-healthcheck.service
    /etc/systemd/system/snapshot-healthcheck.timer
)
for f in "${UNIT_FILES[@]}"; do
    if [[ -e "$f" ]]; then
        run rm -f "$f"
        [[ $DRY -eq 0 ]] && info "borrado $f"
    fi
done
for DROPIN in /etc/systemd/system/snapshot@reconcile.timer.d \
              /etc/systemd/system/snapshot@archive.timer.d \
              /etc/systemd/system/snapshot@archive.service.d; do
    if [[ -d "$DROPIN" ]]; then
        run rm -rf "$DROPIN"
        [[ $DRY -eq 0 ]] && info "borrado $DROPIN"
    fi
done

sd daemon-reload
# reset-failed SIN argumentos resetea TODO systemd, afectando a otros
# servicios del host que el admin pueda estar investigando. Scope a lo
# nuestro con patrones.
sd reset-failed 'snapshot-*.service' 'snapshot-*.timer' \
                'snapshot@*.service' 'snapshot@*.timer'

bold "[4/5] Eliminando CLI y código"
if [[ -L "$SNAPCTL_LINK" || -e "$SNAPCTL_LINK" ]]; then
    run rm -f "$SNAPCTL_LINK"
    [[ $DRY -eq 0 ]] && info "borrado $SNAPCTL_LINK"
fi
if [[ -d "$INSTALL_ROOT" ]]; then
    run rm -rf "$INSTALL_ROOT"
    [[ $DRY -eq 0 ]] && info "borrado $INSTALL_ROOT"
fi

bold "[5/5] Datos, logs y override local"
if [[ $PURGE -eq 1 ]]; then
    for d in "$STATE_DIR" "$LOG_DIR" "$LOCAL_CONF_DIR"; do
        if [[ -d "$d" ]]; then
            run rm -rf "$d"
            [[ $DRY -eq 0 ]] && info "borrado $d"
        fi
    done
else
    info "Conservados (reinstalar mantiene estos datos intactos):"
    [[ -d "$STATE_DIR"      ]] && info "    $STATE_DIR       (backups restic, SQLite, rclone token)"
    [[ -d "$LOG_DIR"        ]] && info "    $LOG_DIR       (historial JSON)"
    [[ -d "$LOCAL_CONF_DIR" ]] && info "    $LOCAL_CONF_DIR           (SECRET_KEY, CENTRAL_TOKEN, taxonomía)"
    info "Para eliminarlos ejecuta: sudo ./uninstall.sh --purge"
fi

bold "Desinstalación completa."
cat <<EOF

  NO se han tocado:
    - Paquetes apt (restic, rclone, python3-venv, mailutils, jq, rsync)
      → si los quieres remover: sudo apt-get purge restic rclone
    - Snapshots en Google Drive
      → elimínalos manualmente desde tu cuenta si hace falta.

EOF
