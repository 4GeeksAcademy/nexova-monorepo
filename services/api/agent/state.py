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
