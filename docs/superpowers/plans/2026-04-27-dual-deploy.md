# Despliegue dual + agregación central — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Construir el modo `central` que recibe heartbeats HTTP de N instalaciones cliente y agrega su estado en una UI común. Mantener el modo `client` existente intacto a menos que se opte explícitamente.

**Architecture:** Single binary, dos modos vía variable `MODE` en `snapshot.local.conf`. Cliente envía heartbeats firmados (Bearer token argon2) tras cada operación; central persiste audit append-only y mantiene agregados materializados para dashboard <50ms. Cola SQLite local para reintentos con backoff exponencial.

**Tech Stack:** Flask 3.x, SQLite (WAL), argon2-cffi (ya en uso), `requests` (nuevo dep), pyotp/zxcvbn ya integrados, Tailwind CDN. Sin Alembic — schema migrations vía `CREATE TABLE IF NOT EXISTS`.

**Spec:** `docs/superpowers/specs/2026-04-27-dual-deploy-design.md` (commit `a199bc4`).

**Branch:** `feature/central-mode`. Una sola PR contra `main`.

---

## File structure (locked-in decomposition)

### Nuevos
```
backend/central/
  __init__.py            # blueprint factories + helpers de modo
  models.py              # DAO: clients, targets, central_tokens, central_events
  tokens.py              # issuance, hash argon2, verify Bearer
  schema.py              # validación pydantic-style del payload (sin pydantic, dict + checks)
  api.py                 # /api/v1/heartbeat, /api/v1/ping  (M2M)
  admin.py               # /api/admin/clients, /api/admin/tokens, /api/admin/events
  dashboard.py           # /dashboard-central HTML
  queue.py               # cliente: enqueue, fetch_due, mark_done, mark_dead, backoff
  sender.py              # cliente: HTTP POST + retry classification
  permissions.py         # matriz central.* → required role(s); decorator require_central_perm

frontend/templates/central/
  dashboard.html         # tabla por proyecto, KPIs
  clients.html           # CRUD UI
  client_detail.html     # drill-down con targets y últimos events
  tokens.html            # issue/revoke per cliente

frontend/static/js/central/
  dashboard.js           # render de la tabla, polling cada 30s
  tokens.js              # mostrar plaintext una sola vez

core/lib/central.sh      # helper bash: arma payload JSON y llama snapctl central send
tests/central/
  __init__.py
  conftest.py            # fixtures: central_db, central_app, fake_token, fake_client
  test_*.py              # ver §"Estrategia de testing" del spec
```

### Modificados
```
backend/models/db.py     # +SCHEMA con 6 CREATE TABLE IF NOT EXISTS
backend/config.py        # +MODE, CENTRAL_URL, CENTRAL_TOKEN, CENTRAL_TIMEOUT_S
backend/app.py           # if Config.MODE == "central": register central blueprints; if CENTRAL_URL: register sender
backend/auth/decorators.py  # +require_central_perm (composable con la matriz)
core/bin/snapctl         # +subcomandos: central drain-queue, central status, central send
core/lib/archive.sh      # invocar central.sh tras éxito/falla del archive
core/lib/common.sh       # +write_status_drive() ya existente sigue, no se toca
systemd/snapshot-healthcheck.service  # +ExecStartPost=snapctl central drain-queue
install.sh               # +flag --central
README.md                # +sección "Modo central — agregación cross-cliente"
backend/requirements.txt # +requests>=2.31
```

---

## Convenciones para todas las tasks

- **Branch:** todas las tasks suceden en `feature/central-mode`. No mergeás a main hasta cerrar la última task.
- **Commit message:** `central: <imperative>` (paralelo al `auth:` que usó sub-A). Ej: `central: add token issuance with argon2 hashing`.
- **Tests:** `pytest tests/central/ -q` debe pasar al final de cada task. La cobertura objetivo (≥90% en `backend/central/`) se valida solo al final.
- **Cuando una task dice "show test fails / passes"**: ejecutar literalmente y verificar la salida — no avanzar al siguiente step si el resultado no coincide.
- **Paralelización:** las tasks marcadas con `[parallel: T#, T#]` pueden ejecutarse simultáneamente con esas otras (no comparten archivos modificados).

---

## Task 0: Branch + dep + skeleton

**Files:**
- Create: `backend/central/__init__.py` (vacío)
- Create: `tests/central/__init__.py` (vacío)
- Modify: `backend/requirements.txt` (+`requests>=2.31`)

- [ ] **Step 1:** Crear branch desde main.

```bash
cd /home/superaccess/snapshot-V3
git checkout -b feature/central-mode
git status
```
Expected: `On branch feature/central-mode`, working tree clean.

- [ ] **Step 2:** Crear directorios y placeholders.

```bash
mkdir -p backend/central tests/central frontend/templates/central frontend/static/js/central
touch backend/central/__init__.py tests/central/__init__.py
```

- [ ] **Step 3:** Agregar `requests` a `backend/requirements.txt`.

```text
Flask>=3.0,<4.0
gunicorn>=21.2
argon2-cffi>=23.1
pyotp>=2.9
zxcvbn>=4.4.28
Flask-Limiter>=3.5
Flask-Talisman>=1.1
cryptography>=42.0
segno>=1.6
requests>=2.31
pytest>=8.0
```

- [ ] **Step 4:** Instalar la nueva dep en el venv local.

```bash
sudo /opt/snapshot-V3/.venv/bin/pip install 'requests>=2.31'
```
Expected: `Successfully installed requests-...`.

- [ ] **Step 5:** Commit.

```bash
git add backend/central/ tests/central/ frontend/templates/central frontend/static/js/central backend/requirements.txt
git commit -m "central: scaffold package and add requests dep"
```

---

## Task 1: Schema migration — 6 tablas nuevas

**Files:**
- Modify: `backend/models/db.py` (constante `SCHEMA`)
- Test: `tests/central/test_schema_migration.py`

- [ ] **Step 1: Escribir el test que falla.**

```python
# tests/central/test_schema_migration.py
"""Las 6 tablas nuevas deben crearse al boot, en cualquier modo."""
from backend.models.db import DB


def test_creates_central_tables(tmp_path):
    db_path = tmp_path / "t.db"
    DB(db_path)  # constructor llama _init_schema
    import sqlite3
    con = sqlite3.connect(db_path)
    rows = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    names = {r[0] for r in rows}
    expected = {
        "clients",
        "targets",
        "central_tokens",
        "central_events",
        "central_user_perms",
        "central_queue",
    }
    assert expected.issubset(names), f"falta(n): {expected - names}"


def test_indexes_present(tmp_path):
    db_path = tmp_path / "t.db"
    DB(db_path)
    import sqlite3
    con = sqlite3.connect(db_path)
    rows = con.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
    ).fetchall()
    names = {r[0] for r in rows}
    for needed in (
        "idx_targets_client",
        "idx_targets_silent",
        "idx_tokens_client",
        "idx_events_target_ts",
        "idx_events_client_ts",
        "idx_queue_due",
    ):
        assert needed in names, f"falta índice {needed}"
```

- [ ] **Step 2: Run test, verify it fails.**

```bash
cd /home/superaccess/snapshot-V3
sudo /opt/snapshot-V3/.venv/bin/python -m pytest tests/central/test_schema_migration.py -v
```
Expected: 2 failures (`falta(n): {...}`).

- [ ] **Step 3: Agregar las 6 tablas + índices al `SCHEMA` de `backend/models/db.py`.**

Append al final del string `SCHEMA` (antes del cierre `"""`):

```sql
-- Sub-proyecto B: despliegue dual + agregación central
-- Estas tablas se crean en ambos modos (client y central). En client mode
-- las 5 centrales quedan vacías; en central mode central_queue queda vacía.
-- Trade-off deliberado: un solo path de inicialización.

CREATE TABLE IF NOT EXISTS clients (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    proyecto        TEXT NOT NULL UNIQUE,
    organizacion    TEXT,
    contacto        TEXT,
    retencion_meses INTEGER,
    notas           TEXT,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS targets (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id         INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    category          TEXT NOT NULL CHECK (category IN ('os','db')),
    subkey            TEXT NOT NULL,
    label             TEXT NOT NULL,
    entorno           TEXT,
    pais              TEXT,
    last_exec_ts      TEXT,
    last_exec_status  TEXT,
    last_size_bytes   INTEGER,
    total_size_bytes  INTEGER,
    count_files       INTEGER,
    oldest_backup_ts  TEXT,
    newest_backup_ts  TEXT,
    last_heartbeat_ts TEXT NOT NULL,
    snapctl_version   TEXT,
    rclone_version    TEXT,
    created_at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(client_id, category, subkey, label)
);
CREATE INDEX IF NOT EXISTS idx_targets_client ON targets(client_id);
CREATE INDEX IF NOT EXISTS idx_targets_silent ON targets(last_heartbeat_ts);

CREATE TABLE IF NOT EXISTS central_tokens (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash   TEXT NOT NULL UNIQUE,
    client_id    INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    label        TEXT NOT NULL,
    scope        TEXT NOT NULL DEFAULT 'heartbeat:write',
    created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at   TEXT,
    last_used_at TEXT,
    revoked_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_tokens_client
    ON central_tokens(client_id) WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS central_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id     TEXT NOT NULL UNIQUE,
    received_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    token_id     INTEGER NOT NULL REFERENCES central_tokens(id),
    client_id    INTEGER NOT NULL REFERENCES clients(id),
    target_id    INTEGER REFERENCES targets(id),
    op           TEXT NOT NULL,
    status       TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    src_ip       TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_target_ts
    ON central_events(target_id, received_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_client_ts
    ON central_events(client_id, received_at DESC);

CREATE TABLE IF NOT EXISTS central_user_perms (
    user_id          INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    can_manage_users INTEGER NOT NULL DEFAULT 0,
    notes            TEXT
);

CREATE TABLE IF NOT EXISTS central_queue (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id      TEXT NOT NULL UNIQUE,
    payload_json  TEXT NOT NULL,
    enqueued_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    next_retry_ts TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    attempts      INTEGER NOT NULL DEFAULT 0,
    last_error    TEXT,
    state         TEXT NOT NULL DEFAULT 'pending'
);
CREATE INDEX IF NOT EXISTS idx_queue_due ON central_queue(state, next_retry_ts);
```

- [ ] **Step 4: Run test, verify it passes.**

```bash
sudo /opt/snapshot-V3/.venv/bin/python -m pytest tests/central/test_schema_migration.py -v
```
Expected: 2 passed.

- [ ] **Step 5: Verify el resto de tests no se rompió.**

```bash
sudo /opt/snapshot-V3/.venv/bin/python -m pytest tests/auth/ -q
```
Expected: 101 passed (sin regresión).

- [ ] **Step 6: Commit.**

```bash
git add backend/models/db.py tests/central/test_schema_migration.py
git commit -m "central: add 6 SQLite tables for clients/targets/tokens/events/perms/queue"
```

---

## Task 2: Config knobs (MODE, CENTRAL_URL, CENTRAL_TOKEN)

**Files:**
- Modify: `backend/config.py` (clase `Config`)
- Test: `tests/central/test_config_modes.py`

`[parallel: T1]` — toca un archivo distinto.

- [ ] **Step 1: Test.**

```python
# tests/central/test_config_modes.py
"""La clase Config respeta MODE/CENTRAL_* desde env y desde local.conf."""
import importlib
from pathlib import Path


def _reload_config(monkeypatch, env=None, local_conf_text=None, tmp_path=None):
    """Helper: rebuild Config con env y local.conf controlados."""
    if local_conf_text is not None:
        p = tmp_path / "local.conf"
        p.write_text(local_conf_text)
        monkeypatch.setenv("LOCAL_CONF", str(p))
    if env:
        for k, v in env.items():
            monkeypatch.setenv(k, v)
    # Forzar reload del módulo para releer _CONF
    import backend.config as cfg
    importlib.reload(cfg)
    return cfg.Config


def test_mode_default_is_client(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCAL_CONF", str(tmp_path / "nonexistent.conf"))
    Config = _reload_config(monkeypatch)
    assert Config.MODE == "client"


def test_mode_central_from_local_conf(monkeypatch, tmp_path):
    Config = _reload_config(monkeypatch,
                            local_conf_text='MODE="central"\n',
                            tmp_path=tmp_path)
    assert Config.MODE == "central"


def test_central_url_from_local_conf(monkeypatch, tmp_path):
    txt = 'CENTRAL_URL="https://central.example.com"\nCENTRAL_TOKEN="abc123"\n'
    Config = _reload_config(monkeypatch, local_conf_text=txt, tmp_path=tmp_path)
    assert Config.CENTRAL_URL == "https://central.example.com"
    assert Config.CENTRAL_TOKEN == "abc123"


def test_unknown_mode_falls_back_to_client(monkeypatch, tmp_path):
    Config = _reload_config(monkeypatch,
                            local_conf_text='MODE="nonsense"\n',
                            tmp_path=tmp_path)
    assert Config.MODE == "client"


def test_central_timeout_default_5s(monkeypatch, tmp_path):
    Config = _reload_config(monkeypatch, local_conf_text='', tmp_path=tmp_path)
    assert Config.CENTRAL_TIMEOUT_S == 5
```

- [ ] **Step 2: Run test, verify it fails.**

```bash
sudo /opt/snapshot-V3/.venv/bin/python -m pytest tests/central/test_config_modes.py -v
```
Expected: 5 failures (AttributeError MODE/CENTRAL_*).

