"""
Grafo `rfp_drafting` (Parte 2): generación y evaluación por departamento.

Grafo exterior:

    START → load_handoff ─Send×N→ department_cycle → finalize → END

Subgrafo de cada departamento (corre en paralelo con los demás):

    START → retrieve_knowledge → generate ─┬→ eval_readability ─┐
                                           ├→ eval_relevance  ──┼→ judge ─┬→ generate (feedback)
                                           └→ eval_compliance ──┘         └→ END (pasa | límite)

Cada evaluador escribe en su propia clave del estado (`readability`,
`relevance`, `compliance`), así que correr en paralelo no produce conflictos;
`judge` es el único que los combina.
"""

from __future__ import annotations

import logging
import operator
from datetime import datetime, timezone
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from rfp_drafting.compliance_rules import ComplianceContext
from rfp_drafting.evaluators import (
    build_evaluation,
    evaluate_compliance_check,
    evaluate_readability,
    evaluate_relevance,
)
from rfp_drafting.generators import GENERATORS
from rfp_drafting.knowledge import KnowledgeFn, gather_knowledge
from rfp_intake.departments import (
    DEPARTMENT_IDS,
    SECTION_GENERATING,
    SECTION_NEEDS_HUMAN_REVIEW,
    SECTION_PASSED,
    STATUS_ANALYSIS_COMPLETE,
    STATUS_EVALUATING,
    STATUS_GENERATING,
    STATUS_NEEDS_HUMAN_REVIEW,
)
from rfp_intake.llm import LlmFn
from rfp_intake.store import RfpStore
from rfp_intake.trace import traced

logger = logging.getLogger("rfp_drafting")

INPUT_CONTRACT_VERSION = "rfp_intake.v1"
OUTPUT_CONTRACT_VERSION = "rfp_drafting.v1"
DEFAULT_MAX_ITERATIONS = 3


class HandoffNotReady(ValueError):
    """El ticket no tiene un handoff válido de la Parte 1."""


class CycleState(TypedDict, total=False):
    ticket_id: str
    department_id: str
    header: dict[str, Any]
    workstream: dict[str, Any]
    known_clients: tuple[str, ...]
    knowledge: list[dict[str, Any]]
    iteration: int
    draft: str
    feedback: str
    readability: Any
    relevance: Any
    compliance: Any
    evaluation: dict[str, Any]
    section_status: str


class DraftingState(TypedDict, total=False):
    ticket_id: str
    header: dict[str, Any]
    workstreams: list[dict[str, Any]]
    known_clients: tuple[str, ...]
    sections: Annotated[list[dict[str, Any]], operator.add]
    status: str
    approval_handoff: dict[str, Any]


