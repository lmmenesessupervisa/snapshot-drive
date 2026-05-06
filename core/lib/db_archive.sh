#!/usr/bin/env bash
# ===========================================================
# db_archive.sh — backup de bases de datos (Postgres/MySQL/Mongo)
# -----------------------------------------------------------
# Streaming puro: <dump_cmd> | zstd | [openssl] | rclone rcat
# Config en snapshot.local.conf:
#   DB_BACKUP_TARGETS="postgres:mydb mysql:web mongo:metrics"
# Heartbeat por cada target con category=db, subkey=<engine>, label=<dbname>.
# ===========================================================

# Requiere common.sh + drive.sh + archive.sh + central.sh sourceados.

# Engine válidos
_DB_ENGINES_RE='^(postgres|mysql|mongo)$'

# Path remoto: PROYECTO/ENTORNO/PAIS/db/<engine>/<dbname>/YYYY/MM/DD/servidor_<dbname>_<TS>.<ext>
_db_remote_base() {
    local engine="$1" dbname="$2"
    printf '%s/%s/%s/db/%s/%s' \
        "$BACKUP_PROYECTO" "$BACKUP_ENTORNO" "$BACKUP_PAIS" \
        "$engine" "$dbname"
}

_db_build_path() {
    local engine="$1" dbname="$2" ts="$3"
    local year="${ts:0:4}" month="${ts:4:2}" day="${ts:6:2}"
    local ext
    case "$engine" in
        postgres|mysql) ext="sql.zst" ;;
        mongo)          ext="archive.zst" ;;
        *)              ext="dump.zst" ;;
    esac
    if declare -F crypto_extension >/dev/null 2>&1; then
        local crypto_ext; crypto_ext="$(crypto_extension)"
        [[ -n "$crypto_ext" ]] && ext="${ext}.${crypto_ext}"
    elif [[ -n "${ARCHIVE_PASSWORD:-}" ]]; then
        ext="${ext}.enc"
    fi
    printf '%s/%s/%s/%s/servidor_%s_%s.%s' \
        "$(_db_remote_base "$engine" "$dbname")" \
        "$year" "$month" "$day" "$dbname" "$ts" "$ext"
}

# Construye el comando dump según engine. Imprime a stdout.
# La password NUNCA va en argv; solo via env.
_db_dump_cmd() {
    local engine="$1" dbname="$2"
    case "$engine" in
        postgres)
            local pg_args=(--no-owner --no-acl --quote-all-identifiers)
            [[ -n "${DB_PG_HOST:-}" ]] && pg_args+=(--host "$DB_PG_HOST")
            [[ -n "${DB_PG_PORT:-}" ]] && pg_args+=(--port "$DB_PG_PORT")
            [[ -n "${DB_PG_USER:-}" ]] && pg_args+=(--username "$DB_PG_USER")
            pg_args+=("$dbname")
            PGPASSWORD="${DB_PG_PASSWORD:-}" pg_dump "${pg_args[@]}"
            ;;
        mysql)
            local my_args=(--single-transaction --quick --skip-lock-tables \
                           --routines --triggers --events)
            [[ -n "${DB_MYSQL_HOST:-}" ]] && my_args+=("-h$DB_MYSQL_HOST")
            [[ -n "${DB_MYSQL_PORT:-}" ]] && my_args+=("-P$DB_MYSQL_PORT")
            [[ -n "${DB_MYSQL_USER:-}" ]] && my_args+=("-u$DB_MYSQL_USER")
            my_args+=("$dbname")
            MYSQL_PWD="${DB_MYSQL_PASSWORD:-}" mysqldump "${my_args[@]}"
            ;;
        mongo)
            [[ -n "${DB_MONGO_URI:-}" ]] || { log_error "DB_MONGO_URI vacío"; return 2; }
            mongodump --uri="$DB_MONGO_URI" --archive --db="$dbname"
            ;;
    esac
}

# Verifica que el binario del engine esté disponible.
_db_engine_available() {
    case "$1" in
        postgres) command -v pg_dump >/dev/null 2>&1 ;;
        mysql)    command -v mysqldump >/dev/null 2>&1 ;;
        mongo)    command -v mongodump >/dev/null 2>&1 ;;
        *)        return 1 ;;
    esac
}

