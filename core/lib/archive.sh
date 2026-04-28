#!/usr/bin/env bash
# ===========================================================
# archive.sh — backup mensual cold-storage
# -----------------------------------------------------------
# Genera un archivo .tar.zst (opcionalmente encriptado con AES-256-CBC
# + PBKDF2) por operación y lo sube al shared Drive en una ruta
# taxonómica construida desde BACKUP_PROYECTO/ENTORNO/PAIS/NOMBRE.
#
# Ejemplo:
#   superaccess-uno/local/peru/os/linux/lima/2026/04/01/
#     servidor_lima_20260401_020015.tar.zst.enc
#
# Diseño: streaming puro — tar | zstd | [openssl] | rclone rcat.
# No usa disco local temporal. Escala a servidores de decenas de GB
# siempre que haya ancho de banda.
# ===========================================================

# Requiere que common.sh + drive.sh ya estén sourceados
# (log_info, die, $RCLONE_CONFIG, $RCLONE_REMOTE, $HOSTNAME, etc.)

# ----------------------- helpers -----------------------

_archive_validate_taxonomy() {
    local missing=()
    [[ -n "${BACKUP_PROYECTO:-}" ]] || missing+=(BACKUP_PROYECTO)
    [[ -n "${BACKUP_ENTORNO:-}"  ]] || missing+=(BACKUP_ENTORNO)
    [[ -n "${BACKUP_PAIS:-}"     ]] || missing+=(BACKUP_PAIS)
    if [[ ${#missing[@]} -gt 0 ]]; then
        die "Taxonomía incompleta en snapshot.local.conf — faltan: ${missing[*]}. Configúralo en el panel (Ajustes → Backup mensual)."
    fi
}

_archive_remote_base() {
    # Raíz del árbol de este cliente (sin la fecha/archivo).
    # Ej: superaccess-uno/local/peru/os/linux/lima
    local nombre="${BACKUP_NOMBRE:-$HOSTNAME}"
    printf '%s/%s/%s/os/linux/%s' \
        "$BACKUP_PROYECTO" "$BACKUP_ENTORNO" "$BACKUP_PAIS" "$nombre"
}

_archive_build_path() {
    # Ruta completa del archivo para un timestamp dado (UTC, formato YYYYMMDD_HHMMSS).
    local ts="$1"
    local nombre="${BACKUP_NOMBRE:-$HOSTNAME}"
    local year="${ts:0:4}" month="${ts:4:2}" day="${ts:6:2}"
    local ext="tar.zst"
    if declare -F crypto_extension >/dev/null 2>&1; then
        local crypto_ext; crypto_ext="$(crypto_extension)"
        [[ -n "$crypto_ext" ]] && ext="${ext}.${crypto_ext}"
    elif [[ -n "${ARCHIVE_PASSWORD:-}" ]]; then
        # Fallback si crypto.sh no está sourceado (compat con tests aislados).
        ext="tar.zst.enc"
    fi
    printf '%s/%s/%s/%s/servidor_%s_%s.%s' \
        "$(_archive_remote_base)" "$year" "$month" "$day" \
        "$nombre" "$ts" "$ext"
}

_archive_remote_size() {
    # Devuelve bytes del archivo remoto (0 si no existe / error).
    local path="$1"
    rclone --config "$RCLONE_CONFIG" size --json "${RCLONE_REMOTE}:${path}" 2>/dev/null \
        | python3 -c 'import json,sys
try: d=json.load(sys.stdin); print(d.get("bytes") or 0)
except Exception: print(0)' 2>/dev/null || echo 0
}

_archive_human_bytes() {
    local b="$1"
    python3 -c "
n=float($b)
for u in ('B','KB','MB','GB','TB'):
    if n<1024 or u=='TB': print(f'{n:.1f} {u}' if u!='B' else f'{int(n):,} B'); break
    n/=1024
" 2>/dev/null || echo "${b} B"
}

# ----------------------- comandos -----------------------

cmd_archive() {
    _archive_validate_taxonomy
    require_cmd tar
    require_cmd zstd
    command -v rclone >/dev/null 2>&1 || die "rclone no encontrado en PATH"
    drive_reachable || die "Drive no alcanzable — revisa rclone.conf y conectividad"

    local ts; ts="$(date -u +%Y%m%d_%H%M%S)"
    local remote_path; remote_path="$(_archive_build_path "$ts")"
    local encrypted=false
    [[ -n "${ARCHIVE_PASSWORD:-}" ]] && encrypted=true

    log_info "Archive iniciado → ${RCLONE_REMOTE}:${remote_path} (encrypted=${encrypted})"

    # Construye la lista de paths a empacar. Omite los que no existen.
    local -a paths=()
    for p in $BACKUP_PATHS; do
        if [[ -e "$p" ]]; then
            paths+=("$p")
        else
            log_warn "Ruta no existe, se omite del archive: $p"
        fi
    done
    [[ ${#paths[@]} -gt 0 ]] || die "Ninguna ruta válida en BACKUP_PATHS"

    local start_ts; start_ts="$(date +%s)"
    local rc=0

    # Pipefail: si cualquier etapa falla el pipe entero da el rc de la primera fallida.
    # Sin pipefail, rc=$? sería siempre el último (rclone) y tar/openssl fallidos pasarían silenciosos.
    local save_opts; save_opts="$(set +o | grep pipefail)"
    set -o pipefail

    # tar → zstd → crypto_encrypt_pipe (age|openssl|cat) → rclone rcat.
    # ARCHIVE_PASSWORD se lee via env:VAR (nunca en argv, nunca en ps).
    tar -cf - --warning=no-file-changed --warning=no-file-removed \
            --exclude-from="$EXCLUDES_FILE" "${paths[@]}" 2>/dev/null \
        | zstd -T0 -10 -q \
        | crypto_encrypt_pipe \
        | rclone --config "$RCLONE_CONFIG" rcat "${RCLONE_REMOTE}:${remote_path}" \
        || rc=$?

    eval "$save_opts"

    local dur=$(( $(date +%s) - start_ts ))

    if [[ $rc -eq 0 ]]; then
        local size; size="$(_archive_remote_size "$remote_path")"
        local human; human="$(_archive_human_bytes "$size")"
        log_info "Archive OK (${dur}s, ${human})"
        local _meta
        _meta="$(python3 -c "import json; print(json.dumps({
    'path':'${remote_path}', 'duration_s':${dur}, 'size_bytes':int('${size}'),
    'encrypted':${encrypted^}, 'target':'drive'
}))")"
        notify "Archive mensual OK en $HOSTNAME" \
               "Archivo: ${remote_path} · Tamaño: ${human} · Duración: ${dur}s" \
               "ok" "$_meta"
        write_status_drive "archive" "ok" "$_meta"
        if declare -F central_send >/dev/null 2>&1; then
            local _started_at; _started_at="$(date -u -d @"${start_ts}" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u +%Y-%m-%dT%H:%M:%SZ)"
            ENCRYPTED="${encrypted}" central_send "ok" "archive" "os" "linux" \
                "${HOSTNAME}" "${_started_at}" "${dur}" "${size}" "${size}" "1" \
                "${remote_path}" "null" || true
        fi
    else
        log_error "Archive falló rc=$rc (${dur}s)"
        # Intentar limpiar el archivo remoto parcial si se creó
        rclone --config "$RCLONE_CONFIG" deletefile "${RCLONE_REMOTE}:${remote_path}" 2>/dev/null || true
        local _meta
        _meta="$(python3 -c "import json; print(json.dumps({
    'path':'${remote_path}', 'duration_s':${dur}, 'error':'pipe rc=${rc}',
    'encrypted':${encrypted^}, 'target':'drive'
}))")"
        notify "Archive FALLÓ en $HOSTNAME" \
               "Ruta destino: ${remote_path} · rc=${rc} · Duración: ${dur}s" \
               "fail" "$_meta"
        write_status_drive "archive" "fail" "$_meta"
        if declare -F central_send >/dev/null 2>&1; then
            local _started_at; _started_at="$(date -u -d @"${start_ts}" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u +%Y-%m-%dT%H:%M:%SZ)"
            ENCRYPTED="${encrypted}" central_send "fail" "archive" "os" "linux" \
                "${HOSTNAME}" "${_started_at}" "${dur}" "0" "0" "0" \
                "${remote_path}" "\"pipe rc=${rc}\"" || true
        fi
        return "$rc"
    fi
}

cmd_archive_list() {
    _archive_validate_taxonomy
    command -v rclone >/dev/null 2>&1 || die "rclone no encontrado"
    local base; base="$(_archive_remote_base)"
    local json=0
    [[ "${1:-}" == "--json" ]] && json=1

    local raw
    raw="$(rclone --config "$RCLONE_CONFIG" lsjson -R --files-only \
        "${RCLONE_REMOTE}:${base}" 2>/dev/null || echo '[]')"

    if [[ $json -eq 1 ]]; then
        printf '%s' "$raw" | python3 -c '
import json, sys
items = json.load(sys.stdin)
items = [i for i in items if i.get("Name","").startswith("servidor_")]
items.sort(key=lambda x: x.get("ModTime",""), reverse=True)
print(json.dumps(items, indent=2))
'
    else
        printf '%s' "$raw" | python3 -c '
import json, sys
items = json.load(sys.stdin)
items = [i for i in items if i.get("Name","").startswith("servidor_")]
items.sort(key=lambda x: x.get("ModTime",""), reverse=True)
if not items:
    print("(sin archives)")
    sys.exit(0)
print(f"{\"FECHA\":20} {\"TAMAÑO\":>10}  RUTA")
print("-"*90)
for i in items:
    mb = i.get("Size",0) / (1024*1024)
    ts = i.get("ModTime","")[:19].replace("T"," ")
    print(f"{ts:20} {mb:>9.1f}MB  {i[\"Path\"]}")
'
    fi
}

cmd_archive_restore() {
    local remote_path="" target=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --target) target="$2"; shift 2 ;;
            -h|--help)
                echo "Uso: snapctl archive-restore <remote-path> --target <directorio>"
                echo "  <remote-path>: ruta dentro del shared Drive (de 'snapctl archive-list')"
                return 0 ;;
            *) remote_path="$1"; shift ;;
        esac
    done
    [[ -n "$remote_path" ]] || die "Falta la ruta del archivo remoto. Uso: snapctl archive-restore <path> --target <dir>"
    [[ -n "$target" ]] || die "Falta --target <directorio>"
    mkdir -p "$target"

    require_cmd tar
    require_cmd zstd
    command -v rclone >/dev/null 2>&1 || die "rclone no encontrado"

    local encrypted=false
    [[ "$remote_path" == *.enc ]] && encrypted=true

    if $encrypted; then
        [[ -n "${ARCHIVE_PASSWORD:-}" ]] || \
            die "El archivo $remote_path está encriptado pero ARCHIVE_PASSWORD no está en snapshot.local.conf. Configúrala con la password con que se encriptó."
    fi

    log_info "Archive-restore ${remote_path} → ${target} (encrypted=${encrypted})"
    local start_ts; start_ts="$(date +%s)"

    local save_opts; save_opts="$(set +o | grep pipefail)"
    set -o pipefail
    local rc=0

    # crypto_decrypt_for_path detecta por extensión (.age, .enc) y elige
    # la herramienta. Si el path no termina en ninguna, hace passthrough.
    rclone --config "$RCLONE_CONFIG" cat "${RCLONE_REMOTE}:${remote_path}" \
        | crypto_decrypt_for_path "$remote_path" \
        | zstd -d -T0 \
        | tar -xf - -C "$target" \
        || rc=$?

    eval "$save_opts"
    local dur=$(( $(date +%s) - start_ts ))

    if [[ $rc -eq 0 ]]; then
        log_info "Archive-restore OK → ${target} (${dur}s)"
    else
        log_error "Archive-restore falló rc=$rc (${dur}s) — si ves 'bad decrypt', la password es incorrecta."
        return "$rc"
    fi
}

cmd_archive_prune() {
    _archive_validate_taxonomy
    command -v rclone >/dev/null 2>&1 || die "rclone no encontrado"
    local keep="${ARCHIVE_KEEP_MONTHS:-12}"
    local dry=0
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --keep-months) keep="$2"; shift 2 ;;
            --dry-run) dry=1; shift ;;
            *) die "Opción desconocida: $1" ;;
        esac
    done
    local base; base="$(_archive_remote_base)"
    log_info "Archive-prune en ${RCLONE_REMOTE}:${base} (conserva últimos ${keep} meses${dry:+ · dry-run})"

    local args=(--config "$RCLONE_CONFIG" delete --min-age "${keep}M")
    [[ $dry -eq 1 ]] && args+=(--dry-run)
    rclone "${args[@]}" "${RCLONE_REMOTE}:${base}" 2>&1 \
        | grep -v '^$' | tail -20 || true
    log_info "Archive-prune completado"
}

cmd_archive_paths() {
    # Imprime paths de configuración por si el usuario quiere verificar.
    _archive_validate_taxonomy
    echo "Proyecto:    ${BACKUP_PROYECTO}"
    echo "Entorno:     ${BACKUP_ENTORNO}"
    echo "País:        ${BACKUP_PAIS}"
    echo "Nombre:      ${BACKUP_NOMBRE:-$HOSTNAME}"
    echo "Encriptado:  $([[ -n "${ARCHIVE_PASSWORD:-}" ]] && echo 'sí (AES-256-CBC + PBKDF2)' || echo 'no')"
    echo ""
    echo "Raíz del host:    ${RCLONE_REMOTE}:$(_archive_remote_base)/"
    echo "Próximo archivo:  ${RCLONE_REMOTE}:$(_archive_build_path "$(date -u +%Y%m%d_%H%M%S)")"
}
