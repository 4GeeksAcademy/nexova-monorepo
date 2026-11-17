"""
Vuelta al generador de la Parte 2 desde la aprobación humana.

Cuando el responsable pide cambios (o rechaza dentro del límite de rondas), o
el árbitro los fuerza por regla, la sección vuelve a SU generador con los
comentarios y se reevalúa con los mismos tres evaluadores de la Parte 2
(legibilidad, pertinencia, cumplimiento). El borrador nuevo vuelve a la pausa
de aprobación: un humano siempre tiene la última palabra.
"""

from __future__ import annotations

from typing import Any

from rfp_drafting.compliance_rules import ComplianceContext
from rfp_drafting.evaluators import (
    build_evaluation,
    evaluate_compliance_check,
    evaluate_readability,
    evaluate_relevance,
)
from rfp_drafting.generators import GENERATORS
from rfp_drafting.knowledge import KnowledgeFn, gather_knowledge
from rfp_intake.departments import SECTION_NEEDS_HUMAN_REVIEW, SECTION_PASSED
from rfp_intake.llm import LlmFn
from rfp_intake.store import RfpStore


def revise_section(
    *, llm: LlmFn, store: RfpStore, knowledge_fn: KnowledgeFn, ticket_id: str, department_id: str,
    header: dict[str, Any], workstream: dict[str, Any], previous_draft: str | None, comments: str,
    reviewer: str, iteration: int, known_clients: tuple[str, ...],
) -> dict[str, Any]:
    """Regenera y reevalúa la sección; devuelve el `EvaluationResult` (como dict)."""
    generator = GENERATORS[department_id]
    draft = generator.generate(
        llm, header=header, workstream=workstream,
        knowledge=gather_knowledge(generator.knowledge_queries, knowledge_fn), iteration=iteration,
        revision={"previous_draft": previous_draft, "comments": comments, "reviewer": reviewer},
    )
    store.save_draft(ticket_id, department_id, draft, iteration)
    ctx = ComplianceContext(department_id=department_id, client_hq=header.get("client_hq"),
                            currency=header.get("currency"), known_clients=known_clients)
    result = build_evaluation(
        ticket_id=ticket_id, department_id=department_id, iteration=iteration,
        readability=evaluate_readability(draft),
        relevance=evaluate_relevance(draft, workstream, llm),
        compliance=evaluate_compliance_check(draft, ctx),
    )
    record = result.to_record()
    # Si no pasa la evaluación, el responsable lo verá marcado en la siguiente pausa (no se descarta).
    store.save_evaluation(ticket_id, department_id, record,
                          SECTION_PASSED if result.overall_pass else SECTION_NEEDS_HUMAN_REVIEW)
    return {"draft_content": draft, "evaluation": record}
