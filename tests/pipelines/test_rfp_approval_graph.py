"""
Grafo `rfp_approval` (Parte 3): interrupción y reanudación por departamento,
ramas paralelas, límite de rondas, arbitraje con árbitro fijo, `thread_id`
namespaced, trazabilidad y documento final.

    uv run --project services/api pytest tests/pipelines/test_rfp_approval_graph.py -v
"""

from __future__ import annotations

import pytest

from approval_fakes import Approvals, StatusSpy, drafting_llm, ticket_after_part2
from drafting_fakes import (
    BAD_TTC_SELECCION,
    GOOD_CAPACITACION,
    GOOD_SELECCION,
    GOOD_SOPORTE,
    Sequence,
    section,
)
from rfp_approval import InvalidDecision, branch_status, thread_id_for
from rfp_approval.approval import _config, _graph

EARLY_CAPACITACION = GOOD_CAPACITACION.replace(
    "La formación empezará cuando estén cerradas las nuevas posiciones de mandos medios.",
    "La formación empezará 2 semanas después de la firma del contrato.",
)
USD_CAPACITACION = GOOD_CAPACITACION.replace("cotización personalizada en EUR", "cotización personalizada en USD")
NO_SLA_SOPORTE = GOOD_SOPORTE.replace("Nos comprometemos a un SLA de respuesta de 24 horas para todas las consultas. ", "")


@pytest.fixture
def store():
    return StatusSpy()


def _approvals(store, departments, *, drafts=None, max_rounds=2, hq="España", currency="EUR"):
    llm = ticket_after_part2(store, departments, drafts=drafts, hq=hq, currency=currency)
    store.status_log.clear()
    return Approvals(store, llm, max_rounds=max_rounds), llm


def _approval_trace(store, ticket_id="RFP-P3TEST"):
    return [e for e in store.list_trace(ticket_id) if e["part"] == "approval"]


# --------------------------------------------------------------------------- #
# Interrupción y reanudación
# --------------------------------------------------------------------------- #
def test_branch_pauses_before_approval_and_persists_checkpoint(store):
    flow, _ = _approvals(store, ["seleccion"])

    status = flow.start("seleccion")

    assert status["paused"] is True and status["next"] == ["human_approval"]
    pause = status["interrupt"]
    assert pause["expected_approver"] == "Javier Almeida"
    assert pause["allowed_decisions"] == ["approve", "request_changes", "reject"]
    assert pause["review"]["draft_content"].startswith("## ")  # lo mínimo para decidir, sin releer la RFP
    sec = flow.section("seleccion")
    assert sec["approval_status"] == "pendiente" and sec["approval_requested_at"]
    assert flow.ticket()["status"] == "esperando_aprobación"
    # El checkpoint está guardado en el hilo del departamento.
    saved = flow.checkpointer.get_tuple({"configurable": {"thread_id": "rfp-RFP-P3TEST:seleccion"}})
    assert saved is not None and saved.pending_writes is not None


def test_reaching_the_pause_clears_stale_errors_from_failed_attempts(store):
    """Regresión (Docker): un reintento fallido dejaba "intento 3 de 4 falló" visible con la rama ya en pausa."""
    flow, _ = _approvals(store, ["seleccion"])
    store.record_error("RFP-P3TEST", "La apertura de la aprobación de 'seleccion': intento 1 de 4 falló")

    flow.start("seleccion")
    assert flow.ticket()["error_message"] is None

    store.record_error("RFP-P3TEST", "La reanudación falló")
    flow.decide("seleccion", "approve", "Javier Almeida")
    assert flow.ticket()["error_message"] is None


def test_resuming_keeps_the_original_pause_start_for_the_approval_time_kpi(store):
    """Regresión (Docker): al reanudar desde `procesando` se reiniciaba `approval_requested_at`."""
    flow, _ = _approvals(store, ["seleccion"])
    flow.start("seleccion")
    paused_at = flow.section("seleccion")["approval_requested_at"]
    store.set_approval_status("RFP-P3TEST", "seleccion", "procesando")  # lo que hace la API al recibir la decisión

    flow.decide("seleccion", "approve", "Javier Almeida")

    sec = flow.section("seleccion")
    assert sec["approval_requested_at"] == paused_at and sec["approved_at"] > paused_at


