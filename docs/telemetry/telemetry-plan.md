# Plan de Telemetría — Nexova

**Estado:** Diseño aprobado, captura implementada (`uis/backoffice/lib/telemetry.ts` + `log_telemetry_event` en `services/api`) y almacenamiento en Supabase implementado (`telemetry_events`, ver §4). Este documento y `event-schemas.json` siguen siendo el contrato: el backend lee los allowlists directamente del JSON.

**Alcance de esta primera entrega:** `services/api` (FastAPI + Supabase/SQLModel + TinyDB) y `uis/backoffice` (Next.js). No cubre `packages/incidents-analyzer` como librería en sí, solo su uso desde el router `app/api/incidents`.

---

## 0. Resumen

| | |
|---|---|
| Eventos totales diseñados | **27** |
| Obligatorios (de `CONTEXT-nexova-telemetry.es.md`) | **5** |
| Identificados por esta auditoría | **22** |
| Categorías cubiertas | negocio-inventario, negocio-proveedores, autenticación, rendimiento, errores, navegación (**6**) |
| Eventos en modo *stream* | 10 |
| Eventos en modo *batch* | 17 |

El catálogo completo, clasificado, está en la sección 2. El detalle campo a campo de cada evento vive en [`event-schemas.json`](./event-schemas.json) — este documento no duplica esa información, la referencia.

---

## 1. Event Envelope

Todo evento, sin excepción, se emite con este envoltorio. Definido una sola vez en `event-schemas.json` bajo `envelope.fields` y heredado por los 27 eventos.

| Campo | Tipo | Obligatorio | Descripción |
|---|---|---|---|
| `eventId` | string (UUID v4) | sí | Identificador único del evento, generado en el punto de emisión (no en el pipeline). |
| `timestamp` | string (ISO 8601, UTC) | sí | Momento de ocurrencia del evento, no de su envío/ingesta. |
| `event_type` | string (`entidad_acción`) | sí | Taxonomía consistente, ej. `inbound_order_created`. Ver listado completo en §2. |
| `schemaVersion` | string (semver) | sí | Versión del schema de `properties` para este `event_type`. Arranca en `1.0.0` para los 27 eventos de esta entrega. |
| `sessionId` | string (UUID) \| `null` | sí (clave presente) | Sesión de backoffice, generada por el frontend al hacer login y reenviada en cada llamada a la API vía header `X-Session-Id`. `null` solo en: (a) eventos pre-login (`login_failed` sin sesión previa), (b) procesos internos sin frontend (ninguno en este catálogo hoy). |
| `userId` | string (UUID de TinyDB) \| `null` | sí (clave presente) | `null` solo cuando el evento ocurre *antes* de resolver identidad (`login_failed`) o cuando emitirlo violaría el propio propósito del evento (ver `password_reset_requested`, §3). |
| `requestId` | string \| `null` | sí (clave presente) | Correlación frontend→backend→logs. El frontend genera un UUID antes de cada llamada `fetch` a la API y lo envía como header `X-Request-Id`; el middleware de FastAPI lo recoge o lo genera si falta y lo añade a sus logs. `null` únicamente en eventos 100% cliente que no disparan ninguna llamada a la API en ese instante (ej. `backoffice_section_viewed` de una sección ya cargada). |
| `source` | `"frontend"` \| `"backend"` | sí | **Extensión sobre el mínimo pedido por el README**, añadida deliberadamente: el pipeline necesita saber de qué proceso vino el evento para enrutar/deduplicar correctamente (un mismo flujo de negocio, ej. login, puede tener un evento de cada lado). Documentado aquí para que quede claro que no es un campo improvisado sin criterio. |
| `properties` | object | sí | Payload específico del evento. **Allowlist cerrada** — ver §2 y el JSON: cualquier clave fuera de la lista documentada se descarta en el punto de emisión, no en el pipeline. |

