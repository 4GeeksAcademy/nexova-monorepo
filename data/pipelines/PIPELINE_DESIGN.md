# Diseño del Pipeline de Desempeño de Negocio — Nexova

> **Estado:** diseño aprobado (Parte 1), implementado (Parte 2) y llevado a producción con subflows, tests y dashboard (**Parte 3 de 3**). Cómo ejecutarlo: §11. Parte 3: §12.
> **Entregable:** "Reporte Semanal de Desempeño por Oficina y Programa" para Laura (CEO) y Elena (L&D), fresco los lunes.
> **Fuente:** `public.telemetry_events` (solo lectura). **Destino:** esquema `reporting`.

## 0. Decisiones

| # | Decisión | Motivo |
|---|----------|--------|
| D1 | `cost_variance_events_count` cuenta solo subidas (`value > 0`). | El KPI habla de subidas; la captura emite con `abs(variance_pct) > 10` y no se toca. |
| D2 | `programme_id` nulo → `'unassigned'`. | `Asset.programme_id` es nullable pero forma parte de la clave `unique`. |
| D3 | Módulo en `services/api/reporting/` (no `services/reporting/`). | La API vive en `services/api/`, junto a `telemetry/`. |
| D4 | `unit_cost` nulo se excluye del costo y se cuenta en el log. | Tratarlo como 0 ocultaría datos faltantes. |
| D5 | Cada corrida recalcula la semana actual y la anterior. | La fuente no guarda hora de ingesta; así se absorben eventos tardíos. |
| D6 | Lock por pipeline en `reporting.pipeline_runs`: una sola corrida `running` a la vez. | Cron y disparo manual pueden tener ventanas distintas pero tocan las mismas filas; un lock por ventana no las protegería. |
| D7 | Endpoints con `get_current_user`; restricción a admin como TODO. | Igual que `/telemetry/report`; no existen admins. |
| D8 | `kits_delivered_count` es el **conteo de eventos** `outbound_order_created` (no `Σ quantity`), sin filtrar por categoría ni `exit_type`. | Es la definición literal del CONTEXT. Las unidades entregadas (`Σ quantity`) quedan para v2 como columna aparte, para no mezclar ambos números. |
| D9 | La caché de la transformación (1 h) se indexa por el **contenido** leído: hash de los `event_id` de la ventana + versión de la lógica. | La fuente es append-only, así que el conjunto de `event_id` determina el resultado; un evento tardío cambia la clave y nunca se sirve un agregado obsoleto. |
| D10 | Paso opcional `export_eval_snapshot`: JSON de validación en `data/eval/business_performance/`. Su fallo no interrumpe la corrida. | El reporte ya está publicado cuando corre; es evidencia de auditoría, no parte del entregable. |
| D11 | `total_material_cost` se redondea a 4 decimales. | Hace el agregado determinista frente al orden de suma de los floats; si no, `rows_revised` daría falsos positivos. |

## 1. Estado actual y brecha

**Captura.** El catálogo `docs/telemetry/event-schemas.json` define 27 eventos. Los de negocio los emite `services/api/routers/inventory.py` vía `log_telemetry_event`: `inbound_order_created` (`unit_cost`, `quantity`), `outbound_order_created`, `stock_threshold_triggered` (edge-triggered, sin `currency`) y `kit_cost_variance_detected` (`variance_pct`), todos con `office` y `programme_id`.

**Almacenamiento.** `public.telemetry_events` (migración `002_telemetry_events.sql`): `id`, `timestamp` (ocurrencia), `service`, `event_type`, `level`, `value`, `message`, `tags` (JSONB). Es append-only (triggers) y deduplica por `tags->'_envelope'->>'eventId'` (`ON CONFLICT DO NOTHING`). `office`, `programme_id`, `unit_cost` y `quantity` están dentro de `tags`.

