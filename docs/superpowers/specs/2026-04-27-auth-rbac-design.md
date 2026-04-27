# Auth + RBAC para snapshot-V3

**Fecha:** 2026-04-27
**Estado:** propuesto
**Sub-proyecto:** A (de A-F en la decomposición acordada — ver "Contexto")

## Contexto

snapshot-V3 hoy expone su panel web (`http://0.0.0.0:5070`) **sin
autenticación**. Cualquiera con acceso de red al backend puede listar
archives, ejecutar restores, vincular Drive, borrar archivos en Drive,
modificar timers de systemd. La única protección es que el deploy se
asume detrás de SSH tunnel o reverse proxy con auth externa.

El usuario quiere ahora dos modos de despliegue:

- **Cliente:** instancia local en el host del cliente que solo ve la
  data de ese cliente. Acceden únicamente empleados de la empresa
  prestadora del servicio (técnicos, webmasters). El cliente final
  **nunca** accede a su propio panel.
- **Central:** instancia en un subdominio que ve la data agregada de
  todos los clientes. Acceden empleados con rol ejecutivo (gerentes)
  además de técnicos y webmasters.

El conjunto de personas con acceso a ambos deploys es el mismo equipo
chico (los empleados de la empresa). El riesgo principal es el robo
de credenciales corporativas, no abuso interno.

Esta spec define **solo** la capa de autenticación y autorización
(sub-proyecto A). El despliegue dual con agregación cross-cliente,
dashboard agregado, alertas, backups de DBs y cifrado robusto se
abordan en specs separadas (sub-proyectos B-F).

## Requisitos

### Funcionales

1. Login con usuario + password.
2. Logout que revoque la sesión inmediatamente.
3. Tres roles: `admin`, `operator`, `auditor`. Permisos hard-coded
   por rol.
4. MFA TOTP (RFC 6238) opcional por usuario, **obligatorio para
   `admin`**.
5. Reset de password por email si SMTP está configurado, o por admin
   vía UI/CLI si no.
6. Cambio voluntario de password con verificación de la actual.
7. Audit log de eventos de auth.
8. Gestión de usuarios desde UI (solo `admin`).
9. CLI de recuperación (`snapctl admin ...`) ejecutable como root.
10. Bootstrap del primer admin durante `install.sh`.

### No-funcionales

1. **Hashing:** `argon2id` (no bcrypt — argon2id es el ganador del
   PHC 2015 y la recomendación actual de OWASP).
2. **Sesiones server-side**, no JWT. Revocación instantánea por
   `DELETE FROM sessions`.
3. **Timing-safe**: el flujo de login tiene latencia comparable
   independientemente de si el email existe o no, para no permitir
   user enumeration.
4. **Rate limiting** por IP y por email para resistir credential
   stuffing.
5. **CSRF** en todo POST/PUT/DELETE/PATCH.
6. **Headers de seguridad**: CSP, HSTS, X-Frame-Options, etc.
7. **HTTPS asumido vía reverse proxy externo** (nginx/caddy/traefik)
   o SSH tunnel. La unit `snapshot-backend.service` no monta TLS
   directamente. Si el deploy se accede sin TLS, las cookies marcadas
   `Secure` no viajan — el sistema lo detecta y emite warning en logs,
   pero permite operar (caso típico: dev local en `127.0.0.1`).
8. **Compatible con un solo worker de gunicorn**. La cache TTL del
   backend ya impone esa restricción; sesiones server-side en SQLite
   también funcionan con un único worker. Múltiples workers requerirán
   migrar a Redis en una spec posterior.

### Out of scope

Diferidos a specs siguientes para mantener esta acotada:

- **SSO/federation entre deploys.** Cada deploy tiene su propia tabla
  `users`. El equipo crea cuentas manualmente en cada deploy. Si
  resulta operacionalmente costoso, se aborda en spec dedicada.
- **Roles con scope a proyectos** (gerentes regionales). Hoy todos los
  roles son globales dentro del deploy.
- **Diferencias funcionales entre deploy cliente y central**. Esta
  spec asume comportamiento idéntico de auth en ambos. Las diferencias
  de qué muestra cada deploy se definen en la spec B (despliegue dual).
- **Captcha**. Rate limiting + lockout cubren el riesgo.
- **WebAuthn / hardware keys**. TOTP es suficiente.
- **OAuth con Google / OIDC corporativo**. Spec posterior si surge.
- **Grupos de permisos editables desde UI**. Permisos hard-coded.
- **"Remember me" / sesiones persistentes**. Solo sliding session
  con TTL 8h y idle timeout 1h.

