#!/usr/bin/env bash
# ===========================================================
# snapshot-V3 — librería común (logging, utilidades, validaciones)
# ===========================================================

set -Eeuo pipefail

SNAPSHOT_ROOT="${SNAPSHOT_ROOT:-/opt/snapshot-V3}"
CONF_FILE="${CONF_FILE:-${SNAPSHOT_ROOT}/core/etc/snapshot.conf}"

# Binarios bundled (standalone Python + restic + rclone) descargados por
# install.sh a $SNAPSHOT_ROOT/bundle/. Los anteponemos al PATH para que
# snapctl use EXCLUSIVAMENTE estos y no dependa del restic/rclone/python
# del host (evita sorpresas por upgrades de apt o versiones divergentes).
if [[ -d "${SNAPSHOT_ROOT}/bundle/bin" ]]; then
    PATH="${SNAPSHOT_ROOT}/bundle/bin:${SNAPSHOT_ROOT}/bundle/python/bin:$PATH"
    export PATH
fi

# shellcheck disable=SC1090
[[ -f "$CONF_FILE" ]] && source "$CONF_FILE"

# Override local por instalación (credenciales OAuth, tuning por sitio).
# Vive FUERA del árbol de código en /etc/snapshot-v3/ para sobrevivir a
# upgrades (install.sh hace rsync --delete sobre $SNAPSHOT_ROOT y se comería
# cualquier archivo local que no esté en el repo).
LOCAL_CONF="${LOCAL_CONF:-/etc/snapshot-v3/snapshot.local.conf}"
# shellcheck disable=SC1090
[[ -f "$LOCAL_CONF" ]] && source "$LOCAL_CONF"

export RESTIC_REPOSITORY="${RESTIC_REPO}"
export RESTIC_PASSWORD_FILE="${RESTIC_PASSWORD_FILE}"

# Export para que snapctl-notify (subproceso Python) herede las credenciales
# de SMTP. Variables sourceadas con `source` quedan en el shell pero no se
# propagan a hijos sin export explícito — sin esto el notify salía sin
# enviar correo silenciosamente.
export SMTP_HOST="${SMTP_HOST:-}"
export SMTP_PORT="${SMTP_PORT:-587}"
export SMTP_USER="${SMTP_USER:-}"
export SMTP_PASSWORD="${SMTP_PASSWORD:-}"
export SMTP_FROM="${SMTP_FROM:-}"
export NOTIFY_EMAIL="${NOTIFY_EMAIL:-}"
export HOSTNAME="${HOSTNAME:-$(hostname -s)}"

# ---------- Tuning rclone (se heredan por los subprocess de restic) -----
# rclone lee automáticamente variables RCLONE_<FLAG> mayúsculas, así que
# exportarlas aquí basta para que restic → rclone use estos valores sin
# tocar más la línea de comandos.
#
# PERFIL (RCLONE_PROFILE): auto | personal | shared
#   auto (default)  → detecta team_drive en rclone.conf; shared si existe.
#   personal        → conservador, evita 403 userRateLimitExceeded en Drive personal.
#   shared          → agresivo; los Shared Drives aguantan mucho más throughput.
# El usuario puede seguir overrideando variable a variable en snapshot.local.conf
# (p.ej. fijar RCLONE_TPSLIMIT=12 manualmente) — los defaults del perfil solo
# rellenan lo que esté sin definir.
_snapshot_rclone_profile="${RCLONE_PROFILE:-auto}"
if [[ "$_snapshot_rclone_profile" == "auto" ]]; then
    if [[ -f "${RCLONE_CONFIG:-}" ]] && \
       grep -qE '^[[:space:]]*team_drive[[:space:]]*=[[:space:]]*\S' "${RCLONE_CONFIG}" 2>/dev/null; then
        _snapshot_rclone_profile="shared"
    else
        _snapshot_rclone_profile="personal"
    fi
fi
if [[ "$_snapshot_rclone_profile" == "shared" ]]; then
    # Shared Drive: Google permite ~100 qps sostenidas. Paralelización alta
    # reduce el overhead de latencia (cada API call ~200-400ms en Drive).
    _def_transfers=6; _def_checkers=12; _def_tps=20; _def_burst=40; _def_pacer="10ms"
else
    # Drive personal: ~10 qps antes de 403. Pacer de 100ms previene backoff
    # exponencial de Google (puede dejar un upload "colgado" horas).
    _def_transfers=2; _def_checkers=4;  _def_tps=5;  _def_burst=10; _def_pacer="100ms"
fi

