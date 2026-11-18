"""
Tools del gestor de incidencias.

| Tool                   | Scope            | Endpoint de la API                    |
|------------------------|------------------|---------------------------------------|
| `list_tickets`         | incidents:read   | GET   /api/incidents                  |
| `get_ticket`           | incidents:read   | GET   /api/incidents/{id}             |
| `create_ticket`        | incidents:write  | POST  /api/incidents                  |
| `update_ticket_status` | incidents:write  | PATCH /api/incidents/{id}/status      |

`ticket_id` (`NXV-NNNNNN`) es la referencia de la incidencia: `{id}` de la API es ese
número sin el prefijo (`ticket_pk`).

Los cambios de estado solo pasan por el endpoint de ciclo de vida, nunca por
una edición genérica del ticket.
"""

from __future__ import annotations

from typing import Annotated

from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from mcpauth import MCPAuth
from pydantic import Field

from nexova_mcp.api_client import NexovaApiClient
from nexova_mcp.auth import SCOPE_INCIDENTS_READ, SCOPE_INCIDENTS_WRITE, require_scope
from nexova_mcp.models import (
    TICKET_ID_PATTERN,
    BlankAsNone,
    Ticket,
    TicketBranch,
    TicketCategory,
    TicketList,
    TicketOrigin,
    TicketStatus,
    blank_as,
    ticket_pk,
)

TicketId = Annotated[
    str,
    Field(pattern=TICKET_ID_PATTERN, description="Id del ticket, formato NXV-NNNNNN (p. ej. NXV-000482)."),
]

ERRORS_READ = "Errores: FORBIDDEN_SCOPE, VALIDATION_ERROR, NOT_FOUND, UPSTREAM_UNAVAILABLE."


def register(mcp: FastMCP, mcp_auth: MCPAuth, api: NexovaApiClient) -> None:
    @mcp.tool(
        name="list_tickets",
        title="Listar tickets",
        description=(
            "Lista tickets del gestor de incidencias de Nexova, del más reciente al más antiguo, "
            "con filtros opcionales por estado, categoría y empresa cliente (coincidencia parcial). "
            "Solo lectura. Requiere el scope 'incidents:read'. " + ERRORS_READ
        ),
        annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False),
        tags={"incidents"},
    )
    async def list_tickets(
        status: Annotated[TicketStatus | None, BlankAsNone, Field(description="Filtra por estado.")] = None,
        category: Annotated[TicketCategory | None, BlankAsNone, Field(description="Filtra por categoría.")] = None,
        client_company: Annotated[
            Annotated[str, Field(min_length=2, max_length=100)] | None,
            BlankAsNone,
            Field(description="Empresa cliente (parcial, sin distinguir mayúsculas)."),
        ] = None,
        limit: Annotated[int, blank_as(10), Field(ge=1, le=50, description="Máximo de tickets a devolver.")] = 10,
    ) -> TicketList:
        require_scope(mcp_auth, SCOPE_INCIDENTS_READ)
        params = {"status": status, "category": category, "client_company": client_company, "limit": limit}
        payload = await api.get("/api/incidents", params={k: v for k, v in params.items() if v is not None})
        tickets = [Ticket.from_api(item) for item in payload]
        return TicketList(count=len(tickets), tickets=tickets)

    @mcp.tool(
        name="get_ticket",
        title="Consultar un ticket",
        description=(
            "Devuelve un ticket del gestor de incidencias por su id (formato NXV-NNNNNN), con su estado actual. "
            "Solo lectura. Requiere el scope 'incidents:read'. " + ERRORS_READ
        ),
        annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False),
        tags={"incidents"},
    )
    async def get_ticket(ticket_id: TicketId) -> Ticket:
        require_scope(mcp_auth, SCOPE_INCIDENTS_READ)
        return Ticket.from_api(await api.get(f"/api/incidents/{ticket_pk(ticket_id)}"))

    @mcp.tool(
        name="create_ticket",
        title="Crear un ticket",
        description=(
            "Abre un ticket nuevo en el gestor de incidencias. El servidor asigna el id (NXV-NNNNNN) y el "
            "estado inicial open; no se pueden elegir. Requiere el scope 'incidents:write'. "
            "Errores: FORBIDDEN_SCOPE, VALIDATION_ERROR, UPSTREAM_UNAVAILABLE."
        ),
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False),
        tags={"incidents"},
    )
    async def create_ticket(
        title: Annotated[str, Field(min_length=1, max_length=120, description="Título breve de la incidencia.")],
        description: Annotated[str, Field(min_length=1, max_length=5000, description="Descripción del problema.")],
        category: Annotated[TicketCategory, Field(description="Categoría de la incidencia.")],
        origin: Annotated[TicketOrigin, Field(description="Quién la reporta: customer, branch o internal.")],
        branch: Annotated[TicketBranch, Field(description="Sede; usa central si no corresponde a una concreta.")],
        client_company: Annotated[
            Annotated[str, Field(min_length=1, max_length=100)] | None,
            BlankAsNone,
            Field(description="Empresa cliente asociada (opcional)."),
        ] = None,
    ) -> Ticket:
        require_scope(mcp_auth, SCOPE_INCIDENTS_WRITE)
        body = {"title": title, "description": description, "category": category, "origin": origin, "branch": branch}
        if client_company is not None:
            body["client_company"] = client_company
        return Ticket.from_api(await api.request("POST", "/api/incidents", json=body))

    @mcp.tool(
        name="update_ticket_status",
        title="Cambiar el estado de un ticket",
        description=(
            "Cambia el estado de un ticket mediante el ciclo de vida del gestor de incidencias "
            "(PATCH /api/incidents/{id}/status). Transiciones permitidas: open→in_progress, open→discarded, "
            "in_progress→resolved e in_progress→discarded. resolved y discarded son finales. Cualquier otra transición devuelve "
            "INVALID_TRANSITION con el estado actual y las transiciones permitidas. Es la única forma de "
            "modificar un ticket existente. Requiere el scope 'incidents:write'. "
            "Errores: FORBIDDEN_SCOPE, VALIDATION_ERROR, NOT_FOUND, INVALID_TRANSITION, UPSTREAM_UNAVAILABLE."
        ),
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False),
        tags={"incidents"},
    )
    async def update_ticket_status(
        ticket_id: TicketId,
        status: Annotated[TicketStatus, Field(description="Estado de destino.")],
    ) -> Ticket:
        require_scope(mcp_auth, SCOPE_INCIDENTS_WRITE)
        payload = await api.request("PATCH", f"/api/incidents/{ticket_pk(ticket_id)}/status", json={"status": status})
        return Ticket.from_api(payload)
