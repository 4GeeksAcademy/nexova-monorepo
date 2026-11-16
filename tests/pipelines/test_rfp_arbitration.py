"""
Arbitraje (BRIEFING §7) como funciones puras: detección de los triggers en
estado estructurado y regla de resolución con árbitro fijo.

    uv run --project services/api pytest tests/pipelines/test_rfp_arbitration.py -v
"""

from __future__ import annotations

from drafting_fakes import BAD_TTC_SELECCION, GOOD_CAPACITACION, GOOD_SELECCION, GOOD_SOPORTE
from rfp_approval.arbitration import (
    ACTION_ESCALATE,
    ACTION_REJECT,
    ACTION_REQUEST_CHANGES,
    detect_conflicts,
    resolve,
    structured_facts,
    training_window,
)


def _ticket(hq: str, drafts: dict[str, str]) -> dict:
    return {
        "metadata": {"client_hq": hq},
        "approval_handoff": {"client_hq": hq, "sections": [{"department_id": d} for d in drafts]},
        "sections": [{"department_id": d, "draft_content": text} for d, text in drafts.items()],
    }


def _triggers(ticket: dict) -> list[tuple[str, str]]:
    return [(c.trigger_id, c.department_id) for c in detect_conflicts(ticket)]


def test_clean_proposal_has_no_conflicts():
    assert _triggers(_ticket("España", {"seleccion": GOOD_SELECCION, "capacitacion": GOOD_CAPACITACION})) == []
    assert _triggers(_ticket("Miami", {"soporte": GOOD_SOPORTE})) == []


def test_currency_is_checked_against_the_client_hq_mapping():
    ticket = _ticket("Miami", {"soporte": GOOD_SOPORTE.replace("USD", "EUR")})
    conflicts = detect_conflicts(ticket)
    assert [(c.trigger_id, c.department_id, c.arbiter) for c in conflicts] == [
        ("currency-mismatch", "soporte", "Marcos Ibáñez")]
    assert conflicts[0].evidence["expected_currency"] == "USD"


def test_sections_disagreeing_on_currency_without_known_hq_conflict():
    ticket = _ticket("desconocido", {"seleccion": GOOD_SELECCION,
                                     "soporte": GOOD_SOPORTE})  # EUR vs USD, sede sin confirmar
    assert sorted(_triggers(ticket)) == [("currency-mismatch", "seleccion"), ("currency-mismatch", "soporte")]


def test_support_without_explicit_24h_sla_conflicts():
    draft = GOOD_SOPORTE.replace("Nos comprometemos a un SLA de respuesta de 24 horas para todas las consultas. ", "")
    assert _triggers(_ticket("Miami", {"soporte": draft})) == [("support-sla-missing", "soporte")]
    assert detect_conflicts(_ticket("Miami", {"soporte": draft}))[0].arbiter == "Roberto Díaz"


def test_training_window_is_read_from_the_draft():
    assert training_window(GOOD_CAPACITACION) == {"sequenced_after_close": True, "start_business_days": None}
    early = GOOD_CAPACITACION.replace(
        "La formación empezará cuando estén cerradas las nuevas posiciones de mandos medios.",
        "La formación empezará 2 semanas después de la firma del contrato.")
    assert training_window(early) == {"sequenced_after_close": False, "start_business_days": 10}


def test_ttc_vs_training_window_only_when_both_departments_are_active():
    assert _triggers(_ticket("España", {"seleccion": BAD_TTC_SELECCION})) == []
    both = _ticket("España", {"seleccion": BAD_TTC_SELECCION, "capacitacion": GOOD_CAPACITACION})
    assert _triggers(both) == [("ttc-vs-training-window", "seleccion"), ("ttc-vs-training-window", "capacitacion")]
    facts = structured_facts(both)
    assert facts["seleccion_ttc_business_days"] == [10]


def test_resolution_rule_is_fixed_per_trigger():
    ticket = _ticket("Miami", {"soporte": GOOD_SOPORTE.replace("USD", "EUR").replace(
        "Nos comprometemos a un SLA de respuesta de 24 horas para todas las consultas. ", "")})
    conflicts = detect_conflicts(ticket)
    assert {c.trigger_id for c in conflicts} == {"support-sla-missing", "currency-mismatch"}

    assert resolve(conflicts, approval_round=0, max_rounds=2).action == ACTION_REQUEST_CHANGES
    at_limit = resolve(conflicts, approval_round=2, max_rounds=2)
    assert at_limit.action == ACTION_REJECT
    assert at_limit.arbiter == "Roberto Díaz"  # SLA: Roberto bloquea
    assert "[support-sla-missing]" in at_limit.feedback and "[currency-mismatch]" in at_limit.feedback

    ttc = detect_conflicts(_ticket("España", {"seleccion": BAD_TTC_SELECCION, "capacitacion": GOOD_CAPACITACION}))
    escalated = resolve(ttc, approval_round=2, max_rounds=2)
    assert (escalated.action, escalated.arbiter) == (ACTION_ESCALATE, "Marcos Ibáñez")
    assert resolve([], approval_round=0, max_rounds=2).action is None
