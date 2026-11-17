"""
Generadores por departamento y evaluadores de legibilidad y pertinencia (Parte 2).

    uv run --project services/api pytest tests/pipelines/test_rfp_generators.py -v
"""

from __future__ import annotations

import pytest

from drafting_fakes import (
    CAPACITACION_WS,
    GOOD_SELECCION,
    SELECCION_WS,
    SOPORTE_WS,
    fake_llm,
    section,
)
from rfp_drafting.evaluators import build_evaluation, evaluate_readability, evaluate_relevance
from rfp_drafting.compliance_rules import ComplianceContext, evaluate_compliance
from rfp_drafting.generators import GENERATORS
from rfp_drafting.knowledge import gather_knowledge

HEADER_EUR = {"client_name": "Vantex Retail Group, S.A.", "client_hq": "España", "currency": "EUR",
              "deadline": "2026-08-18", "services_requested": ["Búsqueda ejecutiva"]}
HEADER_USD = {**HEADER_EUR, "client_name": "NubeSoft", "client_hq": "Miami", "currency": "USD"}


# --------------------------------------------------------------------------- #
# Generadores
# --------------------------------------------------------------------------- #
def test_one_generator_per_department_with_its_own_prompt():
    assert set(GENERATORS) == {"seleccion", "capacitacion", "soporte"}
    prompts = {d: g.system_prompt("EUR") for d, g in GENERATORS.items()}
    assert len(set(prompts.values())) == 3
    assert "Javier Almeida" in prompts["seleccion"] and "15 días hábiles" in prompts["seleccion"]
    assert "Elena Vargas" in prompts["capacitacion"]
    assert "Roberto Díaz" in prompts["soporte"] and "24 horas" in prompts["soporte"]


def test_generator_works_from_part1_handoff_only():
    llm = fake_llm(generate_seleccion=section(GOOD_SELECCION))
    knowledge = [{"source_document": "nexova-pricing-model.es.md", "section": "Headhunting",
                  "text": "Tarifa estándar: 22% del salario anual bruto."}]

    draft = GENERATORS["seleccion"].generate(llm, header=HEADER_EUR, workstream=SELECCION_WS, knowledge=knowledge)

    assert draft.startswith("## Búsqueda y selección de talento")
    call = llm.call("generate:seleccion")
    assert "5 posiciones de mandos medios en retail" in call["user"]       # key_aspects de la Parte 1
    assert "22% del salario" in call["user"]                                # base de conocimiento
    assert "<documento>" not in call["user"]                               # nada del PDF/Markdown
    assert "garantía de satisfacción de 90 días" in call["system"]
    assert "EUR" in call["system"] and "No escribas 'USD'" in call["system"]


def test_generator_receives_feedback_on_second_iteration():
    llm = fake_llm(generate_soporte=section(GOOD_SELECCION))
    GENERATORS["soporte"].generate(llm, header=HEADER_USD, workstream=SOPORTE_WS, knowledge=[],
                                   previous_draft="## Borrador viejo", feedback="[sla-24h] Falta el SLA",
                                   iteration=2)
    user = llm.call("generate:soporte")["user"]
    assert "Iteración 2" in user and "## Borrador viejo" in user and "[sla-24h] Falta el SLA" in user


def test_generator_appends_assumptions_to_draft():
    llm = fake_llm(generate_capacitacion={"title": "Programa", "content": "Texto.",
                                          "assumptions": ["Modalidad a confirmar"]})
    draft = GENERATORS["capacitacion"].generate(llm, header=HEADER_EUR, workstream=CAPACITACION_WS, knowledge=[])
    assert "**Supuestos a confirmar con el cliente:**\n- Modalidad a confirmar" in draft


def test_knowledge_is_deduplicated_and_tolerates_empty_results():
    chunk = {"source_document": "a.md", "section": "S", "text": "Tarifa"}
    assert gather_knowledge(("q1", "q2"), lambda q: [chunk]) == [chunk]
    assert gather_knowledge(("q1",), lambda q: []) == []


# --------------------------------------------------------------------------- #
# Evaluador de legibilidad
# --------------------------------------------------------------------------- #
def test_readability_passes_clear_section():
    result = evaluate_readability(GOOD_SELECCION)
    assert result.passed is True
    assert result.details["word_count"] >= 120


def test_readability_fails_too_short_section():
    result = evaluate_readability("## Sección\n\nCubriremos las posiciones. Garantía de 90 días.")
    assert result.passed is False
    assert any("palabras" in p for p in result.details["problems"])


def test_readability_fails_endless_sentences():
    endless = ("La propuesta contempla " + "la coordinación interdepartamental y la optimización metodológica, "
               * 12 + "fin. ") * 3
    result = evaluate_readability(endless)
    assert result.passed is False
    assert any("frase" in p or "Flesch" in p or "Fog" in p for p in result.details["problems"])


# --------------------------------------------------------------------------- #
# Evaluador de pertinencia
# --------------------------------------------------------------------------- #
def test_relevance_receives_only_draft_and_department_aspects():
    llm = fake_llm(relevance_seleccion={"missing_aspects": [], "covered_aspects": ["5 posiciones"]})
    result = evaluate_relevance(GOOD_SELECCION, SELECCION_WS, llm)

    assert result.passed is True
    user = llm.call("relevance:seleccion")["user"]
    assert "Estimar el tiempo de cierre por posición" in user
    assert "Programa de liderazgo" not in user  # nada de otros departamentos