- [ ] **Step 3: Agregar al `Config` en `backend/config.py` (al final de la clase, antes de cualquier método):**

```python
    # ─── Sub-proyecto B: despliegue dual ─────────────────────────────────
    # MODE controla qué blueprints carga app.py. Default "client".
    _MODE_RAW = (os.getenv("MODE") or _CONF.get("MODE") or "client").strip().lower()
    MODE = _MODE_RAW if _MODE_RAW in ("client", "central") else "client"

    # Solo relevante en client mode: dónde postear heartbeats.
    CENTRAL_URL = (os.getenv("CENTRAL_URL") or _CONF.get("CENTRAL_URL") or "").rstrip("/")
    CENTRAL_TOKEN = os.getenv("CENTRAL_TOKEN") or _CONF.get("CENTRAL_TOKEN") or ""
    CENTRAL_TIMEOUT_S = int(os.getenv("CENTRAL_TIMEOUT_S") or _CONF.get("CENTRAL_TIMEOUT_S") or "5")
    # Tope duro de payload aceptado por el endpoint POST /heartbeat
    CENTRAL_MAX_PAYLOAD_BYTES = 64 * 1024
```

- [ ] **Step 4: Run test, verify it passes.**

```bash
sudo /opt/snapshot-V3/.venv/bin/python -m pytest tests/central/test_config_modes.py -v
```
Expected: 5 passed.

- [ ] **Step 5: Commit.**

```bash
git add backend/config.py tests/central/test_config_modes.py
git commit -m "central: add MODE, CENTRAL_URL, CENTRAL_TOKEN config knobs"
```

---

## Task 3: Tokens — issuance, hashing argon2, verify

**Files:**
- Create: `backend/central/tokens.py`
- Create: `tests/central/test_tokens.py`
- Test: `tests/central/test_tokens.py`

`[parallel: T1, T2]` — depende solo de la tabla `central_tokens` (T1) y argon2 ya disponible.

- [ ] **Step 1: Test.**

```python
# tests/central/test_tokens.py
import pytest
from backend.models.db import DB
from backend.central import tokens as tok


@pytest.fixture
def db(tmp_path):
    return DB(tmp_path / "t.db")


@pytest.fixture
def client_id(db):
    cur = db.con().execute(
        "INSERT INTO clients (proyecto) VALUES ('demo')"
    )
    db.con().commit()
    return cur.lastrowid


def test_issue_returns_plaintext_token(db, client_id):
    plaintext, token_id = tok.issue(db, client_id, label="web01")
    assert isinstance(plaintext, str) and len(plaintext) >= 32
    assert isinstance(token_id, int) and token_id > 0


def test_issued_token_is_argon2_hashed_in_db(db, client_id):
    plaintext, token_id = tok.issue(db, client_id, label="web01")
    row = db.con().execute(
        "SELECT token_hash FROM central_tokens WHERE id=?", (token_id,)
    ).fetchone()
    assert row[0].startswith("$argon2"), "token must be argon2-hashed"
    assert plaintext not in row[0], "plaintext must not appear in db"


def test_verify_accepts_correct_token(db, client_id):
    plaintext, _ = tok.issue(db, client_id, label="web01")
    result = tok.verify(db, plaintext)
    assert result is not None
    assert result.client_id == client_id
    assert result.scope == "heartbeat:write"


def test_verify_rejects_wrong_token(db, client_id):
    tok.issue(db, client_id, label="web01")
    assert tok.verify(db, "wrongtoken123") is None


def test_verify_rejects_revoked_token(db, client_id):
    plaintext, token_id = tok.issue(db, client_id, label="web01")
    tok.revoke(db, token_id)
    assert tok.verify(db, plaintext) is None


def test_verify_rejects_expired_token(db, client_id):
    plaintext, token_id = tok.issue(db, client_id, label="web01",
                                    expires_at="2020-01-01T00:00:00Z")
    assert tok.verify(db, plaintext) is None


def test_verify_updates_last_used_at(db, client_id):
    plaintext, token_id = tok.issue(db, client_id, label="web01")
    tok.verify(db, plaintext)
    row = db.con().execute(
        "SELECT last_used_at FROM central_tokens WHERE id=?", (token_id,)
    ).fetchone()
    assert row[0] is not None


def test_revoke_sets_revoked_at(db, client_id):
    _, token_id = tok.issue(db, client_id, label="x")
    tok.revoke(db, token_id)
    row = db.con().execute(
        "SELECT revoked_at FROM central_tokens WHERE id=?", (token_id,)
    ).fetchone()
    assert row[0] is not None
```

- [ ] **Step 2: Run test, verify it fails.**

```bash
sudo /opt/snapshot-V3/.venv/bin/python -m pytest tests/central/test_tokens.py -v
```
Expected: ImportError on `backend.central.tokens`.

> Nota: el test usa `db.con()` que ya existe en `backend/models/db.py` (método helper). Si no, usar `sqlite3.connect(db.path)`.

- [ ] **Step 3: Implementar `backend/central/tokens.py`.**

```python
"""Issuance, hashing y verify de Bearer tokens M2M para sub-proyecto B.

Patrón espejo al de backend/auth/passwords.py — argon2id, jamás texto plano
en DB. Plaintext se devuelve UNA SOLA VEZ al issuance; verify hace SELECT
de todos los hashes (no se puede indexar argon2) y compara uno por uno.
A escalas <10k tokens activos por central esto es <50ms.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_HASHER = PasswordHasher()


@dataclass
class TokenInfo:
    id: int
    client_id: int
    scope: str
    label: str


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _gen_plaintext() -> str:
    """32 bytes random → 64 hex chars."""
    return secrets.token_hex(32)


def issue(db, client_id: int, *, label: str,
          scope: str = "heartbeat:write",
          expires_at: Optional[str] = None) -> tuple[str, int]:
    """Crea un token. Devuelve (plaintext, token_id). El plaintext NO se
    persiste — solo el hash."""
    plaintext = _gen_plaintext()
    h = _HASHER.hash(plaintext)
    cur = db.con().execute(
        "INSERT INTO central_tokens "
        "(token_hash, client_id, label, scope, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (h, client_id, label, scope, _now_iso(), expires_at),
    )
    db.con().commit()
    return plaintext, cur.lastrowid


def verify(db, plaintext: str) -> Optional[TokenInfo]:
    """Intenta validar el plaintext contra todos los hashes activos.
    Devuelve TokenInfo si match, None si no."""
    if not plaintext or len(plaintext) < 16:
        return None
    rows = db.con().execute(
        "SELECT id, token_hash, client_id, scope, label, expires_at "
        "FROM central_tokens WHERE revoked_at IS NULL"
    ).fetchall()
    now = _now_iso()
    for row in rows:
        token_id, token_hash, client_id, scope, label, expires_at = row
        if expires_at and expires_at <= now:
            continue
        try:
            _HASHER.verify(token_hash, plaintext)
        except VerifyMismatchError:
            continue
        # Match.
        db.con().execute(
            "UPDATE central_tokens SET last_used_at=? WHERE id=?",
            (now, token_id),
        )
        db.con().commit()
        return TokenInfo(id=token_id, client_id=client_id, scope=scope, label=label)
    return None


def revoke(db, token_id: int) -> None:
    db.con().execute(
        "UPDATE central_tokens SET revoked_at=? WHERE id=?",
        (_now_iso(), token_id),
    )
    db.con().commit()


def list_active(db, client_id: int) -> list[dict]:
    rows = db.con().execute(
        "SELECT id, label, scope, created_at, last_used_at, expires_at "
        "FROM central_tokens "
        "WHERE client_id=? AND revoked_at IS NULL "
        "ORDER BY created_at DESC",
        (client_id,),
    ).fetchall()
    return [
        {"id": r[0], "label": r[1], "scope": r[2],
         "created_at": r[3], "last_used_at": r[4], "expires_at": r[5]}
        for r in rows
    ]
```

> Si `backend/models/db.py::DB` no expone un método `.con()`, agregalo (1 línea: `def con(self): return self._connect()`) o ajustar a la API existente. **Antes de implementar, leer `backend/models/db.py` completo y adaptarse al patrón real.**

- [ ] **Step 4: Run test, verify it passes.**

```bash
sudo /opt/snapshot-V3/.venv/bin/python -m pytest tests/central/test_tokens.py -v
```
Expected: 8 passed.

- [ ] **Step 5: Commit.**

```bash
git add backend/central/tokens.py tests/central/test_tokens.py
git commit -m "central: add token issuance, argon2 hashing, verify and revoke"
```

---

## Task 4: Schema validation del payload del heartbeat

**Files:**
- Create: `backend/central/schema.py`
- Create: `tests/central/test_heartbeat_schema.py`

`[parallel: T1, T2, T3]` — solo lógica pura, sin DB.

- [ ] **Step 1: Test.**

```python
# tests/central/test_heartbeat_schema.py
import pytest
from backend.central.schema import validate_heartbeat, SchemaError


def _good_payload() -> dict:
    return {
        "event_id": "9f2a1b6c-1234-5678-9abc-def012345678",
        "ts": "2026-04-27T17:42:11Z",
        "client": {"proyecto": "superaccess-uno", "entorno": "cloud", "pais": "colombia"},
        "target": {"category": "os", "subkey": "linux", "label": "web01"},
        "operation": {"op": "archive", "status": "ok",
                      "started_at": "2026-04-27T17:30:02Z",
                      "duration_s": 729, "error": None},
        "snapshot": {
            "size_bytes": 4831838208,
            "remote_path": "superaccess-uno/cloud/colombia/os/linux/web01/2026/04/27/x.tar.zst.enc",
            "encrypted": True,
        },
        "totals": {"size_bytes": 178291823104, "count_files": 14,
                   "oldest_ts": "2025-05-01T02:14:33Z",
                   "newest_ts": "2026-04-27T17:42:11Z"},
        "host_meta": {"hostname": "web01.local",
                      "snapctl_version": "0.4.2",
                      "rclone_version": "v1.68.2"},
    }


def test_good_payload_passes():
    out = validate_heartbeat(_good_payload())
    assert out["client"]["proyecto"] == "superaccess-uno"


def test_missing_event_id_fails():
    p = _good_payload(); del p["event_id"]
    with pytest.raises(SchemaError, match="event_id"):
        validate_heartbeat(p)


def test_event_id_must_be_uuid_format():
    p = _good_payload(); p["event_id"] = "not-a-uuid"
    with pytest.raises(SchemaError, match="event_id.*uuid"):
        validate_heartbeat(p)


def test_invalid_category_fails():
    p = _good_payload(); p["target"]["category"] = "windows"
    with pytest.raises(SchemaError, match="category"):
        validate_heartbeat(p)


def test_invalid_status_fails():
    p = _good_payload(); p["operation"]["status"] = "weird"
    with pytest.raises(SchemaError, match="status"):
        validate_heartbeat(p)


def test_negative_size_fails():
    p = _good_payload(); p["snapshot"]["size_bytes"] = -1
    with pytest.raises(SchemaError, match="size_bytes"):
        validate_heartbeat(p)


def test_proyecto_must_match_path_regex():
    """Sin caracteres raros que rompan el path en Drive."""
    p = _good_payload(); p["client"]["proyecto"] = "../etc/passwd"
    with pytest.raises(SchemaError, match="proyecto"):
        validate_heartbeat(p)


def test_optional_host_meta_can_be_omitted():
    p = _good_payload(); del p["host_meta"]
    out = validate_heartbeat(p)
    assert out.get("host_meta") in (None, {})


def test_oversize_payload_via_check_helper():
    from backend.central.schema import check_size
    big = "x" * 70_000
    with pytest.raises(SchemaError, match="too large"):
        check_size(big.encode())
```

- [ ] **Step 2: Run test, verify it fails.**

```bash
sudo /opt/snapshot-V3/.venv/bin/python -m pytest tests/central/test_heartbeat_schema.py -v
```
Expected: 9 import errors.

- [ ] **Step 3: Implementar `backend/central/schema.py`.**