**Reporte técnico existente.** `GET /telemetry/report` (`services/api/telemetry/analysis.py`) responde para ingeniería: eventos por día, tasa de error por tipo y fallos de login. **No se modifica.**

**Brecha.** Nadie puede responder, por oficina, programa y semana: cuánto se gastó en material, cuántos kits se entregaron, cuántas veces cayó el stock bajo el mínimo y cuántas veces subió el costo de un proveedor. Tampoco hay semanas ISO cerradas ni monedas separadas (`EUR`/`USD`).

## 2. Propósito

> Producir cada lunes el "Reporte Semanal de Desempeño por Oficina y Programa" para Laura y Elena, calculando por `office` × `programme_id` × semana ISO el **costo de material**, los **kits entregados**, la **frecuencia de escasez** y la **frecuencia de variación de costo**, a partir de `inbound_order_created`, `outbound_order_created`, `stock_threshold_triggered` y `kit_cost_variance_detected`.

| Campo destino | Evento | Cálculo |
|---------------|--------|---------|
| `total_material_cost` | `inbound_order_created` | `Σ(unit_cost × quantity)` con `unit_cost` no nulo |
| `kits_delivered_count` | `outbound_order_created` | conteo de eventos |
| `shortage_events_count` | `stock_threshold_triggered` | conteo |
| `cost_variance_events_count` | `kit_cost_variance_detected` | conteo con `value > 0` |

`currency` se deriva de `office` (`valencia` → `EUR`, `miami` → `USD`), como en `routers/inventory.py`. Nunca se convierten ni suman monedas.

## 3. Extracción

Consulta de solo lectura, ventana `[start, end)` UTC, filtrada por los 4 `event_type`. La ventana es la semana ISO actual y la anterior (`lookback_weeks=2`, ampliable en backfill). Cadencia: lunes `04:00 UTC` más disparo manual.

```sql
SELECT "timestamp", event_type, value,
       tags ->> 'office'                    AS office,
       NULLIF(tags ->> 'programme_id', '')  AS programme_id,
       (tags ->> 'unit_cost')::numeric      AS unit_cost,
       (tags ->> 'quantity')::int           AS quantity,
       (tags ->> 'order_id')::int           AS order_id,
       tags -> '_envelope' ->> 'eventId'    AS event_id
FROM public.telemetry_events
WHERE "timestamp" >= :window_start AND "timestamp" < :window_end
  AND event_type IN ('inbound_order_created', 'outbound_order_created',
                     'stock_threshold_triggered', 'kit_cost_variance_detected');
```

`kit_cost_variance_detected` guarda `variance_pct` en `value`, así que D1 no necesita leer el JSONB.

En la implementación (`business_performance/extract.py`) los numéricos del JSONB se leen como texto y se convierten en Pandas con `errors="coerce"`, en vez de con `::numeric`/`::int` en SQL: un solo evento mal formado no debe tumbar la extracción entera (cae en `rows_missing_cost`). La consulta añade `ORDER BY "timestamp", event_id` para que el resultado sea determinista.

## 4. Flujo de datos

```mermaid
flowchart LR
    A[("telemetry_events<br/>eventos sueltos")]
    B["1. EXTRAER<br/>leer eventos de 2 semanas"]
    C["2. TRANSFORMAR<br/>limpiar y resumir por<br/>oficina, programa y semana"]
    D["3. CARGAR<br/>guardar o reemplazar filas"]
    E[("reporting.weekly_office_<br/>program_performance<br/>filas resumen")]
    F["API de reporting<br/>dashboard de Laura y Elena"]

    A --> B --> C --> D --> E --> F
```

En cada etapa el pipeline anota su avance en `reporting.pipeline_runs` (bitácora y lock de la corrida, ver §7).