**Normalización aplicada antes de emitir (no en el pipeline):**
- `office`: el modelo actual (`Asset.office`, `AssetEntry.office`, `AssetExit.office`) guarda `"Valencia"`/`"Miami"` con mayúscula inicial. La telemetría usa siempre el código canónico en minúscula (`valencia`/`miami`) definido en el CONTEXT. La conversión se hace en el punto de emisión, nunca se cambia el dato de negocio original.
- `currency`: se deriva de `office` (`valencia` → `EUR`, `miami` → `USD`), igual que ya hace `ProviderBase.validate_currency_for_country` en `models.py` para proveedores. Nunca se convierte de una moneda a otra en la capa de telemetría (restricción explícita del CONTEXT).

---

## 2. Catálogo de eventos (Fase 1)

### 2.1 Flujo de inventario instrumentado (mínimo 5 puntos exigido)

Flujo: *usuario autenticado → visualiza catálogo/stock → registra una orden de entrada o salida → el sistema recalcula stock → (opcional) el sistema rechaza una escritura fuera de ese camino.*

| # | Punto de instrumentación | Evento(s) |
|---|---|---|
| 1 | Acceso autenticado a una sección de inventario (`RequireAuth` supera la comprobación de token) | `backoffice_section_viewed` (`section=inventory_products` / `inventory_orders`) |
| 2 | Envío del formulario de orden de entrada (`POST /inventory/orders/inbound`) | Éxito → `inbound_order_created` · Rechazo (asset inexistente, cantidad inválida) → `inbound_order_rejected` |
| 3 | Envío del formulario de orden de salida (`POST /inventory/orders/outbound`) | Éxito → `outbound_order_created` · Rechazo por stock insuficiente u otra validación → `outbound_order_rejected` |
| 4 | Recalculo de stock tras una salida, comparado contra el mínimo configurado del producto | `stock_threshold_triggered` (solo en la transición a "por debajo del mínimo", no en cada lectura) |
| 5 | Intento de modificar stock fuera de una orden trazable | `direct_stock_edit_rejected` — **ver nota de dependencia abajo** |

> ⚠️ **Dependencia detectada, no un evento en sí:** hoy `services/api/routers/inventory.py` **no expone ningún endpoint** de edición directa de stock — la única forma de tocar stock es `AssetEntry`/`AssetExit`. Eso significa que, tal como está el backend hoy, `direct_stock_edit_rejected` nunca podría dispararse: no hay nada que rechazar. Antes de instrumentar este evento, el equipo necesita **o bien** exponer una ruta explícitamente bloqueada (ej. `PATCH /inventory/products/{id}` que siempre devuelva 403 y emita el evento) **o bien** interceptar a nivel de capa de datos cualquier intento de escritura directa sobre la columna de stock (que hoy tampoco existe como columna: `current_stock` se calcula, no se guarda). Lo documentamos aquí en vez de omitir el evento, porque es una métrica obligatoria del CONTEXT — la responsabilidad de decidir cómo exponerlo es del equipo, no de este plan.

> ⚠️ **Segunda dependencia:** `stock_threshold_triggered` requiere un umbral mínimo configurado *por producto*. `Asset` (en `models.py`) no tiene ese campo hoy. Se necesita añadir algo como `min_stock_threshold: int` a `Asset` (con un default razonable) antes de poder calcular la transición. Mismo caso para `kit_cost_variance_detected`, que requiere un `unit_cost` en `AssetEntry` — tampoco existe en el modelo actual — y una línea base histórica (media móvil de los últimos N pedidos del mismo producto+proveedor) que hoy no se calcula en ningún sitio.

### 2.2 Catálogo completo

`O` = obligatorio (CONTEXT) · `I` = identificado por esta auditoría.

#### Negocio — Inventario

