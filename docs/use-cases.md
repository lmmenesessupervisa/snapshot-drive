# Casos de uso — snapshot-V3

Cada escenario describe un objetivo del operador, los pasos concretos
para resolverlo y dónde ver/validar el resultado.

## 1. Servidor único — quiero un backup mensual cifrado a Drive

**Persona:** PYME con un único servidor Ubuntu, sin equipo de ops.

**Pasos:**

1. `sudo bash install.sh -y` en el host.
2. Crear admin: `sudo snapctl admin create --email tu@org --role admin`.
3. Loguear al panel `http://<host>:5070`, completar MFA enrolment.
4. **Ajustes → Paso 1 · Vincular cuenta de Google**: en tu workstation con browser corre `rclone authorize "drive"`, copia el JSON y pegálo en la textarea → "Vincular".
5. **Paso 2** — elegir Shared Drive (recomendado) o Personal.
6. **Ajustes → Backup mensual:** completar proyecto/entorno/país.
7. **Ajustes → Cifrado age → Generar nuevo keypair** — copiar la
   privada en un gestor externo.
8. Click en "Agregar pública a recipients" → "Guardar recipients".
9. **Dashboard → Generar archivo ahora** para validar end-to-end.
10. El timer mensual ya está activo; no hace falta nada más.

**Validación:** dashboard muestra "Último archivo: ahora", el path
remoto aparece en `/snapshots`.

---

## 2. Quiero respaldar 4 servidores y verlos a todos en un solo lugar

**Persona:** Sysadmin con 4 hosts Linux.

**Topología:** 4 VMs cliente + 1 VM central (puede ser un host nuevo
o reusar uno de los 4 que ya tenés, pero recomendado separar).

**Pasos:**

1. **VM central:** `sudo bash install.sh -y --central`.
   Bootstrap del primer admin. Configurar SMTP en local.conf si querés alertas por email.
2. En el panel del central → `Clientes → Nuevo cliente` por cada host
   (proyecto único por cliente). Ejemplo: `web-prod`, `db-prod`, `staging`, `ci`.
3. Por cada cliente: `Tokens → Emitir token`. **Anotá los 4 tokens.**
4. En cada VM cliente:
   ```bash
   sudo bash install.sh -y
   sudo snapctl admin create --email admin@vmN --role admin
   sudo systemctl start snapshot-backend
   ```
   Después, desde el browser → `http://<vm-ip>:5070/settings → Vinculación con servidor central`:
   - URL: `http://192.168.1.30:5070` (marcá "Permitir HTTP" si está en LAN).
   - Token: el del paso 3 para esta VM.
   - Click **Probar conexión** → esperar chip emerald.
   - Click **Guardar**.
   - Forzá un heartbeat real: `sudo snapctl db-archive` (si tenés DB targets) o esperá al timer.
5. En la UI del central: `Dashboard central` → ves los 4 clientes con
   sus heartbeats.

**Validación:** después del primer `snapctl archive` (o al pasar el
tiempo del próximo timer), cada cliente aparece con `total_size_bytes`
y `last_heartbeat_ts` recientes.

---

## 3. Necesito que me avisen cuando un cliente deja de reportar

**Persona:** Sysadmin del caso 2; quiere alertas pasivas.

**Pasos (en el central):**

1. `Ajustes → Alertas`:
   - "Sin heartbeat (h)": **24** (más conservador que el default de 48)
   - "Email destinatario": `ops@miorg`
   - "Webhook URL": opcional (Slack incoming webhook URL, etc.)
2. Configurar SMTP en local.conf si todavía no:
   ```bash
   SMTP_HOST="smtp.gmail.com"
   SMTP_PORT="587"
   SMTP_USER="alertas@miorg"
   SMTP_PASSWORD="<app password de 16 chars>"
   SMTP_FROM="alertas@miorg"
   ```
3. `sudo systemctl restart snapshot-backend`.

**Cómo se prueba:**

- Apagá una VM cliente.
- Esperá > `ALERTS_NO_HEARTBEAT_HOURS` o forzá: `sudo snapctl central alerts-sweep`.
- Vas a recibir email + (opcional) POST al webhook.
- En `/dashboard-central/alerts` aparece la alerta `no_heartbeat`.
- Cuando la VM vuelva y mande un heartbeat, la alerta se auto-resuelve
  en el próximo sweep.

