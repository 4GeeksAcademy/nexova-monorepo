"""
Grafo `rfp_drafting` (Parte 2): ciclo generador-evaluador, límite de
iteraciones, `needs_human_review` y estados del ticket.

    uv run --project services/api pytest tests/pipelines/test_rfp_drafting_graph.py -v
"""

from __future__ import annotations

import pytest

from drafting_fakes import (
    BAD_TTC_SELECCION,
    CAPACITACION_WS,
    GOOD_CAPACITACION,
    GOOD_SELECCION,
    GOOD_SOPORTE,
    PASS_RELEVANCE,
    SELECCION_WS,
    SOPORTE_WS,
    Sequence,
    fake_llm,
    no_knowledge,
    section,
)
from rfp_drafting import HandoffNotReady, run_drafting
from rfp_intake import InMemoryRfpStore


class StatusSpy(InMemoryRfpStore):
    """Store que registra cada cambio de estado del ticket (lo que ve la UI en vivo)."""

    def __init__(self):
        super().__init__()
        self.status_log: list[str] = []

    def _touch(self, ticket_id, **changes):
        super()._touch(ticket_id, **changes)
        status = self.tickets[ticket_id]["status"]
        if not self.status_log or self.status_log[-1] != status:
            self.status_log.append(status)


@pytest.fixture
def store():
    return StatusSpy()


def _run(store, llm, iterations=3):
    return run_drafting("RFP-P2TEST", store=store, llm=llm, knowledge_fn=no_knowledge,
                        clients_fn=lambda: ["RetailMax Corp"], iterations=iterations)


def _ready(store, *workstreams, hq="España", currency="EUR"):
    from drafting_fakes import make_ready_ticket

    make_ready_ticket(store, list(workstreams), client_hq=hq, currency=currency)
    store.status_log.clear()


def test_sections_pass_on_first_iteration(store):
    _ready(store, SELECCION_WS, CAPACITACION_WS)
    llm = fake_llm(generate_seleccion=section(GOOD_SELECCION), relevance_seleccion=PASS_RELEVANCE,
                   generate_capacitacion=section(GOOD_CAPACITACION), relevance_capacitacion=PASS_RELEVANCE)

    result = _run(store, llm)

    assert result["status"] == "en_evaluación"
    assert result["sections"] == {"seleccion": "evaluada_ok", "capacitacion": "evaluada_ok"}
    assert result["iterations"] == {"seleccion": 1, "capacitacion": 1}
    ticket = store.get_ticket("RFP-P2TEST")
    assert ticket["ready_for_approval"] is True
    assert store.status_log == ["generando_borrador", "en_evaluación"]
    for s in ticket["sections"]:
        assert s["draft_content"].startswith("## ")
        assert s["evaluation_results"]["overall_pass"] is True
        assert len(s["iteration_history"]) == 1


def test_failed_section_returns_to_its_generator_with_feedback_and_then_passes(store):
    _ready(store, SELECCION_WS)
    generate = Sequence(section(BAD_TTC_SELECCION), section(GOOD_SELECCION))
    llm = fake_llm(generate_seleccion=generate, relevance_seleccion=PASS_RELEVANCE)

    result = _run(store, llm)

    assert result["sections"] == {"seleccion": "evaluada_ok"}
    assert result["iterations"] == {"seleccion": 2}
    second_call = [c for c in llm.calls if c["task"] == "generate:seleccion"][1]
    assert "Iteración 2" in second_call["user"]
    assert "[ttc-min-15-dias-habiles]" in second_call["user"]  # feedback concreto del evaluador
    history = store.get_ticket("RFP-P2TEST")["sections"][0]["iteration_history"]
    assert [h["evaluation"]["overall_pass"] for h in history] == [False, True]