def test_resume_continues_from_the_interrupt_without_restarting(store):
    flow, llm = _approvals(store, ["seleccion"])
    flow.start("seleccion")
    llm_calls_before = len(llm.calls)

    status = flow.decide("seleccion", "approve", "Javier Almeida")

    assert status["paused"] is False and status["outcome"] == "aprobada"
    nodes = [(e["node"], e["status"]) for e in _approval_trace(store)]
    # load_section y arbitrate corrieron UNA vez: la reanudación entra directo en human_approval.
    assert nodes == [
        ("load_section", "ok"), ("arbitrate", "ok"), ("human_approval", "interrumpido"),
        ("human_approval", "ok"), ("apply_decision", "ok"), ("synthesize_final_document", "ok"),
    ]
    assert len(llm.calls) == llm_calls_before  # aprobar no regenera nada
    sec = flow.section("seleccion")
    assert sec["approval_status"] == "aprobada" and sec["approver"] == "Javier Almeida"
    assert sec["decided_by_user"] == "ventas@nexova.test" and sec["approved_at"]


def test_invalid_human_response_is_rejected_before_entering_the_graph(store):
    flow, _ = _approvals(store, ["seleccion"])
    flow.start("seleccion")

    with pytest.raises(InvalidDecision, match="Javier Almeida"):
        flow.decide("seleccion", "approve", "Elena Vargas")          # no es su sección
    with pytest.raises(InvalidDecision, match="motivo"):
        flow.decide("seleccion", "reject", "Javier Almeida")         # rechazo sin comentario
    with pytest.raises(InvalidDecision):
        flow.decide("seleccion", "maybe", "Javier Almeida")          # decisión desconocida
    with pytest.raises(InvalidDecision):
        flow.decide("seleccion", "approve", "Javier Almeida", unexpected="x")  # campos extra

    assert flow.section("seleccion")["approval_status"] == "pendiente"
    assert branch_status(*_branch(flow, "seleccion"))["paused"] is True  # la pausa sigue intacta


def _branch(flow, department_id, ticket_id="RFP-P3TEST"):
    graph, rounds = _graph(flow.store, flow.checkpointer, llm=None, knowledge_fn=None, clients_fn=None,
                           max_rounds=None)
    return graph, _config(ticket_id, department_id, rounds)


# --------------------------------------------------------------------------- #
# Ramas paralelas: aprobar B mientras A sigue interrumpido
# --------------------------------------------------------------------------- #
def test_department_b_is_approved_while_department_a_stays_interrupted(store):
    flow, _ = _approvals(store, ["seleccion", "capacitacion"])
    flow.start("seleccion")
    flow.start("capacitacion")

    flow.decide("capacitacion", "approve", "Elena Vargas")  # B avanza…

    a = branch_status(*_branch(flow, "seleccion"))
    assert a["paused"] is True and a["next"] == ["human_approval"]  # …A sigue en su pausa
    assert flow.section("seleccion")["approval_status"] == "pendiente"
    assert flow.section("capacitacion")["approval_status"] == "aprobada"
    assert flow.ticket()["status"] == "esperando_aprobación"
    assert flow.ticket()["final_document"] is None

    # El trace lo demuestra: B termina entre la pausa de A y su reanudación.
    trace = [(e["agent"], e["status"]) for e in _approval_trace(store)]
    a_paused = trace.index(("aprobacion_humana:seleccion", "interrumpido"))
    b_done = trace.index(("sintetizador_documento_final:capacitacion", "ok"))
    assert a_paused < b_done
    assert ("aprobacion_humana:seleccion", "ok") not in trace

    flow.decide("seleccion", "approve", "Javier Almeida")
    assert flow.ticket()["status"] == "terminado"