**Reglas de transformación.** `week_start` es el lunes 00:00 UTC (`timestamp` convertido con `utc=True`). Solo `valencia` y `miami`; otros valores se descartan y se cuentan. Se deduplica por `event_id` en todos los eventos y, solo en `inbound_order_created`, `outbound_order_created` y `kit_cost_variance_detected` (que sí traen `order_id`), también por `(event_type, order_id)`. `stock_threshold_triggered` no lleva `order_id` y se deduplica solo por `event_id`. Las combinaciones sin eventos no generan fila.

## 5. Registros actualizados (upsert)

La fuente es append-only; lo que cambia es el agregado, que se recalcula cada corrida. Estrategia: upsert sobre `unique (office, programme_id, week_start)` **reemplazando** valores (nunca sumando):

```sql
INSERT INTO reporting.weekly_office_program_performance (...)
VALUES (...)
ON CONFLICT (office, programme_id, week_start) DO UPDATE SET
    total_material_cost        = EXCLUDED.total_material_cost,
    kits_delivered_count       = EXCLUDED.kits_delivered_count,
    shortage_events_count      = EXCLUDED.shortage_events_count,
    cost_variance_events_count = EXCLUDED.cost_variance_events_count,
    currency = EXCLUDED.currency, computed_at = now();
```

## 6. Tablas y endpoints

**Tablas** (migración manual `003_reporting_schema.sql`, fuera de `SQLModel.metadata` como la 002):
- `reporting.weekly_office_program_performance`: el DDL del CONTEXT, sin cambios.
- `reporting.pipeline_runs`: log de corridas (§7.3), con índice único parcial `(pipeline_name) WHERE status = 'running'`.

**Endpoints** en `services/api/reporting/` (con `response_model`). Ninguno toca `analysis.py` ni `/telemetry/report`:

| Endpoint | Función | Función en `data/pipelines/` |
|----------|---------|------------------------------|
| `GET /reporting/pipeline-runs/latest` | Estado de la última corrida (`404` si no hay) | `get_latest_run()` |
| `POST /reporting/pipeline-runs` | Disparo manual (`week_start` y `lookback_weeks` opcionales); `202` con `run_id`, `409` si el lock está tomado | `start_manual_run()` toma el lock antes de responder y `run_weekly_performance_pipeline(trigger="manual", run_id=...)` corre en segundo plano |
| `GET /reporting/weekly-office-program-performance` | KPIs (`week_start` opcional, debe ser lunes; por defecto la última calculada) | `query_weekly_kpis(week_start)` |

La respuesta de KPIs es la del CONTEXT (`week_start` + `entries[]` con `office`, `programme_id`, los 4 contadores y `currency`). Usa `cache.get_or_set` (TTL 60 s), invalidado al terminar la carga. La lógica ETL vive en `data/pipelines/business_performance/`; `services/` solo importa de ahí.

## 7. Resiliencia

### 7.1 Idempotencia

1. **Origen:** `telemetry_events` ya deduplica por `eventId`; el pipeline añade `drop_duplicates` por `event_id` y, solo en los 3 eventos con `order_id`, por `(event_type, order_id)`. Nunca se aplica sobre `order_id` nulo: colapsaría eventos distintos.
2. **Cálculo:** cada corrida recalcula la ventana completa desde la fuente, sin acumular.
3. **Carga:** upsert con reemplazo en una sola transacción.

### 7.2 Segunda corrida tras un fallo en la carga

Si la carga falla tras 847 de 1 412 filas, la transacción hace rollback y la tabla queda como estaba; la corrida queda `failed` con `phase = 'load'`. La siguiente corrida abre un `run_id` nuevo, relee la fuente y hace upsert de las 1 412 filas, con el mismo resultado que una corrida limpia. Si en el futuro la carga se parte en lotes, las filas ya escritas se sobrescriben con los mismos valores (`= EXCLUDED`) y el resultado converge igual. Una corrida `running` de más de 15 min se marca `failed` (`stale run`) antes de tomar el lock.

