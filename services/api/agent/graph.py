"""
Grafo del agente: nodos, aristas condicionales, compilación y checkpointer.

    START → receive_question ─┬─(pregunta vacía)→ reject → END
                              └→ route_question ─→ [lookup_tickets] → [lookup_inventory] → [retrieve] ─┬→ generate       → END
                                                                                                       ├→ tool_fallback  → END
                                                                                                       └→ no_information → END

`route_question` decide qué fuentes necesita la pregunta; después de él y de
cada fuente, `next_step` salta a la siguiente fuente pendiente (datos en vivo
primero, RAG después) o, si no queda ninguna, al nodo de respuesta:

- `generate` si alguna fuente aportó datos (chunks del RAG o una tool con `ok`);
- `tool_fallback` si no hay datos y alguna tool falló o no encontró el recurso;
- `no_information` si no hay datos y ninguna tool falló.

Con `memory` se añaden los nodos de memoria (`agent/memory/nodes.py`) y con
`guardrails` el harness (`agent/guardrails/nodes.py`): `guard_input` justo
después de `receive_question`, `guard_refuse` / `small_talk` sin pasar por las
fuentes, y `guard_output` antes de END (y antes de `propose_memory`).

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
from typing import TYPE_CHECKING, Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agent import nodes
from agent.nodes import ClassifyFn, GenerateFn, InventoryToolFn, RetrieveFn, TicketToolFn
from agent.routing import classify_question
from agent.tools.inventory import lookup_inventory
from agent.tools.tickets import lookup_tickets
from agent.state import AgentState
from agent.tracing import traced

if TYPE_CHECKING:
    from agent.guardrails import Guardrails
    from agent.memory.generation import StructuredGenerateFn
    from agent.memory.service import AgentMemory

_API_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CHECKPOINT_PATH = _API_DIR / "data" / "agent" / "checkpoints.sqlite"


class AgentGraphBuildError(RuntimeError):
    """El grafo tiene un error estructural y no puede compilarse."""


def route_after_receive(state: AgentState) -> str:
    return nodes.REJECT if state.get("error") else nodes.ROUTE_QUESTION


def next_step(state: AgentState) -> str:
    """Siguiente fuente pendiente según `route`, o el nodo de respuesta."""
    route = state.get("route", {})
    used = state.get("sources_used", [])
    for source, node in nodes.SOURCE_NODES.items():
        if route.get(source) and source not in used:
            return node

    tool_results = state.get("tool_results", [])
    if state.get("context") or state.get("memory_facts") or any(r["status"] == "ok" for r in tool_results):
        return nodes.GENERATE
    if tool_results:  # todas las tools fallaron o no encontraron nada, y el RAG no aportó
        return nodes.TOOL_FALLBACK
    return nodes.NO_INFORMATION


def next_step_with_memory(state: AgentState) -> str:
    """Como `next_step`, pero sin datos se pasa igualmente por `generate`.

    Con memoria, el mensaje puede ser una corrección o una preferencia que no
    está en ninguna fuente ("ese cliente siempre pide confirmar por email"):
    solo la generación auto-evalúa si vale la pena proponer recordarlo. El
    `SYSTEM_PROMPT` ya obliga a decir que no hay información si el CONTEXTO
    está vacío, así que la respuesta sigue siendo honesta.
    """
    step = next_step(state)
    return nodes.GENERATE if step == nodes.NO_INFORMATION else step


_ANSWER_NODES = [nodes.GENERATE, nodes.TOOL_FALLBACK, nodes.NO_INFORMATION]


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
    classify_fn: ClassifyFn | None = None,
    ticket_tool: TicketToolFn | None = None,
    inventory_tool: InventoryToolFn | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
    memory: "AgentMemory | None" = None,
    structured_generate_fn: "StructuredGenerateFn | None" = None,
    guardrails: "Guardrails | None" = None,
) -> CompiledStateGraph:
    """Construye y compila el grafo.

    Por defecto usa `retrieve`/`generate_answer` del RAG, el enrutador LLM con
    reglas de respaldo y las tools HTTP reales (incidentes e inventario).

    Con `memory` añade los nodos de memoria (`agent/memory/nodes.py`) y la
    generación estructurada (`structured_generate_fn`, por defecto
    `generate_with_memory`); sin él, la topología es la de la Parte 3.

    Con `guardrails` añade el harness (`agent/guardrails/nodes.py`): guard de
    entrada antes de todo, rechazo y small talk sin pasar por las fuentes,
    contexto externo neutralizado, prompt de soporte seguro y guard de salida
    antes de devolver (y antes de registrar una propuesta de memoria).
    """
    if retrieve_fn is None:
        from knowledge.pipeline_bridge import retrieve as retrieve_fn
    if guardrails is not None:
        from agent.guardrails import nodes as guard_nodes
        from agent.guardrails.generation import secure_generate, secure_structured_generate

        generate_fn = generate_fn or secure_generate
        if memory is not None and structured_generate_fn is None:
            structured_generate_fn = secure_structured_generate()
    elif memory is None and generate_fn is None:
        from knowledge.pipeline_bridge import generate_answer as generate_fn

    context_fn = guard_nodes.secure_generation_context if guardrails is not None else None
    node_fns: dict[str, Any] = {
        nodes.RECEIVE_QUESTION: nodes.receive_question,
        nodes.ROUTE_QUESTION: nodes.make_route_node(classify_fn or classify_question),
        nodes.LOOKUP_TICKETS: nodes.make_tickets_node(ticket_tool or lookup_tickets),
        nodes.LOOKUP_INVENTORY: nodes.make_inventory_node(inventory_tool or lookup_inventory),
        nodes.RETRIEVE: nodes.make_retrieve_node(
            retrieve_fn, guard_nodes.restricted_retrieve_filter if guardrails is not None else None
        ),
        nodes.TOOL_FALLBACK: nodes.tool_fallback,
        nodes.NO_INFORMATION: nodes.no_information,
        nodes.REJECT: nodes.reject,
    }
    step_fn = next_step
    if memory is None:
        node_fns[nodes.GENERATE] = nodes.make_generate_node(generate_fn, context_fn)
    else:
        from agent.memory import nodes as memory_nodes
        from agent.memory.generation import generate_with_memory

        step_fn = next_step_with_memory
        node_fns.update({
            memory_nodes.RESOLVE_PENDING: memory_nodes.make_resolve_pending_node(memory),
            memory_nodes.MEMORY_ACK: memory_nodes.memory_ack,
            memory_nodes.LOAD_MEMORY: memory_nodes.make_load_memory_node(memory),
            nodes.GENERATE: memory_nodes.make_structured_generate_node(
                structured_generate_fn or generate_with_memory, context_fn
            ),
            memory_nodes.PROPOSE_MEMORY: memory_nodes.make_propose_memory_node(memory),
        })
    if guardrails is not None:
        node_fns.update({
            guard_nodes.GUARD_INPUT: guard_nodes.make_guard_input_node(guardrails),
            guard_nodes.GUARD_REFUSE: guard_nodes.guard_refuse,
            guard_nodes.SMALL_TALK: guard_nodes.make_small_talk_node(guardrails),
            guard_nodes.GUARD_OUTPUT: guard_nodes.guard_output,
            nodes.NO_INFORMATION: guard_nodes.support_no_information,
        })

    builder = StateGraph(AgentState)
    for name, fn in node_fns.items():
        builder.add_node(name, traced(name, fn))

    # Tras recibir la pregunta: guard de entrada (si hay harness) → memoria (si hay) → enrutador.
    after_guard = memory_nodes.RESOLVE_PENDING if memory is not None else nodes.ROUTE_QUESTION
    first = guard_nodes.GUARD_INPUT if guardrails is not None else after_guard
    builder.add_edge(START, nodes.RECEIVE_QUESTION)
    builder.add_conditional_edges(
        nodes.RECEIVE_QUESTION, lambda s: nodes.REJECT if s.get("error") else first, [first, nodes.REJECT]
    )
    if guardrails is not None:
        def route_after_guard(state: AgentState) -> str:
            if guard_nodes.is_blocked(state):
                return guard_nodes.GUARD_REFUSE
            # Con memoria, lo casual pasa antes por la propuesta pendiente ("sí, guárdalo", "vale").
            if guard_nodes.is_casual(state) and memory is None:
                return guard_nodes.SMALL_TALK
            return after_guard

        builder.add_conditional_edges(
            guard_nodes.GUARD_INPUT, route_after_guard,
            sorted({guard_nodes.GUARD_REFUSE, guard_nodes.SMALL_TALK, after_guard}),
        )
    if memory is not None:
        # Primero se resuelve la propuesta pendiente; después se lee la memoria y se enruta.
        def after_resolve(state: AgentState) -> str:
            step = memory_nodes.route_after_resolve(state)
            if (guardrails is not None and step == memory_nodes.LOAD_MEMORY
                    and guard_nodes.is_casual(state) and not state.get("memory_decision")):
                return guard_nodes.SMALL_TALK
            return step

        resolve_targets = [memory_nodes.MEMORY_ACK, memory_nodes.LOAD_MEMORY]
        if guardrails is not None:
            resolve_targets.append(guard_nodes.SMALL_TALK)
        builder.add_conditional_edges(memory_nodes.RESOLVE_PENDING, after_resolve, resolve_targets)
        builder.add_edge(memory_nodes.LOAD_MEMORY, nodes.ROUTE_QUESTION)
    # Cada nodo solo puede avanzar hacia fuentes posteriores o hacia la respuesta.
    builder.add_conditional_edges(
        nodes.ROUTE_QUESTION,
        step_fn,
        [nodes.LOOKUP_TICKETS, nodes.LOOKUP_INVENTORY, nodes.RETRIEVE, *_ANSWER_NODES],
    )
    builder.add_conditional_edges(
        nodes.LOOKUP_TICKETS, step_fn, [nodes.LOOKUP_INVENTORY, nodes.RETRIEVE, *_ANSWER_NODES]
    )
    builder.add_conditional_edges(nodes.LOOKUP_INVENTORY, step_fn, [nodes.RETRIEVE, *_ANSWER_NODES])
    builder.add_conditional_edges(nodes.RETRIEVE, step_fn, _ANSWER_NODES)

    # Nodos que producen la respuesta. Con harness, todos pasan por `guard_output`;
    # con memoria, la propuesta se registra DESPUÉS de validar la respuesta.
    answers = [nodes.TOOL_FALLBACK, nodes.NO_INFORMATION, nodes.GENERATE]
    if memory is not None:
        answers.append(memory_nodes.MEMORY_ACK)
    if guardrails is not None:
        answers += [guard_nodes.GUARD_REFUSE, guard_nodes.SMALL_TALK]
        for node in answers:
            builder.add_edge(node, guard_nodes.GUARD_OUTPUT)
        if memory is not None:
            builder.add_conditional_edges(
                guard_nodes.GUARD_OUTPUT,
                lambda s: (memory_nodes.PROPOSE_MEMORY
                           if s.get("memory_candidate") and not s.get("output_blocked") else END),
                [memory_nodes.PROPOSE_MEMORY, END],
            )
            builder.add_edge(memory_nodes.PROPOSE_MEMORY, END)
        else:
            builder.add_edge(guard_nodes.GUARD_OUTPUT, END)
    else:
        if memory is not None:
            answers.remove(nodes.GENERATE)
            answers.append(memory_nodes.PROPOSE_MEMORY)
            builder.add_edge(nodes.GENERATE, memory_nodes.PROPOSE_MEMORY)
        for node in answers:
            builder.add_edge(node, END)
    builder.add_edge(nodes.REJECT, END)

    return compile_graph(builder, checkpointer)


def memory_enabled() -> bool:
    """Memoria activa en producción salvo `AGENT_MEMORY_ENABLED=false` o sin `DATABASE_URL`."""
    flag = os.environ.get("AGENT_MEMORY_ENABLED", "true").strip().lower()
    return flag not in ("0", "false", "no", "off") and bool(os.environ.get("DATABASE_URL"))


@lru_cache(maxsize=1)
def get_agent_graph() -> CompiledStateGraph:
    """Grafo de producción (RAG real + checkpoints SQLite + memoria en Postgres), compilado una vez."""
    memory = None
    if memory_enabled():
        from agent.memory.service import AgentMemory
        from agent.memory.store import PostgresMemoryStore

        memory = AgentMemory(PostgresMemoryStore())
    from agent.guardrails import Guardrails

    # El harness está siempre activo en producción (ticket #SEC-114).
    return build_graph(checkpointer=sqlite_checkpointer(), memory=memory, guardrails=Guardrails())