---

## 4. Restaurar un servidor que se rompió

**Persona:** Te llaman a las 3am porque cayó `web-prod`.

**Pasos:**

1. **Levantá una VM nueva** Ubuntu igual a la rota.
2. `git clone .../snapshot-drive && sudo bash install.sh -y`
3. Configurá Drive con la **misma cuenta** que tenía la rota (mismo
   token rclone) → `local.conf` o copiar `/var/lib/snapshot-v3/rclone.conf` desde un backup.
4. Pegá la **privada age** que guardaste (si los archivos son `.age`):
   ```bash
   sudo nano /var/lib/snapshot-v3/age-identity.txt   # mode 0600
   echo 'AGE-SECRET-KEY-1...' > /var/lib/snapshot-v3/age-identity.txt
   sudo chmod 600 /var/lib/snapshot-v3/age-identity.txt

   sudo nano /etc/snapshot-v3/snapshot.local.conf
   # ARCHIVE_AGE_IDENTITY_FILE="/var/lib/snapshot-v3/age-identity.txt"
   ```
5. Listar archives:
   ```bash
   sudo snapctl archive-list
   ```
6. Restaurar el más reciente:
   ```bash
   sudo snapctl restore "<ruta_que_apareció>"
   # Por default va a /tmp/restore-<ts>/
   ```
7. Mover lo recuperado a su ubicación final con `mv`/`rsync`.

**Para DBs:**

```bash
sudo snapctl db-archive-restore "<remote_path>" --target appdb_new
```

(donde `appdb_new` es la conn-string para Postgres/MySQL, o URI Mongo).

> **Importante después:** borrá `age-identity.txt` y quitá el setting
> `ARCHIVE_AGE_IDENTITY_FILE`. La privada NO debe quedar en el host.

---

## 5. Rotar la clave age (sospecha de compromiso de la privada)

**Persona:** Operador prudente — perdió un disco con la privada.

**Pasos:**

1. **NO borres la pública vieja todavía** — la necesitás para descifrar
   los archivos viejos.
2. Generá un keypair nuevo: `Ajustes → Cifrado age → Generar nuevo keypair`.
3. Copiá la nueva privada en lugar seguro (uno o dos lugares según
   tu política).
4. En el campo "Recipients", reemplazá la pública vieja por la nueva
   (o agregá la nueva y dejá la vieja unos meses — multi-recipient
   permite descifrar con cualquiera).
5. `Guardar recipients`. Los próximos archivos van a usar la nueva
   pública. Los viejos siguen requiriendo la privada vieja.
6. Cuando todos los archivos viejos hayan rotado por retención
   (`ARCHIVE_KEEP_MONTHS`), ya podés descartar la privada vieja con
   tranquilidad.

---

## 6. Backups de un Postgres en otra VM

**Persona:** Tenés Postgres corriendo en otra VM (no en el host de
snapshot-V3).

**Pasos:**

1. En la VM Postgres: crear un user de backup con permisos read-only:
   ```sql
   CREATE ROLE pgbackup LOGIN PASSWORD 'xxx';
   GRANT pg_read_all_data TO pgbackup;
   ```
2. Permitir conexión desde el host de snapshot-V3 (`pg_hba.conf`).
3. En el host de snapshot-V3, **Ajustes → Backups de bases de datos**:
   ```
   Targets:    postgres:appdb postgres:reportingdb
   PG Host:    192.168.1.50
   PG Port:    5432
   PG User:    pgbackup
   PG Password: xxx
   ```
4. `Guardar configuración DB`.
5. Forzá una corrida: `sudo snapctl db-archive`.
6. En `/snapshots` vas a ver los archivos bajo
   `<proyecto>/.../db/postgres/<dbname>/YYYY/MM/DD/`.

---

## 7. Auditor / contador necesita evidencia de que se hicieron los backups

**Persona:** Compliance pide pruebas mensuales.

**Pasos:**

1. Crear cuenta read-only:
   ```bash
   sudo snapctl admin create --email auditor@org --role auditor
   ```
