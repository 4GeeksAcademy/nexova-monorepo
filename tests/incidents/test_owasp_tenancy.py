"""
Auditoría OWASP — hallazgo C2 (A01 Broken Access Control / IDOR) en
`/api/incidents`.

Antes: todas las rutas solo exigían un token válido; un usuario de la empresa A
listaba, leía y cambiaba el estado de tickets de la empresa B (fuga de datos
entre clientes de outsourcing, BRIEFING §3), y `client_company` era un filtro
que elegía quien llamaba.

Después: misma tenancy que el agente (`agent/guardrails/tenancy.py`): personal
interno ve todo; un cliente solo su empresa (filtro forzado, ajenos → 404);
`user` sin empresa → 403; cambios de estado y análisis CSV solo personal interno.

    uv run --project services/api pytest tests/incidents/test_owasp_tenancy.py -v
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timezone

import pytest


@pytest.fixture()
def two_companies(engine):
    from models import Incident
    from sqlmodel import Session

    rows = [("NXV-000001", "Acme Corp"), ("NXV-000002", "Globex SA")]
    with Session(engine) as session:
        for ticket_id, company in rows:
            session.add(Incident(
                ticket_id=ticket_id, date=date(2026, 11, 1), client_company=company,
                category="TECHNICAL", description="VPN caída", agent_id="AG-01", status="OPEN",
                updated_at=datetime(2026, 11, 1, tzinfo=UTC),
            ))
        session.commit()


@pytest.fixture()
def client_as(engine):
    from app.routers import incidents
    from database import get_db
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from models import Role, User
    from security import get_current_user
    from sqlmodel import Session

    def _db():
        with Session(engine) as session:
            yield session

    def _make(role: str, company: str | None = None) -> TestClient:
        app = FastAPI()
        app.include_router(incidents.router)
        app.dependency_overrides[get_db] = _db
        user = User(id=f"u-{role}-{company}", email="x@example.com", hashed_password="x",
                    role=Role(role), client_company=company)
        app.dependency_overrides[get_current_user] = lambda: user
        return TestClient(app)

    return _make


def test_client_only_lists_own_company_even_if_asking_for_another(client_as, two_companies):
    acme = client_as("user", "Acme Corp")
    assert {t["client_company"] for t in acme.get("/api/incidents").json()} == {"Acme Corp"}
    forced = acme.get("/api/incidents", params={"client_company": "Globex SA"}).json()
    assert all(t["client_company"] == "Acme Corp" for t in forced)


def test_client_cannot_read_foreign_ticket_by_id(client_as, two_companies):
    acme = client_as("user", "Acme Corp")
    assert acme.get("/api/incidents/NXV-000001").status_code == 200
    assert acme.get("/api/incidents/NXV-000002").status_code == 404  # no revela que existe


def test_client_cannot_change_ticket_status(client_as, two_companies):
    acme = client_as("user", "Acme Corp")
    for ticket_id in ("NXV-000001", "NXV-000002"):
        r = acme.patch(f"/api/incidents/{ticket_id}/status", json={"status": "CLOSED"})
        assert r.status_code == 403


def test_client_creates_tickets_only_for_own_company(client_as, two_companies):
    acme = client_as("user", "Acme Corp")
    r = acme.post("/api/incidents", json={"client_company": "Globex SA", "category": "TECHNICAL",
                                          "description": "Ticket inyectado", "agent_id": "AG-01",
                                          "date": "2026-11-02"})
    assert r.status_code == 201 and r.json()["client_company"] == "Acme Corp"


def test_user_without_company_gets_403(client_as, two_companies):
    nobody = client_as("user")
    assert nobody.get("/api/incidents").status_code == 403
    assert nobody.get("/api/incidents/NXV-000001").status_code == 403


def test_csv_analysis_and_export_are_staff_only(client_as):
    acme = client_as("user", "Acme Corp")
    assert acme.post("/api/incidents/analyze",
                     files={"file": ("t.csv", b"a,b\n1,2\n", "text/csv")}).status_code == 403
    assert acme.get("/api/incidents/results/export").status_code == 403


@pytest.mark.parametrize("role", ["admin", "manager"])
def test_staff_sees_and_manages_everything(client_as, two_companies, role):
    staff = client_as(role)
    assert len(staff.get("/api/incidents").json()) == 2
    assert staff.get("/api/incidents/NXV-000002").status_code == 200
    assert staff.patch("/api/incidents/NXV-000002/status", json={"status": "CLOSED"}).status_code == 200
