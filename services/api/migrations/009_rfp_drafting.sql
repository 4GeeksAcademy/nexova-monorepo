-- =============================================================================
-- 009 — Generación y evaluación de respuestas a RFP (Hito 9 · Parte 2)
--
-- Amplía las tablas de la 008. `create_db_and_tables()` NO añade columnas a
-- tablas que ya existen, así que esta migración hay que aplicarla a mano en
-- Supabase (SQL Editor) antes de desplegar la Parte 2. Idempotente.
--
-- rfp_department_section (por departamento):
--   * draft_content / evaluation_results — ya existían (008): último borrador y
--     último EvaluationResult.
--   * section_status   — generando | evaluando | evaluada_ok | needs_human_review
--   * iteration_count  — iteraciones del ciclo generador-evaluador consumidas.
--   * iteration_history— [{iteration, draft_content, evaluation}] de cada vuelta.
--   * generated_at     — cuándo se escribió el último borrador.
--
-- rfp_ticket:
--   * ready_for_approval — handoff listo para la Parte 3.
--   * approval_handoff   — contrato `rfp_drafting.v1` (ver docs/rfp/rfp-drafting-design.md).
-- =============================================================================

ALTER TABLE public.rfp_department_section
    ADD COLUMN IF NOT EXISTS section_status    varchar(30),
    ADD COLUMN IF NOT EXISTS iteration_count   integer     NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS iteration_history jsonb       NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS generated_at      timestamptz;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_rfp_section_status') THEN
        ALTER TABLE public.rfp_department_section
            ADD CONSTRAINT ck_rfp_section_status CHECK (
                section_status IS NULL
                OR section_status IN ('generando', 'evaluando', 'evaluada_ok', 'needs_human_review')
            );
    END IF;
END $$;

ALTER TABLE public.rfp_ticket
    ADD COLUMN IF NOT EXISTS ready_for_approval boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS approval_handoff   jsonb;

CREATE INDEX IF NOT EXISTS ix_rfp_ticket_ready_for_approval ON public.rfp_ticket (ready_for_approval);

COMMENT ON COLUMN public.rfp_ticket.approval_handoff IS
    'Handoff a la Parte 3 (contract_version rfp_drafting.v1): borrador + EvaluationResult por departamento.';
COMMENT ON COLUMN public.rfp_department_section.iteration_history IS
    'Historial del ciclo generador-evaluador: [{iteration, draft_content, evaluation}].';