# ----------------------- comando principal -----------------------

cmd_db_archive() {
    _archive_validate_taxonomy
    require_cmd zstd
    command -v rclone >/dev/null 2>&1 || die "rclone no encontrado"
    drive_reachable || die "Drive no alcanzable"

    [[ -n "${DB_BACKUP_TARGETS:-}" ]] || die "DB_BACKUP_TARGETS vacío en snapshot.local.conf"

    # Filtros opcionales: --engine NAME (repetible) y --target ENGINE:DBNAME
    # (repetible). Sin filtros corre todos los targets configurados.
    local -a only_engines=()
    local -a only_targets=()
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --engine) only_engines+=("$2"); shift 2 ;;
            --target) only_targets+=("$2"); shift 2 ;;
            *) shift ;;
        esac
    done

    _engine_allowed() {
        [[ ${#only_engines[@]} -eq 0 ]] && return 0
        local e="$1"
        for x in "${only_engines[@]}"; do [[ "$x" == "$e" ]] && return 0; done
        return 1
    }
    _target_allowed() {
        [[ ${#only_targets[@]} -eq 0 ]] && return 0
        local t="$1"
        for x in "${only_targets[@]}"; do [[ "$x" == "$t" ]] && return 0; done
        return 1
    }

    local fail_count=0 ok_count=0 skipped=0
    for tok in $DB_BACKUP_TARGETS; do
        [[ "$tok" == *:* ]] || { log_warn "target malformado: $tok"; fail_count=$((fail_count+1)); continue; }
        local engine="${tok%%:*}"
        local dbname="${tok#*:}"
        if [[ ! "$engine" =~ $_DB_ENGINES_RE ]]; then
            log_warn "engine no soportado: $engine"; fail_count=$((fail_count+1)); continue
        fi
        if [[ -z "$dbname" || "$dbname" == *[!A-Za-z0-9._-]* ]]; then
            log_warn "dbname inválido: $dbname"; fail_count=$((fail_count+1)); continue
        fi
        if ! _engine_allowed "$engine" || ! _target_allowed "$tok"; then
            skipped=$((skipped+1)); continue
        fi
        if ! _db_engine_available "$engine"; then
            log_warn "engine $engine: tool ausente, target $tok salteado"
            fail_count=$((fail_count+1)); continue
        fi
        if _db_archive_target "$engine" "$dbname"; then
            ok_count=$((ok_count+1))
        else
            fail_count=$((fail_count+1))
        fi
    done

    log_info "DB archive: ${ok_count} ok, ${fail_count} fail${skipped:+, ${skipped} skipped}"
    [[ $fail_count -eq 0 ]] || return 1
}


# ----------------------- check connection -----------------------
# Prueba conexión + autenticación a un engine, sin hacer dump.
# Devuelve JSON: {ok: bool, engine, latency_ms?, error?}.
# Usa las credenciales de local.conf, salvo que se pase --password VAL
# (caso "valida antes de guardar" desde la UI).
cmd_db_archive_check() {
    local engine="${1:-}"
    shift || true
    [[ -n "$engine" ]] || { echo '{"ok":false,"error":"engine requerido"}'; return 2; }
    local password_override=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --password) password_override="$2"; shift 2 ;;
            *) shift ;;
        esac
    done

    if [[ ! "$engine" =~ $_DB_ENGINES_RE ]]; then
        echo "{\"ok\":false,\"engine\":\"${engine}\",\"error\":\"engine no soportado\"}"
        return 2
    fi
    if ! _db_engine_available "$engine"; then
        echo "{\"ok\":false,\"engine\":\"${engine}\",\"error\":\"herramienta del engine ausente en este host\"}"
        return 2
    fi

    local t_start_ms; t_start_ms="$(date +%s%3N)"
    local err_msg="" rc=0
    case "$engine" in
        postgres)
            local pwd="${password_override:-${DB_PG_PASSWORD:-}}"
            command -v psql >/dev/null 2>&1 || {
                echo "{\"ok\":false,\"engine\":\"postgres\",\"error\":\"psql ausente\"}"
                return 2
            }
            local pg_args=()
            [[ -n "${DB_PG_HOST:-}" ]] && pg_args+=(-h "$DB_PG_HOST")
            [[ -n "${DB_PG_PORT:-}" ]] && pg_args+=(-p "$DB_PG_PORT")
            [[ -n "${DB_PG_USER:-}" ]] && pg_args+=(-U "$DB_PG_USER")
            err_msg="$(PGPASSWORD="$pwd" psql -tAq "${pg_args[@]}" -d postgres -c "SELECT 1" 2>&1 >/dev/null)"
            rc=$?
            ;;
        mysql)
            local pwd="${password_override:-${DB_MYSQL_PASSWORD:-}}"
            command -v mysqladmin >/dev/null 2>&1 || {
                echo "{\"ok\":false,\"engine\":\"mysql\",\"error\":\"mysqladmin ausente\"}"
                return 2
            }
            local my_args=(--connect-timeout=5)
            [[ -n "${DB_MYSQL_HOST:-}" ]] && my_args+=("-h$DB_MYSQL_HOST")
            [[ -n "${DB_MYSQL_PORT:-}" ]] && my_args+=("-P$DB_MYSQL_PORT")
            [[ -n "${DB_MYSQL_USER:-}" ]] && my_args+=("-u$DB_MYSQL_USER")
            err_msg="$(MYSQL_PWD="$pwd" mysqladmin "${my_args[@]}" ping 2>&1 >/dev/null)"
            rc=$?
            ;;
        mongo)
            local uri="${DB_MONGO_URI:-}"
            [[ -n "$password_override" ]] && uri="${uri/${DB_MONGO_PASSWORD:-__placeholder__}/$password_override}"
            [[ -n "$uri" ]] || {
                echo "{\"ok\":false,\"engine\":\"mongo\",\"error\":\"DB_MONGO_URI vacío\"}"
                return 2
            }
            local mongo_bin
            mongo_bin="$(command -v mongosh || command -v mongo)" || {
                echo "{\"ok\":false,\"engine\":\"mongo\",\"error\":\"mongosh/mongo ausente\"}"
                return 2
            }
            err_msg="$("$mongo_bin" --quiet "$uri" --eval 'db.runCommand({ping:1})' 2>&1 >/dev/null)"
            rc=$?
            ;;
    esac
    local t_end_ms; t_end_ms="$(date +%s%3N)"
    local lat=$((t_end_ms - t_start_ms))
    if [[ $rc -eq 0 ]]; then
        echo "{\"ok\":true,\"engine\":\"${engine}\",\"latency_ms\":${lat}}"
        return 0
    fi
    # Sanitiza el error para JSON: una línea, sin comillas dobles, ≤ 300 chars
    local clean
    clean="$(printf '%s' "$err_msg" | tr '\n' ' ' | sed 's/"/\\"/g' | cut -c1-300)"
    echo "{\"ok\":false,\"engine\":\"${engine}\",\"latency_ms\":${lat},\"error\":\"${clean}\"}"
    return 1
}