def test_relevance_fails_with_missing_aspects():
    llm = fake_llm(relevance_seleccion={"missing_aspects": ["Dimensionar las horas de consultoría"]})
    result = evaluate_relevance(GOOD_SELECCION, SELECCION_WS, llm)
    assert result.passed is False
    assert result.missing_aspects == ["Dimensionar las horas de consultoría"]


# --------------------------------------------------------------------------- #
# EvaluationResult y feedback
# --------------------------------------------------------------------------- #
def test_failed_evaluation_produces_concrete_feedback_and_contract_shape():
    llm = fake_llm(relevance_seleccion={"missing_aspects": ["Dimensionar las horas de consultoría"]})
    draft = GOOD_SELECCION.replace("entre 30 y 40 días hábiles", "en 10 días hábiles")
    ctx = ComplianceContext(department_id="seleccion", client_hq="España", currency="EUR")

    result = build_evaluation(
        ticket_id="RFP-1", department_id="seleccion", iteration=1,
        readability=evaluate_readability(draft), relevance=evaluate_relevance(draft, SELECCION_WS, llm),
        compliance=evaluate_compliance(draft, ctx),
    )
    record = result.to_record()

    assert record["overall_pass"] is False
    assert record["readability"]["pass"] is True
    assert record["relevance"] == {"pass": False, "missing_aspects": ["Dimensionar las horas de consultoría"],
                                   "covered_aspects": []}
    assert record["compliance"]["pass"] is False
    assert record["compliance"]["violations"][0]["rule_id"] == "ttc-min-15-dias-habiles"
    feedback = record["feedback_for_generator"]
    assert "[ttc-min-15-dias-habiles]" in feedback and "10 días hábiles" in feedback
    assert "[pertinencia] No respondes a: Dimensionar las horas de consultoría" in feedback


@pytest.mark.parametrize("ws", [SELECCION_WS, CAPACITACION_WS, SOPORTE_WS])
def test_section_id_identifies_ticket_and_department(ws):
    ok = evaluate_readability(GOOD_SELECCION)
    llm = fake_llm(**{f"relevance_{ws['department_id']}": {"missing_aspects": []}})
    result = build_evaluation(ticket_id="RFP-9", department_id=ws["department_id"], iteration=1,
                              readability=ok, relevance=evaluate_relevance(GOOD_SELECCION, ws, llm),
                              compliance=evaluate_compliance(GOOD_SELECCION, ComplianceContext(
                                  department_id=ws["department_id"], client_hq="España", currency="EUR")))
    assert result.section_id == f"RFP-9:{ws['department_id']}"


def test_relevance_uses_per_aspect_verdicts_and_requires_evidence():
    llm = fake_llm(relevance_seleccion={"aspects": [
        {"aspect": "5 posiciones de mandos medios en retail", "covered": True,
         "evidence": "Cubriremos las 5 posiciones"},
        {"aspect": "Primer shortlist en 3 semanas", "covered": True, "evidence": ""},  # sin cita
        {"aspect": "Dimensionar las horas de consultoría", "covered": False, "evidence": ""},
    ]})
    result = evaluate_relevance(GOOD_SELECCION, SELECCION_WS, llm)

    assert result.passed is False
    assert result.covered_aspects == ["5 posiciones de mandos medios en retail"]
    assert result.missing_aspects == ["Primer shortlist en 3 semanas", "Dimensionar las horas de consultoría"]


def test_render_drops_heading_repeated_by_the_model():
    llm = fake_llm(generate_capacitacion={"title": "Programa de formación corporativa",
                                          "content": "## Programa de liderazgo\n\nTexto del programa."})
    draft = GENERATORS["capacitacion"].generate(llm, header=HEADER_EUR, workstream=CAPACITACION_WS, knowledge=[])
    assert draft == "## Programa de formación corporativa\n\nTexto del programa."


def test_deadline_is_labelled_as_proposal_due_date():
    llm = fake_llm(generate_capacitacion=section(GOOD_SELECCION))
    GENERATORS["capacitacion"].generate(llm, header=HEADER_EUR, workstream=CAPACITACION_WS, knowledge=[])
    assert '"fecha_limite_entrega_de_la_propuesta": "2026-08-18"' in llm.call("generate:capacitacion")["user"]


def test_generator_unwraps_section_nested_by_the_model():
    """Fallo real del modelo de 4Geeks: devolvió la sección como texto JSON dentro de `messages`."""
    import json as _json

    wrapped = {"messages": [{"role": "user", "content": _json.dumps(
        {"title": "Búsqueda", "content": "Texto de la sección.", "assumptions": []})}]}
    llm = fake_llm(generate_seleccion=wrapped)
    draft = GENERATORS["seleccion"].generate(llm, header=HEADER_EUR, workstream=SELECCION_WS, knowledge=[])
    assert draft == "## Búsqueda\n\nTexto de la sección."
    assert llm.tasks() == ["generate:seleccion"]


def test_generator_retries_once_when_format_is_wrong():
    from drafting_fakes import Sequence

    llm = fake_llm(generate_seleccion=Sequence({"texto": "sin formato"}, section(GOOD_SELECCION)))
    draft = GENERATORS["seleccion"].generate(llm, header=HEADER_EUR, workstream=SELECCION_WS, knowledge=[])
    assert draft.startswith("## Búsqueda y selección de talento")
    assert llm.tasks() == ["generate:seleccion", "generate:seleccion"]
    assert "no tenía el formato pedido" in llm.calls[1]["user"]
