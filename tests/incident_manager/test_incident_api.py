"""Contrato HTTP del gestor de incidencias (README · Backend)."""

from __future__ import annotations

BASE = "/api/incidents"


def _create(client, payload, **overrides):
    return client.post(BASE, json={**payload, **overrides})


# --- POST -------------------------------------------------------------------


def test_create_returns_201_open_with_generated_fields(make_client, payload):
    res = _create(make_client(), payload)
    assert res.status_code == 201
    body = res.json()
    assert body["status"] == "open"
    assert isinstance(body["id"], int)
    assert body["created_at"] and body["updated_at"]
    assert body["branch"] == "valencia_operations"


def test_create_trims_whitespace(make_client, payload):
    body = _create(make_client(), payload, title="  Fallo en el ATS  ").json()
    assert body["title"] == "Fallo en el ATS"


def test_create_accepts_every_branch_and_category(make_client, payload):
    from nexova_shared.incident_domain import BRANCHES, CATEGORIES

    client = make_client()
    for branch in BRANCHES:
        assert _create(client, payload, branch=branch).status_code == 201
    for category in CATEGORIES:
        assert _create(client, payload, category=category).status_code == 201


def test_missing_fields_return_400_naming_each_field(make_client):
    res = make_client().post(BASE, json={})
    assert res.status_code == 400
    fields = {e["field"] for e in res.json()["errors"]}
    assert fields == {"title", "description", "category", "origin", "branch"}
    assert all(e["message"] == "Este campo es obligatorio." for e in res.json()["errors"])


def test_empty_title_and_blank_description_return_400(make_client, payload):
    res = _create(make_client(), payload, title="   ", description="")
    assert res.status_code == 400
    assert {e["field"] for e in res.json()["errors"]} == {"title", "description"}


def test_invalid_enum_value_lists_allowed_values(make_client, payload):
    res = _create(make_client(), payload, branch="headquarters")
    assert res.status_code == 400
    [error] = res.json()["errors"]
    assert error["field"] == "branch"
    assert "central" in error["message"] and "remote" in error["message"]


def test_title_too_long_is_rejected(make_client, payload):
    res = _create(make_client(), payload, title="x" * 121)
    assert res.status_code == 400
    assert res.json()["errors"][0] == {"field": "title", "message": "Máximo 120 caracteres."}


def test_status_cannot_be_chosen_on_create(make_client, payload):
    res = _create(make_client(), payload, status="resolved")
    assert res.status_code == 400
    assert res.json()["errors"][0]["field"] == "status"


def test_invalid_json_body_returns_400(make_client):
    res = make_client().post(
        BASE, content="{no es json", headers={"Content-Type": "application/json"}
    )
    assert res.status_code == 400
    assert "errors" in res.json()


# --- GET lista --------------------------------------------------------------


def test_list_is_empty_list_when_no_data(make_client):
    res = make_client().get(BASE)
    assert res.status_code == 200
    assert res.json() == []


def test_list_filters_by_each_parameter(make_client, payload):
    client = make_client()
    _create(client, payload)
    _create(client, payload, origin="customer", branch="central", category="sla_breach")
    _create(client, payload, origin="internal", branch="remote", category="other")

    assert len(client.get(BASE).json()) == 3
    assert len(client.get(BASE, params={"origin": "customer"}).json()) == 1
    assert len(client.get(BASE, params={"branch": "remote"}).json()) == 1
    assert len(client.get(BASE, params={"category": "sla_breach"}).json()) == 1
    assert len(client.get(BASE, params={"status": "open"}).json()) == 3
    assert client.get(BASE, params={"status": "resolved"}).json() == []
    combined = client.get(BASE, params={"origin": "customer", "branch": "central"}).json()
    assert len(combined) == 1


def test_list_newest_first(make_client, payload):
    client = make_client()
    first = _create(client, payload, title="primera").json()["id"]
    second = _create(client, payload, title="segunda").json()["id"]
    assert [r["id"] for r in client.get(BASE).json()] == [second, first]


def test_invalid_filter_value_returns_400(make_client):
    res = make_client().get(BASE, params={"status": "closed"})
    assert res.status_code == 400
    assert res.json()["errors"][0]["field"] == "status"


# --- GET detalle ------------------------------------------------------------


def test_get_detail_and_404(make_client, payload):
    client = make_client()
    created = _create(client, payload).json()
    assert client.get(f"{BASE}/{created['id']}").json()["title"] == created["title"]
    missing = client.get(f"{BASE}/9999")
    assert missing.status_code == 404


def test_non_numeric_id_is_a_clear_400(make_client):
    res = make_client().get(f"{BASE}/abc")
    assert res.status_code == 400
    assert res.json()["errors"][0]["field"] == "incident_id"


