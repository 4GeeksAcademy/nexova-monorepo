"""
Grafo del agente: nodos, aristas condicionales, compilación y checkpointer.

    START → receive_question ─┬─(pregunta vacía)──→ reject ─────────→ END
                              └─(pregunta válida)─→ retrieve ─┬─(sin contexto)─→ no_information → END
                                                              └─(con contexto)─→ generate ───────→ END

La compilación es explícita (`compile_graph`) y falla con `AgentGraphBuildError`
ante un error estructural: los que detecta LangGraph (arista hacia un nodo
inexistente, sin punto de entrada…) y los que no detecta (nodo inalcanzable
desde START o sin camino hasta END).
"""

from __future__ import annotations

import os
import sqlite3
from collections import deque
from functools import lru_cache
from pathlib import Path
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agent import nodes
from agent.nodes import GenerateFn, RetrieveFn
from agent.state import AgentState
from agent.tracing import traced

_API_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CHECKPOINT_PATH = _API_DIR / "data" / "agent" / "checkpoints.sqlite"


class AgentGraphBuildError(RuntimeError):
    """El grafo tiene un error estructural y no puede compilarse."""


def route_after_receive(state: AgentState) -> str:
    return nodes.REJECT if state.get("error") else nodes.RETRIEVE


def route_after_retrieve(state: AgentState) -> str:
    return nodes.GENERATE if state.get("context") else nodes.NO_INFORMATION


def sqlite_checkpointer(path: str | Path | None = None) -> SqliteSaver:
    """Checkpointer SQLite: `AGENT_CHECKPOINT_PATH`, o `:memory:` para tests."""
    target = str(path or os.environ.get("AGENT_CHECKPOINT_PATH", DEFAULT_CHECKPOINT_PATH))
    if target != ":memory:":
        Path(target).parent.mkdir(parents=True, exist_ok=True)
    # FastAPI ejecuta los endpoints síncronos en un threadpool; SqliteSaver
    # serializa el acceso con su propio lock.
    return SqliteSaver(sqlite3.connect(target, check_same_thread=False))


def _check_connectivity(compiled: CompiledStateGraph) -> None:
    drawable = compiled.get_graph()
    node_ids = set(drawable.nodes)
    forward: dict[str, set[str]] = {n: set() for n in node_ids}
    backward: dict[str, set[str]] = {n: set() for n in node_ids}
    for edge in drawable.edges:
        forward[edge.source].add(edge.target)
        backward[edge.target].add(edge.source)

    def reachable(start: str, adjacency: dict[str, set[str]]) -> set[str]:
        seen, queue = {start}, deque([start])
        while queue:
            for nxt in adjacency[queue.popleft()]:
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        return seen

    unreachable = node_ids - reachable(START, forward)
    dead_ends = node_ids - reachable(END, backward) if END in node_ids else node_ids - {START}
    problems = []
    if unreachable:
        problems.append(f"nodos inalcanzables desde START: {sorted(unreachable)}")
    if dead_ends:
        problems.append(f"nodos sin camino hasta END: {sorted(dead_ends)}")
    if problems:
        raise AgentGraphBuildError("Grafo del agente inválido — " + "; ".join(problems))


def compile_graph(
    builder: StateGraph, checkpointer: BaseCheckpointSaver | None = None
) -> CompiledStateGraph:
    """Compila el grafo y valida su estructura antes de cualquier ejecución."""
    try:
        compiled = builder.compile(checkpointer=checkpointer)
    except ValueError as exc:
        raise AgentGraphBuildError(f"Grafo del agente inválido — {exc}") from exc
    _check_connectivity(compiled)
    return compiled


def build_graph(
    *,
    retrieve_fn: RetrieveFn | None = None,
    generate_fn: GenerateFn | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph:
    """Construye y compila el grafo. Por defecto usa `retrieve`/`generate_answer` del RAG."""
    if retrieve_fn is None or generate_fn is None:
        from knowledge.pipeline_bridge import generate_answer, retrieve

        retrieve_fn = retrieve_fn or retrieve
        generate_fn = generate_fn or generate_answer

    node_fns: dict[str, Any] = {
        nodes.RECEIVE_QUESTION: nodes.receive_question,
        nodes.RETRIEVE: nodes.make_retrieve_node(retrieve_fn),
        nodes.GENERATE: nodes.make_generate_node(generate_fn),
        nodes.NO_INFORMATION: nodes.no_information,
        nodes.REJECT: nodes.reject,
    }

    builder = StateGraph(AgentState)
    for name, fn in node_fns.items():
        builder.add_node(name, traced(name, fn))

    builder.add_edge(START, nodes.RECEIVE_QUESTION)
    builder.add_conditional_edges(
        nodes.RECEIVE_QUESTION, route_after_receive, [nodes.RETRIEVE, nodes.REJECT]
    )
    builder.add_conditional_edges(
        nodes.RETRIEVE, route_after_retrieve, [nodes.GENERATE, nodes.NO_INFORMATION]
    )
    builder.add_edge(nodes.GENERATE, END)
    builder.add_edge(nodes.NO_INFORMATION, END)
    builder.add_edge(nodes.REJECT, END)

    return compile_graph(builder, checkpointer)


@lru_cache(maxsize=1)
def get_agent_graph() -> CompiledStateGraph:
    """Grafo de producción (RAG real + checkpoints SQLite), compilado una sola vez."""
    return build_graph(checkpointer=sqlite_checkpointer())
