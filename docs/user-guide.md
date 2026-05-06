# Guía de uso del panel — snapshot-V3

Esta guía cubre el día-a-día desde la perspectiva del operador. No
incluye instalación (ver [deployment.md](deployment.md)) ni
detalles de API (ver [api.md](api.md)).

## Primer login (admin recién creado)

1. Abrí `http://<host>:5070/auth/login`.
2. Ingresá email y password temporal (devuelta por
   `snapctl admin reset-password` o `snapctl admin create`).
3. Te redirige a "Cambiar contraseña" — elegí una nueva (≥ 12 chars).
4. Como sos `admin`, te lleva a `/auth/mfa-enroll`:
   - Escaneá el QR con tu app de autenticación (Google Authenticator,
     Authy, 1Password, ...).
   - Ingresá el código de 6 dígitos para confirmar.
   - **Anotá los 10 backup codes** que muestra una sola vez.
5. A partir de ahora cada login va a pedir email + password + código TOTP.

## Tour del panel (cliente)

```
[ Sidebar lateral ]
  Dashboard      → KPIs + último archivo + acciones rápidas
  Archivos       → Listado completo de archives en Drive (con restaurar/borrar)
  Logs           → Tail JSON-lines de snapctl.log (auto-refresh)
  Ajustes        → Drive + taxonomía + DB + crypto + alertas (admin)
  Auditoría      → Vista agregada (solo si SNAPSHOT_AUDIT_VIEWER=1, admin/auditor)
[ Header ]
  Avatar + nombre + rol + dropdown
    → Cambiar contraseña
    → Usuarios (admin)
    → Cerrar sesión
```

## Vincular Google Drive (paso 1)

`Ajustes → Paso 1 · Vincular cuenta de Google`:

El flujo es **paste manual del token de rclone**. En tu workstation con browser:

1. Instala rclone (Windows: `winget install Rclone.Rclone`; Linux/macOS: `curl https://rclone.org/install.sh | sudo bash`).
2. Corre: `rclone authorize "drive"` — se abre Google en tu browser, autorizás con la cuenta corporativa, rclone te imprime el JSON del token.
3. Copia el JSON entero (incluye las llaves `{ }`).
4. En el panel `/settings`: pegá el JSON en la textarea **"Token JSON de rclone"** → click "Vincular".
5. Estado debería pasar a `Vinculado a Google Drive · …`.

> **Por qué este flujo y no Device Flow:** Google rechaza el scope `drive` (full) vía Device Flow — solo permite `drive.file` (archivos creados por la app), que NO sirve para que el central vea los archivos subidos por los clientes. `rclone authorize` usa el flujo OAuth tradicional con redirect callback que sí soporta scope completo.

> **Tip Shared Drive:** Para producción, creá un Shared Drive en Google y compartilo con la cuenta. Esto te da:
> - Cuotas independientes (no comen tu cuota personal de 15 GB).
> - Permisos manejables a nivel grupo.
> - Throughput mucho mayor (~100 qps vs ~10 qps de Drive personal).

## Configurar el target de Drive

En `Ajustes → Paso 2 · Dónde guardar los archivos`:

- **Personal**: usa la raíz `/snapshots/<hostname>/` del Drive del usuario logueado.
- **Shared Drive**: elegís uno de la lista. Recomendado para múltiples hosts.

## Configurar la taxonomía

`Ajustes → Backup mensual` define dónde van los archivos en el Drive:

```
<BACKUP_PROYECTO>/<BACKUP_ENTORNO>/<BACKUP_PAIS>/os/linux/<BACKUP_NOMBRE>/YYYY/MM/DD/
   servidor_<nombre>_<YYYYMMDD_HHMMSS>.tar.zst[.age|.enc]
```

Valores válidos:

| Campo | Opciones |
|---|---|
| Proyecto | superaccess-uno · superaccess-dos · basculas · proyectos-especiales · orus |
| Entorno | cloud · local |
| País | colombia · peru · costa-rica · panama |
| Nombre | letras/dígitos/`._-`, default `$(hostname -s)` |

## Elegir cifrado

Tenés 3 modos posibles. Solo uno está activo a la vez:

