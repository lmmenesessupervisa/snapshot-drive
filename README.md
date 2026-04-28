# snapshot-V3

Sistema de backups cold-storage para servidores Linux, con frontend web propio,
streaming directo a Google Drive y aislamiento total respecto al stack del host.
Tres capas independientes que comparten la misma fuente de verdad (el CLI `snapctl`):

```
┌──────────────────────────────────────────────────────────────────┐
│  FRONTEND  (Tailwind CDN)       ← Dashboard, archivos, ajustes  │
│  BACKEND   (Flask + SQLite)     ← API REST en :5070             │
│  CORE      (Bash + zstd +       ← CLI `snapctl`, lógica real    │
│             rclone + systemd)                                    │
└──────────────────────────────────────────────────────────────────┘
```

## Modelo de backup actual: archive mensual cold-storage

El flujo de producción es **un único `tar | zstd | [openssl] | rclone rcat`**
que sube un `.tar.zst[.enc]` mensual directo a Google Drive **sin pasar por
disco local**. La ruta destino se construye con una taxonomía declarada por
el operador:

```
PROYECTO/ENTORNO/PAIS/os/linux/NOMBRE/YYYY/MM/DD/
    servidor_NOMBRE_YYYYMMDD_HHMMSS.tar.zst[.enc]
```

Por ejemplo:
`superaccess-uno/cloud/colombia/os/linux/web01/2026/04/01/servidor_web01_20260401_020317.tar.zst.enc`

Esta taxonomía permite que múltiples hosts compartan el mismo Drive sin
colisiones y que la pestaña de **Auditoría** (opcional) agregue el estado
de todo el fleet leyendo `_status/<host>.json`.

> **Nota sobre `restic`.** `snapctl` aún incluye el flujo restic legacy
> (`create`, `reconcile`, `prune`, `sync`, `check`) por compatibilidad y para
> operadores que prefieran incrementales sobre archive mensual. **Está
> desactivado por defecto** — `install.sh` deshabilita esos timers. Para
> reactivarlo manualmente: `sudo systemctl enable --now snapshot@create.timer`.

## Requisitos de aislamiento respecto al stack host

- Corre en puertos **no estándar** (`5070` API+UI; `5071` reservado).
- **No usa** Nginx/Apache — Flask/Gunicorn sirve UI + API en el mismo puerto.
- **No usa** Docker.
- No interfiere con PostgreSQL, Oracle, Laravel, WebSockets del host.
- Estado y datos aislados en `/var/lib/snapshot-v3/` y `/var/log/snapshot-v3/`.
- Override local con secrets en `/etc/snapshot-v3/` (sobrevive a upgrades).
- Se ejecuta como servicios **systemd** dedicados.
- `core/lib/common.sh` antepone `bundle/bin` al `PATH` → `snapctl` usa
  exclusivamente los binarios bundled, nunca los del host.

## Estructura del repositorio

```
snapshot-V3/
├── core/
│   ├── bin/
│   │   ├── snapctl              CLI central (única fuente de verdad)
│   │   └── snapctl-notify       envío SMTP en éxito/fallo
│   ├── lib/
│   │   ├── common.sh            logging JSON, profiles rclone, config loader
│   │   ├── drive.sh             OAuth + manipulación rclone.conf
│   │   └── archive.sh           pipeline tar|zstd|rclone (cold-storage)
│   └── etc/
│       ├── snapshot.conf            config global (versionada)
│       ├── snapshot.local.conf.example  plantilla con secrets
│       └── excludes.list            exclusiones para tar/restic
├── backend/
│   ├── app.py                   Flask factory + arranque
│   ├── config.py
│   ├── routes/
│   │   ├── api.py               endpoints REST
│   │   ├── web.py               páginas HTML
│   │   └── audit.py             auditoría cross-host (opcional)
│   ├── services/
│   │   ├── snapctl.py           wrapper subprocess + TTL cache
│   │   ├── archive_ops.py       rclone lsjson/cat/deletefile
│   │   ├── archive_config.py    taxonomía + password
│   │   ├── scheduler.py         escribe drop-ins systemd
│   │   ├── drive_oauth.py       Device Flow Google
│   │   ├── audit.py             agrega _status/<host>.json
│   │   └── sysconfig.py         BACKUP_PATHS, RCLONE_REMOTE_PATH
│   ├── models/db.py             SQLite (jobs, audit) — WAL
│   └── requirements.txt
├── frontend/
│   ├── templates/               base, index, snapshots, logs, settings…
│   └── static/{css,js}/         Tailwind CDN + componentes
├── systemd/
│   ├── snapshot-backend.service
│   ├── snapshot@.service        instanciable: archive|create|prune|...
│   ├── snapshot@.timer          template
│   ├── snapshot@archive.{service,timer}.d/  drop-ins del archive
│   ├── snapshot@reconcile.timer.d/          drop-in legacy
│   ├── snapshot-healthcheck.service
│   └── snapshot-healthcheck.timer
├── install.sh
├── uninstall.sh
└── README.md
```

