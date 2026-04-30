# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Comandos comunes

**Tests (siempre con el venv bundled, no el Python del sistema):**

```bash
PATH="/opt/snapshot-V3/bundle/bin:$PATH" \
  /opt/snapshot-V3/.venv/bin/python -m pytest tests/ -q --no-header

# Un test puntual:
/opt/snapshot-V3/.venv/bin/python -m pytest tests/auth/test_login.py::test_csrf -v

# El test test_age_round_trip requiere que age esté en PATH (vive en bundle/bin/),
# por eso se prefija con PATH=…/bundle/bin. Sin ese prefijo, --deselect:
/opt/snapshot-V3/.venv/bin/python -m pytest tests/ -q \
  --deselect tests/db_archive/test_crypto_helper.py::test_age_round_trip
```

**Re-deploy local tras editar código del repo:**

```bash
sudo bash install.sh -y         # idempotente, hace rsync --delete a /opt/snapshot-V3
                                 # NO toca /etc/snapshot-v3 ni /var/lib/snapshot-v3
sudo systemctl restart snapshot-backend
```

**Deploy hot-patch de un solo archivo (no requiere bundle re-download):**

```bash
sudo cp backend/services/X.py /opt/snapshot-V3/backend/services/X.py
sudo cp frontend/templates/Y.html /opt/snapshot-V3/frontend/templates/Y.html
sudo systemctl restart snapshot-backend
```

**Smoke checks útiles después de un cambio:**

```bash
# Endpoint vivo (debe redirigir a /auth/login con 302)
curl -sS -o /dev/null -w "%{http_code}\n" http://127.0.0.1:5070/

# Importar el módulo modificado contra el venv real:
/opt/snapshot-V3/.venv/bin/python -c "from backend.app import create_app; print('ok')"

# Estado de timers
systemctl list-timers 'snapshot*'
sudo journalctl -u snapshot-backend -n 50

# Logs JSON-lines del CLI
sudo tail -f /var/log/snapshot-v3/snapctl.log
```

**CLI snapctl (todos los subcomandos como root):**

```bash
sudo snapctl archive            # crea un archive mensual ahora (sync, puede tardar)
sudo snapctl db-archive         # corre los DB targets configurados
sudo snapctl crypto-keygen      # imprime keypair age (privada NO se persiste)
sudo snapctl admin list         # gestión de usuarios — list/create/set-role/reset-password/reset-mfa/...
sudo snapctl central send-test  # solo en MODE=client: heartbeat de prueba al central
sudo snapctl central drain-queue   # MODE=client: drena central_queue
sudo snapctl central alerts-sweep  # MODE=central: corre detección de alertas
```

## Arquitectura — lo que requiere leer varios archivos

### Tres capas, un binario

```
core/bin/snapctl       (Bash — motor real del backup, fuente de verdad)
backend/               (Flask + SQLite + gunicorn — panel y wrapper de snapctl)
frontend/templates/    (Jinja2 + Tailwind via CDN — server-rendered, no SPA)
```

El backend Flask **no** ejecuta lógica de backup — orquesta `snapctl` con
`subprocess.run`. Esto significa que muchos endpoints son `subprocess`
síncronos con timeout configurable (`SNAPCTL_TIMEOUT`, default 1h).
Mientras corre, el endpoint POST queda bloqueado y la UI muestra un
overlay "Trabajando…".

### MODE=client vs MODE=central (sub-B, dual deploy)

`backend/config.py:Config.MODE` decide qué blueprints registra `app.py`.
**Mismo código, dos modos:**

- `MODE=client` (default): registra `auth_bp`, `api_bp`, `web_bp`,
  `audit_bp` (si `SNAPSHOT_AUDIT_VIEWER=1`).
- `MODE=central`: agrega `central_api_bp` (M2M Bearer), `central_admin_bp`,
  `central_dashboard_bp`, `central_alerts_bp`. Estos viven en
  `backend/central/`.

En cliente, `central_*` SQL tables existen pero quedan vacías
(decisión deliberada en `backend/models/db.py:SCHEMA` — un solo path de
inicialización). En central, `central_queue` queda vacía. Cambiar de
modo requiere reiniciar `snapshot-backend`.

### Sistema de heartbeats (sub-B)

Cliente → POST `/api/heartbeat` con Bearer token (no cookie). Schema en
`backend/central/schema.py`. Validación manual sin pydantic. Idempotencia
con UUID v4 en `event_id`. Si el central no responde, el cliente encola
en `central_queue` (SQLite en el cliente) y el healthcheck timer drena
cada 15 min con backoff exponencial (`backend/central/queue.py`).

### Pipeline crypto (sub-F)

`core/lib/crypto.sh` define un único helper `crypto_encrypt_pipe` que
funciona como filtro stdin→stdout. Selección de modo por precedence:

1. `ARCHIVE_AGE_RECIPIENTS` no vacío → `age` (clave pública)
2. `ARCHIVE_PASSWORD` no vacío → `openssl aes-256-cbc -pbkdf2`
3. ninguno → passthrough

Mismo helper se usa en `archive.sh` (mensual) y `db_archive.sh` (DB) —
**la config de cifrado aplica a TODOS los backups**, no es por engine.
Las extensiones de archivo dependen del modo: `tar.zst[.age|.enc]` o
`sql.zst[.age|.enc]` o `archive.zst[.age|.enc]`.

### Taxonomía remota — fuente de verdad

Los archivos en Drive viven en el **root del remote**, NO bajo
`snapshots/`. Estructura:

```
<RCLONE_REMOTE>:<proyecto>/<entorno>/<pais>/<category>/<subkey>/<label>/YYYY/MM/DD/
  servidor_<label>_<YYYYMMDD_HHMMSS>.<ext>
```

donde `category` ∈ {os, db}, `subkey` ∈ {linux, postgres, mysql, mongo}.
La carpeta `snapshots/` solo existe en deploys legacy con repos restic
(modelo viejo). El audit_tree service (`backend/services/audit_tree.py`)
escanea desde el root precisamente por esto — `AUDIT_REMOTE_PATH` se
ignora ahí.

### Auth + RBAC (sub-A)

- argon2id para passwords, AES-GCM para TOTP secrets at-rest, HKDF para
  derivar la SECRET_KEY de Flask de la master key.
- 3 roles: `admin` (todo, MFA obligatorio), `operator` (gestión sin
  usuarios), `auditor` (read-only).
- En `MODE=central` hay matriz extra de permisos finos en
  `backend/central/permissions.py:PERMISSIONS`.
- Sesiones server-side en SQLite (no JWT). CSRF token por sesión,
  exigido en métodos unsafe; exenciones explícitas en
  `backend/auth/middleware.py:CSRF_EXEMPT_ENDPOINTS`.
- `/audit` ahora gateado por rol `admin`/`auditor`, **no** por password
  propia (esto cambió en commit `8fbec40`; el legacy `AUDIT_PASSWORD`
  ya no existe en `Config`).

### Patrón único de configuración

Todo cambio de config persistente pasa por
`backend/services/archive_config.py:_write_back()` que escribe atómico
con tmp+rename, hace `.bak` del local.conf, y preserva permisos 0600.
Las funciones `set_*` ahí (taxonomía, password openssl, recipients age,
DB targets, alertas) usan la misma plumbing — al agregar config nueva,
seguir ese patrón. Algunas (alertas) además mutan `Config.*` en memoria
para evitar restart del backend.

### Versiones pinneadas en bundle

`install.sh` tiene las versiones en cabecera:
- Python 3.12.8, restic 0.17.3, rclone v1.68.2, age v1.2.1.
- Override con env vars (`PYTHON_VERSION=...`, `RCLONE_VERSION=...`).
- Idempotente: si la versión ya está en `bundle/`, no re-baja.

## Documentación de referencia (en español)

`docs/` tiene 8 docs detallados sobre el proyecto. Cuando algo no está
claro acá, mirá ahí primero antes de preguntar:

- `docs/definicion_proyecto.md` — qué es y por qué
- `docs/arquitectura_y_stack.md` — diagramas + decisiones
- `docs/base_datos_y_roles.md` — schema SQLite + matriz de permisos
- `docs/api.md` — catálogo completo de endpoints
- `docs/configuracion.md` — todas las variables y qué requiere restart
- `docs/deployment.md` — cliente/central, testing en VMs sin dominio
- `docs/user-guide.md` — operación día-a-día
- `docs/use-cases.md` — 10 escenarios paso-a-paso
- `docs/superpowers/specs/` y `docs/superpowers/plans/` — specs y plans
  originales de cada sub-proyecto (A=auth, B=dual deploy, D=alertas,
  E=DB backups, F=crypto)

`README.md` (843 líneas) tiene una visión más operativa y manual de
referencia para usuarios — incluye instalación, recovery, alertas, DB
backups, age vs openssl. Si necesitás contexto histórico de alguna
decisión, suele estar ahí.

## Antes de implementar — confirmar primero

Cuando el usuario pida un cambio o ajuste, **NO empieces a editar/escribir
código directamente**. Antes:

1. Resumí en 1-3 frases lo que entendiste de la solicitud (qué archivos
   tocarías, qué comportamiento cambia, qué efectos secundarios podría
   tener).
2. Si hay más de una manera razonable de hacerlo, presentá las opciones
   con una recomendación corta.
3. Esperá confirmación explícita ("sí", "dale", "esa opción", "hacelo")
   antes de tocar archivos.

