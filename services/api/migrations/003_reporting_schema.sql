-- =============================================================================
-- 003 — Esquema de reporting de negocio (`reporting`)
--
-- Destino del pipeline semanal de desempeño por oficina y programa
-- (`data/pipelines/pipeline.py`) y bitácora de sus corridas. Ver
-- data/pipelines/PIPELINE_DESIGN.md §5–§7.
--
-- Igual que la 002, estas tablas NO las crea `create_db_and_tables()`
-- (SQLModel.create_all): el índice único parcial que hace de lock y la RLS no
-- se pueden expresar ahí. Se aplica a mano en el SQL Editor de Supabase (o
-- `psql "$DATABASE_URL" -f ...`).
-- Idempotente: se puede ejecutar más de una vez sin efectos.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS reporting;

-- --- Tabla de destino --------------------------------------------------------
-- DDL del CONTEXT (§5), sin cambios. Una fila por oficina × programa × semana
-- ISO; el pipeline hace upsert sobre el constraint `unique`.
CREATE TABLE IF NOT EXISTS reporting.weekly_office_program_performance (
    id                         uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    office                     text        NOT NULL,
    programme_id               text        NOT NULL,
    week_start                 date        NOT NULL,
    total_material_cost        numeric     NOT NULL DEFAULT 0,
    kits_delivered_count       integer     NOT NULL DEFAULT 0,
    shortage_events_count      integer     NOT NULL DEFAULT 0,
    cost_variance_events_count integer     NOT NULL DEFAULT 0,
    currency                   text        NOT NULL,
    computed_at                timestamptz NOT NULL DEFAULT now(),
    UNIQUE (office, programme_id, week_start)
);

COMMENT ON TABLE reporting.weekly_office_program_performance IS
    'Reporte Semanal de Desempeño por Oficina y Programa. Ver data/pipelines/PIPELINE_DESIGN.md.';

-- Consulta del endpoint: "la semana calculada más reciente".
CREATE INDEX IF NOT EXISTS weekly_office_program_performance_week_idx
    ON reporting.weekly_office_program_performance (week_start DESC);

-- --- Bitácora y lock de corridas ---------------------------------------------
CREATE TABLE IF NOT EXISTS reporting.pipeline_runs (
    run_id                    uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    pipeline_name             text        NOT NULL,
    trigger                   text        NOT NULL
                                          CHECK (trigger IN ('schedule', 'manual')),
    window_start              timestamptz NOT NULL,
    window_end                timestamptz NOT NULL,
    started_at                timestamptz NOT NULL DEFAULT now(),
    finished_at               timestamptz,
    status                    text        NOT NULL DEFAULT 'running'
                                          CHECK (status IN ('running', 'completed', 'failed', 'skipped')),
    phase                     text
                                          CHECK (phase IN ('extract', 'transform', 'load')),
    rows_read                 integer     NOT NULL DEFAULT 0,
    rows_written              integer     NOT NULL DEFAULT 0,
    rows_revised              integer     NOT NULL DEFAULT 0,
    rows_missing_cost         integer     NOT NULL DEFAULT 0,
    rows_unassigned_programme integer     NOT NULL DEFAULT 0,
    rows_invalid_office       integer     NOT NULL DEFAULT 0,
    rows_duplicates_dropped   integer     NOT NULL DEFAULT 0,
    error_message             text,
    prefect_flow_run_id       text
);

COMMENT ON TABLE reporting.pipeline_runs IS
    'Bitácora y lock de corridas de pipelines. Ver data/pipelines/PIPELINE_DESIGN.md §7.3.';

-- Lock por pipeline: como mucho una corrida `running` a la vez. El segundo
-- INSERT falla con unique_violation aunque su ventana sea distinta (D6).
CREATE UNIQUE INDEX IF NOT EXISTS pipeline_runs_running_uidx
    ON reporting.pipeline_runs (pipeline_name)
    WHERE status = 'running';

-- Consulta de `GET /reporting/pipeline-runs/latest`.
CREATE INDEX IF NOT EXISTS pipeline_runs_started_at_idx
    ON reporting.pipeline_runs (pipeline_name, started_at DESC);

-- --- Acceso ------------------------------------------------------------------
-- RLS sin políticas: PostgREST no expone las tablas a los roles anon/authenticated.
-- La API y el pipeline conectan como propietarios vía DATABASE_URL.
ALTER TABLE reporting.weekly_office_program_performance ENABLE ROW LEVEL SECURITY;
ALTER TABLE reporting.pipeline_runs ENABLE ROW LEVEL SECURITY;
