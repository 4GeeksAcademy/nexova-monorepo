"""
Estado del grafo del agente.

Solo lo que un nodo necesita para decidir el siguiente paso:

- `question`: la pregunta del usuario (normalizada por `receive_question`).
- `route`: decisión del enrutador (`RouteDecision` serializada): qué fuentes
  hacen falta (RAG, tickets, inventario) y con qué parámetros.
- `sources_used`: fuentes ya consultadas, en orden (`"tickets"`, `"inventory"`,
  `"rag"`). Permite saber cuál falta y deja el orden visible en el trace.
- `tool_results`: resultado serializado de cada tool (`status` + datos).
- `context`: chunks que devolvió `retrieve()` (ya filtrados por `min_score`).
- `answer`: respuesta final (generada, fallback honesto o sin información).
- `error`: código de error de negocio (p. ej. `empty_question`) o `None`.

Memoria (solo con `build_graph(memory=...)`, ver `agent/memory/nodes.py`):

- `conversation_id` / `user_id`: a qué conversación y a quién pertenece la
  propuesta pendiente (la propuesta vive en la base de datos, no en el estado).
- `memory_decision` / `memory_notice` / `memory_done`: resultado de clasificar
  el mensaje contra la propuesta pendiente y si el turno termina ahí.
- `memory_facts`: hechos aprobados leídos de la memoria para esta pregunta.
- `memory_candidate`: `propuesta_memoria` de la generación estructurada.
- `memory_proposal` / `memory_blocked`: propuesta registrada como pendiente, o
  motivo por el que la política la bloqueó.

Harness (solo con `build_graph(guardrails=...)`, ver `agent/guardrails/`):

- `tenant`: empresa cliente de quien escribe y si es personal interno
  (`Tenant.as_state()`); la tool de tickets lo aplica siempre.
- `guard`: veredicto del guard de entrada (categoría, acción, tipo de fallo).
- `output_blocked` / `output_rules`: resultado del guard de salida.

No se guarda historial de conversación: cada consulta es independiente y
arrastrarlo engordaría cada checkpoint sin que ningún nodo lo lea. Tampoco
credenciales: el token OAuth de las tools vive solo en el cliente MCP
(`agent.tools.mcp_client`).
"""

from __future__ import annotations

from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    question: str
    route: dict[str, Any]
    sources_used: list[str]
    tool_results: list[dict[str, Any]]
    context: list[dict[str, Any]]
    answer: str | None
    error: str | None
    # Memoria
    conversation_id: str | None
    user_id: str | None
    memory_decision: dict[str, Any] | None
    memory_notice: str | None
    memory_done: bool
    memory_facts: list[dict[str, Any]]
    memory_candidate: dict[str, Any] | None
    memory_proposal: dict[str, Any] | None
    memory_blocked: str | None
    # Harness
    tenant: dict[str, Any]
    guard: dict[str, Any]
    output_blocked: bool
    output_rules: list[str]