# --- PATCH estado -----------------------------------------------------------


def _patch(client, incident_id, status):
    return client.patch(f"{BASE}/{incident_id}/status", json={"status": status})


def test_valid_lifecycle_open_to_in_progress_to_resolved(make_client, payload):
    client = make_client()
    incident_id = _create(client, payload).json()["id"]
    first = _patch(client, incident_id, "in_progress")
    assert first.status_code == 200 and first.json()["status"] == "in_progress"
    second = _patch(client, incident_id, "resolved")
    assert second.status_code == 200 and second.json()["status"] == "resolved"


def test_updated_at_changes_on_status_change(make_client, payload):
    client = make_client()
    created = _create(client, payload).json()
    updated = _patch(client, created["id"], "discarded").json()
    assert updated["updated_at"] >= created["updated_at"]
    assert updated["created_at"] == created["created_at"]


def test_open_can_be_discarded(make_client, payload):
    client = make_client()
    incident_id = _create(client, payload).json()["id"]
    assert _patch(client, incident_id, "discarded").status_code == 200


def test_invalid_transitions_return_400_with_allowed_states(make_client, payload):
    client = make_client()
    incident_id = _create(client, payload).json()["id"]

    skipped = _patch(client, incident_id, "resolved")
    assert skipped.status_code == 400
    body = skipped.json()
    assert body["errors"][0]["field"] == "status"
    assert body["allowed"] == ["in_progress", "discarded"]

    assert _patch(client, incident_id, "open").status_code == 400  # open → open


def test_final_states_cannot_change(make_client, payload):
    client = make_client()
    for final in ("resolved", "discarded"):
        incident_id = _create(client, payload).json()["id"]
        _patch(client, incident_id, "in_progress")
        _patch(client, incident_id, final)
        for target in ("open", "in_progress", "resolved", "discarded"):
            res = _patch(client, incident_id, target)
            assert res.status_code == 400, (final, target)
            assert res.json()["allowed"] == []


def test_patch_unknown_status_value_or_unknown_id(make_client, payload):
    client = make_client()
    incident_id = _create(client, payload).json()["id"]
    bad = _patch(client, incident_id, "closed")
    assert bad.status_code == 400 and bad.json()["errors"][0]["field"] == "status"
    assert _patch(client, 9999, "in_progress").status_code == 404


def test_patch_only_accepts_status(make_client, payload):
    client = make_client()
    incident_id = _create(client, payload).json()["id"]
    res = client.patch(f"{BASE}/{incident_id}/status", json={"status": "in_progress", "title": "x"})
    assert res.status_code == 400


# --- GET summary ------------------------------------------------------------


def test_summary_is_all_zeros_when_empty(make_client):
    res = make_client().get(f"{BASE}/summary")
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 0
    assert body["by_status"] == {"open": 0, "in_progress": 0, "resolved": 0, "discarded": 0}
    assert set(body["by_origin"]) == {"customer", "branch", "internal"}
    assert set(body["by_branch"]) == {"central", "valencia_operations", "miami_office", "remote"}
    assert len(body["by_category"]) == 8
    assert all(v == 0 for key in ("by_category", "by_origin", "by_branch") for v in body[key].values())


def test_summary_counts_each_dimension(make_client, payload):
    client = make_client()
    _create(client, payload)
    second = _create(client, payload, origin="customer", branch="remote", category="sla_breach")
    _patch(client, second.json()["id"], "in_progress")

    body = client.get(f"{BASE}/summary").json()
    assert body["total"] == 2
    assert body["by_status"]["open"] == 1 and body["by_status"]["in_progress"] == 1
    assert body["by_category"]["sla_breach"] == 1
    assert body["by_origin"] == {"customer": 1, "branch": 1, "internal": 0}
    assert body["by_branch"]["remote"] == 1 and body["by_branch"]["valencia_operations"] == 1


# --- Errores del servidor y acceso -----------------------------------------


def test_unhandled_error_is_generic_500_without_internals(make_client):
    res = make_client(broken_db=True).get(BASE)
    assert res.status_code == 500
    text = res.text
    assert "supersecreta" not in text and "Traceback" not in text and "db.interno" not in text
    assert res.json() == {
        "detail": "Ha ocurrido un error inesperado. Inténtalo de nuevo más tarde."
    }


def test_requires_authentication(make_client):
    assert make_client(role=None).get(BASE).status_code == 401


def test_regular_user_is_forbidden_but_staff_is_allowed(make_client):
    assert make_client(role="user").get(BASE).status_code == 403
    assert make_client(role="user").post(BASE, json={}).status_code in (400, 403)
    assert make_client(role="admin").get(BASE).status_code == 200
    assert make_client(role="manager").get(BASE).status_code == 200
