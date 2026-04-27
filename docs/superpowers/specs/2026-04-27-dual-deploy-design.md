# Despliegue dual + agregación central para snapshot-V3

**Fecha:** 2026-04-27
**Estado:** propuesto
**Sub-proyecto:** B (de A-F en la decomposición acordada)
**Depende de:** sub-proyecto A (auth + RBAC) — ya merged en `main`.

## Contexto

snapshot-V3 corre hoy como una instancia única por host: un servidor
respalda lo suyo a Google Drive y la UI local muestra solo lo de ese
host. El operador quiere ahora dos modos de despliegue del mismo
producto:

- **Modo cliente** (lo que existe): instancia local en cada servidor
  del cliente, ve solo sus propios backups.
- **Modo central**: instancia en un subdominio (`central.tu-dominio.com`)
  que **agrega** la información de todas las instalaciones cliente —
  qué proyectos hay, cuándo respaldó cada uno por última vez, cuánto
  pesa cada proyecto en GB, qué targets están en falla.

Hoy existe una vista `/audit` que medio cubre esto: cada cliente
publica `_status/<host>.json` al mismo shared Google Drive y el central
lo lee con `rclone cat` (`backend/services/audit.py`). Tiene tres
limitaciones que esta spec resuelve:

1. **Solo funciona si todos los clientes comparten el mismo Drive.**
   Imposibilita un cliente en un Drive distinto.
2. **Sin autenticación entre nodos.** Cualquiera con acceso al Drive
   puede inyectar `_status/<host>.json` falsos.
3. **Granularidad por hostname**, no por proyecto/cliente lógico. El
   operador necesita ver "superaccess-uno", "superaccess-dos", "orus"
   como entidades de primer nivel, no como nombres de host.

Adicionalmente, hoy solo el flujo restic legacy (`create`, `prune`,
`reconcile`) escribe `_status/<host>.json`. El **flujo archive — el
principal en producción — no publica nada**. Es un bug latente que esta
spec arregla por diseño.

Esta spec define la **capa de transporte y agregación**: cómo cada
instalación reporta al central, qué auth media entre ellos, qué
schema de datos persiste el central, y qué endpoints expone para que
los humanos vean el agregado. Las **alertas activas** (notificación
por borrado, target silencioso) son **sub-proyecto D**. La **creación
real de los `.7z` de Postgres / MySQL / Mongo** es **sub-proyecto E**.
Esta spec solo deja el central preparado para mostrarlos cuando lleguen.

## Requisitos

### Funcionales

1. Una sola codebase soporta ambos modos vía variable `MODE` en
   `/etc/snapshot-v3/snapshot.local.conf`.
2. Cada operación de backup en cliente (`archive`, `create`, `prune`,
   `db_dump`, `delete`) emite un **heartbeat HTTP POST** al central.
3. El central recibe heartbeats autenticados con un Bearer token
   por instalación, persiste el evento crudo (audit) y mantiene
   agregados materializados para el dashboard.
4. Identidad lógica de dos niveles: `clients` (proyecto) +
   `targets` (cada install snapctl). Cada target tiene `category`
   (`os` o `db`) para soportar tanto backups de servidor como de DB.
5. UI del central con tres vistas: dashboard agregado por proyecto,
   detalle de un cliente con sus targets, y administración (clientes,
   tokens, eventos).
6. Tres roles humanos en el central: webmaster (`admin`), técnico
   (`operator`), gerente (`auditor`). Reusa la implementación de
   roles de sub-A.
7. Cliente con cola local persistente: si el central no responde, el
   heartbeat queda encolado y se reintenta con backoff exponencial.
8. Idempotencia end-to-end: cada heartbeat lleva un `event_id` UUIDv4;
   el central rechaza duplicados con 200 OK.

### No funcionales

- **Cero impacto en clientes existentes que no opten al central**:
  sin `MODE` ni `CENTRAL_URL` configurados, la app se comporta
  idéntico a la versión actual.
- **Auth jamás texto plano**: tokens persisten argon2-hashed, igual
  que las passwords de sub-A.
- **Append-only audit**: los eventos crudos en `central_events`
  son la fuente de verdad; los agregados en `targets` son
  proyecciones reconstruibles.
