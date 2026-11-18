"""Contratos HTTP de /agent.

La respuesta lleva el texto, el `run_id` del trace y el `conversation_id` que
el cliente debe reenviar en el siguiente mensaje para que el agente evalúe la
propuesta de memoria pendiente (si la hay).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

_UUID_PATTERN = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"


class AgentQueryRequest(BaseModel):
    # Sin `min_length`: la pregunta vacía la detecta el grafo (`receive_question`
    # → `reject`) para que también quede en el trace.
    question: str = Field(max_length=1000)
    # Opcional: si falta, se abre una conversación nueva y se devuelve su id.
    conversation_id: str | None = Field(default=None, pattern=_UUID_PATTERN)


class MemoryProposalOut(BaseModel):
    """Propuesta pendiente: se guardará solo si el usuario la aprueba en su siguiente mensaje."""

    id: str
    fact: str
    business_line: Literal["headhunting", "support", "training"]
    category: Literal["escalation_procedure", "client_preference", "incident_pattern"]


class MemoryDecisionOut(BaseModel):
    """Qué se hizo con la propuesta pendiente al recibir este mensaje."""

    proposal_id: str
    outcome: Literal["approved", "edited", "rejected", "discarded"]


class GuardrailOut(BaseModel):
    """El guard de entrada rechazó o redirigió el mensaje (sin detalles de qué regla saltó)."""

    action: Literal["block", "redirect"]
    category: Literal["injection", "cross_tenant", "personal_task", "casual"]


class AgentQueryResponse(BaseModel):
    answer: str
    run_id: str
    conversation_id: str | None = None
    memory_proposal: MemoryProposalOut | None = None
    memory_decision: MemoryDecisionOut | None = None
    guardrail: GuardrailOut | None = None


class GuardrailSummary(BaseModel):
    """Cuántas veces se activó cada guardrail (`GET /agent/guardrails/summary`)."""

    total: int
    runs_affected: int
    by_failure_type: dict[str, int]
    by_guard: dict[str, int]
    by_action: dict[str, int]
    by_rule: dict[str, int]
    since: str | None = None
    until: str | None = None
    conversation_id: str | None = None


class AgentDecision(BaseModel):
    """Una línea del registro de decisiones (`agent/audit.py`); sin pregunta ni respuesta."""

    ts: str
    run_id: str
    conversation_id: str | None = None
    user_id: str | None = None
    tenant: dict[str, Any] = Field(default_factory=dict)
    status: str
    outcome: str
    reason: str
    guard_input: dict[str, Any] | None = None
    route: dict[str, Any] | None = None
    sources_used: list[str] = Field(default_factory=list)
    tools: list[dict[str, Any]] = Field(default_factory=list)
    memory: dict[str, Any] = Field(default_factory=dict)
    output_blocked: bool = False
    output_rules: list[str] = Field(default_factory=list)
    nodes: list[str] = Field(default_factory=list)
    latency_ms: float | None = None
    question_sha256: str | None = None
    question_chars: int = 0
    answer_chars: int = 0
