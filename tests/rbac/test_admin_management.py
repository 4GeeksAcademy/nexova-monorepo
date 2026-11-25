"""Administración de usuarios, roles y departamentos; semillas y migración."""

from __future__ import annotations

import json

import pytest
from conftest import ELENA, JAVIER, MEGAN, PASSWORD, SERGIO


def test_me_returns_role_and_department(api, as_user):
    body = api.get("/auth/me", headers=as_user(JAVIER)).json()

    assert body["role"] == body["role_id"] == "supervisor"
    assert body["rank"] == 2
    assert body["department_id"] == "talent_selection"
    assert body["department_name"] == "Talent Selection Operations"
    assert body["profile"]["name"] == "Javier Almeida"


def test_admin_keeps_a_department(api, as_user):
    body = api.get("/auth/me", headers=as_user(SERGIO)).json()
    assert (body["role"], body["department_id"]) == ("admin", "technology")


def test_post_users_is_admin_only_and_assigns_both_axes(api, as_user):
    payload = {"email": "nuevo@nexova.example", "password": PASSWORD, "role_id": "employee", "department_id": "marketing"}

    assert api.post("/users", json=payload, headers=as_user(JAVIER)).status_code == 403
    created = api.post("/users", json=payload, headers=as_user(SERGIO))
    assert created.status_code == 201
    assert (created.json()["role_id"], created.json()["department_id"]) == ("employee", "marketing")


def test_post_users_rejects_unknown_role_or_department(api, as_user):
    sergio = as_user(SERGIO)
    base = {"email": "x@nexova.example", "password": PASSWORD}

    assert api.post("/users", json={**base, "role_id": "dios"}, headers=sergio).status_code == 422
    assert api.post("/users", json={**base, "role_id": "employee", "department_id": "narnia"}, headers=sergio).status_code == 422


def test_public_registration_cannot_escalate(api):
    response = api.post(
        "/auth/register", json={"email": "x@x.com", "password": PASSWORD, "role": "admin", "department_id": "sales"}
    )

    assert response.status_code == 201
    assert response.json()["role"] == "user"
    token = api.post("/auth/login", json={"email": "x@x.com", "password": PASSWORD}).json()["access_token"]
    me = api.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()
    assert (me["role"], me["rank"], me["department_id"]) == ("user", 0, None)


def test_assignment_validates_input(api, as_user, user_id):
    sergio = as_user(SERGIO)
    url = f"/users/{user_id(ELENA)}/assignment"

    assert api.patch(url, json={}, headers=sergio).status_code == 422
    assert api.patch(url, json={"role_id": None}, headers=sergio).status_code == 422
    assert api.patch(url, json={"role_id": "dios"}, headers=sergio).status_code == 422
    assert api.patch(url, json={"department_id": "narnia"}, headers=sergio).status_code == 422
    assert api.patch(url, json={"foo": 1}, headers=sergio).status_code == 422
    assert api.patch("/users/no-existe/assignment", json={"role_id": "employee"}, headers=sergio).status_code == 404


def test_last_admin_cannot_be_demoted_or_deleted(api, as_user, user_id):
    sergio = as_user(SERGIO)
    sergio_id = user_id(SERGIO)

    assert api.patch(f"/users/{sergio_id}/assignment", json={"role_id": "supervisor"}, headers=sergio).status_code == 409
    assert api.delete(f"/users/{sergio_id}", headers=sergio).status_code == 409


def test_only_admin_deletes_users(api, as_user, user_id):
    target = user_id(ELENA)

    assert api.delete(f"/users/{target}", headers=as_user(JAVIER)).status_code == 403
    assert api.delete(f"/users/{target}", headers=as_user(SERGIO)).status_code == 200


def test_roles_crud_and_hierarchy_stays_strict(api, as_user):
    sergio = as_user(SERGIO)

    assert api.put("/roles/admin", json={"rank": 10}, headers=sergio).status_code == 200  # hace hueco bajo admin
    created = api.post("/roles", json={"code": "auditor", "name": "Auditor", "rank": 4}, headers=sergio)
    assert created.status_code == 201
    assert api.post("/roles", json={"code": "auditor", "name": "Otra vez", "rank": 5}, headers=sergio).status_code == 409
    assert api.post("/roles", json={"code": "Bad Code", "name": "x", "rank": 5}, headers=sergio).status_code == 422
    assert api.put("/roles/auditor", json={"name": "Auditoría"}, headers=sergio).json()["name"] == "Auditoría"
    assert api.put("/roles/supervisor", json={"rank": 10}, headers=sergio).status_code == 409  # igualaría a admin
    assert api.put("/roles/admin", json={"rank": 2}, headers=sergio).status_code == 409  # rango repetido / bajo otro rol
    assert api.get("/roles/nope", headers=sergio).status_code == 404


def test_new_roles_cannot_break_the_strict_hierarchy(api, as_user):
    sergio = as_user(SERGIO)

    assert api.post("/roles", json={"code": "auditor", "name": "Auditor", "rank": 4}, headers=sergio).status_code == 409  # sobre admin
    assert api.post("/roles", json={"code": "auditor", "name": "Auditor", "rank": 2}, headers=sergio).status_code == 409  # rango repetido