## Arquitectura

```
┌──────────────────────────────────────────────────────────┐
│  Browser                                                  │
│    ↓ cookie: snapshot_session=<random_id>                │
│  Backend Flask (gunicorn 1 worker × 6 threads)           │
│    ├── auth/                                              │
│    │   ├── routes.py        login, logout, mfa, reset    │
│    │   ├── service.py       hashing, sessions, rate-limit│
│    │   ├── decorators.py    @require_role(...)           │
│    │   └── audit.py         eventos auth → audit_auth    │
│    ├── routes/api.py        ← decoradores aplicados      │
│    ├── routes/web.py        ← decoradores aplicados      │
│    └── models/db.py         + 6 tablas nuevas + migration│
└──────────────────────────────────────────────────────────┘
```

Idéntico en deploy cliente y deploy central. La distinción es solo
operativa (qué cuentas existen y qué rol tienen).

### Sesión server-side, no JWT

El cookie del browser solo lleva el `session_id` opaco. Toda la
información sensible (user_id, rol, MFA verificada, csrf_token) vive
en la tabla `sessions` del servidor.

Ventajas vs. JWT:

- Revocación instantánea: `DELETE FROM sessions WHERE id = ?`.
- Cambio de rol toma efecto al siguiente request sin esperar
  expiración del token.
- Tamaño del cookie mínimo (32 bytes hex).
- Sin riesgo de fuga del payload del JWT (que en debug se loguea,
  se cachea en CDNs, etc.).

Costo: una query a SQLite por request autenticado. Negligible para
la escala esperada (un equipo chico operando, no tráfico público).

## Modelo de datos

Seis tablas nuevas en la SQLite existente del backend
(`/var/lib/snapshot-v3/snapshot.db`):

```sql
-- Usuarios
CREATE TABLE users (
  id              INTEGER PRIMARY KEY,
  email           TEXT NOT NULL UNIQUE COLLATE NOCASE,
  display_name    TEXT NOT NULL,
  password_hash   TEXT NOT NULL,            -- argon2id
  role            TEXT NOT NULL CHECK(role IN ('admin','operator','auditor')),
  mfa_secret      TEXT,                     -- TOTP secret cifrado AES-GCM, NULL = sin MFA
  mfa_enrolled_at TEXT,
  status          TEXT NOT NULL DEFAULT 'active'
                    CHECK(status IN ('active','disabled','locked')),
  failed_attempts INTEGER NOT NULL DEFAULT 0,
  locked_until    TEXT,                     -- ISO timestamp si lockeado
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL,
  last_login_at   TEXT
);

-- Sesiones activas
CREATE TABLE sessions (
  id            TEXT PRIMARY KEY,           -- 256-bit random hex
  user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at    TEXT NOT NULL,
  last_seen_at  TEXT NOT NULL,
  expires_at    TEXT NOT NULL,
  ip            TEXT,
  user_agent    TEXT,
  csrf_token    TEXT NOT NULL,
  mfa_verified  INTEGER NOT NULL DEFAULT 0  -- 0 = pendiente, 1 = verificada
);

-- Historial de passwords (para evitar reutilización)
CREATE TABLE password_history (
  user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  password_hash TEXT NOT NULL,
  changed_at    TEXT NOT NULL,
  PRIMARY KEY (user_id, changed_at)
);

-- Tokens de reset de password
CREATE TABLE password_resets (
  token_hash    TEXT PRIMARY KEY,           -- SHA-256 del token (no guardamos el token plano)
  user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at    TEXT NOT NULL,
  expires_at    TEXT NOT NULL,              -- 1h
  consumed_at   TEXT                        -- NULL hasta usar; one-shot
);

-- Backup codes de MFA (10 por usuario al enrollment, one-shot cada uno)
CREATE TABLE mfa_backup_codes (
  user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  code_hash   TEXT NOT NULL,                -- argon2id
  consumed_at TEXT,
  PRIMARY KEY (user_id, code_hash)
);

-- Audit log de eventos de auth
CREATE TABLE audit_auth (
  id          INTEGER PRIMARY KEY,
  actor       TEXT NOT NULL CHECK(actor IN ('web','cli','system')),
                                             -- consistente con tabla `audit` existente
  user_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
  email       TEXT,                          -- denormalizado por si user_id NULL
  event       TEXT NOT NULL,                 -- ver lista de eventos abajo
  ip          TEXT,
  user_agent  TEXT,
  detail      TEXT,                          -- JSON arbitrario
  created_at  TEXT NOT NULL
);

CREATE INDEX idx_sessions_user ON sessions(user_id);
CREATE INDEX idx_sessions_expires ON sessions(expires_at);
CREATE INDEX idx_audit_auth_created ON audit_auth(created_at DESC);
CREATE INDEX idx_audit_auth_user ON audit_auth(user_id, created_at DESC);
CREATE INDEX idx_password_resets_user ON password_resets(user_id);
```

