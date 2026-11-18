# Auditoría de gestión de errores

Rama `feature/error-handling-audit`. Alcance: frontend (Next.js), backend (FastAPI), tareas en segundo plano y scripts. Sin funcionalidades nuevas: solo resiliencia y comunicación de errores.

**Método.** Barrido de solo lectura con la plantilla del README (try/catch ausente, catch amplio, fallos silenciosos, errores en crudo, datos sensibles, estados de carga/error, llamadas a la acción, salida de scripts) y lectura de cada hallazgo en su contexto. Los números son de ese barrido; las rutas apuntan al código **antes** de la corrección.

Resumen: 0 críticos, 5 altos, 6 medios, varios bajos aceptados. Todos los altos y medios están corregidos salvo lo indicado en «Pendiente».

---

## Hallazgos

### ALTO

| # | Categoría | Dónde | Problema | Corrección |
|---|---|---|---|---|
| A1 | Exposición en crudo / datos sensibles | `services/api/tasks/router.py` (`GET /tasks/{id}`) | Devolvía `Tipo: texto de la excepción` de la tarea Celery a **cualquier usuario autenticado**; una excepción de BD o de red puede contener hosts, rutas o credenciales. | Solo el tipo de la excepción y un texto fijo; el detalle queda en el log del worker y en la DLQ. Test: `tests/pipelines/test_error_handling.py`, `tests/tasks/test_endpoints.py`. |
| A2 | Exposición en crudo | `services/api/app/main.py` | Sin handler global: una excepción no controlada respondía el 500 por defecto de Starlette (texto plano), sin el formato JSON de la API. | `@app.exception_handler(Exception)`: 500 JSON genérico; tipo y traza solo en el log del servidor. |
| A3 | Try/catch ausente | `uis/backoffice/app/api/**/route.ts` (26 handlers proxy) | El `fetch` al backend no estaba protegido: con la API caída, Next devolvía un 500 HTML y los clientes mostraban un error de parseo. | `lib/backend-fetch.ts`: `backendFetch` convierte el fallo de red en un 502 JSON legible; cuerpos `json()`/`formData()` ilegibles → 400 JSON. |
| A4 | Sin salida para el usuario | Las tres apps Next | No había `error.tsx`, `global-error.tsx`, `not-found.tsx` ni `loading.tsx`: un error de render o una ruta inexistente dejaba la pantalla por defecto de Next. | Añadidos en backoffice, talent-pipeline-tracker y website (mensaje legible + Reintentar + Volver al inicio + soporte). |
| A5 | Try/catch ausente / error en crudo | `uis/talent-pipeline-tracker/lib/api.ts` | `fetch` y `JSON.parse` sin proteger; la UI mostraba «Error: Failed to fetch» o «Unexpected token <», y el `message` del servidor tal cual. | `try/catch` acotado a `fetch` y a `JSON.parse`; mensajes de `lib/http-errors.ts`. |

### MEDIO

| # | Categoría | Dónde | Problema | Corrección |
|---|---|---|---|---|
| M1 | Datos sensibles | `data/pipelines/rfp_intake/trace.py` | El texto de la excepción se guardaba en `rfp_trace_event` y se sirve por `GET /rfp/tickets/{id}/trace`. | Solo el tipo; el detalle ya lo registra el worker. Test de comportamiento. |
| M2 | Error en crudo | `lib/api.ts`, `inventory.ts`, `reporting.ts`, `rfp.ts`, `telemetry-report.ts`, `knowledge.ts`, `auth-api.ts` (backoffice y tracker) | Mensajes «Error inesperado (HTTP 500)» y `detail` del servidor mostrado también en 5xx. | `lib/http-errors.ts` (`statusMessage`, `userMessage`): nunca un código HTTP; el texto del servidor solo en 4xx. 9 tests de vitest. |
| M3 | Try/catch ausente | `lib/api.ts`, `inventory.ts`, `auth-api.ts` ×2 | `res.json()` sin proteger y `fetch` sin capturar el fallo de red. | `try/catch` específico por llamada con mensaje de conexión / genérico. |
| M4 | Sin llamada a la acción | Analizador de incidencias, inventario (productos, historial, entrada, salida), reporte, telemetría, bandeja de RFP, listado del tracker | El error se mostraba como un párrafo rojo sin forma de reintentar. | `components/ErrorNotice.tsx` (reintentar + inicio + soporte) y reintento real en cada pantalla. |
| M5 | Sin `sys.exit` / E/S sin proteger | 9 scripts de `scripts/` + `seed_incidents.py` | Terminaban con código 0 ante fallos y mostraban trazas ante CSV, BD o LLM caídos. | `scripts/_cli.py` (`fail` → stderr + código ≠ 0); `try/except` acotado a cada operación de E/S, BD, red o LLM; comprobación de entradas antes de procesar. Tests de códigos de salida del seed. |
| M6 | Catch demasiado amplio | `services/api/app/routers/incidents.py` | `except Exception` alrededor del parseo del CSV. | Solo `csv.Error` y `ValueError`. |

### BAJO (revisado y aceptado)

- **53 `except Exception`.** Revisados uno a uno. La gran mayoría son fronteras deliberadas: reintento de Celery, fallback de un LLM o de Qdrant, notificaciones o trazas «best effort». Cada una registra la causa y degrada con un mensaje limpio. Los que sí filtraban datos están en A1 y M1.
- `services/api/tasks/base.py` guarda `str(exc)[:2000]` en `public.task_dead_letters`. Es una tabla interna que ninguna ruta de la API expone; si algún día se expone, hay que sanear (ver «Pendiente»).
- `services/api/chat/router.py`: `except WebSocketDisconnect: pass` es la desconexión normal del cliente, no un fallo silencioso.
- `services/api/routes/auth.py`: el fallo del proveedor de email se registra y se responde igual que si hubiera ido bien, a propósito, para no revelar si la cuenta existe.
- `rfp/router.py` y `app/routers/incidents.py` devuelven `str(exc)` de excepciones de dominio (`InvalidUpload`, `HandoffNotReady`, `EmptyFileError`…) cuyo texto está redactado para personas.
- `console.*` en el frontend: solo `console.warn` de telemetría sin datos sensibles. No hay `print` de datos internos en servicios.

---

## Qué NO se ha hecho (pendiente)

- Barrido exhaustivo de **optional chaining y valores por defecto** al renderizar: no se ha hecho de forma sistemática; las pantallas modificadas ya validan las respuestas en runtime (`lib/reporting.ts`, `lib/incident-manager.ts`), y las demás se revisaron por muestreo.
- Las pantallas de **chat (`/knowledge`)**, **cuenta** y **detalle de RFP / candidato** ya tenían estados de error con salida y no se han reescrito.
- Sanear la DLQ si se expone por API (arriba).
- `rate_limit.py` y otros handlers no se han vuelto a auditar.
- Los **traces grabados con el LLM real** (`tests/pipelines/fixtures/agent_traces/`) siguen con el vocabulario antiguo de tickets (no se pueden regrabar sin Logto ni cuota del proveedor).

## Verificación

Ver `memory-bank/progress.md` (historial 2026-11-18) para el resultado de tests, lint y build.
