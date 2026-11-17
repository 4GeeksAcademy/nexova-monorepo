"""
Evaluador de cumplimiento de la Parte 2, anclado al BRIEFING §5.

    uv run --project services/api pytest tests/pipelines/test_rfp_compliance.py -v
"""

from __future__ import annotations

import pytest

from drafting_fakes import BAD_TTC_SELECCION, GOOD_CAPACITACION, GOOD_SELECCION, GOOD_SOPORTE
from rfp_drafting.compliance_rules import (
    RULE_ANONYMIZED,
    RULE_CURRENCY,
    RULE_GUARANTEE,
    RULE_SLA,
    RULE_TTC,
    ComplianceContext,
    evaluate_compliance,
)

EUR_SELECCION = ComplianceContext(department_id="seleccion", client_hq="España", currency="EUR",
                                  known_clients=("RetailMax Corp", "FinServ Group"))
USD_SOPORTE = ComplianceContext(department_id="soporte", client_hq="Miami", currency="USD")


def _rule_ids(check):
    return {v.rule_id for v in check.violations}


# --- Caso pedido por el README: un fixture, una aserción de fallo -------------
def test_promising_close_under_15_business_days_fails_compliance():
    """BRIEFING §5: ninguna búsqueda ejecutiva puede cerrar en menos de 15 días hábiles."""
    check = evaluate_compliance(BAD_TTC_SELECCION, EUR_SELECCION)

    assert check.passed is False
    assert _rule_ids(check) == {RULE_TTC}


# --- Resto de reglas ----------------------------------------------------------
@pytest.mark.parametrize(
    "draft, ctx",
    [
        (GOOD_SELECCION, EUR_SELECCION),
        (GOOD_CAPACITACION, ComplianceContext(department_id="capacitacion", client_hq="España", currency="EUR")),
        (GOOD_SOPORTE, USD_SOPORTE),
    ],
)
def test_compliant_drafts_pass_every_rule(draft, ctx):
    check = evaluate_compliance(draft, ctx)
    assert check.passed is True, check.violations
    assert RULE_GUARANTEE in check.rule_ids and RULE_CURRENCY in check.rule_ids


def test_rules_evaluated_depend_on_department():
    assert RULE_TTC in evaluate_compliance(GOOD_SELECCION, EUR_SELECCION).rule_ids
    assert RULE_SLA not in evaluate_compliance(GOOD_SELECCION, EUR_SELECCION).rule_ids
    assert RULE_SLA in evaluate_compliance(GOOD_SOPORTE, USD_SOPORTE).rule_ids


def test_missing_90_day_guarantee_fails():
    draft = GOOD_SOPORTE.replace("Incluimos la garantía de satisfacción de 90 días de Nexova.", "")
    assert _rule_ids(evaluate_compliance(draft, USD_SOPORTE)) == {RULE_GUARANTEE}


def test_wrong_currency_for_spain_fails():
    draft = GOOD_SELECCION.replace("facturados en EUR", "facturados en USD")
    check = evaluate_compliance(draft, EUR_SELECCION)
    assert _rule_ids(check) == {RULE_CURRENCY}
    assert any("USD" in v.message for v in check.violations)


def test_unknown_headquarters_cannot_be_priced():
    ctx = ComplianceContext(department_id="seleccion", client_hq="desconocido", currency=None)
    assert RULE_CURRENCY in _rule_ids(evaluate_compliance(GOOD_SELECCION, ctx))


def test_support_without_24h_sla_fails():
    draft = GOOD_SOPORTE.replace("Nos comprometemos a un SLA de respuesta de 24 horas para todas las consultas. ", "")
    assert _rule_ids(evaluate_compliance(draft, USD_SOPORTE)) == {RULE_SLA}


def test_naming_a_current_client_fails():
    draft = GOOD_SELECCION.replace("con un cliente del sector retail", "con RetailMax")
    check = evaluate_compliance(draft, EUR_SELECCION)
    assert _rule_ids(check) == {RULE_ANONYMIZED}
    assert "RetailMax Corp" in check.violations[0].message


@pytest.mark.parametrize(
    "sentence, fails",
    [
        ("Estimamos el cierre de cada posición en 2 semanas.", True),        # 10 días hábiles
        ("Estimamos el cierre de cada posición en 3 semanas.", False),       # 15 días hábiles
        ("Cubriremos las posiciones en 15 días naturales.", True),           # ≈10,7 hábiles
        ("El cierre llevará entre 15 y 20 días hábiles.", False),
        ("Entregaremos el primer shortlist en 10 días.", False),             # no es cierre
    ],
)
def test_time_to_close_parsing(sentence, fails):
    draft = GOOD_SELECCION.replace("Estimamos el cierre de cada posición entre 30 y 40 días hábiles.", sentence)
    assert (RULE_TTC in _rule_ids(evaluate_compliance(draft, EUR_SELECCION))) is fails