**Eventos de `audit_auth.event`:**
`login_ok`, `login_fail`, `logout`, `pwd_change`, `role_change`,
`mfa_enable`, `mfa_disable`, `mfa_verify_ok`, `mfa_verify_fail`,
`mfa_backup_used`, `account_lock`, `account_unlock`, `reset_request`,
`reset_consume`, `session_revoked_admin`, `user_create`, `user_disable`.

**Migrations:** versión simple por `PRAGMA user_version`. La función
de bootstrap del backend chequea la versión actual al arrancar y
aplica los DDL de versiones siguientes. Idempotente.

## Roles y permisos

Permisos hard-coded en código (no tabla `permissions`):

| Capacidad | admin | operator | auditor |
|-----------|:-----:|:--------:|:-------:|
| Ver dashboard | ✅ | ✅ | ✅ |
| Ver pestaña archivos | ✅ | ✅ | ❌ |
| Ver logs | ✅ | ✅ | ❌ |
| Ver auditoría cross-host | ✅ | ❌ | ✅ |
| Ejecutar archive ahora | ✅ | ✅ | ❌ |
| Restaurar archive | ✅ | ✅ | ❌ |
| **Eliminar archive** | ✅ | ❌ | ❌ |
| Editar paths backup / scheduler | ✅ | ✅ | ❌ |
| Editar taxonomía | ✅ | ❌ | ❌ |
| Vincular/desvincular Drive | ✅ | ❌ | ❌ |
| Setear/borrar password de cifrado | ✅ | ❌ | ❌ |
| Crear/editar/borrar usuarios | ✅ | ❌ | ❌ |
| Cambiar roles | ✅ | ❌ | ❌ |
| Forzar logout de otro usuario | ✅ | ❌ | ❌ |
| Ver `audit_auth` | ✅ | ❌ | ❌ |

Implementación con decoradores:

```python
@api_bp.post("/archive/delete")
@require_role("admin")
def archive_delete(): ...

@api_bp.post("/archive/create")
@require_any_role("admin", "operator")
def archive_create(): ...

@api_bp.get("/archive/summary")
@require_login
def archive_summary(): ...   # cualquier rol logueado
```

La UI esconde lo que el rol no puede tocar (basado en `g.current_user.role`),
**pero el backend valida igual**. La UI no es la fuente de verdad
de seguridad.

## Flujos

### Login

1. POST `/auth/login` con `{email, password, mfa_code?}`.
2. **Rate limit**: por IP 10/min, por email 5 fallidos consecutivos.
3. Lookup user por email (case-insensitive).
4. Si user no existe: ejecutar un `argon2.verify` dummy contra un
   hash conocido para igualar latencia. Devolver mismo error genérico
   que credenciales inválidas. Audit `login_fail`.
5. Si user existe:
   a. Si `status='disabled'` o `locked_until > now` → error genérico
      + audit `login_fail`.
   b. `argon2.verify(password, user.password_hash)`. Si falla:
      `failed_attempts++`. Si `failed_attempts >= 5`:
      `locked_until = now + 15min × 2^(lock_count)` (backoff
      exponencial, máx 24h). Audit `login_fail` o `account_lock`.
   c. Si MFA habilitado y `mfa_code` no vino → respuesta HTTP 200
      `{require_mfa: true}` (sin crear sesión). Frontend pide TOTP.
   d. Si MFA habilitado y `mfa_code` vino: validar TOTP con
      tolerancia ±1 ventana. Si falla, `failed_attempts++` igual,
      audit `mfa_verify_fail`. Si vale: audit `mfa_verify_ok`.
   e. Si user es `admin` y NO tiene MFA → respuesta
      `{require_mfa_enroll: true, enroll_token: <one-shot>}`. El
      frontend usa el token para llamar al flujo de enrollment.
      No se crea sesión hasta completar enrollment.