2. El auditor loguea, va a `/snapshots` y exporta el listado a CSV
   (o usa la API: `GET /api/archive/list`).
3. En central, `/dashboard-central` muestra el estado agregado por
   proyecto y entorno.
4. Para una vista única read-only del shared Drive:
   `SNAPSHOT_AUDIT_VIEWER="1"` en local.conf y `/audit/` queda
   accesible para roles `admin` y `auditor`.

El audit log de cada user (login, password change, mfa enroll) está
en la tabla `audit_auth`:

```bash
sudo sqlite3 /var/lib/snapshot-v3/snapshot.db \
  "SELECT * FROM audit_auth WHERE email='auditor@org' ORDER BY id DESC;"
```

---

## 8. Quiero probar la solución sin gastar plata en hosting

**Persona:** Vos antes de proponerle esto a tu jefe.

**Setup:**

1. Tres VMs Ubuntu en VirtualBox/UTM/Multipass:
   - 192.168.56.10 → central
   - 192.168.56.11 → cliente A
   - 192.168.56.12 → cliente B
2. Bridged network entre ellas (o host-only network suficiente).
3. Seguí [deployment.md → Probarlo localmente con VMs Ubuntu](deployment.md#probarlo-localmente-con-vms-ubuntu-sin-dominio).
4. Para Google Drive de prueba: usá una cuenta personal (gratuita,
   15 GB), creá un Shared Drive si tenés Workspace de prueba.

**Sin Drive:** podés correr todo localmente con un remote local de
rclone:

```bash
# En cada cliente:
rclone config create localbk local
sudo sed -i 's/^RCLONE_REMOTE=.*/RCLONE_REMOTE="localbk"/' \
    /opt/snapshot-V3/core/etc/snapshot.conf
sudo mkdir -p /tmp/fake-drive && sudo chown root /tmp/fake-drive
# Editá snapshot.conf:
#   RCLONE_REMOTE_PATH="/tmp/fake-drive/${HOSTNAME}"
```

Esto te permite probar todo el flow (taxonomía, encriptación,
heartbeats, alertas) sin Drive real.

---

## 9. Migrar de un central a otro (cambio de host de ops)

**Persona:** El central viejo tiene hardware al límite y vas a moverlo.

**Pasos:**

1. **En el central nuevo:** `sudo bash install.sh -y --central`.
2. **En el central viejo:** parar tráfico:
   ```bash
   sudo systemctl stop snapshot-backend
   ```
3. Copiar el state al nuevo:
   ```bash
   # En el viejo:
   sudo tar -czf central-state.tar.gz \
       /etc/snapshot-v3/ \
       /var/lib/snapshot-v3/

   # En el nuevo:
   sudo systemctl stop snapshot-backend
   sudo tar -xzf central-state.tar.gz -C /
   sudo systemctl start snapshot-backend
   ```
4. Cambiar DNS (o IP fija) para que apunte al central nuevo.
   Los clientes no necesitan cambiar `CENTRAL_TOKEN` — el hash sigue
   en el SQLite que copiaste.
5. Si cambió la URL, sí actualizá `CENTRAL_URL` en cada cliente.

---

## 10. Quiero un setup multi-recipient para escrow

**Persona:** Compliance/seguridad exige que UN solo operador no pueda
descifrar todos los backups por sí mismo.

**Pasos:**

1. **Operador:** genera keypair A. Privada queda con el operador.
2. **Encargado de escrow / oficial de seguridad:** genera keypair B
   en un host distinto (incluso Windows con `age-keygen.exe`).
   Privada queda en sobre sellado, custodia legal, gestor offline, etc.
3. En el panel: `Ajustes → Cifrado age → Recipients`:
   ```
   age1ops...A age1escrow...B
   ```
4. Cada archivo nuevo es descifrable con A **o** con B (no requiere
   ambas).
5. El operador opera normalmente sin pedirle nada al escrow. Si el
   operador desaparece o pierde la privada A, el escrow puede
   restaurar con B.

> Truco: si querés requerir las DOS privadas para descifrar (split-key),
> age soporta `age-passphrase` o `age -R` con un wrapper, pero es más
> complejo de operar. Empezá con multi-recipient simple — alcanza para
> 99% de los casos.
