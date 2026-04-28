# Central Alerts (sub-project D)

**Fecha:** 2026-04-27
**Estado:** propuesto
**Sub-proyecto:** D — depende de B (central deploy + heartbeats), ya mergeado a `main`.

## Contexto

Tras mergear B (`MODE=central` con heartbeats), el central ya tiene
visibilidad cross-cliente: cada `MODE=client` reporta `archive` ok/fail.
Lo que falta es **alertar** cuando algo va mal:

- Un cliente deja de reportar (red caída, host down, snapctl roto).
- El cliente reporta archive OK pero faltan paths del filesystem.
- Los totales de un target caen abruptamente (alguien borró backups).

Estas tres detecciones cubren los pedidos originales del usuario:
"notifíque cuando borraron backups, alertas cuando no encuentre carpetas
de los clientes, backup no ejecutado".

Todo el código vive en el deploy `MODE=central`. En `MODE=client` las
tablas `central_alerts` simplemente quedan vacías (mismo patrón que las
otras tablas del central, ver schema en `backend/models/db.py`).

## Requisitos

### Funcionales

1. **Detección reactiva** al recibir un heartbeat: evaluar reglas
   `folder_missing` y `backup_shrink`.
2. **Detección periódica** cada 15min: evaluar regla `no_heartbeat`
   (sweep porque depende de tiempo desde último report).
3. **Estado persistente** — alertas activas viven en `central_alerts`
   con `triggered_at`, `last_seen_at`, `resolved_at`.
4. **Idempotencia** — una alerta `(client_id, target_id, type)` activa
   no genera duplicado; `last_seen_at` se actualiza en cada detección.
5. **Auto-resolución** para `no_heartbeat` y `folder_missing` cuando
   llega un heartbeat OK que satisface la condición opuesta.
6. **Acknowledge manual** para `backup_shrink` (admin clickea, marca
   `resolved_at`). El shrink siempre pide confirmación humana.
7. **Notificación al transicionar pending→active**:
   - Email al destinatario en `ALERTS_EMAIL` (o a todos los admins si
     vacío) si SMTP está configurado.
   - POST JSON a `ALERTS_WEBHOOK` (Slack/Discord/etc) si configurado.
   - Banner global en la UI del central.
8. **UI de gestión** — `/dashboard-central/alerts` con tabla activa +
   histórico, filtro por estado.
9. **API REST** bajo `/api/admin/alerts` (GET, acknowledge, config).
10. **CLI sweep** invocable desde root: `snapctl central alerts-sweep`
    (usado por el healthcheck timer).

### No-funcionales

- **Permission matrix**: ver = `central.dashboard:view`, configurar /
  acknowledge = `central.alerts:configure` (ya existe en sub-B).
- **No spam**: una vez disparada y notificada, la alerta no notifica de
  nuevo hasta resolverse y volver a dispararse.
- **Retención**: alertas resueltas se conservan 90 días; cron las
  purga (out of scope inicial — eliminación manual por ahora).
- **Costo SMTP**: usa el mismo `_send_reset_email` style del auth
  (best-effort, falla silenciosa).
- **Compat con `MODE=client`**: las funciones de detección no se
  ejecutan si `MODE!=central` — sub-D es funcionalmente no-op en
  cliente. La tabla se crea igual (consistencia de schema).

### Out of scope

- **Per-cliente notification routing** (cada cliente notifica a su
  propio email). Spec posterior si se vuelve necesario.
- **Reglas custom editables desde UI** (regex sobre detalles, etc).
  Hard-coded en código por ahora.
