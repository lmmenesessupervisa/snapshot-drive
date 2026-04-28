# Configuración — snapshot-V3

## Archivos de configuración

```
/opt/snapshot-V3/core/etc/snapshot.conf      # base, viene con el repo
/etc/snapshot-v3/snapshot.local.conf         # overrides por instalación, mode 0600
                                             # SOBREVIVE a re-deploys
/var/lib/snapshot-v3/rclone.conf             # rclone (token Drive), mode 0600
/var/lib/snapshot-v3/.restic-pass            # password del repo restic local, mode 0600
/var/lib/snapshot-v3/.secret_key             # fallback master key, mode 0600
```

`common.sh` lee primero `snapshot.conf` y luego `snapshot.local.conf`
(que sobreescribe). Mismo contrato en Python: `backend/config.py` lee
ambos y aplica el local sobre el global.

## Variables más importantes (en `snapshot.local.conf`)

### Identidad de la instalación

```bash
# OAuth Client del Device Flow (creado en GCP Console).
GOOGLE_CLIENT_ID=""
GOOGLE_CLIENT_SECRET=""

# Master key para encriptar MFA secrets + derivar Flask SECRET_KEY (HKDF).
# 64 hex chars. Si vacía, install.sh la genera.
SECRET_KEY=""
```

### Modo de despliegue (sub-B)

```bash
# Default: client. Pasá a "central" para activar el receptor de heartbeats.
MODE="client"

# Solo si MODE=client: dónde mandar heartbeats.
CENTRAL_URL="https://central.miorg.local"
CENTRAL_TOKEN="<bearer token emitido por el central>"
CENTRAL_TIMEOUT_S="5"
```

### Backup mensual / archive

```bash
# Taxonomía (también editable desde la UI). Determina la ruta remota:
# BACKUP_PROYECTO/BACKUP_ENTORNO/BACKUP_PAIS/os/linux/BACKUP_NOMBRE/YYYY/MM/DD/...
BACKUP_PROYECTO="superaccess-uno"   # superaccess-uno|superaccess-dos|basculas|proyectos-especiales|orus
BACKUP_ENTORNO="cloud"              # cloud|local
BACKUP_PAIS="colombia"              # colombia|peru|costa-rica|panama
BACKUP_NOMBRE=""                    # vacío = $(hostname -s)

ARCHIVE_KEEP_MONTHS="12"            # retención del prune

# Encriptación clásica con password (openssl AES-256-CBC + PBKDF2).
# Si está seteada Y no hay ARCHIVE_AGE_RECIPIENTS, los archivos suben con .enc.
ARCHIVE_PASSWORD=""
```

### Cifrado age (sub-F, opt-in, recomendado)

```bash
# Recipients públicos, separados por espacios. Si está seteado, age
# tiene precedencia sobre ARCHIVE_PASSWORD para los backups nuevos.
ARCHIVE_AGE_RECIPIENTS="age1abc... age1escrow..."

# Path al archivo de identidad (privada). Solo se usa al restaurar.
# Mode 0600. NO debe estar seteado en hosts de producción — solo
# temporalmente cuando vas a restaurar un .age.
ARCHIVE_AGE_IDENTITY_FILE=""
```

### Backups de bases de datos (sub-E)

```bash
# Lista space-separated de "engine:dbname". Vacío = no DB backups
# (el timer @db-archive no se activa).
DB_BACKUP_TARGETS="postgres:mydb mysql:web mongo:metrics"

# PostgreSQL — host vacío = socket Unix
DB_PG_HOST=""
DB_PG_PORT="5432"
DB_PG_USER="postgres"
DB_PG_PASSWORD=""

# MySQL / MariaDB
DB_MYSQL_HOST="localhost"
DB_MYSQL_PORT="3306"
DB_MYSQL_USER="backup"
DB_MYSQL_PASSWORD=""

# MongoDB — URI completo con auth
DB_MONGO_URI="mongodb://user:pass@host:27017/?authSource=admin"
```

### Notificaciones SMTP (panel del cliente)

```bash
# snapctl manda email tras cada operación si SMTP_HOST está completo.
# Para Gmail: smtp.gmail.com / 587 / app-password (16 chars).
SMTP_HOST=""
SMTP_PORT="587"
SMTP_USER=""
SMTP_PASSWORD=""
SMTP_FROM=""
NOTIFY_EMAIL=""
NOTIFY_WEBHOOK=""
```

### Vista de auditoría agregada