def test_final_document_is_generated_only_when_all_active_sections_are_approved(store):
    flow, _ = _approvals(store, ["seleccion", "capacitacion"])
    flow.start("seleccion")
    flow.start("capacitacion")
    flow.decide("seleccion", "approve", "Javier Almeida")
    assert store.get_final_document("RFP-P3TEST") is None

    status = flow.decide("capacitacion", "approve", "Elena Vargas")

    assert status["final_document"] is True
    document = store.get_final_document("RFP-P3TEST")
    assert document["currency"] == "EUR"
    assert [s["department_id"] for s in document["sections"]] == ["seleccion", "capacitacion"]
    assert {a["approver"] for a in document["approvals"]} == {"Javier Almeida", "Elena Vargas"}
    content = document["content_markdown"]
    assert "Vantex Retail Group" in content and "garantía de satisfacción estándar de Nexova a 90 días" in content
    assert GOOD_SELECCION.split("\n\n", 1)[1][:60] in content  # el texto aprobado, sin reescribir
    assert store.status_log[-2:] == ["esperando_aprobación", "terminado"]


# --------------------------------------------------------------------------- #
# Rechazo / cambios y límite de iteraciones
# --------------------------------------------------------------------------- #
def test_request_changes_returns_to_the_part2_generator_with_the_comment(store):
    revised = GOOD_SELECCION.replace("unas 60 horas", "unas 55 horas")
    flow, llm = _approvals(store, ["seleccion"],
                           drafts={"seleccion": Sequence(section(GOOD_SELECCION), section(revised))})
    flow.start("seleccion")

    status = flow.decide("seleccion", "request_changes", "Javier Almeida", "Ajusta las horas de consultoría a 55.")

    assert status["paused"] is True and status["approval_round"] == 1
    prompt = [c for c in llm.calls if c["task"] == "generate:seleccion"][-1]["user"]
    assert "Revisión pedida en la aprobación humana por Javier Almeida" in prompt
    assert "Ajusta las horas de consultoría a 55." in prompt
    sec = flow.section("seleccion")
    assert "55 horas" in sec["draft_content"] and sec["iteration_count"] == 2
    assert sec["approval_status"] == "pendiente" and sec["approval_round"] == 1
    assert [h["decision"] for h in sec["approval_history"]] == ["request_changes"]


def test_iteration_limit_is_enforced_and_then_only_approve_or_reject(store):
    flow, _ = _approvals(store, ["seleccion"], max_rounds=1)
    flow.start("seleccion")
    flow.decide("seleccion", "request_changes", "Javier Almeida", "Más detalle del proceso.")

    pause = branch_status(*_branch(flow, "seleccion"))["interrupt"]
    assert pause["approval_round"] == 1 and pause["max_rounds"] == 1
    assert pause["allowed_decisions"] == ["approve", "reject"]  # límite agotado
    with pytest.raises(InvalidDecision, match="no está permitida"):
        flow.decide("seleccion", "request_changes", "Javier Almeida", "Otra vuelta más.")

    status = flow.decide("seleccion", "reject", "Javier Almeida", "No encaja con el cliente.")

    assert status["outcome"] == "rechazada" and status["paused"] is False
    assert flow.section("seleccion")["approval_status"] == "rechazada"
    assert flow.ticket()["status"] == "esperando_aprobación"   # no hay documento final con una sección rechazada
    assert store.get_final_document("RFP-P3TEST") is None


def test_rejected_section_can_be_reopened_as_a_new_run(store):
    flow, _ = _approvals(store, ["seleccion"], max_rounds=1)
    flow.start("seleccion")
    flow.decide("seleccion", "request_changes", "Javier Almeida", "Más detalle del proceso.")
    flow.decide("seleccion", "reject", "Javier Almeida", "No encaja con el cliente.")

    status = flow.start("seleccion")  # "Reabrir aprobación"

    assert status["paused"] is True and status["approval_round"] == 0
    sec = flow.section("seleccion")
    assert sec["approval_status"] == "pendiente" and sec["approval_history"] == []