**Eventos tardíos:** se absorben al recalcular la semana actual y la anterior. `computed_at` deja el rastro (se contrasta con `started_at`/`finished_at` de `pipeline_runs`), y `rows_revised > 0` en una semana cerrada señala un evento tardío. Más allá de 2 semanas se requiere backfill manual.

**Concurrencia:** `open_run` inserta una fila `running`; el índice único parcial por `pipeline_name` rechaza la segunda, aunque su ventana sea distinta. Esa corrida termina `skipped` (cron) o `409` (manual). El lock es por pipeline y no por ventana porque cron y disparo manual pueden recalcular semanas distintas que comparten filas.

### 7.3 Log de ejecución — `reporting.pipeline_runs`

| Campo | Tipo | Para qué sirve en auditoría |
|-------|------|-----------------------------|
| `run_id` | `uuid` PK | Identifica la corrida de forma única. |
| `pipeline_name` | `text` | Clave del lock (una corrida `running` por pipeline); permite reutilizar la tabla. |
| `trigger` | `text` (`schedule`/`manual`) | Distingue cron de disparo humano. |
| `window_start`, `window_end` | `timestamptz` | Ventana procesada; permite saber qué semanas se recalcularon. |
| `started_at`, `finished_at` | `timestamptz` | Duración y detección de corridas zombi (`finished_at` nulo). |
| `status` | `text` (`running`/`completed`/`failed`/`skipped`) | Estado que consulta `/pipeline-runs/latest`. |
| `phase` | `text` (`extract`/`transform`/`load`) | Checkpoint: dónde falló. |
| `rows_read` | `integer` | Eventos leídos; `0` con `completed` distingue "sin actividad" de "no corrió". |
| `rows_written`, `rows_revised` | `integer` | Filas escritas y filas ya publicadas que cambiaron (eventos tardíos). |
| `rows_missing_cost`, `rows_unassigned_programme`, `rows_invalid_office`, `rows_duplicates_dropped` | `integer` | Calidad de dato (D4, D2, oficina inválida, duplicados). |
| `error_message` | `text` nulo | Causa del fallo. |
| `prefect_flow_run_id` | `text` nulo | Enlace con los logs de Prefect. |

## 8. Mapeo a Prefect

**Flow principal:** `weekly_office_program_performance_flow` (parámetros `week_start`, `lookback_weeks=2`, `trigger`). Flow opcional de backfill por rango de semanas.

**Subflows (Parte 3):** el flow principal no contiene lógica; abre la corrida (`open_run`), invoca un subflow por fase y la cierra (`close_run`). Entre fases, `mark_phase` anota el checkpoint en `pipeline_runs.phase`.

```
weekly_office_program_performance_flow
├─ open_run
├─ extract_business_telemetry_events_flow           (window_start, window_end) → eventos
│    └─ extract_events
├─ compute_weekly_office_program_kpis_flow          (eventos) → {rows, quality}
│    ├─ prepare_business_events                      dedupe, oficina, programa, semana (con caché)
│    ├─ compute_total_material_cost                  KPI "Costo de material por oficina/programa"
│    ├─ compute_kits_delivered_count                 KPI "Kits entregados"
│    ├─ compute_shortage_events_count                KPI "Frecuencia de escasez"
│    ├─ compute_cost_variance_events_count           KPI "Frecuencia de variación de costo"
│    └─ assemble_weekly_office_program_rows          une los 4 KPIs + moneda
├─ publish_weekly_office_program_performance_flow   (rows) → {rows_written, rows_revised}
│    └─ load_weekly_performance
├─ check_capture_silence_flow                       opcional (§12.4)
│    └─ count_session_events
├─ export_kpi_eval_snapshot_flow                    opcional
│    └─ export_eval_snapshot
└─ close_run
```

