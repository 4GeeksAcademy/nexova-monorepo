-- =============================================================================
-- 007 — Memoria persistente del agente (Hito 8 · Parte 1)
--
-- Tres tablas, todas FUERA de la base de conocimiento RAG (`nexova_knowledge`
-- en Qdrant, que sigue siendo de solo lectura):
--
--   agent_memory_proposals  una fila por propuesta que el agente hizo al usuario.
--                           Solo puede pasar de `pending` a un estado final; una
--                           vez decidida es inmutable.
--   agent_memory_entries    hechos aprobados (la memoria que el agente lee).
--                           Nunca se borran: olvidar = cambiar `status`.
--   agent_memory_audit      bitácora append-only de cada paso (propuesta,
--                           decisión, escritura, deduplicación, expiración…).
--
-- Igual que 002 (`telemetry_events`), NO la crea `SQLModel.metadata.create_all`:
-- los triggers y la RLS no se expresan ahí. Se aplica a mano en el SQL Editor
-- de Supabase (o `psql "$DATABASE_URL" -f ...`). Idempotente.
-- Ver docs/agent/memory-design.md.
-- =============================================================================

-- --- Propuestas ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.agent_memory_proposals (
    id                  uuid        PRIMARY KEY,
    conversation_id     uuid        NOT NULL,
    user_id             text        NOT NULL,
    fact                text        NOT NULL CHECK (char_length(fact) BETWEEN 10 AND 400),
    business_line       text        NOT NULL
                                    CHECK (business_line IN ('headhunting', 'support', 'training')),
    category            text        NOT NULL
                                    CHECK (category IN ('escalation_procedure', 'client_preference', 'incident_pattern')),
    reason              text,
    source_message      text        NOT NULL,
    status              text        NOT NULL DEFAULT 'pending'
                                    CHECK (status IN ('pending', 'approved', 'edited', 'rejected', 'discarded')),
    decision_label      text        CHECK (decision_label IN ('approve', 'reject', 'edit', 'unrelated', 'ambiguous', 'expired')),
    decision_confidence real        CHECK (decision_confidence BETWEEN 0 AND 1),
    decision_message    text,
    final_fact          text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    decided_at          timestamptz,
    CONSTRAINT agent_memory_proposals_decided_chk
        CHECK ((status = 'pending') = (decided_at IS NULL))
);

-- Una sola propuesta pendiente por conversación (el lock lo pone la base de datos).
CREATE UNIQUE INDEX IF NOT EXISTS agent_memory_proposals_one_pending_uidx
    ON public.agent_memory_proposals (conversation_id) WHERE status = 'pending';

-- Límite de propuestas por usuario y día (anti-envenenamiento).
CREATE INDEX IF NOT EXISTS agent_memory_proposals_user_created_idx
    ON public.agent_memory_proposals (user_id, created_at);

CREATE OR REPLACE FUNCTION public.agent_memory_proposals_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'agent_memory_proposals: DELETE no está permitido (registro auditable)';
    END IF;
    IF OLD.status <> 'pending' THEN
        RAISE EXCEPTION 'agent_memory_proposals: la propuesta % ya está decidida y es inmutable', OLD.id;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS agent_memory_proposals_guard_trg ON public.agent_memory_proposals;
CREATE TRIGGER agent_memory_proposals_guard_trg
    BEFORE UPDATE OR DELETE ON public.agent_memory_proposals
    FOR EACH ROW EXECUTE FUNCTION public.agent_memory_proposals_guard();

