-- =============================================================================
-- 010 — Aprobación humana y cierre del documento de RFP (Hito 9 · Parte 3)
--
-- Igual que la 009: `create_db_and_tables()` crea las tablas nuevas, pero NO
-- añade columnas a tablas existentes, así que esta migración se aplica a mano en
-- Supabase (SQL Editor) antes de desplegar la Parte 3. Idempotente.
--
-- rfp_department_section (por departamento):
--   * approval_status        — pendiente | procesando | aprobada | cambios_solicitados | rechazada
--   * expected_approver      — quién debe decidir: el responsable (BRIEFING §2.1) o el árbitro fijo (§7)
--   * allowed_decisions      — decisiones válidas en la pausa actual (approve / request_changes / reject)
--   * approval_round         — rondas de cambios consumidas (límite RFP_MAX_APPROVAL_ROUNDS)
--   * approval_requested_at  — inicio de la pausa (KPI: tiempo de aprobación por departamento)
--   * approval_comment       — último comentario del aprobador
--   * decided_by_user        — usuario del backoffice que registró la decisión en nombre del responsable
--   * approval_history       — [{round, decision, acting_as, decided_by_user, comment, by_rule, decided_at}]
--   (approver / approved_at ya existían desde la 008)
--
-- Tablas nuevas: rfp_final_document, rfp_trace_event, rfp_arbitration.
--
-- Los checkpoints de LangGraph (`checkpoints`, `checkpoint_writes`,
-- `checkpoint_blobs`, `checkpoint_migrations`) los crea `PostgresSaver.setup()`
-- la primera vez que corre el worker; no hace falta crearlos aquí.
-- =============================================================================

ALTER TABLE public.rfp_department_section
    ADD COLUMN IF NOT EXISTS expected_approver     varchar(120),
    ADD COLUMN IF NOT EXISTS allowed_decisions     jsonb       NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS approval_round        integer     NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS approval_requested_at timestamptz,
    ADD COLUMN IF NOT EXISTS approval_comment      text,
    ADD COLUMN IF NOT EXISTS decided_by_user       varchar(255),
    ADD COLUMN IF NOT EXISTS approval_history      jsonb       NOT NULL DEFAULT '[]'::jsonb;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_rfp_section_approval_status') THEN
        ALTER TABLE public.rfp_department_section
            ADD CONSTRAINT ck_rfp_section_approval_status CHECK (
                approval_status IS NULL
                OR approval_status IN ('pendiente', 'procesando', 'aprobada', 'cambios_solicitados', 'rechazada')
            );
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS public.rfp_final_document (
    ticket_id        varchar(20) PRIMARY KEY REFERENCES public.rfp_ticket (ticket_id) ON DELETE CASCADE,
    currency         varchar(3),
    sections         jsonb       NOT NULL DEFAULT '[]'::jsonb,
    approvals        jsonb       NOT NULL DEFAULT '[]'::jsonb,
    content_markdown text        NOT NULL,
    generated_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.rfp_trace_event (
    id          serial PRIMARY KEY,
    ticket_id   varchar(20) NOT NULL REFERENCES public.rfp_ticket (ticket_id) ON DELETE CASCADE,
    part        varchar(20) NOT NULL,
    thread_id   varchar(80),
    node        varchar(60) NOT NULL,
    agent       varchar(80) NOT NULL,
    status      varchar(20) NOT NULL,
    input       jsonb,
    output      jsonb,
    started_at  timestamptz NOT NULL,
    finished_at timestamptz NOT NULL,
    duration_ms integer     NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_rfp_trace_event_ticket_id ON public.rfp_trace_event (ticket_id);

CREATE TABLE IF NOT EXISTS public.rfp_arbitration (
    id             serial PRIMARY KEY,
    ticket_id      varchar(20) NOT NULL REFERENCES public.rfp_ticket (ticket_id) ON DELETE CASCADE,
    department_id  varchar(30) NOT NULL,
    trigger_id     varchar(40) NOT NULL,
    arbiter        varchar(120) NOT NULL,
    action         varchar(30) NOT NULL,
    resolution     text        NOT NULL,
    evidence       jsonb       NOT NULL DEFAULT '{}'::jsonb,
    approval_round integer     NOT NULL DEFAULT 0,
    created_at     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_rfp_arbitration_round UNIQUE (ticket_id, department_id, trigger_id, approval_round)
);
CREATE INDEX IF NOT EXISTS ix_rfp_arbitration_ticket_id ON public.rfp_arbitration (ticket_id);

COMMENT ON TABLE public.rfp_trace_event IS
    'Trazabilidad del flujo RFP (Partes 1–3): agente, input, output y timestamps de cada ejecución de nodo.';
COMMENT ON TABLE public.rfp_arbitration IS
    'Triggers de conflicto del BRIEFING §7 detectados en estado estructurado y resueltos por el árbitro fijo.';