## Instalación (Linux x86_64 / aarch64)

```bash
sudo bash install.sh       # instalación estándar
sudo bash install.sh -y    # pip silencioso (no-interactivo)
```

**El instalador NO toca apt ni el Python del sistema.** Descarga de los
releases oficiales y deja aislado en `/opt/snapshot-V3/bundle/`:

- **Python standalone** (`python-build-standalone` de astral-sh) — 3.12.8
- **restic** (release estático de `restic/restic`) — 0.17.3
- **rclone** (release estático de `rclone.org`) — v1.68.2

Versiones pinneadas en cabecera de `install.sh`; override con env vars:
`PYTHON_VERSION`, `PYTHON_PBS_DATE`, `RESTIC_VERSION`, `RCLONE_VERSION`.
El instalador es idempotente: si la versión bundled coincide con la pinneada,
no re-baja el binario.

Dependencias del sistema mínimas: `curl`, `tar`, `python3` (cualquier versión,
solo se usa como runner de extractores `bz2`/`zip`), `rsync`. Todas son core
utils presentes por defecto en Ubuntu Server.

Pasos del instalador:

1. Verifica el tooling mínimo. Si falta algo, aborta con un mensaje claro.
2. Crea dirs: `/opt/snapshot-V3` (0750), `/var/lib/snapshot-v3` (0750),
   `/var/log/snapshot-v3` (0755).
3. Despliega el código con `rsync --delete` (excluye `bundle/`, `.venv/`,
   `logs/`, `__pycache__`). Crea `/etc/snapshot-v3/snapshot.local.conf`
   (0600) desde plantilla si no existe — **no se sobrescribe en upgrades**.
4. Descarga Python/restic/rclone a `bundle/`. Idempotente por versión.
5. Crea el venv contra el Python bundled y resuelve `requirements.txt`.
6. Instala unidades systemd y drop-ins.
7. Activa: `snapshot-backend.service`, `snapshot@archive.timer`,
   `snapshot-healthcheck.timer`. Desactiva los timers restic legacy.
8. Valida `GET /api/health`.

### Desinstalación

```bash
sudo bash uninstall.sh              # quita código + servicios, CONSERVA datos
sudo bash uninstall.sh --purge      # también borra /var/lib, /var/log, /etc/snapshot-v3 (IRREVERSIBLE)
sudo bash uninstall.sh --dry-run    # simula sin ejecutar
```

`uninstall.sh` no toca paquetes apt ni los archivos ya subidos a Google Drive.
Por defecto preserva `snapshot.local.conf` y la base SQLite, así que una
reinstalación posterior reaprovecha vinculación OAuth e historial de jobs.

## Configuración inicial

### 1. Credenciales OAuth (Device Flow)

Tras `install.sh`, edita el override local con tus credenciales de Google
Cloud Console (OAuth Client tipo *TVs and Limited Input devices*):

```bash
sudo nano /etc/snapshot-v3/snapshot.local.conf
# Rellena GOOGLE_CLIENT_ID y GOOGLE_CLIENT_SECRET
sudo systemctl restart snapshot-backend
```

`snapshot.local.conf` vive **fuera del árbol de código** (`/etc/snapshot-v3/`),
con permisos `600`, sobreescribe los defaults de `snapshot.conf` al cargarse
desde `common.sh`, y **sobrevive a upgrades** (`rsync --delete` opera solo
sobre `/opt/snapshot-V3/`).

### 2. Vincular Drive desde el panel

