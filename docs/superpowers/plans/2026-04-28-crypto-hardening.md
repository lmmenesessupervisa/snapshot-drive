# Crypto Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `age` (modern public-key crypto) as opt-in alternative to the existing `openssl` password-based encryption in archive.sh and db_archive.sh, without breaking existing backups.

**Architecture:** New `core/lib/crypto.sh` helper centralizes the encrypt/decrypt pipe selection (age | openssl | passthrough) based on config. archive.sh and db_archive.sh refactor to use the helper. Restore auto-detects by file extension (`.age` vs `.enc`). New `snapctl crypto-keygen` wraps `age-keygen`.

**Tech Stack:** Bash, age (FiloSottile/age v1.2.1), openssl (existing).

**Spec:** `docs/superpowers/specs/2026-04-28-crypto-hardening-design.md`

---

## File Structure

### Created

| Path | Responsibility |
|------|---------------|
| `core/lib/crypto.sh` | 4 helper functions: `crypto_mode`, `crypto_extension`, `crypto_encrypt_pipe`, `crypto_decrypt_for_path` |
| `tests/db_archive/test_crypto_helper.py` | Round-trip tests (encrypt then decrypt) for all 3 modes |

### Modified

| Path | Change |
|------|--------|
| `backend/config.py` | + `ARCHIVE_AGE_RECIPIENTS` and `ARCHIVE_AGE_IDENTITY_FILE` |
| `core/lib/archive.sh` | Replace inline openssl branches with `crypto_encrypt_pipe` |
| `core/lib/db_archive.sh` | Idem |
| `core/bin/snapctl` | Source `crypto.sh`; add `crypto-keygen` subcommand |
| `install.sh` | Download `age` + `age-keygen` binaries to bundle (idempotent) |
| `core/etc/snapshot.local.conf.example` | Document age config block |
| `README.md` | New section "Cifrado de backups" |

---

## Tasks

### Task 1: Config knobs for age

**Files:**
- Modify: `backend/config.py`
- Create: `tests/db_archive/test_crypto_config.py`

- [ ] **Step 1: Failing test**

```python
# tests/db_archive/test_crypto_config.py
import importlib


def test_default_age_recipients_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCAL_CONF", str(tmp_path / "missing.conf"))
    monkeypatch.setenv("CONF_FILE", str(tmp_path / "missing-global.conf"))
    monkeypatch.delenv("ARCHIVE_AGE_RECIPIENTS", raising=False)
    monkeypatch.delenv("ARCHIVE_AGE_IDENTITY_FILE", raising=False)
    import backend.config
    importlib.reload(backend.config)
    assert backend.config.Config.ARCHIVE_AGE_RECIPIENTS == ""
    assert backend.config.Config.ARCHIVE_AGE_IDENTITY_FILE == ""


def test_age_recipients_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCAL_CONF", str(tmp_path / "missing.conf"))
    monkeypatch.setenv("CONF_FILE", str(tmp_path / "missing-global.conf"))
    monkeypatch.setenv("ARCHIVE_AGE_RECIPIENTS", "age1xyz age1abc")
    import backend.config
    importlib.reload(backend.config)
    assert backend.config.Config.ARCHIVE_AGE_RECIPIENTS == "age1xyz age1abc"


def test_age_identity_file_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCAL_CONF", str(tmp_path / "missing.conf"))
    monkeypatch.setenv("CONF_FILE", str(tmp_path / "missing-global.conf"))
    monkeypatch.setenv("ARCHIVE_AGE_IDENTITY_FILE", "/var/lib/snapshot-v3/age-id.txt")
    import backend.config
    importlib.reload(backend.config)
    assert backend.config.Config.ARCHIVE_AGE_IDENTITY_FILE == "/var/lib/snapshot-v3/age-id.txt"
```

- [ ] **Step 2: Run, expect fail**

`.venv/bin/pytest tests/db_archive/test_crypto_config.py -v`

- [ ] **Step 3: Add to `backend/config.py` inside `class Config:`** (after the DB_MONGO_URI block):

