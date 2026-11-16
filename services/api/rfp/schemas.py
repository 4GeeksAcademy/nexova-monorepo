"""Modelos de respuesta de `/rfp` (recepción de RFPs, Hito 9 · Parte 1)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

TicketStatus = Literal[
    "analizando",
    "descartado",
    "analisis_completo",
    "generando_borrador",
    "en_evaluación",
    "needs_human_review",
    "esperando_aprobación",
    "terminado",
]
DepartmentId = Literal["seleccion", "capacitacion", "soporte"]


class RfpTicketAccepted(BaseModel):
    ticket_id: str
    status: TicketStatus
    task_id: str


class RfpTicketSummary(BaseModel):
    ticket_id: str
    rfp_id: str | None = None
    status: TicketStatus
    original_filename: str
    client_name: str | None = None
    client_hq: str | None = None
    currency: str | None = None
    departments_needed: list[DepartmentId] = Field(default_factory=list)
    error_message: str | None = None
    attempts: int = 0
    created_at: datetime
    updated_at: datetime


class RfpMetadataRead(BaseModel):
    client_name: str | None = None
    client_hq: str
    client_location: str | None = None
    currency: str | None = None
    rfp_type: str | None = None
    services_requested: list[str] = Field(default_factory=list)
    scope: str | None = None
    volumes: list[dict[str, Any]] = Field(default_factory=list)
    deadline: str | None = None
    deadline_text: str | None = None
    budget_range: str | None = None
    client_contact: str | None = None
    departments_needed: list[DepartmentId] = Field(default_factory=list)
    unmapped_services: list[str] = Field(default_factory=list)
    readability: dict[str, Any] | None = None


class DepartmentSectionRead(BaseModel):
    department_id: DepartmentId
    owner_name: str
    key_aspects: list[str] = Field(default_factory=list)
    requests_to_owner: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    figures_cited: list[dict[str, Any]] = Field(default_factory=list)
    excerpts: list[str] = Field(default_factory=list)
    approval_status: str | None = None


class RfpTicketDetail(RfpTicketSummary):
    raw_pdf_path: str
    classification: dict[str, Any] | None = None
    synthesis: dict[str, Any] | None = None
    handoff_payload: dict[str, Any] | None = None
    ready_for_drafting: bool = False
    metadata: RfpMetadataRead | None = None
    sections: list[DepartmentSectionRead] = Field(default_factory=list)
