-- =============================================================================
-- 002 — Almacenamiento de telemetría (`telemetry_events`)
--
-- Destino real de los eventos que llegan a `POST /telemetry/events` y de los
-- que emite el propio backend vía `log_telemetry_event()`. Ver
-- docs/telemetry/telemetry-plan.md §4.
--
-- La 001 corresponde a la migración aditiva de inventario de la fase de
-- captura (`min_stock_threshold`/`programme_id` en `asset`, `unit_cost` en
-- `assetentry`), aplicada directamente en Supabase sin quedar versionada.
--
-- Esta tabla NO la crea `create_db_and_tables()` (SQLModel.create_all): el
-- trigger de inmutabilidad y la RLS no se pueden expresar ahí. Se aplica a
-- mano en el SQL Editor de Supabase (o `psql "$DATABASE_URL" -f ...`).
-- Idempotente: se puede ejecutar más de una vez sin efectos.
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.telemetry_events (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    "timestamp" timestamptz NOT NULL,
    service     text        NOT NULL,
    event_type  text        NOT NULL,
    level       text        NOT NULL DEFAULT 'info'
                            CHECK (level IN ('info', 'warn', 'error')),
    value       numeric,
    message     text,
    tags        jsonb       NOT NULL DEFAULT '{}'::jsonb
);

COMMENT ON TABLE public.telemetry_events IS
    'Telemetría de Nexova (append-only). Ver docs/telemetry/telemetry-plan.md §4.';

-- --- Índices -----------------------------------------------------------------
-- Consultas analíticas por ventana temporal y por tipo de evento.
CREATE INDEX IF NOT EXISTS telemetry_events_timestamp_idx
    ON public.telemetry_events ("timestamp");

CREATE INDEX IF NOT EXISTS telemetry_events_event_type_idx
    ON public.telemetry_events (event_type);

-- Búsquedas dentro del JSONB (ej. tags @> '{"office": "miami"}').
CREATE INDEX IF NOT EXISTS telemetry_events_tags_gin_idx
    ON public.telemetry_events USING gin (tags);

-- Idempotencia: el TelemetryService reintenta con backoff; si un insert se
-- completó pero la respuesta se perdió, el reintento no duplica filas
-- (la API inserta con ON CONFLICT DO NOTHING sobre esta expresión).
CREATE UNIQUE INDEX IF NOT EXISTS telemetry_events_event_id_uidx
    ON public.telemetry_events (((tags -> '_envelope') ->> 'eventId'));

-- --- Inmutabilidad -----------------------------------------------------------
-- Los eventos son hechos: nunca se actualizan ni se borran.
CREATE OR REPLACE FUNCTION public.telemetry_events_immutable()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'telemetry_events es append-only: % no está permitido', TG_OP;
END;
$$;

DROP TRIGGER IF EXISTS telemetry_events_no_update_delete ON public.telemetry_events;
CREATE TRIGGER telemetry_events_no_update_delete
    BEFORE UPDATE OR DELETE ON public.telemetry_events
    FOR EACH ROW EXECUTE FUNCTION public.telemetry_events_immutable();

DROP TRIGGER IF EXISTS telemetry_events_no_truncate ON public.telemetry_events;
CREATE TRIGGER telemetry_events_no_truncate
    BEFORE TRUNCATE ON public.telemetry_events
    FOR EACH STATEMENT EXECUTE FUNCTION public.telemetry_events_immutable();

-- --- Acceso ------------------------------------------------------------------
-- RLS sin políticas: PostgREST no expone la tabla a los roles anon/authenticated.
-- La API conecta como propietaria vía DATABASE_URL y no se ve afectada.
ALTER TABLE public.telemetry_events ENABLE ROW LEVEL SECURITY;
