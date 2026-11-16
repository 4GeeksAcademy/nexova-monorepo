"""
Dobles de prueba de la Parte 3 (`rfp_approval`): ticket que ya pasó por la
Parte 2 (borradores evaluados de verdad con `run_drafting` y LLM falso),
checkpointer en memoria y decisiones humanas simuladas.
"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from drafting_fakes import (
    CAPACITACION_WS,
    GOOD_CAPACITACION,
    GOOD_SELECCION,
    GOOD_SOPORTE,
    PASS_RELEVANCE,
    SELECCION_WS,
    SOPORTE_WS,
    fake_llm,
    make_ready_ticket,
    no_knowledge,
    section,
)
from rfp_approval import resume_approval, start_approval
from rfp_drafting import run_drafting
from rfp_intake import InMemoryRfpStore

class StatusSpy(InMemoryRfpStore):
    """Store que registra cada cambio de estado del ticket (lo que vería la UI en vivo)."""

    def __init__(self) -> None:
        super().__init__()
        self.status_log: list[str] = []

    def _touch(self, ticket_id: str, **changes: Any) -> None:
        super()._touch(ticket_id, **changes)
        status = self.tickets[ticket_id]["status"]
        if not self.status_log or self.status_log[-1] != status:
            self.status_log.append(status)


WORKSTREAMS = {"seleccion": SELECCION_WS, "capacitacion": CAPACITACION_WS, "soporte": SOPORTE_WS}
GOOD_DRAFTS = {"seleccion": GOOD_SELECCION, "capacitacion": GOOD_CAPACITACION, "soporte": GOOD_SOPORTE}


def drafting_llm(drafts: dict[str, Any]) -> Any:
    """LLM falso para generar (y regenerar) cada sección: `drafts[dept]` es un borrador o un `Sequence`."""
    responses: dict[str, Any] = {}
    for department_id, draft in drafts.items():
        responses[f"generate_{department_id}"] = section(draft) if isinstance(draft, str) else draft
        responses[f"relevance_{department_id}"] = PASS_RELEVANCE
    return fake_llm(**responses)


def ticket_after_part2(store: Any, departments: list[str], *, drafts: dict[str, Any] | None = None,
                       hq: str = "España", currency: str = "EUR", ticket_id: str = "RFP-P3TEST") -> Any:
    """Deja el ticket como lo entrega la Parte 2 (borradores + EvaluationResult + approval_handoff)."""
    make_ready_ticket(store, [WORKSTREAMS[d] for d in departments], client_hq=hq, currency=currency,
                      ticket_id=ticket_id)
    drafts = {d: (drafts or {}).get(d, GOOD_DRAFTS[d]) for d in departments}
    llm = drafting_llm(drafts)
    run_drafting(ticket_id, store=store, llm=llm, knowledge_fn=no_knowledge, clients_fn=lambda: [], iterations=1)
    return llm


class Approvals:
    """Atajo: abre y reanuda ramas sobre un store + checkpointer en memoria compartidos."""

    def __init__(self, store: Any, llm: Any, *, max_rounds: int = 2, checkpointer: Any | None = None,
                 ticket_id: str = "RFP-P3TEST") -> None:
        self.store = store
        self.checkpointer = checkpointer or InMemorySaver()
        self.ticket_id = ticket_id
        self.kwargs = dict(store=store, checkpointer=self.checkpointer, llm=llm, knowledge_fn=no_knowledge,
                           clients_fn=lambda: [], max_rounds=max_rounds)

    def start(self, department_id: str, ticket_id: str | None = None) -> dict[str, Any]:
        return start_approval(ticket_id or self.ticket_id, department_id, **self.kwargs)

    def decide(self, department_id: str, decision: str, acting_as: str, comment: str = "",
               ticket_id: str | None = None, **extra: Any) -> dict[str, Any]:
        payload = {"decision": decision, "acting_as": acting_as, "comment": comment,
                   "decided_by_user": "ventas@nexova.test", **extra}
        return resume_approval(ticket_id or self.ticket_id, department_id, payload, **self.kwargs)

    def section(self, department_id: str, ticket_id: str | None = None) -> dict[str, Any]:
        ticket = self.store.get_ticket(ticket_id or self.ticket_id)
        return next(s for s in ticket["sections"] if s["department_id"] == department_id)

    def ticket(self, ticket_id: str | None = None) -> dict[str, Any]:
        return self.store.get_ticket(ticket_id or self.ticket_id)
