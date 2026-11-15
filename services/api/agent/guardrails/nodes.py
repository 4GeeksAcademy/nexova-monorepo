"""
Nodos del harness. Solo se añaden con `build_graph(guardrails=Guardrails(...))`:

    receive_question → guard_input ─┬─(injection / cross_tenant / personal_task)→ guard_refuse ──┐
                                    ├─(casual)→ small_talk ─────────────────────────────────────┤
                                    └→ [resolve_pending → load_memory] → route_question → …     │
                                         … → generate / tool_fallback / no_information / …  ────┴→ guard_output → [propose_memory] → END

- `guard_input`: guard determinista + clasificador LLM acotado (solo si las
  reglas no deciden). Los bloqueos ocurren ANTES de la memoria, el enrutador,
  las tools y la generación: un jailbreak no llega a ningún sitio.
- `guard_refuse`: rechazo fijo (sin LLM) + redirección al soporte.
- `small_talk`: respuesta breve (hora local calculada por código cuando se
  puede, si no LLM sin contexto) + redirección obligatoria añadida por código.
- `guard_output`: valida la respuesta final; si la bloquea, no se registra
  ninguna propuesta de memoria con ella.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agent.guardrails import Guardrails, events
from agent.guardrails.input_guard import InputVerdict, check_input
from agent.guardrails.isolation import filter_restricted_sources, sanitize_chunks
from agent.guardrails.normalize import normalize
from agent.guardrails.output_guard import check_output
from agent.guardrails.prompt import REDIRECT_LINE
from agent.guardrails.tenancy import Tenant
from agent.state import AgentState

logger = logging.getLogger(__name__)

GUARD_INPUT = "guard_input"
GUARD_REFUSE = "guard_refuse"
SMALL_TALK = "small_talk"
GUARD_OUTPUT = "guard_output"

REFUSALS = {
    "injection": (
        "No puedo cambiar, ignorar ni revelar mis instrucciones, ni actuar con otro rol o sin reglas: "
        "soy el asistente de soporte de Nexova y siempre funciono con las mismas normas. Si tienes una "
        "consulta sobre un ticket, un SLA o un procedimiento de soporte, con gusto te ayudo."
    ),
    "cross_tenant": (
        "Solo puedo darte información de la cuenta con la que has iniciado sesión: no comparto tickets, "
        "SLAs ni datos de otras empresas cliente. ¿Quieres que revise algo de tu propia cuenta?"
    ),
    "personal_task": (
        "Esa tarea queda fuera de lo que puedo hacer: soy el asistente de soporte de Nexova y solo atiendo "
        "consultas sobre tickets, incidencias, SLAs y procedimientos de soporte. ¿Te ayudo con algo de tu "
        "ticket de soporte?"
    ),
}
SMALL_TALK_FALLBACK = "¡Hola! Soy el asistente de soporte de Nexova."
SUPPORT_NO_INFORMATION = (
    "No tengo información suficiente sobre eso en la base de conocimiento de soporte de Nexova. "
    "Te sugiero contactar con el equipo de soporte de Nexova o con tu account manager."
)

_CITY_ZONES = {
    "valencia": "Europe/Madrid", "madrid": "Europe/Madrid", "barcelona": "Europe/Madrid",
    "sevilla": "Europe/Madrid", "lisboa": "Europe/Lisbon", "londres": "Europe/London",
    "paris": "Europe/Paris", "berlin": "Europe/Berlin", "tokio": "Asia/Tokyo", "tokyo": "Asia/Tokyo",
    "nueva york": "America/New_York", "new york": "America/New_York", "miami": "America/New_York",
    "ciudad de mexico": "America/Mexico_City", "mexico": "America/Mexico_City",
    "bogota": "America/Bogota", "lima": "America/Lima", "santiago": "America/Santiago",
    "buenos aires": "America/Argentina/Buenos_Aires", "caracas": "America/Caracas",
}
_TIME_RE = re.compile(r"\bque hora es (en|ahora en)\s+(?P<city>[a-z ]{3,30}?)\s*\??$")


# --- Entrada --------------------------------------------------------------------------------


def make_guard_input_node(guardrails: Guardrails):
    scope_fn = guardrails.scope_fn
    if scope_fn is None and guardrails.use_llm_scope:
        from agent.guardrails.scope_classifier import classify_scope

        scope_fn = classify_scope

    def guard_input(state: AgentState) -> dict[str, Any]:
        """Clasifica el mensaje (inyección, cruce de cuentas, tarea personal, casual, dominio)."""
        question = state["question"]
        verdict = check_input(question, Tenant.from_state(state))
        if verdict.category == "unknown":
            verdict = scope_fn(question) if scope_fn else InputVerdict("domain", "allow")
        if verdict.action != "allow":
            events.emit("input" if verdict.method == "rules" else "scope", verdict.rule or verdict.category,
                        verdict.action, verdict.failure_type or "content",
                        category=verdict.category, excerpt=question)
        return {"guard": verdict.as_dict()}

    return guard_input


def is_blocked(state: AgentState) -> bool:
    return (state.get("guard") or {}).get("action") == "block"


def is_casual(state: AgentState) -> bool:
    return (state.get("guard") or {}).get("category") == "casual"


def guard_refuse(state: AgentState) -> dict[str, Any]:
    """Rechazo fijo, sin LLM y sin tocar memoria ni tools."""
    category = (state.get("guard") or {}).get("category", "injection")
    return {"answer": REFUSALS.get(category, REFUSALS["injection"])}


# --- Small talk -----------------------------------------------------------------------------


def local_time_answer(question: str, now: datetime) -> str | None:
    """"¿Qué hora es en Valencia?" → hora local calculada por código (sin LLM)."""
    match = _TIME_RE.search(normalize(question))
    if not match:
        return None
    city = match.group("city").strip()
    zone = _CITY_ZONES.get(city)
    if zone is None:
        return None
    try:
        local = now.astimezone(ZoneInfo(zone))
    except ZoneInfoNotFoundError:
        return None
    return f"En {city.title()} son las {local:%H:%M} (hora local)."


def make_small_talk_node(guardrails: Guardrails):
    talk_fn = guardrails.small_talk_fn
    if talk_fn is None:
        from agent.guardrails.generation import llm_small_talk

        talk_fn = llm_small_talk

    def small_talk(state: AgentState) -> dict[str, Any]:
        """Respuesta breve a lo casual + redirección obligatoria al soporte."""
        question = state["question"]
        text = local_time_answer(question, guardrails.clock())
        if text is None:
            try:
                text = (talk_fn(question) or "").strip()
            except Exception as exc:  # LLM caído: saludo fijo
                logger.warning("Small talk sin LLM (%s)", type(exc).__name__)
                text = ""
        return {"answer": f"{drop_trailing_questions(text) or SMALL_TALK_FALLBACK}\n\n{REDIRECT_LINE}"}

    return small_talk


_SENTENCE_RE = re.compile(r"[^.!?¡¿]*[¿¡]?[^.!?¿¡]*[.!?]+|\S[^.!?]*$")


def drop_trailing_questions(text: str) -> str:
    """Quita las preguntas finales del modelo ("¿En qué puedo ayudarte hoy?"): la única
    pregunta de cierre es la redirección al soporte, que añade el código."""
    sentences = [s.strip() for s in _SENTENCE_RE.findall(text or "") if s.strip()]
    while len(sentences) > 1 and sentences[-1].endswith("?"):
        sentences.pop()
    return " ".join(sentences)


# --- Contexto y salida ----------------------------------------------------------------------


def secure_generation_context(state: AgentState) -> list[dict[str, Any]]:
    """Contexto de generación con cada fragmento externo neutralizado (RAG, tools, memoria)."""
    from agent.nodes import build_generation_context

    return sanitize_chunks(build_generation_context(state))


def restricted_retrieve_filter(state: AgentState, chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return filter_restricted_sources(chunks, is_staff=Tenant.from_state(state).is_staff)


def support_no_information(state: AgentState) -> dict[str, Any]:
    return {"answer": SUPPORT_NO_INFORMATION}


def guard_output(state: AgentState) -> dict[str, Any]:
    """Valida la respuesta final antes de devolverla."""
    if state.get("answer") is None and state.get("error"):
        return {}
    verdict = check_output(state.get("answer"), Tenant.from_state(state))
    return {"answer": verdict.answer, "output_blocked": verdict.blocked, "output_rules": verdict.rules}