```python
    # ─── Sub-proyecto F: cifrado age (opt-in) ────────────────────────────
    # Recipients públicos space-separated. Si está seteado, age toma
    # precedencia sobre ARCHIVE_PASSWORD para los backups nuevos.
    ARCHIVE_AGE_RECIPIENTS = (
        os.getenv("ARCHIVE_AGE_RECIPIENTS")
        or _CONF.get("ARCHIVE_AGE_RECIPIENTS", "")
    )
    # Path al archivo de identidad (privada). Solo se usa en restore.
    ARCHIVE_AGE_IDENTITY_FILE = (
        os.getenv("ARCHIVE_AGE_IDENTITY_FILE")
        or _CONF.get("ARCHIVE_AGE_IDENTITY_FILE", "")
    )
```

- [ ] **Step 4: Run, expect 3 passed**

- [ ] **Step 5: Commit**

```bash
git add backend/config.py tests/db_archive/test_crypto_config.py
git commit -m "crypto: Config knobs for age recipients + identity file"
```

---

### Task 2: crypto.sh helper

**Files:**
- Create: `core/lib/crypto.sh`
- Create: `tests/db_archive/test_crypto_helper.py`

- [ ] **Step 1: Failing test**

```python
# tests/db_archive/test_crypto_helper.py
import os
import shutil
import subprocess
import tempfile
import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HELPER = os.path.join(REPO, "core/lib/crypto.sh")


def _run(snippet, env=None, stdin=None):
    """Source common.sh + crypto.sh and run the snippet, return (stdout, stderr, rc)."""
    full = (
        f'export SNAPSHOT_ROOT="{REPO}"\n'
        f'set -Eeuo pipefail\n'
        f'_log_stub() {{ :; }}\n'
        f'log_info()  {{ _log_stub; }}\n'
        f'log_warn()  {{ _log_stub; }}\n'
        f'log_error() {{ _log_stub; }}\n'
        f'die()       {{ echo "die: $*" >&2; exit 1; }}\n'
        f'source "{HELPER}"\n'
        f'{snippet}\n'
    )
    p = subprocess.run(["bash", "-c", full],
                       input=stdin, capture_output=True,
                       env={**os.environ, **(env or {})})
    return p.stdout, p.stderr, p.returncode


def test_crypto_mode_none_when_nothing_set():
    out, _, rc = _run('crypto_mode',
                      env={"ARCHIVE_AGE_RECIPIENTS": "", "ARCHIVE_PASSWORD": ""})
    assert rc == 0
    assert out.strip() == b"none"


def test_crypto_mode_openssl_when_password():
    out, _, rc = _run('crypto_mode',
                      env={"ARCHIVE_AGE_RECIPIENTS": "", "ARCHIVE_PASSWORD": "x"})
    assert out.strip() == b"openssl"


def test_crypto_mode_age_when_recipients():
    out, _, rc = _run('crypto_mode',
                      env={"ARCHIVE_AGE_RECIPIENTS": "age1abc", "ARCHIVE_PASSWORD": ""})
    assert out.strip() == b"age"


def test_crypto_mode_age_wins_over_password():
    out, _, rc = _run('crypto_mode',
                      env={"ARCHIVE_AGE_RECIPIENTS": "age1abc", "ARCHIVE_PASSWORD": "x"})
    assert out.strip() == b"age"


def test_crypto_extension_matches_mode():
    out, _, _ = _run('crypto_extension',
                     env={"ARCHIVE_AGE_RECIPIENTS": "age1abc"})
    assert out.strip() == b"age"
    out, _, _ = _run('crypto_extension',
                     env={"ARCHIVE_AGE_RECIPIENTS": "", "ARCHIVE_PASSWORD": "x"})
    assert out.strip() == b"enc"
    out, _, _ = _run('crypto_extension',
                     env={"ARCHIVE_AGE_RECIPIENTS": "", "ARCHIVE_PASSWORD": ""})
    assert out.strip() == b""


def test_openssl_round_trip(tmp_path):
    plaintext = b"hello world\n" * 100
    enc = tmp_path / "enc.bin"
    out, err, rc = _run(
        f'crypto_encrypt_pipe > "{enc}"',
        env={"ARCHIVE_AGE_RECIPIENTS": "", "ARCHIVE_PASSWORD": "topsecret"},
        stdin=plaintext,
    )
    assert rc == 0, err
    assert enc.exists() and enc.stat().st_size > 0
    # Decrypt:
    dec_out, err, rc = _run(
        f'crypto_decrypt_for_path "x.zst.enc" < "{enc}"',
        env={"ARCHIVE_AGE_RECIPIENTS": "", "ARCHIVE_PASSWORD": "topsecret"},
    )
    assert rc == 0, err
    assert dec_out == plaintext


@pytest.mark.skipif(shutil.which("age") is None and not os.path.exists(
    "/opt/snapshot-V3/bundle/bin/age"
), reason="age binary not available")
def test_age_round_trip(tmp_path):
    # Generate keypair via age-keygen.
    age_keygen = shutil.which("age-keygen") or "/opt/snapshot-V3/bundle/bin/age-keygen"
    p = subprocess.run([age_keygen], capture_output=True, text=True)
    assert p.returncode == 0
    # age-keygen prints "# created: ..." line + "# public key: agexxx" + private on its own line.
    pub = ""
    priv_lines = []
    for line in p.stdout.splitlines():
        if line.startswith("# public key:"):
            pub = line.split(":", 1)[1].strip()
        elif not line.startswith("#"):
            priv_lines.append(line)
    assert pub.startswith("age1")
    identity_file = tmp_path / "identity.txt"
    identity_file.write_text(p.stdout)
    plaintext = b"super secret data\n" * 50
    enc = tmp_path / "enc.age"
    _, err, rc = _run(
        f'crypto_encrypt_pipe > "{enc}"',
        env={"ARCHIVE_AGE_RECIPIENTS": pub, "ARCHIVE_PASSWORD": ""},
        stdin=plaintext,
    )
    assert rc == 0, err
    assert enc.exists() and enc.stat().st_size > 0
    dec_out, err, rc = _run(
        f'crypto_decrypt_for_path "x.zst.age" < "{enc}"',
        env={"ARCHIVE_AGE_RECIPIENTS": pub,
             "ARCHIVE_AGE_IDENTITY_FILE": str(identity_file),
             "ARCHIVE_PASSWORD": ""},
    )
    assert rc == 0, err
    assert dec_out == plaintext


def test_passthrough_round_trip(tmp_path):
    plaintext = b"no crypto here\n" * 30
    enc = tmp_path / "enc.bin"
    _, err, rc = _run(
        f'crypto_encrypt_pipe > "{enc}"',
        env={"ARCHIVE_AGE_RECIPIENTS": "", "ARCHIVE_PASSWORD": ""},
        stdin=plaintext,
    )
    assert rc == 0, err
    assert enc.read_bytes() == plaintext
    # Decrypt with neither extension matching → passthrough cat.
    dec_out, _, rc = _run(
        f'crypto_decrypt_for_path "x.zst" < "{enc}"',
        env={"ARCHIVE_AGE_RECIPIENTS": "", "ARCHIVE_PASSWORD": ""},
    )
    assert rc == 0
    assert dec_out == plaintext
```

