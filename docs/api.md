# API HTTP — snapshot-V3

Todas las rutas son relativas al backend Flask (default `http://127.0.0.1:5070`).
Excepto `/api/heartbeat` (M2M Bearer), todas requieren sesión web válida.

## Convenciones

- Sesión: cookie `snapshot_session` (HttpOnly, Lax, Secure en HTTPS).
- CSRF: header `X-CSRF-Token` en `POST/PUT/PATCH/DELETE` (token expuesto en `<meta name="csrf-token">`).
- Respuestas JSON: `{"ok": bool, "data": <obj|null>, "error": <str|null>}` para `/api/*`,
  o `{"ok": bool, ...}` con campos directos en `/auth/*`.
- Errores estándar:
  - 401 `unauthenticated` — sin sesión válida
  - 403 `forbidden` o `csrf` — sesión OK pero faltó permiso o CSRF
  - 400 `<msg>` — payload inválido o regla de validación
  - 404 — recurso no existe (o blueprint no cargado en este modo)

## Auth — `/auth/*`

Blueprint que **siempre** se registra (cliente y central).

| Método | Path | Quién | Para qué |
|---|---|---|---|
| `GET`  | `/auth/login` | público | Renderiza `login.html` |
| `POST` | `/auth/login` | público | Body `{email, password, mfa_code?}`. Devuelve `{ok, require_mfa?, require_mfa_enroll?}` |
| `POST` | `/auth/logout` | logueado | Revoca la sesión actual |
| `GET`  | `/auth/csrf` | logueado | Devuelve `{csrf_token}` para clientes JSON |
| `GET`  | `/auth/me` | logueado | Devuelve `{user}` con email, role, mfa_enrolled |
| `POST` | `/auth/password` | logueado | Body `{current, new}`. Cambia password, revoca otras sesiones |
| `POST` | `/auth/mfa/enroll/start` | logueado sin MFA | Devuelve TOTP secret + URI para QR |
| `POST` | `/auth/mfa/enroll/confirm` | logueado sin MFA | Body `{code}`. Persiste el secret y devuelve backup codes |
| `POST` | `/auth/reset-request` | público | Body `{email}`. Genera token de reset y envía email (si SMTP configurado) |
| `POST` | `/auth/reset-consume` | público | Body `{token, new_password}`. Aplica reset |
| `GET`  | `/auth/users` | admin | Lista usuarios |
| `POST` | `/auth/users` | admin | Crea usuario. Body `{email, display_name, role, password?}` |
| `POST` | `/auth/users/<uid>/set-role` | admin | Body `{role}` |
| `POST` | `/auth/users/<uid>/disable` | admin | |
| `POST` | `/auth/users/<uid>/enable` | admin | |
| `POST` | `/auth/users/<uid>/reset-password` | admin | Devuelve `{temp_password}` |
| `POST` | `/auth/users/<uid>/revoke-sessions` | admin | Cierra todas las sesiones del user |
| `POST` | `/auth/users/<uid>/reset-mfa` | admin | Borra el secret MFA y los backup codes |

### Login flow

```mermaid
sequenceDiagram
  participant U as Browser
  participant F as Flask /auth/login
  participant DB as SQLite
  U->>F: POST {email, password}
  F->>DB: get user
  F->>F: argon2 verify
  alt user has MFA
    F-->>U: 200 {ok, require_mfa: true}
    U->>F: POST {email, password, mfa_code}
    F->>F: TOTP verify
  else admin without MFA
    F-->>U: 200 {ok, require_mfa_enroll: true}
    U->>F: GET /auth/mfa-enroll → enroll flow
  else
    F->>DB: create session row
    F-->>U: 200 + Set-Cookie
  end
```

## Snapshot panel — `/api/*`

Blueprint principal (siempre). Todos requieren login excepto `/api/health`.

### Sistema

