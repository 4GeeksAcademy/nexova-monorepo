# Auditoría de Serialización del Backend

Estado del backend `services/api` respecto al contrato de serialización de respuestas (Pydantic `response_model` explícito en cada endpoint, sin objetos ORM/dict en crudo, sin campos sensibles filtrados).

**Fecha de la auditoría inicial:** 2026-09-10
**Fecha de implementación (Fase 2):** 2026-09-10
**Fecha de verificación (Fase 3):** 2026-09-10
**Rama:** `feature/serialization-audit`

## Resumen ejecutivo

Estado inicial (Fase 1):

| Estado | Cantidad |
| --- | --- |
| ✅ Ya serializado | 19 |
| ⚠️ Parcialmente serializado | 7 |
| ❌ Sin serializar | 1 |
| **Total endpoints** | **27** |

Estado final tras Fase 2 + Fase 3: **26 ✅ + 1 N/A** (el export CSV, que nunca aplicó `response_model` por no ser JSON). Todos los cambios quedaron verificados manualmente contra un servidor real (ver sección "Verificación (Fase 3)" al final).

Hallazgo prioritario: **`POST /users` (registro público) devuelve `UserRead`, que incluye `email`.** El registro es un flujo de auth no autenticado y, según el estándar acordado, no debe reenviar el email en el body de la respuesta. Es el único caso donde un endpoint ya "serializado" (tiene `response_model`) está exponiendo un campo que no debería.

Segundo hallazgo: **`POST /api/incidents/analyze` devuelve un `dict` sin tipar** — es el único endpoint realmente sin serializar de toda la API.

El resto de brechas (`⚠️`) son endpoints que devuelven `dict[str, str]` / `dict[str, bool]` sin `response_model` declarado — funcionalmente correctos y sin fuga de datos sensibles, pero sin contrato explícito.

---

## Auth — `/auth` (`routes/auth.py`)

> Rutas de mayor riesgo por definición: login, registro y reseteo de contraseña. Revisadas primero.

| Endpoint | Método | Propósito | Respuesta actual | Estado |
| --- | --- | --- | --- | --- |
| `/auth/login` | POST | Login, devuelve JWT | `response_model=Token` (`access_token`, `token_type`) | ✅ |
| `/auth/me` | GET | Usuario autenticado + su perfil | `response_model=MeRead` (`email`, `role`, `profile: ProfileRead`) | ✅ — `email` permitido aquí (vista de perfil del propio usuario) |
| `/auth/forgot-password` | POST | Envía email de reset si el usuario existe | `response_model=MessageResponse` | ✅ *(fix aplicado)* |
| `/auth/reset-password` | POST | Consume token, fija nueva contraseña | `response_model=MessageResponse` | ✅ *(fix aplicado)* |
| `/auth/change-password` | POST | Cambia contraseña de la sesión activa | `response_model=MessageResponse` | ✅ *(fix aplicado)* |

**Verificación de contraseñas:** ninguna ruta de auth devuelve `hashed_password` ni contraseña en texto plano. ✅ correcto en todas.

**Cambio aplicado:** nuevo esquema compartido `MessageResponse {detail: str}` en `models.py`, aplicado a las 3 rutas anteriores (`routes/auth.py`).

---

## Users — `/users` (`routes/users.py`)

| Endpoint | Método | Propósito | Respuesta actual | Estado |
| --- | --- | --- | --- | --- |
| `/users` | POST | Registro público (crea `User` + `Profile`) | `response_model=UserRegistered` → `id`, `is_active`, `role`, `created_at` (sin `email`) | ✅ *(fix aplicado)* |
| `/users` | GET | Lista todos los usuarios | `response_model=list[UserRead]` | ✅ (ver nota) |
| `/users/{user_id}` | GET | Detalle de un usuario | `response_model=UserRead` | ✅ |
| `/users/{user_id}` | PUT | Actualiza email/role | `response_model=UserRead`, input `UserUpdate` (separado) | ✅ |
| `/users/{user_id}` | DELETE | Borra usuario + su perfil | `response_model=DeleteResponse` | ✅ *(fix aplicado)* |

