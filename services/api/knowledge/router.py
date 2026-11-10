"""
Router: /knowledge

Consulta en lenguaje natural sobre la base de conocimiento de Nexova (líneas de
servicio, tarifas, SLA, objeciones). Solo HTTP: autenticación, validación y
códigos de estado. Recuperación y generación viven en `data/pipelines/rag.py`
y se importan vía `pipeline_bridge`.

Nunca se devuelven chunks, payloads ni puntuaciones de Qdrant: solo
`{"answer": ...}`. Los detalles se registran en el servidor.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from knowledge import pipeline_bridge
from knowledge.schemas import KnowledgeQueryRequest, KnowledgeQueryResponse
from models import User
from security import get_current_user

router = APIRouter(prefix="/knowledge", tags=["knowledge"])
logger = logging.getLogger(__name__)


@router.post("/query", response_model=KnowledgeQueryResponse)
def query_knowledge(
    payload: KnowledgeQueryRequest,
    current_user: User = Depends(get_current_user),
) -> KnowledgeQueryResponse:
    # `def` síncrono: FastAPI lo ejecuta en su threadpool (Qdrant y el LLM bloquean).
    try:
        answer = pipeline_bridge.query(payload.question)
    except Exception:  # Qdrant/LLM caídos o sin configurar: el cliente no ve detalles.
        logger.exception("Fallo en la consulta a la base de conocimiento")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Knowledge base unavailable.",
        ) from None
    return KnowledgeQueryResponse(answer=answer)