| Modo | Cómo se activa | Qué archivo sube |
|---|---|---|
| **age** (recomendado) | `Ajustes → Cifrado age`: pegá `age1...` recipients, o usá el botón "Generar nuevo keypair" | `*.tar.zst.age` |
| **openssl** (legacy) | `Ajustes → Backup mensual → Contraseña` | `*.tar.zst.enc` |
| **Sin cifrado** | Vacío en ambos | `*.tar.zst` |

Si seteás `age` Y hay password openssl configurada, age tiene
precedencia. Los archivos viejos siguen necesitando su modo original
para descifrarse.

### Generar tu primer keypair age

`Ajustes → Cifrado age → Generar nuevo keypair`:

1. Aparece un modal con `public` y `private`.
2. **Copiá la privada AHORA** y guardala fuera del servidor (gestor de
   passwords, sobre sellado, escrow). Si la perdés, no podrás restaurar.
3. Click en `Agregar pública a recipients` para que la pública se
   pegue automáticamente al campo.
4. Click en `Guardar recipients`.

Para multi-recipient (operacional + escrow):

1. Generá dos keypairs (corré "Generar" dos veces, anotando ambas
   privadas en lugares distintos).
2. Pegá ambas públicas separadas por espacio en el campo:
   `age1ops... age1escrow...`

Cualquiera de las dos privadas puede descifrar.

## Generar un archivo manualmente

`Dashboard → Generar archivo ahora` (o `snapctl archive` por SSH).

- Aparece un overlay "Trabajando…" mientras corre.
- El timeout default es 1 hora. Para hosts grandes editá `SNAPCTL_TIMEOUT` en
  `local.conf`.
- En `Logs` vas a ver las líneas en tiempo real.

## Restaurar un archivo

`Archivos → click en una fila → Restaurar`:

1. Te pide path local de destino (default `/tmp/restore-<timestamp>/`).
2. Si el archivo es `.age`, te pide el `ARCHIVE_AGE_IDENTITY_FILE` —
   apuntálo al path local con la privada (mode 0600).
3. Si es `.enc`, te pide la password openssl.

> **Nota:** Restaurar es destructivo si elegís un path con datos.
> Default va a `/tmp` para ser seguro — moviste con `mv` lo que necesites.

CLI equivalente:

```bash
# Lista los archivos:
sudo snapctl archive-list

# Restaura un path remoto a /tmp/restore/:
sudo ARCHIVE_AGE_IDENTITY_FILE=/root/age-id.txt \
    snapctl restore <ruta_remota>

# DB restore:
sudo snapctl db-archive-restore <remote_path> --target mydb_restore
```

## Backups de bases de datos

`Ajustes → Backups de bases de datos`:

1. **Targets**: lista space-separated de `engine:dbname`. Ejemplo:
   `postgres:appdb mysql:web mongo:metrics`
2. **PostgreSQL**: host (vacío = socket Unix), puerto, usuario, password.
3. **MySQL**: idem para MySQL/MariaDB.
4. **MongoDB**: URI completo `mongodb://user:pass@host:27017/...`.
5. Click en `Guardar configuración DB`.

Dentro de cada engine card hay 2 botones nuevos:

- **"Probar conexión"** → corre `pg_isready` / `mysqladmin ping` / `mongosh ping` con timeout 5s. Resultado inline en chip emerald (`OK · 23ms`) o rose con el mensaje del error. Si pegaste un password nuevo en el form, prueba con ESE sin guardarlo (validación pre-save).
- **"Generar dump ahora"** → dispara `snapctl db-archive --engine <ese>` síncrono. Toast con resultado.

### Manual desde Dashboard / Archivos

Hay un botón **"Generar backup BD ahora"** en `/`, `/snapshots` y `/settings`. Es **smart**:
- Sin engines configurados → disabled con hint "Configura un engine en Ajustes".
- 1 engine configurado → click corre directo.
- 2+ engines → modal con checkbox por engine + opción "Todos".

El KPI **"Último backup BD"** del Dashboard muestra el timestamp del dump más reciente y un mini-grid con cada engine: chip de salud (emerald < 24h, amber < 48h, rose ≥ 48h) + tamaño + path.

### Schedule automático

El timer `snapshot@db-archive.timer` corre diariamente y dispara
`snapctl db-archive` (sin filtros = todos los targets):
```
<dump_cmd> | zstd -10 -T0 | crypto_encrypt_pipe | rclone rcat <remote>
```

