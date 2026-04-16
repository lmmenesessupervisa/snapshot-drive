#!/usr/bin/env bash
# ===========================================================
# drive.sh — gestión de la vinculación con Google Drive (rclone)
# ===========================================================
# Escribe/lee /var/lib/snapshot-v3/rclone.conf en formato INI.
# El token OAuth es un JSON que el usuario obtiene ejecutando en su
# equipo local (con navegador):
#     rclone authorize "drive"
# y pega el resultado en la UI.
# ===========================================================

set -Eeuo pipefail

# Requiere que haya sido source-ado tras common.sh (variables disponibles)

drive_conf_path() { echo "${RCLONE_CONFIG}"; }

# --- helpers INI ----------------------------------------------------------
_ini_read() {
    # _ini_read <section> <key>  -> imprime valor (vacío si no existe)
    local file="${RCLONE_CONFIG}" section="$1" key="$2"
    [[ -f "$file" ]] || { echo ""; return 0; }
    python3 - "$file" "$section" "$key" <<'PY'
import configparser, sys
p = configparser.ConfigParser(interpolation=None, strict=False)
try: p.read(sys.argv[1])
except Exception: pass
print(p.get(sys.argv[2], sys.argv[3], fallback=""))
PY
}

_ini_write_gdrive() {
    # _ini_write_gdrive <token-json-file> <team_drive_id-or-empty>
    local token_file="$1" team_drive="${2:-}"
    install -d -m 0750 "$(dirname "${RCLONE_CONFIG}")"
    umask 077
    python3 - "$token_file" "${RCLONE_REMOTE}" "$team_drive" "${RCLONE_CONFIG}" "${RCLONE_SCOPE:-drive}" <<'PY'
import configparser, json, sys, pathlib
token_path, remote, team_drive, cfg_path, scope = sys.argv[1:6]
token = pathlib.Path(token_path).read_text().strip()
# validar que es un JSON con los campos esperados
try:
    d = json.loads(token)
    assert "access_token" in d and "refresh_token" in d
except Exception as e:
    sys.exit(f"token JSON inválido: {e}")
p = configparser.ConfigParser(interpolation=None, strict=False)
p.read(cfg_path)
if remote not in p:
    p[remote] = {}
p[remote]["type"]   = "drive"
p[remote]["scope"]  = scope
p[remote]["token"]  = token
p[remote]["team_drive"] = team_drive or ""
with open(cfg_path, "w") as fh:
    p.write(fh)
PY
    chmod 0600 "${RCLONE_CONFIG}"
}

# --- comandos públicos ----------------------------------------------------

# drive_status -> JSON con estado de la vinculación
drive_status() {
    local linked="false" team_drive="" reachable="false"
    if [[ -f "${RCLONE_CONFIG}" ]] && _ini_read "${RCLONE_REMOTE}" "token" | grep -q "access_token"; then
        linked="true"
        team_drive="$(_ini_read "${RCLONE_REMOTE}" "team_drive")"
        if rclone --config "${RCLONE_CONFIG}" lsd "${RCLONE_REMOTE}:" >/dev/null 2>&1; then
            reachable="true"
        fi
    fi
    python3 - "$linked" "$team_drive" "$reachable" "${RCLONE_REMOTE}" <<'PY'
import json, sys
linked, team_drive, reachable, remote = sys.argv[1:5]
print(json.dumps({
    "linked": linked == "true",
    "reachable": reachable == "true",
    "remote": remote,
    "target": "shared" if team_drive else ("personal" if linked == "true" else "none"),
    "team_drive": team_drive or None,
}, indent=2))
PY
}

# drive_link <token-json-file> [team_drive_id]
drive_link() {
    local token_file="${1:?falta fichero de token}"
    local team_drive="${2:-}"
    require_cmd rclone
    [[ -f "$token_file" ]] || die "token file no existe: $token_file"
    _ini_write_gdrive "$token_file" "$team_drive"
    log_info "rclone remote '${RCLONE_REMOTE}' configurado (team_drive='${team_drive}')"
    # prueba de alcance
    if rclone --config "${RCLONE_CONFIG}" lsd "${RCLONE_REMOTE}:" >/dev/null 2>&1; then
        log_info "Drive accesible OK"
        echo "ok"
    else
        die "Drive configurado pero no accesible (token inválido o expirado)"
    fi
}

# drive_shared_list -> JSON: [{id,name}, ...]
drive_shared_list() {
    require_cmd rclone
    [[ -f "${RCLONE_CONFIG}" ]] || die "rclone no está configurado. Ejecuta drive-link primero."
    rclone --config "${RCLONE_CONFIG}" backend drives "${RCLONE_REMOTE}:" 2>/dev/null || echo "[]"
}

# drive_set_target personal | shared <id>
drive_set_target() {
    local kind="${1:?personal|shared}"
    require_cmd rclone
    [[ -f "${RCLONE_CONFIG}" ]] || die "rclone no está configurado"
    local team_drive=""
    case "$kind" in
        personal) team_drive="" ;;
        shared)   team_drive="${2:?falta id de unidad compartida}" ;;
        *) die "tipo inválido: $kind (personal|shared)" ;;
    esac
    # Actualizar sólo team_drive
    python3 - "${RCLONE_CONFIG}" "${RCLONE_REMOTE}" "$team_drive" <<'PY'
import configparser, sys
cfg, remote, td = sys.argv[1:4]
p = configparser.ConfigParser(interpolation=None, strict=False)
p.read(cfg)
if remote not in p: sys.exit(f"remote {remote} no existe en {cfg}")
p[remote]["team_drive"] = td
with open(cfg, "w") as fh: p.write(fh)
PY
    log_info "Target actualizado (team_drive='${team_drive}')"
    if rclone --config "${RCLONE_CONFIG}" lsd "${RCLONE_REMOTE}:" >/dev/null 2>&1; then
        echo "ok"
    else
        die "Target cambiado pero no accesible"
    fi
}

# drive_unlink
drive_unlink() {
    [[ -f "${RCLONE_CONFIG}" ]] || { echo "ok"; return 0; }
    python3 - "${RCLONE_CONFIG}" "${RCLONE_REMOTE}" <<'PY'
import configparser, sys
cfg, remote = sys.argv[1:3]
p = configparser.ConfigParser(interpolation=None, strict=False)
p.read(cfg)
if remote in p:
    p.remove_section(remote)
    with open(cfg, "w") as fh: p.write(fh)
PY
    log_info "rclone remote '${RCLONE_REMOTE}' eliminado"
    echo "ok"
}