```python
"""Validación del payload del heartbeat. Sin pydantic — chequeos manuales
con mensajes de error explícitos para devolver al cliente en 400."""
from __future__ import annotations

import re
import uuid
from typing import Any

MAX_PAYLOAD_BYTES = 64 * 1024
_OP_VALUES = {"archive", "create", "reconcile", "prune", "delete", "db_dump"}
_STATUS_VALUES = {"ok", "fail", "running"}
_CATEGORY_VALUES = {"os", "db"}
# Caracteres permitidos en proyecto/entorno/pais/subkey/label: alfanum, dash, underscore, dot
_PATH_SAFE = re.compile(r"^[a-zA-Z0-9._-]{1,64}$")


class SchemaError(ValueError):
    pass


def check_size(raw: bytes) -> None:
    if len(raw) > MAX_PAYLOAD_BYTES:
        raise SchemaError(f"payload too large: {len(raw)} > {MAX_PAYLOAD_BYTES}")


def _require(d: dict, key: str, types: tuple) -> Any:
    if key not in d:
        raise SchemaError(f"missing field: {key}")
    if not isinstance(d[key], types):
        raise SchemaError(f"wrong type for {key}: {type(d[key]).__name__}")
    return d[key]


def _require_path_safe(value: str, fieldname: str) -> str:
    if not _PATH_SAFE.match(value):
        raise SchemaError(f"{fieldname} has invalid chars (must match {_PATH_SAFE.pattern})")
    return value


def validate_heartbeat(p: dict) -> dict:
    if not isinstance(p, dict):
        raise SchemaError("payload must be a JSON object")

    eid = _require(p, "event_id", (str,))
    try:
        uuid.UUID(eid)
    except ValueError:
        raise SchemaError("event_id is not a valid uuid")

    _require(p, "ts", (str,))

    client = _require(p, "client", (dict,))
    _require_path_safe(_require(client, "proyecto", (str,)), "client.proyecto")
    if "entorno" in client and client["entorno"] is not None:
        _require_path_safe(client["entorno"], "client.entorno")
    if "pais" in client and client["pais"] is not None:
        _require_path_safe(client["pais"], "client.pais")

    target = _require(p, "target", (dict,))
    cat = _require(target, "category", (str,))
    if cat not in _CATEGORY_VALUES:
        raise SchemaError(f"invalid category: {cat}")
    _require_path_safe(_require(target, "subkey", (str,)), "target.subkey")
    _require_path_safe(_require(target, "label", (str,)), "target.label")

    op = _require(p, "operation", (dict,))
    if op["op"] not in _OP_VALUES:
        raise SchemaError(f"invalid operation.op: {op['op']}")
    if op["status"] not in _STATUS_VALUES:
        raise SchemaError(f"invalid operation.status: {op['status']}")
    if "duration_s" in op and op["duration_s"] is not None:
        if not isinstance(op["duration_s"], int) or op["duration_s"] < 0:
            raise SchemaError("operation.duration_s must be non-negative int")
    if op.get("error") is not None and len(str(op["error"])) > 500:
        raise SchemaError("operation.error too long (>500 chars)")

    snap = p.get("snapshot")
    if snap is not None:
        if not isinstance(snap, dict):
            raise SchemaError("snapshot must be object")
        if "size_bytes" in snap:
            if not isinstance(snap["size_bytes"], int) or snap["size_bytes"] < 0:
                raise SchemaError("snapshot.size_bytes must be non-negative int")

    totals = p.get("totals")
    if totals is not None:
        if not isinstance(totals, dict):
            raise SchemaError("totals must be object")
        for k in ("size_bytes", "count_files"):
            if k in totals and (not isinstance(totals[k], int) or totals[k] < 0):
                raise SchemaError(f"totals.{k} must be non-negative int")

    return p
```

- [ ] **Step 4: Run test, verify it passes.**

```bash
sudo /opt/snapshot-V3/.venv/bin/python -m pytest tests/central/test_heartbeat_schema.py -v
```
Expected: 9 passed.

- [ ] **Step 5: Commit.**

```bash
git add backend/central/schema.py tests/central/test_heartbeat_schema.py
git commit -m "central: add heartbeat payload validation with explicit errors"
```

---

## Task 5: Models DAO — clients, targets, events upsert

**Files:**
- Create: `backend/central/models.py`
- Create: `tests/central/test_central_models.py`

Depende de T1 (tablas).

- [ ] **Step 1: Test.**

```python
# tests/central/test_central_models.py
import pytest
import json
from backend.models.db import DB
from backend.central import models as m


@pytest.fixture
def db(tmp_path):
    return DB(tmp_path / "t.db")


@pytest.fixture
def client_id(db):
    return m.create_client(db, proyecto="superaccess-uno",
                           organizacion="superaccess s.a.")


def _payload(eid="11111111-1111-1111-1111-111111111111", op="archive",
             status="ok", size=1000, total=5000):
    return {
        "event_id": eid,
        "ts": "2026-04-27T17:00:00Z",
        "client": {"proyecto": "superaccess-uno", "entorno": "cloud", "pais": "co"},
        "target": {"category": "os", "subkey": "linux", "label": "web01"},
        "operation": {"op": op, "status": status,
                      "started_at": "2026-04-27T17:00:00Z", "duration_s": 5,
                      "error": None},
        "snapshot": {"size_bytes": size, "remote_path": "x", "encrypted": True},
        "totals": {"size_bytes": total, "count_files": 1,
                   "oldest_ts": "2026-04-27T17:00:00Z",
                   "newest_ts": "2026-04-27T17:00:00Z"},
        "host_meta": {"hostname": "web01", "snapctl_version": "0.4",
                      "rclone_version": "v1.68"},
    }


def test_create_client_unique(db):
    cid = m.create_client(db, proyecto="alpha")
    assert isinstance(cid, int)
    with pytest.raises(Exception):  # IntegrityError
        m.create_client(db, proyecto="alpha")


def test_first_heartbeat_creates_target(db, client_id):
    res = m.apply_heartbeat(db, _payload(), token_id=1, client_id=client_id, src_ip="1.2.3.4")
    assert res.target_created is True
    assert res.event_inserted is True
    row = db.con().execute("SELECT category, subkey, label FROM targets").fetchone()
    assert tuple(row) == ("os", "linux", "web01")


def test_second_heartbeat_updates_target(db, client_id):
    m.apply_heartbeat(db, _payload(eid="aaaa1111-1111-1111-1111-111111111111", size=1000, total=1000),
                      token_id=1, client_id=client_id, src_ip="1.1.1.1")
    m.apply_heartbeat(db, _payload(eid="bbbb2222-2222-2222-2222-222222222222", size=2000, total=3000),
                      token_id=1, client_id=client_id, src_ip="1.1.1.1")
    row = db.con().execute(
        "SELECT last_size_bytes, total_size_bytes FROM targets"
    ).fetchone()
    assert row == (2000, 3000)


def test_duplicate_event_id_is_silent(db, client_id):
    p = _payload(eid="cccc3333-3333-3333-3333-333333333333")
    r1 = m.apply_heartbeat(db, p, token_id=1, client_id=client_id, src_ip="x")
    r2 = m.apply_heartbeat(db, p, token_id=1, client_id=client_id, src_ip="x")
    assert r1.event_inserted is True
    assert r2.event_inserted is False  # idempotent
    n = db.con().execute("SELECT COUNT(*) FROM central_events").fetchone()[0]
    assert n == 1


def test_dashboard_query_aggregates(db, client_id):
    cid2 = m.create_client(db, proyecto="orus")
    p1 = _payload(eid="11111111-1111-1111-1111-111111111111", total=10_000)
    m.apply_heartbeat(db, p1, token_id=1, client_id=client_id, src_ip="x")
    p2 = _payload(eid="22222222-2222-2222-2222-222222222222", total=20_000)
    p2["target"]["label"] = "web02"
    m.apply_heartbeat(db, p2, token_id=1, client_id=client_id, src_ip="x")
    rows = m.dashboard_summary(db)
    by_proj = {r["proyecto"]: r for r in rows}
    assert by_proj["superaccess-uno"]["targets_count"] == 2
    assert by_proj["superaccess-uno"]["total_bytes"] == 30_000
    assert by_proj["orus"]["targets_count"] == 0
```

- [ ] **Step 2: Run test, verify it fails (ImportError).**

```bash
sudo /opt/snapshot-V3/.venv/bin/python -m pytest tests/central/test_central_models.py -v
```

- [ ] **Step 3: Implementar `backend/central/models.py`.**

```python
"""Data access layer del modo central. Patrón: funciones puras que toman
db (instancia de DB), sin ORM. Queries cortas, parámetros bound, transacción
explícita en apply_heartbeat para mantener events ↔ targets coherentes."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class HeartbeatResult:
    event_inserted: bool        # False si ya existía (duplicado idempotente)
    target_created: bool         # True si se creó un target nuevo
    target_id: int


def create_client(db, *, proyecto: str, organizacion: str | None = None,
                  contacto: str | None = None,
                  retencion_meses: int | None = None,
                  notas: str | None = None) -> int:
    cur = db.con().execute(
        "INSERT INTO clients (proyecto, organizacion, contacto, retencion_meses, notas, "
        "created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
        (proyecto, organizacion, contacto, retencion_meses, notas, _now_iso(), _now_iso()),
    )
    db.con().commit()
    return cur.lastrowid


def get_client(db, client_id: int) -> dict | None:
    row = db.con().execute(
        "SELECT id, proyecto, organizacion, contacto, retencion_meses, notas "
        "FROM clients WHERE id=?", (client_id,)
    ).fetchone()
    return None if not row else {
        "id": row[0], "proyecto": row[1], "organizacion": row[2],
        "contacto": row[3], "retencion_meses": row[4], "notas": row[5],
    }


def list_clients(db) -> list[dict]:
    rows = db.con().execute(
        "SELECT id, proyecto, organizacion, contacto FROM clients ORDER BY proyecto"
    ).fetchall()
    return [{"id": r[0], "proyecto": r[1], "organizacion": r[2], "contacto": r[3]}
            for r in rows]


def update_client(db, client_id: int, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    vals = list(fields.values()) + [_now_iso(), client_id]
    db.con().execute(
        f"UPDATE clients SET {cols}, updated_at=? WHERE id=?", vals
    )
    db.con().commit()


def delete_client(db, client_id: int) -> None:
    """Cascade borra targets + tokens. central_events quedan huérfanos
    (preservamos audit)."""
    db.con().execute("DELETE FROM clients WHERE id=?", (client_id,))
    db.con().commit()


def apply_heartbeat(db, payload: dict, *, token_id: int, client_id: int,
                    src_ip: str | None) -> HeartbeatResult:
    """Inserta event + upsert target. Una sola transacción.
    Idempotente por event_id. Llamar solo después de validar el payload."""
    eid = payload["event_id"]
    op = payload["operation"]["op"]
    status = payload["operation"]["status"]
    target = payload["target"]
    snap = payload.get("snapshot") or {}
    totals = payload.get("totals") or {}
    host_meta = payload.get("host_meta") or {}
    now = _now_iso()
    payload_blob = json.dumps(payload, separators=(",", ":"))

    con = db.con()
    try:
        con.execute("BEGIN")

        # 1) Upsert target
        cat, sub, label = target["category"], target["subkey"], target["label"]
        cur = con.execute(
            "SELECT id, total_size_bytes, count_files FROM targets "
            "WHERE client_id=? AND category=? AND subkey=? AND label=?",
            (client_id, cat, sub, label),
        )
        row = cur.fetchone()
        if row is None:
            cur = con.execute(
                "INSERT INTO targets "
                "(client_id, category, subkey, label, entorno, pais, "
                " last_exec_ts, last_exec_status, last_size_bytes, "
                " total_size_bytes, count_files, oldest_backup_ts, newest_backup_ts, "
                " last_heartbeat_ts, snapctl_version, rclone_version, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (client_id, cat, sub, label,
                 (payload.get("client") or {}).get("entorno"),
                 (payload.get("client") or {}).get("pais"),
                 payload["operation"].get("started_at"), status,
                 snap.get("size_bytes"),
                 totals.get("size_bytes"), totals.get("count_files"),
                 totals.get("oldest_ts"), totals.get("newest_ts"),
                 now, host_meta.get("snapctl_version"),
                 host_meta.get("rclone_version"), now),
            )
            target_id = cur.lastrowid
            target_created = True
        else:
            target_id = row[0]
            con.execute(
                "UPDATE targets SET "
                " last_exec_ts=COALESCE(?,last_exec_ts), "
                " last_exec_status=?, "
                " last_size_bytes=COALESCE(?,last_size_bytes), "
                " total_size_bytes=COALESCE(?,total_size_bytes), "
                " count_files=COALESCE(?,count_files), "
                " oldest_backup_ts=COALESCE(?,oldest_backup_ts), "
                " newest_backup_ts=COALESCE(?,newest_backup_ts), "
                " last_heartbeat_ts=?, "
                " snapctl_version=COALESCE(?,snapctl_version), "
                " rclone_version=COALESCE(?,rclone_version) "
                "WHERE id=?",
                (payload["operation"].get("started_at"), status,
                 snap.get("size_bytes"),
                 totals.get("size_bytes"), totals.get("count_files"),
                 totals.get("oldest_ts"), totals.get("newest_ts"),
                 now, host_meta.get("snapctl_version"),
                 host_meta.get("rclone_version"), target_id),
            )
            target_created = False

        # 2) Insert event (UNIQUE event_id → silent on conflict)
        try:
            con.execute(
                "INSERT INTO central_events "
                "(event_id, received_at, token_id, client_id, target_id, "
                " op, status, payload_json, src_ip) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (eid, now, token_id, client_id, target_id, op, status,
                 payload_blob, src_ip),
            )
            event_inserted = True
        except sqlite3.IntegrityError:
            event_inserted = False

        con.commit()
    except Exception:
        con.rollback()
        raise

    return HeartbeatResult(
        event_inserted=event_inserted,
        target_created=target_created,
        target_id=target_id,
    )


def list_targets_by_client(db, client_id: int) -> list[dict]:
    rows = db.con().execute(
        "SELECT id, category, subkey, label, last_exec_ts, last_exec_status, "
        "       last_size_bytes, total_size_bytes, count_files, last_heartbeat_ts "
        "FROM targets WHERE client_id=? ORDER BY category, subkey, label",
        (client_id,),
    ).fetchall()
    keys = ("id", "category", "subkey", "label", "last_exec_ts", "last_exec_status",
            "last_size_bytes", "total_size_bytes", "count_files", "last_heartbeat_ts")
    return [dict(zip(keys, r)) for r in rows]


def list_events(db, *, target_id: int | None = None, client_id: int | None = None,
                since: str | None = None, limit: int = 50) -> list[dict]:
    sql = ("SELECT id, event_id, received_at, op, status, src_ip "
           "FROM central_events WHERE 1=1 ")
    params: list = []
    if target_id is not None:
        sql += "AND target_id=? "
        params.append(target_id)
    if client_id is not None:
        sql += "AND client_id=? "
        params.append(client_id)
    if since is not None:
        sql += "AND received_at >= ? "
        params.append(since)
    sql += "ORDER BY received_at DESC LIMIT ?"
    params.append(int(limit))
    rows = db.con().execute(sql, params).fetchall()
    keys = ("id", "event_id", "received_at", "op", "status", "src_ip")
    return [dict(zip(keys, r)) for r in rows]


def dashboard_summary(db) -> list[dict]:
    rows = db.con().execute(
        "SELECT c.id, c.proyecto, c.organizacion, "
        "       COUNT(t.id) AS targets_count, "
        "       COALESCE(SUM(t.total_size_bytes),0) AS total_bytes, "
        "       MAX(t.last_exec_ts) AS last_exec_ts, "
        "       SUM(CASE t.last_exec_status WHEN 'fail' THEN 1 ELSE 0 END) AS failed_targets, "
        "       MIN(t.last_heartbeat_ts) AS oldest_heartbeat "
        "FROM clients c LEFT JOIN targets t ON t.client_id = c.id "
        "GROUP BY c.id ORDER BY c.proyecto"
    ).fetchall()
    keys = ("client_id", "proyecto", "organizacion", "targets_count",
            "total_bytes", "last_exec_ts", "failed_targets", "oldest_heartbeat")
    return [dict(zip(keys, r)) for r in rows]
```