- **Escalación temporal** ("si una alerta sigue activa por 4 días,
  promover de warning a critical"). Severidad fija en el código.
- **Integración con PagerDuty / Opsgenie**. Webhook genérico cubre
  el 80% del caso.
- **Métricas Prometheus exportadas**. Solo SQLite + email + webhook.

## Arquitectura

```
                    ┌─────────────────────────────────────────────┐
                    │   backend/central/alerts.py                 │
                    │                                              │
   apply_heartbeat ─┼─► evaluate_heartbeat(payload, target)       │
                    │      │                                       │
                    │      ├─ folder_missing ─┐                    │
                    │      └─ backup_shrink   ├─► fire(type, …)   │
                    │                          │      │            │
   sweep timer ─────┼─► sweep_inactive() ──────┘      │            │
                    │                                  ▼            │
                    │                       ┌──────────────────┐   │
                    │                       │ central_alerts   │   │
                    │                       │ INSERT or UPDATE │   │
                    │                       └────────┬─────────┘   │
                    │                                │             │
                    │                       state changed?         │
                    │                                │             │
                    │                                ▼             │
                    │                       dispatch.notify()      │
                    │                          ├─ SMTP             │
                    │                          ├─ Webhook          │
                    │                          └─ (UI sees DB)     │
                    └─────────────────────────────────────────────┘
```

## Modelo de datos

Una tabla nueva, agregada al schema en `backend/models/db.py`
(idempotente por `CREATE TABLE IF NOT EXISTS`):

```sql
CREATE TABLE IF NOT EXISTS central_alerts (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  type          TEXT NOT NULL CHECK(type IN
                  ('no_heartbeat','folder_missing','backup_shrink')),
  client_id     INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  target_id     INTEGER REFERENCES targets(id) ON DELETE CASCADE,
  severity      TEXT NOT NULL DEFAULT 'warning'
                  CHECK(severity IN ('info','warning','critical')),
  triggered_at  TEXT NOT NULL,
  last_seen_at  TEXT NOT NULL,
  resolved_at   TEXT,                     -- NULL = activa
  notified_at   TEXT,                     -- NULL hasta primer envío
  detail_json   TEXT
);

-- "Solo una activa por (client, target, type)" se enforce por
-- transacción en el código (SQLite no permite índices únicos parciales
-- portables a versiones viejas; el índice abajo acelera el lookup).
CREATE INDEX IF NOT EXISTS idx_alerts_active_lookup
  ON central_alerts(client_id, target_id, type, resolved_at);

CREATE INDEX IF NOT EXISTS idx_alerts_triggered
  ON central_alerts(triggered_at DESC);
```

### Reglas de severity

- `no_heartbeat` → `critical` si pasaron >7 días, `warning` si está
  entre el threshold y 7 días.
- `folder_missing` → `warning`.
- `backup_shrink` → `critical` si shrink >50%, `warning` entre el
  threshold y 50%.

## Reglas de detección — definición precisa

### `no_heartbeat`

Sweep: para cada `(client_id, target_id)` en tabla `targets`:

```
if (now - last_heartbeat_ts) > ALERTS_NO_HEARTBEAT_HOURS:
    fire("no_heartbeat", client_id, target_id,
         severity = "critical" if hours > 7*24 else "warning",
         detail = {"hours_since": int(...), "last_ts": last_heartbeat_ts})
```

Auto-resolve: en `apply_heartbeat`, si llega un OK para ese
`(client, target)`, cualquier `no_heartbeat` activa se marca resolved.

### `folder_missing`

Reactiva: en `apply_heartbeat`, si `payload.host_meta.missing_paths`
es lista no vacía:

```
fire("folder_missing", client_id, target_id, severity="warning",
     detail = {"missing_paths": [...]})
```

Auto-resolve: el siguiente heartbeat con `missing_paths == []` (o
ausente) marca la alerta resolved.

### `backup_shrink`

Reactiva: en `apply_heartbeat`, comparar el `payload.totals.size_bytes`
nuevo contra el previo del target:

```
prev = SELECT totals.size_bytes FROM ... WHERE target_id=? ORDER BY
       received_at DESC LIMIT 1 OFFSET 1   -- el penúltimo
if prev and prev > 0:
    pct = 100 * (prev - new) / prev
    if pct >= ALERTS_SHRINK_PCT:
        fire("backup_shrink", client_id, target_id,
             severity = "critical" if pct >= 50 else "warning",
             detail = {"prev_bytes": prev, "new_bytes": new, "pct": pct})
```

Resolución: solo manual (`POST /api/admin/alerts/<id>/acknowledge`).
La idea es que un shrink pide intervención humana — nunca auto-resolve.

## API REST

Bajo `/api/admin/alerts` (gated por permisos del central):

| Método | Endpoint | Permiso | Descripción |
|---|---|---|---|
| GET | `/api/admin/alerts?active=1&limit=N` | `central.dashboard:view` | Lista alertas (default solo activas) |
| GET | `/api/admin/alerts/<id>` | `central.dashboard:view` | Detalle |
| POST | `/api/admin/alerts/<id>/acknowledge` | `central.alerts:configure` | Marca resolved manualmente |
| GET | `/api/admin/alerts/config` | `central.dashboard:view` | Lee thresholds + canales actuales |
| POST | `/api/admin/alerts/config` | `central.alerts:configure` | Persiste thresholds + canales en `snapshot.local.conf` |

## Configuración

En `snapshot.local.conf` (cargada vía `Config`):

```bash
# Threshold para "cliente sin reportar":
ALERTS_NO_HEARTBEAT_HOURS="48"

# Threshold para "shrink sospechoso":
ALERTS_SHRINK_PCT="20"

# Destinatario notificaciones — vacío = todos los users con role=admin:
ALERTS_EMAIL=""

# POST JSON opcional al disparar / resolver una alerta:
ALERTS_WEBHOOK=""
```

`Config.ALERTS_*` los expone como `int` / `str`. UI puede editarlos via
`POST /api/admin/alerts/config` que reescribe el archivo.

## UI

### Banner global (en `frontend/templates/base.html` central-only)

Cuando hay ≥1 alerta `critical` activa, en el header sticky aparece una
barra roja:

```
🚨 3 alertas críticas activas — [Ver alertas]
```

Implementación: nuevo `context_processor` que cuenta activas. Solo
inyecta el dato si `Config.MODE == "central"`.

### Página `/dashboard-central/alerts`

Tabla con columnas: tipo, cliente (link), target, severity, triggered_at,
last_seen_at, detalle (modal/expand), botón "Acknowledge" (si activa).

Filtro `?status=active|resolved|all` (default active).

## Notificación: dispatcher

`backend/central/alerts/dispatch.py`:

```python
def notify(alert: dict, client: dict, target: dict) -> None:
    """Best-effort. Falla silencioso. Marca notified_at en éxito o intento."""
    if Config.SMTP_HOST and (Config.ALERTS_EMAIL or _admin_emails(...)):
        send_email(...)
    if Config.ALERTS_WEBHOOK:
        post_webhook(...)
    UPDATE central_alerts SET notified_at=now() WHERE id=?
```

El email es HTML simple — reusa el patrón de `_send_reset_email` del
auth subsystem.

El webhook payload:

```json
{
  "event": "alert.fired",
  "alert": {
    "id": 42, "type": "no_heartbeat", "severity": "critical",
    "triggered_at": "2026-04-27T...", "detail": {...}
  },
  "client": {"id": 1, "proyecto": "alpha", "organizacion": "..."},
  "target": {"id": 2, "category": "os", "subkey": "linux", "label": "web01"}
}
```

(O `event: "alert.resolved"` cuando se resuelve.)

## Sweep periódico

Extiende `systemd/snapshot-healthcheck.service` con un `ExecStartPost`
adicional:

```ini
ExecStartPost=-/opt/snapshot-V3/core/bin/snapctl central alerts-sweep
```

El sub-comando llama a `backend/central/alerts.py::sweep_inactive`.
No-op si `MODE != "central"`.

## Cambios al código existente

| Archivo | Cambio |
|---|---|
| `backend/models/db.py` | + tabla `central_alerts` + 2 índices (idempotente) |
| `backend/central/models.py` | `apply_heartbeat` invoca `alerts.evaluate_heartbeat(payload, ...)` antes de retornar |
| `backend/config.py` | `ALERTS_NO_HEARTBEAT_HOURS`, `ALERTS_SHRINK_PCT`, `ALERTS_EMAIL`, `ALERTS_WEBHOOK` |
| `backend/app.py` | Si `MODE=central`: registrar `central_alerts_bp`. Context processor para banner |
| `backend/central/__init__.py` | Re-export |
| `core/bin/snapctl` | Sub-comando `central alerts-sweep` |
| `core/lib/archive.sh` | Acumular paths faltantes en una variable y enviarlos en `host_meta.missing_paths` del heartbeat |
| `core/lib/central.sh` | `central_send` acepta missing_paths |
| `systemd/snapshot-healthcheck.service` | `ExecStartPost` para sweep |
| `core/etc/snapshot.local.conf.example` | Documentar las 4 vars de `ALERTS_*` |
| `README.md` | Sección "Alertas (modo central)" |

## Plan de testing

### Unit
- `evaluate_heartbeat`: dispara folder_missing si missing_paths no vacío;
  resuelve si missing_paths vacío.
- `evaluate_heartbeat`: dispara backup_shrink si pct >= threshold; no
  dispara si pct < threshold.
- `sweep_inactive`: dispara no_heartbeat si threshold superado; no
  duplica.
- `acknowledge`: marca resolved_at, deja resto inalterado.
- `notify`: no llama SMTP si no configurado; llama webhook si configurado.
- `Config.ALERTS_*` parsea ints válidos, default razonable si missing.

### Integration
- E2E: heartbeat con missing_paths → alerta activa visible en
  `/api/admin/alerts`.
- Heartbeat OK siguiente → folder_missing resuelve.
- Two heartbeats con shrink suficiente → backup_shrink active +
  notified_at no NULL.
- Sweep tras inactividad >48h → no_heartbeat active.
- Auth: operator puede ack, auditor recibe 403 al ack.

## Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| Sweep lento con muchos targets | Query con `WHERE last_heartbeat_ts < threshold_iso` indexado; sweep cada 15 min, no es tiempo real |
| Falsos positivos en shrink durante retención legítima | Detail incluye prev/new bytes; admin acknowledgea sin resolver el target. Threshold es configurable por deploy. |
| Email spam si fluctúa folder_missing entre heartbeats | Una vez disparada, no re-notifica hasta resolverse y volver a dispararse (estado activo se identifica por `resolved_at IS NULL`). |
| Webhook lento bloqueando heartbeat | Timeout 5s en POST; si falla, igual marca notified_at e ignora |
| Config edit corrompe `snapshot.local.conf` | Escribe a `tmp` + `os.replace` atómico, mismo patrón que `scheduler.py::_write_dropin` |

## Métricas de éxito

- Schema migra clean en deploys existentes (idempotent).
- Sweep ejecuta en <1s con 1000 targets.
- Email se envía en <3s desde fire.
- 100% test coverage en `evaluate_heartbeat` y `sweep_inactive`.

## Resumen en una frase

Sub-D agrega una capa de detección + notificación al deploy
`MODE=central`: tres reglas (no_heartbeat, folder_missing, backup_shrink)
que se evalúan reactivamente al recibir heartbeats o periódicamente vía
sweep, persisten en una tabla nueva, disparan email + webhook, y se
muestran en una página `/dashboard-central/alerts` + banner global.
