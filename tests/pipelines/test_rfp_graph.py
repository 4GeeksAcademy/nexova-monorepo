"""
Tests de extremo a extremo del grafo `rfp_intake` con los 3 PDF de muestra del
BRIEFING (conversión real con MarkItDown, LLM falso, store en memoria).

    uv run --project services/api pytest tests/pipelines/test_rfp_graph.py -v
"""

from __future__ import annotations

import copy

import pytest

from rfp_fakes import (
    FORMAL_PDF,
    FORMAL_RESPONSES,
    INFORMAL_PDF,
    INFORMAL_RESPONSES,
    FakeLLM,
    upload,
)
from rfp_intake import InMemoryRfpStore, build_rfp_graph, run_intake
from rfp_intake.nodes import HANDOFF_CONTRACT_VERSION


@pytest.fixture(autouse=True)
def raw_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("RFP_RAW_DIR", str(tmp_path / "raw" / "rfp"))
    return tmp_path / "raw" / "rfp"


@pytest.fixture
def store():
    return InMemoryRfpStore()


def test_formal_rfp_routes_to_seleccion_and_capacitacion_in_eur(store, raw_dir):
    ticket_id = upload(store, FORMAL_PDF)
    assert (raw_dir / f"{ticket_id}.pdf").is_file()
    llm = FakeLLM(FORMAL_RESPONSES)

    result = run_intake(ticket_id, store=store, llm=llm)

    assert result == {"ticket_id": ticket_id, "status": "analisis_completo",
                      "departments": ["seleccion", "capacitacion"], "attempt": 1}
    assert "worker:soporte" not in llm.tasks()  # nunca los tres por defecto

    ticket = store.get_ticket(ticket_id)
    assert ticket["status"] == "analisis_completo"
    assert ticket["rfp_id"] == "VRG-2026-011"
    metadata = ticket["metadata"]
    assert metadata["client_hq"] == "España"
    assert metadata["currency"] == "EUR"
    assert metadata["deadline"] == "2026-08-18"
    # El presupuesto que "vio" el modelo no está en la RFP: no se guarda.
    assert {v["concept"] for v in metadata["volumes"]} == {"posiciones de mandos medios", "participantes en formación"}
    assert metadata["readability"]["word_count"] > 100

    owners = {s["department_id"]: s["owner_name"] for s in ticket["sections"]}
    assert owners == {"seleccion": "Javier Almeida", "capacitacion": "Elena Vargas"}
    assert all(s["key_aspects"] for s in ticket["sections"])

    # Ventas sabe qué pedir a quién sin abrir el PDF.
    who = {w["owner"]: w["requests"] for w in ticket["synthesis"]["who_to_ask"]}
    assert set(who) == {"Javier Almeida", "Elena Vargas"}
    assert ticket["synthesis"]["currency"] == "EUR"


def test_handoff_carries_ticket_id_and_workstreams_for_part_two(store):
    ticket_id = upload(store, FORMAL_PDF)
    run_intake(ticket_id, store=store, llm=FakeLLM(FORMAL_RESPONSES))

    ticket = store.get_ticket(ticket_id)
    handoff = ticket["handoff_payload"]
    assert ticket["ready_for_drafting"] is True
    assert handoff["contract_version"] == HANDOFF_CONTRACT_VERSION
    assert handoff["ticket_id"] == ticket_id
    assert handoff["currency"] == "EUR"
    assert [w["department_id"] for w in handoff["workstreams"]] == ["seleccion", "capacitacion"]
    assert all(w["key_aspects"] and w["owner"] for w in handoff["workstreams"])


def test_informal_email_is_accepted_and_routed_to_soporte_in_usd(store):
    ticket_id = upload(store, INFORMAL_PDF)

    result = run_intake(ticket_id, store=store, llm=FakeLLM(INFORMAL_RESPONSES))

    assert result["status"] == "analisis_completo"
    ticket = store.get_ticket(ticket_id)
    assert ticket["classification"]["rfp_type"] == "informal"
    assert ticket["metadata"]["client_hq"] == "Miami"
    assert ticket["metadata"]["currency"] == "USD"
    assert ticket["metadata"]["deadline"] is None  # "August 23rd" sin año: no se inventa
    assert "confirmar" in ticket["metadata"]["deadline_text"]
    assert ticket["handoff_payload"]["deadline"] is None
    assert [s["owner_name"] for s in ticket["sections"]] == ["Roberto Díaz"]