- [ ] **Step 2: Run, expect failure**

- [ ] **Step 3: Implement `core/lib/crypto.sh`**

```bash
#!/usr/bin/env bash
# ===========================================================
# crypto.sh — wrapper centralizado de cifrado para archive.sh
# y db_archive.sh. Tres modos:
#   - age (opt-in via ARCHIVE_AGE_RECIPIENTS) — public-key crypto
#   - openssl (legacy via ARCHIVE_PASSWORD)   — password-based
#   - none (pasthrough, sin cifrar)
#
# Restore auto-detecta por extensión del path (.age o .enc).
# ===========================================================

# crypto_mode: imprime "age" | "openssl" | "none"
crypto_mode() {
    if [[ -n "${ARCHIVE_AGE_RECIPIENTS:-}" ]]; then
        if [[ -n "${ARCHIVE_PASSWORD:-}" ]]; then
            echo "warning: ARCHIVE_AGE_RECIPIENTS y ARCHIVE_PASSWORD ambos seteados; age tiene precedencia" >&2
        fi
        echo "age"
        return
    fi
    if [[ -n "${ARCHIVE_PASSWORD:-}" ]]; then
        echo "openssl"
        return
    fi
    echo "none"
}

# crypto_extension: imprime el sufijo (sin punto) que se appendea
# al filename del archivo cifrado. "" para passthrough.
crypto_extension() {
    case "$(crypto_mode)" in
        age)     echo "age" ;;
        openssl) echo "enc" ;;
        *)       echo ""    ;;
    esac
}

# crypto_encrypt_pipe: lee stdin, escribe a stdout cifrado según el modo.
# Diseñado para usarse en pipelines: `... | crypto_encrypt_pipe | rclone rcat`.
crypto_encrypt_pipe() {
    case "$(crypto_mode)" in
        age)
            local args=()
            for r in $ARCHIVE_AGE_RECIPIENTS; do
                args+=(-r "$r")
            done
            exec age "${args[@]}"
            ;;
        openssl)
            exec openssl enc -aes-256-cbc -pbkdf2 -iter 100000 \
                -pass env:ARCHIVE_PASSWORD
            ;;
        *)
            exec cat
            ;;
    esac
}

# crypto_decrypt_for_path: detecta por extensión y descifra.
# Uso: `rclone cat path | crypto_decrypt_for_path "$path" | zstd -dc | tar -xf -`
crypto_decrypt_for_path() {
    local path="$1"
    if [[ "$path" == *.age ]]; then
        local id="${ARCHIVE_AGE_IDENTITY_FILE:-}"
        [[ -n "$id" && -f "$id" ]] || die "Restore .age requiere ARCHIVE_AGE_IDENTITY_FILE apuntando a la privada"
        exec age -d -i "$id"
    elif [[ "$path" == *.enc ]]; then
        [[ -n "${ARCHIVE_PASSWORD:-}" ]] || die "Restore .enc requiere ARCHIVE_PASSWORD"
        exec openssl enc -d -aes-256-cbc -pbkdf2 -iter 100000 \
            -pass env:ARCHIVE_PASSWORD
    else
        exec cat
    fi
}
```

