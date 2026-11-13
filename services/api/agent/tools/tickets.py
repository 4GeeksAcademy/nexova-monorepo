"""
Tool de tickets: consulta en vivo el gestor de incidentes (solo lectura).

    GET /api/incidents/{ticket_id}   (por id)
    GET /api/incidents?status=&category=&client_company=&limit=   (por filtros)

Ambos endpoints exigen JWT: se reenvía el de quien llama al agente (ver
`agent.tools.transport`).
"""

from __future__ import annotations

import time
from datetime import date, datetime
from typing import Literal

import httpx
from pydantic import BaseModel, Field, ValidationError

from agent.tools.transport import ToolCallError, ToolStatus, http_get

TICKET_ID_PATTERN = r"^NXV-\d{6}$"

TicketStatus = Literal["OPEN", "CLOSED", "DISCARDED"]
TicketCategory = Literal["TECHNICAL", "BILLING", "ACCESS", "HR_QUERY", "COMPLAINT"]


class TicketQuery(BaseModel):
    """Entrada: un `ticket_id` concreto o filtros de búsqueda."""

    ticket_id: str | None = Field(default=None, pattern=TICKET_ID_PATTERN)
    status: TicketStatus | None = None
    category: TicketCategory | None = None
    client_company: str | None = Field(default=None, min_length=2, max_length=100)
    limit: int = Field(default=10, ge=1, le=50)


class TicketInfo(BaseModel):
    """Salida: los mismos campos que expone `GET /api/incidents` (sin emails)."""

    ticket_id: str
    date: date
    client_company: str
    category: str
    description: str
    agent_id: str
    status: str
    satisfaction_score: int | None = None
    updated_at: datetime | None = None


class TicketToolResult(BaseModel):
    tool: Literal["tickets"] = "tickets"
    status: ToolStatus
    query: TicketQuery
    tickets: list[TicketInfo] = Field(default_factory=list)
    detail: str | None = None
    duration_ms: float = 0.0


def lookup_tickets(query: TicketQuery, *, client: httpx.Client | None = None) -> TicketToolResult:
    """Consulta el gestor de incidentes. Nunca lanza: los fallos van en `status`."""
    start = time.perf_counter()

    def done(status: ToolStatus, tickets: list[TicketInfo] | None = None, detail: str | None = None):
        return TicketToolResult(
            status=status,
            query=query,
            tickets=tickets or [],
            detail=detail,
            duration_ms=round((time.perf_counter() - start) * 1000, 1),
        )

    try:
        if query.ticket_id:
            payload = http_get(f"/api/incidents/{query.ticket_id}", authenticated=True, client=client)
            tickets = [TicketInfo.model_validate(payload)]
        else:
            payload = http_get(
                "/api/incidents",
                params=query.model_dump(exclude={"ticket_id"}),
                authenticated=True,
                client=client,
            )
            tickets = [TicketInfo.model_validate(item) for item in payload]
    except ToolCallError as exc:
        return done(exc.status, detail=exc.detail)
    except (ValidationError, TypeError):
        return done("error", detail="Respuesta del gestor de incidentes con formato inesperado")

    if not tickets:
        return done("not_found", detail="Ningún ticket coincide con la búsqueda")
    return done("ok", tickets)
