"""
Tablas SQLModel del flujo RFP (Postgres / Supabase).

Esquema versionado en `services/api/migrations/008_rfp_intake.sql`. Como la
tabla `incident` (006), estas clases también entran en `SQLModel.metadata`:
cuando la API importa el puente (`services/api/rfp/pipeline_bridge.py`) antes
del `startup`, `create_db_and_tables()` las crea si no existen.

`rfp_department_section` ya trae las columnas de las Partes 2 y 3 (borrador,
evaluaciones, aprobación) a `NULL`, para que el mismo ticket continúe sin migrar.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import CheckConstraint, Column, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import DateTime
from sqlmodel import Field, SQLModel

from rfp_intake.departments import APPROVAL_STATUSES, DEPARTMENT_IDS, SECTION_STATUSES, TICKET_STATUSES


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _in_list(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({quoted})"


class RfpTicket(SQLModel, table=True):
    __tablename__ = "rfp_ticket"
    __table_args__ = (
        CheckConstraint(_in_list("status", TICKET_STATUSES), name="ck_rfp_ticket_status"),
    )

    ticket_id: str = Field(primary_key=True, max_length=20)
    rfp_id: str | None = Field(default=None, max_length=120)
    status: str = Field(default="analizando", max_length=30, index=True)
    raw_pdf_path: str = Field(max_length=500)
    original_filename: str = Field(max_length=255)
    markdown: str | None = Field(default=None, sa_type=Text)
    classification: dict[str, Any] | None = Field(default=None, sa_type=JSONB)
    synthesis: dict[str, Any] | None = Field(default=None, sa_type=JSONB)
    handoff_payload: dict[str, Any] | None = Field(default=None, sa_type=JSONB)
    ready_for_drafting: bool = Field(default=False, index=True)
    # --- Parte 2 (migración 009) -------------------------------------------
    ready_for_approval: bool = Field(default=False, index=True)
    approval_handoff: dict[str, Any] | None = Field(default=None, sa_type=JSONB)
    error_message: str | None = Field(default=None, sa_type=Text)
    attempts: int = Field(default=0)
    created_by: str | None = Field(default=None, max_length=120)
    created_at: datetime = Field(default_factory=utcnow, sa_type=DateTime(timezone=True))
    updated_at: datetime = Field(default_factory=utcnow, sa_type=DateTime(timezone=True))


class RfpMetadata(SQLModel, table=True):
    __tablename__ = "rfp_metadata"

    ticket_id: str = Field(
        sa_column=Column(
            String(20), ForeignKey("rfp_ticket.ticket_id", ondelete="CASCADE"), primary_key=True
        )
    )
    client_name: str | None = Field(default=None, max_length=255)
    client_hq: str = Field(default="desconocido", max_length=30)
    client_location: str | None = Field(default=None, max_length=255)
    currency: str | None = Field(default=None, max_length=3)
    rfp_type: str | None = Field(default=None, max_length=20)
    services_requested: list[str] = Field(default_factory=list, sa_type=JSONB)
    scope: str | None = Field(default=None, sa_type=Text)
    volumes: list[dict[str, Any]] = Field(default_factory=list, sa_type=JSONB)
    deadline: date | None = Field(default=None)
    deadline_text: str | None = Field(default=None, max_length=255)
    budget_range: str | None = Field(default=None, max_length=255)
    client_contact: str | None = Field(default=None, max_length=255)
    departments_needed: list[str] = Field(default_factory=list, sa_type=JSONB)
    unmapped_services: list[str] = Field(default_factory=list, sa_type=JSONB)
    readability: dict[str, Any] | None = Field(default=None, sa_type=JSONB)
    created_at: datetime = Field(default_factory=utcnow, sa_type=DateTime(timezone=True))


class RfpDepartmentSection(SQLModel, table=True):
    __tablename__ = "rfp_department_section"
    __table_args__ = (
        UniqueConstraint("ticket_id", "department_id", name="uq_rfp_section_ticket_department"),
        CheckConstraint(_in_list("department_id", DEPARTMENT_IDS), name="ck_rfp_section_department"),
        CheckConstraint(
            f"section_status IS NULL OR {_in_list('section_status', SECTION_STATUSES)}",
            name="ck_rfp_section_status",
        ),
        CheckConstraint(
            f"approval_status IS NULL OR {_in_list('approval_status', APPROVAL_STATUSES)}",
            name="ck_rfp_section_approval_status",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    ticket_id: str = Field(
        sa_column=Column(
            String(20),
            ForeignKey("rfp_ticket.ticket_id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    department_id: str = Field(max_length=30)
    owner_name: str = Field(max_length=120)
    key_aspects: list[str] = Field(default_factory=list, sa_type=JSONB)
    requests_to_owner: list[str] = Field(default_factory=list, sa_type=JSONB)
    open_questions: list[str] = Field(default_factory=list, sa_type=JSONB)
    figures_cited: list[dict[str, Any]] = Field(default_factory=list, sa_type=JSONB)
    excerpts: list[str] = Field(default_factory=list, sa_type=JSONB)
    # --- Partes 2 y 3 (NULL en la Parte 1) ---------------------------------
    draft_content: str | None = Field(default=None, sa_type=Text)
    evaluation_results: dict[str, Any] | None = Field(default=None, sa_type=JSONB)
    approval_status: str | None = Field(default=None, max_length=30)
    approver: str | None = Field(default=None, max_length=120)
    approved_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    # --- Parte 2: ciclo generador-evaluador (migración 009) ---------------
    section_status: str | None = Field(default=None, max_length=30)
    iteration_count: int = Field(default=0)
    iteration_history: list[dict[str, Any]] = Field(default_factory=list, sa_type=JSONB)
    generated_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    # --- Parte 3: aprobación humana (migración 010) -----------------------
    expected_approver: str | None = Field(default=None, max_length=120)   # responsable o árbitro fijo
    allowed_decisions: list[str] = Field(default_factory=list, sa_type=JSONB)
    approval_round: int = Field(default=0)
    approval_requested_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    approval_comment: str | None = Field(default=None, sa_type=Text)
    decided_by_user: str | None = Field(default=None, max_length=255)    # usuario real que registró
    approval_history: list[dict[str, Any]] = Field(default_factory=list, sa_type=JSONB)
    created_at: datetime = Field(default_factory=utcnow, sa_type=DateTime(timezone=True))
    updated_at: datetime = Field(default_factory=utcnow, sa_type=DateTime(timezone=True))


def _ticket_fk(primary_key: bool = False) -> Column:
    return Column(
        String(20), ForeignKey("rfp_ticket.ticket_id", ondelete="CASCADE"),
        primary_key=primary_key, nullable=False, index=not primary_key,
    )


class RfpFinalDocument(SQLModel, table=True):
    """Documento final consolidado (BRIEFING §2.3 · FinalDocument). Uno por ticket."""

    __tablename__ = "rfp_final_document"

    ticket_id: str = Field(sa_column=_ticket_fk(primary_key=True))
    currency: str | None = Field(default=None, max_length=3)
    sections: list[dict[str, Any]] = Field(default_factory=list, sa_type=JSONB)
    approvals: list[dict[str, Any]] = Field(default_factory=list, sa_type=JSONB)
    content_markdown: str = Field(sa_type=Text)
    generated_at: datetime = Field(default_factory=utcnow, sa_type=DateTime(timezone=True))


class RfpTraceEvent(SQLModel, table=True):
    """Trazabilidad: una fila por ejecución de nodo (agente, input, output, timestamp)."""

    __tablename__ = "rfp_trace_event"

    id: int | None = Field(default=None, primary_key=True)
    ticket_id: str = Field(sa_column=_ticket_fk())
    part: str = Field(max_length=20)                 # intake | drafting | approval
    thread_id: str | None = Field(default=None, max_length=80)
    node: str = Field(max_length=60)
    agent: str = Field(max_length=80)
    status: str = Field(max_length=20)               # ok | interrumpido | error
    input: dict[str, Any] | None = Field(default=None, sa_type=JSONB)
    output: dict[str, Any] | None = Field(default=None, sa_type=JSONB)
    started_at: datetime = Field(sa_type=DateTime(timezone=True))
    finished_at: datetime = Field(sa_type=DateTime(timezone=True))
    duration_ms: int = Field(default=0)


class RfpArbitration(SQLModel, table=True):
    """Conflicto detectado en estado estructurado y su resolución por el árbitro fijo (BRIEFING §7)."""

    __tablename__ = "rfp_arbitration"
    __table_args__ = (
        UniqueConstraint("ticket_id", "department_id", "trigger_id", "approval_round",
                         name="uq_rfp_arbitration_round"),
    )

    id: int | None = Field(default=None, primary_key=True)
    ticket_id: str = Field(sa_column=_ticket_fk())
    department_id: str = Field(max_length=30)       # sección sobre la que actúa la resolución
    trigger_id: str = Field(max_length=40)
    arbiter: str = Field(max_length=120)
    action: str = Field(max_length=30)              # request_changes | reject | escalate
    resolution: str = Field(sa_type=Text)
    evidence: dict[str, Any] = Field(default_factory=dict, sa_type=JSONB)
    approval_round: int = Field(default=0)
    created_at: datetime = Field(default_factory=utcnow, sa_type=DateTime(timezone=True))