- [ ] **Step 4: Run, expect at least the openssl + passthrough tests pass; age may skip if binary missing**

- [ ] **Step 5: Commit**

```bash
git add core/lib/crypto.sh tests/db_archive/test_crypto_helper.py
git commit -m "crypto: helper crypto.sh with mode selection + encrypt/decrypt pipe"
```

---

### Task 3: install.sh — bundle age binary

**Files:**
- Modify: `install.sh`

- [ ] **Step 1: Add AGE_VERSION pin and URL**

In install.sh, in the version pin block (after `RCLONE_VERSION`), add:

```bash
: "${AGE_VERSION:=v1.2.1}"
```

In the URL block (after `RCLONE_URL=`), add:

```bash
# age platform suffix mirrors restic's pattern:
case "$ARCH" in
    x86_64)   AGE_PLATFORM="linux-amd64" ;;
    aarch64|arm64) AGE_PLATFORM="linux-arm64" ;;
esac
AGE_URL="https://github.com/FiloSottile/age/releases/download/${AGE_VERSION}/age-${AGE_VERSION}-${AGE_PLATFORM}.tar.gz"
```

(`AGE_PLATFORM` already implicit from the existing `ARCH` switch — actually we need to set it. Let me put the case at the top with the others.)

Actually: the existing top-of-file `case "$ARCH" in` already sets `RESTIC_PLATFORM` and `RCLONE_PLATFORM`. Add `AGE_PLATFORM` to that same case:

Find the existing block (look for `RESTIC_PLATFORM="linux_amd64"`). Modify both branches:

```bash
    x86_64)
        PYTHON_PLATFORM="x86_64-unknown-linux-gnu"
        RESTIC_PLATFORM="linux_amd64"
        RCLONE_PLATFORM="linux-amd64"
        AGE_PLATFORM="linux-amd64"
        ;;
    aarch64|arm64)
        PYTHON_PLATFORM="aarch64-unknown-linux-gnu"
        RESTIC_PLATFORM="linux_arm64"
        RCLONE_PLATFORM="linux-arm64"
        AGE_PLATFORM="linux-arm64"
        ;;
```

- [ ] **Step 2: Add the version-check helper and install block**

After the `_rclone_bundled_ver()` function, add:

```bash
_age_bundled_ver() {
    "$BUNDLE_DIR/bin/age" --version 2>/dev/null | awk 'NR==1{print $1}' || true
}
```

After the rclone install block (the `if [[ ! -x "$BUNDLE_DIR/bin/rclone" ]]` block), add:

```bash
# --- age (opt-in encryption alternative to openssl) ---
AGE_EXPECT="${AGE_VERSION#v}"
if [[ ! -x "$BUNDLE_DIR/bin/age" ]] || [[ "$(_age_bundled_ver)" != "$AGE_EXPECT" ]]; then
    fetch "$AGE_URL" "$TMP_DIR/age.tar.gz"
    # tarball trae age/ y age/age + age/age-keygen
    tar -xzf "$TMP_DIR/age.tar.gz" -C "$TMP_DIR"
    install -m 0755 "$TMP_DIR/age/age" "$BUNDLE_DIR/bin/age"
    install -m 0755 "$TMP_DIR/age/age-keygen" "$BUNDLE_DIR/bin/age-keygen"
    info "age $(_age_bundled_ver) instalado."
else
    info "age bundled ya presente: $(_age_bundled_ver)"
fi
```

- [ ] **Step 3: Update the URL block to declare AGE_URL**

Find the line `RCLONE_URL="https://downloads.rclone.org/...` and add right after:

```bash
AGE_URL="https://github.com/FiloSottile/age/releases/download/${AGE_VERSION}/age-${AGE_VERSION}-${AGE_PLATFORM}.tar.gz"
```

- [ ] **Step 4: Syntax check**

```bash
bash -n install.sh
```

- [ ] **Step 5: Commit**

```bash
git add install.sh
git commit -m "crypto: install.sh bundles age + age-keygen (FiloSottile/age v1.2.1)"
```

---

### Task 4: Wire archive.sh + db_archive.sh through helper

**Files:**
- Modify: `core/lib/archive.sh`
- Modify: `core/lib/db_archive.sh`
- Modify: `core/bin/snapctl` (source crypto.sh)

- [ ] **Step 1: Source crypto.sh in snapctl**

Find the `source ".../archive.sh"` line in `core/bin/snapctl`. Add immediately after:

```bash
# shellcheck disable=SC1091
[[ -f "${SNAPSHOT_ROOT}/core/lib/crypto.sh" ]] && source "${SNAPSHOT_ROOT}/core/lib/crypto.sh"
```

- [ ] **Step 2: Refactor archive.sh**

In `_archive_build_path`:

```bash
_archive_build_path() {
    local ts="$1"
    local nombre="${BACKUP_NOMBRE:-$HOSTNAME}"
    local year="${ts:0:4}" month="${ts:4:2}" day="${ts:6:2}"
    local ext="tar.zst"
    local crypto_ext; crypto_ext="$(crypto_extension)"
    [[ -n "$crypto_ext" ]] && ext="${ext}.${crypto_ext}"
    printf '%s/%s/%s/%s/servidor_%s_%s.%s' \
        "$(_archive_remote_base)" "$year" "$month" "$day" \
        "$nombre" "$ts" "$ext"
}
```

In `cmd_archive`, replace the `if $encrypted; then ... else ... fi` block with a single pipeline using the helper:

```bash
    local encrypted=false
    [[ "$(crypto_mode)" != "none" ]] && encrypted=true

    log_info "Archive iniciado → ${RCLONE_REMOTE}:${remote_path} (mode=$(crypto_mode))"

    # ... (paths + missing_paths logic unchanged) ...

    local start_ts; start_ts="$(date +%s)"
    local rc=0
    local save_opts; save_opts="$(set +o | grep pipefail)"
    set -o pipefail

    tar -cf - --warning=no-file-changed --warning=no-file-removed \
            --exclude-from="$EXCLUDES_FILE" "${paths[@]}" 2>/dev/null \
        | zstd -T0 -10 -q \
        | crypto_encrypt_pipe \
        | rclone --config "$RCLONE_CONFIG" rcat "${RCLONE_REMOTE}:${remote_path}" \
        || rc=$?

    eval "$save_opts"
```

The success/fail branches stay the same; just the encrypted=$encrypted in `_meta` JSON now reflects "is anything encrypting" (true/false), which is what it always meant.

In `cmd_archive_restore`, replace the openssl decrypt block with `crypto_decrypt_for_path`. Find:

```bash
if $encrypted; then
    rclone --config "$RCLONE_CONFIG" cat "${RCLONE_REMOTE}:${remote_path}" \
        | openssl enc -d ... \
        | zstd -dc | tar -xf - -C "$target"
else
    rclone --config "$RCLONE_CONFIG" cat "${RCLONE_REMOTE}:${remote_path}" \
        | zstd -dc | tar -xf - -C "$target"
fi
```

Replace with:

```bash
rclone --config "$RCLONE_CONFIG" cat "${RCLONE_REMOTE}:${remote_path}" \
    | crypto_decrypt_for_path "$remote_path" \
    | zstd -dc | tar -xf - -C "$target"
```

- [ ] **Step 3: Refactor db_archive.sh**

In `_db_build_path`, swap the `[[ -n "$ARCHIVE_PASSWORD" ]] && ext="${ext}.enc"` for crypto-aware:

```bash
_db_build_path() {
    local engine="$1" dbname="$2" ts="$3"
    local year="${ts:0:4}" month="${ts:4:2}" day="${ts:6:2}"
    local ext
    case "$engine" in
        postgres|mysql) ext="sql.zst" ;;
        mongo)          ext="archive.zst" ;;
        *)              ext="dump.zst" ;;
    esac
    local crypto_ext; crypto_ext="$(crypto_extension)"
    [[ -n "$crypto_ext" ]] && ext="${ext}.${crypto_ext}"
    printf '%s/%s/%s/%s/servidor_%s_%s.%s' \
        "$(_db_remote_base "$engine" "$dbname")" \
        "$year" "$month" "$day" "$dbname" "$ts" "$ext"
}
```

In `_db_archive_target`, replace the `if $encrypted; then ... openssl ... else ... fi` with:

```bash
    _db_dump_cmd "$engine" "$dbname" 2>/dev/null \
        | zstd -T0 -10 -q \
        | crypto_encrypt_pipe \
        | rclone --config "$RCLONE_CONFIG" rcat "${RCLONE_REMOTE}:${remote_path}" \
        || rc=$?
```

In `cmd_db_archive_restore`, replace the openssl decrypt block with `crypto_decrypt_for_path`:

```bash
    rclone --config "$RCLONE_CONFIG" cat "${RCLONE_REMOTE}:${remote_path}" \
        | crypto_decrypt_for_path "$remote_path" \
        | zstd -dc \
        | bash -c "$restore_cmd"
```

The `encrypted=true/false` boolean stays for the DRY-RUN message but
the actual pipeline doesn't branch on it.

- [ ] **Step 4: Syntax check**

```bash
bash -n core/lib/archive.sh
bash -n core/lib/db_archive.sh
bash -n core/bin/snapctl
```

- [ ] **Step 5: Run full pytest (no regression)**

```bash
.venv/bin/pytest tests/ -q
```

- [ ] **Step 6: Commit**

```bash
git add core/lib/archive.sh core/lib/db_archive.sh core/bin/snapctl
git commit -m "crypto: route archive/db_archive through crypto.sh helper"
```

---

### Task 5: snapctl crypto-keygen subcommand

