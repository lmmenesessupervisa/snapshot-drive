# DB Backups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `snapctl db-archive` command that streams Postgres/MySQL/Mongo dumps directly to Drive with the same taxonomy + cifrado + heartbeat-to-central as the FS archive.

**Architecture:** New bash module `core/lib/db_archive.sh` with per-engine handler functions invoked by `snapctl db-archive`. Streaming pipeline `<dump_cmd> | zstd -T0 | [openssl] | rclone rcat`. Config in `snapshot.local.conf` (`DB_BACKUP_TARGETS="engine:dbname ..."`). Heartbeat per target via existing `central_send` helper with `category=db, subkey=<engine>, label=<dbname>`. New systemd timer `snapshot@db-archive.timer`.

**Tech Stack:** Bash, zstd, openssl, rclone, pg_dump/mysqldump/mongodump (system tools), Python (parsing helper for tests).

**Spec:** `docs/superpowers/specs/2026-04-27-db-archive-design.md`

---

## File Structure

### Created

| Path | Responsibility |
|------|---------------|
| `backend/services/db_targets.py` | Pure-Python parser for `DB_BACKUP_TARGETS` string + helpers (testable) |
| `core/lib/db_archive.sh` | Per-engine dump/restore handlers + main pipeline |
| `systemd/snapshot@db-archive.timer.d/override.conf` | Daily 03:00 OnCalendar |
| `systemd/snapshot@db-archive.service.d/override.conf` | TimeoutStartSec=infinity, Nice=15 |
| `tests/db_archive/__init__.py` | |
| `tests/db_archive/test_targets_parser.py` | Tests for the parser |

### Modified

| Path | Change |
|------|--------|
| `backend/config.py` | Add `DB_BACKUP_TARGETS` and per-engine connection vars to `Config` |
| `backend/services/scheduler.py` | Add `"db-archive"` to `SUPPORTED_UNITS` and `_DEFAULT_ONCALENDAR` |
| `core/bin/snapctl` | Source `db_archive.sh`; add subcommands `db-archive`, `db-archive-list`, `db-archive-restore`; usage block |
| `core/etc/snapshot.local.conf.example` | Document the DB config block |
| `install.sh` | Install drop-ins; enable timer if `DB_BACKUP_TARGETS` set |
| `README.md` | New section "Backups de bases de datos" |

---

## Phase 1: Parser + config

### Task 1: Targets parser (Python)

**Files:**
- Create: `backend/services/db_targets.py`
- Create: `tests/db_archive/__init__.py` (empty)
- Create: `tests/db_archive/test_targets_parser.py`

- [ ] **Step 1: Failing tests**

```python
# tests/db_archive/test_targets_parser.py
import pytest
from backend.services.db_targets import parse_targets, ParseError, VALID_ENGINES


def test_empty_string_returns_empty_list():
    assert parse_targets("") == []
    assert parse_targets("   ") == []


def test_single_target():
    assert parse_targets("postgres:mydb") == [("postgres", "mydb")]


def test_multiple_space_separated():
    out = parse_targets("postgres:a mysql:b mongo:c")
    assert out == [("postgres", "a"), ("mysql", "b"), ("mongo", "c")]


def test_extra_whitespace_tolerated():
    assert parse_targets("  postgres:a   mysql:b  ") == [
        ("postgres", "a"), ("mysql", "b")
    ]


def test_engine_validation_rejects_unknown():
    with pytest.raises(ParseError) as e:
        parse_targets("redis:mydb")
    assert "redis" in str(e.value)


def test_missing_colon_raises():
    with pytest.raises(ParseError):
        parse_targets("postgres mydb")


def test_empty_dbname_raises():
    with pytest.raises(ParseError):
        parse_targets("postgres:")


def test_dbname_with_dash_underscore_dot_ok():
    assert parse_targets("postgres:my-db_v2.0") == [("postgres", "my-db_v2.0")]


def test_dbname_with_invalid_chars_rejected():
    with pytest.raises(ParseError):
        parse_targets("postgres:my db")  # space in name
    with pytest.raises(ParseError):
        parse_targets("postgres:'; DROP TABLE--")


def test_valid_engines_constant():
    assert set(VALID_ENGINES) == {"postgres", "mysql", "mongo"}
```

