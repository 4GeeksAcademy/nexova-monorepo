"""
Evals de la Parte 2 del agente: tools fuera del RAG y enrutamiento. Se ejecutan con:

    uv run --project services/api pytest tests/pipelines/test_agent_tools_evals.py -v

- Evals de flujo (T1–T8, T10): las tools REALES (`lookup_tickets`,
  `lookup_inventory`) llaman al MCP Server `mcps/nexova-tools` a través de un
  `McpToolCaller` falso con el mismo contrato que el servidor (nombres de
  tool, argumentos, `structuredContent` y códigos de error). El cliente MCP
  real (OAuth + `langchain-mcp-adapters`) se prueba en
  `test_agent_mcp_client.py`. El enrutador LLM se sustituye por decisiones
  fijas, salvo en T8, que comprueba el respaldo por reglas. Cada eval corre el
  grafo y verifica sobre el trace JSON.
- Evals con traces REALES (T9): leen corridas grabadas con
  `scripts/agent_record_traces.py` contra la API, Supabase, Qdrant y los
  modelos de 4Geeks, con el enrutador LLM real.
"""

from __future__ import annotations

import json
import sys
from functools import partial
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
API_DIR = str(ROOT / "services" / "api")
if API_DIR not in sys.path:
    sys.path.append(API_DIR)

from agent import nodes  # noqa: E402
from agent.graph import build_graph, sqlite_checkpointer  # noqa: E402
from agent.routing import RouteDecision, classify_question, classify_with_rules  # noqa: E402
from agent.runner import run_agent  # noqa: E402
from agent.tools.inventory import lookup_inventory  # noqa: E402
from agent.tools.tickets import lookup_tickets  # noqa: E402
from agent.tools.transport import ToolCallError  # noqa: E402
from agent.tracing import load_trace  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "agent_traces"

TICKET = {
    "ticket_id": "NXV-000482",
    "date": "2024-03-14",
    "client_company": "FinServ Group",
    "category": "ACCESS",
    "description": "Role permissions not updated after department change",
    "agent_id": "AGT-08",
    "status": "OPEN",
    "satisfaction_score": None,
    "updated_at": "2026-11-12T10:00:00Z",
}
PRODUCTS = [
    {"id": 1, "name": "Portátil Dell Latitude", "sku": "LAP-DELL-01", "category": "it_and_software_licenses",
     "office": "Madrid", "current_stock": 3, "min_stock_threshold": 5, "programme_id": None},
    {"id": 2, "name": "Silla ergonómica", "sku": "CHR-ERG-01", "category": "office_and_facilities",
     "office": "Madrid", "current_stock": 12, "min_stock_threshold": 4, "programme_id": None},
]
POLICY_CHUNK = {
    "source_document": "hiring-process-sla",
    "section": "Tiempos de entrega",
    "text": "Los tiempos de entrega son promedios, no garantías.",
}


class FakeServices:
    """MCP Server falso: mismas tools, argumentos y `structuredContent` que `mcps/nexova-tools`."""

    def __init__(self, *, incidents_down: bool = False, inventory_down: bool = False) -> None:
        self.incidents_down = incidents_down
        self.inventory_down = inventory_down
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, arguments))
        if name in ("get_ticket", "list_tickets"):
            if self.incidents_down:
                raise ToolCallError("timeout", "Sin respuesta en 4 s")
            if name == "get_ticket":
                if arguments["ticket_id"] != "NXV-000482":
                    raise ToolCallError("not_found", "MCP NOT_FOUND")
                return TICKET
            return {"count": 1, "tickets": [TICKET]}
        if name == "query_inventory":
            if self.inventory_down:
                raise ToolCallError("error", "MCP UPSTREAM_UNAVAILABLE")
            term = (arguments.get("search") or "").casefold().split()
            match = [p for p in PRODUCTS if all(w[:4] in p["name"].casefold() for w in term)]
            return {"count": len(match), "truncated": False, "products": match}
        raise AssertionError(f"tool inesperada: {name}")

class FakeRAG:
    def __init__(self, context: list[dict[str, Any]]) -> None:
        self.context = context
        self.retrieve_calls: list[str] = []
        self.generate_calls: list[list[dict[str, Any]]] = []

    def retrieve(self, question: str) -> list[dict[str, Any]]:
        self.retrieve_calls.append(question)
        return self.context

    def generate_answer(self, question: str, context: list[dict[str, Any]]) -> str:
        self.generate_calls.append(context)
        return "Respuesta generada con: " + " | ".join(c["text"] for c in context)