def test_custom_role_is_assignable_and_ranked(api, as_user, user_id):
    sergio = as_user(SERGIO)
    assert api.put("/roles/admin", json={"rank": 10}, headers=sergio).status_code == 200  # hace hueco en la jerarquía
    assert api.post("/roles", json={"code": "auditor", "name": "Auditor", "rank": 5}, headers=sergio).status_code == 201

    elena_id = user_id(ELENA)
    assigned = api.patch(f"/users/{elena_id}/assignment", json={"role_id": "auditor"}, headers=sergio)
    assert assigned.status_code == 200
    assert assigned.json()["department_id"] == "corporate_training"  # el departamento no cambia

    me = api.get("/auth/me", headers=as_user(ELENA)).json()
    assert (me["role"], me["rank"]) == ("auditor", 5)
    # Supera a supervisor (actualizar) pero no a admin (eliminar / administrar).
    assert api.get("/roles", headers=as_user(ELENA)).status_code == 403
    assert api.post("/internal-reports", json={"title": "T", "body": "B"}, headers=as_user(ELENA)).status_code == 201


def test_departments_crud(api, as_user):
    sergio = as_user(SERGIO)

    assert api.post("/departments", json={"code": "legal", "name": "Legal"}, headers=sergio).status_code == 201
    assert api.post("/departments", json={"code": "legal", "name": "Otra"}, headers=sergio).status_code == 409
    assert api.put("/departments/legal", json={"name": "Asuntos legales"}, headers=sergio).json()["name"] == "Asuntos legales"
    assert api.get("/departments/nope", headers=sergio).status_code == 404
    assert api.put("/departments/nope", json={"name": "x"}, headers=sergio).status_code == 404


def test_legacy_manager_role_still_works_as_supervisor(api, as_user, user_id, tmp_path):
    """Datos sin migrar: `manager` cuenta como `supervisor` (rango 2)."""
    from tinydb import Query, TinyDB

    db = TinyDB(tmp_path / "users.json")
    db.table("users").update({"role": "manager"}, Query().id == user_id(MEGAN))
    db.close()

    me = api.get("/auth/me", headers=as_user(MEGAN)).json()
    assert (me["role"], me["rank"]) == ("manager", 2)
    assert api.put(
        f"/internal-reports/{next(r['id'] for r in api.get('/internal-reports', headers=as_user(MEGAN)).json())}",
        json={"title": "ok"}, headers=as_user(MEGAN),
    ).status_code == 200


# --- semillas y migración --------------------------------------------------------------------


def test_seed_matches_the_briefing_exactly(seeded):
    from rbac import store
    from routes import users

    assert [(r["code"], r["rank"]) for r in store.list_roles()] == [("employee", 1), ("supervisor", 2), ("admin", 3)]
    assert len(store.list_departments()) == 7
    for email, role, department in [
        ("javier.talent@nexova.example", "supervisor", "talent_selection"),
        ("megan.sales@nexova.example", "supervisor", "sales"),
        ("elena.training@nexova.example", "employee", "corporate_training"),
        ("patricia.hr@nexova.example", "employee", "human_resources"),
        ("sergio.admin@nexova.example", "admin", "technology"),
    ]:
        user = users.get_user_by_email(email)
        assert (user.role, user.department_id) == (role, department)
    assert {r["department_id"] for r in store.list_reports()} == {"talent_selection", "sales"}


def test_seed_is_idempotent(seeded, tmp_path):
    from rbac import seeding, store
    from security import hash_password

    again = seeding.seed_users(tmp_path / "users.json", tmp_path / "profiles.json", hash_password(PASSWORD))
    seeding.seed_roles_and_departments()
    reports = seeding.seed_internal_reports(tmp_path / "users.json")

    assert again == {"created": 0, "skipped": 5}
    assert reports == {"created": 0, "skipped": 2}
    assert len(store.list_roles()) == 3 and len(store.list_reports()) == 2


def test_migration_maps_legacy_roles_without_touching_the_other_axis(tmp_path):
    from rbac import seeding

    path = tmp_path / "legacy.json"
    rows = [
        {"id": "1", "email": "m@x.com", "role": "manager"},
        {"id": "2", "email": "a@x.com", "role": "admin"},
        {"id": "3", "email": "a2@x.com", "role": "admin", "department_id": "sales"},
        {"id": "4", "email": "u@x.com", "role": "user"},
    ]
    path.write_text(json.dumps({"users": {str(i): r for i, r in enumerate(rows, 1)}}), encoding="utf-8")

    assert len(seeding.migrate_users(path, dry_run=True)) == 2
    assert json.loads(path.read_text())["users"]["1"]["role"] == "manager"  # dry-run no escribe

    seeding.migrate_users(path)
    stored = {r["id"]: r for r in json.loads(path.read_text())["users"].values()}
    assert stored["1"]["role"] == "supervisor" and "department_id" not in stored["1"]
    assert (stored["2"]["role"], stored["2"]["department_id"]) == ("admin", "technology")
    assert stored["3"]["department_id"] == "sales"  # conserva el suyo
    assert stored["4"]["role"] == "user"
    assert seeding.migrate_users(path) == []  # idempotente


@pytest.mark.parametrize("email", [ELENA, JAVIER])
def test_login_works_for_seeded_users(api, email):
    assert api.post("/auth/login", json={"email": email, "password": PASSWORD}).status_code == 200
