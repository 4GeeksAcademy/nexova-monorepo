"""
El agente (y el MCP) hablan el vocabulario del gestor de incidencias.

Los Literal del agente se duplican a propósito (el worker no monta `packages/shared`);
este test garantiza que no se desincronizan de `nexova_shared.incident_domain`, que es
la fuente de verdad, y que las reglas de respaldo del enrutador entienden los estados.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import get_args

import pytest

ROOT = Path(__file__).resolve().parents[2]
for path in (ROOT / "services" / "api", ROOT / "packages" / "shared", ROOT / "packages" / "incidents-analyzer"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def test_agent_enums_match_the_shared_domain() -> None:
    from agent.tools.tickets import TicketCategory, TicketStatus
    from nexova_shared.incident_domain import CATEGORIES, STATUSES

    assert get_args(TicketStatus) == STATUSES
    assert get_args(TicketCategory) == CATEGORIES


@pytest.mark.parametrize(
    ("question", "status"),
    [
        ("¿Cuántas incidencias abiertas tenemos?", "open"),
        ("¿Qué tickets están en curso?", "in_progress"),
        ("Dime las incidencias resueltas", "resolved"),
        ("¿Hay tickets cerrados esta semana?", "resolved"),  # «cerrado» = resuelto en el gestor nuevo
        ("Incidencias descartadas del mes", "discarded"),
    ],
)
def test_rules_fallback_understands_every_status(question: str, status: str) -> None:
    from agent.routing import classify_with_rules

    decision = classify_with_rules(question)
    assert decision.tickets and decision.ticket_status == status


def test_ticket_reference_keeps_the_nxv_format() -> None:
    from agent.routing import classify_with_rules

    assert classify_with_rules("¿Cómo va el ticket NXV-000054?").ticket_id == "NXV-000054"
    assert classify_with_rules("¿y la incidencia 54?").ticket_id == "NXV-000054"


def test_a_ticket_without_company_is_invisible_to_a_client() -> None:
    from agent.guardrails.tenancy import Tenant

    assert Tenant(client_company="Acme Corp").owns(None) is False  # fallo seguro
    assert Tenant(is_staff=True).owns(None) is True