_db_archive_target() {
    local engine="$1" dbname="$2"
    local ts; ts="$(date -u +%Y%m%d_%H%M%S)"
    local remote_path; remote_path="$(_db_build_path "$engine" "$dbname" "$ts")"
    local encrypted=false
    [[ -n "${ARCHIVE_PASSWORD:-}" ]] && encrypted=true

    log_info "DB archive: ${engine}:${dbname} → ${remote_path} (encrypted=${encrypted})"

    local start_ts; start_ts="$(date +%s)"
    local rc=0
    local save_opts; save_opts="$(set +o | grep pipefail)"
    set -o pipefail

    _db_dump_cmd "$engine" "$dbname" 2>/dev/null \
        | zstd -T0 -10 -q \
        | crypto_encrypt_pipe \
        | rclone --config "$RCLONE_CONFIG" rcat "${RCLONE_REMOTE}:${remote_path}" \
        || rc=$?
    eval "$save_opts"

    local dur=$(( $(date +%s) - start_ts ))
    local _started_at; _started_at="$(date -u -d @"${start_ts}" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u +%Y-%m-%dT%H:%M:%SZ)"

    if [[ $rc -eq 0 ]]; then
        local size; size="$(_archive_remote_size "$remote_path")"
        log_info "DB archive OK: ${engine}:${dbname} (${dur}s, ${size}B)"
        if declare -F central_send >/dev/null 2>&1; then
            ENCRYPTED="${encrypted}" central_send "ok" "db-archive" \
                "db" "${engine}" "${dbname}" "${_started_at}" "${dur}" \
                "${size}" "${size}" "1" "${remote_path}" "null" || true
        fi
        return 0
    else
        log_error "DB archive FAIL: ${engine}:${dbname} rc=${rc} (${dur}s)"
        rclone --config "$RCLONE_CONFIG" deletefile "${RCLONE_REMOTE}:${remote_path}" 2>/dev/null || true
        if declare -F central_send >/dev/null 2>&1; then
            ENCRYPTED="${encrypted}" central_send "fail" "db-archive" \
                "db" "${engine}" "${dbname}" "${_started_at}" "${dur}" \
                "0" "0" "0" "${remote_path}" "\"pipe rc=${rc}\"" || true
        fi
        return "$rc"
    fi
}