- [ ] **Step 2: Run, expect failure**

`.venv/bin/pytest tests/db_archive/test_targets_parser.py -v`

- [ ] **Step 3: Implement `backend/services/db_targets.py`**

```python
"""Parser puro para DB_BACKUP_TARGETS.

Format: "engine:dbname engine:dbname ..." (space-separated).
Engine: one of VALID_ENGINES.
DB name: alphanumeric + dash + underscore + dot. Anything else → ParseError.
"""
from __future__ import annotations

import re
from typing import List, Tuple

VALID_ENGINES = ("postgres", "mysql", "mongo")
_DBNAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class ParseError(ValueError):
    pass


def parse_targets(s: str) -> List[Tuple[str, str]]:
    """Returns [(engine, dbname), ...]. Empty string → []."""
    s = (s or "").strip()
    if not s:
        return []
    out: list[tuple[str, str]] = []
    for tok in s.split():
        if ":" not in tok:
            raise ParseError(f"target malformado (falta ':'): {tok!r}")
        engine, _, dbname = tok.partition(":")
        engine = engine.strip().lower()
        dbname = dbname.strip()
        if engine not in VALID_ENGINES:
            raise ParseError(
                f"engine no soportado: {engine!r} (válidos: {VALID_ENGINES})"
            )
        if not dbname:
            raise ParseError(f"target sin dbname: {tok!r}")
        if not _DBNAME_RE.match(dbname):
            raise ParseError(
                f"dbname inválido: {dbname!r} (solo alfanum + ._-)"
            )
        out.append((engine, dbname))
    return out
```

- [ ] **Step 4: Run, expect 10 passed**

- [ ] **Step 5: Commit**

```bash
git add backend/services/db_targets.py tests/db_archive/__init__.py tests/db_archive/test_targets_parser.py
git commit -m "db-archive: targets parser (engine:dbname format)"
```

---

### Task 2: Config knobs

**Files:**
- Modify: `backend/config.py`
- Create: `tests/db_archive/test_db_config.py`

- [ ] **Step 1: Failing test**

```python
# tests/db_archive/test_db_config.py
import importlib


def test_default_db_targets_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCAL_CONF", str(tmp_path / "missing.conf"))
    monkeypatch.setenv("CONF_FILE", str(tmp_path / "missing-global.conf"))
    monkeypatch.delenv("DB_BACKUP_TARGETS", raising=False)
    import backend.config
    importlib.reload(backend.config)
    assert backend.config.Config.DB_BACKUP_TARGETS == ""


def test_db_targets_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCAL_CONF", str(tmp_path / "missing.conf"))
    monkeypatch.setenv("CONF_FILE", str(tmp_path / "missing-global.conf"))
    monkeypatch.setenv("DB_BACKUP_TARGETS", "postgres:foo")
    import backend.config
    importlib.reload(backend.config)
    assert backend.config.Config.DB_BACKUP_TARGETS == "postgres:foo"


def test_pg_password_not_exposed_in_repr(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCAL_CONF", str(tmp_path / "missing.conf"))
    monkeypatch.setenv("CONF_FILE", str(tmp_path / "missing-global.conf"))
    monkeypatch.setenv("DB_PG_PASSWORD", "supersecret")
    import backend.config
    importlib.reload(backend.config)
    # Password is loaded but not part of repr / accidental logging.
    assert backend.config.Config.DB_PG_PASSWORD == "supersecret"
```

- [ ] **Step 2: Run, expect fail (`DB_BACKUP_TARGETS` not on `Config`)**

- [ ] **Step 3: Append to `backend/config.py` inside `class Config:`** (right after `CENTRAL_MAX_PAYLOAD_BYTES`):

