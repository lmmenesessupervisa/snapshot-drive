#!/usr/bin/env bash
# ===========================================================
# snapshot-V3 — librería común (logging, utilidades, validaciones)
# ===========================================================

set -Eeuo pipefail

SNAPSHOT_ROOT="${SNAPSHOT_ROOT:-/opt/snapshot-V3}"
CONF_FILE="${CONF_FILE:-${SNAPSHOT_ROOT}/core/etc/snapshot.conf}"

# shellcheck disable=SC1090
[[ -f "$CONF_FILE" ]] && source "$CONF_FILE"

# Override local por instalación (credenciales OAuth, tuning por sitio).
# NO se trackea en git — se crea desde snapshot.local.conf.example en install.
LOCAL_CONF="${LOCAL_CONF:-${SNAPSHOT_ROOT}/core/etc/snapshot.local.conf}"
# shellcheck disable=SC1090
[[ -f "$LOCAL_CONF" ]] && source "$LOCAL_CONF"

export RESTIC_REPOSITORY="${RESTIC_REPO}"
export RESTIC_PASSWORD_FILE="${RESTIC_PASSWORD_FILE}"

# ---------- Tuning rclone (se heredan por los subprocess de restic) -----
# rclone lee automáticamente variables RCLONE_<FLAG> mayúsculas, así que
# exportarlas aquí basta para que restic → rclone use estos valores sin
# tocar más la línea de comandos.
export RCLONE_DRIVE_CHUNK_SIZE="${RCLONE_DRIVE_CHUNK_SIZE:-64M}"
export RCLONE_DRIVE_PACER_MIN_SLEEP="${RCLONE_PACER_MIN_SLEEP:-100ms}"
export RCLONE_DRIVE_USE_TRASH="${RCLONE_DRIVE_USE_TRASH:-false}"
export RCLONE_TRANSFERS="${RCLONE_TRANSFERS:-2}"
export RCLONE_CHECKERS="${RCLONE_CHECKERS:-4}"
export RCLONE_TPSLIMIT="${RCLONE_TPSLIMIT:-5}"
export RCLONE_TPSLIMIT_BURST="${RCLONE_TPSLIMIT_BURST:-10}"
export RCLONE_CONTIMEOUT="${RCLONE_CONTIMEOUT:-30s}"
export RCLONE_TIMEOUT="${RCLONE_TIMEOUT:-300s}"
export RCLONE_LOW_LEVEL_RETRIES="${RCLONE_LOW_LEVEL_RETRIES:-10}"
export RCLONE_RETRIES="${RCLONE_RETRIES:-5}"
[[ -n "${RCLONE_BWLIMIT:-}" ]] && export RCLONE_BWLIMIT

# ---------- Logging estructurado (JSON lines) ----------
_ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

log() {
    local level="$1"; shift
    local msg="$*"
    local line
    line=$(printf '{"ts":"%s","level":"%s","host":"%s","msg":%s}' \
        "$(_ts)" "$level" "${HOSTNAME:-$(hostname -s)}" \
        "$(printf '%s' "$msg" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().strip()))')")
    mkdir -p "${LOG_DIR:-/var/log/snapshot-v3}"
    echo "$line" | tee -a "${LOG_FILE:-/var/log/snapshot-v3/snapctl.log}" >&2
}

log_info()  { log "INFO"  "$*"; }
log_warn()  { log "WARN"  "$*"; }
log_error() { log "ERROR" "$*"; }

die() { log_error "$*"; exit 1; }

# ---------- Validaciones ----------
require_cmd() {
    command -v "$1" >/dev/null 2>&1 || die "Comando requerido no encontrado: $1"
}

require_repo() {
    [[ -d "${RESTIC_REPO}" ]] || die "Repo restic no existe: ${RESTIC_REPO}. Ejecuta: snapctl init"
    [[ -f "${RESTIC_PASSWORD_FILE}" ]] || die "Password file no existe: ${RESTIC_PASSWORD_FILE}"
}

ensure_dirs() {
    mkdir -p "$(dirname "${RESTIC_PASSWORD_FILE}")" "${LOG_DIR}" "$(dirname "${DB_PATH}")"
}

# ---------- Notificaciones ----------
notify() {
    local subject="$1" body="$2"
    if [[ -n "${NOTIFY_EMAIL:-}" ]] && command -v mail >/dev/null 2>&1; then
        printf '%s\n' "$body" | mail -s "[snapshot-V3] $subject" "$NOTIFY_EMAIL" || true
    fi
    if [[ -n "${NOTIFY_WEBHOOK:-}" ]] && command -v curl >/dev/null 2>&1; then
        curl -fsS -X POST -H 'Content-Type: application/json' \
            -d "$(printf '{"subject":"%s","body":"%s","host":"%s"}' "$subject" "$body" "$HOSTNAME")" \
            "$NOTIFY_WEBHOOK" >/dev/null 2>&1 || true
    fi
}

# ---------- JSON helpers ----------
json_out() {
    # Uso: json_out "status=ok" "msg=creado" "id=abc123"
    python3 - "$@" <<'PY'
import json, sys
d = {}
for arg in sys.argv[1:]:
    if "=" in arg:
        k, v = arg.split("=", 1)
        d[k] = v
print(json.dumps(d))
PY
}
