"""
Tests HTTP de `/rfp` (backend existente, sin API nueva): subida asíncrona,
consulta de estado y control de acceso.

Solo se monta el router de RFPs con un store en memoria y la cola sustituida
por un doble: ni Supabase ni Redis.

    uv run --project services/api pytest tests/pipelines/test_rfp_api.py -v
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "services" / "api") not in sys.path:
    sys.path.insert(0, str(ROOT / "services" / "api"))
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("SECRET_KEY", "test-secret")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from rfp_fakes import FORMAL_PDF  # noqa: E402
from rfp_intake import InMemoryRfpStore  # noqa: E402


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    from models import Role, User
    from rfp import pipeline_bridge, router as rfp_router
    from security import get_current_user

    monkeypatch.setenv("RFP_RAW_DIR", str(tmp_path / "raw" / "rfp"))
    store = InMemoryRfpStore()
    monkeypatch.setattr(pipeline_bridge, "get_store", lambda: store)

    enqueued: list[dict] = []

    def fake_apply_async(kwargs):
        enqueued.append(kwargs)
        return SimpleNamespace(id="task-123")

    monkeypatch.setattr(rfp_router.run_rfp_intake, "apply_async", fake_apply_async)

    user = User(id="u1", email="ventas@nexova-solutions.com", hashed_password="x", role=Role.USER)
    app = FastAPI()
    app.include_router(rfp_router.router)
    app.dependency_overrides[get_current_user] = lambda: user
    return SimpleNamespace(client=TestClient(app), store=store, enqueued=enqueued, user=user,
                           raw_dir=tmp_path / "raw" / "rfp", router=rfp_router)


def _upload(client, content=None, name="vantex.pdf"):
    content = FORMAL_PDF.read_bytes() if content is None else content
    return client.post("/rfp/tickets", files={"file": (name, content, "application/pdf")})


def test_upload_returns_202_fast_and_enqueues_only_the_ticket_id(ctx):
    response = _upload(ctx.client)

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "analizando"
    assert body["task_id"] == "task-123"
    assert ctx.enqueued == [{"ticket_id": body["ticket_id"]}]  # mensaje ligero
    assert (ctx.raw_dir / f"{body['ticket_id']}.pdf").is_file()
    assert ctx.store.tickets[body["ticket_id"]]["created_by"] == "ventas@nexova-solutions.com"


def test_status_can_be_polled_after_upload(ctx):
    ticket_id = _upload(ctx.client).json()["ticket_id"]

    listing = ctx.client.get("/rfp/tickets").json()
    assert [t["ticket_id"] for t in listing] == [ticket_id]
    assert listing[0]["status"] == "analizando"

    detail = ctx.client.get(f"/rfp/tickets/{ticket_id}")
    assert detail.status_code == 200
    assert detail.json()["raw_pdf_path"] == f"data/raw/rfp/{ticket_id}.pdf"
    assert detail.json()["sections"] == []


def test_non_pdf_upload_is_rejected_without_ticket(ctx):
    response = _upload(ctx.client, b"no soy un pdf", "rfp.pdf")

    assert response.status_code == 415
    assert ctx.store.list_tickets() == []
    assert ctx.enqueued == []


def test_unknown_ticket_is_404(ctx):
    assert ctx.client.get("/rfp/tickets/RFP-NOPE").status_code == 404


def test_external_client_users_are_forbidden(ctx):
    ctx.user.client_company = "RetailMax Corp"
    assert ctx.client.get("/rfp/tickets").status_code == 403
    assert _upload(ctx.client).status_code == 403


def test_queue_down_returns_503_and_ticket_shows_the_error(ctx, monkeypatch):
    def broken(kwargs):
        raise ConnectionError("Redis caído")

    monkeypatch.setattr(ctx.router.run_rfp_intake, "apply_async", broken)
    response = _upload(ctx.client)

    assert response.status_code == 503
    ticket = ctx.store.list_tickets()[0]
    assert ticket["status"] == "analizando"
    assert "cola" in ticket["error_message"]


# --------------------------------------------------------------------------- #
# Parte 2: regeneración de borradores y encadenado automático
# --------------------------------------------------------------------------- #
@pytest.fixture
def drafting_queue(ctx, monkeypatch):
    enqueued: list[dict] = []

    def fake_apply_async(kwargs):
        enqueued.append(kwargs)
        return SimpleNamespace(id="draft-task-1")

    monkeypatch.setattr(ctx.router.generate_rfp_responses, "apply_async", fake_apply_async)
    return enqueued


def test_regenerate_drafts_returns_202_for_ticket_with_part1_handoff(ctx, drafting_queue):
    from drafting_fakes import SOPORTE_WS, make_ready_ticket

    ticket_id = make_ready_ticket(ctx.store, [SOPORTE_WS], client_hq="Miami", currency="USD")
    response = ctx.client.post(f"/rfp/tickets/{ticket_id}/drafts")

    assert response.status_code == 202
    assert response.json() == {"ticket_id": ticket_id, "status": "analisis_completo", "task_id": "draft-task-1"}
    assert drafting_queue == [{"ticket_id": ticket_id}]


def test_regenerate_drafts_without_part1_handoff_is_409(ctx, drafting_queue):
    ticket_id = _upload(ctx.client).json()["ticket_id"]  # sigue en `analizando`
    response = ctx.client.post(f"/rfp/tickets/{ticket_id}/drafts")
    assert response.status_code == 409
    assert drafting_queue == []


def test_regenerate_drafts_while_in_progress_is_409(ctx, drafting_queue):
    from drafting_fakes import SOPORTE_WS, make_ready_ticket

    ticket_id = make_ready_ticket(ctx.store, [SOPORTE_WS], client_hq="Miami", currency="USD")
    ctx.store.start_drafting(ticket_id, ["soporte"])
    assert ctx.client.post(f"/rfp/tickets/{ticket_id}/drafts").status_code == 409
    assert ctx.client.post("/rfp/tickets/RFP-NOPE/drafts").status_code == 404


def test_detail_exposes_drafts_and_evaluation(ctx):
    from drafting_fakes import GOOD_SOPORTE, PASS_RELEVANCE, SOPORTE_WS, fake_llm, make_ready_ticket, no_knowledge, section
    from rfp_drafting import run_drafting

    ticket_id = make_ready_ticket(ctx.store, [SOPORTE_WS], client_hq="Miami", currency="USD")
    run_drafting(ticket_id, store=ctx.store, knowledge_fn=no_knowledge, clients_fn=lambda: [],
                 llm=fake_llm(generate_soporte=section(GOOD_SOPORTE), relevance_soporte=PASS_RELEVANCE))

    body = ctx.client.get(f"/rfp/tickets/{ticket_id}").json()
    assert body["status"] == "en_evaluación" and body["ready_for_approval"] is True
    sec = body["sections"][0]
    assert sec["section_status"] == "evaluada_ok" and sec["iteration_count"] == 1
    assert sec["evaluation_results"]["compliance"]["pass"] is True
    assert body["approval_handoff"]["contract_version"] == "rfp_drafting.v1"


def test_part1_success_enqueues_part2_automatically(ctx, monkeypatch):
    from rfp import pipeline_bridge
    from tasks import rfp as rfp_tasks

    monkeypatch.setattr(pipeline_bridge, "run_intake",
                        lambda ticket_id, store: {"ticket_id": ticket_id, "status": "analisis_completo"})
    enqueued: list[dict] = []
    monkeypatch.setattr(rfp_tasks.generate_rfp_responses, "apply_async",
                        lambda kwargs: enqueued.append(kwargs) or SimpleNamespace(id="draft-task-2"))

    result = rfp_tasks.run_rfp_intake.apply(kwargs={"ticket_id": "RFP-AUTO"}).get()

    assert result["drafting_task_id"] == "draft-task-2"
    assert enqueued == [{"ticket_id": "RFP-AUTO"}]


# --------------------------------------------------------------------------- #
# Parte 3: aprobación humana, trace y documento final
# --------------------------------------------------------------------------- #
@pytest.fixture
def approval_ctx(ctx, monkeypatch):
    """Ticket tras la Parte 2 con la rama de `soporte` en pausa (checkpointer en memoria)."""
    from langgraph.checkpoint.memory import InMemorySaver

    from approval_fakes import Approvals, ticket_after_part2

    llm = ticket_after_part2(ctx.store, ["soporte"], hq="Miami", currency="USD")
    flow = Approvals(ctx.store, llm, checkpointer=InMemorySaver())
    flow.start("soporte")
    resumed: list[dict] = []
    monkeypatch.setattr(ctx.router.resume_approval_branch, "apply_async",
                        lambda kwargs: resumed.append(kwargs) or SimpleNamespace(id="resume-1"))
    return SimpleNamespace(flow=flow, resumed=resumed, ticket_id="RFP-P3TEST")


def test_decision_is_validated_and_resumes_only_that_branch(ctx, approval_ctx):
    response = ctx.client.post(f"/rfp/tickets/{approval_ctx.ticket_id}/approvals/soporte",
                               json={"decision": "approve", "acting_as": "Roberto Díaz"})

    assert response.status_code == 202
    assert response.json()["approval_status"] == "procesando"
    [queued] = approval_ctx.resumed
    assert queued["department_id"] == "soporte"
    assert queued["decision"]["acting_as"] == "Roberto Díaz"
    assert queued["decision"]["decided_by_user"] == "ventas@nexova-solutions.com"  # usuario real, no el cliente
    # Segunda decisión mientras la primera se procesa: 409, no se encola dos veces.
    again = ctx.client.post(f"/rfp/tickets/{approval_ctx.ticket_id}/approvals/soporte",
                            json={"decision": "approve", "acting_as": "Roberto Díaz"})
    assert again.status_code == 409 and len(approval_ctx.resumed) == 1

    # El worker reanuda con la decisión encolada → documento final accesible.
    approval_ctx.flow.decide("soporte", **{k: v for k, v in queued["decision"].items() if k != "decision"},
                             decision=queued["decision"]["decision"])
    detail = ctx.client.get(f"/rfp/tickets/{approval_ctx.ticket_id}").json()
    assert detail["status"] == "terminado"
    assert detail["sections"][0]["approval_status"] == "aprobada"
    assert detail["final_document"]["currency"] == "USD"
    final = ctx.client.get(f"/rfp/tickets/{approval_ctx.ticket_id}/final-document")
    assert final.status_code == 200 and "Roberto Díaz" in final.json()["content_markdown"]


def test_decision_on_behalf_of_someone_else_is_422(ctx, approval_ctx):
    response = ctx.client.post(f"/rfp/tickets/{approval_ctx.ticket_id}/approvals/soporte",
                               json={"decision": "approve", "acting_as": "Elena Vargas"})
    assert response.status_code == 422
    assert "Roberto Díaz" in response.json()["detail"]
    reject = ctx.client.post(f"/rfp/tickets/{approval_ctx.ticket_id}/approvals/soporte",
                             json={"decision": "reject", "acting_as": "Roberto Díaz", "comment": ""})
    assert reject.status_code == 422
    assert approval_ctx.resumed == []


def test_decision_for_inactive_department_or_unknown_ticket(ctx, approval_ctx):
    inactive = ctx.client.post(f"/rfp/tickets/{approval_ctx.ticket_id}/approvals/seleccion",
                               json={"decision": "approve", "acting_as": "Javier Almeida"})
    assert inactive.status_code == 409
    assert ctx.client.post("/rfp/tickets/RFP-NOPE/approvals/soporte",
                           json={"decision": "approve", "acting_as": "Roberto Díaz"}).status_code == 404
    assert ctx.client.get(f"/rfp/tickets/{approval_ctx.ticket_id}/final-document").status_code == 404


def test_trace_endpoint_lists_agents_in_order(ctx, approval_ctx):
    trace = ctx.client.get(f"/rfp/tickets/{approval_ctx.ticket_id}/trace")
    assert trace.status_code == 200
    events = trace.json()
    assert [e["seq"] for e in events] == sorted(e["seq"] for e in events)
    assert events[-1]["agent"] == "aprobacion_humana:soporte" and events[-1]["status"] == "interrumpido"
    assert events[-1]["thread_id"] == "rfp-RFP-P3TEST:soporte"


def test_open_approvals_enqueues_one_task_per_active_department(ctx, monkeypatch):
    from approval_fakes import ticket_after_part2
    from tasks import rfp as rfp_tasks

    ticket_after_part2(ctx.store, ["seleccion", "capacitacion"])
    started: list[dict] = []
    monkeypatch.setattr(rfp_tasks.run_approval_branch, "apply_async",
                        lambda kwargs: started.append(kwargs) or SimpleNamespace(id=f"t-{len(started)}"))

    response = ctx.client.post("/rfp/tickets/RFP-P3TEST/approvals")

    assert response.status_code == 202
    assert response.json()["department_ids"] == ["seleccion", "capacitacion"]
    assert started == [{"ticket_id": "RFP-P3TEST", "department_id": "seleccion"},
                       {"ticket_id": "RFP-P3TEST", "department_id": "capacitacion"}]
    one = ctx.client.post("/rfp/tickets/RFP-P3TEST/approvals?department_id=capacitacion")
    assert one.status_code == 202 and one.json()["department_ids"] == ["capacitacion"]


def test_part2_success_opens_the_approvals_automatically(ctx, monkeypatch):
    from rfp import pipeline_bridge
    from tasks import rfp as rfp_tasks

    monkeypatch.setattr(pipeline_bridge, "run_drafting", lambda ticket_id, store: {
        "ticket_id": ticket_id, "status": "en_evaluación", "sections": {"soporte": "evaluada_ok"}})
    started: list[dict] = []
    monkeypatch.setattr(rfp_tasks.run_approval_branch, "apply_async",
                        lambda kwargs: started.append(kwargs) or SimpleNamespace(id="approval-1"))

    result = rfp_tasks.generate_rfp_responses.apply(kwargs={"ticket_id": "RFP-AUTO"}).get()

    assert result["approval_task_ids"] == {"soporte": "approval-1"}
    assert started == [{"ticket_id": "RFP-AUTO", "department_id": "soporte"}]


def test_discarded_rfp_does_not_enqueue_part2(ctx, monkeypatch):
    from rfp import pipeline_bridge
    from tasks import rfp as rfp_tasks

    monkeypatch.setattr(pipeline_bridge, "run_intake",
                        lambda ticket_id, store: {"ticket_id": ticket_id, "status": "descartado"})
    monkeypatch.setattr(rfp_tasks.generate_rfp_responses, "apply_async",
                        lambda kwargs: pytest.fail("no debe encolar la Parte 2"))

    result = rfp_tasks.run_rfp_intake.apply(kwargs={"ticket_id": "RFP-NO"}).get()
    assert "drafting_task_id" not in result