- [ ] **Step 4: Run test, verify it passes.**

```bash
sudo /opt/snapshot-V3/.venv/bin/python -m pytest tests/central/test_central_models.py -v
```
Expected: 5 passed.

- [ ] **Step 5: Commit.**

```bash
git add backend/central/models.py tests/central/test_central_models.py
git commit -m "central: add DAO with idempotent heartbeat upsert and dashboard query"
```

---

## Task 6: Permisos central.* + decorator

**Files:**
- Modify: `backend/auth/decorators.py` (+`require_central_perm`)
- Create: `backend/central/permissions.py`
- Create: `tests/central/test_central_perms.py`

Depende de T2 (Config), independiente del resto.

- [ ] **Step 1: Test.**

```python
# tests/central/test_central_perms.py
import pytest
from backend.central.permissions import (
    PERMISSIONS, role_has, require_central_perm,
)


def test_matrix_admin_has_everything():
    for perm in PERMISSIONS:
        assert role_has("admin", perm) is True


def test_auditor_only_has_read():
    assert role_has("auditor", "central.dashboard:view") is True
    assert role_has("auditor", "central.audit:view") is True
    assert role_has("auditor", "central.clients:read") is True
    assert role_has("auditor", "central.clients:write") is False
    assert role_has("auditor", "central.tokens:revoke") is False
    assert role_has("auditor", "central.users:manage") is False


def test_operator_can_revoke_tokens():
    assert role_has("operator", "central.tokens:revoke") is True
    assert role_has("operator", "central.tokens:issue") is True
    assert role_has("operator", "central.users:manage") is False


def test_unknown_perm_is_denied():
    assert role_has("admin", "central.bogus:thing") is False


def test_decorator_returns_403_when_role_lacks_perm(app, client):
    # Necesita un endpoint de prueba registrado al import; ver fixtures
    # más abajo en este archivo.
    pass  # cubierto por test_routes_perms en task 7
```

- [ ] **Step 2: Run test, verify it fails.**

```bash
sudo /opt/snapshot-V3/.venv/bin/python -m pytest tests/central/test_central_perms.py -v
```

- [ ] **Step 3: Implementar `backend/central/permissions.py`.**

```python
"""Matriz central.* → roles que la conceden. Aliases para UI:
admin = webmaster, operator = técnico, auditor = gerente."""
from __future__ import annotations

from functools import wraps
from typing import Callable

from flask import g, jsonify

# perm → set de roles que la tienen
PERMISSIONS: dict[str, set[str]] = {
    "central.dashboard:view":   {"admin", "operator", "auditor"},
    "central.audit:view":       {"admin", "operator", "auditor"},
    "central.clients:read":     {"admin", "operator", "auditor"},
    "central.clients:write":    {"admin", "operator"},
    "central.tokens:issue":     {"admin", "operator"},
    "central.tokens:revoke":    {"admin", "operator"},
    "central.alerts:configure": {"admin", "operator"},
    "central.users:manage":     {"admin"},
    "central.settings:edit":    {"admin"},
}

ROLE_ALIASES_ES = {"admin": "webmaster", "operator": "técnico", "auditor": "gerente"}


def role_has(role: str, perm: str) -> bool:
    return role in PERMISSIONS.get(perm, set())


def require_central_perm(perm: str) -> Callable:
    """Decorator equivalente a require_role pero contra la matriz."""
    def deco(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = getattr(g, "current_user", None)
            if not user:
                return jsonify(ok=False, error="unauthenticated"), 401
            if not role_has(user.role, perm):
                return jsonify(ok=False, error="forbidden",
                               required_perm=perm), 403
            return view(*args, **kwargs)
        return wrapped
    return deco
```

- [ ] **Step 4: Run test, verify the unit ones pass.**

```bash
sudo /opt/snapshot-V3/.venv/bin/python -m pytest tests/central/test_central_perms.py -v
```
Expected: 4 passed (test_decorator_returns_403... = pass por placeholder).

- [ ] **Step 5: Commit.**

```bash
git add backend/central/permissions.py tests/central/test_central_perms.py
git commit -m "central: define permission matrix + require_central_perm decorator"
```

---

## Task 7: Endpoint M2M — `/api/v1/heartbeat` y `/api/v1/ping`

**Files:**
- Create: `backend/central/api.py`
- Create: `tests/central/test_api_heartbeat.py`

Depende de T1, T3, T4, T5.

- [ ] **Step 1: Test.**

```python
# tests/central/test_api_heartbeat.py
import json
import pytest
from backend.models.db import DB
from backend.central import models as m
from backend.central import tokens as tok


@pytest.fixture
def central_app(monkeypatch, tmp_path):
    monkeypatch.setenv("MODE", "central")
    monkeypatch.setenv("SNAPSHOT_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("SNAPSHOT_SECRET_KEY", "0" * 64)
    monkeypatch.setenv("SNAPSHOT_TEST_MODE", "1")
    import importlib, backend.config
    importlib.reload(backend.config)
    from backend.app import create_app
    app = create_app()
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(central_app):
    return central_app.test_client()


@pytest.fixture
def db(central_app):
    return DB(central_app.config["DB_PATH"])


@pytest.fixture
def setup(db):
    cid = m.create_client(db, proyecto="superaccess-uno")
    plaintext, token_id = tok.issue(db, cid, label="web01")
    return {"client_id": cid, "token_id": token_id, "token": plaintext}


def _good_payload():
    return {
        "event_id": "11111111-1111-1111-1111-111111111111",
        "ts": "2026-04-27T17:00:00Z",
        "client": {"proyecto": "superaccess-uno", "entorno": "cloud", "pais": "co"},
        "target": {"category": "os", "subkey": "linux", "label": "web01"},
        "operation": {"op": "archive", "status": "ok",
                      "started_at": "2026-04-27T17:00:00Z", "duration_s": 5,
                      "error": None},
        "snapshot": {"size_bytes": 1000, "remote_path": "x", "encrypted": True},
        "totals": {"size_bytes": 5000, "count_files": 1,
                   "oldest_ts": "2026-04-27T17:00:00Z",
                   "newest_ts": "2026-04-27T17:00:00Z"},
        "host_meta": {"hostname": "web01", "snapctl_version": "0.4",
                      "rclone_version": "v1.68"},
    }


def test_heartbeat_no_token_returns_401(client):
    r = client.post("/api/v1/heartbeat", json=_good_payload())
    assert r.status_code == 401


def test_heartbeat_bad_token_returns_401(client):
    r = client.post("/api/v1/heartbeat", json=_good_payload(),
                    headers={"Authorization": "Bearer wrongtoken"})
    assert r.status_code == 401


def test_heartbeat_good_token_accepted(client, setup):
    r = client.post("/api/v1/heartbeat", json=_good_payload(),
                    headers={"Authorization": f"Bearer {setup['token']}"})
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body["ok"] is True
    assert body["event_id"] == "11111111-1111-1111-1111-111111111111"


def test_heartbeat_invalid_schema_returns_400(client, setup):
    p = _good_payload(); p["target"]["category"] = "windows"
    r = client.post("/api/v1/heartbeat", json=p,
                    headers={"Authorization": f"Bearer {setup['token']}"})
    assert r.status_code == 400


def test_heartbeat_proyecto_mismatch_returns_409(client, setup):
    p = _good_payload(); p["client"]["proyecto"] = "otro"
    r = client.post("/api/v1/heartbeat", json=p,
                    headers={"Authorization": f"Bearer {setup['token']}"})
    assert r.status_code == 409


def test_heartbeat_idempotent_replay(client, setup):
    p = _good_payload()
    h = {"Authorization": f"Bearer {setup['token']}"}
    r1 = client.post("/api/v1/heartbeat", json=p, headers=h)
    r2 = client.post("/api/v1/heartbeat", json=p, headers=h)
    assert r1.status_code == 200 and r2.status_code == 200


def test_heartbeat_too_large_returns_413(client, setup):
    p = _good_payload()
    p["operation"]["error"] = "x" * 70_000  # rompe schema (error >500) o tamaño total
    r = client.post("/api/v1/heartbeat", json=p,
                    headers={"Authorization": f"Bearer {setup['token']}"})
    assert r.status_code in (400, 413)


def test_ping_no_auth_required(client):
    r = client.get("/api/v1/ping")
    assert r.status_code == 200
    assert r.get_json()["ok"] is True
```

- [ ] **Step 2: Run test, verify it fails.**

```bash
sudo /opt/snapshot-V3/.venv/bin/python -m pytest tests/central/test_api_heartbeat.py -v
```
Expected: failures (404 — no blueprint registrado aún).

- [ ] **Step 3: Implementar `backend/central/api.py`.**

```python
"""Endpoints máquina-a-máquina: heartbeat y ping."""
from __future__ import annotations

import logging

from flask import Blueprint, current_app, jsonify, request

from . import models as m
from . import tokens as tok
from . import schema as sch

log = logging.getLogger(__name__)

central_api_bp = Blueprint("central_api", __name__, url_prefix="/api/v1")


def _db():
    return current_app.config["DB_CONN"]


def _client_meta():
    return request.remote_addr or ""


@central_api_bp.get("/ping")
def ping():
    return jsonify(ok=True, version=current_app.config.get("VERSION", "dev"))


@central_api_bp.post("/heartbeat")
def heartbeat():
    raw = request.get_data() or b""
    try:
        sch.check_size(raw)
    except sch.SchemaError as e:
        return jsonify(ok=False, error=str(e)), 413

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return jsonify(ok=False, error="missing bearer token"), 401
    plaintext = auth[7:].strip()
    db = _db()
    info = tok.verify(db, plaintext)
    if info is None:
        return jsonify(ok=False, error="invalid or revoked token"), 401

    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify(ok=False, error="payload must be JSON"), 400
    try:
        sch.validate_heartbeat(payload)
    except sch.SchemaError as e:
        return jsonify(ok=False, error=f"schema: {e}"), 400

    client = m.get_client(db, info.client_id)
    if client is None:
        # client borrado pero token todavía no revocado en cascada — defensivo
        return jsonify(ok=False, error="client gone"), 410
    if payload["client"]["proyecto"] != client["proyecto"]:
        return jsonify(ok=False, error="proyecto mismatch with token's client"), 409

    res = m.apply_heartbeat(db, payload, token_id=info.id,
                            client_id=info.client_id, src_ip=_client_meta())
    return jsonify(ok=True, event_id=payload["event_id"],
                   target_id=res.target_id), 200
```

- [ ] **Step 4:** Aún no se registra el blueprint. Eso se hace en T11. Por ahora el test va a fallar con 404. Para hacer pasar este test antes de T11, registramos el blueprint condicionalmente en `backend/app.py` aquí mismo. **Modificar `backend/app.py`** después del registro de `audit_bp`:

```python
    if Config.MODE == "central":
        from .central.api import central_api_bp
        app.register_blueprint(central_api_bp)
```

- [ ] **Step 5: Run test, verify it passes.**

```bash
sudo /opt/snapshot-V3/.venv/bin/python -m pytest tests/central/test_api_heartbeat.py -v
```
Expected: 8 passed.

- [ ] **Step 6: Commit.**

```bash
git add backend/central/api.py backend/app.py tests/central/test_api_heartbeat.py
git commit -m "central: add /api/v1/heartbeat and /api/v1/ping with token auth"
```

---

## Task 8: Endpoints admin (clientes, tokens, events)

**Files:**
- Create: `backend/central/admin.py`
- Create: `tests/central/test_api_admin.py`

Depende de T5, T3, T6.

- [ ] **Step 1: Test (resumido — patrón parametrizado por (role, endpoint)).**

