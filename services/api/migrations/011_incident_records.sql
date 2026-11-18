-- 011 · Gestor de incidencias centralizado (README + BRIEFING de Nexova)
--
-- Referencia versionada del esquema de `incident_records` e `incident_seed_log`.
-- La API (`create_all()` al arrancar) y `scripts/seed_incidents.py` ya crean estas
-- tablas; este fichero sirve para aplicarlas a mano en Supabase o auditarlas.
-- Es independiente de la tabla `incident` del analizador (006), que no se toca.
--
-- Los valores permitidos coinciden con packages/shared/nexova_shared/incident_domain.py.

CREATE TABLE IF NOT EXISTS public.incident_records (
    id          SERIAL PRIMARY KEY,
    title       VARCHAR(120)  NOT NULL,
    description VARCHAR(5000) NOT NULL,
    category    VARCHAR       NOT NULL,
    status      VARCHAR       NOT NULL DEFAULT 'open',
    origin      VARCHAR       NOT NULL,
    branch      VARCHAR       NOT NULL,
    created_at  TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ   NOT NULL DEFAULT now(),
    CONSTRAINT ck_incident_records_status CHECK (status IN ('open', 'in_progress', 'resolved', 'discarded')),
    CONSTRAINT ck_incident_records_category CHECK (category IN (
        'technical_failure', 'process_error', 'client_complaint', 'candidate_issue',
        'staff_issue', 'sla_breach', 'data_quality', 'other'
    )),
    CONSTRAINT ck_incident_records_origin CHECK (origin IN ('customer', 'branch', 'internal')),
    CONSTRAINT ck_incident_records_branch CHECK (branch IN ('central', 'valencia_operations', 'miami_office', 'remote')),
    CONSTRAINT ck_incident_records_title CHECK (length(trim(title)) > 0),
    CONSTRAINT ck_incident_records_description CHECK (length(trim(description)) > 0)
);

CREATE INDEX IF NOT EXISTS ix_incident_records_status   ON public.incident_records (status);
CREATE INDEX IF NOT EXISTS ix_incident_records_category ON public.incident_records (category);
CREATE INDEX IF NOT EXISTS ix_incident_records_origin   ON public.incident_records (origin);
CREATE INDEX IF NOT EXISTS ix_incident_records_branch   ON public.incident_records (branch);

-- Claves del CSV ya cargadas: hace idempotente scripts/seed_incidents.py sin
-- almacenar el ticket_id en el modelo (BRIEFING).
CREATE TABLE IF NOT EXISTS public.incident_seed_log (
    source_key  VARCHAR PRIMARY KEY,
    incident_id INTEGER NOT NULL REFERENCES public.incident_records (id)
);

CREATE INDEX IF NOT EXISTS ix_incident_seed_log_incident_id ON public.incident_seed_log (incident_id);