**`POST /users` — cambio aplicado:** nuevo esquema `UserRegistered` (`models.py`) sin `email`, aplicado en `routes/users.py`. El registro ya no reenvía el email en el body, consistente con login/forgot/reset.

**Nota sobre `GET /users`:** el schema en sí es correcto (`UserRead` no expone `hashed_password`), pero cualquier usuario autenticado —no solo admins— puede listar el email de todos los usuarios de la empresa. Es una cuestión de autorización, no de forma del payload, así que queda fuera del alcance estricto de esta auditoría de serialización, pero se deja anotado como recomendación a revisar aparte.

**`DELETE /users/{user_id}` — cambio aplicado:** nuevo esquema compartido `DeleteResponse {deleted: bool}` (`models.py`).

---

## Profiles — `/profiles` (`routes/profiles.py`)

| Endpoint | Método | Propósito | Respuesta actual | Estado |
| --- | --- | --- | --- | --- |
| `/profiles/me` | GET | Perfil del usuario autenticado | `response_model=ProfileRead` | ✅ |
| `/profiles/me` | PUT | Actualiza perfil propio | `response_model=ProfileRead`, input `ProfileUpdate` (separado) | ✅ |

Sin cambios necesarios. Buen ejemplo de esquemas de entrada/salida ya desacoplados.

---

## Suppliers — `/suppliers` (`routes/suppliers.py`)

| Endpoint | Método | Propósito | Respuesta actual | Estado |
| --- | --- | --- | --- | --- |
| `/suppliers` | POST | Crea proveedor | `response_model=SupplierRead` (campos explícitos) | ✅ *(refactor aplicado)* |
| `/suppliers` | GET | Lista proveedores (filtros `country`, `category`) | `response_model=list[SupplierRead]` | ✅ *(refactor aplicado)* |
| `/suppliers/{supplier_id}` | GET | Detalle de proveedor | `response_model=SupplierRead` | ✅ *(refactor aplicado)* |
| `/suppliers/{supplier_id}/rate` | PATCH | Actualiza tarifa mensual | `response_model=SupplierRead`, input `RateUpdate` (separado) | ✅ |
| `/suppliers/{supplier_id}/status` | PATCH | Actualiza estado (active/suspended) | `response_model=SupplierRead`, input `StatusUpdate` (separado) | ✅ |
| `/suppliers/{supplier_id}` | DELETE | Borra proveedor | `response_model=DeleteResponse` | ✅ *(fix aplicado)* |

**Refactor aplicado:** `SupplierRead` dejó de heredar de `SupplierCreate` y ahora declara sus propios campos explícitos (`routes/suppliers.py`), desacoplando el contrato de lectura del de escritura.

**`DELETE /suppliers/{supplier_id}` — cambio aplicado:** reutiliza el `DeleteResponse` compartido.

---

## Incidents — `/api/incidents` (`app/routers/incidents.py`)

| Endpoint | Método | Propósito | Respuesta actual | Estado |
| --- | --- | --- | --- | --- |
| `/api/incidents/analyze` | POST | Analiza un CSV subido, devuelve resumen | `response_model=IncidentAnalysisSummary` | ✅ *(fix aplicado)* |
| `/api/incidents/results/export` | GET | Descarga el último análisis en CSV | `Response` (`text/csv`, `Content-Disposition: attachment`) | ✅ — no aplica `response_model` (no es JSON), correcto tal cual |

**Cambio aplicado:** nuevo módulo `app/schemas.py` con `IncidentAnalysisSummary` y sub-esquemas, modelando exactamente la forma que produce `build_summary()` (verificado contra `packages/incidents-analyzer/incidents_analyzer/analyzer.py` y validado con una ejecución real del analyzer):

```
IncidentAnalysisSummary
├── source_file: str
├── total_records: int
├── valid_records: int
├── invalid_records: int
├── invalid_breakdown: list[InvalidBreakdownItem]      # {rule, label, count}
├── category_breakdown: list[CategoryBreakdownItem]    # {category, count, percentage}
├── status_breakdown: list[StatusBreakdownItem]        # {status, count, percentage}
└── satisfaction: SatisfactionSummary
    ├── closed_tickets: int
    ├── scored_tickets: int
    ├── average: float
    └── distribution: list[SatisfactionDistributionItem]  # {score, label, count}
```