- **Dashboard <50ms** con hasta 500 targets gracias a agregados
  materializados.
- **Sin dependencia de servicios externos nuevos**: misma stack
  (Flask + SQLite + Tailwind), mismos timers systemd, misma
  `install.sh`.

### Fuera de scope (sub-proyectos posteriores)

| Capacidad | Sub-proyecto |
|---|---|
| Alertas activas (deletion, missing folder, silent target) | D |
| Creación de `.7z` de Postgres / MySQL / Mongo | E |
| Cifrado hardening (GPG vs openssl actual) | F |
| Backfill desde Drive del histórico previo a B | futuro |
| Permisos por-proyecto (auditor solo de un proyecto) | futuro (sub-G) |
| Portal del cliente final logueando para verse a sí mismo | futuro (sub-G) |

## Arquitectura

Una sola codebase, dos modos de operación gobernados por `MODE` en
`snapshot.local.conf`:

```
MODE=client    # default — comportamiento actual + envío de heartbeats al central
MODE=central   # dashboard agregador, recibe heartbeats de N clientes
```

```
┌──────────────────────────┐                ┌──────────────────────────────┐
│  CLIENTE (MODE=client)   │  HTTPS POST    │  CENTRAL (MODE=central)      │
│  ─────────────────────   │  Bearer token  │  ──────────────────────────  │
│  snapctl archive ─┐      │  /heartbeat    │  /api/v1/heartbeat           │
│                   ├─────►│ ──────────►    │   ↓                          │
│  cola SQLite ◄────┘      │                │  central_events (raw audit)  │
│  /var/lib/.../snapshot.db│                │   ↓                          │
│                          │                │  upsert targets, clients     │
│  Flask :5070 (UI local)  │                │   ↓                          │
│                          │                │  Flask :5070 + Tailwind UI   │
└──────────────────────────┘                │  /dashboard-central          │
                                            │  /clients, /tokens, /alerts  │
                                            └──────────────────────────────┘
```

### Diferencias prácticas entre modos

| Capa | client | central |
|---|---|---|
| Blueprints Flask cargados | api, web, audit, auth | api, web, audit, auth + **central_api, central_admin, central_dashboard** |
| Timer `snapshot@archive` | activo | desactivado (el central no respalda nada propio) |
| Cola `central_queue` (cliente) | activa si `CENTRAL_URL` definido | n/a |
| Tablas extra en SQLite | `central_queue` | `clients`, `targets`, `central_tokens`, `central_events`, `central_user_perms` |
| `install.sh` | sin cambios | flag nuevo `--central` que setea `MODE=central` + crea las tablas + bootstrappea webmaster + emite primer token |

**Justificación de un solo binario**: ~80% del código (auth, jobs,
drive, settings, archivos, MFA) se reusa. Mantener dos repos
divergentes garantiza drift en pocos meses. La penalización en el
cliente de tener el módulo central cargado es nula porque las rutas
no se registran si `MODE=client`.

### Subdominio y TLS

El central va detrás de Caddy con TLS automático:

```
central.tu-dominio.com {
  reverse_proxy 127.0.0.1:5070
}
```

Mismo patrón que ya documenta el README para el cliente. El backend
sigue escuchando en `127.0.0.1:5070` sin TLS — Caddy termina TLS.

## Modelo de datos

5 tablas nuevas en la SQLite del central + 1 tabla en el cliente.
Todas creadas idempotentemente al boot por `backend/models/db.py`
(mismo patrón que sub-A — sin Alembic).

### En el central