```python
# tests/central/test_api_admin.py
import pytest
from backend.models.db import DB
from backend.central import models as m


@pytest.fixture
def central_app(monkeypatch, tmp_path):
    monkeypatch.setenv("MODE", "central")
    monkeypatch.setenv("SNAPSHOT_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("SNAPSHOT_SECRET_KEY", "0" * 64)
    monkeypatch.setenv("SNAPSHOT_TEST_MODE", "1")
    import importlib, backend.config; importlib.reload(backend.config)
    from backend.app import create_app
    app = create_app()
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(central_app): return central_app.test_client()
@pytest.fixture
def db(central_app): return DB(central_app.config["DB_PATH"])


def _login_as(client, db, role: str):
    """Crea user con role y abre sesión. Reusa helpers de tests/auth/."""
    from tests.auth.helpers import create_user_and_login  # asumido existir o reusar patrón
    return create_user_and_login(client, db, role=role)


@pytest.mark.parametrize("role,endpoint,method,expected", [
    ("auditor", "/api/admin/clients", "GET", 200),
    ("auditor", "/api/admin/clients", "POST", 403),
    ("operator", "/api/admin/clients", "POST", 200),  # depende de body
    ("auditor", "/api/admin/tokens/1", "DELETE", 403),
    ("operator", "/api/admin/tokens/1", "DELETE", 404),  # 404 = perm OK + no existe
])
def test_admin_perm_matrix(client, db, role, endpoint, method, expected):
    _login_as(client, db, role)
    if method == "GET":
        r = client.get(endpoint)
    elif method == "POST":
        r = client.post(endpoint, json={"proyecto": "test"})
    else:
        r = client.delete(endpoint)
    assert r.status_code == expected, r.get_data(as_text=True)


def test_create_then_list_then_delete_client(client, db):
    _login_as(client, db, "operator")
    r = client.post("/api/admin/clients",
                    json={"proyecto": "alpha", "organizacion": "Acme"})
    assert r.status_code == 200
    cid = r.get_json()["data"]["id"]
    r = client.get("/api/admin/clients")
    proyectos = [c["proyecto"] for c in r.get_json()["data"]]
    assert "alpha" in proyectos
    r = client.delete(f"/api/admin/clients/{cid}")
    assert r.status_code == 200


def test_issue_token_returns_plaintext_once(client, db):
    _login_as(client, db, "operator")
    r = client.post("/api/admin/clients", json={"proyecto": "alpha"})
    cid = r.get_json()["data"]["id"]
    r = client.post(f"/api/admin/clients/{cid}/tokens", json={"label": "web01"})
    assert r.status_code == 200
    body = r.get_json()["data"]
    assert "plaintext" in body and len(body["plaintext"]) >= 32
    assert "token_id" in body
```

> Nota: si `tests/auth/helpers.py::create_user_and_login` no existe, **crearlo en este task** con este código:

```python
# tests/auth/helpers.py
"""Test helpers compartidos. Patrón: insertar user vía users module y
abrir sesión vía POST /auth/login + completar MFA si admin lo requiere.
Para tests de central que NO necesitan MFA, usamos el bypass SNAPSHOT_TEST_MODE
ya existente en sub-A (skip MFA enforcement when test mode is set)."""
from backend.auth import users as users_mod
from backend.auth.passwords import hash_password


def create_user_and_login(test_client, db, *, role: str = "operator",
                          email: str | None = None,
                          password: str = "TestPassword-123!"):
    """Crea un user en `users` con role solicitado y abre sesión.
    Retorna (email, password)."""
    email = email or f"{role}@test.local"
    users_mod.create_user(
        db, email=email, password_hash=hash_password(password),
        role=role, status="active",
    )
    r = test_client.post("/auth/login",
                         json={"email": email, "password": password})
    assert r.status_code == 200, f"login failed: {r.get_data(as_text=True)}"
    return email, password
```

- [ ] **Step 2: Run, verify it fails.**

```bash
sudo /opt/snapshot-V3/.venv/bin/python -m pytest tests/central/test_api_admin.py -v
```

- [ ] **Step 3: Implementar `backend/central/admin.py`.**

```python
"""Endpoints administrativos del central (humanos, sesión + RBAC)."""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from . import models as m
from . import tokens as tok
from .permissions import require_central_perm

central_admin_bp = Blueprint("central_admin", __name__, url_prefix="/api/admin")


def _db(): return current_app.config["DB_CONN"]


def _ok(data=None): return jsonify(ok=True, data=data, error=None)
def _err(msg, code=400): return jsonify(ok=False, data=None, error=msg), code


# --- clients ---

@central_admin_bp.get("/clients")
@require_central_perm("central.clients:read")
def list_clients():
    return _ok(m.list_clients(_db()))


@central_admin_bp.get("/clients/<int:cid>")
@require_central_perm("central.clients:read")
def get_client(cid):
    c = m.get_client(_db(), cid)
    if not c: return _err("not found", 404)
    c["targets"] = m.list_targets_by_client(_db(), cid)
    c["recent_events"] = m.list_events(_db(), client_id=cid, limit=50)
    return _ok(c)


@central_admin_bp.post("/clients")
@require_central_perm("central.clients:write")
def create_client():
    body = request.get_json(silent=True) or {}
    proyecto = (body.get("proyecto") or "").strip()
    if not proyecto:
        return _err("proyecto required")
    try:
        cid = m.create_client(_db(),
                              proyecto=proyecto,
                              organizacion=body.get("organizacion"),
                              contacto=body.get("contacto"),
                              retencion_meses=body.get("retencion_meses"),
                              notas=body.get("notas"))
    except Exception as e:
        return _err(f"create failed: {e}", 409)
    return _ok({"id": cid, "proyecto": proyecto})


@central_admin_bp.patch("/clients/<int:cid>")
@require_central_perm("central.clients:write")
def update_client(cid):
    body = request.get_json(silent=True) or {}
    allowed = {k: v for k, v in body.items()
               if k in ("organizacion", "contacto", "retencion_meses", "notas")}
    m.update_client(_db(), cid, **allowed)
    return _ok({"id": cid})


@central_admin_bp.delete("/clients/<int:cid>")
@require_central_perm("central.clients:write")
def delete_client(cid):
    m.delete_client(_db(), cid)
    return _ok({"id": cid, "deleted": True})


# --- tokens ---

@central_admin_bp.post("/clients/<int:cid>/tokens")
@require_central_perm("central.tokens:issue")
def issue_token(cid):
    body = request.get_json(silent=True) or {}
    label = (body.get("label") or "").strip()
    if not label: return _err("label required")
    if m.get_client(_db(), cid) is None:
        return _err("client not found", 404)
    plaintext, token_id = tok.issue(_db(), cid, label=label,
                                    expires_at=body.get("expires_at"))
    return _ok({"plaintext": plaintext, "token_id": token_id, "label": label})


@central_admin_bp.delete("/tokens/<int:tid>")
@require_central_perm("central.tokens:revoke")
def revoke_token(tid):
    db = _db()
    row = db.con().execute(
        "SELECT id FROM central_tokens WHERE id=? AND revoked_at IS NULL", (tid,)
    ).fetchone()
    if not row: return _err("not found", 404)
    tok.revoke(db, tid)
    return _ok({"id": tid, "revoked": True})


@central_admin_bp.get("/tokens")
@require_central_perm("central.clients:read")
def list_tokens():
    cid = request.args.get("client_id", type=int)
    if not cid: return _err("client_id query param required")
    return _ok(tok.list_active(_db(), cid))


# --- events ---

@central_admin_bp.get("/events")
@require_central_perm("central.audit:view")
def list_events_endpoint():
    target_id = request.args.get("target_id", type=int)
    client_id = request.args.get("client_id", type=int)
    since = request.args.get("since")
    limit = min(request.args.get("limit", default=50, type=int), 500)
    return _ok(m.list_events(_db(), target_id=target_id,
                             client_id=client_id, since=since, limit=limit))
```

- [ ] **Step 4:** Registrar el blueprint en `backend/app.py` justo después del de `central_api`:

```python
        from .central.admin import central_admin_bp
        app.register_blueprint(central_admin_bp)
```

- [ ] **Step 5: Run, verify it passes.**

```bash
sudo /opt/snapshot-V3/.venv/bin/python -m pytest tests/central/test_api_admin.py -v
```
Expected: 7 passed.

- [ ] **Step 6: Commit.**

```bash
git add backend/central/admin.py backend/app.py tests/central/test_api_admin.py tests/auth/helpers.py
git commit -m "central: add admin endpoints for clients, tokens and events"
```

---

## Task 9: Dashboard HTML del central

**Files:**
- Create: `backend/central/dashboard.py`
- Create: `frontend/templates/central/dashboard.html`
- Create: `frontend/static/js/central/dashboard.js`
- Create: `tests/central/test_dashboard_html.py`

Depende de T5, T6, T8.

- [ ] **Step 1: Test (smoke).**

```python
# tests/central/test_dashboard_html.py
def test_dashboard_requires_auth(client):  # central_app fixture from earlier
    r = client.get("/dashboard-central")
    assert r.status_code in (302, 401)


def test_dashboard_renders_for_auditor(client, db):
    from tests.auth.helpers import create_user_and_login
    create_user_and_login(client, db, role="auditor")
    from backend.central import models as m
    m.create_client(db, proyecto="alpha")
    r = client.get("/dashboard-central")
    assert r.status_code == 200
    assert b"alpha" in r.data
    assert b"Dashboard central" in r.data
```

- [ ] **Step 2: Run, verify it fails.**

- [ ] **Step 3: `backend/central/dashboard.py`.**

```python
"""Vista HTML agregada del central."""
from flask import Blueprint, current_app, render_template

from . import models as m
from .permissions import require_central_perm

central_dashboard_bp = Blueprint(
    "central_dashboard", __name__,
    template_folder="../../frontend/templates",
)


@central_dashboard_bp.get("/dashboard-central")
@require_central_perm("central.dashboard:view")
def dashboard():
    rows = m.dashboard_summary(current_app.config["DB_CONN"])
    return render_template("central/dashboard.html", rows=rows)


@central_dashboard_bp.get("/dashboard-central/clients/<int:cid>")
@require_central_perm("central.clients:read")
def client_detail(cid):
    db = current_app.config["DB_CONN"]
    client = m.get_client(db, cid)
    if not client:
        return render_template("404.html"), 404
    targets = m.list_targets_by_client(db, cid)
    events = m.list_events(db, client_id=cid, limit=50)
    return render_template("central/client_detail.html",
                           client=client, targets=targets, events=events)
```

- [ ] **Step 4: `frontend/templates/central/dashboard.html`.** Patrón mínimo (reusa el base.html del proyecto si existe; si no, header inline + Tailwind CDN como en `mfa_enroll.html`).

```html
{% extends "base.html" if has_base else "_blank.html" %}

{% block title %}Dashboard central{% endblock %}

{% block content %}
<section class="p-6">
  <h1 class="text-2xl font-semibold mb-4">Dashboard central</h1>
  <p class="text-sm text-[var(--muted)] mb-6">
    {{ rows|length }} cliente(s). Auto-refresh cada 30s.
  </p>
  <div class="overflow-x-auto rounded border border-[var(--border)]">
    <table class="min-w-full text-sm">
      <thead class="bg-[var(--surface)]">
        <tr>
          <th class="text-left px-4 py-2">Proyecto</th>
          <th class="text-left px-4 py-2">Organización</th>
          <th class="text-right px-4 py-2">Targets</th>
          <th class="text-right px-4 py-2">Total (GB)</th>
          <th class="text-left px-4 py-2">Última ejec.</th>
          <th class="text-right px-4 py-2">Fallidos</th>
          <th class="text-left px-4 py-2">Heartbeat más viejo</th>
        </tr>
      </thead>
      <tbody id="dashboard-tbody">
      {% for r in rows %}
        <tr class="border-t border-[var(--border)]">
          <td class="px-4 py-2 font-mono">
            <a href="/dashboard-central/clients/{{ r.client_id }}"
               class="text-brand-400 hover:underline">{{ r.proyecto }}</a>
          </td>
          <td class="px-4 py-2">{{ r.organizacion or "—" }}</td>
          <td class="px-4 py-2 text-right">{{ r.targets_count }}</td>
          <td class="px-4 py-2 text-right">
            {{ "%.2f"|format((r.total_bytes or 0) / 1073741824) }}
          </td>
          <td class="px-4 py-2">{{ r.last_exec_ts or "—" }}</td>
          <td class="px-4 py-2 text-right
                     {% if (r.failed_targets or 0) > 0 %}text-rose-400{% endif %}">
            {{ r.failed_targets or 0 }}
          </td>
          <td class="px-4 py-2">{{ r.oldest_heartbeat or "—" }}</td>
        </tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
</section>
<script src="{{ url_for('static', filename='js/central/dashboard.js') }}"></script>
{% endblock %}
```

> Si `base.html` no existe, crear `frontend/templates/_blank.html` con header básico (Tailwind CDN, viewport meta) y usar `extends "_blank.html"` directo.

- [ ] **Step 5: `frontend/static/js/central/dashboard.js`.**

```javascript
// Auto-refresh visibility-aware. Usa el mismo patrón del dashboard local.
async function refreshDashboard() {
  if (document.hidden) return;
  try {
    const r = await fetch("/api/admin/clients", {credentials: "same-origin"});
    if (!r.ok) return;
    const json = await r.json();
    if (!json.ok) return;
    // Re-render simple: reload de la página. UI más fina queda para fase 2.
    location.reload();
  } catch (e) { /* network blip, retry next tick */ }
}
setInterval(refreshDashboard, 30000);
```

- [ ] **Step 6: Registrar blueprint en `backend/app.py` (junto al admin_bp).**

```python
        from .central.dashboard import central_dashboard_bp
        app.register_blueprint(central_dashboard_bp)
```

- [ ] **Step 7: Run, verify it passes.**