@pytest.fixture(autouse=True)
def trace_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("AGENT_TRACE_DIR", str(tmp_path / "traces"))
    return tmp_path / "traces"


def _run(question: str, route: RouteDecision | None, services: FakeServices, rag: FakeRAG, *, classify=None):
    graph = build_graph(
        retrieve_fn=rag.retrieve,
        generate_fn=rag.generate_answer,
        classify_fn=classify or (lambda _q: route),
        ticket_tool=partial(lookup_tickets, call_tool=services.call_tool),
        inventory_tool=partial(lookup_inventory, call_tool=services.call_tool),
        checkpointer=sqlite_checkpointer(":memory:"),
    )
    result = run_agent(question, graph=graph)
    return result, load_trace(result.run_id)


def _tool_step(trace: dict[str, Any], node: str) -> dict[str, Any]:
    step = next(s for s in trace["steps"] if s["node"] == node)
    return step["output"]["tool_results"][-1]


# --- Enrutamiento ----------------------------------------------------------------------


def test_T1_ticket_question_uses_ticket_tool_not_rag() -> None:
    services, rag = FakeServices(), FakeRAG([POLICY_CHUNK])
    result, trace = _run(
        "¿En qué estado está el ticket NXV-000482?",
        RouteDecision(tickets=True, ticket_id="NXV-000482", method="llm"),
        services,
        rag,
    )

    assert trace["sources_used"] == ["tickets"]
    assert trace["nodes"] == [
        nodes.RECEIVE_QUESTION, nodes.ROUTE_QUESTION, nodes.LOOKUP_TICKETS, nodes.GENERATE,
    ]
    assert nodes.RETRIEVE not in trace["nodes"] and rag.retrieve_calls == []
    tool = _tool_step(trace, nodes.LOOKUP_TICKETS)
    assert tool["status"] == "ok" and tool["tickets"][0]["status"] == "OPEN"
    # La respuesta se genera con el dato en vivo del gestor, no con la base de conocimiento.
    assert "estado: OPEN" in result.answer


def test_T2_policy_question_uses_rag_not_tools() -> None:
    services, rag = FakeServices(), FakeRAG([POLICY_CHUNK])
    _, trace = _run(
        "¿Los tiempos de entrega son garantizados?",
        RouteDecision(rag=True, method="llm"),
        services,
        rag,
    )

    assert trace["sources_used"] == ["rag"]
    assert nodes.LOOKUP_TICKETS not in trace["nodes"]
    assert nodes.LOOKUP_INVENTORY not in trace["nodes"]
    assert services.calls == []  # ninguna llamada al MCP Server
    assert trace["nodes"][-1] == nodes.GENERATE


def test_T5_mixed_question_uses_tool_then_rag_in_order() -> None:
    services, rag = FakeServices(), FakeRAG([POLICY_CHUNK])
    result, trace = _run(
        "El ticket NXV-000482 sigue abierto, ¿qué plazos le podemos prometer al cliente?",
        RouteDecision(rag=True, tickets=True, ticket_id="NXV-000482", method="llm"),
        services,
        rag,
    )

    assert trace["sources_used"] == ["tickets", "rag"]
    assert trace["nodes"].index(nodes.LOOKUP_TICKETS) < trace["nodes"].index(nodes.RETRIEVE)
    sources = [c["source_document"] for c in rag.generate_calls[0]]
    assert sources == [nodes.TICKETS_SOURCE_LABEL, "hiring-process-sla"]
    assert "estado: OPEN" in result.answer and "promedios" in result.answer


def test_T6_stock_question_uses_inventory_tool() -> None:
    services, rag = FakeServices(), FakeRAG([POLICY_CHUNK])
    result, trace = _run(
        "¿Tenemos stock de portátiles Dell?",
        RouteDecision(inventory=True, product="Dell Latitude", method="llm"),
        services,
        rag,
    )

    assert trace["sources_used"] == ["inventory"]
    tool = _tool_step(trace, nodes.LOOKUP_INVENTORY)
    assert tool["status"] == "ok"
    assert [p["sku"] for p in tool["products"]] == ["LAP-DELL-01"]
    assert tool["products"][0]["below_threshold"] is True
    assert "stock actual 3" in result.answer and rag.retrieve_calls == []