No hay campos sensibles que filtrar aquí (el propio análisis ya excluye emails por diseño), es puramente un problema de falta de tipado/contrato.

---

## Inventory — `/inventory` (`routers/inventory.py`)

| Endpoint | Método | Propósito | Respuesta actual | Estado |
| --- | --- | --- | --- | --- |
| `/inventory/products` | GET | Lista assets con stock calculado | `response_model=list[AssetRead]` | ✅ |
| `/inventory/products` | POST | Crea asset | `response_model=AssetRead`, input `AssetCreate` (separado) | ✅ |
| `/inventory/products/{asset_id}` | GET | Detalle de asset | `response_model=AssetRead` | ✅ |
| `/inventory/orders/inbound` | POST | Registra entrada de stock | `response_model=AssetEntryRead`, input `AssetEntryCreate` (separado) | ✅ |
| `/inventory/orders/outbound` | POST | Registra salida de stock | `response_model=AssetExitRead`, input `AssetExitCreate` (separado) | ✅ |
| `/inventory/orders` | GET | Lista unificada de entradas + salidas | `response_model=list[OrderRead]` | ✅ |

Ningún cambio necesario. Es el dominio mejor resuelto de toda la API — vale la pena citarlo como referencia:

- **Relación anidada decidida explícitamente:** `OrderRead.asset` usa `AssetSummary` (`id`, `name`, `sku`), no el `Asset` completo ni solo el `asset_id` — el cliente necesita mostrar nombre/SKU en el listado de órdenes sin pagar el coste de anidar el objeto completo. Ejemplo directo de "proyección plana" bien aplicada.
- **Campo calculado nunca persistido:** `current_stock` se computa en la capa de rutas (`_current_stock` / `_stock_by_asset`) y solo aparece en los esquemas de salida — nunca en los modelos ORM (`Asset`, `AssetEntry`, `AssetExit` no tienen esa columna).
- **`user_uuid` expuesto en `AssetEntryRead` / `AssetExitRead` / `OrderRead`:** es una FK a TinyDB (no un campo secreto), necesaria para trazabilidad de quién creó cada movimiento. Se documenta como decisión intencional, no como fuga de datos.

---

## Health — `/api/health` (`app/main.py`)

| Endpoint | Método | Propósito | Respuesta actual | Estado |
| --- | --- | --- | --- | --- |
| `/api/health` | GET | Healthcheck | `response_model=HealthResponse` | ✅ *(fix aplicado)* |

Sin riesgo de exposición de datos; se añadió `HealthResponse {status: str}` (`models.py`) por completitud, ya que el criterio pide `response_model` explícito sin excepciones.

---

## Cambios aplicados (Fase 2)

| Cambio | Endpoints afectados | Tipo | Archivo(s) |
| --- | --- | --- | --- |
| Nuevo esquema `UserRegistered` (sin `email`) | `POST /users` | Fix de exposición de campo — **prioritario** | `models.py`, `routes/users.py` |
| Nuevo esquema `IncidentAnalysisSummary` + sub-esquemas | `POST /api/incidents/analyze` | Tipado completo — único endpoint sin serializar | `app/schemas.py` (nuevo), `app/routers/incidents.py` |
| Nuevo esquema compartido `MessageResponse {detail: str}` | `forgot-password`, `reset-password`, `change-password` | Declarar `response_model` | `models.py`, `routes/auth.py` |
| Nuevo esquema compartido `DeleteResponse {deleted: bool}` | `DELETE /users/{id}`, `DELETE /suppliers/{id}` | Declarar `response_model` | `models.py`, `routes/users.py`, `routes/suppliers.py` |
| Nuevo esquema `HealthResponse {status: str}` | `GET /api/health` | Declarar `response_model` | `models.py`, `app/main.py` |
| Refactor `SupplierRead` a campos explícitos (sin heredar de `SupplierCreate`) | `POST/GET/GET{id}/PATCH×2 /suppliers` | Desacoplar input/output | `routes/suppliers.py` |