# --------------------------------------------------------------------------- #
# Arbitraje (BRIEFING §7) con árbitro fijo, sin LLM
# --------------------------------------------------------------------------- #
def test_currency_mismatch_forces_changes_by_marcos_then_returns_to_human(store):
    flow, llm = _approvals(store, ["capacitacion"],
                           drafts={"capacitacion": Sequence(section(USD_CAPACITACION), section(GOOD_CAPACITACION))})

    status = flow.start("capacitacion")

    # El árbitro resolvió por regla (sin humano ni LLM juez) y la sección volvió a su generador.
    assert status["paused"] is True and status["approval_round"] == 1
    assert status["interrupt"]["expected_approver"] == "Elena Vargas"
    arbitration = flow.ticket()["arbitrations"]
    assert [(a["trigger_id"], a["arbiter"], a["action"]) for a in arbitration] == [
        ("currency-mismatch", "Marcos Ibáñez", "request_changes")]
    history = flow.section("capacitacion")["approval_history"]
    assert history[0]["by_rule"] == ["currency-mismatch"] and history[0]["acting_as"] == "Marcos Ibáñez"
    prompt = [c for c in llm.calls if c["task"] == "generate:capacitacion"][-1]["user"]
    assert "[currency-mismatch]" in prompt and "EUR" in prompt
    assert {t for t in llm.tasks() if not t.startswith(("generate:", "relevance:"))} == set()  # sin LLM árbitro


def test_currency_mismatch_unresolved_after_limit_is_rejected(store):
    flow, _ = _approvals(store, ["capacitacion"], max_rounds=1, drafts={"capacitacion": USD_CAPACITACION})

    status = flow.start("capacitacion")

    assert status["outcome"] == "rechazada" and status["paused"] is False
    actions = [(a["approval_round"], a["action"]) for a in flow.ticket()["arbitrations"]]
    assert actions == [(0, "request_changes"), (1, "reject")]
    assert flow.section("capacitacion")["approval_status"] == "rechazada"


def test_support_sla_missing_is_blocked_by_roberto(store):
    flow, _ = _approvals(store, ["soporte"], max_rounds=1, hq="Miami", currency="USD",
                         drafts={"soporte": NO_SLA_SOPORTE})

    status = flow.start("soporte")

    arbitration = flow.ticket()["arbitrations"]
    assert {(a["trigger_id"], a["arbiter"]) for a in arbitration} == {("support-sla-missing", "Roberto Díaz")}
    assert status["outcome"] == "rechazada"  # el SLA 24h nunca se hizo explícito: aprobación bloqueada


def test_ttc_vs_training_window_sequences_and_escalates_to_marcos(store):
    flow, _ = _approvals(store, ["seleccion", "capacitacion"], max_rounds=1,
                         drafts={"seleccion": GOOD_SELECCION, "capacitacion": EARLY_CAPACITACION})
    flow.start("seleccion")

    status = flow.start("capacitacion")

    # Ronda 0: Marcos fuerza request_changes; sigue sin secuenciar → escala a Marcos como árbitro humano.
    pause = status["interrupt"]
    assert pause["expected_approver"] == "Marcos Ibáñez" and pause["escalated"] is True
    assert pause["allowed_decisions"] == ["approve", "reject"]
    assert pause["conflicts"][0]["trigger_id"] == "ttc-vs-training-window"
    assert pause["conflicts"][0]["evidence"]["training_window"]["start_business_days"] == 10
    with pytest.raises(InvalidDecision, match="Marcos Ibáñez"):
        flow.decide("capacitacion", "approve", "Elena Vargas")
    # La sección de selección (cierre realista) no tiene conflicto: la aprueba su responsable.
    assert branch_status(*_branch(flow, "seleccion"))["interrupt"]["expected_approver"] == "Javier Almeida"

    flow.decide("capacitacion", "approve", "Marcos Ibáñez", "Aceptado: el inicio se confirmará tras el cierre.")
    flow.decide("seleccion", "approve", "Javier Almeida")
    assert flow.section("capacitacion")["approver"] == "Marcos Ibáñez"
    assert flow.ticket()["status"] == "terminado"


def test_unrealistic_time_to_close_triggers_changes_in_both_sections(store):
    flow, _ = _approvals(store, ["seleccion", "capacitacion"],
                         drafts={"seleccion": Sequence(section(BAD_TTC_SELECCION), section(GOOD_SELECCION)),
                                 "capacitacion": GOOD_CAPACITACION})

    flow.start("seleccion")

    arbitration = flow.ticket()["arbitrations"]
    assert [(a["department_id"], a["trigger_id"]) for a in arbitration] == [("seleccion", "ttc-vs-training-window")]
    sec = flow.section("seleccion")
    assert "30 y 40 días hábiles" in sec["draft_content"]  # reescrito a un cierre realista
    assert sec["approval_status"] == "pendiente" and sec["approval_round"] == 1


