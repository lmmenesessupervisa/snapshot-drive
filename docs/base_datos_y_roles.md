# Base de datos y roles — snapshot-V3

## Ubicación

Archivo único SQLite con WAL: `/var/lib/snapshot-v3/snapshot.db`
(mode 0600, owner root). El backend lo abre con
`PRAGMA journal_mode=WAL`. Migraciones versionadas con
`PRAGMA user_version` (`backend/auth/migrations.py`).

## Tablas

```mermaid
erDiagram
    users ||--o{ sessions : "tiene"
    users ||--o{ password_history : "tiene"
    users ||--o{ password_resets : "tiene"
    users ||--o{ mfa_backup_codes : "tiene"
    users ||--o{ audit_auth : "actúa"
    users ||--o| central_user_perms : "permisos extra"
    clients ||--o{ targets : "agrupa"
    clients ||--o{ central_tokens : "autorizan"
    clients ||--o{ central_events : "reciben"
    clients ||--o{ central_alerts : "disparan"
    targets ||--o{ central_events : "ref"
    central_tokens ||--o{ central_events : "firmaron"

    users {
        int id PK
        text email UK
        text display_name
        text password_hash "argon2id"
        text role "admin|operator|auditor"
        text mfa_secret "AES-GCM at rest"
        int mfa_disabled "v3: bypass MFA per-user"
        text status "active|disabled|locked"
        int failed_attempts
        text locked_until
    }
    sessions {
        text id PK "256-bit random"
        int user_id FK
        text expires_at
        text csrf_token
        int mfa_verified
    }
    clients {
        int id PK
        text proyecto UK
        text organizacion
        int retencion_meses
    }
    targets {
        int id PK
        int client_id FK
        text category "os|db"
        text subkey "linux|postgres|mysql|mongo"
        text label "hostname o dbname"
        text last_heartbeat_ts
        int total_size_bytes
    }
    central_tokens {
        int id PK
        text token_hash "argon2id, no plaintext"
        int client_id FK
        text scope "heartbeat:write"
        text expires_at
        text revoked_at
    }
    central_events {
        int id PK
        text event_id UK "uuid del cliente, idempotencia"
        int client_id FK
        int token_id FK
        text op "archive|create|reconcile|prune|delete|db_dump"
        text status "ok|fail|running"
        text payload_json
    }
    central_alerts {
        int id PK
        text type "no_heartbeat|folder_missing|backup_shrink"
        int client_id FK
        text severity "info|warning|critical"
        text triggered_at
        text resolved_at
    }
    central_queue {
        int id PK
        text event_id UK
        text payload_json
        int attempts
        text state "pending|sent|failed"
    }
    central_user_perms {
        int user_id PK_FK
        int can_manage_users
    }
```

### Tablas core (siempre existen)

| Tabla | Filas típicas | Para qué |
|---|---|---|
| `users` | 1-10 | Cuentas del panel. argon2id, MFA opcional/obligatorio. |
| `sessions` | 0-50 | Sesiones server-side activas. Expira por TTL absoluto + idle timeout. |
| `password_history` | N por user | Últimas hashes para evitar reuse en `change-password`. |
| `password_resets` | < 5 | Tokens de reset (hashed) con `expires_at`. |
| `mfa_backup_codes` | 10 por user con MFA | Códigos one-shot para emergencias. |
| `audit_auth` | append-only | Eventos: login_success/fail, role_change, mfa_reset, etc. |
| `jobs` | crece | Histórico de ejecuciones del CLI lanzadas desde la UI. |
| `audit` | crece | Audit log antiguo del CLI (pre-sub-A). Coexiste con `audit_auth`. |

### Tablas central (vacías en `MODE=client`)

| Tabla | Para qué |
|---|---|
| `clients` | Catálogo de clientes registrados (uno por host operado). |
| `targets` | Cada combinación (cliente, categoría, subkey, label). Un host Linux + 3 DBs = 4 rows. |
| `central_tokens` | Bearer tokens emitidos por el central a cada cliente. Hash argon2id, jamás plaintext. |
| `central_events` | Heartbeats recibidos. Idempotencia con `event_id` UK. |
| `central_alerts` | Alertas detectadas. State machine: `triggered → notified → resolved`. |
| `central_user_perms` | Granularidad extra para usuarios del central (gestión de subusuarios). |

### Tablas Drive inventory cache

Materialización en DB del listado del shared Drive. La UI `/audit/` lee de aquí (sub-segundo) en vez de hacer `rclone lsjson` en vivo. Solo el botón "Refrescar" o un heartbeat con campo `inventory` reescriben estas tablas.

| Tabla | Para qué |
|---|---|
| `drive_inventory` | Una fila por leaf `(proyecto, entorno, pais, label, category, subkey)` con count, total_size, encrypted_count, newest_ts, prev_size, shrunk, source (`drive_scan`\|`client_push`). |
| `drive_inventory_files` | Top-N archivos recientes por leaf (FK + ON DELETE CASCADE). |
| `drive_scans` | Histórico de scans completos: started_at, finished_at, status (`running`\|`ok`\|`error`), files_total, size_bytes_total, leaves_total, duration_s, triggered_by (`manual`\|`scheduler`\|`startup`). |

### Tabla cliente (vacía en `MODE=central`)

| Tabla | Para qué |
|---|---|
| `central_queue` | Heartbeats que el cliente no pudo enviar (offline, central down). Drain reintenta cada 15min con backoff. |

## Roles del panel

Los roles viven en `users.role` y se valida en cada endpoint.