```bash
# Solo activala en la instalación ops (NO en hosts de cliente).
SNAPSHOT_AUDIT_VIEWER="1"
# Carpeta raíz del shared Drive con subfolders por host.
AUDIT_REMOTE_PATH="snapshots"
```

> Nota: `AUDIT_PASSWORD` ya **no existe** — `/audit` ahora usa el login
> normal del panel + rol `admin` o `auditor`.

### Alertas (solo en `MODE=central`, sub-D)

```bash
ALERTS_NO_HEARTBEAT_HOURS="48"   # entre 1 y 720
ALERTS_SHRINK_PCT="20"           # entre 1 y 99
ALERTS_EMAIL=""                  # vacío = sin email; requiere SMTP arriba
ALERTS_WEBHOOK=""                # POST JSON al disparar/resolver
```

### Tuning rclone (opcional)

```bash
RCLONE_PROFILE="auto"        # auto|personal|shared
RCLONE_TPSLIMIT="30"
RCLONE_TRANSFERS="8"
RCLONE_BWLIMIT="8M"          # vacío = sin límite
```

## Variables de entorno (override de cualquier valor de local.conf)

Tienen precedencia sobre `local.conf`. Útil para CI/tests.

| Env var | Equivalente |
|---|---|
| `MODE` | `MODE` |
| `CENTRAL_URL`, `CENTRAL_TOKEN`, `CENTRAL_TIMEOUT_S` | mismos |
| `GOOGLE_CLIENT_ID` / `_SECRET` / `_OAUTH_SCOPE` | mismos |
| `SNAPSHOT_SECRET_KEY` (hex) | `SECRET_KEY` |
| `SNAPSHOT_DB_PATH` | path del SQLite (tests) |
| `SNAPCTL_TIMEOUT` | timeout subprocess (sec) |
| `API_HOST`, `API_PORT` | bind del backend |
| `LOCAL_CONF` | path al local.conf (default `/etc/snapshot-v3/snapshot.local.conf`) |
| `CONF_FILE` | path al snapshot.conf base |
| `SNAPSHOT_TEST_MODE=1` | desactiva logging a archivo |

## Archivos asociados

| Archivo | Mode | Owner | Para qué |
|---|---|---|---|
| `/etc/snapshot-v3/snapshot.local.conf` | 0600 | root | Override por instalación (creds, modos) |
| `/var/lib/snapshot-v3/snapshot.db` | 0600 | root | Estado del panel (users, sessions, events) |
| `/var/lib/snapshot-v3/snapshot.db-wal` | 0600 | root | WAL log de SQLite |
| `/var/lib/snapshot-v3/rclone.conf` | 0600 | root | OAuth tokens Drive |
| `/var/lib/snapshot-v3/.restic-pass` | 0600 | root | Password del repo restic |
| `/var/lib/snapshot-v3/.secret_key` | 0600 | root | Fallback master (solo si `SECRET_KEY` vacío) |
| `/var/log/snapshot-v3/snapctl.log` | 0640 | root | Log JSON-lines del CLI |
| `/var/log/snapshot-v3/backend.log` | 0640 | root | Log del Flask (rotado, 5 MB × 5) |
| `/opt/snapshot-V3/bundle/bin/{rclone,restic,age,age-keygen}` | 0755 | root | Binarios pinneados (vienen con el repo) |

## Cambios de configuración: ¿requieren restart?

| Cambio | Requiere restart `snapshot-backend`? |
|---|---|
| Taxonomía (`/api/archive/config`) | **No** — `snapctl` lee `local.conf` fresh |
| `ARCHIVE_PASSWORD` o `ARCHIVE_AGE_RECIPIENTS` | **No** — bash crypto helper relee |
| `DB_BACKUP_TARGETS` y creds DB | **No** |
| `MODE` (cliente↔central) | **Sí** — los blueprints se registran al startup |
| `ALERTS_*` | **No** — el set_alerts_config también actualiza `Config.*` en memoria |
| `SECRET_KEY` master | **Sí** — se deriva la Flask session key al startup |
| `BACKUP_PATHS` (paths a respaldar) | **No** — leído fresh por `snapctl create` |
| `SMTP_*`, `NOTIFY_*` | **Sí** para los del backend; **No** para los del CLI |

## Healthcheck

`systemd-timer` cada 15min ejecuta `snapshot-healthcheck.service` que:

1. Verifica que `snapshot-backend.service` esté `active`
2. En `MODE=client`: ejecuta `snapctl central drain-queue` (drena heartbeats encolados offline)
3. En `MODE=central`: ejecuta `snapctl central alerts-sweep` (corre las reglas de detección)