Abrí `http://127.0.0.1:5070/` (por SSH tunnel si estás remoto) →
**Vincular Drive** → mostrará un código tipo `ABC-1234` y abrirá la URL de
verificación de Google. Tras autorizar, el backend hace polling y escribe
la sección `[gdrive]` en `/var/lib/snapshot-v3/rclone.conf` (0600).

Scope OAuth solicitado: `https://www.googleapis.com/auth/drive.file`
(rclone solo verá los archivos creados por la propia app).

### 3. Definir taxonomía + paths a respaldar

En **Ajustes**:

- **Taxonomía** (obligatoria para archive): `BACKUP_PROYECTO`, `BACKUP_ENTORNO`
  (`cloud` | `local`), `BACKUP_PAIS`, `BACKUP_NOMBRE`. Se persisten en
  `snapshot.local.conf`.
- **Rutas a respaldar** (`BACKUP_PATHS`): por defecto `/etc /home /var/www /root`.
- **Password de cifrado** (opcional): si está seteada, los archives se
  cifran con `openssl aes-256-cbc -pbkdf2`. **La password no se devuelve
  por la API por seguridad** — solo `password_set: true/false`. Si la
  perdés, los `.tar.zst.enc` ya subidos no se pueden descifrar. Backuppeala
  en un gestor de contraseñas o sobre sellado.

### 4. Verificación

```bash
sudo snapctl status
systemctl list-timers 'snapshot*'
sudo snapctl archive            # primer archive manual de prueba
```

## Autenticación y usuarios

snapshot-V3 expone su panel web detrás de login. **Cualquier acceso al
panel requiere usuario + contraseña**, y el rol `admin` requiere MFA
(TOTP) obligatorio.

### Roles

| Rol | Acceso |
|-----|--------|
| `admin` | Todas las pantallas y acciones, incluyendo gestión de usuarios |
| `operator` | Dashboard, archivos, logs, ejecutar/restaurar archive, editar paths y horarios |
| `auditor` | Dashboard + Auditoría (solo lectura) |

Los permisos detallados (qué endpoint puede llamar cada rol) están en
`docs/superpowers/specs/2026-04-27-auth-rbac-design.md`.

### Bootstrap del primer admin

`install.sh` crea automáticamente un admin la primera vez:

- **Modo interactivo** (`sudo ./install.sh`): te pide el email, genera
  password aleatoria de 24 chars, la imprime una sola vez. Anotala.
- **Modo `-y`** (`sudo ./install.sh -y`): genera email
  `admin@<hostname>`, password aleatoria, y deja credenciales en
  `/root/.snapshot-v3-admin-credentials` (0600).

En tu primer login el admin debe configurar MFA (TOTP, compatible
con Google Authenticator, Authy, 1Password, Bitwarden). Recibirás
**10 backup codes** — guardalos en un lugar seguro fuera del sistema
(gestor de contraseñas, sobre sellado, etc.).

### Recuperación: comandos de admin

Si olvidás la password del único admin, podés resetearla como root
desde el host:

```bash
sudo snapctl admin reset-password --email admin@hostname
```

Otros comandos disponibles (todos requieren root, sin login):

```bash
sudo snapctl admin list                                     # listar
sudo snapctl admin create --email x@y.z --role operator     # crear
sudo snapctl admin set-role --email x@y.z --role admin      # cambiar rol
sudo snapctl admin disable --email x@y.z                    # deshabilitar
sudo snapctl admin enable --email x@y.z                     # rehabilitar
sudo snapctl admin revoke-sessions --email x@y.z            # cerrar sesiones
sudo snapctl admin reset-mfa --email x@y.z                  # quitar TOTP
```

### MFA — perdiste el dispositivo

1. En el prompt de login, ingresá uno de tus **10 backup codes** en
   lugar del código TOTP. Es un solo uso.
2. Una vez dentro, andá a la pestaña Usuarios y hacé "Reset MFA" sobre
   tu propia cuenta — te pedirá enrollar de nuevo.
3. Si perdiste también los backup codes, otro admin (o vos vía
   `sudo snapctl admin reset-mfa`) los puede limpiar.

### Política de contraseñas

- Mínimo **12 caracteres**.
- Score zxcvbn ≥ 3 (rechaza passwords típicas como `password123`).
- No puede contener tu email ni tu nombre.
- No puede ser igual a tus **últimas 5 contraseñas**.

