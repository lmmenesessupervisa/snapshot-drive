# Crypto Hardening (sub-project F)

**Fecha:** 2026-04-28
**Estado:** propuesto
**Sub-proyecto:** F — independiente. Aditivo: no rompe los backups existentes encriptados con openssl.

## Contexto

Hoy `archive.sh` y `db_archive.sh` cifran (cuando hay password) con
`openssl enc -aes-256-cbc -pbkdf2 -iter 100000 -pass env:ARCHIVE_PASSWORD`.

Ese esquema es funcional pero tiene tres limitaciones operativas:

1. **Single shared password** — si la perdés, los backups se vuelven
   irrecuperables.
2. **Sin recovery secundario** — no hay forma de dar una llave
   "read-only" a un tercero (auditor, escrow corporativo) sin
   compartir el secreto operacional.
3. **Rotar la password rompe la coherencia** — los backups viejos
   siguen exigiendo la password vieja, lo cual el operador suele
   olvidar.

`age` (https://age-encryption.org) resuelve los tres con criptografía
de clave pública: el host tiene la pubkey, la privada vive en escrow,
y se puede encriptar a múltiples pubkeys simultáneamente.

GPG queda descartado: gestión de keys / agentes / web of trust →
complejidad innecesaria para el caso de uso.

## Requisitos

### Funcionales

1. **Opt-in age** — si `ARCHIVE_AGE_RECIPIENTS` está seteado en
   `snapshot.local.conf`, el pipeline usa `age` en vez de `openssl`.
   Si está vacío, se mantiene el comportamiento actual con
   `ARCHIVE_PASSWORD`.
2. **Múltiples destinatarios** —
   `ARCHIVE_AGE_RECIPIENTS="age1xyz... age1abc..."` (space-separated).
   Cualquiera de las privadas asociadas puede descifrar.
3. **Bundle del binario** — `install.sh` descarga `age` y `age-keygen`
   pinneados desde el release oficial de `FiloSottile/age` a
   `/opt/snapshot-V3/bundle/bin/`, igual que restic y rclone.
4. **Auto-detect en restore** — el restore detecta el formato por la
   extensión del archivo (`.enc` → openssl, `.age` → age) y elige la
   herramienta correcta. **Backups viejos siguen siendo
   restauraables sin tocar nada.**
5. **CLI `snapctl crypto-keygen`** — genera un keypair age e imprime
   la privada UNA SOLA VEZ. La pública se imprime también para que el
   operador la pegue en `ARCHIVE_AGE_RECIPIENTS`.
6. **Mutex de configuración** — si ambos `ARCHIVE_PASSWORD` y
   `ARCHIVE_AGE_RECIPIENTS` están seteados, age gana y se imprime un
   warning visible en logs (no error: pueden coexistir durante una
   migración).
7. **Path/extensión** — backups nuevos con age usan `.tar.zst.age` /
   `.sql.zst.age` en vez de `.tar.zst.enc` / `.sql.zst.enc`.

### No-funcionales

- **Streaming compatible** — `age` lee stdin/stdout nativamente
  (igual que openssl), así que el pipeline `tar | zstd | age | rclone
  rcat` funciona sin cambios estructurales.
- **Reproducibilidad** — versión de age pinneada en `install.sh`.
- **Sin dependencias del host** — el binario `age` viene bundled. El
  operador no tiene que `apt install age`.
- **Speed** — age es más rápido que openssl por dos razones: (a)
  ChaCha20-Poly1305 sobre AES-CBC no necesita PBKDF2 100k al inicio,
  (b) el binario es Go estático con cero overhead de proceso.

### Out of scope

- **Migración automática** de backups viejos (descifrar openssl, re-
  encriptar age). Si el operador quiere migrar, lo hace manual con
  `snapctl archive-restore` + `snapctl archive` con la nueva config.
- **Hardware tokens** (YubiKey via age-plugin-yubikey). Requiere otro
  binario; spec aparte si se necesita.
- **Backend UI para gestionar recipients**. Por ahora se editan en
  `snapshot.local.conf`.

## Arquitectura

```
┌─ encrypt_pipe (helper en core/lib/crypto.sh) ────────────────────┐
│                                                                  │
│   if ARCHIVE_AGE_RECIPIENTS no vacío:                            │
│       exec age -r $1 [-r $2 ...]    # stdin → encriptado stdout  │
│   elif ARCHIVE_PASSWORD no vacío:                                │
│       exec openssl enc -aes-256-cbc -pbkdf2 -iter 100000 \       │
│           -pass env:ARCHIVE_PASSWORD                             │
│   else:                                                          │
│       exec cat                       # passthrough sin cifrar    │
│                                                                  │
│   crypto_extension():                                            │
│       echo "age" if recipients else ("enc" if password else "")  │
└──────────────────────────────────────────────────────────────────┘

archive.sh:        tar | zstd | encrypt_pipe | rclone rcat
db_archive.sh:     <dump> | zstd | encrypt_pipe | rclone rcat

decrypt_pipe (restore):
   if path matches *.age:    age -d -i $AGE_IDENTITY_FILE
   elif path matches *.enc:   openssl enc -d -aes-256-cbc -pbkdf2 ...
   else:                       cat
```

## Configuración

En `snapshot.local.conf`:

```bash
# ── Sub-F: cifrado con age (opt-in) ─────────────────────────────
# Recipients públicos (uno o más, space-separated). Si está seteado,
# age toma precedencia sobre ARCHIVE_PASSWORD para los backups nuevos.
# Genera el keypair con: snapctl crypto-keygen
ARCHIVE_AGE_RECIPIENTS=""

# Para restaurar backups encriptados con age:
# Path al archivo de identidad (la privada). Mode 0600. Solo se usa en
# restore — los backups en producción no deben tener la privada en disco.
ARCHIVE_AGE_IDENTITY_FILE=""
```

`Config` (Python) expone:
- `ARCHIVE_AGE_RECIPIENTS: str` (string crudo, parseado en bash)
- `ARCHIVE_AGE_IDENTITY_FILE: str`

## Bundle de binario

Versión pinneada en `install.sh`:

```bash
: "${AGE_VERSION:=v1.2.1}"
```

URLs (tarball estático Go):

```
https://github.com/FiloSottile/age/releases/download/${AGE_VERSION}/
  age-${AGE_VERSION}-linux-amd64.tar.gz   (o linux-arm64)
```

Extracción a `bundle/bin/age` y `bundle/bin/age-keygen` (vienen ambos
en el tarball). Idempotente igual que restic/rclone (skip si la
versión bundled coincide).

## Helper bash (`core/lib/crypto.sh`)

Funciones públicas (todas pure-shell, sin estado):

| Función | Returns | Comportamiento |
|---|---|---|
| `crypto_mode` | echo `age` / `openssl` / `none` | Decide qué encryption usar según config |
| `crypto_extension` | echo `age` / `enc` / `""` | Extensión a appendear al filename |
| `crypto_encrypt_pipe` | exec process replacing | Ejecuta el cifrado leyendo stdin → stdout |
| `crypto_decrypt_for_path` | exec process replacing | Detecta formato del path y decifra |

Las funciones que envuelven el pipe usan `exec` (no `cat | $cmd`) para
no introducir un proceso shell intermedio que rompería pipefail
detection.

## Cambios al código existente

| Archivo | Cambio |
|---|---|
| `core/lib/crypto.sh` (nuevo) | Helper con las 4 funciones |
| `core/lib/archive.sh` | Reemplazar el bloque inline `if encrypted then openssl else passthrough` por `crypto_encrypt_pipe`; usar `crypto_extension` para la extensión |
| `core/lib/db_archive.sh` | Idem |
| `core/bin/snapctl` | Source `crypto.sh`; nuevo subcomando `crypto-keygen` |
| `install.sh` | Bajar `age` + `age-keygen` al bundle (idempotente) |
| `core/etc/snapshot.local.conf.example` | Bloque `ARCHIVE_AGE_*` |
| `backend/config.py` | Exponer `ARCHIVE_AGE_RECIPIENTS` y `ARCHIVE_AGE_IDENTITY_FILE` (lectura sin secretos) |
| `README.md` | Sección "Cifrado de backups (openssl vs age)" |

## Restore con age

Flujo desde la CLI:

```bash
# Configurar la privada en local.conf:
ARCHIVE_AGE_IDENTITY_FILE="/var/lib/snapshot-v3/age-identity.txt"

# Asegurar permisos:
chmod 600 /var/lib/snapshot-v3/age-identity.txt
chown root:root /var/lib/snapshot-v3/age-identity.txt

# Ahora restore detecta automáticamente que el path es .age
# y usa age -d -i $ARCHIVE_AGE_IDENTITY_FILE:
snapctl archive-restore <remote_path> --target /tmp/restore
snapctl db-archive-restore <remote_path> --target mydb
```

## CLI `snapctl crypto-keygen`

```
$ sudo snapctl crypto-keygen

Generando age keypair...

PUBLIC KEY (pegar en ARCHIVE_AGE_RECIPIENTS):
  age1xyz...

PRIVATE KEY (anotar AHORA — no se vuelve a mostrar):
  AGE-SECRET-KEY-1ABC...

⚠  Recomendaciones:
   - Pegá la pública en /etc/snapshot-v3/snapshot.local.conf
   - Guardá la privada en un gestor de contraseñas o sobre sellado
   - NUNCA escribas la privada en este host (excepto temporalmente
     para restore, en /var/lib/snapshot-v3/age-identity.txt 0600)
```

Implementación: ejecuta `bundle/bin/age-keygen` que ya emite ambos
en el formato correcto; el bash wrapper solo agrega el banner.

## Plan de testing

- **Unit Python**: parsing de `ARCHIVE_AGE_RECIPIENTS` (cuántos
  recipients, formato).
- **Integration bash**: round-trip `cat fixture | crypto_encrypt_pipe |
  crypto_decrypt_for_path` produce el mismo bytes para los 3 modos
  (age, openssl, passthrough).
- **Bash syntax** (`bash -n`).
- **Manual smoke**: `snapctl crypto-keygen` genera keypair que age
  puede usar; archive con recipients válidos sube `.age` a Drive;
  restore con identity file recupera intacto.

## Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| Operador pierde la privada de age | Recomendar 2+ recipients (operacional + escrow) — múltiples privadas pueden decifrar |
| Operador deja la privada en `snapshot.local.conf` por error | `ARCHIVE_AGE_RECIPIENTS` documentado como **público**; identity file separado y opcional, solo para restore |
| Versión de age cambia el formato | age usa Apache 2.0 + spec estable v1.x, archivos retro-compatibles desde 1.0 |
| Mixing modes durante migración | Helper detecta por extensión en restore; no hay ambigüedad |

## Métricas de éxito

- `snapctl crypto-keygen` genera un keypair en <100ms.
- Archive `.tar.zst.age` se descomprime + descifra en restore con la
  privada y produce bytes idénticos al input original.
- Cero regresión en backups viejos (`.enc` siguen restaurándose con
  `ARCHIVE_PASSWORD`).
- `bash -n` pasa para todos los archivos modificados.

## Resumen en una frase

Sub-F agrega `age` como alternativa opt-in a `openssl` para cifrar
archives y db-archives, con bundle del binario, helper centralizado,
auto-detección por extensión en restore, y CLI `snapctl crypto-keygen`
para generar keypairs — sin romper backups existentes.