Sin disco intermedio. Por cada target manda un heartbeat al central
(si `MODE=client` con `CENTRAL_URL` configurado) — uno por DB.

## Gestión de usuarios

`Header → Usuarios` (solo admin).

- Crear usuario: email, nombre, rol, password inicial (vacío = aleatoria).
- Acciones por user: **Editar** · Reset PWD · Cerrar sesiones · Reset MFA · Deshabilitar/Habilitar
- Reset PWD genera una temporal — el user debe cambiarla al primer login.

### Diálogo "Editar usuario"

- Cambia **email**, **display_name**, **rol** y/o el flag **"Desactivar MFA"** en una sola operación.
- Si te editás a vos mismo: rol y toggle MFA quedan disabled (anti-lockout). Cambiar tu propio rol o quitarte MFA requiere otro admin.
- Cuando `mfa_disabled=true`, el login para ese usuario salta el enroll-required y el challenge TOTP, aunque el rol sea admin. El secret existente NO se borra (podés reactivar luego sin re-enroll). En la columna MFA aparece chip slate `MFA off`.

CLI equivalente:

```bash
sudo snapctl admin list
sudo snapctl admin create --email u@org --role operator
sudo snapctl admin set-role --email u@org --role admin
sudo snapctl admin reset-password --email u@org
sudo snapctl admin reset-mfa --email u@org
sudo snapctl admin revoke-sessions --email u@org
sudo snapctl admin disable --email u@org
sudo snapctl admin enable --email u@org
```

## Audit log

Todos los eventos relevantes (login, mfa, role change, password reset,
user create/disable, drive link/unlink, archive create) se registran en
la tabla `audit_auth`.

Consulta SQL:

```bash
sudo sqlite3 /var/lib/snapshot-v3/snapshot.db \
  "SELECT created_at, event, email, ip, detail
   FROM audit_auth ORDER BY id DESC LIMIT 50;"
```

## Modo central (operador del fleet)

### Sidebar adicional

Cuando `MODE=central`, en el sidebar aparecen además:

- **Auditoría** → `/audit/` — vista jerárquica del shared Drive (lee del cache `drive_inventory`, sub-segundo). Botón "Refrescar" gatilla scan completo + reescribe DB.
- **Clientes** → `/dashboard-central/clients` — CRUD de clientes, emisión y revocación de tokens.
- **Alertas** → `/dashboard-central/alerts` — listado de activas con ack inline; banner rojo en todas las páginas si hay críticas.
- **Drive** → `/central/drive` — vincular cuenta de Google del central via paste de token rclone, definir Shared Drive destino + carpeta raíz a auditar.

### Registrar un cliente nuevo

1. **En el central**: `Clientes → Nuevo cliente`. Ingresá `proyecto` (único), organización, contacto.
2. Click en el cliente → `Tokens → Emitir token`. **Copiá** la plaintext que aparece UNA vez.
3. **En el host del cliente** — desde el panel del cliente, NO editando archivos:
   - `Ajustes → Vinculación con servidor central`.
   - **Base URL de la API**: pega la URL del central (`https://central.miorg.local` o `http://10.0.0.5:5070`).
   - Si usás HTTP en LAN, marcá **"Permitir HTTP en red local"**.
   - **Token Bearer**: pegá el token que copiaste.
   - Click **"Probar conexión"** → debería darte chip emerald `OK · central dev · proyecto: <tu-proyecto>`.
   - Click **"Guardar"**.
4. Forzá un heartbeat real para validar end-to-end (cualquier backup lo manda):
   ```bash
   sudo snapctl db-archive    # o snapctl archive
   ```
5. Volvé al dashboard central — el cliente aparece con `last_heartbeat_ts` reciente.

> **Alternativa CLI**: editar `/etc/snapshot-v3/snapshot.local.conf` con `CENTRAL_URL` y `CENTRAL_TOKEN` y reiniciar `snapshot-backend`. Es lo que hacía la UI internamente; sigue funcionando si preferís SSH.

### Configurar alertas (sub-D)

`Ajustes → Alertas` (solo aparece en MODE=central):