6. Crear sesión:
   - `session_id = secrets.token_hex(32)`.
   - `csrf_token = secrets.token_hex(32)`.
   - `expires_at = now + 8h`.
   - `mfa_verified = 1` si TOTP fue OK o si user no usa MFA.
7. Setear cookie `snapshot_session` (HttpOnly, Secure, SameSite=Strict).
8. Reset `failed_attempts = 0`. Update `last_login_at`.
9. Audit `login_ok`.
10. Respuesta `{ok, role, display_name, csrf_token, expires_at}`.

### Logout

- POST `/auth/logout` → `DELETE FROM sessions WHERE id = ?`.
  Setear cookie con `expires=0`. Audit `logout`. Respuesta `{ok: true}`.

### Sliding session

Middleware en cada request autenticado:

1. Lee `session_id` del cookie. Si no hay → 401 (anónimo).
2. `SELECT * FROM sessions WHERE id = ?`.
3. Si `expires_at <= now` → eliminar, 401.
4. Si `now - last_seen_at > 1h` (idle) → eliminar, 401.
5. Si `expires_at - now < 2h` → extender `expires_at = now + 8h`.
6. Update `last_seen_at = now`.
7. Carga `g.current_user`, `g.session`. Continúa al endpoint.

### Enrollment MFA

1. POST `/auth/mfa/enroll/start` → genera secret de 20 bytes,
   responde `{secret, otpauth_uri, qr_svg}`. El secret aún no se
   persiste.
2. Frontend muestra QR. Usuario escanea con app autenticadora
   (Google Authenticator, Authy, 1Password, Bitwarden).
3. POST `/auth/mfa/enroll/confirm` con `{secret, code}`. Backend
   valida `pyotp.TOTP(secret).verify(code, valid_window=1)`.
4. Si OK:
   a. Cifrar `secret` con AES-GCM (key = HKDF(SECRET_KEY, "mfa")).
   b. Guardar en `users.mfa_secret`, set `mfa_enrolled_at = now`.
   c. Generar 10 backup codes (16 chars cada uno, base32). Hash
      con argon2id, guardar en `mfa_backup_codes`.
   d. Devolver los 10 backup codes en plano (única vez). Avisar
      "Anotalos, no se vuelven a mostrar".
   e. Audit `mfa_enable`.
5. Si falla: respuesta de error, sin persistir nada.

### Login con backup code

Si el usuario perdió el dispositivo TOTP, en el prompt de MFA puede
ingresar un backup code (16 chars) en vez del código de 6 dígitos:

1. Endpoint detecta longitud >6 → trata como backup code.
2. Hashea y busca en `mfa_backup_codes WHERE user_id = ? AND consumed_at IS NULL`.
3. Si match: `consumed_at = now`. Audit `mfa_backup_used`. Login OK.
4. El usuario debería re-enrollar MFA con un dispositivo nuevo
   (la UI lo notifica).

### Password reset (self-service, requiere SMTP)

1. POST `/auth/reset-request` con `email`.
2. **Rate limit**: 3/min por IP, 3/h por email.
3. Respuesta siempre `200 {ok: true}` independientemente de si el
   email existe (no enumeration).
4. Si user existe y está `active`:
   - Generar `token = secrets.token_urlsafe(32)`.
   - Guardar `sha256(token)` en `password_resets` con `expires_at = now + 1h`.
   - Email al user con link `https://<host>/auth/reset?token=<token>`.
   - Audit `reset_request`.
5. POST `/auth/reset-consume` con `{token, new_password}`.
6. Hashear token y buscar en `password_resets WHERE token_hash = ?
   AND consumed_at IS NULL AND expires_at > now`.
7. Validar policy de la nueva password.
8. Marcar `consumed_at = now`. Hash y guardar nueva password.
   Agregar al `password_history`. Reset `failed_attempts`.
9. **Revocar todas las sesiones del user** (`DELETE FROM sessions WHERE user_id = ?`).
10. Audit `reset_consume`, `pwd_change`.

### Password reset (admin/CLI, sin SMTP)

UI Ajustes → Usuarios → "Resetear password":
- Backend genera password aleatoria de 16 chars, la setea, devuelve
  en JSON una sola vez. UI la muestra para copiar.
