"""Modelos de respuesta de `/rfp` (recepción de RFPs, Hito 9 · Parte 1)."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

# Rastros de un error crudo de proveedor/infraestructura que nunca deben llegar a la UI
# (auditoría OWASP A05/A09): URLs, cadena LiteLLM/OpenRouter, códigos HTTP, JSON del proveedor.
_LEAK_RE = re.compile(
    r"https?://|litellm|openrouter|openai|APIError|Error code:|Exception|Traceback|"
    r"\{\s*\"|api[_ -]?key|redis://|postgres(ql)?://",
    re.IGNORECASE,
)
_LEAK_DETAIL = "detalle técnico en los logs del worker"


def public_error_message(message: str | None) -> str | None:
    """Defensa en profundidad sobre `error_message` ya guardado (incluidos los anteriores a la
    auditoría): si contiene detalles internos, se conserva solo la frase inicial ("… falló")."""
    if not message or not _LEAK_RE.search(message):
        return message
    head = message.split(" (", 1)[0].split(": ", 1)
    prefix = head[0] if len(head) == 1 else f"{head[0]}: {head[1]}"
    if _LEAK_RE.search(prefix):
        prefix = "La última tarea de este ticket falló"
    retry = re.search(r"reintento en \d+ s\.?$", message)
    return f"{prefix.rstrip('. ')} ({_LEAK_DETAIL})" + (f"; {retry.group(0)}" if retry else ".")

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
SectionStatus = Literal["generando", "evaluando", "evaluada_ok", "needs_human_review"]
ApprovalStatus = Literal["pendiente", "procesando", "aprobada", "cambios_solicitados", "rechazada"]
DecisionKind = Literal["approve", "request_changes", "reject"]


class RfpDraftingAccepted(BaseModel):
    ticket_id: str
    status: TicketStatus
    task_id: str


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

    @field_validator("error_message")
    @classmethod
    def _hide_internal_details(cls, value: str | None) -> str | None:
        return public_error_message(value)
    attempts: int = 0
    ready_for_approval: bool = False
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
    # Parte 2: borrador y EvaluationResult del ciclo generador-evaluador.
    section_status: SectionStatus | None = None
    draft_content: str | None = None
    evaluation_results: dict[str, Any] | None = None
    iteration_count: int = 0
    iteration_history: list[dict[str, Any]] = Field(default_factory=list)
    generated_at: datetime | None = None
    # Parte 3: aprobación humana.
    approval_status: ApprovalStatus | None = None
    expected_approver: str | None = None
    allowed_decisions: list[DecisionKind] = Field(default_factory=list)
    approval_round: int = 0
    approval_requested_at: datetime | None = None
    approval_comment: str | None = None
    approver: str | None = None
    approved_at: datetime | None = None
    decided_by_user: str | None = None
    approval_history: list[dict[str, Any]] = Field(default_factory=list)


class ArbitrationRead(BaseModel):
    department_id: DepartmentId
    trigger_id: str
    arbiter: str
    action: str
    resolution: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    approval_round: int = 0
    created_at: datetime


class FinalDocumentRead(BaseModel):
    ticket_id: str
    currency: str | None = None
    sections: list[dict[str, Any]] = Field(default_factory=list)
    approvals: list[dict[str, Any]] = Field(default_factory=list)
    content_markdown: str
    generated_at: datetime


class RfpTicketDetail(RfpTicketSummary):
    raw_pdf_path: str
    classification: dict[str, Any] | None = None
    synthesis: dict[str, Any] | None = None
    handoff_payload: dict[str, Any] | None = None
    ready_for_drafting: bool = False
    approval_handoff: dict[str, Any] | None = None
    metadata: RfpMetadataRead | None = None
    sections: list[DepartmentSectionRead] = Field(default_factory=list)
    # Parte 3
    max_approval_rounds: int | None = None
    arbitrations: list[ArbitrationRead] = Field(default_factory=list)
    final_document: FinalDocumentRead | None = None


class ApprovalDecisionIn(BaseModel):
    """Decisión humana desde el backoffice. `acting_as`: responsable (o árbitro) en cuyo nombre se decide."""

    decision: DecisionKind
    acting_as: str = Field(min_length=1, max_length=120)
    comment: str = Field(default="", max_length=2000)


class RfpDecisionAccepted(BaseModel):
    ticket_id: str
    department_id: DepartmentId
    decision: DecisionKind
    acting_as: str
    approval_status: ApprovalStatus
    task_id: str


class RfpApprovalAccepted(BaseModel):
    ticket_id: str
    department_ids: list[DepartmentId]
    task_ids: dict[str, str | None]


class TraceEventRead(BaseModel):
    seq: int
    part: str
    thread_id: str | None = None
    node: str
    agent: str
    status: str
    input: dict[str, Any] | None = None
    output: dict[str, Any] | None = None
    started_at: datetime
    finished_at: datetime
    duration_ms: int = 0
