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

# Recolecta el inventario del subarbol de este cliente en Drive y lo emite
# como objeto JSON listo para embeber bajo "inventory". Si esta apagado
# (CENTRAL_PUSH_INVENTORY != 1), si rclone no esta listo, o si el scan
# falla, emite "null". El central tolera "null" sin romperse — en el peor
# caso el operador hace clic en "Refrescar" y el central rehace el scan
# completo desde Drive.
_collect_inventory() {
    [[ "${CENTRAL_PUSH_INVENTORY:-0}" = "1" ]] || { echo "null"; return 0; }
    [[ -n "${BACKUP_PROYECTO:-}" && -n "${BACKUP_ENTORNO:-}" \
        && -n "${BACKUP_PAIS:-}" && -n "${RCLONE_REMOTE:-}" ]] \
        || { echo "null"; return 0; }
    local label="${BACKUP_NOMBRE:-$(hostname -s)}"
    local subtree="${RCLONE_REMOTE}:${BACKUP_PROYECTO}/${BACKUP_ENTORNO}/${BACKUP_PAIS}"
    local rclone_bin="${RCLONE_BIN:-rclone}"
    local rclone_cfg="${RCLONE_CONFIG:-$HOME/.config/rclone/rclone.conf}"
    local tmp_lsjson; tmp_lsjson="$(mktemp)"
    if ! "$rclone_bin" --config "$rclone_cfg" \
            lsjson -R --files-only --no-modtime --fast-list \
            "$subtree" >"$tmp_lsjson" 2>/dev/null; then
        rm -f "$tmp_lsjson"
        echo "null"
        return 0
    fi
    "${PYTHON_BIN:-python3}" - "$label" "$tmp_lsjson" <<'PY'
import json, re, sys, datetime
label, lsjson_path = sys.argv[1], sys.argv[2]
try:
    with open(lsjson_path) as fh:
        items = json.load(fh)
except Exception:
    print("null"); sys.exit(0)
FRE = re.compile(
    r"^servidor_(?P<lbl>[A-Za-z0-9_.\-]+)_(?P<ts>\d{8}_\d{6})\."
    r"(?P<ext>(?:tar|sql|archive)\.zst(?:\.enc|\.age)?)$"
)
MAX_LEAVES, MAX_FILES = 64, 200
leaves: dict = {}
files_total = 0
for it in items:
    if it.get("IsDir"):
        continue
    p = it.get("Path") or it.get("Name", "")
    parts = p.split("/")
    if len(parts) < 4:
        continue
    cat, sub, lbl = parts[0], parts[1], parts[2]
    if cat not in ("os", "db") or lbl != label:
        continue
    fname = parts[-1]
    m = FRE.match(fname)
    if not m:
        continue
    ext = m.group("ext")
    enc = ext.endswith(".enc") or ext.endswith(".age")
    crypto = ("age" if ext.endswith(".age")
              else "openssl" if ext.endswith(".enc") else "none")
    if files_total >= MAX_FILES:
        break
    leaves.setdefault((cat, sub), []).append({
        "name": fname,
        "path": p,
        "size": int(it.get("Size") or 0),
        "ts": m.group("ts"),
        "encrypted": enc,
        "crypto": crypto,
    })
    files_total += 1
out = []
for (cat, sub), files in list(leaves.items())[:MAX_LEAVES]:
    files.sort(key=lambda f: f["ts"], reverse=True)
    out.append({"category": cat, "subkey": sub, "files": files})
print(json.dumps({
    "scanned_at": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    "leaves": out,
}, separators=(",", ":")))
PY
    local rc=$?
    rm -f "$tmp_lsjson"
    [[ $rc -eq 0 ]] || echo "null"
    return 0
}

# Args (12): status, op, category, subkey, label, started_at, duration_s,
# size_bytes, total_bytes, count_files, remote_path, error
central_send() {
    central_enabled || return 0
    local status="$1" op="$2" cat="$3" subkey="$4" label="$5"
    local started="$6" duration="$7" size="$8" total="$9" count="${10}"
    local remote="${11}" err="${12}"
    local inventory_json; inventory_json="$(_collect_inventory)"
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
                "rclone_version": "$(rclone version 2>/dev/null | head -1 | awk '{print $2}')",
                "missing_paths": ${MISSING_PATHS_JSON:-[]}},
  "inventory": ${inventory_json}
}
EOF
    # PYTHONPATH explícito: sin esto, `python -m backend.central.cli`
    # falla con ModuleNotFoundError cuando central_send se invoca desde
    # snapctl (cwd suele NO ser SNAPSHOT_ROOT). El error queda silencioso
    # porque el redirect a /dev/null lo oculta — dimensión típica del
    # bug "el central no recibe heartbeats aunque local.conf tenga URL+TOKEN".
    PYTHONPATH="${SNAPSHOT_ROOT:-/opt/snapshot-V3}${PYTHONPATH:+:$PYTHONPATH}" \
    "${PYTHON_BIN:-/opt/snapshot-V3/.venv/bin/python}" \
        -m backend.central.cli send "$tmp" >/dev/null 2>&1
    local rc=$?
    rm -f "$tmp"
    return $rc
}