-- --- Hechos aprobados -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.agent_memory_entries (
    id                uuid        PRIMARY KEY,
    business_line     text        NOT NULL
                                  CHECK (business_line IN ('headhunting', 'support', 'training')),
    category          text        NOT NULL
                                  CHECK (category IN ('escalation_procedure', 'client_preference', 'incident_pattern')),
    fact              text        NOT NULL CHECK (char_length(fact) BETWEEN 10 AND 400),
    fingerprint       text        NOT NULL,
    approved_by       text        NOT NULL,
    proposal_id       uuid        NOT NULL REFERENCES public.agent_memory_proposals (id),
    status            text        NOT NULL DEFAULT 'active'
                                  CHECK (status IN ('active', 'superseded', 'expired')),
    status_reason     text,
    supersedes_id     uuid        REFERENCES public.agent_memory_entries (id),
    use_count         integer     NOT NULL DEFAULT 0,
    created_at        timestamptz NOT NULL DEFAULT now(),
    last_used_at      timestamptz,
    status_changed_at timestamptz
);

-- Deduplicación exacta: un mismo hecho normalizado solo puede estar activo una vez.
CREATE UNIQUE INDEX IF NOT EXISTS agent_memory_entries_active_fingerprint_uidx
    ON public.agent_memory_entries (fingerprint) WHERE status = 'active';

CREATE INDEX IF NOT EXISTS agent_memory_entries_active_bucket_idx
    ON public.agent_memory_entries (business_line, category) WHERE status = 'active';

CREATE OR REPLACE FUNCTION public.agent_memory_entries_no_delete()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'agent_memory_entries: % no está permitido; olvidar = cambiar status', TG_OP;
END;
$$;

DROP TRIGGER IF EXISTS agent_memory_entries_no_delete_trg ON public.agent_memory_entries;
CREATE TRIGGER agent_memory_entries_no_delete_trg
    BEFORE DELETE ON public.agent_memory_entries
    FOR EACH ROW EXECUTE FUNCTION public.agent_memory_entries_no_delete();

DROP TRIGGER IF EXISTS agent_memory_entries_no_truncate_trg ON public.agent_memory_entries;
CREATE TRIGGER agent_memory_entries_no_truncate_trg
    BEFORE TRUNCATE ON public.agent_memory_entries
    FOR EACH STATEMENT EXECUTE FUNCTION public.agent_memory_entries_no_delete();

-- --- Auditoría (append-only) ------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.agent_memory_audit (
    id              bigserial   PRIMARY KEY,
    event           text        NOT NULL CHECK (event IN (
                        'proposed', 'blocked', 'approved', 'edited', 'rejected', 'discarded',
                        'written', 'deduplicated', 'superseded', 'expired', 'evicted'
                    )),
    proposal_id     uuid,
    entry_id        uuid,
    conversation_id uuid,
    user_id         text,
    message         text,
    details         jsonb       NOT NULL DEFAULT '{}'::jsonb,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS agent_memory_audit_proposal_idx ON public.agent_memory_audit (proposal_id);
CREATE INDEX IF NOT EXISTS agent_memory_audit_conversation_idx ON public.agent_memory_audit (conversation_id);
CREATE INDEX IF NOT EXISTS agent_memory_audit_created_idx ON public.agent_memory_audit (created_at);

CREATE OR REPLACE FUNCTION public.agent_memory_audit_immutable()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'agent_memory_audit es append-only: % no está permitido', TG_OP;
END;
$$;

DROP TRIGGER IF EXISTS agent_memory_audit_no_update_delete ON public.agent_memory_audit;
CREATE TRIGGER agent_memory_audit_no_update_delete
    BEFORE UPDATE OR DELETE ON public.agent_memory_audit
    FOR EACH ROW EXECUTE FUNCTION public.agent_memory_audit_immutable();

DROP TRIGGER IF EXISTS agent_memory_audit_no_truncate ON public.agent_memory_audit;
CREATE TRIGGER agent_memory_audit_no_truncate
    BEFORE TRUNCATE ON public.agent_memory_audit
    FOR EACH STATEMENT EXECUTE FUNCTION public.agent_memory_audit_immutable();

-- --- Acceso ------------------------------------------------------------------
-- RLS sin políticas: PostgREST no expone las tablas a anon/authenticated.
-- La API conecta como propietaria vía DATABASE_URL.
ALTER TABLE public.agent_memory_proposals ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.agent_memory_entries   ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.agent_memory_audit     ENABLE ROW LEVEL SECURITY;