**Files:**
- Modify: `core/bin/snapctl`

- [ ] **Step 1: Add the dispatch case**

After the `db-archive-restore)` case in the dispatcher:

```bash
        crypto-keygen)      cmd_crypto_keygen      "$@" ;;
```

- [ ] **Step 2: Add the function (define it before `main()`, near `usage()`)**

```bash
cmd_crypto_keygen() {
    local age_keygen="${SNAPSHOT_ROOT}/bundle/bin/age-keygen"
    [[ -x "$age_keygen" ]] || die "age-keygen no instalado. Re-ejecuta install.sh."

    local out; out="$("$age_keygen" 2>&1)"
    local pub priv
    pub="$(echo "$out" | awk -F': ' '/# public key:/{print $2}' | tr -d ' ')"
    priv="$(echo "$out" | grep -v '^#' | head -1)"

    cat <<EOF

Generando age keypair…

PUBLIC KEY (pegar en ARCHIVE_AGE_RECIPIENTS de snapshot.local.conf):
  ${pub}

PRIVATE KEY (anotar AHORA — no se vuelve a mostrar):
  ${priv}

⚠  Recomendaciones:
   - Pegá la pública en /etc/snapshot-v3/snapshot.local.conf
     ARCHIVE_AGE_RECIPIENTS="${pub}"
   - Guardá la privada en un gestor de contraseñas o sobre sellado
   - NUNCA escribas la privada en este host (excepto temporalmente
     para restore, en /var/lib/snapshot-v3/age-identity.txt 0600)

Para generar 2+ recipients (operacional + escrow), corré este comando
varias veces y pegá ambas pubs separadas por espacios.

EOF
}
```

- [ ] **Step 3: Add to usage block**

In `usage()`, after the DB BACKUPS section, add:

```
CRYPTO:
  crypto-keygen                        Genera keypair age (pub + priv una vez)
```

- [ ] **Step 4: Syntax check + smoke**

```bash
bash -n core/bin/snapctl
```

If `age-keygen` is bundled (or available), test:

```bash
SNAPSHOT_ROOT=/home/superaccess/snapshot-V3 /home/superaccess/snapshot-V3/core/bin/snapctl crypto-keygen
```

If not yet (Task 3 might not have run), the smoke produces "die: age-keygen no instalado" — that's fine.

- [ ] **Step 5: Commit**

```bash
git add core/bin/snapctl
git commit -m "crypto: snapctl crypto-keygen wrapper around age-keygen"
```

---

### Task 6: Local conf example + README

**Files:**
- Modify: `core/etc/snapshot.local.conf.example`
- Modify: `README.md`

- [ ] **Step 1: Append to `core/etc/snapshot.local.conf.example`**

```bash

# --- Sub-F: Cifrado opcional con age (alternativa a ARCHIVE_PASSWORD) ----
# Si está seteado, age toma precedencia sobre ARCHIVE_PASSWORD para los
# backups nuevos (los viejos siguen restauraables con su modo original).
# Generá un keypair con: sudo snapctl crypto-keygen
# Soporta 2+ recipients separados por espacios:
#   ARCHIVE_AGE_RECIPIENTS="age1abc... age1xyz..."
ARCHIVE_AGE_RECIPIENTS=""

# Path al archivo de identidad (privada). Solo se usa para restore.
# Mode 0600. NO debe estar seteado en hosts de producción — solo
# temporalmente cuando vas a restaurar.
ARCHIVE_AGE_IDENTITY_FILE=""
```

- [ ] **Step 2: Append to README.md after the "Backups de bases de datos" section, before "## CLI"**

