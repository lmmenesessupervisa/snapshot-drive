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

     Los archivos ya subidos a Google Drive no se tocan — siguen en
     tu cuenta y puedes restaurarlos con rclone/restic más adelante.

EOF
    if [[ $ASSUME_YES -eq 0 && $DRY -eq 0 ]]; then
        read -rp "  Escribe 'PURGE' para confirmar: " ANS
        [[ "$ANS" == "PURGE" ]] || { echo "Abortado."; exit 1; }
    fi
fi

bold "[1/4] Deteniendo y deshabilitando servicios systemd"
UNITS=(
    snapshot-backend.service
    snapshot-healthcheck.timer
    snapshot-healthcheck.service
    snapshot@create.timer
    snapshot@reconcile.timer
    snapshot@prune.timer
    snapshot@sync.timer
    snapshot@check.timer
)
for u in "${UNITS[@]}"; do
    sd disable --now "$u"
done

bold "[2/4] Eliminando unit files de systemd"
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
        info "borrado $f"
    fi
done
if [[ -d /etc/systemd/system/snapshot@reconcile.timer.d ]]; then
    run rm -rf /etc/systemd/system/snapshot@reconcile.timer.d
    info "borrado /etc/systemd/system/snapshot@reconcile.timer.d"
fi

sd daemon-reload
sd reset-failed

bold "[3/4] Eliminando CLI y código"
if [[ -L "$SNAPCTL_LINK" || -e "$SNAPCTL_LINK" ]]; then
    run rm -f "$SNAPCTL_LINK"
    info "borrado $SNAPCTL_LINK"
fi
if [[ -d "$INSTALL_ROOT" ]]; then
    run rm -rf "$INSTALL_ROOT"
    info "borrado $INSTALL_ROOT"
fi

bold "[4/4] Datos y logs"
if [[ $PURGE -eq 1 ]]; then
    if [[ -d "$STATE_DIR" ]]; then
        run rm -rf "$STATE_DIR"
        info "borrado $STATE_DIR"
    fi
    if [[ -d "$LOG_DIR" ]]; then
        run rm -rf "$LOG_DIR"
        info "borrado $LOG_DIR"
    fi
else
    info "Conservados (reinstalar mantiene estos datos intactos):"
    [[ -d "$STATE_DIR" ]] && info "    $STATE_DIR"
    [[ -d "$LOG_DIR"  ]] && info "    $LOG_DIR"
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
