-- =============================================================================
-- 008 — Recepción y enrutamiento de RFPs (Hito 9 · Parte 1)
--
-- Tres tablas del ticket RFP (BRIEFING §2.3):
--   * rfp_ticket             — ciclo de vida del ticket + handoff a la Parte 2.
--   * rfp_metadata           — metadatos extraídos de la RFP y legibilidad (1:1).
--   * rfp_department_section — aspectos clave por departamento activo (1:N).
--
-- Como `incident` (006), estas tablas SÍ las crea `create_db_and_tables()`:
-- los modelos SQLModel viven en `data/pipelines/rfp_intake/models.py` y entran
-- en `SQLModel.metadata` cuando la API importa `rfp/pipeline_bridge.py`. Este
-- archivo deja el esquema versionado y sirve para crearlas a mano en Supabase
-- (SQL Editor). Idempotente.
--
-- `status` admite ya el vocabulario completo de las Partes 1–3, y las columnas
-- de borrador/evaluación/aprobación de `rfp_department_section` quedan a NULL
-- hasta la Parte 2, para que el mismo ticket continúe sin otra migración.
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.rfp_ticket (
    ticket_id          varchar(20)  PRIMARY KEY,
    rfp_id             varchar(120),
    status             varchar(30)  NOT NULL DEFAULT 'analizando',
    raw_pdf_path       varchar(500) NOT NULL,
    original_filename  varchar(255) NOT NULL,
    markdown           text,
    classification     jsonb,
    synthesis          jsonb,
    handoff_payload    jsonb,
    ready_for_drafting boolean      NOT NULL DEFAULT false,
    error_message      text,
    attempts           integer      NOT NULL DEFAULT 0,
    created_by         varchar(120),
    created_at         timestamptz  NOT NULL DEFAULT now(),
    updated_at         timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT ck_rfp_ticket_status CHECK (status IN (
        'analizando', 'descartado', 'analisis_completo',
        'generando_borrador', 'en_evaluación', 'needs_human_review',
        'esperando_aprobación', 'terminado'
    ))
);

CREATE INDEX IF NOT EXISTS ix_rfp_ticket_status             ON public.rfp_ticket (status);
CREATE INDEX IF NOT EXISTS ix_rfp_ticket_ready_for_drafting ON public.rfp_ticket (ready_for_drafting);

CREATE TABLE IF NOT EXISTS public.rfp_metadata (
    ticket_id          varchar(20)  PRIMARY KEY
                       REFERENCES public.rfp_ticket (ticket_id) ON DELETE CASCADE,
    client_name        varchar(255),
    client_hq          varchar(30)  NOT NULL DEFAULT 'desconocido',
    client_location    varchar(255),
    currency           varchar(3),
    rfp_type           varchar(20),
    services_requested jsonb        NOT NULL DEFAULT '[]'::jsonb,
    scope              text,
    volumes            jsonb        NOT NULL DEFAULT '[]'::jsonb,
    deadline           date,
    deadline_text      varchar(255),
    budget_range       varchar(255),
    client_contact     varchar(255),
    departments_needed jsonb        NOT NULL DEFAULT '[]'::jsonb,
    unmapped_services  jsonb        NOT NULL DEFAULT '[]'::jsonb,
    readability        jsonb,
    created_at         timestamptz  NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.rfp_department_section (
    id                 serial       PRIMARY KEY,
    ticket_id          varchar(20)  NOT NULL
                       REFERENCES public.rfp_ticket (ticket_id) ON DELETE CASCADE,
    department_id      varchar(30)  NOT NULL,
    owner_name         varchar(120) NOT NULL,
    key_aspects        jsonb        NOT NULL DEFAULT '[]'::jsonb,
    requests_to_owner  jsonb        NOT NULL DEFAULT '[]'::jsonb,
    open_questions     jsonb        NOT NULL DEFAULT '[]'::jsonb,
    figures_cited      jsonb        NOT NULL DEFAULT '[]'::jsonb,
    excerpts           jsonb        NOT NULL DEFAULT '[]'::jsonb,
    -- Partes 2 y 3
    draft_content      text,
    evaluation_results jsonb,
    approval_status    varchar(30),
    approver           varchar(120),
    approved_at        timestamptz,
    created_at         timestamptz  NOT NULL DEFAULT now(),
    updated_at         timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT uq_rfp_section_ticket_department UNIQUE (ticket_id, department_id),
    CONSTRAINT ck_rfp_section_department CHECK (department_id IN ('seleccion', 'capacitacion', 'soporte'))
);

CREATE INDEX IF NOT EXISTS ix_rfp_department_section_ticket_id ON public.rfp_department_section (ticket_id);

COMMENT ON TABLE public.rfp_ticket IS
    'Tickets de RFP (Hito 9). Handoff a la Parte 2: ready_for_drafting + handoff_payload. Ver docs/rfp/rfp-intake-design.md.';
COMMENT ON TABLE public.rfp_metadata IS
    'Metadatos extraídos de la RFP (cliente, sede → moneda, servicios, volúmenes literales) y legibilidad.';
COMMENT ON TABLE public.rfp_department_section IS
    'Aspectos clave por departamento activo (seleccion/capacitacion/soporte); Partes 2–3 añaden borrador y aprobación.';