```python
    # ─── Sub-proyecto E: DB backups ──────────────────────────────────────
    # Lista space-separated de "engine:dbname". Vacío = no DB backups.
    DB_BACKUP_TARGETS = (
        os.getenv("DB_BACKUP_TARGETS")
        or _CONF.get("DB_BACKUP_TARGETS", "")
    )
    # Postgres connection (env vars también respetadas por pg_dump):
    DB_PG_HOST = os.getenv("DB_PG_HOST") or _CONF.get("DB_PG_HOST", "")
    DB_PG_PORT = os.getenv("DB_PG_PORT") or _CONF.get("DB_PG_PORT", "5432")
    DB_PG_USER = os.getenv("DB_PG_USER") or _CONF.get("DB_PG_USER", "")
    DB_PG_PASSWORD = os.getenv("DB_PG_PASSWORD") or _CONF.get("DB_PG_PASSWORD", "")
    # MySQL:
    DB_MYSQL_HOST = os.getenv("DB_MYSQL_HOST") or _CONF.get("DB_MYSQL_HOST", "")
    DB_MYSQL_PORT = os.getenv("DB_MYSQL_PORT") or _CONF.get("DB_MYSQL_PORT", "3306")
    DB_MYSQL_USER = os.getenv("DB_MYSQL_USER") or _CONF.get("DB_MYSQL_USER", "")
    DB_MYSQL_PASSWORD = os.getenv("DB_MYSQL_PASSWORD") or _CONF.get("DB_MYSQL_PASSWORD", "")
    # Mongo URI completo (incluye auth):
    DB_MONGO_URI = os.getenv("DB_MONGO_URI") or _CONF.get("DB_MONGO_URI", "")
```

- [ ] **Step 4: Run, expect 3 passed**

- [ ] **Step 5: Commit**

```bash
git add backend/config.py tests/db_archive/test_db_config.py
git commit -m "db-archive: Config knobs for DB targets and per-engine connections"
```

---

## Phase 2: Bash pipeline

### Task 3: db_archive.sh skeleton + helpers

**Files:**
- Create: `core/lib/db_archive.sh`

- [ ] **Step 1: Create file with shared helpers**

```bash
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
    [[ -n "${ARCHIVE_PASSWORD:-}" ]] && ext="${ext}.enc"
    printf '%s/%s/%s/%s/servidor_%s_%s.%s' \
        "$(_db_remote_base "$engine" "$dbname")" \
        "$year" "$month" "$day" "$dbname" "$ts" "$ext"
}

# Itera DB_BACKUP_TARGETS sin shell injection.
# Llama a _db_archive_one engine dbname para cada uno.
_db_iterate_targets() {
    [[ -n "${DB_BACKUP_TARGETS:-}" ]] || return 0
    for tok in $DB_BACKUP_TARGETS; do
        [[ "$tok" == *:* ]] || { log_warn "target malformado: $tok"; continue; }
        local engine="${tok%%:*}"
        local dbname="${tok#*:}"
        if [[ ! "$engine" =~ $_DB_ENGINES_RE ]]; then
            log_warn "engine no soportado: $engine"
            continue
        fi
        if [[ -z "$dbname" || "$dbname" == *[!A-Za-z0-9._-]* ]]; then
            log_warn "dbname inválido: $dbname"
            continue
        fi
        _db_archive_one "$engine" "$dbname"
    done
}

# Override-able por tests; default delega a la función real.
_db_archive_one() {
    local engine="$1" dbname="$2"
    _db_archive_target "$engine" "$dbname"
}

# Construye el comando dump según engine. Imprime a stdout.
# La password NUNCA va en argv; solo via env.
_db_dump_cmd() {
    local engine="$1" dbname="$2"
    case "$engine" in
        postgres)
            local args=(--no-owner --no-acl --quote-all-identifiers)
            [[ -n "$DB_PG_HOST" ]] && args+=(--host "$DB_PG_HOST")
            [[ -n "$DB_PG_PORT" ]] && args+=(--port "$DB_PG_PORT")
            [[ -n "$DB_PG_USER" ]] && args+=(--username "$DB_PG_USER")
            args+=("$dbname")
            PGPASSWORD="${DB_PG_PASSWORD:-}" pg_dump "${args[@]}"
            ;;
        mysql)
            local args=(--single-transaction --quick --skip-lock-tables \
                        --routines --triggers --events)
            [[ -n "$DB_MYSQL_HOST" ]] && args+=("-h$DB_MYSQL_HOST")
            [[ -n "$DB_MYSQL_PORT" ]] && args+=("-P$DB_MYSQL_PORT")
            [[ -n "$DB_MYSQL_USER" ]] && args+=("-u$DB_MYSQL_USER")
            args+=("$dbname")
            MYSQL_PWD="${DB_MYSQL_PASSWORD:-}" mysqldump "${args[@]}"
            ;;
        mongo)
            [[ -n "$DB_MONGO_URI" ]] || { log_error "DB_MONGO_URI vacío"; return 2; }
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
```