def test_figure_whose_value_is_not_in_its_quote_is_rejected(store):
    """Citar "5 posiciones" para afirmar "4" no cuela: pasa a pregunta abierta."""
    responses = copy.deepcopy(FORMAL_RESPONSES)
    responses["worker:capacitacion"]["figures_cited"] = [
        {"concept": "posiciones de mandos medios", "value": "4", "quote": "Búsqueda ejecutiva para 5 posiciones"},
    ]
    ticket_id = upload(store, FORMAL_PDF)
    run_intake(ticket_id, store=store, llm=FakeLLM(responses))

    sections = {s["department_id"]: s for s in store.get_ticket(ticket_id)["sections"]}
    assert sections["capacitacion"]["figures_cited"] == []
    assert any("(4)" in q for q in sections["capacitacion"]["open_questions"])
    assert store.get_ticket(ticket_id)["synthesis"]["conflicts"] == []


CONTRADICTORY_RFP = """REQUEST FOR PROPOSAL — Grupo Ejemplo (Madrid, España)

Búsqueda ejecutiva para 5 posiciones de mandos medios.

Programa de formación en liderazgo para los 4 mandos medios que se incorporen."""


def test_contradicting_workers_raise_conflict_without_resolving_it(store):
    responses = copy.deepcopy(FORMAL_RESPONSES)
    responses["metadata"]["volumes"] = []
    responses["orchestrate"]["departments"][0]["excerpts"] = ["Búsqueda ejecutiva para 5 posiciones de mandos medios."]
    responses["orchestrate"]["departments"][1]["excerpts"] = ["formación en liderazgo para los 4 mandos medios"]
    responses["worker:seleccion"]["figures_cited"] = [
        {"concept": "Mandos medios", "value": "5", "quote": "5 posiciones de mandos medios"}]
    responses["worker:capacitacion"]["figures_cited"] = [
        {"concept": "mandos medios", "value": "4", "quote": "los 4 mandos medios"}]
    store.create_ticket(ticket_id="RFP-C1", raw_pdf_path="data/raw/rfp/RFP-C1.pdf",
                        original_filename="c.pdf", created_by=None)

    graph = build_rfp_graph(llm=FakeLLM(responses), store=store)
    final = graph.invoke({"ticket_id": "RFP-C1", "markdown": CONTRADICTORY_RFP})

    assert final["status"] == "analisis_completo"
    conflicts = store.get_ticket("RFP-C1")["synthesis"]["conflicts"]
    assert len(conflicts) == 1
    assert conflicts[0]["resolution"] == "pendiente_ventas"  # se señala, no se resuelve
    assert {v["source"] for v in conflicts[0]["values"]} == {"seleccion", "capacitacion"}
    assert store.get_ticket("RFP-C1")["handoff_payload"]["conflicts"] == conflicts


def test_synthesizer_failure_falls_back_to_deterministic_summary(store):
    responses = {**FORMAL_RESPONSES, "synthesize": RuntimeError("timeout")}
    ticket_id = upload(store, FORMAL_PDF)

    run_intake(ticket_id, store=store, llm=FakeLLM(responses))

    synthesis = store.get_ticket(ticket_id)["synthesis"]
    assert synthesis["source"] == "fallback"
    assert "Javier Almeida" in synthesis["summary_for_sales"]
    assert "EUR" in synthesis["summary_for_sales"]
    assert synthesis["next_steps"]


def test_worker_failure_leaves_ticket_analyzing_and_rerun_is_idempotent(store):
    responses = {**FORMAL_RESPONSES, "worker:capacitacion": RuntimeError("LLM caído")}
    ticket_id = upload(store, FORMAL_PDF)

    with pytest.raises(RuntimeError):
        run_intake(ticket_id, store=store, llm=FakeLLM(responses))
    assert store.get_ticket(ticket_id)["status"] == "analizando"

    # Reintento (como haría Celery): termina y no duplica secciones.
    run_intake(ticket_id, store=store, llm=FakeLLM(FORMAL_RESPONSES))
    run_intake(ticket_id, store=store, llm=FakeLLM(FORMAL_RESPONSES))
    ticket = store.get_ticket(ticket_id)
    assert ticket["status"] == "analisis_completo"
    assert ticket["attempts"] == 3
    assert sorted(s["department_id"] for s in ticket["sections"]) == ["capacitacion", "seleccion"]


def test_one_failed_ticket_does_not_block_others(store):
    bad = upload(store, FORMAL_PDF)
    good = upload(store, INFORMAL_PDF)

    with pytest.raises(RuntimeError):
        run_intake(bad, store=store, llm=FakeLLM({**FORMAL_RESPONSES, "metadata": RuntimeError("x")}))
    run_intake(good, store=store, llm=FakeLLM(INFORMAL_RESPONSES))

    statuses = {t["ticket_id"]: t["status"] for t in store.list_tickets()}
    assert statuses == {bad: "analizando", good: "analisis_completo"}
