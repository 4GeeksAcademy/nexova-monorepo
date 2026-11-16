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


def test_discarded_rfp_does_not_enqueue_part2(ctx, monkeypatch):
    from rfp import pipeline_bridge
    from tasks import rfp as rfp_tasks

    monkeypatch.setattr(pipeline_bridge, "run_intake",
                        lambda ticket_id, store: {"ticket_id": ticket_id, "status": "descartado"})
    monkeypatch.setattr(rfp_tasks.generate_rfp_responses, "apply_async",
                        lambda kwargs: pytest.fail("no debe encolar la Parte 2"))

    result = rfp_tasks.run_rfp_intake.apply(kwargs={"ticket_id": "RFP-NO"}).get()
    assert "drafting_task_id" not in result