Excepción: si el usuario explícitamente delega ("hazlo por mi", "lo
dejo en tus manos", "auto mode"), seguí adelante sin preguntar pero
manteniendo el resumen breve antes del primer tool call.

Bugs evidentes con causa raíz clara y fix de 1-2 líneas pueden ir
directos al `Edit` sin confirmación previa, siempre y cuando lo
expliques en la misma respuesta.

## Convenciones que importan

- **Idioma**: comentarios y commit messages en español. Variables/código en
  inglés. Mensajes de log en español. Mensajes de error al usuario también.
- **Commits**: conventional-ish (`feat(scope):`, `fix(scope):`,
  `docs:`). Co-Authored-By al final de cuerpos largos.
- **No emojis** en código fuente. En Markdown está bien.
- **No mocks de DB** en tests — los tests de auth/central usan SQLite
  real (in-memory o tmp). Hay un patrón de fixture
  `importlib.reload(backend.app)` en tests centrales para evitar
  pollution entre cases.
- **systemd templates**: `snapshot@<unit>.timer` con drop-ins en
  `/etc/systemd/system/snapshot@<unit>.timer.d/`. El service
  `services/scheduler.py` los escribe; nunca editar a mano.
- **bundle/bin tiene precedencia en PATH**: `core/lib/common.sh`
  antepone `/opt/snapshot-V3/bundle/bin` antes de cualquier llamada,
  así que `snapctl` siempre usa los binarios pinneados, no los del host.

## Estilos del frontend — usar siempre lo que ya existe

**Regla:** al modificar o crear vistas, usar **siempre** las clases ya
definidas en `frontend/static/css/app.css`. **No** introducir Tailwind
raw (`bg-brand-600`, `rounded`, `border border-[var(--border)] bg-[var(--bg)] px-3 py-2`,
etc.) cuando hay equivalentes en el sistema.

Sistema de estilos disponible (shadcn-like, tema claro):

- **Layout / surfaces:** `.card`, `.card-hover`, `.kpi`
- **Botones:** `.btn-primary`, `.btn-secondary`, `.btn-danger`
- **Inputs:** `.input` (cubre `<input>`, `<textarea>`, `<select>`)
- **Chips / badges:** `.chip` + `.chip-sky` / `-amber` / `-emerald` /
  `-rose` / `-slate` / `-violet`
- **Tablas:** styling automático sobre `<table>`/`<thead>`/`<tbody>`
  con borders y hover
- **User menu (header):** `.user-menu`, `.user-menu-summary`,
  `.user-menu-panel`, `.user-menu-item[-danger]`
- **Row actions menu:** `.row-actions-panel`, `.row-actions-item[-danger]`
  (para tablas con muchas acciones por fila)
- **Audit tree:** `.audit-tab[-active]`, `.audit-proyecto[-row|-body]`,
  `.audit-region[-row|-body]`, `.audit-cli[-row|-detail]`, `.audit-bk-block`
- **Settings cards:** `.db-engine-card[-toggle|-fields]`,
  `.crypto-mode-card`, `.sched-row[-name|-desc|-fields|-actions]`
- **CSS variables (preferí estas sobre colores Tailwind):**
  `var(--foreground)`, `var(--muted)`, `var(--muted-2)`,
  `var(--border)`, `var(--card)`, `var(--accent-surface)`,
  `var(--primary)`, `var(--success)`, `var(--warn)`, `var(--danger)`

**Si necesitás un estilo nuevo que no existe:** agregalo a `app.css` con
la misma convención (token-driven, kebab-case con prefijo del módulo
si es local, p.ej. `.audit-bk-table`). No lo pongas inline en el template.

**Tipografía monospace:** clase `.mono` (no `font-mono`). Para
identificadores tipo email/path se usa `class="mono"` directamente.

## Cosas que históricamente rompen

- Editar `frontend/templates/auth/*.html` sin incluir el bloque
  `<script>tailwind.config = {...}</script>`. Sin esa config, las clases
  `bg-brand-600`/`text-brand-X` no generan estilos → botones invisibles.
  `login.html` y `password_reset_*.html` ya tienen el patrón correcto;
  copiarlo al agregar páginas standalone (las que NO extienden
  `base.html`).
- Llamar `apiFetch(...)` en `<script>` inline ANTES de que `auth.js`
  (cargado con `defer`) registre `window.apiFetch`. Si necesitás
  ejecutar JS al cargar una página, ponelo en un archivo separado bajo
  `frontend/static/js/` con `defer`, no inline al final del template.
- Asumir que `Config.X` reflejará cambios escritos a `local.conf` sin
  restart. Solo `set_alerts_config` es la excepción que actualiza la
  copia en memoria.
- `rclone lsjson` puede tardar 5-15s con muchos archivos. Los services
  `audit.py`, `audit_tree.py`, `archive_ops.py` cachean con TTL
  (10-60s) en proceso. Hay invalidación manual tras create/delete
  (`invalidate_cache()`).