```sql
-- 1) Cliente lógico (lo que el operador llama "superaccess-uno", "orus", "basculas")
CREATE TABLE clients (
    id              INTEGER PRIMARY KEY,
    proyecto        TEXT NOT NULL UNIQUE,         -- key de negocio
    organizacion    TEXT,                          -- opcional (p.ej. "Banco X")
    contacto        TEXT,                          -- email comercial, opcional
    retencion_meses INTEGER,                       -- objetivo de retención, opcional
    notas           TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

-- 2) Cada install snapctl, agrupada bajo un cliente. category = os | db.
CREATE TABLE targets (
    id                INTEGER PRIMARY KEY,
    client_id         INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    category          TEXT NOT NULL CHECK (category IN ('os','db')),
    subkey            TEXT NOT NULL,    -- 'linux' | 'postgresql' | 'mysql' | 'mongo'
    label             TEXT NOT NULL,    -- hostname (os) | dbname (db)
    entorno           TEXT,             -- cloud | local
    pais              TEXT,
    last_exec_ts      TEXT,             -- ISO8601 UTC, max(received_at OK)
    last_exec_status  TEXT,             -- ok | fail | running
    last_size_bytes   INTEGER,
    total_size_bytes  INTEGER,
    count_files       INTEGER,
    oldest_backup_ts  TEXT,
    newest_backup_ts  TEXT,
    last_heartbeat_ts TEXT NOT NULL,    -- usado por sub-D para detectar "silent"
    snapctl_version   TEXT,
    rclone_version    TEXT,
    created_at        TEXT NOT NULL,
    UNIQUE(client_id, category, subkey, label)
);
CREATE INDEX idx_targets_client ON targets(client_id);
CREATE INDEX idx_targets_silent ON targets(last_heartbeat_ts);

-- 3) Token API por install. Argon2-hashed, jamás texto plano.
CREATE TABLE central_tokens (
    id           INTEGER PRIMARY KEY,
    token_hash   TEXT NOT NULL UNIQUE,            -- argon2id
    client_id    INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    label        TEXT NOT NULL,                    -- humano: 'web01-prod'
    scope        TEXT NOT NULL DEFAULT 'heartbeat:write',
    created_at   TEXT NOT NULL,
    expires_at   TEXT,                              -- NULL = no expira
    last_used_at TEXT,
    revoked_at   TEXT
);
CREATE INDEX idx_tokens_client ON central_tokens(client_id) WHERE revoked_at IS NULL;

-- 4) Audit raw, append-only. Fuente de verdad. Los agregados de
--    `targets` son una proyección reconstruible desde aquí.
CREATE TABLE central_events (
    id           INTEGER PRIMARY KEY,
    event_id     TEXT NOT NULL UNIQUE,             -- UUIDv4 del cliente, idempotencia
    received_at  TEXT NOT NULL,                    -- now() del central, no del cliente
    token_id     INTEGER NOT NULL REFERENCES central_tokens(id),
    client_id    INTEGER NOT NULL REFERENCES clients(id),
    target_id    INTEGER REFERENCES targets(id),   -- NULL si es create-target
    op           TEXT NOT NULL,                     -- archive|create|prune|delete|db_dump
    status       TEXT NOT NULL,                     -- ok|fail|running
    payload_json TEXT NOT NULL,                     -- el JSON crudo del heartbeat
    src_ip       TEXT
);
CREATE INDEX idx_events_target_ts ON central_events(target_id, received_at DESC);
CREATE INDEX idx_events_client_ts ON central_events(client_id, received_at DESC);

-- 5) Permisos centrales por usuario humano. Extiende sub-A.
CREATE TABLE central_user_perms (
    user_id          INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    can_manage_users INTEGER NOT NULL DEFAULT 0,    -- redundante con role=admin pero explícito
    notes            TEXT
);
```

### En el cliente

```sql
-- Cola persistente de heartbeats pendientes. Drenada por el sender
-- síncrono y por el healthcheck cada 15 min.
CREATE TABLE central_queue (
    id            INTEGER PRIMARY KEY,
    event_id      TEXT NOT NULL UNIQUE,
    payload_json  TEXT NOT NULL,
    enqueued_at   TEXT NOT NULL,
    next_retry_ts TEXT NOT NULL,
    attempts      INTEGER NOT NULL DEFAULT 0,
    last_error    TEXT,
    state         TEXT NOT NULL DEFAULT 'pending'  -- pending | dead
);
CREATE INDEX idx_queue_due ON central_queue(state, next_retry_ts);
```

### Decisiones notables del modelo

- **`central_events.received_at`, no `payload.ts`**: el central usa
  su propio reloj para ordenar. Evita que un cliente con reloj
  desfasado se pinte permanentemente como "más reciente".
