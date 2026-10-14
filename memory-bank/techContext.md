# Stack tecnológico
- Monorepo por dominios para separar UI, servicios, datos, agentes, workflows e infraestructura.
- TypeScript como base técnica del proyecto.
- Frontend principal del caso de uso en Next.js 16 con React 19.
- Tailwind CSS 4 para estilos y PostCSS para procesamiento.
- ESLint 9 con configuración de Next.js para calidad de código.
- Capa cliente de API REST centralizada para consumir candidatos y notas.

## Decisiones de arquitectura tomadas
- Estructura modular por responsabilidades para facilitar escalabilidad y mantenimiento.
- Implementación inicial enfocada en una UI independiente de seguimiento de candidatos (Talent Pipeline Tracker).
- Centralización del acceso HTTP en una librería de API para evitar lógica de red dispersa en componentes.
- Definición de contratos tipados de dominio (status, stage, payloads y responses) para consistencia entre frontend y backend.
- Estado de filtros sincronizado con query params para compartir vistas filtradas y mantener navegación coherente.
- Uso de Suspense en la página principal para soportar correctamente hooks de navegación y parámetros en cliente.
- Enfoque human-in-the-loop: la IA asiste el pre-cribado y ranking, pero la decisión final de selección sigue en manos del consultor.

- Backend único en FastAPI (`services/api`) con persistencia dual: TinyDB para usuarios/perfiles/proveedores y Supabase/Postgres vía SQLModel para inventario y telemetría. El backoffice consume la API a través de route handlers proxy (`app/api/*`), salvo la telemetría, que el navegador envía directamente a `NEXT_PUBLIC_TELEMETRY_ENDPOINT`.
- Tablas que necesitan DDL que SQLModel no expresa (triggers, RLS, índices por expresión) se crean con SQL versionado en `services/api/migrations/` y se aplican a mano en Supabase; quedan fuera de `SQLModel.metadata` para que `create_all()` no las cree incompletas. Primer caso: `telemetry_events` (append-only).
- El catálogo de telemetría `docs/telemetry/event-schemas.json` es contrato ejecutable: la API lee de él los allowlists de propiedades en tiempo de arranque (ruta configurable con `TELEMETRY_SCHEMAS_PATH`).

- Análisis de telemetría con Pandas (`services/api/telemetry/analysis.py`): cada métrica carga solo lo necesario con SQL (ventana `[start, end)` UTC y `event_type`), convierte `timestamp` con `utc=True` antes de agrupar y se sirve vía `GET /telemetry/report` con la caché en memoria de `cache.py` (60 s).

- Pipelines de datos con Prefect 3 en `data/pipelines/` (`pipeline.py` = flow, tasks y CLI; la lógica ETL vive en un paquete sin Prefect, `business_performance/`, para que la API la reutilice). Corren sin servidor Prefect dedicado (servidor temporal local) y leen `DATABASE_URL` del entorno. La API no duplica lógica: `services/api/reporting/pipeline_bridge.py` añade `data/pipelines` a `sys.path` (ruta por `PIPELINES_PATH`; en Docker, bind mount en `/data/pipelines`). No se importa como `data.pipelines` porque `services/api/data/` (TinyDB) ocupa el nombre `data`.
- Los flows de Prefect se estructuran en subflows por fase (extracción, cálculo, publicación) coordinados por un flow principal sin lógica propia; los pasos no críticos son subflows invocados con `return_state=True`. Tests de pipelines con `pytest` (dependencia dev de `services/api`) en `tests/pipelines/`: llaman a `task.fn` con DataFrames en memoria y un `conftest.py` bloquea la base de datos.
- Los dashboards de negocio del backoffice (p. ej. `/reporting`) siguen el patrón del de telemetría: página cliente + proxy en `app/api/*` + cliente tipado que valida la respuesta; sin librerías de gráficos (barras con Tailwind).
- Los resultados de negocio se publican en un esquema Postgres aparte, `reporting` (migración `003_reporting_schema.sql`), nunca en `telemetry_events`. Toda carga es un upsert de reemplazo sobre un constraint `unique`, y cada corrida queda en `reporting.pipeline_runs`, que además hace de lock (una corrida `running` por pipeline).

## Restricciones técnicas
- Dependencia obligatoria de variable de entorno para URL de API; sin configuración no funciona la integración.
- Restricción de valores de estado y etapa mediante enums cerrados; cualquier valor fuera de contrato rompe consistencia.
- Dependencia de endpoints REST existentes para records y notes; sin backend compatible no hay flujo completo.
- Contexto técnico formal todavía incipiente: no hay ADRs documentadas en este punto.
- Alcance funcional acotado al pre-cribado asistido; no se contempla automatización completa de contratación sin intervención humana.