| `event_type` | Clase | Hipótesis → Decisión (resumen) | Entrega |
|---|---|---|---|
| `inbound_order_created` | O | Cuánto material se compra/produce y para qué programa → planificar producción según demanda esperada (Elena) | batch |
| `outbound_order_created` | O | Qué programas consumen más material y a qué ritmo → anticipar reposición antes de una ola de matrículas (Elena) | batch |
| `stock_threshold_triggered` | O | Con qué frecuencia un programa se queda sin material → ajustar el umbral o acelerar reproducción (Elena) | **stream** |
| `direct_stock_edit_rejected` | O | Si el personal intenta saltarse la trazabilidad → reforzar capacitación/permisos por oficina (Patricia) | **stream** |
| `kit_cost_variance_detected` | O | Cuándo un proveedor sube precios de forma anómala → renegociar o buscar alterno (Elena, Laura) | **stream** |
| `product_created` | I | Con qué frecuencia crece el catálogo y en qué categoría/oficina → detectar catálogos desbalanceados, priorizar qué material diseñar después (Elena) | batch |
| `product_creation_rejected` | I | Si el personal intenta crear productos duplicados/inválidos con frecuencia → si se repite, falta un "buscar antes de crear" en el frontend | batch |
| `inbound_order_rejected` | I | Si se referencian productos inexistentes o cantidades inválidas al registrar entradas → mejorar autocompletado/validación de ese formulario | batch |
| `outbound_order_rejected` | I | Cuándo se intenta entregar más material del disponible → demanda insatisfecha *ahora mismo*, expeditar reposición o redirigir a la otra oficina (Elena, Patricia) | **stream** |

#### Negocio — Proveedores

| `event_type` | Clase | Hipótesis → Decisión (resumen) | Entrega |
|---|---|---|---|
| `supplier_created` | I | Frecuencia de altas de proveedor por país/categoría → detectar dependencia excesiva de pocos proveedores, diversificar a tiempo | batch |
| `supplier_rate_updated` | I | Frecuencia y magnitud de cambios de tarifa → alimentar la misma vigilancia de coste que `kit_cost_variance_detected`, a nivel de contrato | batch |
| `supplier_status_updated` | I | Cuántos proveedores se suspenden → disparar revisión de qué productos dependían de ese proveedor antes de que bloquee una entrada futura | **stream** |
| `supplier_deleted` | I | Cuándo se elimina un proveedor (irreversible) → rastro de auditoría para negociación futura | **stream** |

#### Autenticación

| `event_type` | Clase | Hipótesis → Decisión (resumen) | Entrega |
|---|---|---|---|
| `login_succeeded` | I | Patrones de uso del backoffice por rol/horario → dimensionar capacidad, detectar oficinas con baja adopción (Patricia, Roberto) | batch |
| `login_failed` | I | Frecuencia/patrón de credenciales incorrectas → detectar fuerza bruta o problemas de onboarding de cuentas | **stream** |
| `session_expired` | I | Cuántas sesiones expiran mientras el usuario sigue activo → decidir si subir `ACCESS_TOKEN_EXPIRE_MINUTES` o añadir refresh silencioso | batch |
| `password_reset_requested` | I | Volumen de recuperación de contraseña → detectar si un flujo de onboarding concreto genera fricción sistemática | batch |
| `password_reset_completed` | I | Tasa de conversión del flujo de reset (solicitado → completado) → si es baja, el email o el formulario tienen fricción | batch |
| `password_changed` | I | Igual que arriba pero para el cambio voluntario desde la cuenta ya autenticada | batch |

#### Rendimiento

| `event_type` | Clase | Hipótesis → Decisión (resumen) | Entrega |
|---|---|---|---|
| `api_request_completed` | I | Latencia real por ruta en producción → confirmar que la caché de `CACHING_REPORT.md` sigue rindiendo y detectar regresiones antes que el usuario se queje | **stream** (muestreado, ver §3.2) |
| `frontend_page_load_recorded` | I | LCP real por ruta y tipo de dispositivo → verificar si el `next/dynamic` aplicado en `REPORT.md`/`CACHING_REPORT.md` bajó el LCP móvil de 2.9s por debajo de 2.5s, y detectar regresiones | batch |

#### Errores

| `event_type` | Clase | Hipótesis → Decisión (resumen) | Entrega |
|---|---|---|---|
| `api_request_failed` | I | Qué rutas fallan con 5xx/excepción no controlada → priorizar qué endpoint estabilizar primero | **stream** |
| `frontend_error_captured` | I | Errores de JS no capturados por ruta → priorizar qué pantalla del backoffice es más frágil hoy | **stream** (con dedupe, ver §3.2) |
| `csv_analysis_rejected` | I | Con qué frecuencia y por qué motivo falla la subida de CSV de incidencias → si un motivo domina (ej. encoding), mejorar el mensaje o aceptar más formatos | batch |