- [ ] **Step 2: Syntax check**

```bash
bash -n core/lib/db_archive.sh
```

- [ ] **Step 3: Commit**

```bash
git add core/lib/db_archive.sh
git commit -m "db-archive: skeleton with target parser and dump_cmd builder"
```

---

### Task 4: db-archive main command

**Files:**
- Modify: `core/lib/db_archive.sh` (append `cmd_db_archive`, `_db_archive_target`)

- [ ] **Step 1: Append to `core/lib/db_archive.sh`**

```bash

# ----------------------- comando principal -----------------------

cmd_db_archive() {
    _archive_validate_taxonomy
    require_cmd zstd
    command -v rclone >/dev/null 2>&1 || die "rclone no encontrado"
    drive_reachable || die "Drive no alcanzable"

    [[ -n "${DB_BACKUP_TARGETS:-}" ]] || die "DB_BACKUP_TARGETS vacío en snapshot.local.conf"

    local fail_count=0 ok_count=0
    for tok in $DB_BACKUP_TARGETS; do
        [[ "$tok" == *:* ]] || { log_warn "target malformado: $tok"; ((fail_count++)); continue; }
        local engine="${tok%%:*}"
        local dbname="${tok#*:}"
        if [[ ! "$engine" =~ $_DB_ENGINES_RE ]]; then
            log_warn "engine no soportado: $engine"
            ((fail_count++)); continue
        fi
        if [[ -z "$dbname" || "$dbname" == *[!A-Za-z0-9._-]* ]]; then
            log_warn "dbname inválido: $dbname"
            ((fail_count++)); continue
        fi
        if ! _db_engine_available "$engine"; then
            log_warn "engine $engine: tool ausente, target $tok salteado"
            ((fail_count++)); continue
        fi
        if _db_archive_target "$engine" "$dbname"; then
            ((ok_count++))
        else
            ((fail_count++))
        fi
    done

    log_info "DB archive: ${ok_count} ok, ${fail_count} fail"
    [[ $fail_count -eq 0 ]] || return 1
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

    if $encrypted; then
        _db_dump_cmd "$engine" "$dbname" 2>/dev/null \
            | zstd -T0 -10 -q \
            | openssl enc -aes-256-cbc -pbkdf2 -iter 100000 -pass env:ARCHIVE_PASSWORD \
            | rclone --config "$RCLONE_CONFIG" rcat "${RCLONE_REMOTE}:${remote_path}" \
            || rc=$?
    else
        _db_dump_cmd "$engine" "$dbname" 2>/dev/null \
            | zstd -T0 -10 -q \
            | rclone --config "$RCLONE_CONFIG" rcat "${RCLONE_REMOTE}:${remote_path}" \
            || rc=$?
    fi
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
```

- [ ] **Step 2: Syntax check**

