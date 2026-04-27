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