| Task | Resiliencia | Si falla |
|------|-------------|----------|
| `open_run`, `mark_phase`, `close_run` | `retries=2`, espera 5 s | Se propaga: sin bitácora no se corre. |
| `extract_events` | `retries=3`, espera 2/10/30 s | Corrida `failed`, `phase = 'extract'`. |
| `prepare_business_events` | Caché: `cache_key_fn` (D9) + `cache_expiration` de 1 h | Corrida `failed`, `phase = 'transform'`. |
| `compute_*`, `assemble_weekly_office_program_rows` | Sin reintentos (puras: un fallo es un bug) | Corrida `failed`, `phase = 'transform'`. |
| `load_weekly_performance` | `retries=3`, espera 2/10/30 s | Rollback; corrida `failed`, `phase = 'load'`. |
| `count_session_events` | `retries=2`, espera 5 s | Falla el subflow opcional; la corrida sigue. |
| `export_eval_snapshot` | Sin reintentos (no crítica) | Se registra un aviso y la corrida termina `completed`. |

Los tres subflows críticos y los dos opcionales se invocan con `return_state=True`: el flow principal inspecciona el estado; si falla uno crítico, anota la fase y cierra la corrida antes de propagar el error; si falla uno opcional, solo registra un aviso.

**States:** `Running` → `running`; `Completed` → `completed`; `Failed` → `failed`; `Retrying` sigue en `running`; `Crashed` queda `running` y se limpia por antigüedad.

**Configuración (v1):** `DATABASE_URL` por entorno (la misma de `services/api/database.py`) y constantes en `business_performance/config.py`. Los Blocks `Secret` `nexova-database-url` y `JSON` `reporting-pipeline-config` quedan para cuando exista un servidor Prefect compartido. Cron `0 4 * * 1` (UTC) con `pipeline.py --serve`. El `POST` invoca la función del flow directamente en `BackgroundTasks`; la caché de la API (`reporting:`) la invalida ese mismo proceso al terminar, y una corrida por CLI o cron se refleja al expirar el TTL de 60 s.

## 9. Preguntas de diseño

1. **Duplicados en origen:** la clave es `eventId` (envelope), aplicada en la **ingesta** con índice único + `ON CONFLICT DO NOTHING`. Los agregados cuentan después de deduplicar; para los eventos con `order_id` también por `(event_type, order_id)`.
2. **Reintento tras fallo:** §7.2; rollback de la transacción y upsert con reemplazo.
3. **Eventos tardíos:** §7.2; recalculo de dos semanas y `rows_revised`.
4. **Silencio vs. ausencia:** toda corrida `completed` deja fila en `pipeline_runs`, incluso con `rows_read = 0`; sin fila, el pipeline no corrió. Un cero de negocio se contrasta con la actividad de sesiones (`backoffice_section_viewed`, `login_succeeded`): cero eventos de todo tipo sugiere captura caída. **Implementado en la Parte 3** como `check_capture_silence_flow` (§12.4).
5. **Trazabilidad:** `eventId` → ventana de la corrida (`window_start/end`) → `run_id`; la fila del reporte se enlaza con su corrida por `computed_at`. Los eventos de inventario se emiten con `request_id=None`, así que `requestId` no sirve para correlacionar; se usa ventana y conteos.
6. **Crecimiento vs. pérdida:** se compara el volumen con las sesiones activas y las oficinas que reportan. Riesgo: `log_telemetry_event` nunca lanza, así que un fallo de Supabase pierde el evento sin rastro.
7. **Caída de DB:** checkpoint en `pipeline_runs.phase`. No se persisten resultados intermedios; la siguiente corrida recalcula todo.
8. **Buffer en frontend:** los 4 eventos del pipeline son `source = backend`, así que no dependen del buffer del navegador. Para los de frontend, el servidor deduplica por `eventId`.
9. **Reintento de transmisión:** `eventId` hace de clave de idempotencia. `POST /telemetry/events` responde `200` (no reintentar) o `503` (reintentar).
10. **Corridas concurrentes:** §7.2; índice único parcial por pipeline (una corrida `running` a la vez) y `run_id` único.