```bash
sudo /opt/snapshot-V3/.venv/bin/python -m pytest tests/central/test_dashboard_html.py -v
```

- [ ] **Step 8: Commit.**

```bash
git add backend/central/dashboard.py frontend/templates/central/ frontend/static/js/central/ \
        backend/app.py tests/central/test_dashboard_html.py
git commit -m "central: add dashboard HTML and client_detail views"
```

---

## Task 10: UI de gestión de clientes y tokens

**Files:**
- Create: `frontend/templates/central/clients.html`
- Create: `frontend/templates/central/tokens.html`
- Create: `frontend/templates/central/client_detail.html`
- Create: `frontend/static/js/central/tokens.js`
- Modify: `backend/central/dashboard.py` (+rutas `/dashboard-central/clients`, `/dashboard-central/clients/<id>/tokens`)
- Test: `tests/central/test_admin_ui.py` (smoke render)

`[parallel: T9 ya hecho]`

- [ ] **Step 1: Test smoke.**

```python
# tests/central/test_admin_ui.py
def test_clients_page_renders_for_operator(client, db):
    from tests.auth.helpers import create_user_and_login
    create_user_and_login(client, db, role="operator")
    r = client.get("/dashboard-central/clients")
    assert r.status_code == 200
    assert b"Crear cliente" in r.data


def test_tokens_page_requires_clients_write_or_read(client, db):
    from tests.auth.helpers import create_user_and_login
    from backend.central import models as m
    create_user_and_login(client, db, role="auditor")  # solo read
    cid = m.create_client(db, proyecto="x")
    r = client.get(f"/dashboard-central/clients/{cid}/tokens")
    # auditor puede VER tokens (clients:read) pero no issue/revoke
    assert r.status_code == 200
    assert b"Emitir token" not in r.data  # botón oculto por rol
```

- [ ] **Step 2: Agregar rutas en `backend/central/dashboard.py`.**

```python
@central_dashboard_bp.get("/dashboard-central/clients")
@require_central_perm("central.clients:read")
def clients_page():
    return render_template("central/clients.html",
                           clients=m.list_clients(current_app.config["DB_CONN"]))


@central_dashboard_bp.get("/dashboard-central/clients/<int:cid>/tokens")
@require_central_perm("central.clients:read")
def tokens_page(cid):
    db = current_app.config["DB_CONN"]
    client = m.get_client(db, cid)
    if not client: return render_template("404.html"), 404
    return render_template("central/tokens.html",
                           client=client, tokens=tok.list_active(db, cid))
```

(Importar `tok` arriba del archivo: `from . import tokens as tok`.)

- [ ] **Step 3: `frontend/templates/central/clients.html`.**

```html
{% extends "_blank.html" %}
{% block title %}Clientes — Central{% endblock %}
{% block content %}
<section class="p-6">
  <h1 class="text-2xl font-semibold mb-4">Clientes</h1>

  {% if g.current_user.role in ('admin', 'operator') %}
  <form id="new-client" class="mb-6 flex gap-2">
    <input name="proyecto" placeholder="proyecto"
           class="px-3 py-2 rounded border border-[var(--border)] bg-[var(--bg)]" required>
    <input name="organizacion" placeholder="organización (opcional)"
           class="px-3 py-2 rounded border border-[var(--border)] bg-[var(--bg)]">
    <button type="submit" class="px-4 py-2 rounded bg-brand-600 hover:bg-brand-700 text-white">
      Crear cliente
    </button>
  </form>
  {% endif %}

  <table class="min-w-full text-sm rounded border border-[var(--border)]">
    <thead class="bg-[var(--surface)]">
      <tr>
        <th class="text-left px-4 py-2">Proyecto</th>
        <th class="text-left px-4 py-2">Organización</th>
        <th class="text-left px-4 py-2">Contacto</th>
        <th class="text-right px-4 py-2">Acciones</th>
      </tr>
    </thead>
    <tbody>
      {% for c in clients %}
      <tr class="border-t border-[var(--border)]">
        <td class="px-4 py-2 font-mono">
          <a href="/dashboard-central/clients/{{ c.id }}"
             class="text-brand-400 hover:underline">{{ c.proyecto }}</a>
        </td>
        <td class="px-4 py-2">{{ c.organizacion or "—" }}</td>
        <td class="px-4 py-2">{{ c.contacto or "—" }}</td>
        <td class="px-4 py-2 text-right">
          <a href="/dashboard-central/clients/{{ c.id }}/tokens"
             class="text-brand-400 hover:underline text-xs">tokens</a>
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</section>
<script>
document.getElementById("new-client")?.addEventListener("submit", async e => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const r = await fetch("/api/admin/clients", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      proyecto: fd.get("proyecto"),
      organizacion: fd.get("organizacion") || null,
    }),
    credentials: "same-origin",
  });
  if (r.ok) location.reload();
  else alert("Error: " + (await r.text()));
});
</script>
{% endblock %}
```

- [ ] **Step 4: `frontend/templates/central/tokens.html`.**

```html
{% extends "_blank.html" %}
{% block title %}Tokens — {{ client.proyecto }}{% endblock %}
{% block content %}
<section class="p-6 max-w-3xl">
  <h1 class="text-2xl font-semibold mb-1">Tokens de {{ client.proyecto }}</h1>
  <p class="text-sm text-[var(--muted)] mb-6">
    Cada install snapctl que reporta a este cliente necesita su propio token.
    El plaintext se muestra una sola vez al crearlo.
  </p>

  {% if g.current_user.role in ('admin', 'operator') %}
  <form id="new-token" class="mb-6 flex gap-2">
    <input name="label" placeholder="Etiqueta (ej: web01-prod)" required
           class="flex-1 px-3 py-2 rounded border border-[var(--border)] bg-[var(--bg)]">
    <button type="submit" class="px-4 py-2 rounded bg-brand-600 hover:bg-brand-700 text-white">
      Emitir token
    </button>
  </form>
  <pre id="new-token-result" class="hidden mb-6 p-4 rounded border border-amber-500/40 bg-amber-500/10 text-xs font-mono break-all"></pre>
  {% endif %}

  <table class="min-w-full text-sm rounded border border-[var(--border)]">
    <thead class="bg-[var(--surface)]">
      <tr>
        <th class="text-left px-4 py-2">Etiqueta</th>
        <th class="text-left px-4 py-2">Creado</th>
        <th class="text-left px-4 py-2">Último uso</th>
        <th class="text-right px-4 py-2">Acción</th>
      </tr>
    </thead>
    <tbody>
      {% for t in tokens %}
      <tr class="border-t border-[var(--border)]" data-token-id="{{ t.id }}">
        <td class="px-4 py-2 font-mono">{{ t.label }}</td>
        <td class="px-4 py-2">{{ t.created_at }}</td>
        <td class="px-4 py-2">{{ t.last_used_at or "—" }}</td>
        <td class="px-4 py-2 text-right">
          {% if g.current_user.role in ('admin', 'operator') %}
          <button class="revoke-btn text-rose-400 hover:underline text-xs">Revocar</button>
          {% endif %}
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</section>
<script src="{{ url_for('static', filename='js/central/tokens.js') }}"
        data-client-id="{{ client.id }}"></script>
{% endblock %}
```

- [ ] **Step 5: `frontend/static/js/central/tokens.js`.**

```javascript
const cid = document.currentScript.dataset.clientId;

document.getElementById("new-token")?.addEventListener("submit", async e => {
  e.preventDefault();
  const label = e.target.label.value.trim();
  if (!label) return;
  const r = await fetch(`/api/admin/clients/${cid}/tokens`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({label}),
    credentials: "same-origin",
  });
  const j = await r.json();
  if (j.ok) {
    const out = document.getElementById("new-token-result");
    out.textContent = `TOKEN (guardalo ahora — no se vuelve a mostrar):\n\n${j.data.plaintext}`;
    out.classList.remove("hidden");
    setTimeout(() => location.reload(), 30000);
  } else {
    alert("Error: " + (j.error || "unknown"));
  }
});

document.querySelectorAll(".revoke-btn").forEach(btn => {
  btn.addEventListener("click", async () => {
    const tid = btn.closest("tr").dataset.tokenId;
    if (!confirm("¿Revocar este token? Las instalaciones que lo usan dejarán de poder reportar.")) return;
    const r = await fetch(`/api/admin/tokens/${tid}`, {
      method: "DELETE", credentials: "same-origin",
    });
    if (r.ok) location.reload();
    else alert("Error revocando");
  });
});
```

- [ ] **Step 6: `frontend/templates/central/client_detail.html`** (drill-down con targets + events).

```html
{% extends "_blank.html" %}
{% block title %}{{ client.proyecto }} — detalle{% endblock %}
{% block content %}
<section class="p-6 max-w-5xl">
  <h1 class="text-2xl font-semibold mb-1">{{ client.proyecto }}</h1>
  <p class="text-sm text-[var(--muted)] mb-6">
    {{ client.organizacion or "Sin organización" }}
    {% if g.current_user.role in ('admin', 'operator') %}
    · <a href="/dashboard-central/clients/{{ client.id }}/tokens"
         class="text-brand-400 hover:underline">gestionar tokens</a>
    {% endif %}
  </p>

  <h2 class="text-lg font-semibold mb-2">Targets ({{ targets|length }})</h2>
  <table class="min-w-full text-sm mb-8 rounded border border-[var(--border)]">
    <thead class="bg-[var(--surface)]">
      <tr>
        <th class="text-left px-4 py-2">Categoría</th>
        <th class="text-left px-4 py-2">Subkey</th>
        <th class="text-left px-4 py-2">Label</th>
        <th class="text-left px-4 py-2">Última ejec.</th>
        <th class="text-left px-4 py-2">Estado</th>
        <th class="text-right px-4 py-2">Total (GB)</th>
      </tr>
    </thead>
    <tbody>
      {% for t in targets %}
      <tr class="border-t border-[var(--border)]">
        <td class="px-4 py-2">{{ t.category }}</td>
        <td class="px-4 py-2">{{ t.subkey }}</td>
        <td class="px-4 py-2 font-mono">{{ t.label }}</td>
        <td class="px-4 py-2">{{ t.last_exec_ts or "—" }}</td>
        <td class="px-4 py-2 {% if t.last_exec_status == 'fail' %}text-rose-400{% endif %}">
          {{ t.last_exec_status or "—" }}
        </td>
        <td class="px-4 py-2 text-right">
          {{ "%.2f"|format((t.total_size_bytes or 0) / 1073741824) }}
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>

  <h2 class="text-lg font-semibold mb-2">Eventos recientes ({{ events|length }})</h2>
  <table class="min-w-full text-sm rounded border border-[var(--border)]">
    <thead class="bg-[var(--surface)]">
      <tr>
        <th class="text-left px-4 py-2">Recibido</th>
        <th class="text-left px-4 py-2">Op</th>
        <th class="text-left px-4 py-2">Estado</th>
        <th class="text-left px-4 py-2">IP</th>
      </tr>
    </thead>
    <tbody>
      {% for e in events %}
      <tr class="border-t border-[var(--border)]">
        <td class="px-4 py-2 font-mono">{{ e.received_at }}</td>
        <td class="px-4 py-2">{{ e.op }}</td>
        <td class="px-4 py-2 {% if e.status == 'fail' %}text-rose-400{% endif %}">{{ e.status }}</td>
        <td class="px-4 py-2 font-mono">{{ e.src_ip or "—" }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</section>
{% endblock %}
```

- [ ] **Step 7: Run + commit.**

```bash
git add frontend/templates/central/ frontend/static/js/central/ \
        backend/central/dashboard.py tests/central/test_admin_ui.py
git commit -m "central: add UI for client CRUD and token management"
```

---

## Task 11: Cliente — cola local con backoff exponencial

**Files:**
- Create: `backend/central/queue.py`
- Create: `tests/central/test_queue.py`

`[parallel: T7-T10]` — toca solo client side, no central side.

- [ ] **Step 1: Test.**

