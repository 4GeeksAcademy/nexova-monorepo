"""
Punto de entrada único del agente: ejecuta una corrida del grafo compilado,
con checkpoints (`thread_id = run_id`) y trace JSON persistido, incluso si
algún nodo falla. Las tools se autentican ante el MCP Server con su propio
token OAuth (`agent.tools.mcp_client`), no con el JWT de quien llama.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langgraph.graph.state import CompiledStateGraph

from agent.graph import get_agent_graph
from agent.guardrails.events import collect_events
from agent.guardrails.tenancy import INTERNAL_TENANT, Tenant
from agent.tracing import start_run


@dataclass(frozen=True)
class AgentRunResult:
    run_id: str
    answer: str | None
    error: str | None
    trace_path: Path
    conversation_id: str | None = None
    memory_proposal: dict[str, Any] | None = None
    memory_decision: dict[str, Any] | None = None
    # Veredicto del guard de entrada cuando bloqueó o redirigió (None si la consulta pasó).
    guardrail: dict[str, Any] | None = None
    guardrail_events: tuple[dict[str, Any], ...] = ()


def run_config(run_id: str) -> dict:
    return {"configurable": {"thread_id": run_id}}


def _compose_answer(notice: str | None, answer: str | None) -> str | None:
    """Aviso de la decisión sobre la propuesta (si lo hubo) + respuesta a la pregunta."""
    if notice and answer:
        return f"{notice}\n\n{answer}"
    return notice or answer


def run_agent(
    question: str,
    *,
    graph: CompiledStateGraph | None = None,
    conversation_id: str | None = None,
    user_id: str | None = None,
    tenant: Tenant = INTERNAL_TENANT,
) -> AgentRunResult:
    """Una corrida del grafo. `conversation_id` + `user_id` activan la memoria
    (propuesta pendiente de esa conversación); el `thread_id` sigue siendo el `run_id`.

    `tenant` limita los tickets a la empresa cliente de quien pregunta. La API
    siempre pasa el del usuario autenticado; el valor por defecto (personal
    interno) es solo para scripts y tests internos."""
    compiled = graph or get_agent_graph()
    run_id = str(uuid.uuid4())
    conversation_id = conversation_id or str(uuid.uuid4())
    initial: dict[str, Any] = {
        "question": question,
        "conversation_id": conversation_id,
        "user_id": user_id,
        "tenant": tenant.as_state(),
    }
    with start_run(run_id, question) as tracer, collect_events(run_id, conversation_id, user_id) as guard_log:
        try:
            state = compiled.invoke(initial, config=run_config(run_id))
        except Exception as exc:
            tracer.save(status="failed", final_answer=None, error=type(exc).__name__,
                        conversation_id=conversation_id,
                        guardrails=[e.to_dict() for e in guard_log.events])
            raise
        error = state.get("error")
        answer = None if error else _compose_answer(state.get("memory_notice"), state.get("answer"))
        memory = {
            "facts_used": [f["id"] for f in state.get("memory_facts") or []],
            "decision": state.get("memory_decision"),
            "proposal": state.get("memory_proposal"),
            "blocked": state.get("memory_blocked"),
        }
        guard = state.get("guard") or {}
        guard_events = tuple(e.to_dict() for e in guard_log.events)
        trace_path = tracer.save(
            status="rejected" if error else "completed",
            final_answer=answer,
            error=error,
            route=state.get("route"),
            sources_used=state.get("sources_used"),
            conversation_id=conversation_id,
            memory=memory if any(memory.values()) else None,
            guardrails=list(guard_events) if (guard or guard_events) else None,
            guard=guard or None,
        )
    return AgentRunResult(
        run_id=run_id,
        answer=answer,
        error=error,
        trace_path=trace_path,
        conversation_id=conversation_id,
        memory_proposal=state.get("memory_proposal"),
        memory_decision=state.get("memory_decision"),
        guardrail=guard if guard.get("action") in ("block", "redirect") else None,
        guardrail_events=guard_events,
    )
