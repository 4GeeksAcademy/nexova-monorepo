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