#### Navegación

| `event_type` | Clase | Hipótesis → Decisión (resumen) | Entrega |
|---|---|---|---|
| `backoffice_section_viewed` | I | Qué secciones visitan más los operadores → priorizar qué parte del backoffice pulir/optimizar primero | batch |
| `inventory_order_form_abandoned` | I | Si el formulario de entrada/salida se abandona a medio llenar → priorizar su rediseño antes que otras mejoras de UX | batch |
| `csv_results_exported` | I | Si el análisis de incidencias se genera pero no se exporta → el formato de export quizás no es el que el equipo necesita | batch |

---

## 3. Diseño del schema (Fase 2)

El detalle campo a campo (tipo, obligatoriedad, descripción, allowlist) de los 27 eventos está en `event-schemas.json`. Aquí se documentan las decisiones transversales que ese JSON no puede expresar por sí solo.

### 3.1 Datos sensibles / PII — hallazgos concretos y cómo se resuelven

| Dato | Dónde vive hoy | Riesgo | Decisión |
|---|---|---|---|
| `AssetExit.assigned_to` (texto libre, ej. `"Laura Gómez"`) | `models.py` / `services/api/routers/inventory.py` | Es un nombre de persona (candidato, consultor o agente) — el CONTEXT prohíbe explícitamente nombres en `properties`. | **Excluido del allowlist de `outbound_order_created`.** Si más adelante se necesita segmentar por tipo de destinatario, se propone añadir `recipient_type` (`candidate`/`client`/`consultant`/`support_agent`/`internal_other`) como campo categórico nuevo — nunca el nombre. No implementado en esta entrega, solo documentado como opción futura. |
| Email en intentos de login fallidos | `routes/auth.py::login` | Guardar el email tal cual en `login_failed` sería PII innecesaria y, peor, permitiría enumerar cuentas desde el propio pipeline de telemetría. | `login_failed.properties` **no** incluye el email en claro. Incluye `email_hash` (HMAC-SHA256 truncado, clave rotable en el backend, nunca el email plano) para poder correlacionar intentos repetidos contra la misma cuenta sin poder revertir el hash a un email. |
| Enumeración de cuentas vía `forgot-password` | `routes/auth.py::forgot_password` | El endpoint ya evita revelar si el email existe en su respuesta HTTP (siempre 200 genérico). Emitir telemetría distinguible solo cuando el email *no* existe recrearía ese mismo canal de fuga a nivel de datos, aunque la API no lo exponga. | `password_reset_requested` **solo se emite en la rama donde el usuario existe y está activo** (con `userId` set). La rama de "email no encontrado / inactivo" no emite evento individual — se cuenta en un contador agregado sin `userId` a nivel de infraestructura (fuera de alcance de este plan) si el equipo de seguridad lo necesita más adelante. |
| Nombre de fichero CSV subido en `/api/incidents/analyze` | `app/routers/incidents.py` | El nombre del fichero puede contener el nombre de un cliente o candidato (ej. `incidencias_cliente_acme.csv`). | `csv_analysis_rejected` **nunca incluye el nombre de fichero real**, solo `file_size_bytes` y `rejection_reason`. |
| Stack traces / URLs con token en `frontend_error_captured` | Frontend (nuevo) | Un stack trace o una URL con querystring puede arrastrar un token de sesión o un parámetro con datos de negocio. | Antes de emitir: se despoja cualquier querystring/fragment de `route`, se trunca el stack a frames de código propio (se excluyen frames de `node_modules`/vendor), y nunca se incluye el header `Authorization`. |
| Contraseñas en cualquier payload | Todo `/auth/*` | Un logger o interceptor mal puesto podría capturar el body completo de `POST /auth/login`, `change-password`, etc. | Ningún evento de este catálogo incluye el body de una petición completo — cada evento define su propio allowlist cerrado, construido a mano campo por campo, nunca por volcado genérico del `request.body()`. Esta regla aplica a los 27 eventos, no solo a los de auth. |

### 3.2 Estrategia de entrega — stream vs. batch (Fase 3)

La decisión no es técnica, es de urgencia de la decisión que habilita cada evento:

- **Stream** cuando la decisión que el evento habilita pierde valor si se conoce horas después: seguridad (`login_failed`, `direct_stock_edit_rejected`), continuidad operativa (`stock_threshold_triggered`, `outbound_order_rejected`, `supplier_status_updated`, `supplier_deleted`), coste (`kit_cost_variance_detected`) y salud del sistema en producción (`api_request_failed`, `api_request_completed`, `frontend_error_captured`).
- **Batch** cuando la decisión es de planificación o tendencia, no de reacción inmediata: creación de órdenes/productos, altas de proveedor y cambios de tarifa, analítica de sesión/navegación, rendimiento agregado.

Dos decisiones que merecen justificación explícita porque no son obvias:
- **`supplier_status_updated` es *stream* pero `supplier_rate_updated` es *batch*.** Una suspensión bloquea de inmediato cualquier orden de entrada futura con ese proveedor — el equipo necesita enterarse el mismo día. Un cambio de tarifa es administrativo: afecta al presupuesto, no a si se puede o no producir el pedido de mañana.
- **`outbound_order_rejected` es *stream* pero `inbound_order_rejected` es *batch*.** Un rechazo de salida por stock insuficiente es demanda real insatisfecha ocurriendo ahora mismo (alguien está esperando un kit). Un rechazo de entrada (SKU inexistente, cantidad inválida) es casi siempre un error de captura de datos, no una emergencia operativa.

### 3.3 Throttle / debounce (eventos de alta frecuencia)

| Evento | Estrategia | Por qué |
|---|---|---|
| `api_request_completed` | Muestreo: 100% de respuestas no-2xx/3xx se cubren vía `api_request_failed` (no aquí); de las respuestas exitosas, **10% de muestreo aleatorio** en producción (configurable por env var). | Se emite en cada request — a volumen de producción, capturar el 100% de los "todo bien" aporta poco valor marginal sobre una tendencia agregada y satura el pipeline. |
| `login_failed` | **Ninguno — deliberado.** Se captura cada intento, incluso en ráfaga. | Una ráfaga de este evento *es* la señal de negocio (posible fuerza bruta). Deduplicar en el punto de emisión destruiría la única señal que hace valioso al evento. El throttle, si hace falta, va río abajo en la capa de alerta — nunca en la captura. |
| `frontend_error_captured` | Debounce por `(error_fingerprint, sessionId)`: máximo 1 evento cada 60s para el mismo error en la misma sesión. Las repeticiones se cuentan en cliente y se adjuntan como `occurrence_count` en el siguiente envío. | Un error en un bucle de render puede generar cientos de excepciones idénticas en segundos; sin dedupe, un solo usuario podría saturar el pipeline sin aportar información nueva tras el primer evento. |
| `backoffice_section_viewed` | Cola en cliente, vaciada cada 15s o cada 20 eventos (lo que ocurra primero) — un solo POST por lote, no uno por vista. | Es, por diseño, el evento de mayor volumen del catálogo (se dispara en cada navegación). |
| `stock_threshold_triggered` | *Edge-triggered, no level-triggered*: se emite solo en la transición de "≥ umbral" a "< umbral". Mientras el stock siga por debajo, no se reemite en cada lectura de `/inventory/products` (que además ya está cacheado 30s — ver `CACHING_REPORT.md`). Vuelve a poder dispararse solo tras cruzar de nuevo por encima del umbral. | Sin este límite, cada `GET /inventory/products` mientras el stock esté bajo generaría un evento — con el TTL de caché de 30s ya en producción, eso seguiría siendo ruido, no señal. |
| `inventory_order_form_abandoned` | Se dispara una sola vez por intento de formulario, al detectar salida de la ruta (o `beforeunload`) sin que haya habido un submit exitoso previo. | Evita duplicados si el usuario navega varias veces dentro del mismo formulario sin enviarlo. |

Ningún otro evento del catálogo tiene volumen suficiente para justificar throttle — se capturan 1:1 con la acción que los origina.

### 3.4 Riesgos y exclusiones

**Descartado deliberadamente (y por qué):**