### TLS y exposición externa

El backend escucha en `0.0.0.0:5070` **sin TLS**. Para exponer al
público, **siempre detrás de un reverse proxy con TLS** (Caddy,
nginx, traefik). Sin TLS, el cookie `Secure` no viaja y el login no
funciona desde un navegador remoto.

Ejemplo Caddy:

```
panel.tu-dominio.com {
  reverse_proxy 127.0.0.1:5070
}
```

### Audit log

Cada evento de auth (login OK/fail, MFA, password change, role change,
account_disable, etc.) se registra en la tabla `audit_auth` de la
SQLite del backend. Consulta rápida:

```bash
sudo sqlite3 /var/lib/snapshot-v3/snapshot.db \
  "SELECT created_at,actor,event,email,ip FROM audit_auth ORDER BY id DESC LIMIT 50"
```

### Sesiones

- TTL **8 horas** con sliding refresh: cada request extiende la sesión
  si quedan menos de 2h por vencer.
- **Idle timeout 1 hora**: si no hacés ningún request por más de 60
  minutos, te toca volver a loguear.
- Logout revoca la sesión inmediatamente (no es solo borrar la cookie
  del browser).

### Rate limiting

- Login: **10 intentos por minuto por IP**, **5 fallos consecutivos**
  por cuenta antes del lockout.
- Lockout exponencial: 15 min × 2^lock_count, máximo 24 h.
- Reset request: 3/min por IP.

### SECRET_KEY

`/etc/snapshot-v3/snapshot.local.conf` contiene `SECRET_KEY="..."`
(64 hex chars). Cifra los TOTP secrets en la base. **Si la perdés, los
TOTP enrollados quedan inválidos** y los usuarios deben re-enrollar
con sus backup codes (o `snapctl admin reset-mfa`).
`install.sh` la genera automáticamente; backupéala junto al resto
del archivo de config local.

## Modo central (deploy dual)

snapshot-V3 soporta dos modos de despliegue:

- **`MODE=client`** (default): el deploy tradicional. Corre el `archive`
  mensual, sube a Drive, no recibe nada de otros hosts.
- **`MODE=central`**: subdominio agregador. **No** corre `archive`, pero
  **sí** acepta heartbeats de otros hosts vía `POST /api/v1/heartbeat`,
  y muestra un dashboard agregado por proyecto/cliente.

### Arquitectura

```
┌──────────────┐  POST /api/v1/heartbeat   ┌────────────────────┐
│  Host cliente │ ─────────────────────────▶│  central.dominio   │
│  MODE=client  │  Bearer <token>           │  MODE=central      │
│  + central.sh │                           │  + dashboard agreg │
└──────────────┘                           └────────────────────┘
       │                                              │
       │ tar│zstd│rclone (archive mensual a Drive)    │
       ▼                                              │
┌──────────────┐                                      │
│ Google Drive │ ◀────── audit / status agreg ────────┘
└──────────────┘
```

### Bootstrap del central

```bash
sudo bash install.sh --central
```

Esto:

1. Setea `MODE=central` en `/etc/snapshot-v3/snapshot.local.conf`.
2. Deshabilita `snapshot@archive.timer` (el central no respalda nada).
3. Reinicia `snapshot-backend` que ahora registra los blueprints
   `central_api`, `central_admin` y `central_dashboard`.
4. Crea un cliente `demo` + token inicial e imprime el plaintext
   **una sola vez**. Anotalo.
5. Imprime el snippet de Caddy para `central.tu-dominio.com`.

### Enrolar un cliente existente al central

En el host cliente, edita `/etc/snapshot-v3/snapshot.local.conf`:

```bash
CENTRAL_URL="https://central.tu-dominio.com"
CENTRAL_TOKEN="<token plaintext que copiaste del central>"
```

`sudo systemctl restart snapshot-backend` (o reboot). Desde ese momento,
cada `snapctl archive` (éxito o fallo) emite un heartbeat al central. La
cola local en `central_queue` retiene los heartbeats hasta confirmar
entrega — backoff exponencial (1m, 5m, 15m, 1h, 6h, 24h) y máximo 20
intentos antes de marcarlo `dead`. El healthcheck (cada 15min) drena
la cola. CLI manual: `sudo snapctl central drain-queue`.

