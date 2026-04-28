# Documentación — snapshot-V3

| Documento | Para qué |
|---|---|
| [`definicion_proyecto.md`](definicion_proyecto.md) | Qué es, por qué existe, alcance, decisiones clave |
| [`arquitectura_y_stack.md`](arquitectura_y_stack.md) | Stack técnico, capas, diagramas de componentes |
| [`base_datos_y_roles.md`](base_datos_y_roles.md) | Schema SQLite, roles, MFA, permisos |
| [`api.md`](api.md) | Catálogo HTTP completo (Auth + API + Audit + Central) |
| [`configuracion.md`](configuracion.md) | Variables, archivos de config, qué requiere restart |
| [`deployment.md`](deployment.md) | Instalación, modos cliente/central, TLS, upgrades |
| [`user-guide.md`](user-guide.md) | Guía de uso del panel desde la perspectiva del operador |
| [`use-cases.md`](use-cases.md) | 10 escenarios concretos resueltos paso-a-paso |

## Lectura recomendada

- **Si vas a evaluar el proyecto:** `definicion_proyecto.md` → `arquitectura_y_stack.md` → `use-cases.md`
- **Si vas a desplegarlo:** `deployment.md` → `configuracion.md` → `user-guide.md`
- **Si vas a operarlo día-a-día:** `user-guide.md` → `use-cases.md`
- **Si vas a integrarlo / escribir clientes:** `api.md` → `base_datos_y_roles.md`

## Carpeta `superpowers/`

Las subcarpetas `superpowers/specs/` y `superpowers/plans/` contienen
los specs y plans originales de cada sub-proyecto (A-F). Útiles como
referencia histórica del por qué de cada decisión:

- `2026-04-27-auth-rbac-design.md` — sub-A
- `2026-04-27-dual-deploy-design.md` — sub-B
- `2026-04-27-central-alerts-design.md` — sub-D
- `2026-04-27-db-archive-design.md` — sub-E
- `2026-04-28-crypto-hardening-design.md` — sub-F
