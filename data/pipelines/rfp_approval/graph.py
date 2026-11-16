"""
Grafo `rfp_approval` (Parte 3): aprobación humana de UNA sección.

Cada departamento activo corre este grafo en su propio hilo del checkpointer
(`rfp-{ticket_id}:{department_id}`), así que la pausa de un departamento no
bloquea a los demás: cada rama se interrumpe, se guarda y se reanuda sola.

    START → load_section → arbitrate ─┬─(sin conflicto / escalado)→ human_approval ⏸ → apply_decision ─┬→ synthesize_final_document → END
                                      │                                 (interrupt)                    ├→ revise → load_section (ronda + 1)
                                      ├─(conflicto, quedan rondas)→ revise                             └→ close → END (rechazada)
                                      └─(conflicto, límite agotado + regla `reject`)→ close

- `arbitrate` aplica la tabla fija del BRIEFING §7 (`arbitration.py`): no hay LLM.
- `human_approval` es el único punto de pausa (`interrupt`); al reanudar valida
  la decisión otra vez antes de dejarla entrar al grafo.
- `revise` vuelve al generador de la Parte 2 con los comentarios. El número de
  vueltas está limitado por `max_rounds` (`RFP_MAX_APPROVAL_ROUNDS`); agotado el
  límite ya no se puede pedir cambios: solo aprobar o rechazar.
- `synthesize_final_document` genera el documento final cuando ESTA aprobación
  completa el conjunto (la última rama en aprobar lo genera; es idempotente).
"""

from __future__ import annotations

import logging
import operator
from typing import Annotated, Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from rfp_approval.arbitration import (
    ACTION_ESCALATE,
    ACTION_REJECT,
    ACTION_REQUEST_CHANGES,
    TRIGGERS,
    detect_conflicts,
    resolve,
    structured_facts,
)
from rfp_approval.final_document import maybe_finalize
from rfp_approval.revision import revise_section
from rfp_approval.schemas import (
    DECISION_APPROVE,
    DECISION_REJECT,
    DECISION_REQUEST_CHANGES,
    DECISIONS,
    validate_decision,
)
from rfp_drafting.knowledge import KnowledgeFn
from rfp_intake.departments import (
    APPROVAL_APPROVED,
    APPROVAL_CHANGES_REQUESTED,
    APPROVAL_REJECTED,
    DEPARTMENTS,
    STATUS_AWAITING_APPROVAL,
    STATUS_DONE,
)
from rfp_intake.llm import LlmFn
from rfp_intake.models import utcnow
from rfp_intake.store import RfpStore, TicketNotFound
from rfp_intake.trace import traced

logger = logging.getLogger("rfp_approval")

DEFAULT_MAX_APPROVAL_ROUNDS = 2

NODE_HUMAN_APPROVAL = "human_approval"


class ApprovalState(TypedDict, total=False):
    ticket_id: str
    department_id: str
    thread_id: str
    approval_round: int
    review: dict[str, Any]          # lo mínimo que ve el humano para decidir (ver `review_packet`)
    conflicts: list[dict[str, Any]]
    facts: dict[str, Any]           # estado estructurado que leyó el árbitro
    route: str
    pending: dict[str, Any]         # quién decide y qué decisiones admite esta pausa
    decision: dict[str, Any] | None
    outcome: str
    final_document: bool
    # Trazabilidad dentro del estado (también en `rfp_trace_event`).
    trace: Annotated[list[dict[str, Any]], operator.add]


def review_packet(ticket: dict[str, Any], section: dict[str, Any]) -> dict[str, Any]:
    """Lo mínimo para decidir sin releer la RFP: cliente, moneda, borrador y qué falló en la evaluación."""
    evaluation = section.get("evaluation_results") or {}
    compliance = evaluation.get("compliance") or {}
    metadata = ticket.get("metadata") or {}
    return {
        "client_name": metadata.get("client_name"),
        "client_hq": metadata.get("client_hq"),
        "currency": metadata.get("currency"),
        "department": DEPARTMENTS[section["department_id"]].name,
        "owner": DEPARTMENTS[section["department_id"]].owner,
        "section_status": section.get("section_status"),
        "iteration_count": section.get("iteration_count", 0),
        "overall_pass": evaluation.get("overall_pass"),
        "violations": [{"rule_id": v.get("rule_id"), "message": v.get("message")}
                       for v in compliance.get("violations") or []],
        "missing_aspects": (evaluation.get("relevance") or {}).get("missing_aspects") or [],
        "open_questions": section.get("open_questions") or [],
        "draft_content": section.get("draft_content"),
    }