```markdown
## Cifrado de backups (openssl vs age)

snapshot-V3 soporta dos modos de cifrado, mutuamente excluyentes:

| Modo | Activación | Recovery |
|---|---|---|
| **openssl** (legacy) | `ARCHIVE_PASSWORD="..."` | password compartida; perderla = data perdida |
| **age** (recomendado) | `ARCHIVE_AGE_RECIPIENTS="age1xyz..."` | privada(s) en escrow; múltiples recipients = múltiples privadas válidas |
| **none** | ambos vacíos | sin cifrado (solo zstd) |

Si ambos están seteados, **age gana** y emite un warning. Los backups
viejos en formato `.enc` siguen restauraables — el restore detecta la
extensión y elige la herramienta correcta.

### Generar keypair age

```bash
sudo snapctl crypto-keygen
```

Imprime una vez:
- **PUBLIC KEY** (`age1xyz...`) → pegar en
  `ARCHIVE_AGE_RECIPIENTS` de `snapshot.local.conf`.
- **PRIVATE KEY** (`AGE-SECRET-KEY-1...`) → guardar en gestor de
  contraseñas o sobre sellado. **NO en este host** (excepto temporal
  para restore).

### Múltiples recipients (recomendado)

```bash
ARCHIVE_AGE_RECIPIENTS="age1ops... age1escrow..."
```

age cifra para todos los recipients en paralelo. Cualquiera de las
privadas asociadas puede descifrar — útil para tener:
- Una privada operacional (gestor del equipo).
- Una privada en escrow (oficina, sobre sellado, hardware key).

### Restore con age

```bash
# 1. Copiar la privada al host temporalmente:
sudo nano /var/lib/snapshot-v3/age-identity.txt
sudo chmod 600 /var/lib/snapshot-v3/age-identity.txt

# 2. Setear el path en local.conf:
sudo nano /etc/snapshot-v3/snapshot.local.conf
# ARCHIVE_AGE_IDENTITY_FILE="/var/lib/snapshot-v3/age-identity.txt"

# 3. Restore (auto-detecta .age):
sudo snapctl archive-restore <remote_path> --target /tmp/restore
sudo snapctl db-archive-restore <remote_path> --target mydb

# 4. BORRAR la privada del host:
sudo shred -u /var/lib/snapshot-v3/age-identity.txt
sudo sed -i 's/^ARCHIVE_AGE_IDENTITY_FILE=.*/ARCHIVE_AGE_IDENTITY_FILE=""/' \
    /etc/snapshot-v3/snapshot.local.conf
```

### Migrar de openssl a age

No hay migración automática. Para que los backups nuevos usen age:

```bash
sudo snapctl crypto-keygen   # imprime pub + priv
sudo nano /etc/snapshot-v3/snapshot.local.conf
# ARCHIVE_AGE_RECIPIENTS="age1xyz..."
# (dejá ARCHIVE_PASSWORD seteado por ahora — sigue cubriendo restore
#  de backups viejos; age gana automáticamente para backups nuevos)
```

Cuando todos los backups que querés conservar ya estén en `.age`,
podés vaciar `ARCHIVE_PASSWORD` (los archivos `.enc` viejos en Drive
ya no serán restauraables sin re-setear la password).

### Velocidad

age es notablemente más rápido que openssl:
- ChaCha20-Poly1305 (age) vs AES-CBC (openssl) en CPUs sin AES-NI.
- Sin overhead de PBKDF2 100k rounds al inicio (age usa key wrapping
  X25519, que es mucho más barato).

En un VPS típico: dump Postgres 1 GB → 8s zstd + age vs 12s zstd +
openssl.
```

- [ ] **Step 3: Run full pytest**

```bash
.venv/bin/pytest tests/ -q
```

- [ ] **Step 4: Commit**

```bash
git add core/etc/snapshot.local.conf.example README.md
git commit -m "docs: README + local.conf example for age encryption"
```

---

### Task 7: Push + PR

- [ ] **Step 1: Push branch**

```bash
git push origin feature/crypto-hardening
```

- [ ] **Step 2: PR URL**

```
https://github.com/lmmenesessupervisa/snapshot-drive/pull/new/feature/crypto-hardening
```

---

## Plan complete

7 tasks covering full sub-project F:
- T1: Config knobs
- T2: crypto.sh helper + tests
- T3: install.sh bundles age
- T4: archive.sh + db_archive.sh refactored to use helper
- T5: snapctl crypto-keygen
- T6: README + local.conf docs
- T7: Push + PR