| Método | Path | Roles | Notas |
|---|---|---|---|
| `GET` | `/api/health` | público | `{status: "up"}` |
| `GET` | `/api/logs?lines=N` | admin/operator | Tail de `/var/log/snapshot-v3/snapctl.log` (máx 5000) |
| `GET` | `/api/jobs?limit=N` | logueado | Lista de ejecuciones recientes del CLI |
| `GET` | `/api/jobs/<jid>` | logueado | Detalle del job (stdout, stderr, rc) |

### Configuración

| Método | Path | Roles | Para qué |
|---|---|---|---|
| `GET`  | `/api/config` | admin/operator | `{backup_paths: [...], excludes: [...]}` |
| `POST` | `/api/config` | admin | Setea paths a backup |
| `GET`  | `/api/archive/config` | logueado | Taxonomía + flag de password |
| `POST` | `/api/archive/config` | admin | Setea proyecto/entorno/pais/nombre/keep_months |
| `POST` | `/api/archive/password` | admin | Setea ARCHIVE_PASSWORD |
| `DELETE` | `/api/archive/password` | admin | Quita encriptación openssl |
| `GET`  | `/api/db-archive/config` | admin | Targets DB + creds (passwords no se devuelven) |
| `POST` | `/api/db-archive/config` | admin | Body con engine targets, hosts, users, passwords |
| `GET`  | `/api/crypto/config` | admin | `{recipients, recipients_count, active_mode}` |
| `POST` | `/api/crypto/config` | admin | Body `{recipients}` (space-separated `age1...`) |
| `POST` | `/api/crypto/keygen` | admin | Genera keypair age. Retorna `{public, private}` UNA VEZ |

### Drive (rclone)

| Método | Path | Roles | Para qué |
|---|---|---|---|
| `GET`  | `/api/drive/status` | logueado | Estado de la vinculación + remote actual |
| `POST` | `/api/drive/link` | admin | Body `{token: "..."}` (rclone JSON) |
| `POST` | `/api/drive/unlink` | admin | Borra rclone.conf |
| `GET`  | `/api/drive/shared` | admin | Lista shared drives accesibles |
| `POST` | `/api/drive/target` | admin | Body `{kind: "shared"\|"personal", shared_id?}` |
| `POST` | `/api/drive/oauth/device/start` | admin | Inicia Device Flow (devuelve user_code + URL) |
| `POST` | `/api/drive/oauth/device/poll` | admin | Polling del Device Flow |

### Archive operations

| Método | Path | Roles | Para qué |
|---|---|---|---|
| `GET`  | `/api/archive/list?force=0\|1` | admin/operator | Lista archives en Drive |
| `GET`  | `/api/archive/summary?force=0\|1` | logueado | KPIs: count, last, size total |
| `POST` | `/api/archive/create` | admin/operator | Dispara archive ahora (sync, hasta `SNAPCTL_TIMEOUT`) |
| `POST` | `/api/archive/restore` | admin | Body `{path, target_dir}` |
| `POST` | `/api/archive/delete` | admin | Body `{path}` |

## Audit — `/audit/*`

Solo si `SNAPSHOT_AUDIT_VIEWER=1` en local.conf. Requiere login con rol `admin` o `auditor`.

| Método | Path | Para qué |
|---|---|---|
| `GET`  | `/audit/` | Vista agregada de clientes (HTML) |
| `GET`  | `/audit/api/status?force=1` | JSON con KPIs + clientes |
| `POST` | `/audit/api/refresh` | Invalida la cache (read-only) |

## Central — solo si `MODE=central`

### M2M (Bearer token)

| Método | Path | Auth | Para qué |
|---|---|---|---|
| `GET`  | `/api/ping` | Bearer | Health del receptor |
| `POST` | `/api/heartbeat` | Bearer | Recibe heartbeat del cliente |

#### Heartbeat schema (`POST /api/heartbeat`)