- **`event_id UNIQUE`**: el cliente reenvía libremente si pierde el
  ACK; el central rechaza duplicados con 200 OK silencioso.
- **Upsert en `targets`** por `(client_id, category, subkey, label)`:
  primer heartbeat con esa combinación crea el target; los siguientes
  actualizan los `last_*` y los `total_*`.
- **Path canónico** que el cliente envía y el central deriva el
  target: `PROYECTO/ENTORNO/PAIS/<category>/<subkey>/<label>/YYYY/MM/DD/<file>`.
  Cubre tanto `superaccess-uno/cloud/colombia/os/linux/web01/...tar.zst.enc`
  como `basculas/local/costa-rica/db/mysql/clientes_db/...7z`.
- **`ON DELETE CASCADE`** desde `clients` hacia `targets` y `central_tokens`,
  pero **no** hacia `central_events` (los eventos de un cliente borrado
  quedan huérfanos para preservar audit).

## Endpoints HTTP

Todos bajo `central_api_bp` (montado en `/api/v1`) y `central_admin_bp`
(montado en `/api/admin`), solo registrados si `MODE=central`.

### Machine-to-machine — auth = Bearer token de instalación

| Método | Ruta | Para qué |
|---|---|---|
| `POST` | `/api/v1/heartbeat` | Reportar evento. Body = JSON del heartbeat (ver "Payload"). |
| `GET` | `/api/v1/ping` | Healthcheck barato. Cliente lo usa antes de drenar la cola. |

Rate limit: `60/min` por token (`heartbeat`), `120/min` por IP (`ping`).
Aplicado vía Flask-Limiter (ya en uso por sub-A).

### Humanos — auth = sesión + RBAC de sub-A

| Método | Ruta | Permiso requerido | Para qué |
|---|---|---|---|
| `GET` | `/dashboard-central` | `central.dashboard:view` | HTML — tabla por proyecto |
| `GET` | `/api/admin/clients` | `central.clients:read` | Lista de clientes con agregados de targets |
| `GET` | `/api/admin/clients/<id>` | `central.clients:read` | Drill-down: cliente + targets + últimos 50 events |
| `POST` | `/api/admin/clients` | `central.clients:write` | Crear cliente |
| `PATCH` | `/api/admin/clients/<id>` | `central.clients:write` | Editar cliente |
| `DELETE` | `/api/admin/clients/<id>` | `central.clients:write` | Borrar cliente (cascade targets+tokens) |
| `POST` | `/api/admin/clients/<id>/tokens` | `central.tokens:issue` | Emitir token (devuelve plaintext una sola vez) |
| `DELETE` | `/api/admin/tokens/<id>` | `central.tokens:revoke` | Revocar token |
| `GET` | `/api/admin/events?target_id=&since=` | `central.audit:view` | Stream paginado del audit |

CSRF y headers de seguridad heredados del middleware de sub-A
(Talisman + middleware propio).

### Matriz de roles

| Permiso | admin (webmaster) | operator (técnico) | auditor (gerente) |
|---|:-:|:-:|:-:|
| `central.dashboard:view` | ✓ | ✓ | ✓ |
| `central.audit:view` | ✓ | ✓ | ✓ |
| `central.clients:read` | ✓ | ✓ | ✓ |
| `central.clients:write` | ✓ | ✓ | – |
| `central.tokens:issue` | ✓ | ✓ | – |
| `central.tokens:revoke` | ✓ | ✓ | – |
| `central.alerts:configure` | ✓ | ✓ | – |
| `central.users:manage` | ✓ | – | – |
| `central.settings:edit` | ✓ | – | – |

Scope: **global**. No hay "auditor solo de un proyecto" en sub-B.

## Payload del heartbeat

Tipo `application/json`, máx **64KB** (rechazado con 413 si excede).