```bash
bash -n core/lib/db_archive.sh
```

- [ ] **Step 3: Commit**

```bash
git add core/lib/db_archive.sh
git commit -m "db-archive: cmd_db_archive iterates targets with streaming pipeline"
```

---

### Task 5: db-archive list + restore

**Files:**
- Modify: `core/lib/db_archive.sh` (append)

- [ ] **Step 1: Append**

```bash

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
        # Solo imprime el comando y aborta.
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

    if $encrypted; then
        rclone --config "$RCLONE_CONFIG" cat "${RCLONE_REMOTE}:${remote_path}" \
            | openssl enc -d -aes-256-cbc -pbkdf2 -iter 100000 -pass env:ARCHIVE_PASSWORD \
            | zstd -dc \
            | bash -c "$restore_cmd"
    else
        rclone --config "$RCLONE_CONFIG" cat "${RCLONE_REMOTE}:${remote_path}" \
            | zstd -dc \
            | bash -c "$restore_cmd"
    fi
    local rc=$?
    eval "$save_opts"
    [[ $rc -eq 0 ]] && log_info "DB restore OK" || log_error "DB restore FAIL rc=$rc"
    return $rc
}
```

- [ ] **Step 2: Syntax check**

```bash
bash -n core/lib/db_archive.sh
```

- [ ] **Step 3: Commit**

```bash
git add core/lib/db_archive.sh
git commit -m "db-archive: list + restore with engine auto-detection from path"
```

---

## Phase 3: snapctl wiring

### Task 6: snapctl subcommands

**Files:**
- Modify: `core/bin/snapctl`

- [ ] **Step 1: Source `db_archive.sh` after the existing sources**

After the line `source "${SNAPSHOT_ROOT}/core/lib/archive.sh"`, insert:

```bash
# shellcheck disable=SC1091
[[ -f "${SNAPSHOT_ROOT}/core/lib/db_archive.sh" ]] && source "${SNAPSHOT_ROOT}/core/lib/db_archive.sh"
```

- [ ] **Step 2: Add subcommands to dispatcher**

In the `case "$cmd" in` block (find via `grep -n 'case "\$cmd"' core/bin/snapctl`), add right after the `archive-paths)` line:

```bash
        db-archive)         cmd_db_archive         "$@" ;;
        db-archive-list)    cmd_db_archive_list    "$@" ;;
        db-archive-restore) cmd_db_archive_restore "$@" ;;
```

- [ ] **Step 3: Update usage block**

In `usage()`, add a new section after the Drive commands, before "USERS / AUTH":

```
DB BACKUPS:
  db-archive                          Dump configurado en DB_BACKUP_TARGETS y sube a Drive
  db-archive-list [--json]            Lista los dumps en Drive
  db-archive-restore <path> --target  Restaura dump (decompress + pipe a engine)
```

- [ ] **Step 4: Smoke test**

```bash
bash -n core/bin/snapctl
.venv/bin/pytest tests/ -q
```

