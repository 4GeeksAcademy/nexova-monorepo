"""
Puntos de entrada de la Parte 3 (los llaman el worker de Celery, la API y
`scripts/rfp_e2e.py`).

- `start_approval(ticket_id, department_id)`: abre (o reabre) la rama de ese
  departamento en su hilo `rfp-{ticket_id}:{department_id}` y la ejecuta hasta
  su pausa de aprobación humana (`interrupt`).
- `resume_approval(ticket_id, department_id, decision)`: punto de entrada
  explícito de reanudación. Valida la decisión contra la pausa guardada en el
  checkpoint y reanuda EXACTAMENTE ese nodo (`Command(resume=...)`); no reinicia
  el flujo. Si la rama se cayó a mitad de camino (p. ej. el LLM de una revisión
  falló), continúa desde el último checkpoint.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable, Iterable

from langgraph.types import Command

from rfp_approval.checkpointer import thread_id_for
from rfp_approval.final_document import active_departments
from rfp_approval.graph import (
    DEFAULT_MAX_APPROVAL_ROUNDS,
    NODE_HUMAN_APPROVAL,
    build_approval_graph,
    recursion_limit,
)
from rfp_approval.schemas import ApprovalDecision, validate_decision
from rfp_drafting import OUTPUT_CONTRACT_VERSION
from rfp_drafting.compliance_rules import normalize_known_clients
from rfp_drafting.knowledge import KnowledgeFn
from rfp_intake.departments import STATUS_DONE
from rfp_intake.llm import LlmFn
from rfp_intake.store import RfpStore, TicketNotFound

logger = logging.getLogger("rfp_approval")


class ApprovalNotReady(ValueError):
    """El ticket no tiene el handoff de la Parte 2 o la sección no está en la propuesta."""


class ApprovalNotPending(ValueError):
    """La rama no está esperando una decisión humana."""


def max_approval_rounds() -> int:
    raw = os.environ.get("RFP_MAX_APPROVAL_ROUNDS", str(DEFAULT_MAX_APPROVAL_ROUNDS))
    try:
        value = int(raw)
    except ValueError:
        raise RuntimeError(f"RFP_MAX_APPROVAL_ROUNDS debe ser un entero: {raw!r}") from None
    if value < 1:
        raise RuntimeError(f"RFP_MAX_APPROVAL_ROUNDS debe ser ≥ 1: {value}")
    return value


def validate_approval_handoff(ticket: dict[str, Any] | None, department_id: str | None = None) -> list[str]:
    """Comprueba el contrato de la Parte 2 y devuelve los departamentos activos."""
    if ticket is None:
        raise TicketNotFound("El ticket no existe.")
    handoff = ticket.get("approval_handoff") or {}
    if not ticket.get("ready_for_approval") or handoff.get("contract_version") != OUTPUT_CONTRACT_VERSION:
        raise ApprovalNotReady("El ticket no tiene el handoff de la Parte 2 (ready_for_approval).")
    departments = active_departments(ticket)
    if not departments:
        raise ApprovalNotReady("El handoff de la Parte 2 no trae secciones para aprobar.")
    if department_id is not None and department_id not in departments:
        raise ApprovalNotReady(f"El departamento '{department_id}' no forma parte de esta propuesta.")
    return departments


def _default_llm() -> LlmFn:
    from rfp_intake.llm import OpenAIJsonLLM

    return OpenAIJsonLLM()


def _default_knowledge(query: str) -> list[dict[str, Any]]:
    from rfp_drafting.knowledge import rag_knowledge

    return rag_knowledge(query)


def _default_clients() -> Iterable[str]:
    from rfp_drafting.drafting import incident_clients

    return incident_clients()


def _graph(store: RfpStore, checkpointer: Any, *, llm: LlmFn | None, knowledge_fn: KnowledgeFn | None,
           clients_fn: Callable[[], Iterable[str]] | None, max_rounds: int | None) -> tuple[Any, int]:
    rounds = max_rounds or max_approval_rounds()
    clients_cache: list[tuple[str, ...]] = []

    def known_clients() -> tuple[str, ...]:
        if not clients_cache:
            clients_cache.append(normalize_known_clients((clients_fn or _default_clients)()))
        return clients_cache[0]

    graph = build_approval_graph(
        store, checkpointer, llm_factory=(lambda: llm) if llm else _default_llm,
        knowledge_fn=knowledge_fn or _default_knowledge, known_clients_fn=known_clients, max_rounds=rounds,
    )
    return graph, rounds


def _config(ticket_id: str, department_id: str, rounds: int) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id_for(ticket_id, department_id)},
            "recursion_limit": recursion_limit(rounds)}


def branch_status(graph: Any, config: dict[str, Any]) -> dict[str, Any]:
    """Dónde está la rama: en pausa (con la pregunta al humano), terminada o a medio camino."""
    snapshot = graph.get_state(config)
    interrupts = [i.value for i in snapshot.interrupts] if snapshot.interrupts else []
    return {
        "thread_id": config["configurable"]["thread_id"],
        "paused": bool(interrupts),
        "next": list(snapshot.next),
        "interrupt": interrupts[0] if interrupts else None,
        "outcome": snapshot.values.get("outcome"),
        "approval_round": snapshot.values.get("approval_round", 0),
        "final_document": bool(snapshot.values.get("final_document")),
    }


def pending_interrupt(ticket_id: str, department_id: str, *, store: RfpStore, checkpointer: Any) -> dict | None:
    """La pausa guardada en el checkpoint de la rama (o `None`)."""
    graph, rounds = _graph(store, checkpointer, llm=None, knowledge_fn=None, clients_fn=None, max_rounds=None)
    return branch_status(graph, _config(ticket_id, department_id, rounds))["interrupt"]


def start_approval(
    ticket_id: str, department_id: str, *, store: RfpStore, checkpointer: Any, llm: LlmFn | None = None,
    knowledge_fn: KnowledgeFn | None = None, clients_fn: Callable[[], Iterable[str]] | None = None,
    max_rounds: int | None = None,
) -> dict[str, Any]:
    """(Re)abre la aprobación de una sección y la ejecuta hasta su pausa humana."""
    ticket = store.get_ticket(ticket_id)
    validate_approval_handoff(ticket, department_id)
    if ticket["status"] == STATUS_DONE:
        raise ApprovalNotReady("El ticket ya está terminado: su documento final está generado.")
    graph, rounds = _graph(store, checkpointer, llm=llm, knowledge_fn=knowledge_fn, clients_fn=clients_fn,
                           max_rounds=max_rounds)
    config = _config(ticket_id, department_id, rounds)
    # Reabrir = rama nueva: se descarta el checkpoint anterior de ESTE departamento (los demás no se tocan).
    checkpointer.delete_thread(config["configurable"]["thread_id"])
    store.reset_approval(ticket_id, department_id)
    graph.invoke({"ticket_id": ticket_id, "department_id": department_id,
                  "thread_id": config["configurable"]["thread_id"], "approval_round": 0}, config)
    status = branch_status(graph, config)
    logger.info("ticket=%s dept=%s aprobación abierta → %s", ticket_id, department_id,
                "pausa humana" if status["paused"] else status["outcome"])
    return status


def resume_approval(
    ticket_id: str, department_id: str, decision: dict[str, Any] | ApprovalDecision | None, *,
    store: RfpStore, checkpointer: Any, llm: LlmFn | None = None, knowledge_fn: KnowledgeFn | None = None,
    clients_fn: Callable[[], Iterable[str]] | None = None, max_rounds: int | None = None,
) -> dict[str, Any]:
    """Reanuda la rama en su pausa con la decisión humana (validada antes de entrar al grafo)."""
    graph, rounds = _graph(store, checkpointer, llm=llm, knowledge_fn=knowledge_fn, clients_fn=clients_fn,
                           max_rounds=max_rounds)
    config = _config(ticket_id, department_id, rounds)
    status = branch_status(graph, config)
    if status["paused"]:
        if decision is None:
            raise ApprovalNotPending("La rama espera una decisión humana: falta la decisión.")
        pause = status["interrupt"]
        valid = validate_decision(decision, expected_approver=pause["expected_approver"],
                                  allowed_decisions=pause["allowed_decisions"])
        graph.invoke(Command(resume=valid.model_dump(mode="json")), config)
    elif status["next"]:
        # La rama quedó a medio camino (fallo después de la decisión): sigue desde el último checkpoint.
        logger.warning("ticket=%s dept=%s continúa desde %s", ticket_id, department_id, status["next"])
        graph.invoke(None, config)
    else:
        raise ApprovalNotPending(f"La aprobación de '{department_id}' no está esperando una decisión.")
    status = branch_status(graph, config)
    logger.info("ticket=%s dept=%s reanudada → %s", ticket_id, department_id,
                "nueva pausa" if status["paused"] else status["outcome"])
    return status


def is_waiting_human(status: dict[str, Any]) -> bool:
    return status["paused"] and NODE_HUMAN_APPROVAL in status["next"]