- **Captura de cada cambio de campo en `InboundOrderForm`/`OutboundOrderForm`** (keystroke-level). Descartado: `inventory_order_form_abandoned` ya responde a la pregunta de negocio real ("¿se abandona el formulario?") sin el coste de privacidad y volumen de loguear cada tecla.
- **Log completo de request/response body en cada llamada a la API.** Descartado: redundante con los eventos de dominio ya diseñados, y con riesgo real de capturar contraseñas o tokens si algún endpoint nuevo se añade sin excluirlo explícitamente. Los 27 eventos de este catálogo usan allowlists construidos a mano, nunca un volcado genérico.
- **Heatmaps / tracking de movimiento de ratón.** Descartado: el backoffice lo usan ~30 operadores internos, no es una superficie de conversión pública — no hay una decisión de negocio identificable que este dato habilite hoy, y si aparece una (ej. rediseño mayor de una pantalla), se puede añadir entonces de forma acotada.
- **Geolocalización por IP de los usuarios del backoffice.** Descartado: `office` ya da la ubicación operativa relevante (Valencia/Miami); geolocalizar además por IP añade superficie de privacidad sin una decisión nueva que habilite.
- **Conversión de moneda en la capa de telemetría.** Excluido explícitamente por restricción del CONTEXT — ningún evento incluye un campo de importe "convertido", solo el monto en la moneda nativa de la oficina (`EUR`/`USD`).
- **Nombre de destinatario en órdenes de salida (`assigned_to`).** Ver §3.1.

**Riesgos abiertos que el equipo debe resolver antes de instrumentar (no antes de aprobar este plan):**

1. `direct_stock_edit_rejected` no tiene hoy ninguna superficie de código que lo dispare — requiere una decisión de diseño de API primero (§2.1).
2. `stock_threshold_triggered` y `kit_cost_variance_detected` dependen de campos que no existen en el modelo de datos actual (`min_stock_threshold` en `Asset`, `unit_cost` en `AssetEntry`, y una línea base histórica de coste). Este plan asume que se añadirán; no los añade.
3. `programme_id` no existe como entidad ni como campo en `Asset` hoy — el CONTEXT lo define como entidad de negocio, pero el modelo de datos actual de inventario no lo modela todavía. Todos los eventos de negocio-inventario que lo referencian (`*_order_created`, `stock_threshold_triggered`, `kit_cost_variance_detected`, `product_created`) dependen de esa extensión de modelo.
4. `supplier` en `AssetEntry` es hoy texto libre (ej. `"TechDistrib Valencia S.L."`), no una referencia al catálogo de `/suppliers`. Se recomienda normalizar a `supplier_id` cuando el proveedor de una entrada de inventario coincide con uno del catálogo, y mantener `supplier_name` como campo de reserva cuando no.

Estos cuatro puntos no bloquean la aprobación del plan — son exactamente el tipo de brecha entre "lo que el negocio pide medir" y "lo que el modelo de datos actual permite medir" que este documento existe para exponer antes de que alguien empiece a escribir instrumentación a ciegas.

---

## 4. Almacenamiento (`telemetry_events`)

Los eventos se persisten en Supabase en la tabla `telemetry_events`, creada por [`services/api/migrations/002_telemetry_events.sql`](../../services/api/migrations/002_telemetry_events.sql) (no por `SQLModel.create_all`, que no puede expresar el trigger ni la RLS). Llegan por dos caminos que comparten el mismo mapeo (`services/api/telemetry_store.py`):

- **Frontend** → `POST /telemetry/events` (lotes del `TelemetryService`). Un único bulk insert por lote.
- **Backend** → `log_telemetry_event()` (eventos de negocio, auth y rendimiento emitidos por la propia API). Insert de 1 fila; si falla, se registra un warning y la petición de negocio sigue su curso.

### 4.1 Esquema

| Columna | Tipo | Notas |
|---|---|---|
| `id` | `uuid` PK, `gen_random_uuid()` | Identificador del registro (distinto de `eventId`). |
| `timestamp` | `timestamptz` NOT NULL | Momento de ocurrencia (`envelope.timestamp`), no de ingesta. |
| `service` | `text` NOT NULL | `backoffice` o `api`. |
| `event_type` | `text` NOT NULL | Taxonomía `entidad_acción` de §2. |
| `level` | `text` NOT NULL, default `info` | `info` / `warn` / `error` (CHECK). |
| `value` | `numeric` | Métrica principal del evento, si tiene una. |
| `message` | `text` | Descripción legible del tipo de evento. |
| `tags` | `jsonb` NOT NULL, default `{}` | `properties` filtradas por allowlist + `_envelope`. |