class ApprovalNodes:
    def __init__(
        self, store: RfpStore, *, llm_factory: Callable[[], LlmFn], knowledge_fn: KnowledgeFn,
        known_clients_fn: Callable[[], tuple[str, ...]], max_rounds: int,
    ) -> None:
        self.store = store
        self._llm_factory = llm_factory
        self._llm: LlmFn | None = None
        self.knowledge_fn = knowledge_fn
        self._known_clients_fn = known_clients_fn
        self.max_rounds = max(1, max_rounds)

    @property
    def llm(self) -> LlmFn:
        # Perezoso: aprobar no necesita LLM; solo revisar una sección.
        if self._llm is None:
            self._llm = self._llm_factory()
        return self._llm

    # -- helpers ---------------------------------------------------------------
    def _ticket(self, ticket_id: str) -> dict[str, Any]:
        ticket = self.store.get_ticket(ticket_id)
        if ticket is None:
            raise TicketNotFound(ticket_id)
        return ticket

    @staticmethod
    def _section(ticket: dict[str, Any], department_id: str) -> dict[str, Any]:
        for section in ticket.get("sections") or []:
            if section["department_id"] == department_id:
                return section
        raise TicketNotFound(f"{ticket['ticket_id']}/{department_id}")

    def _awaiting(self, ticket_id: str) -> None:
        ticket = self._ticket(ticket_id)
        if ticket["status"] != STATUS_DONE:
            self.store.set_status(ticket_id, STATUS_AWAITING_APPROVAL)

    def _allowed(self, approval_round: int) -> list[str]:
        if approval_round < self.max_rounds:
            return list(DECISIONS)
        return [DECISION_APPROVE, DECISION_REJECT]  # límite agotado: ya no se piden más cambios

    # -- nodos -----------------------------------------------------------------
    def load_section(self, state: ApprovalState) -> dict[str, Any]:
        ticket = self._ticket(state["ticket_id"])
        section = self._section(ticket, state["department_id"])
        self._awaiting(state["ticket_id"])
        return {"review": review_packet(ticket, section), "approval_round": state.get("approval_round", 0),
                "decision": None}

    def arbitrate(self, state: ApprovalState) -> dict[str, Any]:
        ticket = self._ticket(state["ticket_id"])
        department_id = state["department_id"]
        approval_round = state.get("approval_round", 0)
        facts = structured_facts(ticket)
        mine = [c for c in detect_conflicts(ticket) if c.department_id == department_id]
        resolution = resolve(mine, approval_round=approval_round, max_rounds=self.max_rounds)
        conflicts = [c.as_dict() for c in mine]
        owner = DEPARTMENTS[department_id].owner

        for conflict in mine:
            self.store.save_arbitration(state["ticket_id"], {
                "department_id": department_id, "trigger_id": conflict.trigger_id,
                "arbiter": conflict.arbiter, "action": resolution.action,
                "resolution": TRIGGERS[conflict.trigger_id].rule, "approval_round": approval_round,
                "evidence": {"message": conflict.message, **conflict.evidence},
            })

        if resolution.action is None:
            pending = {"expected_approver": owner, "allowed_decisions": self._allowed(approval_round),
                       "escalated": False, "reason": None}
            return {"conflicts": [], "facts": facts, "route": NODE_HUMAN_APPROVAL, "pending": pending}

        if resolution.action == ACTION_ESCALATE:
            # Límite agotado en un trigger que se escala: decide el árbitro fijo (humano), no el responsable.
            pending = {"expected_approver": resolution.arbiter, "allowed_decisions": [DECISION_APPROVE, DECISION_REJECT],
                       "escalated": True,
                       "reason": f"Conflicto sin resolver tras {approval_round} rondas: decide {resolution.arbiter}."}
            return {"conflicts": conflicts, "facts": facts, "route": NODE_HUMAN_APPROVAL, "pending": pending}

        # Resolución por regla a nombre del árbitro: pide cambios o rechaza sin intervención de un LLM.
        decision = {
            "decision_id": f"rule:{'+'.join(c.trigger_id for c in mine)}:{approval_round}",
            "decision": DECISION_REQUEST_CHANGES if resolution.action == ACTION_REQUEST_CHANGES else DECISION_REJECT,
            "acting_as": resolution.arbiter, "comment": resolution.feedback, "decided_by_user": None,
            "by_rule": [c.trigger_id for c in mine],
        }
        route = "revise" if resolution.action == ACTION_REQUEST_CHANGES else "close"
        self._record(state, decision, final=(route == "close"))
        return {"conflicts": conflicts, "facts": facts, "route": route, "decision": decision}

    def human_approval(self, state: ApprovalState) -> dict[str, Any]:
        pending = state["pending"]
        approval_round = state.get("approval_round", 0)
        self.store.request_approval(
            state["ticket_id"], state["department_id"], expected_approver=pending["expected_approver"],
            allowed_decisions=pending["allowed_decisions"], approval_round=approval_round,
        )
        self._awaiting(state["ticket_id"])
        # ⏸ Pausa SOLO de esta rama. El checkpoint queda guardado en el hilo de este departamento.
        raw = interrupt({
            "ticket_id": state["ticket_id"],
            "department_id": state["department_id"],
            "thread_id": state.get("thread_id"),
            "expected_approver": pending["expected_approver"],
            "allowed_decisions": pending["allowed_decisions"],
            "escalated": pending.get("escalated", False),
            "reason": pending.get("reason"),
            "approval_round": approval_round,
            "max_rounds": self.max_rounds,
            "conflicts": state.get("conflicts") or [],
            "review": state.get("review") or {},
        })
        # Reanudación: guardrail antes de que la respuesta humana siga por el grafo.
        decision = validate_decision(raw, expected_approver=pending["expected_approver"],
                                     allowed_decisions=pending["allowed_decisions"])
        return {"decision": decision.model_dump(mode="json")}

    def apply_decision(self, state: ApprovalState) -> dict[str, Any]:
        decision = state["decision"]
        approval_round = state.get("approval_round", 0)
        kind = decision["decision"]
        if kind == DECISION_APPROVE:
            self._record(state, decision, final=True)
            return {"route": "synthesize_final_document", "outcome": APPROVAL_APPROVED}
        if kind == DECISION_REJECT and approval_round >= self.max_rounds:
            self._record(state, decision, final=True)
            return {"route": "close"}
        # request_changes, o reject con rondas disponibles: vuelve al generador.
        self._record(state, decision, final=False)
        return {"route": "revise"}

    def revise(self, state: ApprovalState) -> dict[str, Any]:
        ticket = self._ticket(state["ticket_id"])
        department_id = state["department_id"]
        section = self._section(ticket, department_id)
        decision = state["decision"] or {}
        payload = ticket.get("handoff_payload") or {}
        header = {k: payload.get(k) for k in (
            "ticket_id", "rfp_id", "client_name", "client_hq", "currency", "deadline", "services_requested",
        )}
        workstream = next((w for w in payload.get("workstreams") or [] if w.get("department_id") == department_id),
                          {"department_id": department_id})
        restart = decision.get("decision") == DECISION_REJECT
        result = revise_section(
            llm=self.llm, store=self.store, knowledge_fn=self.knowledge_fn, ticket_id=state["ticket_id"],
            department_id=department_id, header=header, workstream=workstream,
            previous_draft=None if restart else section.get("draft_content"),
            comments=decision.get("comment") or "", reviewer=decision.get("acting_as") or "",
            iteration=(section.get("iteration_count") or 0) + 1, known_clients=self._known_clients_fn(),
        )
        logger.info("ticket=%s dept=%s ronda=%d revisada (overall_pass=%s)", state["ticket_id"], department_id,
                    state.get("approval_round", 0) + 1, result["evaluation"].get("overall_pass"))
        return {"approval_round": state.get("approval_round", 0) + 1, "decision": None,
                "conflicts": state.get("conflicts") or []}

    def close(self, state: ApprovalState) -> dict[str, Any]:
        # Sección rechazada: el ticket sigue `esperando_aprobación` hasta que Ventas reabra esa aprobación.
        self._awaiting(state["ticket_id"])
        return {"outcome": APPROVAL_REJECTED}

    def synthesize_final_document(self, state: ApprovalState) -> dict[str, Any]:
        document = maybe_finalize(self.store, state["ticket_id"])
        if document is None:
            self._awaiting(state["ticket_id"])  # otras ramas siguen pendientes
        return {"outcome": APPROVAL_APPROVED, "final_document": document is not None}

    def _record(self, state: ApprovalState, decision: dict[str, Any], *, final: bool) -> None:
        kind = decision["decision"]
        status = {DECISION_APPROVE: APPROVAL_APPROVED, DECISION_REQUEST_CHANGES: APPROVAL_CHANGES_REQUESTED,
                  DECISION_REJECT: APPROVAL_REJECTED if final else APPROVAL_CHANGES_REQUESTED}[kind]
        now = utcnow()
        approval_round = state.get("approval_round", 0)
        entry = {
            "id": decision.get("decision_id"), "round": approval_round, "decision": kind,
            "acting_as": decision.get("acting_as"), "decided_by_user": decision.get("decided_by_user"),
            "comment": decision.get("comment") or None, "by_rule": decision.get("by_rule"),
            "decided_at": now.isoformat(),
        }
        self.store.record_decision(
            state["ticket_id"], state["department_id"], approval_status=status, entry=entry,
            approval_round=approval_round,
            approver=decision.get("acting_as") if kind == DECISION_APPROVE else None,
            approved_at=now if kind == DECISION_APPROVE else None,
        )