### Gestión de tokens desde la UI

Login al central como `admin` u `operator` → `/dashboard-central/clients`:

- **Crear cliente** (proyecto + organización) — solo admin/operator.
- **Emitir token** desde `/dashboard-central/clients/<id>/tokens` — el
  plaintext aparece una vez en banner amber, página recarga a los 30s.
- **Revocar token** — botón rojo en cada fila. El cliente que usaba ese
  token deja de poder reportar inmediatamente.

### Roles en el central

| Rol     | Alias UI    | Permisos central |
|---------|-------------|------------------|
| admin   | webmaster   | Todo (CRUD clients/tokens, gestionar usuarios, settings) |
| operator| técnico     | CRUD clients, emitir/revocar tokens, configurar alertas |
| auditor | gerente     | Solo lectura (dashboard, audit, ver clients y tokens) |

Matriz completa en `backend/central/permissions.py`.

### TLS al central

Igual que el cliente: detrás de reverse proxy. Snippet Caddy mínimo:

```
central.tu-dominio.com {
  reverse_proxy 127.0.0.1:5070
}
```

### Limitaciones conocidas

- **Sin permisos por proyecto** — un `auditor` ve TODOS los clientes.
  Si necesitás aislamiento por cuenta, usá deploys central separados.
- **Cola local sin tope temporal automático** — un host desconectado
  durante meses puede acumular muchos heartbeats. `dead` los excluye
  del retry pero los conserva en SQLite. Limpiar manualmente si es
  necesario.
- **Heartbeats sin firma adicional** — autenticación es por bearer
  token. Robar el token = poder reportar como ese cliente. Mitigación:
  rotación regular de tokens vía la UI.

### Alertas (modo central)

El central detecta automáticamente tres condiciones y notifica:

| Tipo | Disparo | Resolución |
|---|---|---|
| `no_heartbeat` | target sin reportar > `ALERTS_NO_HEARTBEAT_HOURS` (default 48h) | auto al siguiente heartbeat OK |
| `folder_missing` | heartbeat reporta `host_meta.missing_paths` no vacío | auto al siguiente heartbeat sin paths faltantes |
| `backup_shrink` | totals cae > `ALERTS_SHRINK_PCT`% (default 20%) entre heartbeats | manual (admin clickea "Acknowledge") |

**Configuración** en `/etc/snapshot-v3/snapshot.local.conf`:

```bash
ALERTS_NO_HEARTBEAT_HOURS="48"
ALERTS_SHRINK_PCT="20"
ALERTS_EMAIL=""              # vacío = no enviar email
ALERTS_WEBHOOK=""            # POST JSON para Slack/Discord/etc
```

**UI:** `/dashboard-central/alerts` muestra activas + histórico. Banner
rojo en el header cuando hay alertas críticas activas.

**Notificación:** email (vía SMTP de `snapshot.local.conf`) + webhook
opcional. Falla silenciosa si SMTP/webhook no configurado o caído.

**Sweep `no_heartbeat`:** ejecuta cada 15 min vía
`snapshot-healthcheck.timer` → `snapctl central alerts-sweep`.

**Severidad automática:**
- `no_heartbeat` → critical si pasaron >7 días, warning entre 48h-7d.
- `backup_shrink` → critical si shrink >50%, warning entre 20-50%.
- `folder_missing` → siempre warning.

## Backups de bases de datos

`snapctl db-archive` respalda Postgres / MySQL / MongoDB en streaming
directo a Drive con la misma taxonomía que el archive de FS pero en
sub-carpeta `db/<engine>/<dbname>/`:

```
PROYECTO/ENTORNO/PAIS/db/postgres/mydb/2026/04/27/
    servidor_mydb_20260427_030010.sql.zst[.enc]
```

### Engines soportados

| Engine | Tool requerido | Comando dump |
|---|---|---|
| postgres | `pg_dump` | `pg_dump --no-owner --no-acl --quote-all-identifiers <db>` |
| mysql / mariadb | `mysqldump` | `mysqldump --single-transaction --quick --routines --triggers --events <db>` |
| mongo | `mongodump` | `mongodump --uri=$DB_MONGO_URI --archive --db=<db>` |

