# Definición del proyecto — snapshot-V3

## Qué es

snapshot-V3 es un sistema de backups multi-nivel para servidores Linux,
con panel web propio, CLI propia y agregación opcional de múltiples
clientes en una instalación central. Está pensado para PYMEs que
administran su propio fleet de Ubuntu/Debian y quieren backups firmados
hacia Google Drive (o cualquier remote rclone) sin contratar SaaS.

## Problema que resuelve

| Pain point típico | Cómo lo aborda snapshot-V3 |
|---|---|
| `tar.gz` + cron + `rclone copy` requieren reescribirse en cada cliente. | Un instalador único (`install.sh`) que rinde sobre cualquier Ubuntu 22.04+/24.04. |
| Visibilidad cero del estado de backups en producción. | Panel Flask con dashboard, listado de archivos remotos, logs en vivo. |
| Restaurar requiere SSH y comandos manuales. | "Restaurar" desde la UI o `snapctl restore`, con preview. |
| Cifrado opcional con password compartida → fuga de password = todos los backups expuestos. | Soporte nativo para [age](https://age-encryption.org) (clave pública/privada), con la privada que nunca vive en el servidor. |
| Cuentas root compartidas para entrar al panel. | Sub-A: usuarios locales con argon2id, roles (admin/operator/auditor), TOTP MFA obligatorio para admin. |
| Backups que fallan en silencio durante meses. | Sub-D: alertas (no_heartbeat / folder_missing / backup_shrink) con email y webhook. |
| Sin forma de ver el estado de N clientes simultáneamente. | Sub-B: deploy "central" que recibe heartbeats firmados de cada cliente y los muestra en `/dashboard-central`. |
| Backups de bases de datos requieren scripts paralelos. | Sub-E: `snapctl db-archive` con streaming directo `pg_dump | zstd | crypto | rclone rcat` para Postgres/MySQL/Mongo. |

## Alcance funcional

### Sub-proyectos entregados (todos en `main`)

- **A — Auth + RBAC:** login, sesiones server-side, MFA TOTP, password reset, audit log, `snapctl admin` CLI.
- **B — Deploy dual:** mismo binario, dos modos. `MODE=client` envía heartbeats; `MODE=central` los recibe y agrega.
- **C — Dashboard + scheduler UI:** vistas agregadas en central + edición del timer mensual desde la UI.
- **D — Alertas:** detección automática + dispatch SMTP/webhook + UI en central.
- **E — DB backups:** Postgres / MySQL / Mongo, streaming, sin disco intermedio.
- **F — Cifrado age:** alternativa robusta a `ARCHIVE_PASSWORD`, opt-in, con keygen desde el panel.

### Fuera de alcance (de momento)

- **Restic.** El motor de archivos mensuales (`archive`) usa `tar | zstd | crypto | rclone rcat`, no restic. Restic queda solo para snapshots incrementales locales (legacy).
- **Replicación cross-region.** Un solo `rclone remote` por host.
- **Multi-tenant en el panel.** El panel web sirve a UNA organización. La separación se hace por carpeta taxonómica en el shared Drive.
- **Encryption at rest de la SQLite.** El DB del panel se asume protegido por permisos del FS (`/var/lib/snapshot-v3` mode 700, owned by root).

## Audiencia

- **Operador único** (rol `admin`/webmaster): instala, configura, restaura, gestiona usuarios.
- **Técnico** (rol `operator`): puede crear archivos manualmente, ver logs, restaurar — no puede gestionar usuarios.
- **Gerente / auditor** (rol `auditor`): solo lectura — dashboard, listados, logs. Útil para cumplimiento sin riesgo de modificación.

En modo central se mantienen los mismos roles; el operador del central
ve a TODOS los clientes desde una sola UI, los clientes nunca se ven
entre sí.

## Decisiones de diseño clave

1. **Bash + Python.** El motor de backups es bash (`snapctl`) para ser
   inspeccionable a ojo y debuggeable con `bash -x`. El panel es Flask
   porque es trivial de operar y empacar.
2. **SQLite, no Postgres.** El panel maneja MB de datos, no GB. WAL
   mode + un único proceso = sin contention. Sin servidor de DB
   externo.
3. **Streaming, no archivos temporales.** `dump | zstd | crypto | rclone
   rcat` evita usar disco como buffer. Crítico cuando el host tiene
   `/var` chico.
4. **Heartbeats firmados, no shared filesystem.** El central nunca lee
   los archivos de los clientes — solo recibe metadatos firmados con
   un token Bearer. Así una credencial del central no compromete
   ningún backup.
5. **Local config sobre env vars.** Toda la configuración vive en
   `/etc/snapshot-v3/snapshot.local.conf` (mode 0600). Sobrevive a
   re-deploys de la app porque está fuera de `/opt/snapshot-V3`.

## Referencias internas

- [Arquitectura y stack técnico](arquitectura_y_stack.md)
- [Esquema de base de datos y roles](base_datos_y_roles.md)
- [Catálogo de API HTTP](api.md)
- [Configuración detallada](configuracion.md)
- [Deployment y operación](deployment.md)
- [Guía de uso del panel](user-guide.md)
- [Casos de uso concretos](use-cases.md)
