# DB Backups (sub-project E)

**Fecha:** 2026-04-27
**Estado:** propuesto
**Sub-proyecto:** E — independiente. Corre en `MODE=client` (igual que el archive de FS).

## Contexto

Sub-A (auth+RBAC) y sub-B (deploy dual + central) ya están en main.
Sub-D (alertas) está en su propio branch. Lo que falta del pedido
original es respaldar **bases de datos** que viven en los hosts cliente:
Postgres, MySQL y MongoDB.

El flujo de filesystem (`snapctl archive`) hoy hace
`tar | zstd | rclone rcat` directo a Drive. Para DBs queremos el mismo
patrón pero con `pg_dump`/`mysqldump`/`mongodump` como fuente del pipe.
La taxonomía Drive ya soporta sub-categoría `db/<engine>/<dbname>`
gracias a la convención `category/subkey/label` que sub-B usa en sus
modelos (ya hay un CHECK constraint `category IN ('os','db')` en la
tabla `targets`).

## Requisitos

### Funcionales

1. **`snapctl db-archive`** ejecuta un dump por cada target configurado
   en `DB_BACKUP_TARGETS` y lo sube en streaming a Drive.
2. **3 engines soportados**: PostgreSQL (`pg_dump`), MySQL/MariaDB
   (`mysqldump`) y MongoDB (`mongodump --archive`).
3. **Múltiples DBs por engine**: la config soporta `DB_BACKUP_TARGETS=
   "postgres:mydb postgres:other mysql:web mongo:metrics"`.
4. **Detección de herramientas**: si el binario del engine no está
   instalado, ese target se salta con un warning y un evento heartbeat
   con `status="fail"` (no rompe los demás targets).
5. **Cifrado**: si `ARCHIVE_PASSWORD` está seteada (la misma de sub-A),
   los dumps se cifran con AES-256-CBC + PBKDF2 100k antes de subir.
6. **Heartbeat por target** al central (sub-B) con
   `target.category="db"`, `subkey=<engine>`, `label=<dbname>`.
7. **Listado**: `snapctl db-archive-list` muestra los `.sql.zst[.enc]`
   y `.archive.zst[.enc]` agrupados por engine/db.
8. **Restore**: `snapctl db-archive-restore <remote_path>
   [--target conn-string]` baja + descomprime + pipea a
   `psql`/`mysql`/`mongorestore`.
9. **Schedule**: nuevo timer `snapshot@db-archive.timer` con default
   diario 03:00 UTC (DBs cambian más rápido que FS, que es mensual).
10. **Editable desde UI**: el fix de scheduler (T9 de sub-A) ya añade
    `archive` a `SUPPORTED_UNITS`; agregar `db-archive` a esa misma
    lista.

### No-funcionales

- **Streaming puro** sin disco temporal (mismo patrón que archive.sh).
  Razones: HDDs llenos en clientes con DBs grandes son comunes.
- **Idempotencia por target**: cada dump es independiente, una falla en
  postgres no aborta el dump de mysql.
- **Logs JSON line** a `snapctl.log` con `op="db-archive"`,
  `engine=<x>`, `db=<y>`, status, duration_s, size_bytes.
- **Auth opcional**: si `DB_*_HOST` está vacío, `pg_dump` etc. usan los
  defaults del sistema (socket Unix, `~/.pgpass`, etc.).
- **No tocamos paquetes apt**: `pg_dump`, `mysqldump`, `mongodump` se
  asumen presentes en el host (instalados por el operador antes de
  habilitar este target). `install.sh` chequea pero no instala.

### Out of scope

- **Backup logical incremental** (WAL shipping, binlog tail). Los dumps
  son full cada vez. Si en el futuro el dataset crece >100 GB y los
  dumps tardan horas, otra spec.
- **Dump de bases que no soportan dump-streaming** (Redis, ElasticSearch,
  ClickHouse, etc). Solo los 3 engines pedidos.
- **Restore granular** (solo una tabla). Restore es full o nothing.
- **Lock awareness avanzado** (pg_basebackup, mysql replicas). Usamos
  los flags estándar (`--single-transaction` para mysql,
  `--no-owner --no-acl` para pg, `--archive` para mongo).
