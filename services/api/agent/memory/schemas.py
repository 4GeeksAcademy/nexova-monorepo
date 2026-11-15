"""
Contratos de la memoria del agente.

- `MemoryCandidate`: lo que el LLM propone recordar (campo `propuesta_memoria`
  de su salida estructurada). Aún no es memoria: es una propuesta.
- `StructuredAnswer`: salida de la única llamada de generación (respuesta +
  propuesta opcional).
- `MemoryProposal`: la propuesta ya registrada, con su ciclo de vida
  (`pending` → `approved` | `edited` | `rejected` | `discarded`).
- `PendingDecision`: clasificación explícita del mensaje del usuario frente a
  la propuesta pendiente.
- `MemoryFact`: hecho aprobado (lo único que el agente lee de la memoria).
- `AuditEvent`: fila de la bitácora append-only.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

BusinessLine = Literal["headhunting", "support", "training"]
MemoryCategory = Literal["escalation_procedure", "client_preference", "incident_pattern"]
DecisionLabel = Literal["approve", "reject", "edit", "unrelated", "ambiguous"]
ProposalStatus = Literal["pending", "approved", "edited", "rejected", "discarded"]
FactStatus = Literal["active", "superseded", "expired"]
AuditEventType = Literal[
    "proposed", "blocked", "approved", "edited", "rejected", "discarded",
    "written", "deduplicated", "superseded", "expired", "evicted",
]

BUSINESS_LINE_LABELS: dict[str, str] = {
    "headhunting": "Headhunting",
    "support": "Outsourcing de soporte",
    "training": "Formación corporativa",
}
CATEGORY_LABELS: dict[str, str] = {
    "escalation_procedure": "Procedimiento de escalación",
    "client_preference": "Preferencia de cliente",
    "incident_pattern": "Patrón de incidentes",
}

FACT_MIN_LENGTH = 10
FACT_MAX_LENGTH = 400


class MemoryCandidate(BaseModel):
    fact: str = Field(min_length=FACT_MIN_LENGTH, max_length=FACT_MAX_LENGTH)
    business_line: BusinessLine
    category: MemoryCategory
    reason: str = Field(default="", max_length=300)


class StructuredAnswer(BaseModel):
    respuesta: str
    propuesta_memoria: MemoryCandidate | None = None


class MemoryProposal(BaseModel):
    id: str
    conversation_id: str
    user_id: str
    fact: str
    business_line: BusinessLine
    category: MemoryCategory
    reason: str = ""
    source_message: str
    status: ProposalStatus = "pending"
    decision_label: DecisionLabel | Literal["expired"] | None = None
    decision_confidence: float | None = None
    decision_message: str | None = None
    final_fact: str | None = None
    created_at: datetime
    decided_at: datetime | None = None


class PendingDecision(BaseModel):
    label: DecisionLabel
    edited_fact: str | None = Field(default=None, max_length=FACT_MAX_LENGTH)
    remaining_question: str | None = Field(default=None, max_length=1000)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    method: Literal["llm", "fallback"] = "llm"


class MemoryFact(BaseModel):
    id: str
    business_line: BusinessLine
    category: MemoryCategory
    fact: str
    fingerprint: str
    approved_by: str
    proposal_id: str
    status: FactStatus = "active"
    status_reason: str | None = None
    supersedes_id: str | None = None
    use_count: int = 0
    created_at: datetime
    last_used_at: datetime | None = None
    status_changed_at: datetime | None = None

    def last_activity(self) -> datetime:
        return self.last_used_at or self.created_at


class AuditEvent(BaseModel):
    event: AuditEventType
    proposal_id: str | None = None
    entry_id: str | None = None
    conversation_id: str | None = None
    user_id: str | None = None
    message: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
