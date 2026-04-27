#!/usr/bin/env bash
# Helpers para enviar heartbeats al central. Uso:
#   central_send_archive ok|fail "$op_meta"

central_enabled() {
    [[ -n "${CENTRAL_URL:-}" && -n "${CENTRAL_TOKEN:-}" ]]
}

# Genera UUID v4 sin depender de uuidgen ausente
_uuid4() {
    if command -v uuidgen >/dev/null 2>&1; then uuidgen; return; fi
    python3 -c "import uuid; print(uuid.uuid4())"
}

# Args (12): status, op, category, subkey, label, started_at, duration_s,
# size_bytes, total_bytes, count_files, remote_path, error
central_send() {
    central_enabled || return 0
    local status="$1" op="$2" cat="$3" subkey="$4" label="$5"
    local started="$6" duration="$7" size="$8" total="$9" count="${10}"
    local remote="${11}" err="${12}"
    local tmp; tmp="$(mktemp)"
    cat >"$tmp" <<EOF
{
  "event_id": "$(_uuid4)",
  "ts": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "client": {
    "proyecto": "${BACKUP_PROYECTO:-}",
    "entorno": "${BACKUP_ENTORNO:-}",
    "pais": "${BACKUP_PAIS:-}"
  },
  "target": {"category": "${cat}", "subkey": "${subkey}", "label": "${label}"},
  "operation": {"op": "${op}", "status": "${status}",
                "started_at": "${started}", "duration_s": ${duration:-0},
                "error": ${err:-null}},
  "snapshot": {"size_bytes": ${size:-0}, "remote_path": "${remote}", "encrypted": ${ENCRYPTED:-false}},
  "totals": {"size_bytes": ${total:-0}, "count_files": ${count:-0},
             "oldest_ts": null, "newest_ts": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"},
  "host_meta": {"hostname": "$(hostname)", "snapctl_version": "${SNAPCTL_VERSION:-dev}",
                "rclone_version": "$(rclone version 2>/dev/null | head -1 | awk '{print $2}')"}
}
EOF
    "${PYTHON_BIN:-/opt/snapshot-V3/.venv/bin/python}" \
        -m backend.central.cli send "$tmp" >/dev/null 2>&1
    local rc=$?
    rm -f "$tmp"
    return $rc
}
