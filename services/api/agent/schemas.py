"""Contratos HTTP de /agent. La respuesta lleva el texto y el `run_id` del trace."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AgentQueryRequest(BaseModel):
    # Sin `min_length`: la pregunta vacía la detecta el grafo (`receive_question`
    # → `reject`) para que también quede en el trace.
    question: str = Field(max_length=1000)


class AgentQueryResponse(BaseModel):
    answer: str
    run_id: str
