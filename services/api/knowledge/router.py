"""
Router: /knowledge

Consulta en lenguaje natural sobre la base de conocimiento de Nexova (líneas de
servicio, tarifas, SLA, objeciones). Solo HTTP: autenticación, validación y
códigos de estado. Recuperación y generación viven en `data/pipelines/rag.py`
y se importan vía `pipeline_bridge`.

Nunca se devuelven chunks, payloads ni puntuaciones de Qdrant: solo
`{"answer": ...}`. Los detalles se registran en el servidor.

Antes del LLM (informe NIST · Protect): presupuesto por usuario
(`rate_limit.enforce_llm_budget`, compartido con `/agent/query`) y el mismo guard
de entrada determinista que el agente de soporte (`agent.guardrails.input_guard`).
Una pregunta bloqueada responde 422 sin llegar a Qdrant ni al modelo y queda en el
log de guardrails. El prompt separa roles y aísla el contexto (`rag.build_user_message`).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from knowledge import pipeline_bridge
from knowledge.schemas import KnowledgeQueryRequest, KnowledgeQueryResponse
from models import User
from authz import require_staff  # OWASP A01: solo personal interno (admin/manager)
from agent.guardrails import events
from agent.guardrails.input_guard import check_input
from agent.guardrails.tenancy import tenant_for_user
from rate_limit import enforce_llm_budget

BLOCKED_QUESTION_MESSAGE = "La consulta no se puede procesar. Reformúlala como una pregunta sobre los servicios de Nexova."

router = APIRouter(prefix="/knowledge", tags=["knowledge"])
logger = logging.getLogger(__name__)


@router.post("/query", response_model=KnowledgeQueryResponse)
def query_knowledge(
    payload: KnowledgeQueryRequest,
    current_user: User = Depends(require_staff),
) -> KnowledgeQueryResponse:
    # `def` síncrono: FastAPI lo ejecuta en su threadpool (Qdrant y el LLM bloquean).
    enforce_llm_budget("knowledge", current_user.id)
    verdict = check_input(payload.question, tenant_for_user(current_user))
    if verdict.blocked:
        events.emit("input", verdict.rule or verdict.category, "block", verdict.failure_type or "security",
                    category=verdict.category, excerpt=payload.question)
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=BLOCKED_QUESTION_MESSAGE)
    try:
        answer = pipeline_bridge.query(payload.question)
    except Exception:  # Qdrant/LLM caídos o sin configurar: el cliente no ve detalles.
        logger.exception("Fallo en la consulta a la base de conocimiento")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Knowledge base unavailable.",
        ) from None
    return KnowledgeQueryResponse(answer=answer)
