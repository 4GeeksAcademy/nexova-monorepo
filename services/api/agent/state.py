"""
Estado del grafo del agente.

Solo lo que un nodo necesita para decidir el siguiente paso:

- `question`: la pregunta del usuario (normalizada por `receive_question`).
- `context`: chunks que devolvió `retrieve()` (ya filtrados por `min_score`).
- `answer`: respuesta final (generada o la respuesta honesta sin información).
- `error`: código de error de negocio (p. ej. `empty_question`) o `None`.

No se guarda historial de conversación: cada consulta al asistente de ventas
es independiente (el RAG actual tampoco lo usa) y arrastrarlo engordaría cada
checkpoint sin que ningún nodo lo lea. Si la Parte 2 necesita memoria entre
turnos, se añadirá aquí con su justificación.
"""

from __future__ import annotations

from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    question: str
    context: list[dict[str, Any]]
    answer: str | None
    error: str | None
