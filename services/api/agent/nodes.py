"""
Nodos del grafo. Cada uno tiene una sola responsabilidad y devuelve solo la
parte del estado que modifica.

Contrato con el RAG (`data/pipelines/rag.py`): el nodo `retrieve` llama a
`retrieve()` y el nodo `generate` llama a `generate_answer(question, context)`
con el contexto que ya produjo `retrieve`. Ningún nodo llama a `query()`
(recuperación + generación juntas), que volvería a recuperar y ocultaría el
flujo. Las funciones se inyectan para poder probar cada nodo de forma aislada.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent.state import AgentState

RetrieveFn = Callable[[str], list[dict[str, Any]]]
GenerateFn = Callable[[str, list[dict[str, Any]]], str]
Node = Callable[[AgentState], dict[str, Any]]

RECEIVE_QUESTION = "receive_question"
RETRIEVE = "retrieve"
GENERATE = "generate"
NO_INFORMATION = "no_information"
REJECT = "reject"

EMPTY_QUESTION_ERROR = "empty_question"
EMPTY_QUESTION_MESSAGE = "La pregunta está vacía: escribe qué quieres consultar."

# Misma política que la regla 5 del SYSTEM_PROMPT del RAG, sin gastar una
# llamada al LLM cuando no hay contexto que lo respalde.
NO_INFORMATION_ANSWER = (
    "La base de conocimiento de Nexova no tiene información suficiente sobre eso. "
    "Te sugiero consultarlo con un account manager senior antes de responder al prospecto."
)


def receive_question(state: AgentState) -> dict[str, Any]:
    """Normaliza la pregunta y marca el error si queda vacía."""
    question = (state.get("question") or "").strip()
    return {
        "question": question,
        "error": None if question else EMPTY_QUESTION_ERROR,
    }


def make_retrieve_node(retrieve_fn: RetrieveFn) -> Node:
    def retrieve(state: AgentState) -> dict[str, Any]:
        """Recupera los chunks relevantes (ya filtrados por `min_score`)."""
        return {"context": retrieve_fn(state["question"])}

    return retrieve


def make_generate_node(generate_fn: GenerateFn) -> Node:
    def generate(state: AgentState) -> dict[str, Any]:
        """Genera la respuesta a partir del contexto ya recuperado."""
        return {"answer": generate_fn(state["question"], state.get("context") or [])}

    return generate


def no_information(state: AgentState) -> dict[str, Any]:
    """Responde con honestidad cuando ningún chunk supera el umbral."""
    return {"answer": NO_INFORMATION_ANSWER}


def reject(state: AgentState) -> dict[str, Any]:
    """Cierra la corrida sin recuperar nada cuando la pregunta es inválida."""
    return {"answer": None, "error": state.get("error") or EMPTY_QUESTION_ERROR}