def validate_handoff(ticket: dict[str, Any] | None) -> dict[str, Any]:
    """Comprueba el contrato de la Parte 1 y devuelve el payload."""
    if ticket is None:
        raise HandoffNotReady("El ticket no existe.")
    payload = ticket.get("handoff_payload") or {}
    if not ticket.get("ready_for_drafting") or not payload:
        raise HandoffNotReady("El ticket no tiene el handoff de la Parte 1 (ready_for_drafting).")
    if payload.get("contract_version") != INPUT_CONTRACT_VERSION:
        raise HandoffNotReady(f"Contrato de handoff no soportado: {payload.get('contract_version')!r}.")
    if not payload.get("workstreams"):
        raise HandoffNotReady("El handoff no trae workstreams por departamento.")
    return payload


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class DraftingNodes:
    def __init__(self, llm: LlmFn, store: RfpStore, knowledge_fn: KnowledgeFn, max_iterations: int) -> None:
        self.llm = llm
        self.store = store
        self.knowledge_fn = knowledge_fn
        self.max_iterations = max(1, max_iterations)

    # -- Subgrafo por departamento ------------------------------------------
    def retrieve_knowledge(self, state: CycleState) -> dict[str, Any]:
        generator = GENERATORS[state["department_id"]]
        return {"knowledge": gather_knowledge(generator.knowledge_queries, self.knowledge_fn), "iteration": 0}

    def generate(self, state: CycleState) -> dict[str, Any]:
        generator = GENERATORS[state["department_id"]]
        iteration = state.get("iteration", 0) + 1
        draft = generator.generate(
            self.llm,
            header=state["header"],
            workstream=state["workstream"],
            knowledge=state.get("knowledge") or [],
            previous_draft=state.get("draft") if iteration > 1 else None,
            feedback=state.get("feedback") if iteration > 1 else None,
            iteration=iteration,
        )
        self.store.save_draft(state["ticket_id"], state["department_id"], draft, iteration)
        # Primer borrador entrando a evaluación: el ticket pasa a `en_evaluación` (idempotente).
        self.store.set_status(state["ticket_id"], STATUS_EVALUATING)
        logger.info("ticket=%s dept=%s iteración=%d borrador generado", state["ticket_id"],
                    state["department_id"], iteration)
        return {"draft": draft, "iteration": iteration}

    def eval_readability(self, state: CycleState) -> dict[str, Any]:
        return {"readability": evaluate_readability(state["draft"])}

    def eval_relevance(self, state: CycleState) -> dict[str, Any]:
        return {"relevance": evaluate_relevance(state["draft"], state["workstream"], self.llm)}

    def eval_compliance(self, state: CycleState) -> dict[str, Any]:
        header = state["header"]
        ctx = ComplianceContext(
            department_id=state["department_id"],
            client_hq=header.get("client_hq"),
            currency=header.get("currency"),
            known_clients=state.get("known_clients") or (),
        )
        return {"compliance": evaluate_compliance_check(state["draft"], ctx)}

    def judge(self, state: CycleState) -> dict[str, Any]:
        iteration = state["iteration"]
        result = build_evaluation(
            ticket_id=state["ticket_id"], department_id=state["department_id"], iteration=iteration,
            readability=state["readability"], relevance=state["relevance"], compliance=state["compliance"],
        )
        if result.overall_pass:
            status = SECTION_PASSED
        elif iteration >= self.max_iterations:
            status = SECTION_NEEDS_HUMAN_REVIEW
        else:
            status = SECTION_GENERATING  # vuelve a su generador con el feedback
        record = result.to_record()
        self.store.save_evaluation(state["ticket_id"], state["department_id"], record, status)
        logger.info("ticket=%s dept=%s iteración=%d overall_pass=%s → %s", state["ticket_id"],
                    state["department_id"], iteration, result.overall_pass, status)
        return {"evaluation": record, "feedback": result.feedback_for_generator, "section_status": status}

    # -- Grafo exterior -------------------------------------------------------
    def load_handoff(self, state: DraftingState) -> dict[str, Any]:
        ticket = self.store.get_ticket(state["ticket_id"])
        payload = validate_handoff(ticket)
        if ticket["status"] not in (STATUS_ANALYSIS_COMPLETE, STATUS_EVALUATING, STATUS_NEEDS_HUMAN_REVIEW,
                                    STATUS_GENERATING):
            raise HandoffNotReady(f"El ticket está en '{ticket['status']}', no listo para generar borradores.")
        order = {d: i for i, d in enumerate(DEPARTMENT_IDS)}
        workstreams = sorted(
            (w for w in payload["workstreams"] if w.get("department_id") in GENERATORS),
            key=lambda w: order[w["department_id"]],
        )
        header = {k: payload.get(k) for k in (
            "ticket_id", "rfp_id", "client_name", "client_hq", "currency", "deadline", "services_requested",
        )}
        self.store.start_drafting(state["ticket_id"], [w["department_id"] for w in workstreams])
        return {"header": header, "workstreams": workstreams}

    def finalize(self, state: DraftingState) -> dict[str, Any]:
        order = {d: i for i, d in enumerate(DEPARTMENT_IDS)}
        sections = sorted(state.get("sections") or [], key=lambda s: order[s["department_id"]])
        needs_review = any(s["needs_human_review"] for s in sections)
        status = STATUS_NEEDS_HUMAN_REVIEW if needs_review else STATUS_EVALUATING
        header = state["header"]
        iterations = [s["iterations"] for s in sections]
        handoff = {
            "contract_version": OUTPUT_CONTRACT_VERSION,
            "ticket_id": state["ticket_id"],
            "rfp_id": header.get("rfp_id"),
            "client_name": header.get("client_name"),
            "client_hq": header.get("client_hq"),
            "currency": header.get("currency"),
            "deadline": header.get("deadline"),
            "max_iterations": self.max_iterations,
            "needs_human_review": needs_review,
            "sections": sections,
            "kpis": {
                "sections": len(sections),
                "passed": sum(1 for s in sections if s["section_status"] == SECTION_PASSED),
                "needs_human_review": sum(1 for s in sections if s["needs_human_review"]),
                "avg_iterations": round(sum(iterations) / len(iterations), 2) if iterations else 0,
            },
            "generated_at": _now_iso(),
        }
        self.store.finish_drafting(state["ticket_id"], status, handoff)
        return {"status": status, "approval_handoff": handoff}


