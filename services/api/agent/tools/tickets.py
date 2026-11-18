"""
Tool de tickets: consulta en vivo el gestor de incidencias a través del MCP Server
`mcps/nexova-tools` (solo lectura para el agente).

    get_ticket(ticket_id)                                   (por id)
    list_tickets(status, category, client_company, limit)   (por filtros)

El agente solo tiene el scope `incidents:read`: aunque el MCP Server expone
`create_ticket` y `update_ticket_status`, este cliente nunca puede usarlas.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from agent.tools.mcp_client import McpToolCaller, call_mcp_tool
from agent.tools.transport import ToolCallError, ToolStatus

TICKET_ID_PATTERN = r"^NXV-\d{6}$"

# Mismos valores que `packages/shared/nexova_shared/incident_domain.py` (fuente de verdad; un
# test comprueba que coinciden). El agente no importa ese paquete: el worker no lo monta.
TicketStatus = Literal["open", "in_progress", "resolved", "discarded"]
TicketCategory = Literal[
    "technical_failure",
    "process_error",
    "client_complaint",
    "candidate_issue",
    "staff_issue",
    "sla_breach",
    "data_quality",
    "other",
]


class TicketQuery(BaseModel):
    """Entrada: un `ticket_id` concreto o filtros de búsqueda."""

    ticket_id: str | None = Field(default=None, pattern=TICKET_ID_PATTERN)
    status: TicketStatus | None = None
    category: TicketCategory | None = None
    client_company: str | None = Field(default=None, min_length=2, max_length=100)
    limit: int = Field(default=10, ge=1, le=50)


class TicketInfo(BaseModel):
    """Salida: los campos que expone el gestor de incidencias. `ticket_id` es la referencia
    `NXV-NNNNNN` (el MCP la deriva del `id` numérico de la API)."""

    ticket_id: str
    title: str
    description: str
    category: str
    status: str
    origin: str
    branch: str
    client_company: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class TicketToolResult(BaseModel):
    tool: Literal["tickets"] = "tickets"
    status: ToolStatus
    query: TicketQuery
    tickets: list[TicketInfo] = Field(default_factory=list)
    detail: str | None = None
    duration_ms: float = 0.0


def lookup_tickets(query: TicketQuery, *, call_tool: McpToolCaller | None = None) -> TicketToolResult:
    """Consulta el gestor de incidentes vía MCP. Nunca lanza: los fallos van en `status`."""
    call = call_tool or call_mcp_tool
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
            payload = call("get_ticket", {"ticket_id": query.ticket_id})
            tickets = [TicketInfo.model_validate(payload)]
        else:
            filters = query.model_dump(exclude={"ticket_id"}, exclude_none=True)
            payload = call("list_tickets", filters)
            tickets = [TicketInfo.model_validate(item) for item in payload["tickets"]]
    except ToolCallError as exc:
        return done(exc.status, detail=exc.detail)
    except (ValidationError, TypeError, KeyError):
        return done("error", detail="Respuesta del gestor de incidentes con formato inesperado")

    if not tickets:
        return done("not_found", detail="Ningún ticket coincide con la búsqueda")
    return done("ok", tickets)
