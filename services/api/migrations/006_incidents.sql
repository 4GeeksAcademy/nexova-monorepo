-- =============================================================================
-- 006 — Tickets del gestor de incidentes (`incident`)
--
-- Una fila por ticket válido de los CSV analizados con
-- `POST /api/incidents/analyze` (upsert por `ticket_id`). La leen
-- `GET /api/incidents` y `GET /api/incidents/{ticket_id}`, que a su vez
-- consulta la tool de tickets del agente LangGraph (Parte 2).
--
-- A diferencia de la 002–005, esta tabla SÍ la crea `create_db_and_tables()`
-- (modelo SQLModel `Incident` en `services/api/models.py`, como el
-- inventario). Este archivo deja el esquema versionado y sirve para crearla a
-- mano si hiciera falta. Idempotente.
--
-- Privacidad: NO hay columna `customer_email`.
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.incident (
    ticket_id          varchar     PRIMARY KEY,
    date               date        NOT NULL,
    client_company     varchar     NOT NULL,
    category           varchar     NOT NULL,
    description        varchar     NOT NULL,
    agent_id           varchar     NOT NULL,
    status             varchar     NOT NULL,
    satisfaction_score integer,
    updated_at         timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_incident_client_company ON public.incident (client_company);
CREATE INDEX IF NOT EXISTS ix_incident_category       ON public.incident (category);
CREATE INDEX IF NOT EXISTS ix_incident_status         ON public.incident (status);

COMMENT ON TABLE public.incident IS
    'Tickets válidos del gestor de incidentes (sin emails). Ver services/api/app/incidents_store.py.';