| Campo | Default | Para qué |
|---|---|---|
| Sin heartbeat (h) | 48 | Tiempo sin reportar para marcar `silent` |
| Shrink threshold (%) | 20 | Caída en bytes acumulados para `backup_shrink` |
| Email | — | Destinatario (vacío = sin email; requiere SMTP) |
| Webhook | — | URL `https://...` para POST JSON |

Cambios se aplican inmediatamente sin restart.

### Cómo funcionan las reglas

```mermaid
flowchart LR
    Sweep[snapctl central<br/>alerts-sweep] --> R1[no_heartbeat<br/>last_hb &lt; threshold]
    Sweep --> R2[folder_missing<br/>esperaba listar y vino vacío]
    Sweep --> R3[backup_shrink<br/>totals cayeron &gt; X%]
    R1 --> Store[(central_alerts)]
    R2 --> Store
    R3 --> Store
    Store --> Dispatch[dispatch SMTP/webhook]
    Dispatch --> N{notified?}
    N -->|no| Send[email + webhook]
    Send --> Mark[update notified_at]
```

Las alertas se auto-resuelven cuando la condición desaparece (un
heartbeat nuevo borra `no_heartbeat`, totals que vuelven al rango
borra `backup_shrink`, etc.). El `triggered_at` se mantiene para
histórico.

## Troubleshooting rápido

| Síntoma | Probable causa | Acción |
|---|---|---|
| Login pide MFA pero no tengo app | Code lost | Loguear con backup code; o `snapctl admin reset-mfa --email …` por SSH |
| `/dashboard-central` da 404 | `MODE=client` | Verificar `MODE=central` en local.conf y reiniciar |
| Heartbeat 401 | Token revocado/expirado | Re-emitir desde el central, actualizar `CENTRAL_TOKEN` en cliente |
| Heartbeat 403 | CSRF o permiso | Para M2M (Bearer) no debería pasar — chequear que el token pertenece al cliente correcto |
| Archivo `.age` no se descifra | Privada incorrecta o no es del recipient | Verificar pubkey: `age-keygen -y identity.txt` debe coincidir con un recipient |
| `pg_dump: connection refused` | DB_PG_HOST mal o pg_hba.conf bloquea | Probar manualmente: `sudo -u postgres pg_dump db` |
| Logs vacíos en `/logs` | Permisos de `/var/log/snapshot-v3/snapctl.log` | `chown root:root` y `chmod 640` |
| `/users` muestra "Cargando…" eterno | JS error en consola | Ver F12 → Console. Refrescá con Ctrl+Shift+R |
| Tabla de `/users` se desborda con muchos usuarios | Ya arreglado — `max-w-7xl` + `overflow-x-auto`. Si pasa, Ctrl+Shift+R para bajar el JS/HTML nuevos |
| `/audit/` lento al cambiar de vista | Ya arreglado — la UI lee de DB cache. Si seguís viéndolo, click "Refrescar" para reconciliar y reportá si vuelve a tardar |
| "Generar backup BD" disabled | No tenés engines en `Ajustes → Backups de bases de datos`. El botón se habilita en cuanto guardes uno |
| `/api/config` devuelve 500 con muchas pestañas abiertas | Era el bug de `auth_conn` compartido entre threads — ya arreglado (`ThreadLocalConn`). Si reaparece, capturá la traceback en `journalctl` |
| `snapctl interrumpido en línea 756/758` cada ~17 min | Era el `ModuleNotFoundError: backend` cuando el healthcheck llamaba a `snapctl central drain-queue` desde systemd — arreglado con `PYTHONPATH` explícito |

## Atajos por SSH (cuando el panel no responde)

```bash
# Estado de servicios
sudo systemctl status snapshot-backend
sudo systemctl list-timers 'snapshot*'

# Logs en vivo
sudo journalctl -u snapshot-backend -f
sudo tail -f /var/log/snapshot-v3/snapctl.log | jq -r '"\(.ts) [\(.level)] \(.msg)"'

# Ejecutar archive manual con verbose
sudo bash -x /opt/snapshot-V3/core/bin/snapctl archive

# Verificar conectividad al central
curl -i -H "Authorization: Bearer $CENTRAL_TOKEN" \
     -X GET http://central:5070/api/ping

# Estado del panel via DB
sudo sqlite3 /var/lib/snapshot-v3/snapshot.db .dump | head -50
```
