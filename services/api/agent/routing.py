"""
Enrutamiento del agente: decide, solo a partir de la pregunta, qué fuentes
necesita — RAG (políticas y procedimientos), tool de tickets (datos en vivo del
gestor de incidentes), tool de inventario (stock en vivo) o varias.

1. `classify_with_llm`: el LLM de generación devuelve un JSON que se valida
   con Pydantic (`RouteDecision`).
2. `classify_with_rules`: respaldo determinista si el LLM falla, tarda o
   devuelve algo inválido (patrón de ticket, palabras de stock…).

`method` ("llm" / "rules") queda en el estado y en el trace.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, model_validator

from agent.tools.tickets import TicketCategory, TicketStatus

logger = logging.getLogger(__name__)

_TICKET_ID_RE = re.compile(r"\bNXV[-\s]?(\d{1,6})\b", re.IGNORECASE)
_TICKET_NUMBER_RE = re.compile(r"\b(?:ticket|incidencia|incidente|caso)\s*(?:n[º°o.]?\s*|#\s*)?(\d{1,6})\b", re.IGNORECASE)
_TICKET_WORDS_RE = re.compile(r"\b(tickets?|incidencias?|incidentes?)\b", re.IGNORECASE)
_INVENTORY_WORDS_RE = re.compile(
    r"\b(stock|inventario|existencias|unidades disponibles|quedan|tenemos)\b", re.IGNORECASE
)
_PRODUCT_RE = re.compile(
    r"\b(?:stock|existencias|unidades|quedan|tenemos|inventario)\b"
    r"(?:\s+(?:de|del|disponibles?|en))*\s+(?:(?:la|el|los|las|un|una|unos|unas)\s+)?"
    r"(?P<product>[\wÁÉÍÓÚÑáéíóúñ\-\s]{2,60}?)\s*(?:\?|$|\ben\b|\bpara\b)",
    re.IGNORECASE,
)
_STATUS_WORDS = {"OPEN": r"abiert", "CLOSED": r"cerrad", "DISCARDED": r"descartad"}


class RouteDecision(BaseModel):
    rag: bool = False
    tickets: bool = False
    inventory: bool = False
    ticket_id: str | None = Field(default=None, pattern=r"^NXV-\d{6}$")
    ticket_status: TicketStatus | None = None
    ticket_category: TicketCategory | None = None
    client_company: str | None = Field(default=None, max_length=100)
    product: str | None = Field(default=None, max_length=100)
    method: Literal["llm", "rules"] = "rules"

    @model_validator(mode="after")
    def _at_least_one_source(self) -> "RouteDecision":
        if not (self.rag or self.tickets or self.inventory):
            self.rag = True  # sin fuente clara: la base de conocimiento decide si hay info
        return self


def normalize_ticket_id(value: str | None) -> str | None:
    """`NXV-482`, `nxv 000482` o `482` → `NXV-000482`."""
    if not value:
        return None
    digits = re.sub(r"\D", "", value)
    return f"NXV-{int(digits):06d}" if digits else None


def _find_ticket_id(question: str) -> str | None:
    match = _TICKET_ID_RE.search(question) or _TICKET_NUMBER_RE.search(question)
    return normalize_ticket_id(match.group(1)) if match else None


def classify_with_rules(question: str) -> RouteDecision:
    ticket_id = _find_ticket_id(question)
    tickets = bool(ticket_id or _TICKET_WORDS_RE.search(question))
    inventory = bool(_INVENTORY_WORDS_RE.search(question)) and not ticket_id
    status = next(
        (code for code, stem in _STATUS_WORDS.items() if tickets and re.search(stem, question, re.I)),
        None,
    )
    product_match = _PRODUCT_RE.search(question) if inventory else None
    return RouteDecision(
        rag=not (tickets or inventory),
        tickets=tickets,
        inventory=inventory,
        ticket_id=ticket_id,
        ticket_status=status,
        product=product_match.group("product").strip() if product_match else None,
        method="rules",
    )


ROUTER_PROMPT = """\
Eres el enrutador de un asistente de soporte de Nexova. NO respondes la pregunta: \
solo decides qué fuentes hacen falta para responderla.

Fuentes:
- "rag": base de conocimiento estable (servicios, tarifas, políticas, SLA, manejo de \
objeciones, procedimientos).
- "tickets": gestor de incidentes en vivo (estado, categoría, cliente, fechas o agente de \
tickets de soporte concretos, o listados de tickets por estado/categoría/cliente). \
Los ids tienen formato NXV-000123.
- "inventory": gestor de inventario en vivo (stock disponible de un producto o material).

Una pregunta puede necesitar varias fuentes (p. ej. estado de un ticket + qué dice la \
política). Si no encaja en ninguna, usa solo "rag".

Responde SOLO un objeto JSON, sin texto adicional, con estas claves:
{"rag": bool, "tickets": bool, "inventory": bool,
 "ticket_id": "NXV-000123" | null,
 "ticket_status": "OPEN" | "CLOSED" | "DISCARDED" | null,
 "ticket_category": "TECHNICAL" | "BILLING" | "ACCESS" | "HR_QUERY" | "COMPLAINT" | null,
 "client_company": string | null,
 "product": string | null}
"""


def _rag_llm():
    """Cliente y modelo de generación del RAG (`data/process/rag_indexing.py`), sin duplicarlos."""
    from knowledge.pipeline_bridge import generation_model, get_llm_client

    return get_llm_client(), generation_model()


def _extract_json(text: str) -> dict:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        raise ValueError("El enrutador no devolvió JSON")
    return json.loads(match.group(0))


def classify_with_llm(question: str) -> RouteDecision:
    client, model = _rag_llm()
    completion = client.chat.completions.create(
        model=model,
        temperature=0,
        timeout=15,
        messages=[
            {"role": "system", "content": ROUTER_PROMPT},
            {"role": "user", "content": question},
        ],
    )
    data = _extract_json(completion.choices[0].message.content or "")
    data["ticket_id"] = normalize_ticket_id(data.get("ticket_id"))
    return RouteDecision.model_validate({**data, "method": "llm"})


Classifier = Callable[[str], RouteDecision]


def classify_question(question: str, *, llm_classifier: Classifier = classify_with_llm) -> RouteDecision:
    """Decisión del LLM; si falla o es inválida, reglas deterministas."""
    try:
        decision = llm_classifier(question)
    except (ValidationError, ValueError, KeyError, TypeError) as exc:
        logger.warning("Enrutador LLM inválido (%s); se usan reglas", type(exc).__name__)
        return classify_with_rules(question)
    except Exception as exc:  # LLM caído, timeout, credenciales…
        logger.warning("Enrutador LLM no disponible (%s); se usan reglas", type(exc).__name__)
        return classify_with_rules(question)

    # El id explícito de la pregunta manda sobre lo que extrajo el modelo.
    explicit_id = _find_ticket_id(question)
    if explicit_id and (decision.ticket_id != explicit_id or not decision.tickets):
        decision = decision.model_copy(update={"tickets": True, "ticket_id": explicit_id})
    return decision
