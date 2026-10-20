-- =============================================================================
-- 005 — Dead Letter Queue de la cola de tareas (`task_dead_letters`)
--
-- Una fila por tarea Celery que agotó sus reintentos (o falló con un error no
-- reintentable), ticket #DEV-55. La escribe el worker desde
-- `services/api/tasks/base.py` (`ObservedTask.on_failure`).
--
-- No sustituye a `reporting.pipeline_runs` (003) ni a `public.job_runs` (004):
-- esas registran cada corrida; esta, solo las tareas que acabaron muertas.
--
-- Igual que la 002–004, NO la crea `create_db_and_tables()`. Se aplica a mano
-- en el SQL Editor de Supabase (o `psql "$DATABASE_URL" -f ...`).
-- Idempotente: se puede ejecutar más de una vez sin efectos.
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.task_dead_letters (
    id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id       text        NOT NULL,
    task_name     text        NOT NULL,
    attempt       integer     NOT NULL CHECK (attempt >= 1),
    error_type    text,
    error_message text        NOT NULL,
    traceback     text,
    -- Solo los argumentos ligeros del mensaje (ids, fechas): nunca payloads.
    task_kwargs   jsonb       NOT NULL DEFAULT '{}'::jsonb,
    failed_at     timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.task_dead_letters IS
    'Tareas Celery que agotaron sus reintentos (DLQ). Ver services/api/tasks/base.py.';

-- --- Índices -----------------------------------------------------------------
-- "¿Esta tarea acabó en la DLQ?" (desde un task_id devuelto por la API).
CREATE INDEX IF NOT EXISTS task_dead_letters_task_id_idx
    ON public.task_dead_letters (task_id);

-- Revisión de los últimos fallos.
CREATE INDEX IF NOT EXISTS task_dead_letters_failed_at_idx
    ON public.task_dead_letters (failed_at DESC);

-- --- Acceso ------------------------------------------------------------------
-- RLS sin políticas: PostgREST no expone la tabla a los roles anon/authenticated.
-- El worker conecta como propietario vía DATABASE_URL.
ALTER TABLE public.task_dead_letters ENABLE ROW LEVEL SECURITY;