# --- Fallback ---------------------------------------------------------------------------


def test_T3_incidents_timeout_routes_to_honest_fallback() -> None:
    services, rag = FakeServices(incidents_down=True), FakeRAG([POLICY_CHUNK])
    result, trace = _run(
        "¿En qué estado está el ticket NXV-000482?",
        RouteDecision(tickets=True, ticket_id="NXV-000482", method="llm"),
        services,
        rag,
    )

    assert trace["status"] == "completed"  # la corrida no se rompe
    assert trace["nodes"][-1] == nodes.TOOL_FALLBACK
    assert _tool_step(trace, nodes.LOOKUP_TICKETS)["status"] == "timeout"
    assert rag.generate_calls == []  # sin LLM: nada que pueda inventar un estado
    assert "No pude confirmar el estado del ticket NXV-000482 ahora mismo" in result.answer
    for invented in ("OPEN", "CLOSED", "DISCARDED", "abierto", "cerrado"):
        assert invented not in result.answer


def test_T4_unknown_ticket_says_it_was_not_found() -> None:
    services, rag = FakeServices(), FakeRAG([])
    result, trace = _run(
        "¿Cómo va el ticket NXV-999999?",
        RouteDecision(tickets=True, ticket_id="NXV-999999", method="llm"),
        services,
        rag,
    )

    assert _tool_step(trace, nodes.LOOKUP_TICKETS)["status"] == "not_found"
    assert trace["nodes"][-1] == nodes.TOOL_FALLBACK
    assert result.answer.startswith("No encontré el ticket NXV-999999")


def test_tool_failure_in_mixed_question_is_stated_not_invented() -> None:
    services, rag = FakeServices(incidents_down=True), FakeRAG([POLICY_CHUNK])
    result, trace = _run(
        "El ticket NXV-000482, ¿qué plazos aplican?",
        RouteDecision(rag=True, tickets=True, ticket_id="NXV-000482", method="llm"),
        services,
        rag,
    )

    # Hay contexto del RAG, así que se genera, pero con la advertencia explícita.
    assert trace["nodes"][-1] == nodes.GENERATE
    warning = rag.generate_calls[0][0]
    assert warning["source_document"] == nodes.TICKETS_SOURCE_LABEL
    assert "no se pudo consultar" in warning["text"] and "No inventes" in warning["text"]


def test_inventory_down_routes_to_honest_fallback() -> None:
    services, rag = FakeServices(inventory_down=True), FakeRAG([])
    result, trace = _run(
        "¿Tenemos sillas ergonómicas en stock?",
        RouteDecision(inventory=True, product="silla ergonómica", method="llm"),
        services,
        rag,
    )

    assert _tool_step(trace, nodes.LOOKUP_INVENTORY)["status"] == "error"
    assert trace["nodes"][-1] == nodes.TOOL_FALLBACK
    assert "No pude confirmar el stock" in result.answer


# --- Contrato de las tools ---------------------------------------------------------------


def test_T7_tools_are_read_only() -> None:
    services, rag = FakeServices(), FakeRAG([POLICY_CHUNK])
    _run(
        "Ticket NXV-000482 y stock de sillas, ¿qué dice la política?",
        RouteDecision(rag=True, tickets=True, inventory=True, ticket_id="NXV-000482",
                      product="silla", method="llm"),
        services,
        rag,
    )

    # Solo tools MCP de lectura; nunca create_ticket / update_ticket_status ni escrituras de inventario.
    assert services.calls == [
        ("get_ticket", {"ticket_id": "NXV-000482"}),
        ("query_inventory", {"action": "list", "limit": 20, "search": "silla"}),
    ]


def test_T10_agent_has_a_single_path_through_mcp() -> None:
    """Sin JWT reenviado ni HTTP directo: el único camino es el MCP con scopes de lectura."""
    import inspect

    from agent.tools import mcp_client, transport

    assert "auth_token" not in inspect.signature(run_agent).parameters
    assert not hasattr(transport, "http_get") and not hasattr(transport, "forwarded_credentials")
    assert mcp_client.DEFAULT_SCOPES.split() == ["incidents:read", "inventory:read"]
    assert lookup_tickets.__kwdefaults__ == {"call_tool": None}  # por defecto: call_mcp_tool
    assert lookup_inventory.__kwdefaults__ == {"call_tool": None}


