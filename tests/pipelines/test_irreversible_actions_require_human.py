"""
Informe NIST · Govern/Protect — las acciones irreversibles del BRIEFING §5 requieren
una persona identificada. Reúne en un solo archivo la prueba de cada una:

| BRIEFING §5                                         | Qué se comprueba aquí                                       |
|-----------------------------------------------------|-------------------------------------------------------------|
| Descartar a un candidato automáticamente            | No hay ruta ni campo de salida que lo permita (API y CV)    |
| Enviar una propuesta comercial o contrato           | La RFP se detiene en `interrupt` hasta una decisión humana; |
|                                                     | no existe ningún endpoint de envío                          |
| Marcar un ticket como resuelto                      | El agente solo tiene tools de lectura y token sin `write`;  |
|                                                     | un cliente no puede cambiar el estado (403)                 |
| Modificar masivamente candidatos en el ATS          | No hay integración ni ruta con el ATS                       |

    uv run --project services/api pytest tests/pipelines/test_irreversible_actions_require_human.py -v
"""

from __future__ import annotations

import os
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
for path in (ROOT / "services" / "api", ROOT / "packages" / "incidents-analyzer"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("REDIS_URL", "redis://localhost:1/0")
os.environ.setdefault("DATABASE_URL", "postgresql://blocked:blocked@localhost:1/blocked")

from approval_fakes import Approvals, StatusSpy, ticket_after_part2  # noqa: E402

READ_ONLY_TOOLS = {"get_ticket", "list_tickets", "query_inventory"}
FORBIDDEN_ROUTE = re.compile(r"send|email|mail|contract|candidat|discard|descart|reject|ats\b|bulk|batch", re.I)


def _mutating_routes() -> list[tuple[str, str]]:
    from app.main import app

    return [(method.upper(), path) for path, ops in app.openapi()["paths"].items()
            for method in ops if method != "get"]


# --- Descartar candidatos / ATS / enviar propuestas: no existe la ruta ---------------------------


def test_api_has_no_route_to_send_proposals_discard_candidates_or_touch_the_ats() -> None:
    routes = _mutating_routes()
    assert routes, "no se pudo leer el OpenAPI de la API"
    # `/auth/reset-password` y `/auth/forgot-password` envían el email de la propia cuenta (no a terceros).
    offenders = [(m, p) for m, p in routes if FORBIDDEN_ROUTE.search(p) and not p.startswith("/auth/")]
    assert offenders == []


def test_cv_extraction_schema_cannot_score_rank_or_discard() -> None:
    from cv_intake import CvExtraction

    fields = set(CvExtraction.model_fields)
    assert not fields & {"score", "ranking", "rank", "recommendation", "decision", "discard", "reject", "status"}
    assert CvExtraction.model_config.get("extra") == "forbid"


# --- Enviar una propuesta: la RFP se detiene hasta una decisión humana ---------------------------


def test_rfp_never_reaches_the_final_document_without_a_human_decision() -> None:
    store = StatusSpy()
    llm = ticket_after_part2(store, ["seleccion"])
    flow = Approvals(store, llm)

    status = flow.start("seleccion")

    assert status["paused"] is True and status["next"] == ["human_approval"]
    assert flow.ticket()["status"] == "esperando_aprobación"
    assert store.get_final_document("RFP-P3TEST") is None  # nada "enviable" sin aprobar

    flow.decide("seleccion", "approve", acting_as="Javier Almeida")
    assert flow.ticket()["status"] == "terminado"
    assert store.get_final_document("RFP-P3TEST") is not None
    assert flow.section("seleccion")["approver"] == "Javier Almeida"


# --- Marcar un ticket como resuelto: el agente no puede, un cliente tampoco ----------------------


class RecordingMCP:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(name)
        return {"count": 0, "tickets": [], "products": []}


def test_agent_tools_only_call_read_only_mcp_tools_with_a_read_only_token() -> None:
    from agent.tools.inventory import InventoryQuery, lookup_inventory
    from agent.tools.mcp_client import DEFAULT_SCOPES
    from agent.tools.tickets import TicketQuery, lookup_tickets

    mcp = RecordingMCP()
    lookup_tickets(TicketQuery(ticket_id="NXV-000054"), call_tool=mcp)
    lookup_tickets(TicketQuery(status="OPEN", client_company="RetailMax Corp"), call_tool=mcp)
    lookup_inventory(InventoryQuery(product="Dell Latitude"), call_tool=mcp)

    assert set(mcp.calls) <= READ_ONLY_TOOLS
    assert "update_ticket_status" not in mcp.calls
    assert all(scope.endswith(":read") for scope in DEFAULT_SCOPES.split())


@pytest.fixture()
def incidents_api():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy.pool import StaticPool
    from sqlmodel import Session, SQLModel, create_engine

    from app.routers import incidents
    from database import get_db
    from models import Incident, Role, User
    from security import get_current_user

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine, tables=[Incident.__table__])
    with Session(engine) as session:
        session.add(Incident(ticket_id="NXV-000001", date=date(2026, 11, 1), client_company="RetailMax Corp",
                             category="COMPLAINT", description="Queja por el trato recibido", agent_id="AGT-01",
                             status="OPEN"))
        session.commit()

    def _client(role: Role, company: str | None) -> TestClient:
        app = FastAPI()
        app.include_router(incidents.router)

        def _db():
            with Session(engine) as session:
                yield session

        app.dependency_overrides[get_db] = _db
        app.dependency_overrides[get_current_user] = lambda: User(
            id=f"u-{role.value}", email=f"{role.value}@x.com", hashed_password="x", role=role,
            client_company=company)
        return TestClient(app)

    return _client


def test_only_identified_support_staff_can_close_a_complaint_ticket(incidents_api) -> None:
    from models import Role

    client = incidents_api(Role.USER, "RetailMax Corp")
    assert client.patch("/api/incidents/NXV-000001/status", json={"status": "CLOSED"}).status_code == 403

    staff = incidents_api(Role.MANAGER, None)
    response = staff.patch("/api/incidents/NXV-000001/status", json={"status": "CLOSED"})
    assert response.status_code == 200 and response.json()["status"] == "CLOSED"
