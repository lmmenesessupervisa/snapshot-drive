# snapshot-V3

Sistema modular de gestión de snapshots (backups) tipo DigitalOcean, inspirado en un
producto real. Se compone de **tres capas independientes** que comparten la misma
fuente de verdad (el CLI `snapctl`):

```
┌──────────────────────────────────────────────────────────────────┐
│  FRONTEND  (Tailwind CDN)       ← Dashboard, gestión, logs      │
│  BACKEND   (Flask + SQLite)     ← API REST en :5070             │
│  CORE      (Bash + Restic +     ← CLI `snapctl`, lógica real    │
│             Rclone + systemd)                                    │
└──────────────────────────────────────────────────────────────────┘
```

## Requisitos de aislamiento respecto al stack host

- Corre en puertos **no estándar** (`5070` API, `5071` reservado front opcional).
- **No usa** Nginx/Apache — Flask/Gunicorn sirven UI + API en el mismo puerto.
- **No usa** Docker.
- No interfiere con PostgreSQL, Oracle, Laravel, WebSockets del host.
- Estado y datos aislados en `/var/lib/snapshot-v3/` y `/var/log/snapshot-v3/`.
- Se ejecuta como servicios **systemd** dedicados (`snapshot-backend.service` y
  timers `snapshot@*.timer`).

## Estructura

```
snapshot-V3/
├── core/
│   ├── bin/snapctl            # CLI central (única fuente de verdad)
│   ├── lib/common.sh          # logging JSON, validaciones, notify
│   └── etc/
│       ├── snapshot.conf      # configuración global
│       └── excludes.list      # exclusiones restic
├── backend/
│   ├── app.py                 # Flask factory + arranque
│   ├── config.py
│   ├── routes/                # api.py (REST), web.py (HTML)
│   ├── services/snapctl.py    # wrapper tipado alrededor del CLI
│   ├── models/db.py           # SQLite (jobs, audit)
│   └── requirements.txt
├── frontend/
│   ├── templates/             # base, index, snapshots, logs
│   └── static/{css,js}/       # Tailwind CDN + componentes propios
├── systemd/
│   ├── snapshot-backend.service
│   ├── snapshot@.service      # instanciable: create|sync|prune|check
│   ├── snapshot@.timer        # timers diarios con RandomizedDelaySec
│   ├── snapshot-healthcheck.service
│   └── snapshot-healthcheck.timer
├── logs/
├── install.sh
└── README.md
```

## Instalación (Ubuntu Server)

```bash
sudo bash install.sh
```

El instalador:

1. Instala `restic`, `rclone`, Python 3 venv, utilidades.
2. Copia el proyecto a `/opt/snapshot-V3/`.
3. Crea venv en `.venv/` y resuelve dependencias.
4. Inicializa el repositorio restic local.
5. Instala/activa servicios y timers systemd.
6. Publica `snapctl` en `/usr/local/bin/`.
7. Verifica `GET /api/health`.

### Credenciales locales (OAuth de Google Drive)

El repo **no contiene** las credenciales reales del OAuth Client. Tras
`install.sh`, edita el override local con los valores de tu Google Cloud Console:

```bash
sudo nano /opt/snapshot-V3/core/etc/snapshot.local.conf
# Rellena GOOGLE_CLIENT_ID y GOOGLE_CLIENT_SECRET
sudo systemctl restart snapshot-backend
```

`snapshot.local.conf` está en `.gitignore`, se crea con permisos `600` y
sobreescribe los valores de `snapshot.conf` al cargarse desde `common.sh`.
En upgrades (re-ejecutar `install.sh`) el instalador respeta el archivo
existente y no lo pisa.

## Configuración de Google Drive (rclone)

```bash
rclone config --config /var/lib/snapshot-v3/rclone.conf
# Crea un remoto llamado 'gdrive' (tipo drive). Luego:
snapctl sync
```

El nombre del remoto se ajusta en `core/etc/snapshot.conf` (`RCLONE_REMOTE`).