def test_tool_timeout_is_numeric_and_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent.tools import transport

    assert transport.tool_timeout_seconds() == 4.0
    monkeypatch.setenv("AGENT_TOOL_TIMEOUT_SECONDS", "2.5")
    assert transport.tool_timeout_seconds() == 2.5
    monkeypatch.setenv("AGENT_TOOL_TIMEOUT_SECONDS", "no-es-un-numero")
    assert transport.tool_timeout_seconds() == 4.0


# --- Enrutador -------------------------------------------------------------------------


def test_T8_router_falls_back_to_rules_when_llm_fails() -> None:
    def broken_llm(_question: str) -> RouteDecision:
        raise ConnectionError("LLM caído")

    services, rag = FakeServices(), FakeRAG([POLICY_CHUNK])
    _, trace = _run(
        "¿En qué estado está el ticket 482?",
        None,
        services,
        rag,
        classify=partial(classify_question, llm_classifier=broken_llm),
    )

    assert trace["route"]["method"] == "rules"
    assert trace["route"]["ticket_id"] == "NXV-000482"
    assert trace["sources_used"] == ["tickets"]


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("¿En qué estado está el ticket NXV-000482?", {"tickets"}),
        ("¿Cómo va la incidencia 17?", {"tickets"}),
        ("¿Cuántos tickets abiertos hay?", {"tickets"}),
        ("¿Tenemos stock de sillas ergonómicas?", {"inventory"}),
        ("¿Cuál es la tarifa del headhunting ejecutivo?", {"rag"}),
    ],
)
def test_rule_router_picks_the_right_source(question: str, expected: set[str]) -> None:
    decision = classify_with_rules(question)
    assert {s for s in ("rag", "tickets", "inventory") if getattr(decision, s)} == expected


def test_explicit_ticket_id_overrides_llm_omission() -> None:
    lazy_llm = lambda _q: RouteDecision(rag=True, method="llm")  # noqa: E731
    decision = classify_question("¿Y el NXV-000482?", llm_classifier=lazy_llm)
    assert decision.tickets and decision.ticket_id == "NXV-000482" and decision.method == "llm"


# --- Traces reales (grabados con la API, Supabase, Qdrant y el LLM real) ------------------


def _fixture(name: str) -> dict[str, Any]:
    path = FIXTURES / f"{name}.json"
    if not path.exists():
        pytest.fail(f"Falta {path.name}: grábalo con scripts/agent_record_traces.py")
    return json.loads(path.read_text(encoding="utf-8"))


def test_T9_real_ticket_run_reports_the_live_status() -> None:
    trace = _fixture("ticket-status")

    assert trace["route"]["tickets"] is True and trace["route"]["rag"] is False
    assert trace["sources_used"] == ["tickets"]
    tool = _tool_step(trace, nodes.LOOKUP_TICKETS)
    assert tool["status"] == "ok"
    live_status = tool["tickets"][0]["status"]
    assert live_status in trace["final_answer"] or {
        "OPEN": "abierto", "CLOSED": "cerrado", "DISCARDED": "descartado"
    }[live_status] in trace["final_answer"].lower()


def test_T9_real_policy_run_uses_only_the_rag() -> None:
    trace = _fixture("rag-policy")

    assert trace["sources_used"] == ["rag"]
    assert nodes.LOOKUP_TICKETS not in trace["nodes"]
    assert nodes.LOOKUP_INVENTORY not in trace["nodes"]


def test_T9_real_mixed_run_uses_ticket_then_rag() -> None:
    trace = _fixture("ticket-plus-policy")

    assert trace["sources_used"] == ["tickets", "rag"]
    assert trace["nodes"][-1] == nodes.GENERATE


def test_T9_real_stock_run_uses_inventory() -> None:
    trace = _fixture("stock")

    assert trace["sources_used"] == ["inventory"]
    assert _tool_step(trace, nodes.LOOKUP_INVENTORY)["status"] in {"ok", "not_found"}