snapshot-V3 **no instala** las herramientas — el operador las instala
con `apt install postgresql-client mysql-client mongodb-database-tools`.
Si el binario no está, ese target se salta con un warning + heartbeat
fail; los demás targets continúan.

### Configuración

En `/etc/snapshot-v3/snapshot.local.conf`:

```bash
DB_BACKUP_TARGETS="postgres:mydb postgres:other mysql:web mongo:metrics"

DB_PG_HOST="localhost"; DB_PG_USER="postgres"; DB_PG_PASSWORD="..."
DB_MYSQL_HOST="localhost"; DB_MYSQL_USER="root"; DB_MYSQL_PASSWORD="..."
DB_MONGO_URI="mongodb://user:pass@localhost:27017"
```

### Schedule

Default: diario 03:00 UTC vía `snapshot@db-archive.timer`. Editable
desde el panel (Programación) — `db-archive` está en
`SUPPORTED_UNITS` del scheduler. `install.sh` activa el timer **solo
si** `DB_BACKUP_TARGETS` no está vacío al momento del install.

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
se cifran con AES-256-CBC + PBKDF2 100k antes de subir. **Mismo trade-off
que el archive de FS**: si la rotás, los dumps viejos siguen necesitando
la password vieja para descifrarlos.

### Heartbeat al central (sub-B)

Cada target emite un heartbeat al central con `target.category="db"`,
`subkey=<engine>`, `label=<dbname>`. El dashboard agregado los muestra
junto a los OS targets. Las alertas (sub-D) — `no_heartbeat`,
`backup_shrink` — aplican igual a DB targets.

## CLI (`snapctl`)

Subcomandos del flujo archive (producción):

```bash
snapctl archive                # genera .tar.zst[.enc] y lo sube a Drive ahora
snapctl archive-list           # lista archives en Drive
snapctl archive-restore <path> --target /ruta
snapctl archive-prune          # aplica retención de archives
snapctl archive-paths          # imprime BACKUP_PATHS efectivos
snapctl status [--json] [--fast]
snapctl logs --lines 200
```

Subcomandos del flujo restic (legacy, requiere reactivar timers):

```bash
snapctl init                   # inicializa repo restic local + Drive
snapctl create [--tag manual]  # snapshot incremental (dual-repo)
snapctl reconcile              # copia local → Drive
snapctl list [--json]
snapctl show <id>
snapctl restore <id> --target /ruta [--include /etc/nginx]
snapctl delete <id>
snapctl prune                  # forget según KEEP_DAILY/WEEKLY/MONTHLY/YEARLY
snapctl check                  # restic check (integridad)
snapctl unlock                 # libera locks stale
```

Comandos de gestión de Drive:

```bash
snapctl drive-status [--json]
snapctl drive-link <token.json> [team_drive_id]
snapctl drive-unlink
snapctl drive-shared-list
snapctl drive-target personal | shared <id>
```

Las operaciones invocadas vía API se **auditan en SQLite** (tabla `jobs`)
y todas producen **logs JSON line** en `/var/log/snapshot-v3/snapctl.log`.

## API REST (`:5070/api`)

Formato uniforme: `{ "ok": true, "data": { ... }, "error": null }`

### Sistema

| Método | Endpoint                | Descripción                          |
|--------|-------------------------|--------------------------------------|
| GET    | /api/health             | Healthcheck                          |
| GET    | /api/status             | Estado del sistema (`--fast` por defecto) |
| GET    | /api/logs?lines=N       | Tail de backend.log                  |
| GET    | /api/jobs               | Histórico de operaciones (SQLite)    |
| GET    | /api/jobs/{id}          | Detalle de job                       |
| GET/POST | /api/config           | `BACKUP_PATHS`, `RCLONE_REMOTE_PATH` |

### Archive (cold-storage mensual)

| Método | Endpoint                  | Descripción                        |
|--------|---------------------------|------------------------------------|
| GET    | /api/archive/config       | Taxonomía actual                   |
| POST   | /api/archive/config       | Setea taxonomía                    |
| GET    | /api/archive/list?force=1 | Lista archives en Drive            |
| GET    | /api/archive/summary      | Agregados (count, size, last)      |
| POST   | /api/archive/create       | Genera archive ahora               |
| POST   | /api/archive/restore      | Descarga + descomprime             |
| POST   | /api/archive/delete       | Elimina archive de Drive           |
| POST   | /api/archive/password     | Setea password de cifrado          |
| DELETE | /api/archive/password     | Limpia password                    |

