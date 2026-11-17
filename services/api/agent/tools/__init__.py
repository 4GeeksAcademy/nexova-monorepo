"""
Tools del agente: consultas de SOLO LECTURA a servicios reales de Nexova, todas
a través del MCP Server `mcps/nexova-tools` (`mcp_client`, OAuth M2M con los
scopes `incidents:read inventory:read`). El agente no tiene ningún otro camino
hacia el Incidents Manager ni el inventario.

- `tickets.lookup_tickets`: tools MCP `get_ticket` / `list_tickets`.
- `inventory.lookup_inventory`: tool MCP `query_inventory` (action `list`).

Cada tool hace una sola cosa, tiene contrato Pydantic de entrada/salida, un
timeout numérico (`transport.tool_timeout_seconds()`) y nunca lanza: los
fallos vuelven como `status` (`timeout`, `error`, `not_found`) para que el
grafo tome la ruta de fallback.
"""