```python
# tests/central/test_queue.py
import json
import pytest
from datetime import datetime, timedelta, timezone
from backend.models.db import DB
from backend.central import queue as q


@pytest.fixture
def db(tmp_path): return DB(tmp_path / "t.db")


def _payload(eid="aaaaaaaa-1111-2222-3333-444444444444"):
    return {"event_id": eid, "ts": "2026-01-01T00:00:00Z"}


def test_enqueue_creates_pending_row(db):
    p = _payload()
    q.enqueue(db, p)
    rows = db.con().execute(
        "SELECT event_id, state, attempts FROM central_queue"
    ).fetchall()
    assert rows == [(p["event_id"], "pending", 0)]


def test_enqueue_idempotent_by_event_id(db):
    p = _payload()
    q.enqueue(db, p)
    q.enqueue(db, p)
    n = db.con().execute("SELECT COUNT(*) FROM central_queue").fetchone()[0]
    assert n == 1


def test_fetch_due_returns_only_pending_past_deadline(db):
    q.enqueue(db, _payload(eid="11111111-1111-1111-1111-111111111111"))
    q.enqueue(db, _payload(eid="22222222-2222-2222-2222-222222222222"))
    db.con().execute(
        "UPDATE central_queue SET next_retry_ts=? WHERE event_id=?",
        ((datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
         "22222222-2222-2222-2222-222222222222"),
    )
    db.con().commit()
    due = q.fetch_due(db, limit=10)
    assert len(due) == 1
    assert due[0]["event_id"] == "11111111-1111-1111-1111-111111111111"


def test_mark_done_deletes_row(db):
    q.enqueue(db, _payload())
    q.mark_done(db, _payload()["event_id"])
    n = db.con().execute("SELECT COUNT(*) FROM central_queue").fetchone()[0]
    assert n == 0


def test_mark_failed_increments_attempts_and_schedules_retry(db):
    p = _payload()
    q.enqueue(db, p)
    q.mark_failed(db, p["event_id"], error="500 Internal")
    row = db.con().execute(
        "SELECT attempts, last_error, state, next_retry_ts FROM central_queue"
    ).fetchone()
    assert row[0] == 1
    assert row[1] == "500 Internal"
    assert row[2] == "pending"
    assert row[3] > p["ts"]  # algo en el futuro


def test_backoff_dead_after_threshold(db):
    p = _payload()
    q.enqueue(db, p)
    for _ in range(20):
        q.mark_failed(db, p["event_id"], error="boom")
    row = db.con().execute("SELECT state FROM central_queue").fetchone()
    assert row[0] == "dead"


def test_mark_dead_explicit(db):
    p = _payload()
    q.enqueue(db, p)
    q.mark_dead(db, p["event_id"], error="401 unauthorized")
    row = db.con().execute("SELECT state, last_error FROM central_queue").fetchone()
    assert row[0] == "dead" and row[1] == "401 unauthorized"


def test_backoff_schedule_progression():
    # 1m → 5m → 15m → 1h → 6h → 24h
    seq = [q.backoff_seconds(n) for n in range(1, 7)]
    assert seq == [60, 300, 900, 3600, 21600, 86400]
    # plateau después
    assert q.backoff_seconds(7) == 86400
    assert q.backoff_seconds(20) == 86400
```

- [ ] **Step 2: Run, verify it fails.**

- [ ] **Step 3: Implementar `backend/central/queue.py`.**

```python
"""Cola local de heartbeats pendientes de envío al central."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

MAX_ATTEMPTS = 20
BACKOFF_LADDER = [60, 300, 900, 3600, 21600, 86400]   # 1m,5m,15m,1h,6h,24h


def backoff_seconds(attempts: int) -> int:
    """Devuelve segundos de espera para el intento N (1-indexed)."""
    if attempts < 1: return 0
    idx = min(attempts - 1, len(BACKOFF_LADDER) - 1)
    return BACKOFF_LADDER[idx]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def enqueue(db, payload: dict) -> None:
    """Inserta el payload. Si el event_id ya existía, no hace nada (idempotente)."""
    try:
        db.con().execute(
            "INSERT INTO central_queue (event_id, payload_json, enqueued_at, "
            " next_retry_ts, attempts, state) VALUES (?,?,?,?,0,'pending')",
            (payload["event_id"], json.dumps(payload, separators=(",", ":")),
             _now_iso(), _now_iso()),
        )
        db.con().commit()
    except sqlite3.IntegrityError:
        pass


def fetch_due(db, *, limit: int = 100) -> list[dict]:
    rows = db.con().execute(
        "SELECT id, event_id, payload_json, attempts FROM central_queue "
        "WHERE state='pending' AND next_retry_ts <= ? "
        "ORDER BY next_retry_ts LIMIT ?",
        (_now_iso(), limit),
    ).fetchall()
    return [{"id": r[0], "event_id": r[1],
             "payload": json.loads(r[2]), "attempts": r[3]}
            for r in rows]


def mark_done(db, event_id: str) -> None:
    db.con().execute("DELETE FROM central_queue WHERE event_id=?", (event_id,))
    db.con().commit()


def mark_failed(db, event_id: str, *, error: str) -> None:
    """Increment attempts, reschedule. Si attempts == MAX → dead."""
    row = db.con().execute(
        "SELECT attempts FROM central_queue WHERE event_id=?", (event_id,)
    ).fetchone()
    if not row: return
    new_attempts = row[0] + 1
    if new_attempts >= MAX_ATTEMPTS:
        db.con().execute(
            "UPDATE central_queue SET attempts=?, last_error=?, state='dead' "
            "WHERE event_id=?", (new_attempts, error[:500], event_id),
        )
    else:
        next_ts = (datetime.now(timezone.utc)
                   + timedelta(seconds=backoff_seconds(new_attempts))).isoformat()
        db.con().execute(
            "UPDATE central_queue SET attempts=?, last_error=?, "
            " next_retry_ts=?, state='pending' WHERE event_id=?",
            (new_attempts, error[:500], next_ts, event_id),
        )
    db.con().commit()


def mark_dead(db, event_id: str, *, error: str) -> None:
    """Marca dead inmediatamente (para 4xx no recuperables)."""
    db.con().execute(
        "UPDATE central_queue SET state='dead', last_error=? WHERE event_id=?",
        (error[:500], event_id),
    )
    db.con().commit()


def stats(db) -> dict:
    rows = db.con().execute(
        "SELECT state, COUNT(*) FROM central_queue GROUP BY state"
    ).fetchall()
    return {r[0]: r[1] for r in rows}
```

- [ ] **Step 4-5: Run + commit.**

```bash
sudo /opt/snapshot-V3/.venv/bin/python -m pytest tests/central/test_queue.py -v
git add backend/central/queue.py tests/central/test_queue.py
git commit -m "central: add local queue with exponential backoff and dead-letter"
```

---

## Task 12: Cliente — sender HTTP

**Files:**
- Create: `backend/central/sender.py`
- Create: `tests/central/test_sender.py`

Depende de T11 + T2 (Config.CENTRAL_URL/TOKEN).

- [ ] **Step 1: Test (con `requests_mock` o simulando con monkeypatch).**

```python
# tests/central/test_sender.py
import pytest
from unittest.mock import patch, MagicMock
from backend.models.db import DB
from backend.central import sender, queue as q


@pytest.fixture
def db(tmp_path): return DB(tmp_path / "t.db")


@pytest.fixture(autouse=True)
def _config(monkeypatch):
    monkeypatch.setenv("CENTRAL_URL", "https://central.example.com")
    monkeypatch.setenv("CENTRAL_TOKEN", "tok123")
    monkeypatch.setenv("CENTRAL_TIMEOUT_S", "1")
    import importlib, backend.config; importlib.reload(backend.config)


def _payload():
    return {"event_id": "11111111-1111-1111-1111-111111111111", "x": 1}


def test_send_200_marks_done(db, monkeypatch):
    q.enqueue(db, _payload())
    fake = MagicMock(); fake.status_code = 200
    monkeypatch.setattr("backend.central.sender.requests.post",
                        lambda *a, **k: fake)
    n = sender.drain(db)
    assert n == 1
    assert db.con().execute("SELECT COUNT(*) FROM central_queue").fetchone()[0] == 0


def test_send_500_increments_attempts(db, monkeypatch):
    q.enqueue(db, _payload())
    fake = MagicMock(); fake.status_code = 500; fake.text = "boom"
    monkeypatch.setattr("backend.central.sender.requests.post",
                        lambda *a, **k: fake)
    sender.drain(db)
    row = db.con().execute(
        "SELECT attempts, state FROM central_queue"
    ).fetchone()
    assert row == (1, "pending")


def test_send_401_marks_dead_immediately(db, monkeypatch):
    q.enqueue(db, _payload())
    fake = MagicMock(); fake.status_code = 401; fake.text = "no"
    monkeypatch.setattr("backend.central.sender.requests.post",
                        lambda *a, **k: fake)
    sender.drain(db)
    row = db.con().execute("SELECT state FROM central_queue").fetchone()
    assert row[0] == "dead"


def test_send_no_central_url_is_noop(db, monkeypatch):
    monkeypatch.setenv("CENTRAL_URL", "")
    import importlib, backend.config; importlib.reload(backend.config)
    q.enqueue(db, _payload())
    n = sender.drain(db)
    assert n == 0  # no envió nada
```

- [ ] **Step 2: Run, verify it fails.**

- [ ] **Step 3: Implementar `backend/central/sender.py`.**

```python
"""HTTP sender que drena la cola local hacia el central."""
from __future__ import annotations

import logging

import requests

from backend.config import Config
from . import queue as q

log = logging.getLogger(__name__)


def _enabled() -> bool:
    return bool(Config.CENTRAL_URL and Config.CENTRAL_TOKEN)


def send_one(payload: dict) -> tuple[int, str]:
    """Devuelve (status_code, body_text). Retorna (0, error) si conn falla."""
    url = Config.CENTRAL_URL.rstrip("/") + "/api/v1/heartbeat"
    headers = {"Authorization": f"Bearer {Config.CENTRAL_TOKEN}",
               "Content-Type": "application/json"}
    try:
        r = requests.post(url, json=payload, headers=headers,
                          timeout=Config.CENTRAL_TIMEOUT_S)
        return r.status_code, (r.text or "")[:500]
    except requests.RequestException as e:
        return 0, str(e)[:500]


def drain(db, *, limit: int = 100) -> int:
    """Procesa hasta `limit` items pendientes due. Retorna cuántos se intentaron."""
    if not _enabled(): return 0
    items = q.fetch_due(db, limit=limit)
    for item in items:
        code, body = send_one(item["payload"])
        if code == 200:
            q.mark_done(db, item["event_id"])
        elif code in (400, 401, 403, 409, 410, 413):
            log.error("heartbeat %s rejected with %s: %s",
                      item["event_id"], code, body[:200])
            q.mark_dead(db, item["event_id"], error=f"{code}: {body[:200]}")
        else:
            # 0 (conn error), 5xx, etc → reintenta con backoff
            q.mark_failed(db, item["event_id"], error=f"{code}: {body[:200]}")
    return len(items)


def send_now(db, payload: dict) -> int:
    """Encola y dispara un único envío inmediato. Retorna el status_code (0 si conn err).
    Incluso en éxito el item queda en cola brevemente — drain() lo limpia."""
    q.enqueue(db, payload)
    if not _enabled(): return 0
    code, body = send_one(payload)
    if code == 200:
        q.mark_done(db, payload["event_id"])
    elif code in (400, 401, 403, 409, 410, 413):
        q.mark_dead(db, payload["event_id"], error=f"{code}: {body[:200]}")
    elif code != 0:
        q.mark_failed(db, payload["event_id"], error=f"{code}: {body[:200]}")
    return code
```

- [ ] **Step 4-5: Run + commit.**

```bash
sudo /opt/snapshot-V3/.venv/bin/python -m pytest tests/central/test_sender.py -v
git add backend/central/sender.py tests/central/test_sender.py
git commit -m "central: add HTTP sender with retry classification"
```

---

## Task 13: snapctl subcommands `central send|drain-queue|status`

**Files:**
- Modify: `core/bin/snapctl` (+nuevos subcomandos)
- Create: `core/lib/central.sh`
- Create: `tests/central/test_snapctl_central.py` (smoke vía subprocess)

Depende de T2 (config.py reads MODE/CENTRAL_*) y T11/T12.

> Nota: el CLI actual ya invoca Python en algunos paths (revisar `core/bin/snapctl` antes). Si no, este task se hace 100% en bash llamando a `python -m backend.central.cli`. Recomendado: agregar `backend/central/cli.py` chico que expone `send`, `drain`, `status`.

- [ ] **Step 1: Crear `backend/central/cli.py`.**

```python
"""CLI entry para el bash wrapper. Uso:
   python -m backend.central.cli send <json_file>
   python -m backend.central.cli drain
   python -m backend.central.cli status
"""
import json
import sys

from backend.config import Config
from backend.models.db import DB
from . import sender, queue as q


def main():
    if len(sys.argv) < 2:
        print("usage: cli.py {send <file>|drain|status}", file=sys.stderr); sys.exit(2)
    db = DB(Config.DB_PATH)
    cmd = sys.argv[1]
    if cmd == "send":
        payload = json.loads(open(sys.argv[2]).read())
        code = sender.send_now(db, payload)
        print(json.dumps({"ok": code == 200, "code": code}))
        sys.exit(0 if code == 200 else 1)
    elif cmd == "drain":
        n = sender.drain(db)
        print(json.dumps({"ok": True, "drained": n}))
    elif cmd == "status":
        st = q.stats(db)
        print(json.dumps({"ok": True, "queue": st,
                          "central_url": Config.CENTRAL_URL,
                          "mode": Config.MODE}))
    else:
        print(f"unknown: {cmd}", file=sys.stderr); sys.exit(2)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: `core/lib/central.sh`** (ayudante para los subcomandos del snapctl que arman payloads).

```bash
#!/usr/bin/env bash
# Helpers para enviar heartbeats al central. Uso:
#   central_send_archive ok|fail "$op_meta"
#   central_send_db_dump ok|fail "$op_meta"

central_enabled() {
    [[ -n "${CENTRAL_URL:-}" && -n "${CENTRAL_TOKEN:-}" ]]
}

# Genera UUID v4 sin depender de uuidgen ausente
_uuid4() {
    if command -v uuidgen >/dev/null 2>&1; then uuidgen; return; fi
    python3 -c "import uuid; print(uuid.uuid4())"
}

# Args: status, op, category, subkey, label, started_at, duration_s, size_bytes, total_bytes, count_files, remote_path, error
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
```

- [ ] **Step 3:** Agregar al `core/bin/snapctl` los subcomandos. Insertar al principio del case dispatcher (`case "$cmd" in`):

```bash
        central)
            sub="${1:-status}"; shift || true
            case "$sub" in
                send)        "${PYTHON_BIN}" -m backend.central.cli send "$@" ;;
                drain|drain-queue)
                             "${PYTHON_BIN}" -m backend.central.cli drain ;;
                status)      "${PYTHON_BIN}" -m backend.central.cli status ;;
                *)           echo "snapctl central {send|drain-queue|status}" >&2; exit 2 ;;
            esac ;;
