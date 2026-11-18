"""
Ejecución de la Parte 2 sobre un ticket (la llama el worker de Celery y
`scripts/rfp_drafts.py`).

La entrada es el handoff de la Parte 1 guardado en `rfp_ticket.handoff_payload`;
la salida, los borradores y `EvaluationResult` por departamento en
`rfp_department_section` y el handoff a la Parte 3 en `rfp_ticket.approval_handoff`.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable, Iterable

from sqlalchemy import text

from rfp_drafting.compliance_rules import normalize_known_clients
from rfp_drafting.graph import DEFAULT_MAX_ITERATIONS, build_drafting_graph
from rfp_drafting.knowledge import KnowledgeFn, rag_knowledge
from rfp_intake.llm import LlmFn, OpenAIJsonLLM
from rfp_intake.store import RfpStore, SqlRfpStore, get_engine

logger = logging.getLogger("rfp_drafting")

ClientsFn = Callable[[], Iterable[str]]


def max_iterations() -> int:
    raw = os.environ.get("RFP_MAX_ITERATIONS", str(DEFAULT_MAX_ITERATIONS))
    try:
        value = int(raw)
    except ValueError:
        raise RuntimeError(f"RFP_MAX_ITERATIONS debe ser un entero: {raw!r}") from None
    if value < 1:
        raise RuntimeError(f"RFP_MAX_ITERATIONS debe ser ≥ 1: {value}")
    return value


def incident_clients() -> list[str]:
    """Clientes actuales de Nexova: empresas con incidencias en el gestor de incidencias (Supabase).

    Alimentan la regla `clientes-anonimizados`. Si la tabla no está disponible
    se registra un aviso y la regla se queda sin nombres que vigilar.
    """
    try:
        with get_engine().connect() as conn:
            query = text("SELECT DISTINCT client_company FROM public.incident_records WHERE client_company IS NOT NULL")
            rows = conn.execute(query).scalars().all()
        return [r for r in rows if r]
    except Exception:
        logger.warning("No se pudieron leer los clientes actuales (tabla incident_records)", exc_info=True)
        return []


def run_drafting(
    ticket_id: str,
    *,
    store: RfpStore | None = None,
    llm: LlmFn | None = None,
    knowledge_fn: KnowledgeFn | None = None,
    clients_fn: ClientsFn | None = None,
    iterations: int | None = None,
) -> dict[str, Any]:
    """Genera y evalúa las secciones del ticket. Las excepciones suben al llamador (Celery)."""
    store = store or SqlRfpStore()
    graph = build_drafting_graph(
        llm=llm or OpenAIJsonLLM(),
        store=store,
        knowledge_fn=knowledge_fn or rag_knowledge,
        max_iterations=iterations or max_iterations(),
    )
    known_clients = normalize_known_clients((clients_fn or incident_clients)())
    final = graph.invoke({"ticket_id": ticket_id, "known_clients": known_clients})
    handoff = final["approval_handoff"]
    result = {
        "ticket_id": ticket_id,
        "status": final["status"],
        "sections": {s["department_id"]: s["section_status"] for s in handoff["sections"]},
        "iterations": {s["department_id"]: s["iterations"] for s in handoff["sections"]},
    }
    logger.info("rfp_drafting ticket=%s status=%s sections=%s iterations=%s", ticket_id, result["status"],
                result["sections"], result["iterations"])
    return result