- Mismo efecto: revocar sesiones, audit `pwd_change`.

CLI: `sudo snapctl admin reset-password --email x@y.com` → idéntico,
imprime la password en stdout.

### Cambio de password (logueado)

POST `/auth/password` con `{current, new}`:

1. Validar `current` con argon2.
2. Validar policy de `new`.
3. Verificar que `new` no esté en `password_history` (últimas 5).
4. Verificar que `new` no contenga email/display_name del user.
5. Hashear `new`, update `users.password_hash`, append a
   `password_history` (truncar a 5).
6. Audit `pwd_change`. Respuesta `{ok: true}`.

No se revocan otras sesiones — el user sigue logueado en el resto
de sus dispositivos. Si quiere revocarlas, hay un botón "cerrar otras
sesiones" separado.

## Seguridad transversal

### Hashing

- `argon2-cffi` con parámetros: `time_cost=3, memory_cost=64*1024 (64MB),
  parallelism=4, hash_len=32`.
- Re-hash transparente al login si el hash existente fue creado con
  parámetros más débiles (futureproof).

### Password policy

Validada en backend al crear, cambiar y resetear:

- Mínimo 12 caracteres.
- Score `zxcvbn-python` ≥ 3.
- No debe contener (case-insensitive substring) email ni display_name.
- No debe coincidir con las últimas 5 entries de `password_history`
  para ese user.

### Rate limiting

`Flask-Limiter` con storage en SQLite (extensión existente).

| Endpoint | Límite IP | Límite por email | Lockout cuenta |
|----------|-----------|------------------|----------------|
| `/auth/login` | 10/min | 5 fallos consecutivos | 15min × 2^lock_count, máx 24h |
| `/auth/reset-request` | 3/min | 3/h | — |
| `/auth/mfa/enroll/confirm` | 5/min | — | — |

### CSRF

- `csrf_token` generado por sesión, guardado en `sessions.csrf_token`.
- Frontend lo obtiene de `GET /auth/csrf` (requiere sesión válida).
- Para POST/PUT/DELETE/PATCH: header `X-CSRF-Token` debe coincidir.
- Sin token o mismatch → 403.
- Excepciones: `/auth/login`, `/auth/reset-request`, `/auth/reset-consume`
  no requieren CSRF (no hay sesión todavía).

### Cookie de sesión

- Nombre: `snapshot_session`.
- Atributos: `HttpOnly`, `Secure` (auto-detect via `request.is_secure`),
  `SameSite=Strict`, `Path=/`.
- Sin "remember me". Sin persistencia local.
- Logout: `Max-Age=0`.

Si el deploy se accede sin TLS (típico dev local en `127.0.0.1`),
`Secure` se omite; el backend loguea WARNING al primer request en
prod-like host (heurística: hostname != localhost).

### Cifrado de `mfa_secret`

- AES-GCM 256-bit.
- Key derivation: `HKDF(SECRET_KEY, info=b"mfa")`.
- `SECRET_KEY` vive en `/etc/snapshot-v3/snapshot.local.conf` (0600).
  Si no existe, `install.sh` lo genera con `secrets.token_hex(32)`.
- Pérdida de `SECRET_KEY` = todos los TOTP se invalidan. Los users
  re-enrollan con sus backup codes (o admin reset).

### Headers de seguridad

Vía `Flask-Talisman`:

```
Content-Security-Policy: default-src 'self'; img-src 'self' data:;
                         style-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com;
                         script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
Referrer-Policy: same-origin
Strict-Transport-Security: max-age=31536000; includeSubDomains  (solo si HTTPS)
```

`'unsafe-inline'` para Tailwind CDN es necesario hoy. Spec posterior
puede mover a build local + nonces para endurecer la CSP.

### Audit log

Cada evento de auth se loguea en dos lugares:

1. Tabla `audit_auth` (consultable desde la UI por admin).
2. Log JSON line en `/var/log/snapshot-v3/auth.log` (rotación 5MB×5).

Eventos: ver lista en sección "Modelo de datos".

### Logging seguro

Nunca loguear: passwords, hashes, tokens completos, MFA secrets,
backup codes. Tokens largos se loguean solo con primeros 8 chars
+ "..." + últimos 4.

## Bootstrap y operación

### Primera instalación

`install.sh` agrega un paso 9 al final, antes de imprimir el resumen:

```
[9/9] Crear primer usuario admin

  Email del admin: admin@empresa.com
  ✔  Password aleatoria generada (24 chars):  Hk7-8s2P-3xR9-Lm2f-8Qpz-Kw3T
  ⚠  Anotala — NO se vuelve a mostrar.
  ⚠  Tu primer login te pedirá configurar MFA (obligatorio para admin).
```

Con `-y` (no interactivo): el instalador genera email
`admin@<hostname>` y password random, los escribe a
`/root/.snapshot-v3-admin-credentials` (0600), avisa, y termina.

### CLI de operación

```bash
sudo snapctl admin list
sudo snapctl admin create --email x@y.com --display "Juan" --role operator
sudo snapctl admin set-role --email x@y.com --role auditor
sudo snapctl admin reset-password --email x@y.com
sudo snapctl admin disable --email x@y.com
sudo snapctl admin enable --email x@y.com
sudo snapctl admin revoke-sessions --email x@y.com
sudo snapctl admin reset-mfa --email x@y.com
```

Estos comandos se ejecutan como root y NO requieren login. Útiles
para recuperación si se pierde el único admin. Auditados con
`audit_auth.actor='cli'`.

### UI de gestión de usuarios

Nueva pestaña `/users` (solo `admin`):

- Tabla: email, nombre, rol, MFA sí/no, último login, status,
  sesiones activas (count).
- Acciones por fila: editar (rol, status, display), resetear password,
  deshabilitar, forzar logout, ver eventos de audit del usuario.
- Botón "+ Crear usuario": modal con email, display, rol, password
  inicial (generable random).

### Acceso al deploy sin TLS

El backend escucha en `0.0.0.0:5070` por default. Hoy se asume:

- SSH tunnel desde la máquina del operador, o
- Reverse proxy externo con TLS, o
- Acceso desde la misma máquina (`127.0.0.1`).

Esta spec **no** agrega TLS al backend. El cookie marcado `Secure`
no viaja en HTTP plano — esto rompe login si se accede por IP sin
TLS. El backend detecta y emite warning visible.

Recomendación documentada en README: poner Caddy/nginx delante con
Let's Encrypt si se expone públicamente. (No es trabajo de esta spec.)

## Cambios al código existente

| Archivo | Cambio |
|---------|--------|
| `backend/app.py` | Registrar `auth_bp`. Setup `Flask-Limiter`. Setup security headers vía `Flask-Talisman`. Cargar `SECRET_KEY` desde `snapshot.local.conf`. |
| `backend/auth/` (nuevo) | `routes.py`, `service.py`, `decorators.py`, `audit.py`, `__init__.py`. |
| `backend/routes/api.py` | Aplicar `@require_role` o `@require_any_role` en cada endpoint según matriz de roles. |
| `backend/routes/web.py` | `@require_login` redirige a `/auth/login` si no hay sesión. Pasar `g.current_user` al template. |
| `backend/models/db.py` | Migration que crea las 4 tablas + 2 (resets, backup_codes). Versionado por `PRAGMA user_version`. |
| `backend/services/snapctl.py` | Aceptar `actor_user_id` opcional en `_run()` para que la tabla `jobs` registre quién ejecutó. |
| `backend/config.py` | `SECRET_KEY`, `SESSION_TTL_HOURS=8`, `IDLE_TIMEOUT_MINUTES=60`. |
| `backend/requirements.txt` | + `argon2-cffi`, `pyotp`, `zxcvbn-python`, `flask-limiter`, `flask-talisman`. |
| `core/bin/snapctl` | Subcomando `admin` con `list/create/set-role/reset-password/disable/enable/revoke-sessions/reset-mfa`. Implementación delega a un script Python que reusa `backend/auth/service.py`. |
| `frontend/templates/auth/login.html` | Form de login + opcional input MFA. |
| `frontend/templates/auth/mfa_enroll.html` | QR + input de verificación + display de backup codes. |
| `frontend/templates/auth/mfa_challenge.html` | Input de TOTP/backup code para login. |
| `frontend/templates/auth/password_reset.html` | Solicitud + consumo. |
| `frontend/templates/users.html` | Tabla + acciones. |
| `frontend/templates/base.html` | Header con email del user logueado, link a /change-password, dropdown logout, mostrar/ocultar links según rol. |
| `frontend/static/js/auth.js` | Helper para incluir `X-CSRF-Token` en todos los fetch POST/PUT/DELETE. |
| `install.sh` | Paso 9: bootstrap admin + generar `SECRET_KEY` si no existe. |
| `systemd/snapshot-backend.service` | Sin cambios. |
| `README.md` | Sección "Autenticación y usuarios" con setup, roles, recuperación. |