```json
{
  "event_id": "9f2a1b6c-...-uuid",
  "ts": "2026-04-27T17:42:11Z",
  "client": {
    "proyecto": "superaccess-uno",
    "entorno": "cloud",
    "pais": "colombia"
  },
  "target": {
    "category": "os",
    "subkey": "linux",
    "label": "web01"
  },
  "operation": {
    "op": "archive",
    "status": "ok",
    "started_at": "2026-04-27T17:30:02Z",
    "duration_s": 729,
    "error": null
  },
  "snapshot": {
    "size_bytes": 4831838208,
    "remote_path": "superaccess-uno/cloud/colombia/os/linux/web01/2026/04/27/servidor_web01_20260427_173002.tar.zst.enc",
    "encrypted": true
  },
  "totals": {
    "size_bytes": 178291823104,
    "count_files": 14,
    "oldest_ts": "2025-05-01T02:14:33Z",
    "newest_ts": "2026-04-27T17:42:11Z"
  },
  "host_meta": {
    "hostname": "web01.local",
    "snapctl_version": "0.4.2",
    "rclone_version": "v1.68.2"
  }
}
```

### Cuándo se envía cada campo

- `host_meta`: solo en el primer heartbeat tras boot del cliente o
  cuando alguno de sus campos cambió respecto al último envío.
  Ahorra ancho de banda.
- `totals.size_bytes`: recalculado con `rclone size` solo en
  operaciones grandes (`archive`, `prune`); las demás reusan el
  último valor cacheado en `snapshot.local.conf`.
- `operation.error`: string corto (≤500 chars), solo si `status=fail`.
  **Sin logs ni stack traces** — los detalles quedan en el log local.

### Qué pide el operador y dónde sale

| Pedido en el prompt original | Cómo se cubre |
|---|---|
| "tabla de superaccess uno, dos por proyecto" | dashboard agrupa `targets` por `client.proyecto` |
| "hora y fecha de la última ejecución" | `targets.last_exec_ts` |
| "peso del último backup" | `targets.last_size_bytes` (≡ `snapshot.size_bytes` del último heartbeat OK) |
| "cuanto pesa la carpeta del cliente" | `SUM(targets.total_size_bytes) WHERE client_id=?` |
| "cuándo fue la última ejecución de superaccess uno, orus" | `MAX(targets.last_exec_ts) WHERE client_id=?` |
| "cuánto pesa por proyecto en gigas" | misma SUM, dividida por 1024³ en la UI |
| "notifique cuando borraron backups" | (sub-D) — heartbeat ya manda `op=delete` con `snapshot.remote_path` |
| "alertas cuando no encuentre carpetas" | (sub-D) — central detecta `targets` con `last_heartbeat_ts > N días` |

## Lifecycle del heartbeat

### Camino feliz en el cliente

```
[snapctl archive]
  └─ archive_ops.run_archive()           # core/lib/archive.sh
       └─ central_client.send(payload)   # nuevo módulo
            └─ central_queue.enqueue(payload, event_id=uuid4())
                 ├─ síncrono: HTTP POST con timeout 5s
                 │    ├─ 200 OK → DELETE de la cola
                 │    ├─ 4xx (auth/payload) → marca `dead` (no reintenta)
                 │    └─ 5xx, timeout, conn refused → attempts++, schedule retry
                 └─ Si el síncrono falla, el healthcheck lo drena después
```

### Procesamiento en el central

```
POST /api/v1/heartbeat
  ├─ 1. Validar Bearer token (lookup por argon2 hash, revoked_at IS NULL)
  │     └─ 401 si no matchea o está revocado/expirado
  ├─ 2. Validar JSON contra schema (campos requeridos, tipos, tamaños)
  │     └─ 400 si schema inválido
  ├─ 3. Validar payload.client.proyecto == clients[token.client_id].proyecto
  │     └─ 409 si mismatch
  ├─ 4. INSERT INTO central_events (event_id) — UNIQUE conflict = 200 OK
  ├─ 5. UPSERT INTO targets — actualiza last_*, total_*, last_heartbeat_ts
  ├─ 6. UPDATE central_tokens.last_used_at = now()
  └─ 7. Return 200 {"ok":true,"event_id":"..."}
```

Bloque 4-7 dentro de **una sola transacción SQLite** para que un crash
intermedio no deje los agregados desincronizados con el audit.

### Dashboard read path

