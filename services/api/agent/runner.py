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

from langgraph.graph.state import CompiledStateGraph

from agent.graph import get_agent_graph
from agent.tracing import start_run


@dataclass(frozen=True)
class AgentRunResult:
    run_id: str
    answer: str | None
    error: str | None
    trace_path: Path


def run_config(run_id: str) -> dict:
    return {"configurable": {"thread_id": run_id}}


def run_agent(
    question: str,
    *,
    graph: CompiledStateGraph | None = None,
) -> AgentRunResult:
    compiled = graph or get_agent_graph()
    run_id = str(uuid.uuid4())
    with start_run(run_id, question) as tracer:
        try:
            state = compiled.invoke({"question": question}, config=run_config(run_id))
        except Exception as exc:
            tracer.save(status="failed", final_answer=None, error=type(exc).__name__)
            raise
        error = state.get("error")
        answer = state.get("answer")
        trace_path = tracer.save(
            status="rejected" if error else "completed",
            final_answer=answer,
            error=error,
            route=state.get("route"),
            sources_used=state.get("sources_used"),
        )
    return AgentRunResult(run_id=run_id, answer=answer, error=error, trace_path=trace_path)