Endpoints sin cambios (ya cumplían el estándar en Fase 1): `login`, `/auth/me`, `GET/GET{id}/PUT /users`, `/profiles/me` (GET/PUT), todo `/inventory` (6 endpoints), `GET /api/incidents/results/export`.

---

## Estado tras Fase 2

Los 26 endpoints JSON de la API tienen ahora un `response_model` Pydantic explícito. El único endpoint sin `response_model` (`GET /api/incidents/results/export`) es intencional: devuelve un CSV vía `Response`, no JSON.

Verificado con la generación real del `openapi.json` de la app (todos los `response_model` resuelven sin errores) y con una ejecución real del `incidents_analyzer` contra `IncidentAnalysisSummary` (valida sin discrepancias de campos).

No existen tests automatizados en `services/api`, así que la verificación se hizo manualmente (ver detalle abajo).

---

## Verificación (Fase 3)

Servidor levantado localmente (`uvicorn`) con las variables reales de `.env` (`SECRET_KEY`, JWT, etc.) y `DATABASE_URL` apuntando a una SQLite descartable — el `.env` del repo trae un `DATABASE_URL` de Postgres con valor placeholder, no una credencial real de Supabase, así que no era usable para esta verificación; se optó por SQLite en vez de depender de esa credencial. Todos los endpoints tocados en Fase 2 se probaron con peticiones HTTP reales (`curl`), más una pasada de regresión sobre los endpoints no modificados (`/users`, `/profiles/me`, `/inventory/*`) contra los datos TinyDB reales del repo.

| Endpoint probado | Resultado |
| --- | --- |
| `GET /api/health` | `{"status":"ok"}` — `HealthResponse` ✅ |
| `POST /users` (registro) | Respuesta sin `email`: `{"id", "is_active", "role", "created_at"}` ✅ — fix confirmado |
| `POST /auth/login` + `GET /auth/me` | Token válido; `/me` sí devuelve `email` (correcto, es la vista de perfil propio) ✅ |
| `POST /auth/forgot-password` | `{"detail": "..."}` vía `MessageResponse` ✅ |
| `POST /auth/change-password` | `{"detail": "..."}` vía `MessageResponse` ✅ |
| `POST /auth/reset-password` | Token generado directamente (el envío de email está desactivado en local), `{"detail": "..."}` vía `MessageResponse` ✅ |
| `POST/GET/PATCH/DELETE /suppliers` | `SupplierRead` (campos explícitos) correcto en creación, detalle, listado (incluyendo los 16 proveedores reales ya existentes) y update de tarifa; `DELETE` devuelve `DeleteResponse` ✅ |
| `POST /api/incidents/analyze` | CSV de prueba → `IncidentAnalysisSummary` con todos los campos y sub-esquemas correctos ✅ |
| `GET /api/incidents/results/export` | Sigue devolviendo el CSV plano sin cambios ✅ |
| `DELETE /users/{id}` | `{"deleted": true}` vía `DeleteResponse` ✅ |
| `GET/GET{id}/PUT /users`, `GET/PUT /profiles/me` | Sin regresiones — mismas respuestas que antes de Fase 2 ✅ |
| `GET/POST /inventory/products`, `POST /inventory/orders/{inbound,outbound}`, `GET /inventory/orders` | Sin regresiones — `current_stock` calculado correctamente, `OrderRead` con `AssetSummary` anidado ✅ |

Los usuarios y el proveedor creados durante la prueba se borraron con los propios endpoints `DELETE` probados; los archivos TinyDB reales (`data/users.json`, `data/profiles.json`, `data/suppliers.json`) quedaron sin diferencias tras la verificación.

**Conclusión:** los 26 endpoints JSON de la API declaran un `response_model` explícito y su comportamiento se verificó contra un servidor real, sin regresiones en los endpoints no modificados. La auditoría de serialización se considera completa.