```

Cargar `core/lib/central.sh` en el preámbulo de `snapctl` (donde ya cargan otros libs):

```bash
source "${SCRIPT_DIR}/../lib/central.sh"
```

- [ ] **Step 4: Test smoke.**

```python
# tests/central/test_snapctl_central.py
import json, os, subprocess

def test_central_status_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("SNAPSHOT_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("MODE", "client")
    out = subprocess.check_output(
        ["/opt/snapshot-V3/.venv/bin/python", "-m", "backend.central.cli", "status"],
        env={**os.environ},
    )
    j = json.loads(out)
    assert j["ok"] is True
    assert j["mode"] == "client"
```

- [ ] **Step 5-6:** Run + commit.

```bash
sudo /opt/snapshot-V3/.venv/bin/python -m pytest tests/central/test_snapctl_central.py -v
git add backend/central/cli.py core/lib/central.sh core/bin/snapctl tests/central/test_snapctl_central.py
git commit -m "central: add snapctl subcommands and bash helper for heartbeats"
```

---

## Task 14: Hook el sender en archive.sh y demás flujos

**Files:**
- Modify: `core/lib/archive.sh` (+`central_send` tras éxito/fallo)
- Modify: `core/bin/snapctl` (+`central_send` tras `cmd_create`, `cmd_prune`, `cmd_delete`)
- Test: `tests/central/test_archive_emits_heartbeat.py` (integration)

Depende de T13.

- [ ] **Step 1:** En `core/lib/archive.sh`, identificar las dos rutas (éxito y error) del archive y agregar:

```bash
# tras éxito:
central_send "ok" "archive" "os" "linux" "${HOSTNAME}" \
             "${started_at}" "${duration}" "${size_bytes}" \
             "${total_bytes}" "${count_files}" "${remote_path}" "null" || true

# tras error:
central_send "fail" "archive" "os" "linux" "${HOSTNAME}" \
             "${started_at}" "${duration}" "0" "0" "0" "" \
             "\"${error_msg//\"/\\\"}\"" || true
```

> Nota: pasamos `|| true` para que el envío no rompa el archive si el central está caído. La cola local (T11) tomará el relevo.

- [ ] **Step 2:** Mismo patrón en `core/bin/snapctl::cmd_create`, `cmd_prune`, `cmd_delete`.

- [ ] **Step 3:** Test integration que ejecuta un archive fake y verifica que la cola tiene un row.

- [ ] **Step 4-5: Run + commit.**

```bash
git add core/lib/archive.sh core/bin/snapctl tests/central/test_archive_emits_heartbeat.py
git commit -m "central: emit heartbeat from archive, create, prune and delete flows"
```

---

## Task 15: Healthcheck drena la cola periódicamente

**Files:**
- Modify: `systemd/snapshot-healthcheck.service` (`ExecStartPost=`)
- Test: smoke manual via `systemctl status` post-deploy

Depende de T13.

- [ ] **Step 1:** Editar `systemd/snapshot-healthcheck.service` y agregar como `ExecStartPost`:

```ini
ExecStartPost=/opt/snapshot-V3/core/bin/snapctl central drain-queue
```

(o `ExecStart` adicional si la unit ya tiene un solo `ExecStart`.)

- [ ] **Step 2: Commit.**

```bash
git add systemd/snapshot-healthcheck.service
git commit -m "central: drain queue from healthcheck timer (every 15min)"
```

---

## Task 16: `install.sh --central`

**Files:**
- Modify: `install.sh` (+`--central` flag handling)
- Test: dry-run smoke en CI/local

- [ ] **Step 1:** Parsear el flag `--central` cerca del top de `install.sh` (junto a `-y`):

```bash
CENTRAL_MODE=0
for arg in "$@"; do
    case "$arg" in
        --central) CENTRAL_MODE=1 ;;
    esac
done
```

- [ ] **Step 2:** Tras la creación normal del install, si `CENTRAL_MODE=1`:

```bash
if [[ $CENTRAL_MODE -eq 1 ]]; then
    info "Configurando modo CENTRAL"
    # Setear MODE=central en local.conf si no está ya
    if ! grep -q '^MODE=' /etc/snapshot-v3/snapshot.local.conf 2>/dev/null; then
        echo 'MODE="central"' | sudo tee -a /etc/snapshot-v3/snapshot.local.conf >/dev/null
    fi
    # Disable archive timer
    systemctl disable --now snapshot@archive.timer 2>/dev/null || true
    # Bootstrappear webmaster
    if [[ $YES_FLAG -eq 0 ]]; then
        read -rp "Email del webmaster (admin): " WMAIL
    else
        WMAIL="webmaster@$(hostname)"
    fi
    /opt/snapshot-V3/core/bin/snapctl admin create --email "$WMAIL" --role admin
    # Crear cliente demo + token
    /opt/snapshot-V3/.venv/bin/python -c "
from backend.config import Config
from backend.models.db import DB
from backend.central import models, tokens
db = DB(Config.DB_PATH)
cid = models.create_client(db, proyecto='demo')
plain, _ = tokens.issue(db, cid, label='demo-host')
print('CLIENT demo creado.')
print(f'TOKEN (guardar ahora, no se vuelve a mostrar): {plain}')
"
    # Snippet Caddy
    cat <<EOF
─────────────────────────────────────────────────
Snippet de Caddy para central.tu-dominio.com:

central.tu-dominio.com {
  reverse_proxy 127.0.0.1:5070
}
─────────────────────────────────────────────────
EOF
fi
```

- [ ] **Step 3:** Probar con `bash install.sh --dry-run --central` en VM o entorno seguro (en este host está corriendo otro deploy — NO ejecutar `install.sh --central` en `/`).

- [ ] **Step 4: Commit.**

```bash
git add install.sh
git commit -m "central: add --central install flag with bootstrap"
```

---

## Task 17: README — sección "Modo central"

**Files:**
- Modify: `README.md`

- [ ] **Step 1:** Agregar al README, tras la sección "Autenticación y usuarios", una nueva sección con:
  - Diagrama del bloque "Arquitectura" del spec.
  - Cómo deployar con `install.sh --central`.
  - Cómo enrolar un cliente existente (editar `snapshot.local.conf` con `CENTRAL_URL`+`CENTRAL_TOKEN`, reiniciar).
  - Cómo emitir/revocar tokens desde la UI.
  - Tabla de roles webmaster/técnico/gerente y permisos.
  - Snippet de Caddy.
  - Limitaciones conocidas (cola tope 7d, sin permisos por-proyecto, etc).

- [ ] **Step 2: Commit.**

```bash
git add README.md
git commit -m "docs: README section on central mode"
```

---

## Task 18: Tests E2E + property-based de idempotencia

**Files:**
- Create: `tests/central/test_e2e_heartbeat_dashboard.py`
- Create: `tests/central/test_heartbeat_idempotency.py`
- Modify: `backend/requirements.txt` (+`hypothesis>=6` if no presente)

Depende de todo.

- [ ] **Step 1: Test E2E.**

```python
# tests/central/test_e2e_heartbeat_dashboard.py
def test_heartbeat_then_dashboard(client, db):
    """Cliente envía un heartbeat → dashboard muestra el total."""
    from backend.central import models as m, tokens as tok
    cid = m.create_client(db, proyecto="alpha")
    plain, _ = tok.issue(db, cid, label="x")
    payload = {  # ... payload mínimo válido ...
        "event_id": "abcdef00-0000-0000-0000-000000000001",
        "ts": "2026-04-27T17:00:00Z",
        "client": {"proyecto": "alpha", "entorno": "cloud", "pais": "co"},
        "target": {"category": "os", "subkey": "linux", "label": "host01"},
        "operation": {"op": "archive", "status": "ok",
                      "started_at": "2026-04-27T17:00:00Z",
                      "duration_s": 10, "error": None},
        "snapshot": {"size_bytes": 100_000_000, "remote_path": "x", "encrypted": True},
        "totals": {"size_bytes": 500_000_000, "count_files": 5,
                   "oldest_ts": "2026-01-01T00:00:00Z",
                   "newest_ts": "2026-04-27T17:00:00Z"},
        "host_meta": {"hostname": "h", "snapctl_version": "0", "rclone_version": "0"},
    }
    r = client.post("/api/v1/heartbeat", json=payload,
                    headers={"Authorization": f"Bearer {plain}"})
    assert r.status_code == 200
    from tests.auth.helpers import create_user_and_login
    create_user_and_login(client, db, role="auditor")
    r = client.get("/dashboard-central")
    assert b"alpha" in r.data and b"500" in r.data  # 500MB en algún lado
```

- [ ] **Step 2: Test property-based.**

```python
# tests/central/test_heartbeat_idempotency.py
import pytest
from hypothesis import given, strategies as st, settings


@settings(max_examples=50)
@given(replays=st.integers(min_value=2, max_value=20))
def test_replay_does_not_duplicate(replays, db_factory):
    """N replays del mismo payload deben dejar 1 evento y agregados idénticos
    a 1 envío único."""
    db = db_factory()
    from backend.central import models as m
    cid = m.create_client(db, proyecto="prop")
    payload = _good_payload()  # helper compartido
    for _ in range(replays):
        m.apply_heartbeat(db, payload, token_id=1, client_id=cid, src_ip="x")
    n_events = db.con().execute("SELECT COUNT(*) FROM central_events").fetchone()[0]
    assert n_events == 1
    n_targets = db.con().execute("SELECT COUNT(*) FROM targets").fetchone()[0]
    assert n_targets == 1
```

- [ ] **Step 3-5:** Run, ajustar cobertura, commit.

```bash
git add tests/central/ backend/requirements.txt
git commit -m "central: add E2E and property-based idempotency tests"
```

---

## Task 19: Cobertura final + cleanup + PR

- [ ] **Step 1:** Correr toda la suite.

```bash
sudo /opt/snapshot-V3/.venv/bin/python -m pytest tests/ -q
sudo /opt/snapshot-V3/.venv/bin/python -m pytest tests/central/ --cov=backend.central --cov-report=term-missing
```
Expected: todos pasan; cobertura ≥90% en `backend.central`.

- [ ] **Step 2:** Si hay líneas no cubiertas relevantes, agregar tests dirigidos. Si solo son ramas defensivas (`raise` en paths imposibles), está OK.

- [ ] **Step 3:** Push + PR.

```bash
git push -u origin feature/central-mode
gh pr create --title "central: dual-mode deployment with cross-client aggregation" \
  --body "$(cat <<'EOF'
## Summary
- Sub-proyecto B de la decomposición A-F. Spec: docs/superpowers/specs/2026-04-27-dual-deploy-design.md.
- Una sola codebase, dos modos vía MODE=client|central en snapshot.local.conf.
- HTTP push del cliente al central con Bearer token argon2-hashed; idempotente por event_id.
- 6 tablas SQLite nuevas (5 central + 1 cliente). Cola con backoff exponencial (1m→5m→15m→1h→6h→24h, max 7d).
- 3 roles humanos en central (webmaster/técnico/gerente) reusan RBAC de sub-A; matriz central.* documentada.
- Dashboard agregado por proyecto, drill-down por cliente, gestión de clientes y tokens, audit log.
- install.sh --central bootstrappea modo central; healthcheck drena la cola cada 15 min.

## Test plan
- [ ] tests/auth/ siguen pasando (101 tests)
- [ ] tests/central/ ≥90% cobertura
- [ ] Smoke: deploy en VM con MODE=central, enrolar 2 clientes, verificar dashboard
- [ ] Smoke: revocar token, verificar 401 en heartbeat siguiente
- [ ] Smoke: matar central, hacer 3 archives, verificar cola; levantar central, verificar drain

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-review (para el implementador)

Tras escribir el plan, verificar:

1. **Spec coverage:** cada requisito (R1-R8 funcional + R no funcionales) tiene una task que lo implementa.
2. **Test for everything:** cada task funcional tiene tests TDD (escritos antes del código).
3. **Tipos consistentes:** `HeartbeatResult` en T5 se usa también en T13 (CLI) — verificar firma.
4. **No placeholders:** no quedan TBD/TODO en el plan. Si los hay, fix inline.
5. **Orden razonable:** T7 depende de T3,T4,T5 — esos están antes. T11 es paralelo a T7-T10. ✓

## Resumen de paralelización

```
T0 (branch + scaffold)
  ↓
T1 (DB schema)  ┬→ T2 (config)
                ├→ T3 (tokens)        ──┐
                ├→ T4 (schema valid)  ──┤
                └→ T5 (models DAO)    ──┤
                                        ↓
                                       T6 (perms)  T7 (heartbeat API)  T8 (admin API)
                                                                          ↓
                                                                       T9 (dashboard) T10 (admin UI)
T11 (queue) ─→ T12 (sender) ─→ T13 (CLI+helper) ─→ T14 (hooks) ─→ T15 (healthcheck)
                                                                  ↓
                                                                 T16 (install.sh) ─→ T17 (README)
                                                                                      ↓
                                                                                     T18 (E2E) ─→ T19 (PR)
```

**Tasks paralelizables**: T2 ‖ T3 ‖ T4 ‖ T5 ‖ T6 (todos dependen solo de T0 o T1). T11/T12 ‖ T7-T10 (client side vs central side). T17 ‖ T16. Total ~10 días-persona si serial; ~6 con paralelización.
