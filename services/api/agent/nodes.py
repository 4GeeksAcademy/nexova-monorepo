"""
Nodos del grafo. Cada uno tiene una sola responsabilidad y devuelve solo la
parte del estado que modifica.

Contrato con el RAG (`data/pipelines/rag.py`): el nodo `retrieve` llama a
`retrieve()` y el nodo `generate` llama a `generate_answer(question, context)`
con el contexto ya reunido. Ningún nodo llama a `query()`. Los resultados de
las tools entran en ese mismo `context` como chunks marcados "(en vivo)", así
la respuesta se genera siempre con la misma función y las mismas reglas.

Las funciones externas (retrieve, generación, tools, enrutador) se inyectan
para poder probar cada nodo de forma aislada.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent.routing import RouteDecision
from agent.state import AgentState
from agent.tools.inventory import InventoryQuery, InventoryToolResult
from agent.tools.tickets import TicketQuery, TicketToolResult

RetrieveFn = Callable[[str], list[dict[str, Any]]]
GenerateFn = Callable[[str, list[dict[str, Any]]], str]
ClassifyFn = Callable[[str], RouteDecision]
TicketToolFn = Callable[[TicketQuery], TicketToolResult]
InventoryToolFn = Callable[[InventoryQuery], InventoryToolResult]
Node = Callable[[AgentState], dict[str, Any]]

RECEIVE_QUESTION = "receive_question"
ROUTE_QUESTION = "route_question"
LOOKUP_TICKETS = "lookup_tickets"
LOOKUP_INVENTORY = "lookup_inventory"
RETRIEVE = "retrieve"
GENERATE = "generate"
NO_INFORMATION = "no_information"
TOOL_FALLBACK = "tool_fallback"
REJECT = "reject"

# Fuente lógica que cubre cada nodo de consulta, en el orden en que se consultan:
# primero los datos en vivo, después la base de conocimiento.
SOURCE_NODES: dict[str, str] = {
    "tickets": LOOKUP_TICKETS,
    "inventory": LOOKUP_INVENTORY,
    "rag": RETRIEVE,
}

EMPTY_QUESTION_ERROR = "empty_question"
EMPTY_QUESTION_MESSAGE = "La pregunta está vacía: escribe qué quieres consultar."

NO_INFORMATION_ANSWER = (
    "La base de conocimiento de Nexova no tiene información suficiente sobre eso. "
    "Te sugiero consultarlo con un account manager senior antes de responder al prospecto."
)

TICKETS_SOURCE_LABEL = "Gestor de incidentes (en vivo)"
INVENTORY_SOURCE_LABEL = "Gestor de inventario (en vivo)"


# --- Recepción y enrutamiento ---------------------------------------------------------


def receive_question(state: AgentState) -> dict[str, Any]:
    """Normaliza la pregunta y marca el error si queda vacía."""
    question = (state.get("question") or "").strip()
    return {
        "question": question,
        "error": None if question else EMPTY_QUESTION_ERROR,
        "sources_used": [],
        "tool_results": [],
    }


def make_route_node(classify_fn: ClassifyFn) -> Node:
    def route_question(state: AgentState) -> dict[str, Any]:
        """Decide qué fuentes necesita la pregunta (RAG, tickets, inventario)."""
        return {"route": classify_fn(state["question"]).model_dump()}

    return route_question


# --- Fuentes ---------------------------------------------------------------------------


def _record(state: AgentState, source: str, result: dict[str, Any] | None = None) -> dict[str, Any]:
    update: dict[str, Any] = {"sources_used": [*state.get("sources_used", []), source]}
    if result is not None:
        update["tool_results"] = [*state.get("tool_results", []), result]
    return update


def make_tickets_node(ticket_tool: TicketToolFn) -> Node:
    def lookup_tickets(state: AgentState) -> dict[str, Any]:
        """Consulta en vivo el gestor de incidentes (solo lectura)."""
        route = state.get("route", {})
        query = TicketQuery(
            ticket_id=route.get("ticket_id"),
            status=route.get("ticket_status"),
            category=route.get("ticket_category"),
            client_company=route.get("client_company"),
        )
        return _record(state, "tickets", ticket_tool(query).model_dump(mode="json"))

    return lookup_tickets


def make_inventory_node(inventory_tool: InventoryToolFn) -> Node:
    def lookup_inventory(state: AgentState) -> dict[str, Any]:
        """Consulta en vivo el stock del gestor de inventario (solo lectura)."""
        product = (state.get("route", {}).get("product") or "").strip() or None
        query = InventoryQuery(product=product if product and len(product) >= 2 else None)
        return _record(state, "inventory", inventory_tool(query).model_dump(mode="json"))

    return lookup_inventory


def make_retrieve_node(retrieve_fn: RetrieveFn) -> Node:
    def retrieve(state: AgentState) -> dict[str, Any]:
        """Recupera los chunks relevantes (ya filtrados por `min_score`)."""
        return {"context": retrieve_fn(state["question"]), **_record(state, "rag")}

    return retrieve


# --- Respuesta -------------------------------------------------------------------------


def _ticket_chunks(result: dict[str, Any]) -> list[dict[str, Any]]:
    if result["status"] != "ok":
        ticket_id = result["query"].get("ticket_id")
        what = f"el ticket {ticket_id}" if ticket_id else "los tickets solicitados"
        reason = "no existe" if result["status"] == "not_found" else "no se pudo consultar ahora mismo"
        return [{
            "source_document": TICKETS_SOURCE_LABEL,
            "section": "Resultado de la consulta",
            "text": f"Consulta en vivo: {what} {reason}. No inventes su estado ni sus datos.",
        }]
    return [
        {
            "source_document": TICKETS_SOURCE_LABEL,
            "section": f"Ticket {t['ticket_id']}",
            "text": (
                f"Ticket {t['ticket_id']} — estado: {t['status']}; categoría: {t['category']}; "
                f"cliente: {t['client_company']}; fecha: {t['date']}; agente: {t['agent_id']}; "
                f"satisfacción: {t['satisfaction_score'] if t['satisfaction_score'] is not None else 'sin valorar'}; "
                f"descripción: {t['description']}"
            ),
        }
        for t in result["tickets"]
    ]


def _inventory_chunks(result: dict[str, Any]) -> list[dict[str, Any]]:
    if result["status"] != "ok":
        product = result["query"].get("product")
        what = f"el producto «{product}»" if product else "el inventario"
        reason = "no aparece en el inventario" if result["status"] == "not_found" else "no se pudo consultar ahora mismo"
        return [{
            "source_document": INVENTORY_SOURCE_LABEL,
            "section": "Resultado de la consulta",
            "text": f"Consulta en vivo: {what} {reason}. No inventes cifras de stock.",
        }]
    return [
        {
            "source_document": INVENTORY_SOURCE_LABEL,
            "section": f"{p['name']} ({p['sku']})",
            "text": (
                f"{p['name']} (SKU {p['sku']}, oficina {p['office']}): stock actual {p['current_stock']} "
                f"unidades; mínimo {p['min_stock_threshold']}"
                f"{' — POR DEBAJO DEL MÍNIMO' if p['below_threshold'] else ''}."
            ),
        }
        for p in result["products"]
    ]


def build_generation_context(state: AgentState) -> list[dict[str, Any]]:
    """Datos en vivo de las tools (en el orden consultado) + chunks del RAG."""
    chunks: list[dict[str, Any]] = []
    for result in state.get("tool_results", []):
        chunks += _ticket_chunks(result) if result["tool"] == "tickets" else _inventory_chunks(result)
    return chunks + list(state.get("context") or [])


def make_generate_node(generate_fn: GenerateFn) -> Node:
    def generate(state: AgentState) -> dict[str, Any]:
        """Genera la respuesta a partir del contexto ya reunido (tools + RAG)."""
        return {"answer": generate_fn(state["question"], build_generation_context(state))}

    return generate


def no_information(state: AgentState) -> dict[str, Any]:
    """Responde con honestidad cuando ninguna fuente aporta información."""
    return {"answer": NO_INFORMATION_ANSWER}


def _fallback_line(result: dict[str, Any]) -> str:
    query, status = result["query"], result["status"]
    if result["tool"] == "tickets":
        ticket_id = query.get("ticket_id")
        if status == "not_found":
            what = f"el ticket {ticket_id}" if ticket_id else "tickets que coincidan con la búsqueda"
            return f"No encontré {what} en el gestor de incidentes. Revisa el número e inténtalo de nuevo."
        whose = f"del ticket {ticket_id}" if ticket_id else "de esos tickets"
        return (
            f"No pude confirmar el estado {whose} ahora mismo: el gestor de incidentes no respondió. "
            "Inténtalo de nuevo en unos minutos o consúltalo directamente en el gestor."
        )
    what = f"«{query['product']}»" if query.get("product") else "ese producto"
    if status == "not_found":
        return f"No encontré {what} en el gestor de inventario."
    return (
        f"No pude confirmar el stock de {what} ahora mismo: el gestor de inventario no respondió. "
        "Inténtalo de nuevo en unos minutos."
    )


def tool_fallback(state: AgentState) -> dict[str, Any]:
    """Respuesta honesta cuando una tool falló o no encontró el recurso. Sin LLM."""
    failed = [r for r in state.get("tool_results", []) if r["status"] != "ok"]
    return {"answer": " ".join(_fallback_line(r) for r in failed)}


def reject(state: AgentState) -> dict[str, Any]:
    """Cierra la corrida sin consultar nada cuando la pregunta es inválida."""
    return {"answer": None, "error": state.get("error") or EMPTY_QUESTION_ERROR}