## CLI (`snapctl`)

```bash
snapctl init                 # inicializa repo restic
snapctl create --tag manual  # crea snapshot
snapctl list [--json]        # lista snapshots
snapctl show <id>            # detalle JSON
snapctl restore <id> --target /ruta [--include /etc/nginx]
snapctl delete <id>          # elimina snapshot
snapctl prune                # aplica políticas de retención
snapctl check                # restic check (integridad)
snapctl sync                 # rsync hacia Google Drive
snapctl status [--json]      # estado del sistema
snapctl logs --lines 200
```

Todas las operaciones son **auditadas en SQLite** cuando se invocan vía API, y
producen **logs JSON line** en `/var/log/snapshot-v3/snapctl.log`.

## API REST (`:5070/api`)

| Método | Endpoint              | Descripción                             |
|--------|-----------------------|-----------------------------------------|
| GET    | /api/snapshots        | Lista snapshots                         |
| POST   | /api/snapshots        | Crea snapshot `{tag?}`                  |
| DELETE | /api/snapshots/{id}   | Elimina snapshot                        |
| POST   | /api/restore          | `{id,target,include?}`                  |
| POST   | /api/prune            | Retención                               |
| POST   | /api/check            | Verificación integridad                 |
| POST   | /api/sync             | Sync hacia Google Drive                 |
| GET    | /api/status           | Estado sistema (JSON)                   |
| GET    | /api/logs?lines=N     | Logs recientes                          |
| GET    | /api/jobs             | Histórico de operaciones (SQLite)       |
| GET    | /api/jobs/{id}        | Detalle de job                          |
| GET    | /api/health           | Healthcheck                             |

Formato de respuesta:

```json
{ "ok": true, "data": { ... }, "error": null }
```

## Frontend

- **Dashboard** (`/`): KPIs (repo OK, count, tamaño, sync), último backup,
  acciones rápidas, jobs recientes.
- **Snapshots** (`/snapshots`): tabla con fecha, host, tags, paths, restaurar
  y eliminar. Modal de restore con `target`/`include`.
- **Logs** (`/logs`): viewer tipo consola con coloreado por nivel, auto-refresh.

Usa Tailwind vía CDN — no requiere build step.

## Automatización

Timers systemd instanciados:

```
snapshot@create.timer    # backup diario
snapshot@sync.timer      # sync a Drive diario
snapshot@prune.timer     # retención diaria
snapshot-healthcheck.timer  # cada 15 min
```

Override por instancia (ejemplo):

```bash
sudo systemctl edit snapshot@create.timer
# [Timer]
# OnCalendar=02:30
# RandomizedDelaySec=20min
```

## Healthcheck, logs y notificaciones

- `snapshot-healthcheck.service` escribe `/var/log/snapshot-v3/health.json`
  y valida `GET /api/health`.
- Logs estructurados JSON lines en `/var/log/snapshot-v3/`.
- `NOTIFY_EMAIL` y `NOTIFY_WEBHOOK` en `snapshot.conf` envían avisos en
  éxito/fallo de backup.

## Seguridad y buenas prácticas

- Password restic generado con `openssl rand -base64 48` (umask 077).
- Validación estricta de IDs y paths en la API (regex + prohibición de `..`).
- `NoNewPrivileges`, `ProtectSystem`, `ReadWritePaths` en la unidad backend.
- Separación clara core/backend/frontend: la lógica de negocio vive sólo en el
  CLI. El backend **nunca** ejecuta restic/rclone directamente — siempre via
  `snapctl`. Esto garantiza paridad total entre CLI y panel.

## Puertos y red

| Servicio    | Puerto  | Bind       |
|-------------|---------|------------|
| API + UI    | 5070    | 127.0.0.1  |
| (opcional)  | 5071    | (libre)    |

Para exponer externamente, usa SSH port-forward o un reverse proxy propio;
por defecto el backend escucha sólo en localhost.