| Role | Alias UI | Permisos en cliente | Permisos en central |
|---|---|---|---|
| **admin** | webmaster | Todo: configurar, restaurar, gestionar usuarios, drive link/unlink, cambiar timer | Todo lo de admin cliente + gestionar clientes/tokens/alerts |
| **operator** | técnico | Crear archivo manual, ver logs, restaurar, ver dashboard. **No** gestiona usuarios. | Igual + emitir/revocar tokens, configurar alertas, **no** gestiona usuarios. |
| **auditor** | gerente | Solo lectura: dashboard, listado, logs, audit. | Solo lectura: dashboard agregado, eventos, alertas, audit. |

### MFA por rol

- **admin** → MFA TOTP **obligatorio**. Si la cuenta no tiene MFA enrolada, el primer login redirige a `/auth/mfa-enroll` antes de permitir cualquier otra acción.
- **operator / auditor** → MFA opcional pero recomendado. Pueden enrolar desde la cuenta.

### Override per-usuario: `mfa_disabled`

Flag agregado en migración v3. Cuando está en `true`, el login para ese usuario **salta el enroll-required y el challenge TOTP**, aunque su rol sea admin. Útil para:

- Cuentas de servicio sin segundo factor.
- Clientes en kioskos donde TOTP no aplica.
- Login de emergencia tras perder el TOTP (alternativa a backup codes).

El flag se setea desde `/users → Editar` (toggle "Desactivar MFA") o vía `POST /auth/users/<uid>` con body `{mfa_disabled: true}`. **Guard**: un admin no puede desactivarse su propio MFA — debe pedirle a otro admin (anti-lockout).

### Matriz fina del módulo central (`backend/central/permissions.py`)

| Permiso | admin | operator | auditor |
|---|:-:|:-:|:-:|
| `central.dashboard:view` | ✓ | ✓ | ✓ |
| `central.audit:view` | ✓ | ✓ | ✓ |
| `central.clients:read` | ✓ | ✓ | ✓ |
| `central.clients:write` | ✓ | ✓ | — |
| `central.tokens:issue` | ✓ | ✓ | — |
| `central.tokens:revoke` | ✓ | ✓ | — |
| `central.alerts:configure` | ✓ | ✓ | — |
| `central.users:manage` | ✓ | — | — |
| `central.settings:edit` | ✓ | — | — |

## Sesiones y CSRF

- Cookie `snapshot_session` (HttpOnly, **Secure** en HTTPS, **SameSite=Lax**, **`max_age` = TTL** = 8h) con un ID random de 256 bits.
- TTL absoluto: `SESSION_TTL_HOURS` (default **8**).
- Idle timeout: `IDLE_TIMEOUT_MINUTES` (default **480** = 8h, alineado al TTL absoluto). Se refresca en cada request.
- Sliding refresh: si quedan menos de `SLIDING_THRESHOLD_HOURS` (default 2) para expirar, se renueva por otro full TTL al usar la sesión.
- CSRF: token único por sesión, expuesto en `<meta name="csrf-token">`, exigido en header `X-CSRF-Token` para `POST/PUT/PATCH/DELETE`.
- Endpoints exentos de CSRF: pre-login (auth.login, reset-request, mfa enroll), endpoints M2M con bearer (`/api/v1/heartbeat`, `/api/v1/ping`, `/api/v1/auth-check`), y `/audit/api/refresh`.

> **Cambio histórico**: el cookie antes era `SameSite=Strict` sin `max_age` (sesión-only). Causaba "me bota a login al cambiar de módulo" en algunas navegaciones top-level. Migrado a `Lax + max_age` en una iteración previa.

## Hashing y crypto en DB

| Dato | Algoritmo | Notas |
|---|---|---|
| `users.password_hash` | argon2id (memlimit ~ 19 MB, time 2, parallelism 1) | Vía `argon2-cffi`. |
| `users.mfa_secret` | AES-256-GCM | Cifrado en reposo con clave HKDF derivada de `SECRET_KEY` master. |
| `mfa_backup_codes.code_hash` | argon2id | Igual que passwords. |
| `password_resets.token_hash` | sha256 | Token plaintext nunca se guarda. |
| `central_tokens.token_hash` | argon2id | El plaintext se devuelve UNA vez al emitirlo desde el panel. |

## Master key

- 32 bytes hex (64 chars). Vive en `/etc/snapshot-v3/snapshot.local.conf`
  bajo `SECRET_KEY="…"`.
- Si está vacía o no existe, `install.sh` genera una y la guarda con
  mode 0600.
- Se puede sobreescribir con la env var `SNAPSHOT_SECRET_KEY` (precedence
  más alta).
- **Si la perdés** → todos los TOTP secrets quedan inservibles. Los
  usuarios deben re-enrolar con backup codes o `snapctl admin reset-mfa`.
  La cookie de sesión Flask también se invalida.

## Comandos útiles

```bash
# Ver versión actual del schema (esperado: 3)
sudo sqlite3 /var/lib/snapshot-v3/snapshot.db 'PRAGMA user_version;'

# Versiones del schema:
#   v1 - tablas auth iniciales (users, sessions, mfa, audit_auth)
#   v2 - jobs.actor_user_id (audit del CLI lanzado desde UI)
#   v3 - users.mfa_disabled (bypass MFA per-user)

# Listar usuarios
sudo snapctl admin list

# Crear usuario
sudo snapctl admin create --email user@org --role operator

# Resetear contraseña (devuelve una temporal)
sudo snapctl admin reset-password --email user@org

# Resetear MFA
sudo snapctl admin reset-mfa --email user@org

# Cambiar rol
sudo snapctl admin set-role --email user@org --role admin

# Cerrar todas las sesiones de un user
sudo snapctl admin revoke-sessions --email user@org

# Inspeccionar audit_auth (last 50 eventos)
sudo sqlite3 /var/lib/snapshot-v3/snapshot.db \
  "SELECT created_at, event, email FROM audit_auth ORDER BY id DESC LIMIT 50;"
```