```sql
SELECT c.proyecto, c.organizacion,
       COUNT(t.id) AS targets_count,
       COALESCE(SUM(t.total_size_bytes),0) AS total_bytes,
       MAX(t.last_exec_ts) AS last_exec_ts,
       SUM(CASE t.last_exec_status WHEN 'fail' THEN 1 ELSE 0 END) AS failed_targets,
       MIN(t.last_heartbeat_ts) AS oldest_heartbeat
FROM clients c LEFT JOIN targets t ON t.client_id = c.id
GROUP BY c.id ORDER BY c.proyecto;
```

Una sola query mantiene el dashboard <50ms hasta varios miles de
targets — los agregados ya están materializados en `targets`,
no se recalculan en read time.

## Error handling y edge cases

### Errores en el cliente al postear

| Caso | Acción |
|---|---|
| Conn refused / timeout / 5xx | `attempts++`, `next_retry_ts = now + backoff(attempts)`. Backoff: 1m → 5m → 15m → 1h → 6h → 24h. Tras 7 días o 20 intentos, `state=dead` y log WARN. |
| 401/403 (token revocado o expirado) | `state=dead` inmediato. Log ERROR con instrucciones para reissuar. |
| 400 (schema inválido) | `state=dead` inmediato. Bug del cliente. Log ERROR con `schema_error` del central. |
| 409 (proyecto mismatch) | `state=dead` inmediato. Indica desconfig. |
| 200 OK | `DELETE FROM central_queue WHERE event_id=?`. |

El healthcheck (`snapshot-healthcheck.timer`, ya existente) llama a
`snapctl central drain-queue` que procesa hasta 100 items pendientes
con `next_retry_ts <= now()`.

### Errores en el central

| Caso | Respuesta |
|---|---|
| DB lock | Reintento interno 3× con jitter, luego 503 |
| `event_id` duplicado | 200 OK silencioso (es lo correcto) |
| `clients` referenciado por token no existe (cliente borrado) | 410 Gone → cliente marca `dead` |
| Payload > 64KB | 413 Payload Too Large |

### Edge cases atajados por diseño

- **Reloj cliente desfasado**: ordenamos por `received_at`, no `payload.ts`.
- **Doble install en el mismo host**: colisión en `(client_id, category, subkey, label)`.
  Mitigación: `label` configurable en `snapshot.local.conf`.
- **Token regenerado para mismo install**: viejo se revoca, nuevo
  apunta al mismo `client_id`, los `targets` ya creados se reusan.
  Cero pérdida de historial.
- **Cliente offline 3 meses**: cola tiene a lo sumo 7 días, eventos
  viejos quedan `dead`. Backfill desde Drive es futuro, no en B.
- **Borrado de cliente desde central**: cascade borra targets/tokens;
  `central_events` quedan huérfanos para preservar audit; heartbeats
  subsecuentes del token revocado fallan con 401.

## Estrategia de testing

Reuso del patrón de sub-A (101 tests passing en `tests/auth/`).
Nuevo árbol `tests/central/` con cobertura ≥90% en `backend/central/`:

| Suite | Cubre |
|---|---|
| `test_token.py` | Issuance, hashing argon2, scope, revocación, expiración, rate limit por token |
| `test_heartbeat_schema.py` | Validación del payload, límites de tamaño, campos requeridos |
| `test_heartbeat_idempotency.py` | Replays no duplican eventos ni inflan agregados (incl. property-based con `hypothesis`) |
| `test_heartbeat_upsert.py` | Targets se crean/actualizan correctamente, agregados materializados |
| `test_proyecto_mismatch.py` | Token apuntando a otro `client_id` que el payload |
| `test_central_queue.py` | Cliente: enqueue, drain, backoff, dead-letter |
| `test_central_perms.py` | Matriz RBAC parametrizada `(role, endpoint) → (allowed, status)` |
| `test_dashboard_query.py` | Query agregada contra fixture de 50 clientes / 500 targets |
| `test_install_modes.py` | `MODE=client` no carga blueprints centrales; `MODE=central` los carga |
| `test_migration_existing_db.py` | DB pre-B existente arranca sin perder datos al añadir tablas nuevas |

Sin tests Selenium para la UI HTML del central — solo 2-3 smoke tests
con Flask test client.

## Rollout y migración

### Branch + merge

- Rama: `feature/central-mode`. Una sola PR.
- Tamaño esperado: ~2.5k líneas (backend ~1.5k, tests ~800, frontend ~200, README ~80).
- Tests verdes + spec + plan + review antes de merge a `main`.