# ----------------------- list -----------------------

cmd_db_archive_list() {
    _archive_validate_taxonomy
    command -v rclone >/dev/null 2>&1 || die "rclone no encontrado"
    local base="${BACKUP_PROYECTO}/${BACKUP_ENTORNO}/${BACKUP_PAIS}/db"
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
    print("(sin db archives)")
    sys.exit(0)
print(f"{\"FECHA\":20} {\"TAMANO\":>10}  RUTA")
print("-"*90)
for i in items:
    mb = i.get("Size",0) / (1024*1024)
    ts = i.get("ModTime","")[:19].replace("T"," ")
    print(f"{ts:20} {mb:>9.1f}MB  {i[\"Path\"]}")
'
    fi
}

# ----------------------- restore -----------------------

cmd_db_archive_restore() {
    local remote_path="" target=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --target) target="$2"; shift 2 ;;
            -*) die "Opción desconocida: $1" ;;
            *)  remote_path="$1"; shift ;;
        esac
    done
    [[ -n "$remote_path" ]] || die "Uso: snapctl db-archive-restore <remote_path> [--target conn]"

    # Detecta engine por la subcarpeta /db/<engine>/
    local engine=""
    if [[ "$remote_path" == *"/db/postgres/"* ]]; then engine="postgres"
    elif [[ "$remote_path" == *"/db/mysql/"* ]];   then engine="mysql"
    elif [[ "$remote_path" == *"/db/mongo/"* ]];   then engine="mongo"
    else die "No puedo inferir engine del path: $remote_path"
    fi

    local encrypted=false
    [[ "$remote_path" == *".enc" ]] && encrypted=true

    if [[ -z "$target" ]]; then
        echo "DRY-RUN: imprime el comando que se ejecutaría con --target."
        echo "  rclone cat ${RCLONE_REMOTE}:${remote_path} | "
        if $encrypted; then
            echo "    openssl enc -d -aes-256-cbc -pbkdf2 -iter 100000 -pass env:ARCHIVE_PASSWORD | "
        fi
        echo "    zstd -dc | <restore_cmd para ${engine}>"
        echo
        echo "Pasale --target <conn> para ejecutar (ej: --target mydb_restore)."
        return 0
    fi

    require_cmd zstd
    command -v rclone >/dev/null 2>&1 || die "rclone no encontrado"

    local restore_cmd
    case "$engine" in
        postgres) restore_cmd="psql ${target}" ;;
        mysql)    restore_cmd="mysql ${target}" ;;
        mongo)    restore_cmd="mongorestore --archive --uri=\"${target}\" --drop" ;;
    esac

    log_info "DB restore: ${remote_path} → ${restore_cmd}"
    local save_opts; save_opts="$(set +o | grep pipefail)"
    set -o pipefail

    rclone --config "$RCLONE_CONFIG" cat "${RCLONE_REMOTE}:${remote_path}" \
        | crypto_decrypt_for_path "$remote_path" \
        | zstd -dc \
        | bash -c "$restore_cmd"
    local rc=$?
    eval "$save_opts"
    [[ $rc -eq 0 ]] && log_info "DB restore OK" || log_error "DB restore FAIL rc=$rc"
    return $rc
}
