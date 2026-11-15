"""
Router: /agent

Invoca el grafo compilado del agente (`agent.runner.run_agent`). Solo HTTP:
autenticación, validación y códigos de estado; la recuperación y la
generación viven en `data/pipelines/rag.py` y el flujo en `agent/graph.py`.

Nunca se devuelven chunks, puntuaciones ni trazas de error: solo
`{"answer", "run_id"}`. El trace completo queda en el servidor.

- `POST /agent/query`: una consulta. El tenant (empresa cliente y si es
  personal interno) sale SIEMPRE del usuario autenticado, nunca del cuerpo.
- `GET /agent/guardrails/summary`: cuántas veces se activó cada guardrail
  (solo personal interno: admin / manager).
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status

from agent.guardrails.events import read_events, summarize
from agent.guardrails.tenancy import tenant_for_user
from agent.nodes import EMPTY_QUESTION_ERROR, EMPTY_QUESTION_MESSAGE
from agent.runner import run_agent
from agent.schemas import (
    AgentQueryRequest,
    AgentQueryResponse,
    GuardrailOut,
    GuardrailSummary,
    MemoryDecisionOut,
    MemoryProposalOut,
    _UUID_PATTERN,
)
from models import User
from security import get_current_user

router = APIRouter(prefix="/agent", tags=["agent"])
logger = logging.getLogger(__name__)


@router.post("/query", response_model=AgentQueryResponse)
def query_agent(
    payload: AgentQueryRequest,
    current_user: User = Depends(get_current_user),
) -> AgentQueryResponse:
    # `def` síncrono: FastAPI lo ejecuta en su threadpool (Qdrant, el LLM y las tools bloquean).
    # Las tools hablan con el MCP Server con el token OAuth del agente (solo lectura);
    # el JWT de quien llama ya no sale de la API.
    # `user_id` identifica quién aprueba cada hecho (auditoría) y quién puede
    # decidir la propuesta pendiente de la conversación; `tenant` limita los
    # tickets a su empresa cliente.
    try:
        result = run_agent(
            payload.question,
            conversation_id=payload.conversation_id,
            user_id=current_user.id,
            tenant=tenant_for_user(current_user),
        )
    except Exception:  # Cualquier nodo caído (Qdrant, LLM, checkpoint): sin detalles al cliente.
        logger.exception("Fallo en una corrida del agente")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Agent unavailable.",
        ) from None

    if result.error == EMPTY_QUESTION_ERROR:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=EMPTY_QUESTION_MESSAGE,
        )
    if result.error or not result.answer:
        logger.error("Corrida %s sin respuesta (error=%s)", result.run_id, result.error)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Agent unavailable.",
        )
    decision = result.memory_decision
    guard = result.guardrail
    return AgentQueryResponse(
        answer=result.answer,
        run_id=result.run_id,
        conversation_id=result.conversation_id,
        memory_proposal=MemoryProposalOut(**result.memory_proposal) if result.memory_proposal else None,
        memory_decision=(
            MemoryDecisionOut(proposal_id=decision["proposal_id"], outcome=decision["outcome"])
            if decision else None
        ),
        guardrail=GuardrailOut(action=guard["action"], category=guard["category"]) if guard else None,
    )


@router.get("/guardrails/summary", response_model=GuardrailSummary)
def guardrails_summary(
    since: datetime | None = Query(default=None, description="Desde (ISO 8601), p. ej. inicio de la sesión de pruebas"),
    until: datetime | None = Query(default=None, description="Hasta (ISO 8601)"),
    conversation_id: str | None = Query(default=None, pattern=_UUID_PATTERN),
    current_user: User = Depends(get_current_user),
) -> GuardrailSummary:
    """Conteo de activaciones de cada guardrail (por guard, regla, acción y tipo de fallo)."""
    if not tenant_for_user(current_user).is_staff:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Solo personal interno de Nexova.")
    return GuardrailSummary(**summarize(read_events(), since=since, until=until,
                                        conversation_id=conversation_id))