_CYCLE_AGENTS = {
    "retrieve_knowledge": "rag_conocimiento",
    "generate": "generador",
    "eval_readability": "evaluador_legibilidad",
    "eval_relevance": "evaluador_pertinencia",
    "eval_compliance": "evaluador_cumplimiento",
    "judge": "juez",
}


def _cycle_agent(name: str) -> Any:
    return lambda state: f"{_CYCLE_AGENTS[name]}:{state.get('department_id')}"


def build_cycle_graph(nodes: DraftingNodes) -> Any:
    builder = StateGraph(CycleState)
    for name in _CYCLE_AGENTS:
        builder.add_node(name, traced(getattr(nodes, name), store=nodes.store, part="drafting", node=name,
                                      agent=_cycle_agent(name)))
    builder.add_edge(START, "retrieve_knowledge")
    builder.add_edge("retrieve_knowledge", "generate")
    for evaluator in ("eval_readability", "eval_relevance", "eval_compliance"):
        builder.add_edge("generate", evaluator)  # los tres en paralelo
    builder.add_edge(["eval_readability", "eval_relevance", "eval_compliance"], "judge")  # espera a los tres
    builder.add_conditional_edges(
        "judge",
        lambda s: "generate" if s["section_status"] == SECTION_GENERATING else END,
        ["generate", END],
    )
    return builder.compile()


def build_drafting_graph(
    llm: LlmFn, store: RfpStore, knowledge_fn: KnowledgeFn, max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> Any:
    nodes = DraftingNodes(llm, store, knowledge_fn, max_iterations)
    cycle = build_cycle_graph(nodes)
    # Cada vuelta del ciclo son 3 supersteps (generar, evaluar, juzgar) + recuperación.
    cycle_config = {"recursion_limit": 6 + 4 * nodes.max_iterations}

    def department_cycle(payload: dict[str, Any]) -> dict[str, Any]:
        workstream = payload["workstream"]
        final = cycle.invoke(
            {
                "ticket_id": payload["ticket_id"],
                "department_id": workstream["department_id"],
                "header": payload["header"],
                "workstream": workstream,
                "known_clients": payload.get("known_clients") or (),
            },
            config=cycle_config,
        )
        needs_review = final["section_status"] == SECTION_NEEDS_HUMAN_REVIEW
        return {
            "sections": [
                {
                    "department_id": workstream["department_id"],
                    "owner": workstream.get("owner") or GENERATORS[workstream["department_id"]].owner,
                    "section_status": final["section_status"],
                    "needs_human_review": needs_review,
                    "iterations": final["iteration"],
                    "draft_content": final["draft"],
                    "evaluation_result": final["evaluation"],
                }
            ]
        }

    def fan_out(state: DraftingState) -> list[Send]:
        return [
            Send("department_cycle", {
                "ticket_id": state["ticket_id"],
                "header": state["header"],
                "workstream": workstream,
                "known_clients": state.get("known_clients") or (),
            })
            for workstream in state["workstreams"]
        ]

    builder = StateGraph(DraftingState)
    builder.add_node("load_handoff", traced(nodes.load_handoff, store=store, part="drafting",
                                            node="load_handoff", agent="carga_handoff_parte1"))
    builder.add_node("department_cycle", department_cycle)  # sus nodos internos ya dejan su trace
    builder.add_node("finalize", traced(nodes.finalize, store=store, part="drafting", node="finalize",
                                        agent="handoff_parte2"))
    builder.add_edge(START, "load_handoff")
    builder.add_conditional_edges("load_handoff", fan_out, ["department_cycle"])
    builder.add_edge("department_cycle", "finalize")
    builder.add_edge("finalize", END)
    return builder.compile()