## Plan de testing

### Unit tests

- `argon2id` hash/verify con re-hash transparente.
- Validación de password policy: cada regla individualmente.
- Generación y verificación de TOTP (pyotp).
- Generación y verificación de CSRF tokens.
- Decoradores `@require_login`, `@require_role`, `@require_any_role`:
  comportamiento con sesión válida, sin sesión, con rol equivocado.
- Cifrado/descifrado de `mfa_secret` con AES-GCM.
- Backoff exponencial del lockout.

### Integration tests (pytest + Flask test client)

- Login OK con/sin MFA.
- Login fallido: enumera mismo error y latencia comparable.
- Lockout tras 5 intentos: locked_until correcto, login bloqueado.
- Auto-unlock al expirar `locked_until`.
- Sliding session: extiende `expires_at` cuando queda <2h.
- Idle timeout: invalida sesión tras 1h sin requests.
- Logout: revoca sesión.
- Reset password con SMTP: token válido único, expira, una sola
  vez, revoca otras sesiones.
- CSRF: rechaza POST sin token, con token equivocado, con token de
  otra sesión.
- Cambio de password: rechaza si igual a alguna de las últimas 5.
- MFA enrollment completo + login con TOTP + login con backup code.
- Revocación admin de sesión: el user afectado queda fuera al
  siguiente request.

### Security tests

- Timing attack: latencia comparable entre email inexistente y
  password mala (medir N=100 requests, asumir distribución similar).
- SQL injection en email, password, mfa_code (parametrización).
- XSS en display_name (escape correcto en templates).
- Cookie sin `HttpOnly`/`Secure`/`SameSite` no es aceptada (assert
  en build).
- Header `X-Frame-Options: DENY` presente.
- CSP no permite scripts inline excepto los explícitamente whitelisted.

### Manual smoke

1. Install fresca → bootstrap admin → primer login → enroll MFA →
   guardar backup codes.
2. Crear `operator` desde UI → operator hace login → ejecuta archive
   → confirma que NO ve la pestaña de usuarios.
3. Crear `auditor` → confirma que ve solo dashboard + auditoría.
4. Admin revoca sesión del operator → operator queda fuera al
   siguiente request.
5. Forzar 5 fallos seguidos en operator → confirma lockout.
6. Esperar 15 min → operator se desbloquea solo.
7. Reset password vía CLI cuando admin "olvida" la suya → entrar
   con la nueva password.

## Riesgos y mitigaciones

| Riesgo | Mitigación |
|--------|-----------|
| Pérdida del único admin | CLI `snapctl admin create/reset-password` ejecutable como root sin login |
| Pérdida del SECRET_KEY | Backup codes de MFA permiten re-enrollment; en último caso `snapctl admin reset-mfa` |
| Cookie hijack en HTTP plano | `Secure` + warning explícito + recomendación TLS en README |
| Fugas en multi-worker | Spec asume 1 worker (consistente con cache TTL); migración a Redis en spec posterior |
| Timing leak revelando emails válidos | Hash dummy + delay constante en path de "user no existe" |
| Replay de cookie tras logout | Sesión revocada en DB, cualquier request con esa cookie da 401 |
| MFA secret robado de DB | Cifrado AES-GCM; key fuera de la DB; auditable |

## Métricas de éxito

- Bootstrap admin funciona en `install.sh -y` y deja credenciales
  utilizables.
- Login con MFA en <2 segundos en hardware típico.
- Tests integration verde en CI.
- Test de timing attack: diferencia <50ms entre email válido e
  inválido en 95% de runs.
- Fuzz test de 1000 requests aleatorios al login no causa errores
  500.

## Alcance en una frase

Una capa de auth completa con sesiones server-side en SQLite, 3
roles hard-coded (`admin`, `operator`, `auditor`), MFA TOTP
obligatorio para admin, rate limiting + lockout exponencial, CSRF,
audit log dedicado, y bootstrap + recuperación vía `install.sh` y
`snapctl admin` — sin federation entre deploys, sin scoping por
proyecto, sin OAuth externo, sin captcha, sin WebAuthn (todos
diferidos a specs posteriores).