## 10. Riesgos y decisiones abiertas

**Huecos del repo resueltos en la Parte 2:** `data/pipelines` se monta en el contenedor de la API (`/data/pipelines`, variable `PIPELINES_PATH`); `prefect>=3` está en las dependencias de `services/api`; `services/api/data/` colisiona con un paquete llamado `data`, por eso la API importa `pipeline` y `business_performance` añadiendo `data/pipelines` a `sys.path` (`services/api/reporting/pipeline_bridge.py`).

**Siguen abiertos:** no existen admins (D7); `office` es texto libre, así que el pipeline descarta y cuenta lo que no sea `valencia`/`miami`; los formularios del backoffice no piden `unit_cost` ni `programme_id`, así que las órdenes creadas desde ahí llegan sin costo (cuentan en `rows_missing_cost`) y nunca disparan `kit_cost_variance_detected`.

**Abierta:**
- **A1.** La semana es UTC (regla del CONTEXT): un evento del lunes de madrugada hora local puede caer en la semana anterior.

**Restricciones cumplidas:** no se modifica `analysis.py` ni `GET /telemetry/report`; no se escribe en `telemetry_events`; no hay campos nuevos en la captura; no se mezclan monedas.

## 11. Ejecución (Parte 2)

**Frecuencia del ciclo de reporting:** semanal, lunes `04:00 UTC` (cron `0 4 * * 1`), para que el reporte esté fresco la mañana del lunes. Además admite disparo manual por CLI o por `POST /reporting/pipeline-runs`.

**Requisitos:** `DATABASE_URL` en `services/api/.env` (o en el entorno) y la migración `services/api/migrations/003_reporting_schema.sql` aplicada en Supabase.

```bash
# Desde la raíz del monorepo. ETL completo: semana actual + anterior.
uv run --project services/api python data/pipelines/pipeline.py

# Backfill: recalcular 4 semanas hasta la del lunes indicado.
uv run --project services/api python data/pipelines/pipeline.py --week-start 2026-10-05 --lookback-weeks 4

# Dejarlo programado con el cron semanal (necesita un servidor Prefect, ver abajo).
uv run --project services/api python data/pipelines/pipeline.py --serve
```

Con el entorno de `services/api` activado equivale a `python data/pipelines/pipeline.py`. El comando imprime el resumen de la corrida (ventana, contadores, `run_id`, estado) y sale con código distinto de cero si una etapa crítica falla.

**UI de Prefect (opcional):** sin servidor, el flow levanta uno temporal y los estados se ven en consola. Para verlos en la UI:

```bash
uv run --project services/api prefect server start          # UI en http://127.0.0.1:4200
# En otra terminal, antes de lanzar el pipeline:
export PREFECT_API_URL=http://127.0.0.1:4200/api            # PowerShell: $env:PREFECT_API_URL = "..."
```

**Estructura del código:**

| Ruta | Contenido |
|------|-----------|
| `data/pipelines/pipeline.py` | Flow principal, subflows, tasks y CLI. |
| `data/pipelines/business_performance/` | Lógica ETL sin Prefect: `extract.py`, `transform.py`, `load.py`, `runs.py` (bitácora y lock), `queries.py`, `silence.py` (alerta de silencio), `windows.py`, `config.py`, `db.py`. |
| `data/eval/business_performance/` | Snapshots de validación de cada ventana (no se versionan). |
| `tests/pipelines/` | Tests unitarios (§12.2). |
| `services/api/migrations/003_reporting_schema.sql` | Esquema `reporting` y sus dos tablas. |
| `services/api/reporting/` | Los 3 endpoints; importan de `data/pipelines/` vía `pipeline_bridge.py`. |
| `uis/backoffice/app/(reporting)/reporting/` | Dashboard de negocio (§12.3). |

## 12. Parte 3 — subflows, tests y dashboard

### 12.1 Subflows

