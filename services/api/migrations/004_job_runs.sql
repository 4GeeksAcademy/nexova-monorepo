-- =============================================================================
-- 004 — Bitácora de jobs en segundo plano (`job_runs`)
--
-- Registro de las ejecuciones del script nocturno `scripts/nightly_export.py`
-- (ticket #DEV-53): export CSV de `telemetry_events`, disparo del pipeline como
-- subproceso, lock e idempotencia por día. Lo escribe `services/job_runner/`.
--
-- No sustituye a `reporting.pipeline_runs` (003): esa tabla registra las fases
-- internas del ETL y la escribe el propio pipeline; esta, la orquestación.
--
-- Igual que la 002 y la 003, NO la crea `create_db_and_tables()`: el índice
-- único parcial no se puede expresar en SQLModel. Se aplica a mano en el SQL
-- Editor de Supabase (o `psql "$DATABASE_URL" -f ...`).
-- Idempotente: se puede ejecutar más de una vez sin efectos.
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.job_runs (
    id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    job_name      text        NOT NULL,
    target_date   date        NOT NULL,
    status        text        NOT NULL DEFAULT 'pending'
                              CHECK (status IN ('pending', 'processing', 'completed', 'failed')),
    started_at    timestamptz,
    finished_at   timestamptz,
    error_message text,
    created_at    timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.job_runs IS
    'Ejecuciones de jobs en segundo plano (pending → processing → completed | failed). Ver scripts/nightly_export.py.';

-- --- Índices -----------------------------------------------------------------
-- Idempotencia: "¿ya hay una ejecución completed de este job para este día?".
CREATE INDEX IF NOT EXISTS job_runs_job_target_idx
    ON public.job_runs (job_name, target_date);

-- El lock es el propio estado `processing`: este índice solo impide que dos
-- instancias lleguen a `processing` a la vez (la segunda recibe
-- unique_violation), igual que `pipeline_runs_running_uidx` en la 003.
CREATE UNIQUE INDEX IF NOT EXISTS job_runs_processing_uidx
    ON public.job_runs (job_name)
    WHERE status = 'processing';

-- --- Acceso ------------------------------------------------------------------
-- RLS sin políticas: PostgREST no expone la tabla a los roles anon/authenticated.
-- El script conecta como propietario vía DATABASE_URL.
ALTER TABLE public.job_runs ENABLE ROW LEVEL SECURITY;
