"""
Nodos de memoria del grafo. Solo se añaden cuando `build_graph(memory=...)`
recibe un `AgentMemory`; sin él, el grafo es exactamente el de la Parte 3.

    receive_question → resolve_pending ─┬─(solo decidía)→ memory_ack → END
                                        └→ load_memory → route_question → … → generate → propose_memory → END

- `resolve_pending`: si hay propuesta pendiente, clasifica el mensaje contra
  ella y aplica la decisión antes de nada. Si el mensaje traía otra pregunta,
  el flujo sigue con esa pregunta.
- `load_memory`: lee los hechos aprobados afines (interfaz explícita del store).
- `generate` (variante estructurada): una sola llamada que devuelve respuesta
  + `propuesta_memoria` opcional.
- `propose_memory`: registra la propuesta como `pending` y añade la pregunta
  de confirmación a la respuesta. Nunca escribe en la memoria.

Un fallo de la base de datos de memoria nunca tumba la respuesta: se registra
en el log y el agente responde sin memoria (y sin aprobar nada).
"""

from __future__ import annotations

import logging
from typing import Any

from agent.memory.generation import StructuredGenerateFn
from agent.memory.schemas import MemoryCandidate
from agent.memory.service import AgentMemory, confirmation_question
from agent.state import AgentState

logger = logging.getLogger(__name__)

RESOLVE_PENDING = "resolve_pending"
MEMORY_ACK = "memory_ack"
LOAD_MEMORY = "load_memory"
PROPOSE_MEMORY = "propose_memory"


def _ids(state: AgentState) -> tuple[str, str] | None:
    conversation_id, user_id = state.get("conversation_id"), state.get("user_id")
    return (conversation_id, user_id) if conversation_id and user_id else None


def make_resolve_pending_node(memory: AgentMemory):
    def resolve_pending(state: AgentState) -> dict[str, Any]:
        """Evalúa el mensaje contra la propuesta pendiente (si la hay) antes de responder."""
        ids = _ids(state)
        if ids is None:
            return {"memory_done": False}
        try:
            resolution = memory.resolve_pending(*ids, state["question"])
        except Exception as exc:  # BD caída: no se decide nada, se responde sin memoria
            logger.warning("No se pudo resolver la propuesta pendiente (%s)", type(exc).__name__)
            return {"memory_done": False}
        if resolution is None:
            return {"memory_done": False}
        remaining = (resolution.remaining_question or "").strip()
        update: dict[str, Any] = {
            "memory_decision": {
                "proposal_id": resolution.proposal.id,
                "outcome": resolution.outcome,
                "label": resolution.label,
                "fact_id": resolution.fact_id,
            },
            "memory_notice": resolution.notice,
            "memory_done": not remaining,
        }
        if remaining:
            update["question"] = remaining
        return update

    return resolve_pending


def route_after_resolve(state: AgentState) -> str:
    return MEMORY_ACK if state.get("memory_done") else LOAD_MEMORY


def memory_ack(state: AgentState) -> dict[str, Any]:
    """El mensaje solo respondía a la propuesta: se confirma qué se hizo y se termina."""
    return {"answer": state.get("memory_notice") or "", "memory_notice": None}


def make_load_memory_node(memory: AgentMemory):
    def load_memory(state: AgentState) -> dict[str, Any]:
        """Lee de la memoria los hechos aprobados afines a la pregunta."""
        try:
            facts = memory.read_relevant(state["question"])
        except Exception as exc:
            logger.warning("No se pudo leer la memoria (%s); se responde sin ella", type(exc).__name__)
            facts = []
        return {
            "memory_facts": [
                {"id": f.id, "fact": f.fact, "business_line": f.business_line, "category": f.category}
                for f in facts
            ]
        }

    return load_memory


def make_structured_generate_node(generate_fn: StructuredGenerateFn):
    from agent.nodes import build_generation_context

    def generate(state: AgentState) -> dict[str, Any]:
        """Respuesta + auto-evaluación de memoria en una sola llamada al LLM."""
        result = generate_fn(state["question"], build_generation_context(state))
        candidate = result.propuesta_memoria
        return {
            "answer": result.respuesta,
            "memory_candidate": candidate.model_dump() if candidate else None,
        }

    return generate


def make_propose_memory_node(memory: AgentMemory):
    def propose_memory(state: AgentState) -> dict[str, Any]:
        """Registra la propuesta (pendiente) y la pregunta al usuario en la misma respuesta."""
        raw = state.get("memory_candidate")
        ids = _ids(state)
        if not raw or ids is None:
            return {"memory_proposal": None}
        try:
            outcome = memory.propose(*ids, MemoryCandidate.model_validate(raw), state["question"])
        except Exception as exc:
            logger.warning("No se pudo registrar la propuesta (%s)", type(exc).__name__)
            return {"memory_proposal": None}
        if outcome.proposal is None:
            return {"memory_proposal": None, "memory_blocked": outcome.blocked_code}
        proposal = outcome.proposal
        return {
            "answer": (state.get("answer") or "") + confirmation_question(proposal),
            "memory_proposal": {
                "id": proposal.id,
                "fact": proposal.fact,
                "business_line": proposal.business_line,
                "category": proposal.category,
            },
        }

    return propose_memory
