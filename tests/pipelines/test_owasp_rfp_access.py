"""
Auditoría OWASP — hallazgo C1 (A01 Broken Access Control) en `/rfp`.

Antes: `require_internal_user` solo rechazaba usuarios con `client_company`, y
el registro público (`POST /users`) crea cuentas `user` sin empresa → cualquier
persona podía registrarse y subir, aprobar o leer RFPs. Además `acting_as` lo
elegía quien llamaba: cualquiera podía decidir "como" el responsable.

Después: solo personal interno (admin/manager); la decisión de aprobación solo
la registra el responsable configurado en `RFP_APPROVERS` (nombre → email) o un
admin, y siempre queda `decided_by_user`.

    uv run --project services/api pytest tests/pipelines/test_owasp_rfp_access.py -v
"""

from __future__ import annotations

import json

import pytest

from test_rfp_api import _upload, approval_ctx, ctx  # noqa: F401  (fixtures reutilizados)


def _as(ctx, role, *, email="persona@nexova-solutions.com", company=None):
    from models import Role

    ctx.user.role = Role(role)
    ctx.user.email = email
    ctx.user.client_company = company


@pytest.mark.parametrize("path", ["/rfp/tickets", "/rfp/tickets/RFP-X", "/rfp/tickets/RFP-X/trace",
                                  "/rfp/tickets/RFP-X/final-document"])
def test_self_registered_user_cannot_read_rfps(ctx, path):
    _as(ctx, "user")  # lo que crea POST /users: rol user, sin empresa
    assert ctx.client.get(path).status_code == 403


def test_self_registered_user_cannot_upload_rfps(ctx):
    _as(ctx, "user")
    assert _upload(ctx.client).status_code == 403
    assert ctx.enqueued == []


@pytest.mark.parametrize("role", ["admin", "manager"])
def test_staff_can_upload_and_list(ctx, role):
    _as(ctx, role)
    assert _upload(ctx.client).status_code == 202
    assert ctx.client.get("/rfp/tickets").status_code == 200


def test_self_registered_user_cannot_approve(ctx, approval_ctx):
    _as(ctx, "user")
    response = ctx.client.post(f"/rfp/tickets/{approval_ctx.ticket_id}/approvals/soporte",
                               json={"decision": "approve", "acting_as": "Roberto Díaz"})
    assert response.status_code == 403
    assert approval_ctx.resumed == []


def test_manager_cannot_impersonate_the_expected_approver(ctx, approval_ctx, monkeypatch):
    monkeypatch.setenv("RFP_APPROVERS", json.dumps({"Roberto Díaz": "roberto.diaz@nexova-solutions.com"}))
    _as(ctx, "manager", email="otra.manager@nexova-solutions.com")
    response = ctx.client.post(f"/rfp/tickets/{approval_ctx.ticket_id}/approvals/soporte",
                               json={"decision": "approve", "acting_as": "Roberto Díaz"})
    assert response.status_code == 403
    assert "Roberto Díaz" in response.json()["detail"]
    assert approval_ctx.resumed == []


def test_without_approver_mapping_only_admin_decides(ctx, approval_ctx, monkeypatch):
    monkeypatch.delenv("RFP_APPROVERS", raising=False)
    _as(ctx, "manager")
    assert ctx.client.post(f"/rfp/tickets/{approval_ctx.ticket_id}/approvals/soporte",
                           json={"decision": "approve", "acting_as": "Roberto Díaz"}).status_code == 403


def test_mapped_approver_decides_and_is_recorded(ctx, approval_ctx, monkeypatch):
    monkeypatch.setenv("RFP_APPROVERS", json.dumps({"Roberto Díaz": "Roberto.Diaz@nexova-solutions.com"}))
    _as(ctx, "manager", email="roberto.diaz@nexova-solutions.com")
    response = ctx.client.post(f"/rfp/tickets/{approval_ctx.ticket_id}/approvals/soporte",
                               json={"decision": "approve", "acting_as": "Roberto Díaz"})
    assert response.status_code == 202
    [queued] = approval_ctx.resumed
    assert queued["decision"]["decided_by_user"] == "roberto.diaz@nexova-solutions.com"
