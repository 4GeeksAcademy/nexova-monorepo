"""Alta de tickets y ciclo de vida (`PATCH /api/incidents/{id}/status`)."""

from __future__ import annotations

import pytest

NEW_TICKET = {
    "client_company": "Globex",
    "category": "BILLING",
    "description": "Factura duplicada en octubre",
    "agent_id": "AG-07",
}


def test_create_ticket_assigns_next_id_and_open_status(make_client, seed) -> None:
    seed(("NXV-000041", "OPEN"), ("NXV-000096", "CLOSED"))
    response = make_client().post("/api/incidents", json=NEW_TICKET)

    assert response.status_code == 201
    body = response.json()
    assert body["ticket_id"] == "NXV-000097"
    assert body["status"] == "OPEN"
    assert body["category"] == "BILLING"
    assert body["satisfaction_score"] is None
    assert "customer_email" not in body


def test_create_first_ticket_on_empty_table(make_client) -> None:
    response = make_client().post("/api/incidents", json={**NEW_TICKET, "date": "2026-11-10"})
    assert response.status_code == 201
    assert response.json()["ticket_id"] == "NXV-000001"
    assert response.json()["date"] == "2026-11-10"


@pytest.mark.parametrize(
    "payload",
    [
        {**NEW_TICKET, "category": "OTHER"},
        {**NEW_TICKET, "description": "   "},
        {**NEW_TICKET, "status": "CLOSED"},  # el estado inicial no se elige
        {k: v for k, v in NEW_TICKET.items() if k != "agent_id"},
    ],
)
def test_create_ticket_rejects_invalid_payload(make_client, payload) -> None:
    assert make_client().post("/api/incidents", json=payload).status_code == 422


@pytest.mark.parametrize(
    ("current", "target"),
    [("OPEN", "CLOSED"), ("OPEN", "DISCARDED"), ("CLOSED", "OPEN")],
)
def test_allowed_transitions(make_client, seed, current, target) -> None:
    seed(("NXV-000482", current))
    response = make_client().patch("/api/incidents/nxv-000482/status", json={"status": target})

    assert response.status_code == 200
    assert response.json()["status"] == target
    assert response.json()["updated_at"] > "2026-11-01T00:00:00"


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("OPEN", "OPEN"),
        ("CLOSED", "CLOSED"),
        ("CLOSED", "DISCARDED"),
        ("DISCARDED", "OPEN"),
        ("DISCARDED", "CLOSED"),
    ],
)
def test_forbidden_transitions_return_409(make_client, seed, current, target) -> None:
    seed(("NXV-000482", current))
    response = make_client().patch("/api/incidents/NXV-000482/status", json={"status": target})

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "invalid_transition"
    assert detail["current_status"] == current
    assert detail["requested_status"] == target


def test_status_of_missing_ticket_is_404(make_client) -> None:
    response = make_client().patch("/api/incidents/NXV-999999/status", json={"status": "CLOSED"})
    assert response.status_code == 404


def test_unknown_status_is_422(make_client, seed) -> None:
    seed(("NXV-000482", "OPEN"))
    response = make_client().patch("/api/incidents/NXV-000482/status", json={"status": "SOLVED"})
    assert response.status_code == 422


def test_write_endpoints_require_auth(make_client) -> None:
    client = make_client(authenticated=False)
    assert client.post("/api/incidents", json=NEW_TICKET).status_code == 401
    assert client.patch("/api/incidents/NXV-000001/status", json={"status": "CLOSED"}).status_code == 401