### Drive / OAuth

| Método | Endpoint                          | Descripción                        |
|--------|-----------------------------------|------------------------------------|
| GET    | /api/drive/status                 | Estado de vinculación              |
| POST   | /api/drive/link                   | Vincula con token JSON             |
| POST   | /api/drive/unlink                 | Desvincula                         |
| GET    | /api/drive/shared                 | Lista Shared Drives disponibles    |
| POST   | /api/drive/target                 | Cambia destino (personal/shared)   |
| POST   | /api/drive/oauth/device/start     | Inicia Device Flow → user_code     |
| POST   | /api/drive/oauth/device/poll      | Polling hasta token completo       |

### Restic (legacy, solo si reactivado)

| Método | Endpoint              | Descripción                              |
|--------|-----------------------|------------------------------------------|
| GET    | /api/snapshots        | Lista snapshots                          |
| POST   | /api/snapshots        | Crea snapshot `{tag?}`                   |
| DELETE | /api/snapshots/{id}   | Elimina                                  |
| POST   | /api/restore          | `{id, target, include?}`                 |
| POST   | /api/prune            | Retención                                |
| POST   | /api/check            | `restic check`                           |

## Frontend

- **Dashboard** (`/`): KPIs (archives totales, último hace cuánto, espacio
  usado, próximo timer), botón "Generar archivo ahora", jobs recientes.
  Auto-refresh cada 30s con **visibility-aware pause** (no consume CPU si
  la pestaña está oculta).
- **Archivos** (`/snapshots`): tabla mensual de archives con restore/delete.
- **Ajustes** (`/settings`): taxonomía, password, paths a respaldar,
  Vincular/Desvincular Drive, selección personal vs Shared Drive.
- **Logs** (`/logs`): viewer tipo consola con coloreado por nivel.
- **Auditoría** (`/audit`, opcional): vista agregada multi-host —
  requiere `AUDIT_ENABLED=1` y que cada host publique su `_status/`.

Tailwind vía CDN — no requiere build step.

## Automatización (timers systemd)

Por defecto tras `install.sh` quedan **dos timers activos**:

```
snapshot@archive.timer        # día 1 del mes a las 02:00 UTC ±1h jitter
snapshot-healthcheck.timer    # cada 15 min
```

Drop-ins de `archive`:

- `snapshot@archive.timer.d/override.conf` → `OnCalendar=*-*-01 02:00:00`,
  `RandomizedDelaySec=1h`.
- `snapshot@archive.service.d/override.conf` → `TimeoutStartSec=infinity`,
  `Nice=15`, `IOSchedulingClass=idle` (los archives pueden tardar horas
  y no deben competir con la carga normal del servidor).

Timers restic legacy (`create`, `sync`, `prune`, `reconcile`, `check`):
**desactivados por defecto**. `install.sh` los marca `disable --now`.
Para reactivar, ej. backup diario incremental:

```bash
sudo systemctl enable --now snapshot@create.timer
sudo systemctl enable --now snapshot@reconcile.timer
sudo systemctl enable --now snapshot@prune.timer
```

### Editar horarios desde la UI

La pantalla de **Programación** invoca `/api/schedule/<unit>`, que escribe
un drop-in en `/etc/systemd/system/snapshot@<unit>.timer.d/override.conf`,
hace `daemon-reload` y `enable/restart`. Validación previa con
`systemd-analyze calendar`.

> **Limitación actual:** la UI solo permite editar units de la lista
> `SUPPORTED_UNITS` en `backend/services/scheduler.py`, que hoy contiene
> `{"create", "prune"}` (heredado del flujo restic legacy). El timer del
> **archive** mensual — el único realmente activo por defecto — no se
> puede modificar desde la UI todavía. Para cambiar su horario, editá:
> `/etc/systemd/system/snapshot@archive.timer.d/override.conf`.

## Healthcheck, logs y notificaciones

- **`snapshot-healthcheck.service`** corre cada 15 min: invoca
  `snapctl status --json > /var/log/snapshot-v3/health.json` y valida
  `GET /api/health`.
