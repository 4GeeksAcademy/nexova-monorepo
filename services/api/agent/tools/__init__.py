"""
Tools del agente: consultas de SOLO LECTURA a servicios reales de Nexova.

- `tickets.lookup_tickets`: gestor de incidentes (`GET /api/incidents[/{id}]`).
- `inventory.lookup_inventory`: gestor de inventario (`GET /inventory/products`).

Cada tool hace una sola cosa, tiene contrato Pydantic de entrada/salida,
llama por HTTP con timeout numérico (`transport.tool_timeout_seconds()`) y nunca
lanza: los fallos vuelven como `status` (`timeout`, `error`, `not_found`) para
que el grafo tome la ruta de fallback.
"""
