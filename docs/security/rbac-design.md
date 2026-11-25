# Roles y permisos (RBAC): rol = capacidad, departamento = alcance de datos

Ticket de Legal: hasta ahora cualquier usuario autenticado tenía casi el mismo acceso. Esta rama separa **dos ejes
independientes** y los aplica **en el backend** (el frontend solo oculta lo que el rol no permite).

## Roles (eje de capacidad) — «¿qué puede hacer?»

| `code` | Rango | Capacidades |
|---|---|---|
| `employee` | 1 | Crear y leer registros de **su departamento**. No aprueba, no elimina, no administra. |
| `supervisor` | 2 | Todo lo de Empleado + actualizar/aprobar en **su departamento**. No elimina ni administra. |
| `admin` | 3 | Todo lo de Supervisor + eliminar, administrar usuarios/roles/departamentos. **Ve todos los departamentos** (privilegio del rol; sigue perteneciendo a `technology`). |

El rango es estrictamente jerárquico (`employee < supervisor < admin`); `PUT /roles/{id}` rechaza rangos repetidos o que
saquen a `admin` de la cima. Cambiar el departamento de un usuario nunca toca su rango.

Códigos heredados (datos sin migrar): `manager` equivale a `supervisor`; `user` es una **cuenta externa** (cliente, rango 0,
sin acceso interno) y es lo único que crea el registro público `POST /auth/register`.

## Departamentos (eje de alcance) — «¿qué información le compete?»

`talent_selection`, `corporate_training`, `customer_support`, `sales`, `marketing`, `human_resources`, `technology`.
No otorgan acciones: dos supervisores de distinto departamento tienen las mismas capacidades pero no ven los datos del otro.

## Independencia

`User.role` (id del rol) y `User.department_id` son claves foráneas separadas. `PATCH /users/{id}/assignment` solo cambia lo que
viene en el cuerpo; el otro eje queda intacto (`department_id: null` quita el departamento). Ids = `code` (clave natural).

## Dónde se decide (una sola vez)

`services/api/authz.py`, en este orden: **primero el rol, después el departamento**.

- `require_role(minimo)` (`require_employee`, `require_supervisor`, `require_admin`): compara rangos; 403 con el rol exigido en el cuerpo.
- `department_scope` / `ensure_department_access`: Admin = todos; resto = solo el suyo. GET por id de otro departamento → 403;
  los listados se filtran (alcance, no denegación). Sin departamento asignado → 403 explícito, nunca lista vacía.
- Ninguna ruta compara roles a mano: `tests/rbac/test_legal_requirements.py` lo verifica sobre el código.
- `agent/guardrails/tenancy.py` deriva «personal interno» del rango (≥ 1), así que agente, MCP e incidencias siguen una sola definición.

## Mapa de endpoints

| Recurso | Rol mínimo | Alcance departamental |
|---|---|---|
| `GET /auth/me` | autenticado | no (devuelve rol, rango y departamento) |
| `POST /auth/register` | público | crea siempre `user` externo |
| `GET/POST /users`, `DELETE /users/{id}` | `admin` | no |
| `PATCH /users/{id}/assignment` | `admin` | no |
| `/roles`, `/departments` (GET/POST/PUT) | `admin` | no |
| `GET/POST /internal-reports` | `employee` | **sí** (admin: todos) |
| `PUT /internal-reports/{id}` | `supervisor` | **sí** |
| `DELETE /internal-reports/{id}` | `admin` | — |
| `POST /suppliers` | `employee` | — |
| `PATCH /suppliers/{id}/rate\|status` | `supervisor` | — |
| `DELETE /suppliers/{id}` | `admin` | — |
| `PATCH /api/incidents/{id}/status` | `supervisor` | — |
| `POST /reporting/pipeline-runs` | `supervisor` | — |
| RFP: aprobar, reabrir, regenerar borradores | `supervisor` | — |
| Lecturas de inventario, proveedores (alta), RFP, reporting, telemetría, knowledge | `employee` (personal interno) | — |

## Lo que NO está hecho (alcance acotado)

- Inventario, proveedores e incidencias **no** tienen `department_id` todavía: el BRIEFING pide el alcance «cuando el registro tiene
  departamento». Etiquetarlos requiere una migración SQL manual en Supabase (`012_department_scope.sql`) y es la siguiente fase.
- `GET /inventory/products` y `GET /suppliers` siguen sin autenticación (preexistente; ver informe OWASP).
- Los managers heredados pasan a `supervisor` con `scripts/migrate_roles_departments.py` y quedan **sin departamento** hasta que un
  admin se lo asigne (hasta entonces reciben 403 en datos departamentales).

## Operación

```powershell
uv run --project services/api python scripts/migrate_roles_departments.py --dry-run   # simula
uv run --project services/api python scripts/migrate_roles_departments.py             # aplica (haz copia de users.json antes)
$env:RBAC_SEED_PASSWORD = "<contraseña de desarrollo>"
uv run --project services/api python scripts/seed_rbac.py                              # roles, deptos, 5 usuarios y 2 informes del BRIEFING
```

La cuenta de servicio del MCP (`NEXOVA_API_EMAIL`) debe tener rol `supervisor` o superior para crear/cambiar el estado de tickets.

## Evidencia (los 5 casos de Legal, llamadas directas a la API)

`uv run --project services/api pytest tests/rbac/test_legal_requirements.py -v`

```
test_1_employee_cannot_call_a_supervisor_only_update ........ PASSED   (403: «Rol insuficiente: se requiere «supervisor» o superior (tu rol: «employee»).»)
test_2_supervisor_cannot_call_admin_only_endpoints .......... PASSED   (GET /roles, /departments, /users y PATCH assignment → 403)
test_3_javier_cannot_read_megans_sales_report_by_id ......... PASSED   (403 «Este recurso pertenece a otro departamento.»; Megan → 200)
test_4_role_and_department_are_independent_axes ............. PASSED
test_4b_changing_both_axes_at_once_and_unrelated_fields ..... PASSED
test_5_permission_checks_live_in_a_dependency_not_in_route .. PASSED
test_every_sensitive_route_declares_a_role_dependency ....... PASSED
```

Además `tests/rbac/test_access_matrix.py` (rol × departamento × recurso) y `tests/rbac/test_admin_management.py` (semillas, migración,
gestión de roles/departamentos, último admin protegido).
