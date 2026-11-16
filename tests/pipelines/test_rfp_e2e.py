"""
Ruta E2E reproducible Partes 1 → 3, sin BD ni LLM reales: el PDF de muestra
del BRIEFING entra por `register_upload` (como la API), pasa por la recepción,
la generación y evaluación, y termina con aprobaciones humanas SIMULADAS
(`resume_approval` programático) en el documento final.

Comprueba que se sienta como un solo proceso: estados del ticket en orden, sin
saltos ni estados huérfanos, y datos que no se pierden entre partes.

    uv run --project services/api pytest tests/pipelines/test_rfp_e2e.py -v
"""

from __future__ import annotations

import pytest

from approval_fakes import Approvals, StatusSpy, drafting_llm
from drafting_fakes import GOOD_CAPACITACION, GOOD_SELECCION, GOOD_SOPORTE, no_knowledge
from rfp_drafting import run_drafting
from rfp_fakes import FORMAL_PDF, FORMAL_RESPONSES, INFORMAL_PDF, INFORMAL_RESPONSES, FakeLLM, upload
from rfp_intake import run_intake


@pytest.fixture(autouse=True)
def raw_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("RFP_RAW_DIR", str(tmp_path / "raw" / "rfp"))


def _run_parts_1_and_2(store, pdf, intake_responses, drafts):
    ticket_id = upload(store, pdf)
    assert run_intake(ticket_id, store=store, llm=FakeLLM(intake_responses))["status"] == "analisis_completo"
    llm = drafting_llm(drafts)
    run_drafting(ticket_id, store=store, llm=llm, knowledge_fn=no_knowledge, clients_fn=lambda: [])
    return ticket_id, llm


def test_formal_rfp_goes_from_upload_to_final_document():
    store = StatusSpy()
    ticket_id, llm = _run_parts_1_and_2(store, FORMAL_PDF, FORMAL_RESPONSES,
                                        {"seleccion": GOOD_SELECCION, "capacitacion": GOOD_CAPACITACION})
    flow = Approvals(store, llm, ticket_id=ticket_id)

    # Parte 3: cada departamento activo (y solo esos) entra en su pausa.
    departments = [s["department_id"] for s in store.get_ticket(ticket_id)["approval_handoff"]["sections"]]
    assert departments == ["seleccion", "capacitacion"]
    for department_id in departments:
        assert flow.start(department_id)["paused"] is True

    # Aprobaciones simuladas, en orden inverso para demostrar que son independientes.
    flow.decide("capacitacion", "approve", "Elena Vargas")
    flow.decide("seleccion", "approve", "Javier Almeida")

    ticket = store.get_ticket(ticket_id)
    assert ticket["status"] == "terminado"
    assert store.status_log == ["analizando", "analisis_completo", "generando_borrador", "en_evaluación",
                                "esperando_aprobación", "terminado"]
    # Los datos de la Parte 1 llegan intactos al documento final.
    document = ticket["final_document"]
    assert document["currency"] == ticket["metadata"]["currency"] == "EUR"
    assert ticket["metadata"]["client_name"] in document["content_markdown"]
    assert ticket["rfp_id"] == "VRG-2026-011" and "VRG-2026-011" in document["content_markdown"]
    # Trazabilidad completa y ordenada de las tres partes.
    parts = [e["part"] for e in store.list_trace(ticket_id)]
    assert parts[0] == "intake" and parts[-1] == "approval"
    assert parts == sorted(parts, key=["intake", "drafting", "approval"].index)
    agents = {e["agent"] for e in store.list_trace(ticket_id)}
    assert {"clasificador", "orquestador", "worker:seleccion", "generador:seleccion", "juez:capacitacion",
            "arbitro_reglas:seleccion", "aprobacion_humana:capacitacion",
            "sintetizador_documento_final:seleccion"} <= agents


def test_informal_rfp_from_miami_ends_in_usd_with_support_sla():
    store = StatusSpy()
    ticket_id, llm = _run_parts_1_and_2(store, INFORMAL_PDF, INFORMAL_RESPONSES, {"soporte": GOOD_SOPORTE})
    flow = Approvals(store, llm, ticket_id=ticket_id)

    pause = flow.start("soporte")["interrupt"]
    assert pause["expected_approver"] == "Roberto Díaz" and pause["conflicts"] == []
    flow.decide("soporte", "approve", "Roberto Díaz")

    document = store.get_final_document(ticket_id)
    assert document["currency"] == "USD"
    assert "SLA de respuesta de 24 horas" in document["content_markdown"]