_AGENTS = {
    "load_section": "carga_seccion",
    "arbitrate": "arbitro_reglas",
    NODE_HUMAN_APPROVAL: "aprobacion_humana",
    "apply_decision": "aplica_decision",
    "revise": "generador_revision",
    "close": "cierre_rechazo",
    "synthesize_final_document": "sintetizador_documento_final",
}


def build_approval_graph(
    store: RfpStore, checkpointer: Any, *, llm_factory: Callable[[], LlmFn], knowledge_fn: KnowledgeFn,
    known_clients_fn: Callable[[], tuple[str, ...]], max_rounds: int = DEFAULT_MAX_APPROVAL_ROUNDS,
) -> Any:
    nodes = ApprovalNodes(store, llm_factory=llm_factory, knowledge_fn=knowledge_fn,
                          known_clients_fn=known_clients_fn, max_rounds=max_rounds)
    builder = StateGraph(ApprovalState)
    for name, agent in _AGENTS.items():
        builder.add_node(name, traced(
            getattr(nodes, name), store=store, part="approval", node=name,
            agent=(lambda state, agent=agent: f"{agent}:{state.get('department_id')}"),
            thread_id=lambda state: state.get("thread_id"), in_state=True,
        ))
    builder.add_edge(START, "load_section")
    builder.add_edge("load_section", "arbitrate")
    builder.add_conditional_edges("arbitrate", lambda s: s["route"], [NODE_HUMAN_APPROVAL, "revise", "close"])
    builder.add_edge(NODE_HUMAN_APPROVAL, "apply_decision")
    builder.add_conditional_edges("apply_decision", lambda s: s["route"],
                                  ["synthesize_final_document", "revise", "close"])
    builder.add_edge("revise", "load_section")
    builder.add_edge("close", END)
    builder.add_edge("synthesize_final_document", END)
    return builder.compile(checkpointer=checkpointer)


def recursion_limit(max_rounds: int) -> int:
    # Cada ronda: load_section, arbitrate, human_approval, apply_decision, revise (+ cierre).
    return 10 + 6 * (max_rounds + 1)
