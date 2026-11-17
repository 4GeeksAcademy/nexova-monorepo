"""
Tests del orquestador y de los workers por departamento del flujo `rfp_intake`.

    uv run --project services/api pytest tests/pipelines/test_rfp_workers.py -v
"""

from __future__ import annotations

import pytest

from rfp_fakes import FakeLLM
from rfp_intake import InMemoryRfpStore
from rfp_intake.nodes import IntakeNodes, fan_out

MARKDOWN = """REQUEST FOR PROPOSAL (RFP)

Vantex Retail Group es una cadena de retail con sede en Madrid.

2. Scope of Work / Services Requested

- Búsqueda ejecutiva para 5 posiciones de mandos medios (Retail Operations Manager)

- Programa de formación en liderazgo, formato trimestral, para 40 participantes

3. Evaluation Criteria

Precio 25%"""

SHARED = {"client_name": "Vantex Retail Group", "client_hq": "España", "currency": "EUR",
          "services_requested": ["Búsqueda ejecutiva"], "volumes": []}


@pytest.fixture
def store():
    store = InMemoryRfpStore()
    store.create_ticket(ticket_id="RFP-W1", raw_pdf_path="data/raw/rfp/RFP-W1.pdf",
                        original_filename="vantex.pdf", created_by=None)
    return store


def _payload(department_id="seleccion", excerpts=None):
    return {
        "ticket_id": "RFP-W1",
        "department_id": department_id,
        "reason": "Pide búsqueda ejecutiva.",
        "excerpts": excerpts if excerpts is not None else
        ["Búsqueda ejecutiva para 5 posiciones de mandos medios (Retail Operations Manager)"],
        "shared_metadata": SHARED,
    }


# --------------------------------------------------------------------------- #
# Worker
# --------------------------------------------------------------------------- #
def test_worker_receives_only_its_excerpts_and_shared_metadata(store):
    llm = FakeLLM({"worker:seleccion": {"key_aspects": ["5 mandos medios"]}})
    IntakeNodes(llm, store).department_worker(_payload())

    call = llm.call("worker:seleccion")
    assert "Búsqueda ejecutiva para 5 posiciones" in call["user"]
    assert "Vantex Retail Group" in call["user"]  # metadatos compartidos
    # Nada del resto del documento: ni la formación ni los criterios de evaluación.
    assert "40 participantes" not in call["user"]
    assert "Evaluation Criteria" not in call["user"]
    assert "Javier Almeida" in call["system"]


def test_worker_never_keeps_invented_figures(store):
    """Una cifra sin cita literal no se guarda: pasa a pregunta abierta."""
    llm = FakeLLM({"worker:seleccion": {
        "key_aspects": ["5 posiciones"],
        "requests_to_owner": ["Estimar tiempo de cierre"],
        "open_questions": [],
        "figures_cited": [
            {"concept": "posiciones", "value": "5", "quote": "5 posiciones de mandos medios"},
            {"concept": "horas de consultoría", "value": "120", "quote": "120 horas de consultoría"},
        ],
    }})
    update = IntakeNodes(llm, store).department_worker(_payload())

    section = update["department_results"][0]
    assert [f["value"] for f in section["figures_cited"]] == ["5"]
    assert any("horas de consultoría" in q and "120" in q for q in section["open_questions"])


def test_worker_owner_comes_from_catalog_and_section_is_persisted(store):
    llm = FakeLLM({"worker:soporte": {"key_aspects": ["24/7"], "requests_to_owner": ["Dimensionar turnos"]}})
    IntakeNodes(llm, store).department_worker(_payload("soporte", ["12 support agents covering us 24/7"]))

    sections = store.get_ticket("RFP-W1")["sections"]
    assert len(sections) == 1
    assert sections[0]["department_id"] == "soporte"
    assert sections[0]["owner_name"] == "Roberto Díaz"
    assert sections[0]["key_aspects"] == ["24/7"]
    assert sections[0]["approval_status"] is None  # se decide en la Parte 3


# --------------------------------------------------------------------------- #
# Orquestador
# --------------------------------------------------------------------------- #
def _orchestrate(store, plan):
    llm = FakeLLM({"orchestrate": plan})
    state = {"ticket_id": "RFP-W1", "markdown": MARKDOWN, "metadata": dict(SHARED), "rfp_id": "VRG-1"}
    return IntakeNodes(llm, store).orchestrate(state), state


def test_orchestrator_activates_only_requested_departments(store):
    update, state = _orchestrate(store, {"departments": [
        {"department_id": "seleccion", "excerpts": ["Búsqueda ejecutiva para 5 posiciones de mandos medios"]},
        {"department_id": "capacitacion", "excerpts": ["Programa de formación en liderazgo"]},
    ]})

    assert [a["department_id"] for a in update["assignments"]] == ["seleccion", "capacitacion"]
    assert update["metadata"]["departments_needed"] == ["seleccion", "capacitacion"]
    sends = fan_out({**state, **update})
    assert [s.arg["department_id"] for s in sends] == ["seleccion", "capacitacion"]
    assert all(s.node == "department_worker" for s in sends)


def test_orchestrator_routes_unknown_department_to_unmapped_services(store):
    update, _ = _orchestrate(store, {
        "departments": [
            {"department_id": "seleccion", "excerpts": ["Búsqueda ejecutiva para 5 posiciones"]},
            {"department_id": "desarrollo_software", "services": ["App móvil para tiendas"]},
        ],
        "unmapped_services": ["Auditoría de nóminas"],
    })

    assert [a["department_id"] for a in update["assignments"]] == ["seleccion"]
    unmapped = update["metadata"]["unmapped_services"]
    assert "Auditoría de nóminas" in unmapped
    assert any("App móvil" in s and "desarrollo_software" in s for s in unmapped)


def test_orchestrator_replaces_non_literal_excerpts_with_keyword_paragraphs(store):
    update, _ = _orchestrate(store, {"departments": [
        {"department_id": "capacitacion", "excerpts": ["El cliente quiere 300 alumnos"]},  # no está en la RFP
    ]})

    excerpts = update["assignments"][0]["excerpts"]
    assert "El cliente quiere 300 alumnos" not in excerpts
    assert any("40 participantes" in e for e in excerpts)


def test_orchestrator_without_departments_discards(store):
    update, state = _orchestrate(store, {"departments": [], "unmapped_services": ["Desarrollo web"]})

    assert update["discarded"] is True
    assert update["classification"]["decided_by"] == "orchestrator"
    assert fan_out({**state, **update}) == "discard"
