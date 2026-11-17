"""
Router: /agent

Invoca el grafo compilado del agente (`agent.runner.run_agent`). Solo HTTP:
autenticación, validación y códigos de estado; la recuperación y la
generación viven en `data/pipelines/rag.py` y el flujo en `agent/graph.py`.

Nunca se devuelven chunks, puntuaciones ni trazas de error: solo
`{"answer", "run_id"}`. El trace completo queda en el servidor.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from agent.nodes import EMPTY_QUESTION_ERROR, EMPTY_QUESTION_MESSAGE
from agent.runner import run_agent
from agent.schemas import AgentQueryRequest, AgentQueryResponse
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
    try:
        result = run_agent(payload.question)
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
    return AgentQueryResponse(answer=result.answer, run_id=result.run_id)