- **DB encryption at rest** propio (TDE). Confiamos en `ARCHIVE_PASSWORD`
  + cifrado del Drive remoto.

## Arquitectura

```
       ┌── snapctl db-archive ─────────────────────────────┐
       │                                                   │
       │  parse DB_BACKUP_TARGETS  →  for each "engine:db":│
       │                                                   │
       │     ┌──────────────────────────────────────┐      │
       │     │ db_archive.sh::run_db_archive_target │      │
       │     │   1. validate_taxonomy               │      │
       │     │   2. resolve dump command per engine │      │
       │     │   3. dump | zstd -T0 | [openssl]     │      │
       │     │      | rclone rcat to taxonomic path │      │
       │     │   4. notify + write_status_drive     │      │
       │     │   5. central_send (heartbeat)        │      │
       │     └──────────────────────────────────────┘      │
       └───────────────────────────────────────────────────┘
                              │
                              ▼
            PROYECTO/ENTORNO/PAIS/db/<engine>/<dbname>/
                YYYY/MM/DD/servidor_<dbname>_<TS>.<ext>
```

`<ext>` por engine:
- `postgres` → `sql.zst[.enc]`
- `mysql`    → `sql.zst[.enc]`
- `mongo`    → `archive.zst[.enc]`

## Modelo de datos

**Sin cambios al schema**. La tabla `targets` ya tiene
`CHECK(category IN ('os','db'))` desde sub-B. Los heartbeats DB se
upsertan en la misma tabla con `category='db'` y `subkey=<engine>`,
`label=<dbname>`. El dashboard agregado los muestra junto a los OS
targets.

## Configuración (en `snapshot.local.conf`)

```bash
# Lista space-separated de "engine:dbname". Vacío = no DB backups.
# Engines válidos: postgres, mysql, mongo.
DB_BACKUP_TARGETS=""

# --- Postgres ---
# Si DB_PG_HOST está vacío, pg_dump usa el socket Unix por defecto.
DB_PG_HOST=""
DB_PG_PORT="5432"
DB_PG_USER=""
# Si vacía, pg_dump usa ~/.pgpass o trust auth.
DB_PG_PASSWORD=""

# --- MySQL / MariaDB ---
DB_MYSQL_HOST=""
DB_MYSQL_PORT="3306"
DB_MYSQL_USER=""
DB_MYSQL_PASSWORD=""

# --- MongoDB ---
# URI completo: mongodb://user:pass@host:27017/?authSource=admin
DB_MONGO_URI=""
```

## Comando dump por engine

| Engine | Comando |
|---|---|
| postgres | `pg_dump --no-owner --no-acl --format=plain --quote-all-identifiers <db>` |
| mysql    | `mysqldump --single-transaction --quick --skip-lock-tables --routines --triggers --events <db>` |
| mongo    | `mongodump --uri="$DB_MONGO_URI" --archive --db=<db>` |

Connection envs derivadas de la config (sin pasar password en argv):

```bash
# postgres:
PGHOST=$DB_PG_HOST PGPORT=$DB_PG_PORT PGUSER=$DB_PG_USER PGPASSWORD=$DB_PG_PASSWORD pg_dump ...

# mysql:
MYSQL_PWD=$DB_MYSQL_PASSWORD mysqldump -h$DB_MYSQL_HOST -P$DB_MYSQL_PORT -u$DB_MYSQL_USER ...

# mongo: URI ya contiene auth
mongodump --uri="$DB_MONGO_URI" ...
```

## Comando restore

`snapctl db-archive-restore <remote_path> [--target <conn>]`:

```
rclone cat REMOTE:<remote_path> | [openssl dec] | zstd -dc | <restore_cmd>
```

Donde `<restore_cmd>` se infiere del path (subcarpeta `postgres/`,
`mysql/`, `mongo/`):

| Engine | Restore |
|---|---|
| postgres | `psql <db>` (asume DB destino ya existe) |
| mysql    | `mysql <db>` |
| mongo    | `mongorestore --archive --uri="<uri>"` |

Si `--target` no se da, el comando imprime el comando que ejecutaría
y aborta — restore no debe ser accidental.

## Schedule

Drop-in en `systemd/snapshot@db-archive.timer.d/override.conf`:

```ini
[Timer]
OnCalendar=
OnCalendar=*-*-* 03:00:00
RandomizedDelaySec=20min
Persistent=true
```

Drop-in del service hereda de `snapshot@.service` con
`TimeoutStartSec=infinity` (DBs grandes pueden tardar).

`install.sh` activa el timer **solo si** `DB_BACKUP_TARGETS` está
seteado al momento del install. Si está vacío y luego el operador lo
configura, debe `systemctl enable --now snapshot@db-archive.timer` a
mano (documentado en README).

## UI Scheduler

`SUPPORTED_UNITS` (sub-A T9 fix) actualmente: `{"archive","create","prune"}`.
Sub-E agrega `"db-archive"` → la pantalla "Programación" lista el timer
y permite editar su `OnCalendar` desde el panel.

`_DEFAULT_ONCALENDAR` agrega `"db-archive": "*-*-* 03:00:00"`.

## Cambios al código existente

| Archivo | Cambio |
|---|---|
| `core/lib/db_archive.sh` (nuevo) | Pipeline + helpers per-engine |
| `core/bin/snapctl` | Subcomandos `db-archive`, `db-archive-list`, `db-archive-restore` |
| `core/etc/snapshot.local.conf.example` | Bloque `# --- DB backups ---` |
| `systemd/snapshot@db-archive.timer.d/override.conf` (nuevo) | OnCalendar diario |
| `systemd/snapshot@db-archive.service.d/override.conf` (nuevo) | TimeoutStartSec=infinity, Nice=15 |
| `install.sh` | Instalar drop-ins; activar timer si DB_BACKUP_TARGETS |
| `backend/services/scheduler.py` | + `"db-archive"` a `SUPPORTED_UNITS` y `_DEFAULT_ONCALENDAR` |
| `backend/services/archive_ops.py` | Helper `list_db_archives()` para la UI (separa db/* del os/*) |
| `backend/routes/api.py` | Endpoint `GET /api/db-archive/list` |
| `frontend/templates/snapshots.html` | Tab adicional "DB" para mostrar los dumps |
| `README.md` | Sección "Backups de bases de datos" |

## Plan de testing

- **Bash syntax** (`bash -n core/lib/db_archive.sh`).
- **Unit Python**: parse `DB_BACKUP_TARGETS` (split, validar engines,
  ignorar entradas mal formadas).
- **Unit per-engine cmd builder**: la función que construye el array
  argv para `pg_dump`/`mysqldump`/`mongodump` se puede testear en
  Python (paridad con la lógica bash mediante un helper Python ligero
  que el bash invoca para construir args). Alternativa: tests bash
  con `command -v pg_dump || skip`.
- **Integration smoke**: si `pg_dump --version` corre, ejecutar contra
  una DB sqlite-equivalente — no, mejor: integration tests con docker
  compose se deferred a CI futuro.
- **Restore validator**: descarga + descomprime + verifica que el
  output empieza con `--` (postgres SQL header) o `-- MySQL dump` o
  el magic byte de mongo archive (`0x80 0x09`).

## Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| `pg_dump` no instalado en el host | Detección al inicio; warning + heartbeat fail; otros engines siguen |
| Password expuesto en `ps aux` | Usar env vars, nunca argv |
| Dump muy largo bloquea el timer | `TimeoutStartSec=infinity` en el drop-in del service |
| Mongo dump muy grande sin compresión nativa | `--archive` ya streaminable; zstd lo comprime después |
| Restore accidental destruye prod | `--target` requerido; sin él imprime comando y aborta |
| Cifrado password rotado deja dumps inutilizables | Mismo trade-off que archive FS — documentar en README |

## Métricas de éxito

- Dump de Postgres 1 GB en <2 min con zstd -T0.
- 100% de los engines no instalados se saltan sin abortar.
- Heartbeat al central tiene category=db en cada dump (visible en el
  dashboard agregado).
- Editar el horario del db-archive timer desde la UI funciona end-to-end.

## Resumen en una frase

Sub-E agrega `snapctl db-archive` con engines Postgres/MySQL/Mongo en
streaming a Drive con la misma taxonomía + cifrado + heartbeat al
central que el archive de FS, controlado por `DB_BACKUP_TARGETS`,
schedulable diario por defecto desde la UI.