- **Logs estructurados JSON lines** en `/var/log/snapshot-v3/snapctl.log`
  y `/var/log/snapshot-v3/backend.log` (rotación 5MB × 5).
- **Notificaciones** (opcional): `NOTIFY_EMAIL`, `SMTP_*` en
  `snapshot.local.conf` → `snapctl-notify` envía email en cada
  `archive` exitoso/fallido. `NOTIFY_WEBHOOK` para integraciones
  custom.
- **Auditoría cross-host** (opcional, `AUDIT_ENABLED=1`): cada host
  publica `_status/<hostname>.json` a Drive al terminar cada operación;
  el endpoint `/audit` agrega el estado del fleet (último OK, totals,
  health: ok|fail|silent|unknown|running, threshold de silencio 36h).

## Tuning rclone (perfiles automáticos)

`common.sh` detecta si el remoto Drive es personal o Shared Drive
(presencia de `team_drive` en `rclone.conf`) y aplica perfiles:

| Perfil   | TRANSFERS | CHECKERS | TPS_LIMIT | Razón                              |
|----------|-----------|----------|-----------|------------------------------------|
| Personal | 2         | 4        | 5         | Drive personal: 403 sobre ~10 qps |
| Shared   | 6         | 12       | 20        | Shared Drives: aguantan ~100 qps  |

Override manual: `RCLONE_PROFILE=personal|shared|auto` en `snapshot.local.conf`.

Otros parámetros fijos (no profile-sensitive) en `snapshot.conf`:
`RCLONE_DRIVE_CHUNK_SIZE=64M`, `RCLONE_TIMEOUT=300s`, `RCLONE_RETRIES=5`,
`RCLONE_BWLIMIT=""` (sin límite por defecto).

## Seguridad y buenas prácticas

- Password restic generado con `openssl rand -base64 48` (umask 077).
- Password de archive nunca se devuelve por la API — `password_set: bool`
  es la única señal. **Backupéala fuera del sistema.**
- Validación estricta de IDs y paths en la API (regex hex 8-64,
  prohibición de `..`, paths absolutos).
- `NoNewPrivileges`, `ProtectSystem=full`, `ReadWritePaths` explícito en
  la unit del backend. `/etc/systemd/system` está en `ReadWritePaths`
  porque la UI escribe drop-ins de timers en vivo — trade-off consciente
  para permitir edición de horarios desde la web.
- Separación clara core/backend/frontend: la lógica vive sólo en `snapctl`.
  El backend **nunca** ejecuta restic/rclone directamente — siempre via
  `snapctl`. Esto garantiza paridad CLI/UI.

## Puertos y red

| Servicio   | Puerto | Bind        |
|------------|--------|-------------|
| API + UI   | 5070   | 0.0.0.0     |
| (reservado)| 5071   | (libre)     |

Para acceso remoto: SSH port-forward o reverse proxy con TLS propio. El
backend no implementa autenticación — se asume red privada o tunelado.

## Upgrade ante CVE en binarios bundled

Para subir Python, restic o rclone (ej. ante un CVE):

```bash
# Editar las 4 líneas de cabecera en install.sh:
#   PYTHON_VERSION, PYTHON_PBS_DATE, RESTIC_VERSION, RCLONE_VERSION
sudo bash install.sh -y
sudo systemctl restart snapshot-backend
```

El instalador detecta mismatch de versión y baja **solo** el binario
afectado (idempotente). Override puntual sin commit:

```bash
sudo RESTIC_VERSION=0.17.4 bash install.sh -y
```

## Limitaciones conocidas

- **Linux-only.** El código asume paths POSIX, Bash, systemd. Para
  desplegar en Windows: WSL2 (recomendado) o respaldar la DB Windows
  por red desde un host Linux.
- **UI Programación incompleta:** no permite editar el timer del
  archive mensual; solo units restic legacy (ver más arriba).
- **Cache TTL del listado de archives no se invalida en error** —
  si `rclone lsjson` falla, la UI muestra "sin archivos" durante 60s.
- **Timeout fijo de 3600s** para todas las llamadas backend → snapctl;
  insuficiente para `estimate` en repos restic enormes.
- **Single worker gunicorn** (TTL cache in-process). Escalado horizontal
  requeriría mover la cache a Redis.