Expected: syntax OK + tests still pass (no regression — we haven't run db-archive yet).

- [ ] **Step 5: Commit**

```bash
git add core/bin/snapctl
git commit -m "db-archive: snapctl subcommands db-archive[-list|-restore]"
```

---

### Task 7: scheduler.SUPPORTED_UNITS

**Files:**
- Modify: `backend/services/scheduler.py`

- [ ] **Step 1: Add `db-archive` to SUPPORTED_UNITS and defaults**

Find:

```python
SUPPORTED_UNITS = {"archive", "create", "prune"}

_DEFAULT_ONCALENDAR = {
    "archive": "*-*-01 02:00:00",
    "create":  "*-*-* 03:00:00",
    "prune":   "*-*-* 04:00:00",
}
```

Replace with:

```python
SUPPORTED_UNITS = {"archive", "db-archive", "create", "prune"}

_DEFAULT_ONCALENDAR = {
    "archive":     "*-*-01 02:00:00",
    "db-archive":  "*-*-* 03:00:00",
    "create":      "*-*-* 03:00:00",
    "prune":       "*-*-* 04:00:00",
}
```

- [ ] **Step 2: Run full suite**

```bash
.venv/bin/pytest tests/ -q
```

Expected: still all pass (existing scheduler tests parametrize by unit but the unit field is just a string).

- [ ] **Step 3: Commit**

```bash
git add backend/services/scheduler.py
git commit -m "db-archive: register db-archive in scheduler SUPPORTED_UNITS"
```

---

## Phase 4: systemd + install

### Task 8: systemd drop-ins

**Files:**
- Create: `systemd/snapshot@db-archive.service.d/override.conf`
- Create: `systemd/snapshot@db-archive.timer.d/override.conf`

- [ ] **Step 1: Create service drop-in**

```ini
# systemd/snapshot@db-archive.service.d/override.conf
[Service]
TimeoutStartSec=infinity
Nice=15
IOSchedulingClass=idle
```

- [ ] **Step 2: Create timer drop-in**

```ini
# systemd/snapshot@db-archive.timer.d/override.conf
[Timer]
OnCalendar=
OnCalendar=*-*-* 03:00:00
RandomizedDelaySec=20min
Persistent=true
```

- [ ] **Step 3: Commit**

```bash
git add systemd/snapshot@db-archive.service.d/ systemd/snapshot@db-archive.timer.d/
git commit -m "db-archive: systemd drop-ins (daily 03:00 UTC, low priority)"
```

---

### Task 9: install.sh + local.conf example

**Files:**
- Modify: `install.sh`
- Modify: `core/etc/snapshot.local.conf.example`

- [ ] **Step 1: Add db-archive drop-ins to install.sh**

In install.sh, find the section that installs archive drop-ins (search for `snapshot@archive.timer.d`). Right after that block, add:

```bash
# Drop-ins para el backup de bases de datos.
install -d -m 0755 /etc/systemd/system/snapshot@db-archive.timer.d/
install -m 0644 "$INSTALL_ROOT/systemd/snapshot@db-archive.timer.d/override.conf" \
    /etc/systemd/system/snapshot@db-archive.timer.d/override.conf
install -d -m 0755 /etc/systemd/system/snapshot@db-archive.service.d/
install -m 0644 "$INSTALL_ROOT/systemd/snapshot@db-archive.service.d/override.conf" \
    /etc/systemd/system/snapshot@db-archive.service.d/override.conf
```

In the `systemctl enable` block (find `systemctl enable --now snapshot@archive.timer`), append a conditional enable:

```bash
# DB archive timer: solo activar si DB_BACKUP_TARGETS está configurado.
if grep -qE '^DB_BACKUP_TARGETS="[^"]+' "$LOCAL_CONF" 2>/dev/null; then
    systemctl enable --now snapshot@db-archive.timer
    info "snapshot@db-archive.timer activado (DB_BACKUP_TARGETS configurado)."
else
    systemctl disable --now snapshot@db-archive.timer 2>/dev/null || true
    info "snapshot@db-archive.timer NO activado (DB_BACKUP_TARGETS vacío)."
fi
```

- [ ] **Step 2: Append to `core/etc/snapshot.local.conf.example`**

```bash

# --- Sub-E: DB backups ---------------------------------------------------
# Lista space-separated de "engine:dbname" (engines: postgres, mysql, mongo).
# Vacío = no DB backups, no se activa el timer.
DB_BACKUP_TARGETS=""

# Postgres connection. Si DB_PG_HOST está vacío, pg_dump usa socket Unix.
DB_PG_HOST=""
DB_PG_PORT="5432"
DB_PG_USER=""
DB_PG_PASSWORD=""

# MySQL / MariaDB:
DB_MYSQL_HOST=""
DB_MYSQL_PORT="3306"
DB_MYSQL_USER=""
DB_MYSQL_PASSWORD=""

# MongoDB URI completo (incluye auth):
# mongodb://user:pass@host:27017/?authSource=admin
DB_MONGO_URI=""
```

- [ ] **Step 3: Syntax check**

```bash
bash -n install.sh
```

- [ ] **Step 4: Commit**

```bash
git add install.sh core/etc/snapshot.local.conf.example
git commit -m "db-archive: install.sh enables timer if DB_BACKUP_TARGETS set"
```

---

## Phase 5: Docs + final

### Task 10: README + final test + push

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add section after "Modo central / Alertas" (or before "## CLI")**

Append after the existing `## Modo central` section:

```markdown
## Backups de bases de datos

`snapctl db-archive` respalda Postgres / MySQL / MongoDB en streaming
directo a Drive con la misma taxonomía que el archive de FS pero en
sub-carpeta `db/<engine>/<dbname>/`:

```
PROYECTO/ENTORNO/PAIS/db/postgres/mydb/2026/04/27/
    servidor_mydb_20260427_030010.sql.zst[.enc]
```

### Engines soportados

| Engine | Tool requerido | Comando |
|---|---|---|
| postgres | `pg_dump` | `pg_dump --no-owner --no-acl --quote-all-identifiers <db>` |
| mysql / mariadb | `mysqldump` | `mysqldump --single-transaction --quick --routines --triggers --events <db>` |
| mongo | `mongodump` | `mongodump --uri=$DB_MONGO_URI --archive --db=<db>` |

snapshot-V3 no instala las herramientas — el operador las instala
con `apt install postgresql-client mysql-client mongodb-database-tools`.

### Configuración

En `/etc/snapshot-v3/snapshot.local.conf`:

```bash
DB_BACKUP_TARGETS="postgres:mydb postgres:other mysql:web mongo:metrics"

# Per-engine connection (ver snapshot.local.conf.example):
DB_PG_HOST="localhost"; DB_PG_USER="postgres"; DB_PG_PASSWORD="..."
DB_MYSQL_HOST="localhost"; DB_MYSQL_USER="root"; DB_MYSQL_PASSWORD="..."
DB_MONGO_URI="mongodb://user:pass@localhost:27017"
```

### Schedule

Default: diario 03:00 UTC vía `snapshot@db-archive.timer`. Editable
desde el panel (Programación) gracias a `SUPPORTED_UNITS` que incluye
`db-archive`. `install.sh` activa el timer **solo si** `DB_BACKUP_TARGETS`
no está vacío al momento del install.

### CLI

```bash
sudo snapctl db-archive                          # ejecuta todos los targets
sudo snapctl db-archive-list                     # lista dumps en Drive
sudo snapctl db-archive-list --json
sudo snapctl db-archive-restore <path>           # dry-run (imprime cmd)
sudo snapctl db-archive-restore <path> --target mydb  # ejecuta restore
```

### Cifrado

Reusa `ARCHIVE_PASSWORD` del archive de FS. Si está seteada, los dumps
se cifran con AES-256-CBC + PBKDF2 100k antes de subir.

### Heartbeat

Cada target emite un heartbeat al central (sub-B) con
`target.category="db"`, `subkey=<engine>`, `label=<dbname>`. El
dashboard agregado los muestra junto a los OS targets.
```

- [ ] **Step 2: Run full suite**

```bash
.venv/bin/pytest tests/ -q
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: README section on DB backups (Postgres/MySQL/Mongo)"
```

- [ ] **Step 4: Push branch**

```bash
git push origin feature/db-archive
```

PR URL: `https://github.com/lmmenesessupervisa/snapshot-drive/pull/new/feature/db-archive`

---

## Plan complete

10 tasks covering full sub-project E:
- Phase 1 (T1-T2): parser + config
- Phase 2 (T3-T5): bash pipeline + commands
- Phase 3 (T6-T7): snapctl wiring + scheduler integration
- Phase 4 (T8-T9): systemd + install
- Phase 5 (T10): docs + push