Índices: B-tree en `timestamp` y en `event_type`, GIN en `tags`, y único sobre `tags->'_envelope'->>'eventId'` (idempotencia, ver §4.4).

### 4.2 Mapeo envelope → fila

| Columna | Origen |
|---|---|
| `timestamp` | `event.timestamp` (ISO 8601 con zona horaria obligatoria). |
| `service` | Derivado de `event.source`: `frontend` → `backoffice`, `backend` → `api`. |
| `event_type` | `event.event_type`. |
| `level` | `error`: `api_request_failed`, `frontend_error_captured`. `warn`: todo `*_rejected`, `login_failed`, `stock_threshold_triggered`, `kit_cost_variance_detected`. `info`: el resto. |
| `value` | Una propiedad numérica por tipo: `quantity` (`inbound/outbound_order_created`, `stock_threshold_triggered`), `requested_quantity` (`outbound_order_rejected`), `variance_pct` (`kit_cost_variance_detected`, `supplier_rate_updated`), `duration_ms` (`api_request_completed`), `lcp_ms` (`frontend_page_load_recorded`), `occurrence_count` (`frontend_error_captured`), `file_size_bytes` (`csv_analysis_rejected`), `time_on_form_ms` (`inventory_order_form_abandoned`), `last_active_seconds_ago` (`session_expired`). `NULL` en el resto o si el valor no es numérico. |
| `message` | `description` del evento en `event-schemas.json`. Texto fijo por tipo: **nunca** se construye con valores de `properties`, así no puede arrastrar PII. |
| `tags` | `event.properties` filtrado por el allowlist del evento en `event-schemas.json` (claves fuera del allowlist se descartan, el evento no se rechaza), más `_envelope`: `{eventId, sessionId, userId, requestId, schemaVersion}`. |

Las dimensiones del CONTEXT (`office`, `programme_id`, `product_category`, `currency`, …) se conservan tal cual dentro de `tags`, en la moneda nativa de la oficina (sin conversión, §3.4). `_envelope` va en una clave aparte para no mezclarse con las propiedades de negocio; `userId` es el UUID interno, nunca email ni nombre.

### 4.3 Validación y rechazo

El body se acepta de forma laxa (`{"events": [...]}`) y cada evento se valida por separado con `TelemetryEvent.model_validate` — el mismo modelo de la captura, sin cambios. Un evento se rechaza (cuenta en `rejected`, el resto del lote se guarda igual) si:

- no cumple `TelemetryEvent` (campo obligatorio ausente, tipo incorrecto, `event_type` mal formado, `source` desconocido);
- su `event_type` no está en el catálogo de `event-schemas.json`;
- su `timestamp` no es ISO 8601 o no tiene zona horaria;
- sus `properties` permitidas no son serializables a JSON.

Respuestas: `200 {received, stored, rejected}` si el envelope es parseable; `422` solo si falta el array `events`; `503` si falla la base de datos (el `TelemetryService` reintenta el lote, solo mira `res.ok`). `POST /telemetry/events` está excluido de `api_request_*` para no generar una fila por cada lote guardado.

### 4.4 Inmutabilidad e idempotencia

- **Append-only:** triggers `BEFORE UPDATE OR DELETE` (por fila) y `BEFORE TRUNCATE` lanzan una excepción. La API no tiene ninguna ruta que actualice o borre eventos.
- **Idempotencia:** el `TelemetryService` reintenta con backoff; si un insert se completó pero la respuesta se perdió, el reintento no duplica filas (`ON CONFLICT DO NOTHING` sobre el `eventId`). Un duplicado ignorado cuenta como `stored`: el evento está guardado.
- **Acceso:** RLS activada sin políticas — PostgREST no expone la tabla a `anon`/`authenticated`; la API escribe como propietaria vía `DATABASE_URL`.
