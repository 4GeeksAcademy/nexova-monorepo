"""Matriz de acceso del BRIEFING: rol × departamento × recurso, con llamadas directas a la API."""

from __future__ import annotations

import pytest
from conftest import ELENA, JAVIER, MEGAN, PATRICIA, SERGIO

ADMIN_ONLY = [
    ("get", "/roles"),
    ("post", "/roles"),
    ("get", "/roles/employee"),
    ("put", "/roles/employee"),
    ("get", "/departments"),
    ("post", "/departments"),
    ("get", "/departments/sales"),
    ("put", "/departments/sales"),
    ("get", "/users"),
    ("post", "/users"),
]


@pytest.mark.parametrize("who", [ELENA, PATRICIA, JAVIER, MEGAN])
@pytest.mark.parametrize("method,path", ADMIN_ONLY)
def test_admin_only_endpoints_reject_every_other_role_with_403(api, as_user, who, method, path):
    response = getattr(api, method)(path, headers=as_user(who), **({"json": {}} if method in {"post", "put"} else {}))

    assert response.status_code == 403
    assert response.json()["detail"]


@pytest.mark.parametrize("method,path", ADMIN_ONLY)
def test_admin_only_endpoints_require_authentication(api, method, path):
    response = getattr(api, method)(path, **({"json": {}} if method in {"post", "put"} else {}))
    assert response.status_code == 401


def test_admin_lists_roles_and_departments_from_the_briefing(api, as_user):
    sergio = as_user(SERGIO)

    roles = api.get("/roles", headers=sergio).json()
    assert [(r["code"], r["rank"]) for r in roles] == [("employee", 1), ("supervisor", 2), ("admin", 3)]
    departments = {d["code"] for d in api.get("/departments", headers=sergio).json()}
    assert departments == {
        "talent_selection", "corporate_training", "customer_support", "sales",
        "marketing", "human_resources", "technology",
    }


# --- internal-reports: rol primero, departamento después ----------------------------------


@pytest.mark.parametrize(
    "who,department,expected_titles",
    [
        (JAVIER, "talent_selection", {"Search #441 — screening bottleneck"}),
        (MEGAN, "sales", {"HubSpot hygiene — stale opportunities"}),
        (ELENA, "corporate_training", set()),  # alcance, no denegación: su departamento no tiene informes
    ],
)
def test_collection_is_scoped_to_the_callers_department(api, as_user, who, department, expected_titles):
    response = api.get("/internal-reports", headers=as_user(who))

    assert response.status_code == 200
    assert {r["title"] for r in response.json()} == expected_titles
    assert {r["department_id"] for r in response.json()} <= {department}


def test_admin_sees_every_department(api, as_user):
    reports = api.get("/internal-reports", headers=as_user(SERGIO)).json()
    assert {r["department_id"] for r in reports} == {"talent_selection", "sales"}


@pytest.mark.parametrize("who", [ELENA, PATRICIA, JAVIER, MEGAN])
@pytest.mark.parametrize("other", ["talent_selection", "sales"])
def test_get_by_id_of_another_department_is_403_for_non_admins(api, as_user, report_id, who, other):
    own = {ELENA: "corporate_training", PATRICIA: "human_resources", JAVIER: "talent_selection", MEGAN: "sales"}[who]
    response = api.get(f"/internal-reports/{report_id(other)}", headers=as_user(who))

    assert response.status_code == (200 if own == other else 403)


def test_admin_reads_any_department_by_id(api, as_user, report_id):
    for department in ("talent_selection", "sales"):
        assert api.get(f"/internal-reports/{report_id(department)}", headers=as_user(SERGIO)).status_code == 200


def test_employee_creates_only_in_their_own_department(api, as_user):
    elena = as_user(ELENA)

    own = api.post("/internal-reports", json={"title": "T", "body": "B"}, headers=elena)
    assert own.status_code == 201
    assert own.json()["department_id"] == "corporate_training"

    foreign = api.post("/internal-reports", json={"title": "T", "body": "B", "department_id": "sales"}, headers=elena)
    assert foreign.status_code == 403


def test_supervisor_updates_own_department_but_not_another(api, as_user, report_id):
    javier = as_user(JAVIER)

    ok = api.put(f"/internal-reports/{report_id('talent_selection')}", json={"title": "Nuevo"}, headers=javier)
    assert ok.status_code == 200 and ok.json()["title"] == "Nuevo"

    other = api.put(f"/internal-reports/{report_id('sales')}", json={"title": "Hack"}, headers=javier)
    assert other.status_code == 403


@pytest.mark.parametrize("who", [ELENA, JAVIER, MEGAN])
def test_only_admin_deletes_reports(api, as_user, report_id, who):
    target = report_id("talent_selection")

    assert api.delete(f"/internal-reports/{target}", headers=as_user(who)).status_code == 403
    assert api.delete(f"/internal-reports/{target}", headers=as_user(SERGIO)).status_code == 200
    assert api.get(f"/internal-reports/{target}", headers=as_user(SERGIO)).status_code == 404


def test_external_account_has_no_internal_access(api):
    api.post("/auth/register", json={"email": "cliente@acme.com", "password": "Sup3r-secreta!"})
    token = api.post("/auth/login", json={"email": "cliente@acme.com", "password": "Sup3r-secreta!"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    assert api.get("/internal-reports", headers=headers).status_code == 403
    assert api.post("/internal-reports", json={"title": "T", "body": "B"}, headers=headers).status_code == 403


def test_user_without_department_gets_403_not_an_empty_list(api, as_user, user_id):
    api.patch(f"/users/{user_id(ELENA)}/assignment", json={"department_id": None}, headers=as_user(SERGIO))

    response = api.get("/internal-reports", headers=as_user(ELENA))

    assert response.status_code == 403
    assert "departamento" in response.json()["detail"]


# --- endpoints de negocio existentes (proveedores): crear / actualizar / eliminar -----------------

SUPPLIER = {
    "name": "Proveedor Demo", "country": "Spain", "categories": ["job_boards"],
    "monthly_rate": 100.0, "currency": "EUR", "status": "active",
}


def test_supplier_actions_follow_the_role_ladder(api, as_user):
    created = api.post("/suppliers", json=SUPPLIER, headers=as_user(ELENA))  # employee: crear
    assert created.status_code == 201
    supplier_id = created.json()["id"]

    rate = {"monthly_rate": 200.0}
    assert api.patch(f"/suppliers/{supplier_id}/rate", json=rate, headers=as_user(ELENA)).status_code == 403
    assert api.patch(f"/suppliers/{supplier_id}/rate", json=rate, headers=as_user(JAVIER)).status_code == 200
    assert api.delete(f"/suppliers/{supplier_id}", headers=as_user(JAVIER)).status_code == 403
    assert api.delete(f"/suppliers/{supplier_id}", headers=as_user(SERGIO)).status_code == 200
