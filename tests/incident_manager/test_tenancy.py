"""
Tenancy del gestor de incidencias (auditoría OWASP C2, la misma del agente).

Personal interno ve todo; un cliente solo su empresa (filtro forzado, ajenos →
404, alta con su empresa); `user` sin empresa → 403; cambiar el estado, solo
personal interno. Los endpoints del analizador CSV (`/analyze`, `/results/export`)
siguen siendo solo staff.
"""

from __future__ import annotations

import pytest

BASE = "/api/incidents"
PAYLOAD = {
    "title": "VPN caída",
    "description": "No funciona el acceso VPN",
    "category": "technical_failure",
    "origin": "customer",
    "branch": "central",
}


@pytest.fixture()
def two_companies(make_client):
    staff = make_client(role="manager")
    ids = {}
    for company in ("Acme Corp", "Globex SA"):
        ids[company] = staff.post(BASE, json={**PAYLOAD, "client_company": company}).json()["id"]
    ids["none"] = staff.post(BASE, json=PAYLOAD).json()["id"]  # sin empresa
    return ids


def test_staff_can_set_company_and_filter_by_it(make_client, two_companies):
    staff = make_client(role="admin")
    assert len(staff.get(BASE).json()) == 3
    found = staff.get(BASE, params={"client_company": "acme"}).json()
    assert [r["client_company"] for r in found] == ["Acme Corp"]


def test_client_only_lists_own_company_even_if_asking_for_another(make_client, two_companies):
    acme = make_client(role="user", company="Acme Corp")
    assert {r["client_company"] for r in acme.get(BASE).json()} == {"Acme Corp"}
    forced = acme.get(BASE, params={"client_company": "Globex SA"}).json()
    assert {r["client_company"] for r in forced} == {"Acme Corp"}


def test_client_cannot_read_foreign_or_unassigned_incident(make_client, two_companies):
    acme = make_client(role="user", company="Acme Corp")
    assert acme.get(f"{BASE}/{two_companies['Acme Corp']}").status_code == 200
    assert acme.get(f"{BASE}/{two_companies['Globex SA']}").status_code == 404  # no revela que existe
    assert acme.get(f"{BASE}/{two_companies['none']}").status_code == 404


def test_client_cannot_change_status(make_client, two_companies):
    acme = make_client(role="user", company="Acme Corp")
    for incident_id in (two_companies["Acme Corp"], two_companies["Globex SA"]):
        res = acme.patch(f"{BASE}/{incident_id}/status", json={"status": "in_progress"})
        assert res.status_code == 403


def test_client_creates_only_for_own_company(make_client, two_companies):
    acme = make_client(role="user", company="Acme Corp")
    res = acme.post(BASE, json={**PAYLOAD, "client_company": "Globex SA"})
    assert res.status_code == 201 and res.json()["client_company"] == "Acme Corp"


def test_summary_is_scoped_to_the_clients_company(make_client, two_companies):
    assert make_client(role="manager").get(f"{BASE}/summary").json()["total"] == 3
    assert make_client(role="user", company="Acme Corp").get(f"{BASE}/summary").json()["total"] == 1
    assert make_client(role="user", company="Initech").get(f"{BASE}/summary").json()["total"] == 0


def test_user_without_company_gets_403(make_client, two_companies):
    nobody = make_client(role="user")
    assert nobody.get(BASE).status_code == 403
    assert nobody.get(f"{BASE}/summary").status_code == 403
    assert nobody.get(f"{BASE}/{two_companies['Acme Corp']}").status_code == 403


def test_csv_analysis_and_export_are_staff_only(engine):
    from app.routers import incidents as analyzer_router
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from models import Role, User
    from security import get_current_user

    def _client(role: Role, company: str | None) -> TestClient:
        app = FastAPI()
        app.include_router(analyzer_router.router)
        app.dependency_overrides[get_current_user] = lambda: User(
            id="u", email="x@example.com", hashed_password="x", role=role, client_company=company
        )
        return TestClient(app)

    client = _client(Role.USER, "Acme Corp")
    csv = {"file": ("t.csv", b"a,b\n1,2\n", "text/csv")}
    assert client.post("/api/incidents/analyze", files=csv).status_code == 403
    assert client.get("/api/incidents/results/export").status_code == 403
    assert _client(Role.MANAGER, None).get("/api/incidents/results/export").status_code == 404


def test_analyzer_and_manager_share_the_prefix_without_clashing(engine):
    """`/analyze` y `/results/export` (CSV) y el gestor conviven bajo `/api/incidents`."""
    from app.routers import incidents as analyzer_router
    from database import get_db
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from incident_manager.router import router as manager_router
    from models import User
    from security import get_current_user
    from sqlmodel import Session

    def _db():
        with Session(engine) as session:
            yield session

    app = FastAPI()
    app.include_router(manager_router)
    app.include_router(analyzer_router.router)
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: User(
        id="m", email="m@example.com", hashed_password="x", role="manager"
    )
    client = TestClient(app)

    assert client.get("/api/incidents/summary").json()["total"] == 0  # gestor, no «/{id}»
    assert client.get("/api/incidents").json() == []
    assert client.get("/api/incidents/results/export").status_code == 404  # analizador: sin análisis previo
    csv = {"file": ("t.csv", b"ticket_id,date\n", "text/csv")}
    assert client.post("/api/incidents/analyze", files=csv).status_code == 400  # analizador: CSV inválido