### Schema migration

Sin Alembic (el proyecto no lo usa). Patrón existente:
`backend/models/db.py` ejecuta `CREATE TABLE IF NOT EXISTS` al boot.

Las 6 tablas nuevas se crean **incondicionalmente en ambos modos**:
una instancia client tendrá las 5 tablas centrales vacías (≈4KB de
overhead), y una central tendrá `central_queue` vacía. Es deliberado
para mantener un único path de inicialización y simplificar tests.
Idempotente y forward-only.

### Comportamiento de upgrade

| Estado pre-upgrade | Estado post-upgrade | Acción del cliente |
|---|---|---|
| `snapshot.local.conf` sin `MODE` ni `CENTRAL_URL` | `MODE=client` (default), `CENTRAL_URL` vacío | Cero cambio funcional. Cola y sender quedan dormidos. |
| `MODE=client`, `CENTRAL_URL=https://...`, `CENTRAL_TOKEN=...` | Idem | Sender empieza a postear desde el siguiente backup. Archives previos NO se backfilean. |
| Deploy nuevo de central (`install.sh --central`) | Tablas creadas, webmaster bootstrappeado, primer cliente+token emitido | Operador pega `CENTRAL_URL`+`CENTRAL_TOKEN` en cada cliente que quiera sumar |

### `install.sh --central`

Hace lo del install normal más:

1. Setea `MODE=central` en `/etc/snapshot-v3/snapshot.local.conf`.
2. Disable `snapshot@archive.timer` (el central no respalda nada propio).
3. Ejecuta `snapctl admin create --email <preguntado> --role admin` interactivo.
4. Crea cliente de ejemplo (`proyecto=demo`) y emite un token, los imprime una sola vez.
5. Imprime el snippet de Caddy para `central.tu-dominio.com`.

### Doc de README

Sección nueva: **"Modo central — agregación cross-cliente"** con:
- Diagrama del bloque "Arquitectura".
- Cómo deployar (`install.sh --central`).
- Cómo enrolar un cliente existente (editar `snapshot.local.conf`, reiniciar).
- Cómo emitir/revocar tokens desde la UI.
- Roles webmaster/técnico/gerente y qué ven.
- Caddy snippet para subdominio.

## Open questions

Ninguna — todas las decisiones de diseño quedaron cerradas durante el
brainstorming. Ver historial de la sesión 2026-04-27 si se necesita
trazabilidad.

## Apéndice: archivos esperados (referencia rápida para el plan)

```
backend/
  central/                       # nuevo paquete
    __init__.py                  # blueprint factory según MODE
    api.py                       # /api/v1/heartbeat, /api/v1/ping
    admin.py                     # /api/admin/clients, /api/admin/tokens, /api/admin/events
    dashboard.py                 # /dashboard-central (HTML)
    models.py                    # ORM-light: clients, targets, tokens, events
    tokens.py                    # issuance + verify (argon2)
    schema.py                    # validación del payload del heartbeat
    queue.py                     # cliente: enqueue, drain, backoff
    sender.py                    # cliente: HTTP POST + retry
  models/db.py                   # +CREATE TABLE IF NOT EXISTS para 6 tablas nuevas
  app.py                         # if MODE=='central': register central_*_bp
  config.py                      # MODE, CENTRAL_URL, CENTRAL_TOKEN, CENTRAL_TIMEOUT_S

core/
  bin/snapctl                    # nuevos subcmd: central drain-queue, central status
  lib/central.sh                 # helper bash que llama al sender (invocado por archive.sh, etc)

frontend/
  templates/
    central/
      dashboard.html             # tabla por proyecto
      client_detail.html         # drill-down
      tokens.html                # gestión de tokens
      clients.html               # CRUD de clientes
  static/js/central/
    dashboard.js
    tokens.js

systemd/
  snapshot-backend.service       # sin cambios
  snapshot-healthcheck.service   # +invocación a `snapctl central drain-queue`

install.sh                       # +flag --central
README.md                        # +sección "Modo central"

tests/central/
  ...                            # ver §"Estrategia de testing"
```