# --------------------------------------------------------------------------- #
# Identidad del checkpointer y trazabilidad
# --------------------------------------------------------------------------- #
def test_thread_ids_are_namespaced_per_ticket_and_department(store):
    assert thread_id_for("RFP-AAA", "seleccion") == "rfp-RFP-AAA:seleccion"
    llm = drafting_llm({"seleccion": GOOD_SELECCION})
    ticket_after_part2(store, ["seleccion"], ticket_id="RFP-AAA")
    ticket_after_part2(store, ["seleccion"], ticket_id="RFP-BBB")
    flow = Approvals(store, llm)
    flow.start("seleccion", ticket_id="RFP-AAA")
    flow.start("seleccion", ticket_id="RFP-BBB")

    flow.decide("seleccion", "approve", "Javier Almeida", ticket_id="RFP-AAA")

    assert store.get_ticket("RFP-AAA")["status"] == "terminado"
    assert store.get_ticket("RFP-BBB")["status"] == "esperando_aprobación"  # su checkpoint no se tocó
    assert branch_status(*_branch(flow, "seleccion", "RFP-BBB"))["paused"] is True
    threads = {c.config["configurable"]["thread_id"] for c in flow.checkpointer.list(None)}
    assert {"rfp-RFP-AAA:seleccion", "rfp-RFP-BBB:seleccion"} <= threads


def test_checkpoint_survives_a_new_connection(store, tmp_path, monkeypatch):
    """La pausa se guarda en un checkpointer durable: otra conexión (otro proceso: API/worker) la reanuda."""
    from rfp_approval import open_checkpointer, resume_approval, start_approval
    from drafting_fakes import no_knowledge

    monkeypatch.setenv("RFP_CHECKPOINTER", "sqlite")
    monkeypatch.setenv("RFP_CHECKPOINT_PATH", str(tmp_path / "checkpoints.sqlite"))
    llm = ticket_after_part2(store, ["seleccion"])
    kwargs = dict(store=store, llm=llm, knowledge_fn=no_knowledge, clients_fn=lambda: [], max_rounds=2)

    with open_checkpointer() as checkpointer:  # "worker" que abre la rama
        assert start_approval("RFP-P3TEST", "seleccion", checkpointer=checkpointer, **kwargs)["paused"]
    with open_checkpointer() as checkpointer:  # conexión nueva: la pausa sigue ahí
        status = resume_approval("RFP-P3TEST", "seleccion",
                                 {"decision": "approve", "acting_as": "Javier Almeida"},
                                 checkpointer=checkpointer, **kwargs)

    assert status["outcome"] == "aprobada"
    assert store.get_ticket("RFP-P3TEST")["status"] == "terminado"


def test_every_node_execution_is_traced_with_agent_input_output_and_timestamp(store):
    flow, _ = _approvals(store, ["seleccion"])
    flow.start("seleccion")
    flow.decide("seleccion", "approve", "Javier Almeida")

    trace = store.list_trace("RFP-P3TEST")
    assert {e["part"] for e in trace} == {"drafting", "approval"}  # la Parte 2 también deja su rastro
    for event in trace:
        assert event["agent"] and event["node"] and event["started_at"] and event["finished_at"]
        assert isinstance(event["input"], dict) and isinstance(event["output"], dict)
    assert [e["seq"] for e in trace] == sorted(e["seq"] for e in trace)
    approval = [e for e in trace if e["part"] == "approval"]
    assert {e["thread_id"] for e in approval} == {"rfp-RFP-P3TEST:seleccion"}
    decided = next(e for e in approval if e["node"] == "human_approval" and e["status"] == "ok")
    assert decided["output"]["decision"]["acting_as"] == "Javier Almeida"
    # También dentro del estado del grafo (viaja en el checkpoint).
    graph, config = _branch(flow, "seleccion")
    in_state = graph.get_state(config).values["trace"]
    assert [t["node"] for t in in_state][-3:] == ["human_approval", "apply_decision", "synthesize_final_document"]