Topología en §8. Cada subflow tiene entradas y salidas explícitas (nada pasa por variables globales) y se puede ejecutar solo, por ejemplo para recalcular KPIs sobre eventos ya leídos sin tocar la base de datos:

```python
# Desde data/pipelines, con el entorno de services/api.
from pipeline import compute_weekly_office_program_kpis_flow, extract_business_telemetry_events_flow
from business_performance.windows import resolve_window

start, end = resolve_window()                                   # semana actual + anterior
events = extract_business_telemetry_events_flow(start, end)     # solo lectura
result = compute_weekly_office_program_kpis_flow(events)        # sin base de datos
```

La transformación se partió en una task por KPI para que cada uno tenga su propio estado en Prefect y su propio test. La lógica sigue en `business_performance/transform.py` (funciones puras) y no en `data/process/`: el contenedor de la API solo monta `data/pipelines` (§10) y moverla obligaría a otro bind mount y otra entrada en `sys.path` sin ganar nada. La CLI (§11) y las funciones que usa la API no cambiaron.

### 12.2 Tests

`tests/pipelines/test_pipeline.py`, con datos en memoria que tienen la forma de la extracción de `telemetry_events`. `tests/pipelines/conftest.py` bloquea la base de datos en todos los tests: si uno intentara conectarse, falla.

```bash
# Desde la raíz del monorepo
uv run --project services/api python -m pytest tests/pipelines/test_pipeline.py
```

Cubren cada task de KPI, incluido un valor calculado a mano contra la definición del CONTEXT (10 × 12,50 + 4 × 15,00 = 185,00 EUR), entradas defensivas (costo nulo o de tipo incorrecto, oficina inválida, programa vacío, timestamp nulo), deduplicación, semanas ISO en UTC, monedas separadas, la clave de caché ante eventos tardíos, la alerta de silencio, y el subflow de KPIs ejecutado como flow de Prefect por sí solo.

### 12.3 Dashboard

`/reporting` en el backoffice (enlace "Reporte semanal" en el menú lateral). Consume `GET /reporting/weekly-office-program-performance` y `GET /reporting/pipeline-runs/latest` a través de los proxies de `uis/backoffice/app/api/reporting/`.

- Una tarjeta por KPI con el nombre exacto del CONTEXT y una frase que explica qué mide.
- Período en lenguaje natural ("Semana del lunes 21 al domingo 27 de septiembre de 2026") y navegación entre semanas.
- Importes con su moneda y total por oficina; EUR y USD nunca se suman.
- Escasez y variación de costo resaltadas cuando son mayores que cero, con la acción que sugiere el CONTEXT (reordenar, renegociar).
- Fecha de la última actualización, y aviso visible si la última corrida falló.

### 12.4 Mejora adicional: alerta de silencio

**Pregunta que responde:** la 4 (§9, "silencio vs. ausencia").

**Por qué se priorizó:** era la única pregunta del diseño sin respuesta implementada (lock, idempotencia y eventos tardíos ya estaban desde la Parte 2), y protege directamente el entregable: sin ella, una caída de la captura llegaría al reporte de Laura como "cero kits entregados".

**Cómo funciona:** el subflow opcional `check_capture_silence_flow` solo consulta la base de datos si la ventana no tuvo eventos de negocio. Entonces cuenta `login_succeeded` y `backoffice_section_viewed` en la misma ventana:

| Eventos de negocio | Eventos de sesión | Estado | Lectura |
|---|---|---|---|
| > 0 | — | `active` | La captura funciona. |
| 0 | > 0 | `no_business_activity` | La gente usó el sistema pero no movió material: el cero es real. |
| 0 | 0 | `possible_capture_outage` | Aviso `WARNING`: probablemente la telemetría dejó de llegar. |

El estado queda en el log de Prefect, en el resumen de la corrida (`capture_status`) y en el snapshot de `data/eval/`. No añade columnas ni migraciones.