def test_iteration_limit_marks_needs_human_review_and_keeps_last_draft(store):
    _ready(store, SELECCION_WS, CAPACITACION_WS)
    llm = fake_llm(generate_seleccion=section(BAD_TTC_SELECCION), relevance_seleccion=PASS_RELEVANCE,
                   generate_capacitacion=section(GOOD_CAPACITACION), relevance_capacitacion=PASS_RELEVANCE)

    result = _run(store, llm, iterations=2)

    assert result["status"] == "needs_human_review"
    assert result["sections"] == {"seleccion": "needs_human_review", "capacitacion": "evaluada_ok"}
    assert result["iterations"]["seleccion"] == 2
    ticket = store.get_ticket("RFP-P2TEST")
    assert ticket["status"] == "needs_human_review"          # el ticket no se descarta
    assert ticket["ready_for_approval"] is True
    handoff = ticket["approval_handoff"]
    sel = next(s for s in handoff["sections"] if s["department_id"] == "seleccion")
    assert sel["needs_human_review"] is True
    assert sel["draft_content"] and "10 días hábiles" in sel["draft_content"]
    assert sel["evaluation_result"]["compliance"]["pass"] is False
    assert handoff["needs_human_review"] is True
    assert handoff["kpis"] == {"sections": 2, "passed": 1, "needs_human_review": 1, "avg_iterations": 1.5}
    assert store.status_log == ["generando_borrador", "en_evaluación", "needs_human_review"]


def test_stuck_department_does_not_block_the_others(store):
    _ready(store, SELECCION_WS, SOPORTE_WS, hq="Miami", currency="USD")
    llm = fake_llm(
        generate_seleccion=section(BAD_TTC_SELECCION.replace("EUR", "USD")), relevance_seleccion=PASS_RELEVANCE,
        generate_soporte=section(GOOD_SOPORTE), relevance_soporte=PASS_RELEVANCE,
    )

    result = _run(store, llm, iterations=3)

    assert result["sections"]["soporte"] == "evaluada_ok"
    assert result["iterations"]["soporte"] == 1
    assert result["sections"]["seleccion"] == "needs_human_review"
    assert result["iterations"]["seleccion"] == 3


def test_handoff_to_part3_has_draft_and_structured_evaluation_per_department(store):
    _ready(store, SOPORTE_WS, hq="Miami", currency="USD")
    _run(store, fake_llm(generate_soporte=section(GOOD_SOPORTE), relevance_soporte=PASS_RELEVANCE))

    handoff = store.get_ticket("RFP-P2TEST")["approval_handoff"]
    assert handoff["contract_version"] == "rfp_drafting.v1"
    assert handoff["ticket_id"] == "RFP-P2TEST" and handoff["currency"] == "USD"
    sec = handoff["sections"][0]
    assert sec["owner"] == "Roberto Díaz"
    evaluation = sec["evaluation_result"]
    assert set(evaluation) >= {"section_id", "department_id", "readability", "relevance", "compliance",
                               "overall_pass", "feedback_for_generator"}
    assert set(evaluation["compliance"]) >= {"pass", "rule_ids", "violations"}
    assert "sla-24h" in evaluation["compliance"]["rule_ids"]


def test_part2_requires_part1_handoff(store):
    store.create_ticket(ticket_id="RFP-P2TEST", raw_pdf_path="x", original_filename="x.pdf", created_by=None)
    with pytest.raises(HandoffNotReady):
        _run(store, fake_llm())
    assert store.get_ticket("RFP-P2TEST")["status"] == "analizando"


def test_regeneration_resets_previous_drafts(store):
    _ready(store, SOPORTE_WS, hq="Miami", currency="USD")
    llm = fake_llm(generate_soporte=section(GOOD_SOPORTE), relevance_soporte=PASS_RELEVANCE)
    _run(store, llm)
    _run(store, llm)

    sec = store.get_ticket("RFP-P2TEST")["sections"][0]
    assert sec["iteration_count"] == 1
    assert len(sec["iteration_history"]) == 1