export RCLONE_DRIVE_CHUNK_SIZE="${RCLONE_DRIVE_CHUNK_SIZE:-64M}"
export RCLONE_DRIVE_PACER_MIN_SLEEP="${RCLONE_PACER_MIN_SLEEP:-$_def_pacer}"
export RCLONE_DRIVE_USE_TRASH="${RCLONE_DRIVE_USE_TRASH:-false}"
export RCLONE_TRANSFERS="${RCLONE_TRANSFERS:-$_def_transfers}"
export RCLONE_CHECKERS="${RCLONE_CHECKERS:-$_def_checkers}"
export RCLONE_TPSLIMIT="${RCLONE_TPSLIMIT:-$_def_tps}"
export RCLONE_TPSLIMIT_BURST="${RCLONE_TPSLIMIT_BURST:-$_def_burst}"
export RCLONE_CONTIMEOUT="${RCLONE_CONTIMEOUT:-30s}"
export RCLONE_TIMEOUT="${RCLONE_TIMEOUT:-300s}"
export RCLONE_LOW_LEVEL_RETRIES="${RCLONE_LOW_LEVEL_RETRIES:-10}"
export RCLONE_RETRIES="${RCLONE_RETRIES:-5}"
[[ -n "${RCLONE_BWLIMIT:-}" ]] && export RCLONE_BWLIMIT
# Exportar el perfil efectivo para que snapctl lo pueda imprimir en status/logs.
export RCLONE_PROFILE_EFFECTIVE="$_snapshot_rclone_profile"
unset _snapshot_rclone_profile _def_transfers _def_checkers _def_tps _def_burst _def_pacer

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
# notify SUBJECT BODY STATUS [META_JSON]
#   STATUS: ok | fail | info   (para color del banner en el correo HTML)
#   META_JSON: metadata estructurada {tag, duration_s, size_bytes, snapshot_id,
#             target, error} — se pinta como tabla en el correo.
notify() {
    local subject="$1" body="$2" status="${3:-info}" meta="${4:-{}}"
    # SMTP via Python script (no depende de mailutils, usa el Python bundled)
    if [[ -n "${SMTP_HOST:-}" && -n "${NOTIFY_EMAIL:-}" && -x "${SNAPSHOT_ROOT}/core/bin/snapctl-notify" ]]; then
        local _err
        _err="$(printf '%s\n' "$body" | \
            SNAPSHOT_NOTIFY_STATUS="$status" SNAPSHOT_NOTIFY_META="$meta" \
            "${SNAPSHOT_ROOT}/core/bin/snapctl-notify" "$subject" 2>&1 >/dev/null)" || true
        [[ -n "$_err" ]] && log_warn "snapctl-notify: $_err"
    fi
    # Webhook JSON (opcional)
    if [[ -n "${NOTIFY_WEBHOOK:-}" ]] && command -v curl >/dev/null 2>&1; then
        curl -fsS -X POST -H 'Content-Type: application/json' \
            -d "$(printf '{"subject":"%s","body":"%s","host":"%s","status":"%s","meta":%s}' \
                  "$subject" "$body" "$HOSTNAME" "$status" "$meta")" \
            "$NOTIFY_WEBHOOK" >/dev/null 2>&1 || true
    fi
}

# ---------- Status JSON en Drive (fuente de la vista /audit) ----------
# Escribe /$AUDIT_REMOTE_PATH/<hostname>.json en el shared Drive con el
# estado de la última operación + historial corto. La vista agregada en
# la máquina ops lee estos JSON para pintar el dashboard.
#
# Si Drive no está vinculado o rclone no está disponible, es un no-op silencioso.
#
# Uso: write_status_drive OP STATUS META_JSON
#   OP:     create | reconcile | prune | init
#   STATUS: ok | fail | running
#   META:   JSON {tag, duration_s, size_bytes, snapshot_id, target, error}
write_status_drive() {
    local op="$1" status="$2" meta="${3:-{}}"

    # Gate: Drive debe estar vinculado (rclone.conf tiene token)
    [[ -f "${RCLONE_CONFIG:-}" ]] || return 0
    command -v rclone >/dev/null 2>&1 || return 0
    _ini_read "${RCLONE_REMOTE:-gdrive}" "token" 2>/dev/null | grep -q "access_token" || return 0

    # Ruta del audit dentro del shared Drive. Convención: la raíz del árbol
    # del cliente (snapshots/<hostname>/) tiene un parent — ahí va el JSON.
    #    shared/snapshots/<hostname>/       ← restic repo
    #    shared/snapshots/_status/<host>.json ← metadata nuestra
    local parent="${RCLONE_REMOTE_PATH%/*}"
    [[ -z "$parent" || "$parent" == "$RCLONE_REMOTE_PATH" ]] && parent=""
    local status_path
    if [[ -n "$parent" ]]; then
        status_path="${parent}/_status/${HOSTNAME}.json"
    else
        status_path="_status/${HOSTNAME}.json"
    fi

    # Componer el JSON nuevo mergeando con el existente (para conservar historial)
    local new_json
    if ! new_json="$(
        existing="$(rclone --config "$RCLONE_CONFIG" cat "${RCLONE_REMOTE}:${status_path}" 2>/dev/null || echo '{}')"
        python3 - "$existing" "$op" "$status" "$meta" "$HOSTNAME" <<'PY'
import json, sys
from datetime import datetime, timezone

existing_raw, op, status, meta_raw, host = sys.argv[1:6]
try: existing = json.loads(existing_raw)
except Exception: existing = {}
try: meta = json.loads(meta_raw) if meta_raw else {}
except Exception: meta = {}

now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
event = {"op": op, "ts": now, "status": status}
event.update(meta)

history = existing.get("history") or []
# Si el evento anterior era "running" del mismo op, lo reemplazamos.
if history and history[0].get("status") == "running" and history[0].get("op") == op:
    history[0] = event
else:
    history.insert(0, event)
history = history[:50]

totals = existing.get("totals") or {}
if status == "ok":
    totals["last_successful_backup_ts"] = now
    if op == "create":
        totals["create_count"] = (totals.get("create_count") or 0) + 1
elif status == "fail":
    totals["last_failure_ts"] = now
    totals["fail_count"] = (totals.get("fail_count") or 0) + 1

out = {
    "host": host,
    "last": event,
    "totals": totals,
    "history": history,
    "updated_ts": now,
}
print(json.dumps(out, indent=2))
PY
    )"; then
        log_warn "No se pudo componer status.json para Drive"
        return 0
    fi

    # Subir: rclone rcat lee stdin y escribe al remoto atómicamente.
    if ! printf '%s' "$new_json" | rclone --config "$RCLONE_CONFIG" rcat "${RCLONE_REMOTE}:${status_path}" 2>/dev/null; then
        log_warn "No se pudo escribir status.json en Drive (${status_path})"
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