```json
{
  "event_id": "550e8400-e29b-41d4-a716-446655440000",
  "ts": "2026-04-28T12:34:56Z",
  "client": {
    "proyecto": "superaccess-uno",
    "entorno": "cloud",
    "pais": "colombia"
  },
  "target": {
    "category": "os",       // os | db
    "subkey": "linux",      // linux | postgres | mysql | mongo
    "label": "host01"       // hostname o dbname
  },
  "operation": {
    "op": "archive",        // archive | create | reconcile | prune | delete | db_dump
    "status": "ok",         // ok | fail | running
    "started_at": "2026-04-28T12:30:00Z",
    "duration_s": 296,
    "error": null
  },
  "snapshot": {
    "size_bytes": 1572864000,
    "remote_path": "superaccess-uno/cloud/colombia/os/linux/host01/2026/04/28/servidor_host01_20260428_123000.tar.zst.age"
  },
  "totals": {
    "size_bytes": 18800000000,
    "count_files": 24
  }
}
```

- `event_id` debe ser UUID v4. Idempotencia: el central rechaza con 200 (no error) si ya recibió ese event_id.
- Tope hard de payload: 64 KiB (`CENTRAL_MAX_PAYLOAD_BYTES`).
- Headers obligatorios: `Authorization: Bearer <token>`, `Content-Type: application/json`.

### Admin de clientes — `/api/admin/*`

Todos requieren login con permiso correspondiente (ver matriz en `base_datos_y_roles.md`).

| Método | Path | Permiso |
|---|---|---|
| `GET` | `/api/admin/clients` | `central.clients:read` |
| `POST` | `/api/admin/clients` | `central.clients:write` |
| `GET` | `/api/admin/clients/<cid>` | `central.clients:read` |
| `PATCH` | `/api/admin/clients/<cid>` | `central.clients:write` |
| `DELETE` | `/api/admin/clients/<cid>` | `central.clients:write` |
| `POST` | `/api/admin/clients/<cid>/tokens` | `central.tokens:issue` |
| `DELETE` | `/api/admin/tokens/<tid>` | `central.tokens:revoke` |
| `GET` | `/api/admin/tokens` | `central.tokens:revoke` |
| `GET` | `/api/admin/events` | `central.dashboard:view` |

### Alertas — `/api/admin/alerts/*`

| Método | Path | Permiso |
|---|---|---|
| `GET` | `/api/admin/alerts?active=1` | `central.dashboard:view` |
| `GET` | `/api/admin/alerts/config` | `central.dashboard:view` |
| `POST` | `/api/admin/alerts/config` | `central.alerts:configure` |
| `GET` | `/api/admin/alerts/<id>` | `central.dashboard:view` |
| `POST` | `/api/admin/alerts/<id>/acknowledge` | `central.alerts:configure` |

### Vistas HTML

| Path | Permiso |
|---|---|
| `/dashboard-central` | `central.dashboard:view` |
| `/dashboard-central/clients` | `central.clients:read` |
| `/dashboard-central/clients/<cid>` | `central.clients:read` |
| `/dashboard-central/clients/<cid>/tokens` | `central.tokens:revoke` |
| `/dashboard-central/alerts` | `central.dashboard:view` |

## Web (HTML) — `/`

| Path | Auth | Para qué |
|---|---|---|
| `/` | logueado | Dashboard cliente (KPIs + último archive) |
| `/snapshots` | logueado | Listado de archives en Drive |
| `/logs` | admin/operator | Tail JSON-lines del log |
| `/settings` | admin | Drive + taxonomía + DB + crypto + alertas (central) |
| `/users` | admin | Gestión de cuentas |
| `/auth/login` | público | |
| `/auth/mfa-enroll` | público (post-login parcial) | |
| `/auth/reset-request` | público | |
| `/auth/reset?token=…` | público | |
| `/auth/change-password` | logueado | |

## Códigos de estado del CLI (`snapctl ... ; echo $?`)

| Code | Significado |
|---|---|
| 0 | OK |
| 1 | Error genérico |
| 2 | Error de validación / argumento inválido |
| 130 | Interrumpido (SIGINT) |
| Otro | Propaga rc del subproceso (rclone, restic, age, ...) |
