"""Contratos HTTP de /agent.

La respuesta lleva el texto, el `run_id` del trace y el `conversation_id` que
el cliente debe reenviar en el siguiente mensaje para que el agente evalúe la
propuesta de memoria pendiente (si la hay).
"""

from __future__ import annotations

from typing import Literal

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


class AgentQueryResponse(BaseModel):
    answer: str
    run_id: str
    conversation_id: str | None = None
    memory_proposal: MemoryProposalOut | None = None
    memory_decision: MemoryDecisionOut | None = None
